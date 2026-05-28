"""Shared constants for permission migration tools.

All Guardian API endpoints and payload schemas are derived from the
reference implementation in sentry_to_guardian.py.
"""
from __future__ import annotations

# Default Guardian API endpoint and credentials
DEFAULT_GUARDIAN_URL = "https://147.80.29.17:8380"
DEFAULT_GUARDIAN_TOKEN = "MajRDkS61VVw4kKESlnD-TDH"

# Default user profile values
DEFAULT_USER_DOMAIN = "@unionpay.io"
DEFAULT_USER_PASSWORD = "P@ssw0rd"

# Hive resource keys (in order for dataSource construction)
HIVE_RESOURCE_KEYS = ("database", "table", "column")

# HDFS resource key
HDFS_RESOURCE_KEY = "path"

# Ranger service types we can handle
KNOWN_RANGER_SERVICES = frozenset({
    "hive", "hdfs", "hbase", "yarn", "kafka", "atlas", "solr", "ozone", "knox",
})

# Sentry CSV expected column names
SENTRY_COLUMNS = [
    "database", "table", "partition", "column",
    "principal_name", "principal_type", "privilege",
    "grant_option", "grant_time", "grantor",
]

# Sentry principal_type -> Guardian principalType
SENTRY_PRINCIPAL_MAP = {
    "ROLE": "ROLE",
    "USER": "USER",
    "GROUP": "GROUP",
}

# Guardian component name per service type
GUARDIAN_COMPONENT = {
    "hive": "quark1",
    "hdfs": "tdfs1",
    "hbase": "quark1",
    "yarn": "quark1",
    "kafka": "quark1",
    "atlas": "quark1",
    "solr": "quark1",
    "ozone": "tdfs1",
    "knox": "quark1",
    "unknown": "quark1",
}

# Guardian API endpoints (relative to base URL)
ENDPOINT_USERS = "/api/v1/users"
ENDPOINT_GROUPS = "/api/v1/groups"
ENDPOINT_ROLES = "/api/v1/roles"
ENDPOINT_GROUP_ASSIGN = "/api/v1/groups/{name}/assign"
ENDPOINT_ROLE_ASSIGN = "/api/v1/roles/{name}/assign"
ENDPOINT_PERMS_GRANT = "/api/v1/perms/grant"

# Ranger action -> Guardian action (per service family)
RANGER_HDFS_TO_GUARDIAN: dict[str, str] = {
    "read": "READ",
    "write": "WRITE",
    "execute": "EXECUTE",
    "admin": "ADMIN",
    "all": "ADMIN",
}
RANGER_HIVE_TO_GUARDIAN: dict[str, str] = {
    "select": "SELECT",
    "read": "SELECT",
    "query": "SELECT",
    "insert": "INSERT",
    "update": "UPDATE",
    "write": "UPDATE",
    "alter": "UPDATE",
    "create": "CREATE",
    "delete": "DROP",
    "drop": "DROP",
    "all": "ADMIN",
    "admin": "ADMIN",
    "index": "ADMIN",
    "lock": "ADMIN",
    "refresh": "ADMIN",
    "repladmin": "ADMIN",
    "rwstorage": "ADMIN",
    "serviceadmin": "ADMIN",
    "tempudfadmin": "ADMIN",
}

GUARDIAN_HIVE_EXPANDED = ["CREATE", "SELECT", "INSERT", "UPDATE", "DELETE", "ADMIN"]
GUARDIAN_HDFS_EXPANDED = ["READ", "WRITE", "EXECUTE", "ADMIN"]