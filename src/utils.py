"""Shared utility functions for permission migration tools."""

import copy
import hashlib
import json
import os
from typing import Optional

# Try to import from sibling modules
try:
    from .models import MigrationPlan, Policy, PrincipalType, ServiceType
except ImportError:
    from models import MigrationPlan, Policy, PrincipalType, ServiceType  # noqa: F811


def filter_plan_by_users(
    plan: MigrationPlan,
    selected_users: set[str],
    *,
    strict: bool = True,
) -> MigrationPlan:
    """按用户白名单裁剪迁移计划及其可确定的继承权限。

    保留用户直授权、直接授予用户的角色，以及已知组成员关系所带来的组和角色。
    SQL dump 通常没有 LDAP/操作系统组成员关系，因此不会猜测用户所在组。
    """
    requested = {name.strip() for name in selected_users if name.strip()}
    if not requested:
        raise ValueError("用户白名单为空")

    missing = requested - plan.users
    if missing and strict:
        raise ValueError("以下用户不在解析结果中: " + ", ".join(sorted(missing)))
    included_users = requested & plan.users

    included_groups = {
        group_name
        for group_name, members in plan.group_user_assignments.items()
        if members & included_users
    }
    included_roles = {
        role_name
        for role_name, members in plan.role_user_assignments.items()
        if members & included_users
    }
    included_roles.update(
        role_name
        for role_name, groups in plan.role_group_assignments.items()
        if groups & included_groups
    )

    filtered = MigrationPlan(
        users=set(included_users),
        groups=set(included_groups),
        roles=set(included_roles),
        role_user_assignments={
            role_name: set(members & included_users)
            for role_name, members in plan.role_user_assignments.items()
            if role_name in included_roles and members & included_users
        },
        group_user_assignments={
            group_name: set(members & included_users)
            for group_name, members in plan.group_user_assignments.items()
            if group_name in included_groups and members & included_users
        },
        role_group_assignments={
            role_name: set(groups & included_groups)
            for role_name, groups in plan.role_group_assignments.items()
            if role_name in included_roles and groups & included_groups
        },
        source_metadata=copy.deepcopy(plan.source_metadata),
    )

    for policy in plan.policies:
        permissions = []
        for permission in policy.permissions:
            principal = permission.principal
            keep = (
                principal.principal_type == PrincipalType.USER
                and principal.name in included_users
            ) or (
                principal.principal_type == PrincipalType.GROUP
                and principal.name in included_groups
            ) or (
                principal.principal_type == PrincipalType.ROLE
                and principal.name in included_roles
            )
            if keep:
                permissions.append(permission)
        if permissions:
            filtered.policies.append(Policy(
                source=policy.source,
                service_type=policy.service_type,
                service_name=policy.service_name,
                resources=[permission.resource for permission in permissions],
                permissions=permissions,
                description=policy.description,
            ))

    original_permission_count = sum(len(policy.permissions) for policy in plan.policies)
    filtered_permission_count = sum(len(policy.permissions) for policy in filtered.policies)
    filtered.source_metadata["user_filter"] = {
        "selected_users": sorted(included_users),
        "missing_users": sorted(missing),
        "original_user_count": len(plan.users),
        "original_permission_count": original_permission_count,
        "filtered_permission_count": filtered_permission_count,
        "included_group_count": len(included_groups),
        "included_role_count": len(included_roles),
    }
    return filtered


def merge_user_group_memberships(
    plan: MigrationPlan,
    memberships: dict[str, set[str]],
) -> MigrationPlan:
    """把外部目录的用户—组关系合并进计划。

    只接纳 Sentry 元数据中已经存在的组，避免把与本次迁移无关的 LDAP/AD
    组创建到 Guardian。接纳成功的用户会加入 ``plan.users``，因此即使该用户
    没有出现在 ``sentry_user`` 表中，也可以通过组权限被选择迁移。
    """
    known_groups = plan.groups
    accepted_users: set[str] = set()
    ignored_groups: set[str] = set()
    membership_count = 0

    for raw_user, raw_groups in memberships.items():
        user = raw_user.strip()
        if not user:
            continue
        for raw_group in raw_groups:
            group = raw_group.strip()
            if not group:
                continue
            if group not in known_groups:
                ignored_groups.add(group)
                continue
            members = plan.group_user_assignments.setdefault(group, set())
            if user not in members:
                members.add(user)
                membership_count += 1
            plan.users.add(user)
            accepted_users.add(user)

    plan.source_metadata["external_user_groups"] = {
        "user_count": len(accepted_users),
        "membership_count": membership_count,
        "ignored_groups": sorted(ignored_groups),
    }
    return plan


def service_type_from_ranger_name(name: str) -> ServiceType:
    """Normalize a Ranger service-type string to ServiceType enum."""
    if not name:
        return ServiceType.UNKNOWN
    return ServiceType.from_string(name)


def service_type_from_sentry_path(database: str) -> ServiceType:
    """Infer service type from a Sentry 'database' column value.

    Sentry CSV mixes Hive and HDFS entries in the same column:
      - Hive:  dbname, dbname.table, dbname.table.column
      - HDFS:  /user/... or hdfs://... or file:///...
    """
    if not database:
        return ServiceType.UNKNOWN
    db = database.strip()
    if db.startswith("/") or db.startswith("hdfs://") or db.startswith("file://"):
        return ServiceType.HDFS
    return ServiceType.HIVE


def strip_wildcard(*parts: str) -> tuple[str, ...]:
    """Replace '*' with '' for cleaner display."""
    return tuple("" if p == "*" else p for p in parts)


def make_policy_name(service_type: ServiceType, resource_str: str, index: int) -> str:
    """Generate a stable, human-readable policy name."""
    prefix = service_type.value.upper()
    short = hashlib.sha256(resource_str.encode()).hexdigest()[:8]
    return f"{prefix}-{short}-{index:04d}"


def load_json(filepath: str) -> dict:
    """Load a JSON file with UTF-8 encoding."""
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def ensure_dir(filepath: str) -> None:
    """Create parent directories for filepath if they don't exist."""
    d = os.path.dirname(filepath)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)


def merge_policies(policies: list["Policy"]) -> list["Policy"]:
    """Merge duplicate permission entries by target/action/principal.

    The IR stores permissions directly on Policy objects, so this helper keeps
    the first policy shell and removes duplicate PermissionEntry records.
    """
    merged: dict[tuple, "Policy"] = {}
    for p in policies:
        for perm in p.permissions:
            key = (
                perm.resource.service_type,
                tuple(perm.resource.to_guardian_data_source()),
                perm.action,
                perm.principal.name,
                perm.principal.principal_type,
                perm.grantable,
                perm.heritable,
                perm.administrative,
            )
            if key in merged:
                continue
            merged[key] = type(p)(
                source=p.source,
                service_type=p.service_type,
                service_name=p.service_name,
                resources=[perm.resource],
                permissions=[perm],
                description=p.description,
            )

    return list(merged.values())
