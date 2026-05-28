"""CLI entry point for permission migration toolchain.

Unified CLI supporting:
  ranger  - Parse Ranger JSON export to IR
  sentry  - Parse Sentry CSV export to IR
  guardian - Generate Guardian API shell script from IR
  migrate  - Full end-to-end migration (ranger/sentry -> Guardian script)

Usage:
  python -m src.cli ranger  --input ranger.json --output ir.json
  python -m src.cli sentry  --input sentry.csv  --output ir.json
  python -m src.cli guardian --input ir.json --output script.sh
  python -m src.cli migrate  --source ranger --source-input ranger.json --output script.sh
  python -m src.cli migrate  --source sentry --source-input sentry.csv --output script.sh
"""

from __future__ import annotations

import argparse
import json
import sys
import os


def _ensure_src_in_path():
    """Ensure src/ is importable."""
    src_dir = os.path.dirname(os.path.abspath(__file__))
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)


def cmd_ranger(args):
    from ranger_to_ir import parse_ranger_export
    plan = parse_ranger_export(args.input)
    _write_plan(plan, args.output)
    _print_summary(plan)


def cmd_sentry(args):
    from sentry_to_ir import parse_sentry_csv
    plan = parse_sentry_csv(args.input)
    _write_plan(plan, args.output)
    _print_summary(plan)


def cmd_guardian(args):
    plan = _load_plan(args.input)
    from ir_to_guardian import generate_script
    output = args.output or "permission_migration.sh"
    path = generate_script(plan, output, base_url=args.base_url or None)
    print(f"Guardian script generated: {path}")


def cmd_migrate(args):
    """Full migration: source -> IR -> Guardian script."""
    if args.source == "ranger":
        from ranger_to_ir import parse_ranger_export
        plan = parse_ranger_export(args.source_input)
    elif args.source == "sentry":
        from sentry_to_ir import parse_sentry_csv
        plan = parse_sentry_csv(args.source_input)
    else:
        print(f"Unknown source type: {args.source}", file=sys.stderr)
        sys.exit(1)

    if args.save_ir:
        _write_plan(plan, args.save_ir)
        print(f"IR saved to: {args.save_ir}")

    _print_summary(plan)

    from ir_to_guardian import generate_script
    output = args.output or "permission_migration.sh"
    path = generate_script(plan, output, base_url=args.base_url or None)
    print(f"Guardian script generated: {path}")


# ── Helpers ─────────────────────────────────────────────────────────────────

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
        "groups": sorted(plan.groups),
        "roles": sorted(plan.roles),
        "role_group_assignments": {k: sorted(v) for k, v in plan.role_group_assignments.items()},
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
    plan.groups = set(d.get("groups", []))
    plan.roles = set(d.get("roles", []))
    plan.role_group_assignments = {k: set(v) for k, v in d.get("role_group_assignments", {}).items()}
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
    p_sentry = sub.add_parser("sentry", help="Parse Sentry CSV export to IR")
    p_sentry.add_argument("--input", "-i", required=True)
    p_sentry.add_argument("--output", "-o")

    # guardian
    p_guardian = sub.add_parser("guardian", help="Generate Guardian API script from IR")
    p_guardian.add_argument("--input", "-i", required=True)
    p_guardian.add_argument("--output", "-o")
    p_guardian.add_argument("--base-url", help="Guardian API base URL")

    # migrate (end-to-end)
    p_migrate = sub.add_parser("migrate", help="Full migration: source -> IR -> Guardian script")
    p_migrate.add_argument("--source", required=True, choices=["ranger", "sentry"])
    p_migrate.add_argument("--source-input", required=True)
    p_migrate.add_argument("--output", "-o")
    p_migrate.add_argument("--base-url")
    p_migrate.add_argument("--save-ir", help="Save intermediate IR to file")

    args = parser.parse_args()

    if args.command == "ranger":
        cmd_ranger(args)
    elif args.command == "sentry":
        cmd_sentry(args)
    elif args.command == "guardian":
        cmd_guardian(args)
    elif args.command == "migrate":
        cmd_migrate(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
