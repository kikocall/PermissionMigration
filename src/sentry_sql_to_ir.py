"""将 Sentry MySQL ``mysqldump`` 元数据解析为统一迁移 IR。

解析器不会执行 dump 中的 SQL。它只读取授权相关表的 ``CREATE TABLE`` 和
``INSERT INTO ... VALUES``，并按 dump 内的 DDL 动态确定无列名 INSERT 的列顺序。
为适配数百 MB 的 extended-insert dump，非目标表的数据会按流丢弃。
"""

from __future__ import annotations

import gzip
import os
import re
from collections.abc import Callable
from typing import Optional

try:
    from .models import (
        MigrationPlan,
        PermissionEntry,
        Policy,
        Principal,
        PrincipalType,
        ResourcePath,
        ServiceType,
    )
    from .sentry_to_ir import _map_privileges
except ImportError:
    from models import (  # type: ignore  # noqa
        MigrationPlan,
        PermissionEntry,
        Policy,
        Principal,
        PrincipalType,
        ResourcePath,
        ServiceType,
    )
    from sentry_to_ir import _map_privileges  # type: ignore  # noqa


class SentrySqlParseError(ValueError):
    """Sentry dump 格式不完整或与 DDL 不一致。"""


_TARGET_TABLES = {
    "sentry_db_privilege",
    "sentry_role",
    "sentry_group",
    "sentry_user",
    "sentry_role_db_privilege_map",
    "sentry_user_db_privilege_map",
    "sentry_gm_privilege",
    "sentry_role_gm_privilege_map",
    "sentry_role_group_map",
    "sentry_role_user_map",
    "sentry_version",
}

_CREATE_RE = re.compile(
    r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?:(?:`[^`]+`|[A-Za-z0-9_$]+)\s*\.\s*)?"
    r"(?P<table>`[^`]+`|[A-Za-z0-9_$]+)",
    re.IGNORECASE | re.DOTALL,
)
_INSERT_RE = re.compile(
    r"^\s*(?:INSERT|REPLACE)\s+INTO\s+"
    r"(?:(?:`[^`]+`|[A-Za-z0-9_$]+)\s*\.\s*)?"
    r"(?P<table>`[^`]+`|[A-Za-z0-9_$]+)",
    re.IGNORECASE | re.DOTALL,
)
_VALUES_RE = re.compile(r"\bVALUES\b", re.IGNORECASE)


def _open_text(filepath: str):
    if filepath.lower().endswith(".gz"):
        return gzip.open(filepath, "rt", encoding="utf-8-sig", errors="strict", newline="")
    return open(filepath, "r", encoding="utf-8-sig", errors="strict", newline="")


def _identifier(raw: str) -> str:
    return raw.strip().strip("`").lower()


def _split_definitions(body: str) -> list[str]:
    """按顶层逗号切分 CREATE TABLE 定义。"""
    result: list[str] = []
    start = 0
    depth = 0
    quote: Optional[str] = None
    escaped = False
    for index, char in enumerate(body):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")" and depth:
            depth -= 1
        elif char == "," and depth == 0:
            result.append(body[start:index])
            start = index + 1
    result.append(body[start:])
    return result


def _columns_from_create(statement: str) -> list[str]:
    start = statement.find("(")
    end = statement.rfind(")")
    if start < 0 or end <= start:
        return []
    columns: list[str] = []
    non_columns = {
        "primary", "unique", "key", "constraint", "foreign", "index",
        "check", "fulltext", "spatial",
    }
    for definition in _split_definitions(statement[start + 1:end]):
        item = definition.strip()
        match = re.match(r"`([^`]+)`|([A-Za-z_$][\w$]*)", item)
        if not match:
            continue
        name = match.group(1) or match.group(2)
        if name.lower() not in non_columns:
            columns.append(name.upper())
    return columns


def _columns_from_insert(header: str) -> list[str]:
    """读取表名之后、VALUES 之前的可选列清单。"""
    match = _INSERT_RE.match(header)
    if not match:
        return []
    tail = header[match.end():]
    values_match = _VALUES_RE.search(tail)
    if values_match:
        tail = tail[:values_match.start()]
    tail = tail.strip()
    if not tail.startswith("(") or ")" not in tail:
        return []
    return [
        (quoted or bare).upper()
        for quoted, bare in re.findall(r"`([^`]+)`|([A-Za-z_$][\w$]*)", tail[1:tail.rfind(")")])
    ]


def _decode_unquoted(token: str):
    value = token.strip()
    if not value or value.upper() == "NULL":
        return None
    if re.fullmatch(r"[-+]?\d+", value):
        try:
            return int(value)
        except ValueError:
            pass
    return value


