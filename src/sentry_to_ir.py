"""Parse a Sentry CSV privilege export into the unified IR (MigrationPlan).

Sentry CSV format (tab-delimited, from sentry_to_guardian.py):
    database, table, partition, column, principal_name, principal_type,
    privilege, grant_option, grant_time, grantor

Columns observed in real data: user, group, principal_name, principal_type,
database, table, partition, column, privilege, grant_option, grant_time, grantor

The 'database' column is overloaded:
  - Hive:  dbname, dbname.table, dbname.table.column
  - HDFS:  /user/path/..., file:///path, hdfs://nameservice/path
  - GLOBAL: used for global/admin-level grants; maps to dataSource ["GLOBAL"]

Principal types are matched against sentry_to_guardian.py logic:
  principal_type field: "ROLE" / "USER" / "GROUP"
"""

from __future__ import annotations

import csv
from typing import Optional

try:
    from .constants import SENTRY_PRINCIPAL_MAP
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
    from constants import SENTRY_PRINCIPAL_MAP  # noqa
    from models import (  # noqa
        MigrationPlan,
        PermissionEntry,
        Policy,
        Principal,
        PrincipalType,
        ResourcePath,
        ServiceType,
    )


# ── Action normalization ────────────────────────────────────────────────────

# Sentry privilege strings -> Guardian Hive actions
_SENTRY_TO_GUARDIAN: dict[str, str] = {
    "select": "SELECT",
    "read": "SELECT",
    "insert": "INSERT",
    "update": "UPDATE",
    "write": "UPDATE",
    "alter": "UPDATE",
    "create": "CREATE",
    "delete": "DELETE",
    "drop": "DELETE",
    "all": "ADMIN",
    "admin": "ADMIN",
    "*": "ADMIN",
    "index": "ADMIN",
    "lock": "ADMIN",
}


def _map_privilege(priv: str) -> str:
    """Map a Sentry privilege string to a Guardian action string."""
    lower = priv.strip().lower()
    return _SENTRY_TO_GUARDIAN.get(lower, lower.upper())


# ── Service type detection ──────────────────────────────────────────────────

def _detect_service_type(database: str) -> ServiceType:
    """Heuristically detect Hive vs HDFS from the database field."""
    if not database or database == "*" or database.upper() == "GLOBAL":
        return ServiceType.HIVE
    # HDFS paths
    if database.startswith("/") or database.startswith("hdfs://") or database.startswith("file://"):
        return ServiceType.HDFS
    # Otherwise assume Hive (db.table.column)
    return ServiceType.HIVE


# ── Resource extraction ─────────────────────────────────────────────────────

def _build_resource(service_type: ServiceType, row: dict[str, str]) -> ResourcePath:
    """Build a ResourcePath from a Sentry row."""
    if service_type == ServiceType.HDFS:
        path = row.get("database", "").strip()
        # Clean file:// or hdfs:// prefix
        if "://" in path:
            # Remove up to first / after ://
            import re
            path = re.sub(r'^(file|hdfs)://[^/]+', '', path)
        return ResourcePath(service_type=service_type, path=path or "/")

    # Hive path
    database = row.get("database", "").strip()
    table = row.get("table", "").strip()
    column = row.get("column", "").strip()
    partition = row.get("partition", "").strip()

    # If table field is empty, database might encode db.table
    if not table and "." in database and not database.startswith("/"):
        parts = database.split(".")
        database = parts[0] if parts else "*"
        table = parts[1] if len(parts) > 1 else "*"
        column = column or (parts[2] if len(parts) > 2 else "")

    return ResourcePath(
        service_type=service_type,
        database=database if database else "*",
        table=table if table else "",
        column=column if column else "",
    )


# ── Principal extraction ────────────────────────────────────────────────────

def _extract_principal(row: dict[str, str]) -> Optional[Principal]:
    """Extract a Principal from a Sentry row.

    Uses 'principal_name' and 'principal_type' columns.
    Maps Sentry type strings ("ROLE", "USER", "GROUP") to PrincipalType.
    """
    name = row.get("principal_name", "").strip()
    raw_type = row.get("principal_type", "").strip().upper()

    if not name or not raw_type:
        return None

    ptype_map = {
        "ROLE": PrincipalType.ROLE,
        "USER": PrincipalType.USER,
        "GROUP": PrincipalType.GROUP,
    }
    ptype = ptype_map.get(raw_type)
    if ptype is None:
        return None

    return Principal(name=name, principal_type=ptype)


# ── Main parser ─────────────────────────────────────────────────────────────

def parse_sentry_csv(filepath: str) -> MigrationPlan:
    """Parse a Sentry CSV privilege export into a MigrationPlan."""
    plan = MigrationPlan()
    plan.source_metadata["source"] = "sentry"
    plan.source_metadata["file"] = filepath

    with open(filepath, "r", newline="", encoding="utf-8-sig") as csvfile:
        # Auto-detect delimiter
        sample = csvfile.read(4096)
        csvfile.seek(0)
        delimiter = "\\t" if "\\t" in sample else ","
        reader = csv.DictReader(csvfile, delimiter=delimiter)

        row_count = 0
        for row in reader:
            row_count += 1
            database = row.get("database", "").strip()
            if not database:
                continue

            service_type = _detect_service_type(database)
            resource = _build_resource(service_type, row)

            # Map privilege
            privilege = row.get("privilege", "").strip()
            action = _map_privilege(privilege)

            principal = _extract_principal(row)
            if not principal:
                continue

            perm = PermissionEntry(
                action=action,
                resource=resource,
                principal=principal,
                grantable=False,
                heritable=True,
                administrative=True,
            )

            policy = Policy(
                source="sentry",
                service_type=service_type,
                service_name="sentry",  # No service discriminator in Sentry
                resources=[resource],
                permissions=[perm],
                description=f"Row {row_count}: {principal.name} {action} on {database}",
            )

            plan.policies.append(policy)

            # Collect into sets
            if principal.principal_type == PrincipalType.USER:
                plan.users.add(principal.name)
            elif principal.principal_type == PrincipalType.GROUP:
                plan.groups.add(principal.name)
            elif principal.principal_type == PrincipalType.ROLE:
                plan.roles.add(principal.name)

            # Track role-group and group-user assignments from Sentry data
            user_name = row.get("user", "").strip()
            group_name = row.get("group", "").strip()

            if group_name and user_name:
                plan.group_user_assignments.setdefault(group_name, set()).add(user_name)
                plan.groups.add(group_name)
                plan.users.add(user_name)

            if principal.principal_type == PrincipalType.ROLE and group_name:
                plan.role_group_assignments.setdefault(principal.name, set()).add(group_name)
                plan.groups.add(group_name)

    return plan


# ── CLI entry ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import json as _json
    import sys

    ap = argparse.ArgumentParser(description="Parse Sentry CSV export to IR")
    ap.add_argument("--input", "-i", required=True, help="Sentry CSV export file")
    ap.add_argument("--output", "-o", help="Output JSON file for IR (optional)")
    ap.add_argument("--summary", action="store_true", help="Print summary only")
    args = ap.parse_args()

    plan = parse_sentry_csv(args.input)

    if args.summary:
        print(f"Users:  {len(plan.users)}")
        print(f"Groups: {len(plan.groups)}")
        print(f"Roles:  {len(plan.roles)}")
        print(f"Policies: {len(plan.policies)}")
    else:
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
