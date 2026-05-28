"""Parse a Ranger JSON policy export into the unified IR (MigrationPlan).

Ranger JSON format (from Ranger_export_example.json):
  {
    "policies": [
      {
        "serviceType": "hive",
        "name": "policy-name",
        "resources": {
          "database": {"values": ["db1"], "isExcludes": false, "isRecursive": false},
          "table":    {"values": ["tbl1"], "isExcludes": false, "isRecursive": false},
          "column":   {"values": ["col1"], "isExcludes": false, "isRecursive": false}
        },
        "policyItems": [
          {
            "accesses": [{"type": "select", "isAllowed": true}],
            "users": ["user1"],
            "groups": ["group1"],
            "roles": ["role1"]
          }
        ],
        "denyPolicyItems": [],
        "isAuditEnabled": true
      }
    ]
  }
"""

from __future__ import annotations

import json
from typing import Any, Optional

try:
    from .constants import (
        RANGER_HDFS_TO_GUARDIAN,
        RANGER_HIVE_TO_GUARDIAN,
        GUARDIAN_HIVE_EXPANDED,
        GUARDIAN_HDFS_EXPANDED,
    )
    from .models import (
        MigrationPlan,
        PermissionEntry,
        Policy,
        Principal,
        PrincipalType,
        ResourcePath,
        ServiceType,
    )
except ImportError:
    from constants import (  # noqa
        RANGER_HDFS_TO_GUARDIAN,
        RANGER_HIVE_TO_GUARDIAN,
        GUARDIAN_HIVE_EXPANDED,
        GUARDIAN_HDFS_EXPANDED,
    )
    from models import (  # noqa
        MigrationPlan,
        PermissionEntry,
        Policy,
        Principal,
        PrincipalType,
        ResourcePath,
        ServiceType,
    )


# ── Action mapping ──────────────────────────────────────────────────────────

_RANGER_ACTION_MAP: dict[str, dict[str, str]] = {
    "hive": RANGER_HIVE_TO_GUARDIAN,
    "hdfs": RANGER_HDFS_TO_GUARDIAN,
}

_EXPAND_SET: dict[str, frozenset[str]] = {
    "hive": frozenset(GUARDIAN_HIVE_EXPANDED),
    "hdfs": frozenset(GUARDIAN_HDFS_EXPANDED),
}


def _map_actions(service_type: str, ranger_actions: list[str]) -> frozenset[str]:
    """Map a list of Ranger action strings to Guardian action strings."""
    mapping = _RANGER_ACTION_MAP.get(service_type, {})
    expanded: set[str] = set()
    for act in ranger_actions:
        lower = act.lower()
        if lower == "all" or lower == "admin":
            expanded |= (_EXPAND_SET.get(service_type, frozenset()) or set())
        else:
            mapped = mapping.get(lower, lower.upper())
            expanded.add(mapped)
    return frozenset(expanded)


# ── Principal extraction ────────────────────────────────────────────────────

def _collect_principals(item: dict) -> list[Principal]:
    """Extract all principals (users, groups, roles) from a policy item."""
    results: list[Principal] = []
    for user in item.get("users", []) or []:
        if user:
            results.append(Principal(name=user, principal_type=PrincipalType.USER))
    for group in item.get("groups", []) or []:
        if group:
            results.append(Principal(name=group, principal_type=PrincipalType.GROUP))
    for role in item.get("roles", []) or []:
        if role:
            results.append(Principal(name=role, principal_type=PrincipalType.ROLE))
    return results


# ── Resource building ───────────────────────────────────────────────────────

def _safe_first(values: Optional[list], default: str = "*") -> str:
    if values and values[0] and values[0] != "*":
        return values[0]
    return default