_MYSQL_ESCAPES = {
    "0": "\0", "b": "\b", "n": "\n", "r": "\r", "t": "\t",
    "Z": "\x1a", "\\": "\\", "'": "'", '"': '"', "%": "%", "_": "_",
}


class _ValuesParser:
    """增量解析 mysqldump 的 VALUES 元组。"""

    def __init__(self, on_row: Callable[[list[object]], None]):
        self.on_row = on_row
        self.in_tuple = False
        self.in_string = False
        self.escaped = False
        self.quote_pending = False
        self.quoted_value = False
        self.token: list[str] = []
        self.row: list[object] = []

    def _finish_value(self) -> None:
        text = "".join(self.token)
        self.row.append(text if self.quoted_value else _decode_unquoted(text))
        self.token.clear()
        self.quoted_value = False

    def feed(self, text: str) -> bool:
        """消费片段；遇到 INSERT 结束分号时返回 True。"""
        index = 0
        while index < len(text):
            char = text[index]
            if self.in_string:
                if self.quote_pending:
                    if char == "'":
                        self.token.append("'")
                        self.quote_pending = False
                        index += 1
                        continue
                    self.in_string = False
                    self.quote_pending = False
                    continue  # 当前字符需按字符串外状态重新处理
                if self.escaped:
                    self.token.append(_MYSQL_ESCAPES.get(char, char))
                    self.escaped = False
                elif char == "\\":
                    self.escaped = True
                elif char == "'":
                    self.quote_pending = True
                else:
                    self.token.append(char)
                index += 1
                continue

            if not self.in_tuple:
                if char == "(":
                    self.in_tuple = True
                    self.row = []
                    self.token = []
                    self.quoted_value = False
                elif char == ";":
                    return True
                index += 1
                continue

            if char == "'" and not "".join(self.token).strip() and not self.quoted_value:
                self.token.clear()
                self.in_string = True
                self.quoted_value = True
            elif char == ",":
                self._finish_value()
            elif char == ")":
                self._finish_value()
                self.on_row(self.row)
                self.row = []
                self.in_tuple = False
            elif not (self.quoted_value and char.isspace()):
                self.token.append(char)
            index += 1
        return False


class _DumpReader:
    """面向标准 mysqldump 文本的低内存流式读取器。"""

    def __init__(self) -> None:
        self.schemas: dict[str, list[str]] = {}
        self.seen_tables: set[str] = set()
        self.rows: dict[str, list[dict[str, object]]] = {name: [] for name in _TARGET_TABLES}
        self.mode = "start"
        self.header = ""
        self.current_table: Optional[str] = None
        self.create_buffer = ""
        self.values_parser: Optional[_ValuesParser] = None

    def _reset(self) -> None:
        self.mode = "start"
        self.header = ""
        self.current_table = None
        self.create_buffer = ""
        self.values_parser = None

    def _make_row_consumer(self, table: str, columns: list[str]) -> Callable[[list[object]], None]:
        if not columns:
            raise SentrySqlParseError(
                f"表 {table} 的 INSERT 没有列清单，且此前未读取到该表的 CREATE TABLE"
            )

        def consume(values: list[object]) -> None:
            if len(values) != len(columns):
                raise SentrySqlParseError(
                    f"表 {table} 的 INSERT 值数量为 {len(values)}，但 DDL/列清单为 {len(columns)} 列"
                )
            self.rows[table].append(dict(zip(columns, values)))

        return consume

    def feed_line_part(self, part: str, end_of_line: bool) -> None:
        if self.mode == "skip":
            if end_of_line:
                self._reset()
            return

        if self.mode == "create":
            self.create_buffer += part
            if ";" in part:
                assert self.current_table is not None
                columns = _columns_from_create(self.create_buffer)
                if not columns:
                    raise SentrySqlParseError(f"无法从表 {self.current_table} 的 CREATE TABLE 中读取列")
                self.schemas[self.current_table] = columns
                self._reset()
            return

        if self.mode == "insert_values":
            assert self.values_parser is not None
            if self.values_parser.feed(part):
                self._reset()
            elif end_of_line:
                # 标准 mysqldump 的 INSERT 应在行尾结束；允许格式化后的多行 INSERT。
                self.values_parser.feed("\n")
            return

        self.header += part
        stripped = self.header.lstrip()
        create_match = _CREATE_RE.match(stripped)
        if create_match:
            table = _identifier(create_match.group("table"))
            self.seen_tables.add(table)
            if table not in _TARGET_TABLES:
                self.mode = "skip"
                if end_of_line:
                    self._reset()
            else:
                self.mode = "create"
                self.current_table = table
                self.create_buffer = self.header
                if ";" in self.header:
                    columns = _columns_from_create(self.create_buffer)
                    if not columns:
                        raise SentrySqlParseError(f"无法从表 {table} 的 CREATE TABLE 中读取列")
                    self.schemas[table] = columns
                    self._reset()
            return

        insert_match = _INSERT_RE.match(stripped)
        if insert_match:
            table = _identifier(insert_match.group("table"))
            if table not in _TARGET_TABLES:
                self.mode = "skip"
                if end_of_line:
                    self._reset()
                return
            values_match = _VALUES_RE.search(self.header, insert_match.end())
            if not values_match:
                self.mode = "insert_header"
                if len(self.header) > 65536:
                    raise SentrySqlParseError(f"表 {table} 的 INSERT 头超过 64 KiB")
                return
            columns = _columns_from_insert(self.header[:values_match.end()]) or self.schemas.get(table, [])
            consumer = self._make_row_consumer(table, columns)
            self.mode = "insert_values"
            self.current_table = table
            self.values_parser = _ValuesParser(consumer)
            remainder = self.header[values_match.end():]
            self.header = ""
            if self.values_parser.feed(remainder):
                self._reset()
            return

        if self.mode == "insert_header":
            # 已匹配 INSERT，但还没读到 VALUES。
            match = _INSERT_RE.match(stripped)
            assert match is not None
            values_match = _VALUES_RE.search(self.header, match.end())
            if values_match:
                table = _identifier(match.group("table"))
                columns = _columns_from_insert(self.header[:values_match.end()]) or self.schemas.get(table, [])
                consumer = self._make_row_consumer(table, columns)
                self.mode = "insert_values"
                self.current_table = table
                self.values_parser = _ValuesParser(consumer)
                remainder = self.header[values_match.end():]
                self.header = ""
                if self.values_parser.feed(remainder):
                    self._reset()
            return

        if end_of_line:
            self._reset()
        elif len(self.header) > 65536:
            self.mode = "skip"

    def read(self, filepath: str, chunk_size: int = 1024 * 1024) -> None:
        pending = ""
        with _open_text(filepath) as stream:
            while True:
                chunk = stream.read(chunk_size)
                if not chunk:
                    break
                pending += chunk
                start = 0
                while True:
                    newline = pending.find("\n", start)
                    if newline < 0:
                        # 避免把超长 extended-insert 行整体留在 pending。
                        if start < len(pending):
                            self.feed_line_part(pending[start:], False)
                        pending = ""
                        break
                    self.feed_line_part(pending[start:newline + 1], True)
                    start = newline + 1
                    if start == len(pending):
                        pending = ""
                        break
            if pending:
                self.feed_line_part(pending, True)


