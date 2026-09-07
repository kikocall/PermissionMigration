import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.cli import _load_plan, _write_plan, cmd_guardian
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
from src.sentry_sql_to_ir import parse_sentry_sql_dump
from src.utils import filter_plan_by_users, merge_policies


class PermissionMigrationTests(unittest.TestCase):
    def test_filter_plan_by_users_keeps_direct_and_inherited_permissions(self):
        def policy(name, principal_type, action="SELECT"):
            resource = ResourcePath(
                service_type=ServiceType.HIVE,
                database="analytics",
                table=name,
            )
            principal = Principal(name, principal_type)
            return Policy(
                source="unit",
                service_type=ServiceType.HIVE,
                service_name="hive",
                resources=[resource],
                permissions=[PermissionEntry(action, resource, principal)],
            )

        plan = MigrationPlan(
            policies=[
                policy("alice", PrincipalType.USER),
                policy("bob", PrincipalType.USER),
                policy("alice_direct_role", PrincipalType.ROLE),
                policy("bob_role", PrincipalType.ROLE),
                policy("alice_group_role", PrincipalType.ROLE),
                policy("alice_group", PrincipalType.GROUP),
            ],
            users={"alice", "bob"},
            groups={"alice_group", "bob_group"},
            roles={"alice_direct_role", "bob_role", "alice_group_role"},
            role_user_assignments={
                "alice_direct_role": {"alice"},
                "bob_role": {"bob"},
            },
            group_user_assignments={
                "alice_group": {"alice"},
                "bob_group": {"bob"},
            },
            role_group_assignments={
                "alice_group_role": {"alice_group"},
                "bob_role": {"bob_group"},
            },
            source_metadata={"source": "unit"},
        )

        filtered = filter_plan_by_users(plan, {"alice"})

        self.assertEqual(filtered.users, {"alice"})
        self.assertEqual(filtered.groups, {"alice_group"})
        self.assertEqual(filtered.roles, {"alice_direct_role", "alice_group_role"})
        self.assertEqual(
            filtered.role_user_assignments,
            {"alice_direct_role": {"alice"}},
        )
        self.assertEqual(
            filtered.group_user_assignments,
            {"alice_group": {"alice"}},
        )
        principals = {
            pm.principal.name
            for item in filtered.policies
            for pm in item.permissions
        }
        self.assertEqual(
            principals,
            {"alice", "alice_direct_role", "alice_group_role", "alice_group"},
        )
        self.assertEqual(filtered.source_metadata["user_filter"]["original_user_count"], 2)
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "selected.sh"
            generate_script(
                filtered,
                str(output),
                base_url="https://guardian.example",
                access_token="unit-token",
            )
            script = output.read_text(encoding="utf-8")
        self.assertIn('"userName": "alice"', script)
        self.assertNotIn('"userName": "bob"', script)
        self.assertNotIn('"roleName": "bob_role"', script)

    def test_filter_plan_by_users_rejects_unknown_user(self):
        plan = MigrationPlan(users={"alice"})
        with self.assertRaisesRegex(ValueError, "not-found"):
            filter_plan_by_users(plan, {"alice", "not-found"})

    def test_guardian_cli_filters_a_saved_full_ir(self):
        resource = ResourcePath(service_type=ServiceType.HIVE, database="db", table="orders")
        plan = MigrationPlan(
            policies=[
                Policy(
                    source="unit",
                    service_type=ServiceType.HIVE,
                    service_name="hive",
                    resources=[resource],
                    permissions=[
                        PermissionEntry(
                            "SELECT",
                            resource,
                            Principal("alice_role", PrincipalType.ROLE),
                        )
                    ],
                )
            ],
            users={"alice", "bob"},
            roles={"alice_role"},
            role_user_assignments={"alice_role": {"alice"}},
        )
        with tempfile.TemporaryDirectory() as td:
            full_ir = Path(td) / "full.json"
            selected_users = Path(td) / "selected.txt"
            output = Path(td) / "selected.sh"
            _write_plan(plan, str(full_ir))
            selected_users.write_text("alice\n", encoding="utf-8")
            cmd_guardian(SimpleNamespace(
                input=str(full_ir),
                output=str(output),
                base_url="https://guardian.example",
                access_token="unit-token",
                hive_component="unit-hive",
                hdfs_component=None,
                users=None,
                users_file=str(selected_users),
                export_users=None,
            ))
            script = output.read_text(encoding="utf-8")

        self.assertIn('"userName": "alice"', script)
        self.assertIn('"action": "SELECT"', script)
        self.assertIn('"roleName": "alice_role"', script)
        self.assertNotIn("bob", script)

    def test_sentry_mysql_dump_joins_roles_groups_users_and_privileges(self):
        dump = r"""
CREATE TABLE `sentry_db_privilege` (
  `DB_PRIVILEGE_ID` bigint(20) NOT NULL,
  `PRIVILEGE_SCOPE` varchar(32) NOT NULL,
  `SERVER_NAME` varchar(128) NOT NULL,
  `DB_NAME` varchar(128) DEFAULT '__NULL__',
  `TABLE_NAME` varchar(128) DEFAULT '__NULL__',
  `COLUMN_NAME` varchar(128) DEFAULT '__NULL__',
  `URI` varchar(4000) DEFAULT '__NULL__',
  `ACTION` varchar(128) NOT NULL,
  `CREATE_TIME` bigint(20) NOT NULL,
  `WITH_GRANT_OPTION` char(1) NOT NULL
);
CREATE TABLE `sentry_role` (`ROLE_ID` bigint NOT NULL, `ROLE_NAME` varchar(128), `CREATE_TIME` bigint);
CREATE TABLE `sentry_group` (`GROUP_ID` bigint NOT NULL, `GROUP_NAME` varchar(128), `CREATE_TIME` bigint);
CREATE TABLE `sentry_user` (`USER_ID` bigint NOT NULL, `USER_NAME` varchar(128), `CREATE_TIME` bigint);
CREATE TABLE `sentry_role_db_privilege_map` (`ROLE_ID` bigint, `DB_PRIVILEGE_ID` bigint, `GRANTOR_PRINCIPAL` varchar(128));
CREATE TABLE `sentry_user_db_privilege_map` (`USER_ID` bigint, `DB_PRIVILEGE_ID` bigint, `GRANTOR_PRINCIPAL` varchar(128));
CREATE TABLE `sentry_role_group_map` (`ROLE_ID` bigint, `GROUP_ID` bigint, `GRANTOR_PRINCIPAL` varchar(128));
CREATE TABLE `sentry_role_user_map` (`ROLE_ID` bigint, `USER_ID` bigint, `GRANTOR_PRINCIPAL` varchar(128));
CREATE TABLE `authz_path` (`PATH_ID` bigint, `PATH_NAME` varchar(4000), `AUTHZ_OBJ_ID` bigint);
INSERT INTO `sentry_role` VALUES (1,'finance_role',1000);
INSERT INTO `sentry_group` VALUES (10,'finance_group',1000);
INSERT INTO `sentry_user` VALUES (20,'alice',1000),(21,'o\\'reilly',1000);
INSERT INTO `sentry_db_privilege` VALUES
  (100,'TABLE','server1','sales','orders','__NULL__','__NULL__','select',1000,'Y'),
  (101,'URI','server1','__NULL__','__NULL__','__NULL__','hdfs://ns1/data/team','all',1000,'N');
INSERT INTO `sentry_role_db_privilege_map` VALUES (1,100,'admin');
INSERT INTO `sentry_user_db_privilege_map` VALUES (20,101,'admin');
INSERT INTO `sentry_role_group_map` VALUES (1,10,'admin');
INSERT INTO `sentry_role_user_map` VALUES (1,20,'admin');
INSERT INTO `authz_path` VALUES (1,'/warehouse/sales/orders',123);
"""
        dump = dump.replace("o\\\\\\\\'reilly", "o\\\\'reilly")
        dump = dump.replace("o" + chr(92) * 2 + "'reilly", "o" + chr(92) + "'reilly")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sentry.sql"
            path.write_text(dump, encoding="utf-8")
            plan = parse_sentry_sql_dump(str(path))

            script_path = Path(td) / "guardian.sh"
            generate_script(
                plan,
                str(script_path),
                base_url="https://guardian.example",
                access_token="unit-token",
            )
            guardian_script = script_path.read_text(encoding="utf-8")

        self.assertEqual(plan.roles, {"finance_role"})
        self.assertEqual(plan.groups, {"finance_group"})
        self.assertEqual(plan.users, {"alice", "o'reilly"})
        self.assertEqual(plan.role_group_assignments, {"finance_role": {"finance_group"}})
        self.assertEqual(plan.role_user_assignments, {"finance_role": {"alice"}})
        self.assertIn('"name": "alice", "principalType": "USER", "roleName": "finance_role"', guardian_script)
        perms = [pm for policy in plan.policies for pm in policy.permissions]
        self.assertEqual(len(perms), 5)
        role_select = next(pm for pm in perms if pm.principal.name == "finance_role")
        self.assertEqual(role_select.resource.to_guardian_data_source(), ["TABLE_OR_VIEW", "sales", "orders"])
        self.assertTrue(role_select.grantable)
        alice_actions = sorted(pm.action for pm in perms if pm.principal.name == "alice")
        self.assertEqual(alice_actions, ["ADMIN", "EXECUTE", "READ", "WRITE"])
        alice_ds = next(pm.resource.to_guardian_data_source() for pm in perms if pm.principal.name == "alice")
        self.assertEqual(alice_ds, ["PATH", "/", "ns1", "data", "team"])

    def test_sentry_mysql_dump_supports_insert_column_lists_and_nulls(self):
        dump = """
CREATE TABLE `SENTRY_ROLE` (`ROLE_ID` bigint, `ROLE_NAME` varchar(128), `CREATE_TIME` bigint);
CREATE TABLE `SENTRY_DB_PRIVILEGE` (
 `DB_PRIVILEGE_ID` bigint, `PRIVILEGE_SCOPE` varchar(32), `SERVER_NAME` varchar(128),
 `DB_NAME` varchar(128), `TABLE_NAME` varchar(128), `COLUMN_NAME` varchar(128),
 `URI` varchar(4000), `ACTION` varchar(128), `CREATE_TIME` bigint, `WITH_GRANT_OPTION` char(1)
);
CREATE TABLE `SENTRY_ROLE_DB_PRIVILEGE_MAP` (`ROLE_ID` bigint, `DB_PRIVILEGE_ID` bigint);
INSERT INTO SENTRY_ROLE (`ROLE_NAME`,`ROLE_ID`,`CREATE_TIME`) VALUES ('r1',1,0);
INSERT INTO SENTRY_DB_PRIVILEGE
 (`ACTION`,`DB_PRIVILEGE_ID`,`PRIVILEGE_SCOPE`,`SERVER_NAME`,`DB_NAME`,`TABLE_NAME`,`COLUMN_NAME`,`URI`,`CREATE_TIME`,`WITH_GRANT_OPTION`)
 VALUES ('select',2,'DATABASE','server1','analytics',NULL,NULL,NULL,0,'N');
INSERT INTO SENTRY_ROLE_DB_PRIVILEGE_MAP (`DB_PRIVILEGE_ID`,`ROLE_ID`) VALUES (2,1);
"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "columns.sql"
            path.write_text(dump, encoding="utf-8")
            plan = parse_sentry_sql_dump(str(path))

        perm = plan.policies[0].permissions[0]
        self.assertEqual(perm.principal.name, "r1")
        self.assertEqual(perm.resource.to_guardian_data_source(), ["TABLE_OR_VIEW", "analytics"])

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

    def test_hdfs_global_like_paths_map_to_root_path(self):
        for raw_path in ["/", "*", "/*", "GLOBAL", "global"]:
            with self.subTest(raw_path=raw_path):
                resource = ResourcePath(service_type=ServiceType.HDFS, path=raw_path)
                self.assertEqual(resource.to_guardian_data_source(), ["PATH", "/"])

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
            generate_script(
                plan,
                str(out),
                base_url="https://guardian.example",
                access_token="unit-token",
                component_overrides={"hdfs": "unit-hdfs"},
            )
            text = out.read_text(encoding="utf-8")

        self.assertTrue(text.startswith("#!/usr/bin/env bash\n"))
        self.assertNotIn("\\n#", text)
        self.assertIn("guardian_access_token=unit-token", text)
        self.assertIn('"principalType": "GROUP"', text)
        self.assertIn('"dataSource": ["PATH", "/", "user", "team"]', text)
        self.assertIn('"component": "unit-hdfs"', text)

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

    def test_ranger_hdfs_wildcard_path_maps_to_root_path(self):
        data = {
            "policies": [
                {
                    "serviceType": "hdfs",
                    "service": "hdfs_service",
                    "name": "root",
                    "resources": {
                        "path": {"values": ["*"], "isExcludes": False, "isRecursive": True},
                    },
                    "policyItems": [
                        {
                            "accesses": [{"type": "read", "isAllowed": True}],
                            "users": ["hdfs"],
                        }
                    ],
                }
            ]
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ranger.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            plan = parse_ranger_export(str(path))

        self.assertEqual(
            plan.policies[0].permissions[0].resource.to_guardian_data_source(),
            ["PATH", "/"],
        )

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
            role_user_assignments={"role1": {"alice"}},
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
        self.assertEqual(loaded.role_user_assignments, {"role1": {"alice"}})

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
