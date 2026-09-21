"""CLI entry point for permission migration toolchain.

Unified CLI supporting:
  ranger  - Parse Ranger JSON export to IR
  sentry  - Parse Sentry CSV/TSV or MySQL dump to IR
  excel   - Parse Guardian batch Excel template to IR
  guardian - Generate Guardian API shell script from IR
  migrate  - Full end-to-end migration (ranger/sentry/excel -> Guardian script)

Usage:
  python -m src.cli ranger  --input ranger.json --output ir.json
  python -m src.cli sentry  --input sentry.csv  --output ir.json
  python -m src.cli excel   --input guardian.xlsx --output ir.json
  python -m src.cli guardian --input ir.json --output script.sh
  python -m src.cli migrate  --source ranger --source-input ranger.json --output script.sh
  python -m src.cli migrate  --source sentry --source-input sentry.csv --output script.sh
  python -m src.cli migrate  --source excel --source-input guardian.xlsx --output script.sh
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import os


def _ensure_src_in_path():
    """Ensure src/ is importable."""
    src_dir = os.path.dirname(os.path.abspath(__file__))
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)


def cmd_ranger(args):
    try:
        from .ranger_to_ir import parse_ranger_export
    except ImportError:
        from ranger_to_ir import parse_ranger_export
    plan = parse_ranger_export(args.input)
    _write_plan(plan, args.output)
    _print_summary(plan)


def cmd_sentry(args):
    try:
        from .sentry_sql_to_ir import parse_sentry_export
    except ImportError:
        from sentry_sql_to_ir import parse_sentry_export
    plan = parse_sentry_export(args.input)
    plan = _apply_user_group_mapping(plan, args)
    _export_users(plan, getattr(args, "export_users", None))
    plan = _apply_user_filter(plan, args)
    _write_plan(plan, args.output)
    _print_summary(plan)


def _default_validation_report(input_path: str, output_path: str | None) -> str:
    base = output_path or os.path.splitext(os.path.basename(input_path))[0]
    return os.path.splitext(base)[0] + "_validation.json"


def _parse_excel(input_path: str, validation_report: str):
    try:
        from .excel_to_ir import ExcelValidationError, parse_guardian_excel
    except ImportError:
        from excel_to_ir import ExcelValidationError, parse_guardian_excel
    try:
        return parse_guardian_excel(input_path, validation_report=validation_report)
    except ExcelValidationError as error:
        report_note = f"；校验报告: {error.report_path}" if error.report_path else ""
        raise SystemExit(f"Excel 校验失败，共 {len(error.errors)} 个错误{report_note}\n{error}") from error


def cmd_excel(args):
    report_path = getattr(args, "validation_report", None) or _default_validation_report(
        args.input,
        args.output,
    )
    plan = _parse_excel(args.input, report_path)
    _write_plan(plan, args.output)
    _print_summary(plan)
    print(f"Excel validation report: {os.path.abspath(report_path)}")


def cmd_guardian(args):
    plan = _load_plan(args.input)
    plan = _apply_user_group_mapping(plan, args)
    _export_users(plan, getattr(args, "export_users", None))
    plan = _apply_user_filter(plan, args)
    _print_summary(plan)
    try:
        from .ir_to_guardian import generate_script
    except ImportError:
        from ir_to_guardian import generate_script
    output = args.output or "permission_migration.sh"
    component_overrides = _component_overrides(args)
    path = generate_script(
        plan,
        output,
        base_url=args.base_url or None,
        access_token=args.access_token or None,
        component_overrides=component_overrides,
    )
    print(f"Guardian script generated: {path}")


def cmd_migrate(args):
    """Full migration: source -> IR -> Guardian script."""
    if args.source == "ranger":
        try:
            from .ranger_to_ir import parse_ranger_export
        except ImportError:
            from ranger_to_ir import parse_ranger_export
        plan = parse_ranger_export(args.source_input)
    elif args.source == "sentry":
        try:
            from .sentry_sql_to_ir import parse_sentry_export
        except ImportError:
            from sentry_sql_to_ir import parse_sentry_export
        plan = parse_sentry_export(args.source_input)
    elif args.source == "excel":
        report_path = getattr(args, "validation_report", None) or _default_validation_report(
            args.source_input,
            args.output,
        )
        plan = _parse_excel(args.source_input, report_path)
        print(f"Excel validation report: {os.path.abspath(report_path)}")
    else:
        print(f"Unknown source type: {args.source}", file=sys.stderr)
        sys.exit(1)

    plan = _apply_user_group_mapping(plan, args)
    _export_users(plan, getattr(args, "export_users", None))
    plan = _apply_user_filter(plan, args)

    if args.save_ir:
        _write_plan(plan, args.save_ir)
        print(f"IR saved to: {args.save_ir}")

    _print_summary(plan)

    try:
        from .ir_to_guardian import generate_script
    except ImportError:
        from ir_to_guardian import generate_script
    output = args.output or "permission_migration.sh"
    component_overrides = _component_overrides(args)
    path = generate_script(
        plan,
        output,
        base_url=args.base_url or None,
        access_token=args.access_token or None,
        component_overrides=component_overrides,
    )
    print(f"Guardian script generated: {path}")


# ── Helpers ─────────────────────────────────────────────────────────────────

def _selected_users(args) -> set[str]:
    """合并命令行和文件中的用户白名单。"""
    selected: set[str] = set()
    for value in getattr(args, "users", None) or []:
        selected.update(name.strip() for name in value.split(",") if name.strip())
    users_file = getattr(args, "users_file", None)
    if users_file:
        with open(users_file, "r", encoding="utf-8-sig") as stream:
            for line in stream:
                clean_line = line.strip()
                if not clean_line or clean_line.startswith("#"):
                    continue
                selected.update(name.strip() for name in clean_line.split(",") if name.strip())
    return selected


def _apply_user_filter(plan, args):
    selected = _selected_users(args)
    if not selected:
        return plan
    try:
        from .utils import filter_plan_by_users
    except ImportError:
        from utils import filter_plan_by_users
    try:
        return filter_plan_by_users(plan, selected)
    except ValueError as error:
        raise SystemExit(f"错误: {error}") from error


def _read_user_group_memberships(path: str) -> dict[str, set[str]]:
    """读取每行一条 ``user,group`` 的 CSV/TSV 用户组关系。"""
    memberships: dict[str, set[str]] = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as stream:
        meaningful_lines = [
            (line_number, line)
            for line_number, line in enumerate(stream, start=1)
            if line.strip() and not line.lstrip().startswith("#")
        ]

    if not meaningful_lines:
        raise SystemExit(f"错误: 用户组关系文件为空: {path}")
    delimiter = "\t" if "\t" in meaningful_lines[0][1] else ","
    reader = csv.reader((line for _, line in meaningful_lines), delimiter=delimiter)
    for row_index, row in enumerate(reader):
        line_number = meaningful_lines[row_index][0]
        if len(row) < 2:
            raise SystemExit(
                f"错误: 用户组关系文件第 {line_number} 行至少需要 user,group 两列"
            )
        user, group = row[0].strip(), row[1].strip()
        if row_index == 0 and user.lower() in {"user", "username", "user_name"} \
                and group.lower() in {"group", "groupname", "group_name"}:
            continue
        if not user or not group:
            raise SystemExit(
                f"错误: 用户组关系文件第 {line_number} 行的 user 或 group 为空"
            )
        memberships.setdefault(user, set()).add(group)
    return memberships


def _apply_user_group_mapping(plan, args):
    mapping_file = getattr(args, "user_groups_file", None)
    if not mapping_file:
        return plan
    try:
        from .utils import merge_user_group_memberships
    except ImportError:
        from utils import merge_user_group_memberships
    return merge_user_group_memberships(
        plan,
        _read_user_group_memberships(mapping_file),
    )


def _export_users(plan, output_path: str | None) -> None:
    """导出全部已解析用户，供人工删减后作为 --users-file 使用。"""
    if not output_path:
        return
    try:
        from .utils import ensure_dir
    except ImportError:
        from utils import ensure_dir
    ensure_dir(output_path)
    with open(output_path, "w", encoding="utf-8") as stream:
        for user_name in sorted(plan.users):
            stream.write(user_name + "\n")
    print(f"All parsed users exported: {os.path.abspath(output_path)}")


def _add_user_filter_args(parser) -> None:
    parser.add_argument(
        "--users",
        action="append",
        metavar="USER[,USER...]",
        help="仅保留指定用户；可使用逗号分隔或重复传入",
    )
    parser.add_argument(
        "--users-file",
        help="用户白名单文件，每行一个用户名，也支持逗号分隔和 # 注释",
    )
    parser.add_argument(
        "--export-users",
        help="过滤前导出全部已解析用户名，供人工制作白名单",
    )
    parser.add_argument(
        "--user-groups-file",
        help="外部用户组关系 CSV/TSV，每行 user,group；只接纳 Sentry 中已有的组",
    )

def _plan_to_dict(plan) -> dict:
    """Serialize a MigrationPlan to a JSON-friendly dict."""
    policies = []
    for p in plan.policies:
        perms = []
        for pm in p.permissions:
            perms.append({
                "action": pm.action,
                "resource": {
                    "service_type": pm.resource.service_type.value if pm.resource.service_type else None,
                    "database": pm.resource.database,
                    "table": pm.resource.table,
                    "partition": pm.resource.partition,
                    "column": pm.resource.column,
                    "path": pm.resource.path,
                },
                "principal": {
                    "name": pm.principal.name,
                    "type": pm.principal.principal_type.value,
                },
                "grantable": pm.grantable,
                "heritable": pm.heritable,
                "administrative": pm.administrative,
            })
        policies.append({
            "source": p.source,
            "service_type": p.service_type.value if p.service_type else None,
            "service_name": p.service_name,
            "description": p.description,
            "permissions": perms,
        })

    return {
        "source_metadata": plan.source_metadata,
        "users": sorted(plan.users),
        "user_profiles": plan.user_profiles,
        "groups": sorted(plan.groups),
        "roles": sorted(plan.roles),
        "role_group_assignments": {k: sorted(v) for k, v in plan.role_group_assignments.items()},
        "role_user_assignments": {k: sorted(v) for k, v in plan.role_user_assignments.items()},
        "group_user_assignments": {k: sorted(v) for k, v in plan.group_user_assignments.items()},
        "policies": policies,
    }


def _plan_from_dict(d: dict):
    """Deserialize a dict back to MigrationPlan."""
    try:
        from .models import (
            MigrationPlan,
            Policy,
            PermissionEntry,
            Principal,
            PrincipalType,
            ResourcePath,
            ServiceType,
        )
    except ImportError:
        from models import (  # noqa
            MigrationPlan,
            Policy,
            PermissionEntry,
            Principal,
            PrincipalType,
            ResourcePath,
            ServiceType,
        )
    plan = MigrationPlan()
    plan.source_metadata = d.get("source_metadata", {})
    plan.users = set(d.get("users", []))
    plan.user_profiles = {
        name: {str(key): str(value) for key, value in profile.items()}
        for name, profile in d.get("user_profiles", {}).items()
    }
    plan.groups = set(d.get("groups", []))
    plan.roles = set(d.get("roles", []))
    plan.role_group_assignments = {k: set(v) for k, v in d.get("role_group_assignments", {}).items()}
    plan.role_user_assignments = {k: set(v) for k, v in d.get("role_user_assignments", {}).items()}
    plan.group_user_assignments = {k: set(v) for k, v in d.get("group_user_assignments", {}).items()}

    for policy_data in d.get("policies", []):
        if isinstance(policy_data, list):
            perm_list = policy_data
            policy_source = "ir"
            policy_service_name = ""
            policy_description = ""
        else:
            perm_list = policy_data.get("permissions", [])
            policy_source = policy_data.get("source", "ir")
            policy_service_name = policy_data.get("service_name", "")
            policy_description = policy_data.get("description", "")
        permissions = []
        for pm in perm_list:
            r = pm["resource"]
            st = ServiceType.from_string(r.get("service_type", "unknown"))
            resource = ResourcePath(
                service_type=st,
                database=r.get("database"),
                table=r.get("table"),
                partition=r.get("partition"),
                column=r.get("column"),
                path=r.get("path"),
            )
            p = pm["principal"]
            principal = Principal(
                name=p["name"],
                principal_type=PrincipalType(p["type"]),
            )
            permissions.append(PermissionEntry(
                action=pm["action"],
                resource=resource,
                principal=principal,
                grantable=pm.get("grantable", False),
                heritable=pm.get("heritable", True),
                administrative=pm.get("administrative", True),
            ))
        if permissions:
            plan.policies.append(Policy(
                source=policy_source,
                service_type=permissions[0].resource.service_type,
                service_name=policy_service_name,
                resources=[pm2.resource for pm2 in permissions],
                permissions=permissions,
                description=policy_description,
            ))
    return plan


def _write_plan(plan, path: str):
    if not path:
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_plan_to_dict(plan), f, ensure_ascii=False, indent=2)


def _load_plan(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return _plan_from_dict(json.load(f))


def _print_summary(plan):
    print(f"Users:    {len(plan.users)}")
    print(f"Groups:   {len(plan.groups)}")
    print(f"Roles:    {len(plan.roles)}")
    print(f"Policies: {len(plan.policies)}")
    metadata = plan.source_metadata
    user_filter = metadata.get("user_filter") or {}
    if user_filter:
        print(f"Selected users: {len(user_filter.get('selected_users', []))}")
        print(f"Filtered permissions: {user_filter.get('filtered_permission_count', 0)}")
    external_groups = metadata.get("external_user_groups") or {}
    if external_groups:
        print(f"External group memberships: {external_groups.get('membership_count', 0)}")
        ignored_groups = external_groups.get("ignored_groups") or []
        if ignored_groups:
            print(f"Ignored external groups: {len(ignored_groups)}")
    skipped_local = metadata.get("skipped_local_file_uri_privileges", 0)
    if skipped_local:
        print(f"Skipped local file URI privileges: {skipped_local}")
    if metadata.get("source") == "sentry-sql":
        versions = metadata.get("schema_versions") or []
        if versions:
            print(f"Sentry schema: {', '.join(versions)}")
        skipped_gm = metadata.get("skipped_generic_model_privileges", 0)
        if skipped_gm:
            print(f"Skipped GM privilege mappings: {skipped_gm}")
        unresolved = metadata.get("unresolved_references") or {}
        if unresolved:
            print(f"Unresolved references: {json.dumps(unresolved, ensure_ascii=False)}")


def _component_overrides(args) -> dict[str, str]:
    overrides: dict[str, str] = {}
    if getattr(args, "hive_component", None):
        overrides["hive"] = args.hive_component
    if getattr(args, "hdfs_component", None):
        overrides["hdfs"] = args.hdfs_component
    return overrides


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    _ensure_src_in_path()

    parser = argparse.ArgumentParser(
        description="Permission Migration Toolchain - Ranger/Sentry to Guardian"
    )
    sub = parser.add_subparsers(dest="command", help="Subcommand")

    # ranger
    p_ranger = sub.add_parser("ranger", help="Parse Ranger JSON export to IR")
    p_ranger.add_argument("--input", "-i", required=True)
    p_ranger.add_argument("--output", "-o")

    # sentry
    p_sentry = sub.add_parser("sentry", help="Parse Sentry CSV/TSV or MySQL dump to IR")
    p_sentry.add_argument("--input", "-i", required=True)
    p_sentry.add_argument("--output", "-o")
    _add_user_filter_args(p_sentry)

    # guardian excel
    p_excel = sub.add_parser("excel", help="Parse Guardian batch Excel template to IR")
    p_excel.add_argument("--input", "-i", required=True)
    p_excel.add_argument("--output", "-o")
    p_excel.add_argument("--validation-report", help="Excel 行级校验报告 JSON")

    # guardian
    p_guardian = sub.add_parser("guardian", help="Generate Guardian API script from IR")
    p_guardian.add_argument("--input", "-i", required=True)
    p_guardian.add_argument("--output", "-o")
    p_guardian.add_argument("--base-url", help="Guardian API base URL")
    p_guardian.add_argument("--access-token", help="Guardian access token")
    p_guardian.add_argument("--hive-component", help="Guardian Hive component name")
    p_guardian.add_argument("--hdfs-component", help="Guardian HDFS component name")
    _add_user_filter_args(p_guardian)

    # migrate (end-to-end)
    p_migrate = sub.add_parser("migrate", help="Full migration: source -> IR -> Guardian script")
    p_migrate.add_argument("--source", required=True, choices=["ranger", "sentry", "excel"])
    p_migrate.add_argument("--source-input", required=True)
    p_migrate.add_argument("--output", "-o")
    p_migrate.add_argument("--base-url")
    p_migrate.add_argument("--access-token")
    p_migrate.add_argument("--hive-component")
    p_migrate.add_argument("--hdfs-component")
    p_migrate.add_argument("--save-ir", help="Save intermediate IR to file")
    p_migrate.add_argument("--validation-report", help="Excel 行级校验报告 JSON")
    _add_user_filter_args(p_migrate)

    args = parser.parse_args()

    if args.command == "ranger":
        cmd_ranger(args)
    elif args.command == "sentry":
        cmd_sentry(args)
    elif args.command == "excel":
        cmd_excel(args)
    elif args.command == "guardian":
        cmd_guardian(args)
    elif args.command == "migrate":
        cmd_migrate(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
