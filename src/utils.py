"""Shared utility functions for permission migration tools."""

import hashlib
import json
import os
from typing import Optional

# Try to import from sibling modules
try:
    from .models import ServiceType
except ImportError:
    from models import ServiceType  # noqa: F811


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
