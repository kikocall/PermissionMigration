"""Convert the unified IR (MigrationPlan) to a Guardian REST API shell script.

Generates a self-contained shell script using the exact Guardian API
call patterns from sentry_to_guardian.py:
  1. POST /api/v1/users          - create users
  2. POST /api/v1/groups         - create groups
  3. POST /api/v1/roles          - create roles
  4. PUT  /api/v1/groups/{n}/assign - add users to groups
  5. PUT  /api/v1/roles/{n}/assign  - add groups to roles
  6. PUT  /api/v1/perms/grant      - grant permissions to roles
"""

from __future__ import annotations

import json
import os
from typing import Optional

try:
    from .constants import (
        DEFAULT_GUARDIAN_TOKEN,
        DEFAULT_GUARDIAN_URL,
        DEFAULT_USER_DOMAIN,
        DEFAULT_USER_PASSWORD,
        ENDPOINT_USERS,
        ENDPOINT_GROUPS,
        ENDPOINT_ROLES,
        ENDPOINT_GROUP_ASSIGN,
        ENDPOINT_ROLE_ASSIGN,
        ENDPOINT_PERMS_GRANT,
        GUARDIAN_COMPONENT,
    )
    from .models import (
        MigrationPlan,
        PermissionEntry,
        Policy,
        PrincipalType,
        ServiceType,
    )
except ImportError:
    from constants import (  # noqa
        DEFAULT_GUARDIAN_TOKEN,
        DEFAULT_GUARDIAN_URL,
        DEFAULT_USER_DOMAIN,
        DEFAULT_USER_PASSWORD,
        ENDPOINT_USERS,
        ENDPOINT_GROUPS,
        ENDPOINT_ROLES,
        ENDPOINT_GROUP_ASSIGN,
        ENDPOINT_ROLE_ASSIGN,
        ENDPOINT_PERMS_GRANT,
        GUARDIAN_COMPONENT,
    )
    from models import (  # noqa
        MigrationPlan,
        PermissionEntry,
        Policy,
        PrincipalType,
        ServiceType,
    )


# ── Helpers ─────────────────────────────────────────────────────────────────

def _escape_sh(s: str) -> str:
    """Escape a string for single-quote shell context."""
    return s.replace("'", "\\'")

def _json_sh(obj) -> str:
    """JSON-encode an object and escape for shell."""
    return _escape_sh(json.dumps(obj, ensure_ascii=False))

def _token_url(base: str, endpoint: str) -> str:
    """Build a full URL with the access token query parameter."""
    sep = "&" if "?" in endpoint else "?"
    return f"{base}{endpoint}{sep}guardian_access_token={DEFAULT_GUARDIAN_TOKEN}"

def _curl_cmd(method: str, url: str, body: dict) -> str:
    """Format a curl command matching sentry_to_guardian.py style."""
    body_json = json.dumps(body, ensure_ascii=False)
    return (
        f"curl -k -X {method} '{_escape_sh(url)}' "
        f"-H 'accept: */*' -H 'Content-Type: application/json' "
        f"-d '{_escape_sh(body_json)}'\n"
    )


# ── Section generators ──────────────────────────────────────────────────────

def _gen_users(plan: MigrationPlan, base_url: str) -> list[str]:
    """Generate create-user commands."""
    lines: list[str] = []
    for user_name in sorted(plan.users):
        body = {
            "userEmail": f"{user_name}{DEFAULT_USER_DOMAIN}",
            "userName": user_name,
            "userPassword": DEFAULT_USER_PASSWORD,
        }
        url = _token_url(base_url, ENDPOINT_USERS)
        lines.append(_curl_cmd("POST", url, body))
    return lines

def _gen_groups(plan: MigrationPlan, base_url: str) -> list[str]:
    """Generate create-group commands."""
    lines: list[str] = []
    for group_name in sorted(plan.groups):
        body = {"groupName": group_name}
        url = _token_url(base_url, ENDPOINT_GROUPS)
        lines.append(_curl_cmd("POST", url, body))
    return lines

def _gen_roles(plan: MigrationPlan, base_url: str) -> list[str]:
    """Generate create-role commands."""
    lines: list[str] = []
    for role_name in sorted(plan.roles):
        body = {"roleName": role_name}
        url = _token_url(base_url, ENDPOINT_ROLES)
        lines.append(_curl_cmd("POST", url, body))
    return lines

