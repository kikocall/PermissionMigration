-- 脱敏的最小 mysqldump 风格样例，仅用于本地验证解析链路。
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
CREATE TABLE `sentry_role` (
  `ROLE_ID` bigint(20) NOT NULL,
  `ROLE_NAME` varchar(128) NOT NULL,
  `CREATE_TIME` bigint(20) NOT NULL
);
CREATE TABLE `sentry_group` (
  `GROUP_ID` bigint(20) NOT NULL,
  `GROUP_NAME` varchar(128) NOT NULL,
  `CREATE_TIME` bigint(20) NOT NULL
);
CREATE TABLE `sentry_role_db_privilege_map` (
  `ROLE_ID` bigint(20) NOT NULL,
  `DB_PRIVILEGE_ID` bigint(20) NOT NULL,
  `GRANTOR_PRINCIPAL` varchar(128)
);
CREATE TABLE `sentry_role_group_map` (
  `ROLE_ID` bigint(20) NOT NULL,
  `GROUP_ID` bigint(20) NOT NULL,
  `GRANTOR_PRINCIPAL` varchar(128)
);
CREATE TABLE `sentry_version` (
  `VER_ID` bigint(20) NOT NULL,
  `SCHEMA_VERSION` varchar(127) NOT NULL,
  `VERSION_COMMENT` varchar(255) NOT NULL
);
INSERT INTO `sentry_role` VALUES (1,'analyst_role',1700000000000);
INSERT INTO `sentry_group` VALUES (10,'analyst_group',1700000000000);
INSERT INTO `sentry_db_privilege` VALUES
(100,'TABLE','server1','sales','orders','__NULL__','__NULL__','select',1700000000000,'N'),
(101,'URI','server1','__NULL__','__NULL__','__NULL__','hdfs://nameservice1/data/sales','all',1700000000000,'N');
INSERT INTO `sentry_role_db_privilege_map` VALUES (1,100,'admin'),(1,101,'admin');
INSERT INTO `sentry_role_group_map` VALUES (1,10,'admin');
INSERT INTO `sentry_version` VALUES (1,'2.2.0','Sentry release version 2.2.0');