def _clean(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    if text.upper() == "__NULL__":
        return None
    return text


def _truthy(value: object) -> bool:
    return (_clean(value) or "").strip().upper() in {"Y", "YES", "TRUE", "1"}


def _resource_from_privilege(row: dict[str, object]) -> ResourcePath:
    scope = (_clean(row.get("PRIVILEGE_SCOPE")) or "").upper()
    uri = _clean(row.get("URI"))
    if uri or scope in {"URI", "URL"}:
        return ResourcePath(service_type=ServiceType.HDFS, path=uri or "/")

    database = _clean(row.get("DB_NAME"))
    table = _clean(row.get("TABLE_NAME"))
    column = _clean(row.get("COLUMN_NAME"))
    if scope in {"SERVER", "GLOBAL"}:
        database = "*"
        table = None
        column = None
    return ResourcePath(
        service_type=ServiceType.HIVE,
        database=database or "*",
        table=table,
        column=column,
    )


def _id_index(rows: list[dict[str, object]], id_column: str, name_column: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in rows:
        identifier = _clean(row.get(id_column))
        name = _clean(row.get(name_column))
        if identifier is not None and name:
            result[identifier] = name
    return result


def parse_sentry_sql_dump(filepath: str) -> MigrationPlan:
    """解析 Sentry MySQL dump，返回可供 Guardian 生成器使用的迁移计划。"""
    reader = _DumpReader()
    reader.read(filepath)

    rows = reader.rows
    roles = _id_index(rows["sentry_role"], "ROLE_ID", "ROLE_NAME")
    groups = _id_index(rows["sentry_group"], "GROUP_ID", "GROUP_NAME")
    users = _id_index(rows["sentry_user"], "USER_ID", "USER_NAME")
    privileges = {
        identifier: row
        for row in rows["sentry_db_privilege"]
        if (identifier := _clean(row.get("DB_PRIVILEGE_ID"))) is not None
    }

    plan = MigrationPlan()
    plan.users.update(users.values())
    plan.groups.update(groups.values())
    plan.roles.update(roles.values())

    unresolved = {"role_group": 0, "role_user": 0, "role_privilege": 0, "user_privilege": 0}

    for mapping in rows["sentry_role_group_map"]:
        role = roles.get(_clean(mapping.get("ROLE_ID")) or "")
        group = groups.get(_clean(mapping.get("GROUP_ID")) or "")
        if role and group:
            plan.role_group_assignments.setdefault(role, set()).add(group)
        else:
            unresolved["role_group"] += 1

    for mapping in rows["sentry_role_user_map"]:
        role = roles.get(_clean(mapping.get("ROLE_ID")) or "")
        user = users.get(_clean(mapping.get("USER_ID")) or "")
        if role and user:
            plan.role_user_assignments.setdefault(role, set()).add(user)
        else:
            unresolved["role_user"] += 1

    def add_permission(principal_name: str, principal_type: PrincipalType, privilege: dict[str, object]) -> None:
        resource = _resource_from_privilege(privilege)
        action = _clean(privilege.get("ACTION")) or ""
        actions = _map_privileges(action, resource.service_type)
        if not actions:
            return
        principal = Principal(principal_name, principal_type)
        permission_entries = [
            PermissionEntry(
                mapped_action,
                resource,
                principal,
                grantable=_truthy(privilege.get("WITH_GRANT_OPTION")),
                heritable=True,
                administrative=True,
            )
            for mapped_action in actions
        ]
        scope = _clean(privilege.get("PRIVILEGE_SCOPE")) or "UNKNOWN"
        plan.policies.append(Policy(
            source="sentry-sql",
            service_type=resource.service_type,
            service_name=_clean(privilege.get("SERVER_NAME")) or "sentry",
            resources=[resource],
            permissions=permission_entries,
            description=f"Sentry {scope} privilege for {principal_type.value} {principal_name}",
        ))

    for mapping in rows["sentry_role_db_privilege_map"]:
        role = roles.get(_clean(mapping.get("ROLE_ID")) or "")
        privilege = privileges.get(_clean(mapping.get("DB_PRIVILEGE_ID")) or "")
        if role and privilege:
            add_permission(role, PrincipalType.ROLE, privilege)
        else:
            unresolved["role_privilege"] += 1

    for mapping in rows["sentry_user_db_privilege_map"]:
        user = users.get(_clean(mapping.get("USER_ID")) or "")
        privilege = privileges.get(_clean(mapping.get("DB_PRIVILEGE_ID")) or "")
        if user and privilege:
            add_permission(user, PrincipalType.USER, privilege)
        else:
            unresolved["user_privilege"] += 1

    versions = [
        _clean(row.get("SCHEMA_VERSION"))
        for row in rows["sentry_version"]
        if _clean(row.get("SCHEMA_VERSION"))
    ]
    plan.source_metadata = {
        "source": "sentry-sql",
        "file": os.path.abspath(filepath),
        "schema_versions": versions,
        "table_row_counts": {name: len(data) for name, data in rows.items() if data},
        "ddl_tables": sorted(reader.schemas),
        "dump_tables": sorted(reader.seen_tables),
        "skipped_generic_model_privileges": len(rows["sentry_role_gm_privilege_map"]),
        "unresolved_references": {key: value for key, value in unresolved.items() if value},
        "limitations": [
            "Sentry 元数据库不包含 LDAP/操作系统组的用户成员关系，未生成组到用户关系。",
            "AUTHZ_PATH/AUTHZ_PATHS_MAPPING 是 HDFS 同步索引，不作为权限导入。",
            "SENTRY_GM_PRIVILEGE（Kafka/Solr 等通用模型）尚未映射到 Guardian。",
        ],
    }
    return plan


def parse_sentry_export(filepath: str) -> MigrationPlan:
    """按扩展名和文件头自动识别 Sentry CSV/TSV 或 MySQL dump。"""
    lower_path = filepath.lower()
    if lower_path.endswith((".sql", ".dump", ".sql.gz", ".dump.gz")):
        return parse_sentry_sql_dump(filepath)
    with _open_text(filepath) as stream:
        prefix = stream.read(8192)
    if re.search(r"\b(?:CREATE\s+TABLE|INSERT\s+INTO)\b", prefix, re.IGNORECASE):
        return parse_sentry_sql_dump(filepath)
    try:
        from .sentry_to_ir import parse_sentry_csv
    except ImportError:
        from sentry_to_ir import parse_sentry_csv  # type: ignore  # noqa
    return parse_sentry_csv(filepath)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Parse Sentry CSV/TSV or MySQL dump to IR")
    parser.add_argument("--input", "-i", required=True)
    parser.add_argument("--summary", action="store_true")
    arguments = parser.parse_args()
    migration_plan = parse_sentry_export(arguments.input)
    print(f"Users:    {len(migration_plan.users)}")
    print(f"Groups:   {len(migration_plan.groups)}")
    print(f"Roles:    {len(migration_plan.roles)}")
    print(f"Policies: {len(migration_plan.policies)}")
