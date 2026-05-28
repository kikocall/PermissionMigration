import json
import tempfile
import unittest
from pathlib import Path

from src.cli import _load_plan, _write_plan
from src.ir_to_guardian import generate_script
from src.models import (
    MigrationPlan,
    PermissionEntry,
    Policy,
    Principal,
    PrincipalType,
    ResourcePath,
    ServiceType,
)
from src.ranger_to_ir import parse_ranger_export
from src.sentry_to_ir import parse_sentry_csv
from src.utils import merge_policies


class PermissionMigrationTests(unittest.TestCase):
    def test_sentry_tsv_and_wildcard_expand_hdfs(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sentry.tsv"
            path.write_text(
                "database\ttable\tpartition\tcolumn\tprincipal_name\tprincipal_type\tprivilege\tgrant_option\tgrant_time\tgrantor\n"
                "/warehouse/db\t\t\t\tdata_role\tROLE\t*\tTRUE\t1\tadmin\n",
                encoding="utf-8",
            )

            plan = parse_sentry_csv(str(path))

        self.assertEqual(len(plan.policies), 1)
        actions = sorted(p.action for p in plan.policies[0].permissions)
        self.assertEqual(actions, ["ADMIN", "EXECUTE", "READ", "WRITE"])
        self.assertTrue(all(p.grantable for p in plan.policies[0].permissions))

    def test_sentry_hdfs_uri_keeps_old_path_parts(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sentry.csv"
            path.write_text(
                "database,table,partition,column,principal_name,principal_type,privilege,grant_option,grant_time,grantor\n"
                "hdfs://ns1/user/team,,,,data_role,ROLE,READ,FALSE,1,admin\n",
                encoding="utf-8",
            )

            plan = parse_sentry_csv(str(path))

        ds = plan.policies[0].permissions[0].resource.to_guardian_data_source()
        self.assertEqual(ds, ["PATH", "/", "ns1", "user", "team"])

    def test_guardian_script_preserves_principal_type_and_hdfs_datasource(self):
        plan = MigrationPlan(
            policies=[
                Policy(
                    source="unit",
                    service_type=ServiceType.HDFS,
                    service_name="hdfs",
                    resources=[ResourcePath(service_type=ServiceType.HDFS, path="/user/team")],
                    permissions=[
                        PermissionEntry(
                            action="READ",
                            resource=ResourcePath(service_type=ServiceType.HDFS, path="/user/team"),
                            principal=Principal("analysts", PrincipalType.GROUP),
                        )
                    ],
                )
            ],
            groups={"analysts"},
        )
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "grant.sh"
            generate_script(plan, str(out), base_url="https://guardian.example")
            text = out.read_text(encoding="utf-8")

        self.assertTrue(text.startswith("#!/usr/bin/env bash\n"))
        self.assertNotIn("\\n#", text)
        self.assertIn('"principalType": "GROUP"', text)
        self.assertIn('"dataSource": ["PATH", "/", "user", "team"]', text)

    def test_ranger_multiple_resource_values_are_expanded(self):
        data = {
            "policies": [
                {
                    "serviceType": "hive",
                    "service": "hive_service",
                    "name": "multi",
                    "resources": {
                        "database": {"values": ["db1"], "isExcludes": False, "isRecursive": False},
                        "table": {"values": ["t1", "t2"], "isExcludes": False, "isRecursive": False},
                        "column": {"values": ["*"], "isExcludes": False, "isRecursive": False},
                    },
                    "policyItems": [
                        {
                            "accesses": [{"type": "select", "isAllowed": True}],
                            "users": ["alice"],
                            "groups": [],
                            "roles": [],
                            "delegateAdmin": True,
                        }
                    ],
                }
            ]
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ranger.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            plan = parse_ranger_export(str(path))

        data_sources = sorted(
            tuple(perm.resource.to_guardian_data_source())
            for policy in plan.policies
            for perm in policy.permissions
        )
        self.assertEqual(
            data_sources,
            [("TABLE_OR_VIEW", "db1", "t1"), ("TABLE_OR_VIEW", "db1", "t2")],
        )
        self.assertTrue(all(perm.grantable for p in plan.policies for perm in p.permissions))

    def test_ranger_disabled_policy_is_ignored_and_hive_url_maps_to_path(self):
        data = {
            "policies": [
                {
                    "serviceType": "hive",
                    "service": "hive_service",
                    "name": "disabled",
                    "isEnabled": False,
                    "resources": {
                        "database": {"values": ["db1"], "isExcludes": False, "isRecursive": False},
                    },
                    "policyItems": [
                        {
                            "accesses": [{"type": "select", "isAllowed": True}],
                            "users": ["alice"],
                        }
                    ],
                },
                {
                    "serviceType": "hive",
                    "service": "hive_service",
                    "name": "url",
                    "resources": {
                        "url": {"values": ["/warehouse/path"], "isExcludes": False, "isRecursive": True},
                    },
                    "policyItems": [
                        {
                            "accesses": [{"type": "read", "isAllowed": True}],
                            "users": ["bob"],
                        }
                    ],
                },
            ]
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ranger.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            plan = parse_ranger_export(str(path))

        self.assertEqual(len(plan.policies), 1)
        perm = plan.policies[0].permissions[0]
        self.assertEqual(perm.principal.name, "bob")
        self.assertEqual(perm.resource.service_type, ServiceType.HDFS)
        self.assertEqual(perm.resource.to_guardian_data_source(), ["PATH", "/", "warehouse", "path"])

    def test_ir_roundtrip_keeps_permission_flags_and_partition(self):
        plan = MigrationPlan(
            policies=[
                Policy(
                    source="unit",
                    service_type=ServiceType.HIVE,
                    service_name="hive",
                    resources=[
                        ResourcePath(
                            service_type=ServiceType.HIVE,
                            database="db",
                            table="tbl",
                            partition="ds=20260528",
                            column="c1",
                        )
                    ],
                    permissions=[
                        PermissionEntry(
                            action="SELECT",
                            resource=ResourcePath(
                                service_type=ServiceType.HIVE,
                                database="db",
                                table="tbl",
                                partition="ds=20260528",
                                column="c1",
                            ),
                            principal=Principal("role1", PrincipalType.ROLE),
                            grantable=True,
                            heritable=False,
                            administrative=False,
                        )
                    ],
                    description="demo",
                )
            ],
            roles={"role1"},
        )
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ir.json"
            _write_plan(plan, str(path))
            loaded = _load_plan(str(path))

        perm = loaded.policies[0].permissions[0]
        self.assertTrue(perm.grantable)
        self.assertFalse(perm.heritable)
        self.assertFalse(perm.administrative)
        self.assertEqual(perm.resource.partition, "ds=20260528")
        self.assertEqual(loaded.policies[0].description, "demo")

    def test_merge_policies_deduplicates_permission_entries(self):
        resource = ResourcePath(service_type=ServiceType.HIVE, database="db", table="tbl")
        principal = Principal("role1", PrincipalType.ROLE)
        perm = PermissionEntry("SELECT", resource, principal)
        policies = [
            Policy("unit", ServiceType.HIVE, "hive", [resource], [perm], "a"),
            Policy("unit", ServiceType.HIVE, "hive", [resource], [perm], "a"),
        ]

        merged = merge_policies(policies)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].permissions[0].principal.name, "role1")


if __name__ == "__main__":
    unittest.main()