def _build_resource(
    service_type: ServiceType,
    resources: dict[str, Any],
) -> ResourcePath:
    st = service_type

    if st == ServiceType.HIVE:
        return ResourcePath(
            service_type=st,
            database=_safe_first(resources.get("database", {}).get("values") if isinstance(resources.get("database"), dict) else None),
            table=_safe_first(resources.get("table", {}).get("values") if isinstance(resources.get("table"), dict) else None),
            column=_safe_first(resources.get("column", {}).get("values") if isinstance(resources.get("column"), dict) else None),
        )

    if st == ServiceType.HDFS:
        return ResourcePath(
            service_type=st,
            path=_safe_first(resources.get("path", {}).get("values") if isinstance(resources.get("path"), dict) else None, "/"),
        )

    # Generic fallback for other service types
    return ResourcePath(service_type=st)


# ── Main parser ─────────────────────────────────────────────────────────────

def parse_ranger_policy(raw: dict) -> Optional[Policy]:
    """Convert a single Ranger policy dict to an IR Policy."""
    svc_str = raw.get("serviceType", raw.get("service", "")).lower()
    try:
        service_type = ServiceType.from_string(svc_str)
    except Exception:
        return None
    if service_type == ServiceType.UNKNOWN:
        return None

    resources_raw = raw.get("resources", {})
    resource = _build_resource(service_type, resources_raw)

    permissions: list[PermissionEntry] = []

    # Process allow and deny items
    for item_key in ("policyItems", "denyPolicyItems"):
        for item in raw.get(item_key, []) or []:
            accesses = item.get("accesses", []) or []
            ranger_actions = [a.get("type", "") for a in accesses if a.get("isAllowed", True)]
            if not ranger_actions:
                continue
            guardian_actions = _map_actions(svc_str, ranger_actions)
            principals = _collect_principals(item)

            for principal in principals:
                for action in guardian_actions:
                    permissions.append(PermissionEntry(
                        action=action,
                        resource=resource,
                        principal=principal,
                        grantable=False,
                        heritable=True,
                        administrative=True,
                    ))

    if not permissions:
        return None

    return Policy(
        source="ranger",
        service_type=service_type,
        service_name=raw.get("service", ""),
        resources=[resource],
        permissions=permissions,
        description=raw.get("description", raw.get("name", "")),
    )


def parse_ranger_export(filepath: str) -> MigrationPlan:
    """Parse a full Ranger JSON export file into a MigrationPlan."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    plan = MigrationPlan()
    plan.source_metadata["source"] = "ranger"
    plan.source_metadata["host"] = data.get("metaDataInfo", {}).get("Host name", "")

    for raw in data.get("policies", []) or []:
        policy = parse_ranger_policy(raw)
        if policy is None:
            continue
        plan.policies.append(policy)

        for perm in policy.permissions:
            p = perm.principal
            if p.principal_type == PrincipalType.USER:
                plan.users.add(p.name)
            elif p.principal_type == PrincipalType.GROUP:
                plan.groups.add(p.name)
            elif p.principal_type == PrincipalType.ROLE:
                plan.roles.add(p.name)
                # Groups assigned to roles from Ranger policyItems
                for perm2 in policy.permissions:
                    if perm2.principal.principal_type == PrincipalType.GROUP:
                        plan.role_group_assignments.setdefault(p.name, set()).add(perm2.principal.name)

    return plan


# ── CLI entry ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys
    ap = argparse.ArgumentParser(description="Parse Ranger JSON export to IR")
    ap.add_argument("--input", "-i", required=True, help="Ranger JSON export file")
    ap.add_argument("--output", "-o", help="Output JSON file for IR (optional)")
    ap.add_argument("--summary", action="store_true", help="Print summary only")
    args = ap.parse_args()

    plan = parse_ranger_export(args.input)
    if args.summary:
        print(f"Users:  {len(plan.users)}")
        print(f"Groups: {len(plan.groups)}")
        print(f"Roles:  {len(plan.roles)}")
        print(f"Policies: {len(plan.policies)}")
    else:
        import json as _json
        out = {
            "users": sorted(plan.users),
            "groups": sorted(plan.groups),
            "roles": sorted(plan.roles),
            "policy_count": len(plan.policies),
        }
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                _json.dump(out, f, ensure_ascii=False, indent=2)
            print(f"IR written to {args.output}")
        else:
            _json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