def _gen_group_assignments(plan: MigrationPlan, base_url: str) -> list[str]:
    """Generate assign-user-to-group commands.

    PUT /api/v1/groups/{group_name}/assign
    Body: { "groupName": "...", "name": "user_name", "principalType": "USER" }
    """
    lines: list[str] = []
    for group_name in sorted(plan.group_user_assignments):
        endpoint = ENDPOINT_GROUP_ASSIGN.replace("{name}", group_name)
        url = _token_url(base_url, endpoint)
        for user_name in sorted(plan.group_user_assignments[group_name]):
            body = {
                "groupName": group_name,
                "name": user_name,
                "principalType": "USER",
            }
            lines.append(_curl_cmd("PUT", url, body))
    return lines

def _gen_role_assignments(plan: MigrationPlan, base_url: str) -> list[str]:
    """Generate assign-group-to-role commands.

    PUT /api/v1/roles/{role_name}/assign
    Body: { "name": "group_name", "principalType": "GROUP", "roleName": "role_name" }
    """
    lines: list[str] = []
    for role_name in sorted(plan.role_group_assignments):
        endpoint = ENDPOINT_ROLE_ASSIGN.replace("{name}", role_name)
        url = _token_url(base_url, endpoint)
        for group_name in sorted(plan.role_group_assignments[role_name]):
            body = {
                "name": group_name,
                "principalType": "GROUP",
                "roleName": role_name,
            }
            lines.append(_curl_cmd("PUT", url, body))
    return lines

def _gen_permissions(plan: MigrationPlan, base_url: str) -> list[str]:
    """Generate grant-permission-to-role commands.

    PUT /api/v1/perms/grant
    Body: {
      "name": "role_name",
      "permissionVo": {
        "action": "SELECT",
        "administrative": true,
        "component": "quark1",
        "dataSource": ["TABLE_OR_VIEW", "db", "table"],
        "grantable": false,
        "heritable": true
      },
      "principalType": "ROLE"
    }
    """
    lines: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    url = _token_url(base_url, ENDPOINT_PERMS_GRANT)

    for policy in plan.policies:
        component = GUARDIAN_COMPONENT.get(policy.service_type.value, "quark1")
        for perm in policy.permissions:
            # Determine role name
            role_name = perm.principal.name if perm.principal.principal_type == PrincipalType.ROLE else perm.principal.name
            ds = perm.resource.to_guardian_data_source()
            dedup_key = (role_name, perm.action, json.dumps(ds, sort_keys=True))
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            body = {
                "name": role_name,
                "permissionVo": {
                    "action": perm.action.upper(),
                    "administrative": perm.administrative,
                    "component": component,
                    "dataSource": ds,
                    "grantable": perm.grantable,
                    "heritable": perm.heritable,
                },
                "principalType": "ROLE",
            }
            lines.append(_curl_cmd("PUT", url, body))
    return lines


# ── Main entry ──────────────────────────────────────────────────────────────

def generate_script(
    plan: MigrationPlan,
    output_path: str,
    base_url: Optional[str] = None,
) -> str:
    """Generate a Guardian API shell script and write it to output_path.

    Returns the absolute path to the generated script.
    """
    url = (base_url or os.environ.get("GUARDIAN_URL", DEFAULT_GUARDIAN_URL)).rstrip("/")

    sections: list[tuple[str, list[str]]] = [
        ("# ── Create Users ──", _gen_users(plan, url)),
        ("# ── Create Groups ──", _gen_groups(plan, url)),
        ("# ── Create Roles ──", _gen_roles(plan, url)),
        ("# ── Assign Users to Groups ──", _gen_group_assignments(plan, url)),
        ("# ── Assign Groups to Roles ──", _gen_role_assignments(plan, url)),
        ("# ── Grant Permissions to Roles ──", _gen_permissions(plan, url)),
    ]

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("#!/usr/bin/env bash\\n")
        f.write(f"# Guardian Permission Migration Script\\n")
        f.write(f"# Generated from IR with {plan.source_metadata.get('source', 'unknown')} source\\n")
        f.write(f"# Base URL: {url}\\n")
        f.write("set -euo pipefail\\n\\n")

        for header, lines in sections:
            if not lines:
                continue
            f.write(header + "\\n")
            for line in lines:
                f.write(line)
            f.write("\\n")

        f.write("echo 'Migration script completed.'\\n")

    return os.path.abspath(output_path)
