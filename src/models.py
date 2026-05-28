"""IR (Intermediate Representation) data models for permission migration.

Defines canonical data structures that bridge Ranger JSON exports,
Sentry CSV exports, and Guardian REST API calls using the exact
payload schemas from sentry_to_guardian.py.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class PrincipalType(Enum):
    USER = "USER"
    GROUP = "GROUP"
    ROLE = "ROLE"


class ServiceType(Enum):
    HIVE = "hive"
    HDFS = "hdfs"
    HBASE = "hbase"
    YARN = "yarn"
    KAFKA = "kafka"
    ATLAS = "atlas"
    SOLR = "solr"
    OZONE = "ozone"
    KNOX = "knox"
    UNKNOWN = "unknown"

    @classmethod
    def from_string(cls, s: str) -> ServiceType:
        lower = s.lower()
        for member in cls:
            if member.value == lower:
                return member
        return cls.UNKNOWN


@dataclass
class ResourcePath:
    """Canonical resource location.

    For Hive: database, table, column (optional)
    For HDFS: path
    """
    service_type: ServiceType
    database: Optional[str] = None
    table: Optional[str] = None
    partition: Optional[str] = None
    column: Optional[str] = None
    path: Optional[str] = None

    def to_guardian_data_source(self) -> list[str]:
        """Convert to Guardian dataSource array.

        Hive: ["GLOBAL"] | ["TABLE_OR_VIEW", db, table, column...]
        HDFS: path as-is
        """
        if self.service_type == ServiceType.HDFS:
            raw_path = self.path or "/"
            if raw_path == "*" or raw_path.upper() == "GLOBAL":
                return ["GLOBAL"]
            path_parts = raw_path.split("/")
            return ["PATH", "/"] + [
                part for part in path_parts if part and part != "hdfs:"
            ]
        # Hive / table-based
        if not self.database or self.database == "*" or self.database == "GLOBAL":
            return ["GLOBAL"]
        parts = ["TABLE_OR_VIEW"]
        if self.database:
            parts.append(self.database)
        if self.table:
            parts.append(self.table)
        if self.partition:
            parts.append(self.partition)
        if self.column:
            parts.append(self.column)
        return parts


@dataclass
class Principal:
    """A user, group, or role principal."""
    name: str
    principal_type: PrincipalType

    @property
    def guardian_type(self) -> str:
        return self.principal_type.value


@dataclass
class PermissionEntry:
    """A single permission grant to a principal on a resource."""
    action: str
    resource: ResourcePath
    principal: Principal
    grantable: bool = False
    heritable: bool = True
    administrative: bool = True


@dataclass
class Policy:
    """A logical policy grouping one or more permission entries.

    Maps to a Ranger policy or a Sentry row.
    """
    source: str
    service_type: ServiceType
    service_name: str
    resources: list[ResourcePath] = field(default_factory=list)
    permissions: list[PermissionEntry] = field(default_factory=list)
    description: str = ""


@dataclass
class MigrationPlan:
    """The complete set of entities to migrate."""
    policies: list[Policy] = field(default_factory=list)
    users: set[str] = field(default_factory=set)
    groups: set[str] = field(default_factory=set)
    roles: set[str] = field(default_factory=set)
    role_group_assignments: dict[str, set[str]] = field(default_factory=dict)
    group_user_assignments: dict[str, set[str]] = field(default_factory=dict)
    source_metadata: dict = field(default_factory=dict)
