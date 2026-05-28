# PermissionMigration

将 CDH/CDP 中 Ranger 或 Sentry 导出的权限转换为星环 TDH Guardian 赋权 API 调用脚本。

本项目的设计目标是：先把来源权限解析成统一 IR，再从 IR 生成 Guardian API shell 脚本。这样 Ranger、Sentry 的字段差异不会直接污染最终赋权脚本，也方便后续补充更多组件和映射规则。

## 背景调研摘要

### Ranger 导出格式

Ranger 从 Service Manager 导出的权限是 JSON；从 Reports 页面也可以导出 JSON、Excel、CSV，但 CSV 不支持再导入 Ranger。Ranger JSON 的核心字段通常包括：

- `policies`: 策略数组。
- `serviceType`: 组件类型，例如 `hive`、`hdfs`、`hbase`、`kafka`、`atlas`、`solr`、`yarn`、`knox`、`ozone`。
- `service`: Ranger 服务实例名。
- `resources`: 资源定义，每个资源键包含 `values`、`isRecursive`、`isExcludes`。
- `additionalResources`: 额外资源块，结构与 `resources` 类似。
- `policyItems`: 允许策略项，包含 `accesses`、`users`、`groups`、`roles`、`delegateAdmin`、`conditions`。
- `denyPolicyItems`、`allowExceptions`、`denyExceptions`: 拒绝和例外策略项。
- `dataMaskPolicyItems`、`rowFilterPolicyItems`: Hive 数据脱敏和行过滤策略项。
- `isEnabled`: 策略是否启用。

本工具当前迁移 Ranger 的允许类资源权限，即 `policyItems`。禁用策略、deny/exception、data mask、row filter 不会生成 Guardian 授权命令，避免把负向规则误转成正向授权。

### Sentry 导出格式

Cloudera 官方迁移工具 `authzmigrator` 通常将 Sentry 权限导出为 JSON，例如 `permissions.json`，可覆盖 Hive object、URI/URL 以及 Kafka 等权限。当前项目中的 `sentry_export_example.csv` 是一个整理后的 CSV 权限表，字段为：

- `database`: Hive database，或 HDFS/URI 路径，例如 `/user/...`、`file:///tmp`、`hdfs://nameservice/path`。
- `table`: Hive table/view。
- `partition`: Hive partition。
- `column`: Hive column。
- `principal_name`: 被授权主体名称。
- `principal_type`: `ROLE`、`USER`、`GROUP`。
- `privilege`: 权限动作，例如 `SELECT`、`INSERT`、`ALL`、`*`。
- `grant_option`: 是否可转授权。
- `grant_time`、`grantor`: 审计字段。

本工具兼容逗号分隔 CSV 和制表符分隔 TSV。Sentry 的 `ALL` 或 `*` 会按旧脚本逻辑展开：Hive 展开为 `CREATE, SELECT, INSERT, UPDATE, DELETE, ADMIN`；HDFS/URI 路径展开为 `READ, WRITE, EXECUTE, ADMIN`。

## Guardian API 约束

Guardian API 的调用格式严格沿用 `sentry_to_guardian.py` 中已有样例：

- 创建用户：`POST /api/v1/users`
- 创建组：`POST /api/v1/groups`
- 创建角色：`POST /api/v1/roles`
- 用户加入组：`PUT /api/v1/groups/{group}/assign`
- 组加入角色：`PUT /api/v1/roles/{role}/assign`
- 权限授权：`PUT /api/v1/perms/grant`

权限 payload 形态保持为：

```json
{
  "name": "principal_name",
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
```

Hive 表权限的 `dataSource` 使用 `["TABLE_OR_VIEW", database, table, partition, column]`，后面的层级按实际存在字段追加。HDFS/URI 权限使用旧脚本格式 `["PATH", "/", ...pathParts]`。例如 `/user/team` 会生成 `["PATH", "/", "user", "team"]`。

## 脚本说明

### 旧脚本

- `ranger_to_sentry.py`: 旧版 Ranger JSON 转 Sentry CSV 格式脚本。
- `sentry_to_guardian.py`: 旧版 Sentry CSV 转 Guardian API shell 脚本，是当前 Guardian API payload 格式的参考来源。

### 新脚本

- `src/models.py`: 统一 IR 数据结构，描述资源、主体、权限、策略和迁移计划。
- `src/ranger_to_ir.py`: 解析 Ranger JSON 导出文件，生成 IR。
- `src/sentry_to_ir.py`: 解析 Sentry CSV/TSV 权限文件，生成 IR。
- `src/ir_to_guardian.py`: 从 IR 生成 Guardian API shell 脚本。
- `src/cli.py`: 统一命令行入口。
- `src/constants.py`: Guardian endpoint、默认配置、权限动作映射。
- `src/utils.py`: 通用工具函数。

## 快速使用

### 1. Ranger JSON 转 Guardian 脚本

```powershell
python -m src.cli migrate --source ranger --source-input Ranger_export_example.json --save-ir output\ranger_ir.json --output output\permission_migration.sh
```

### 2. Sentry CSV/TSV 转 Guardian 脚本

```powershell
python -m src.cli migrate --source sentry --source-input sentry_export_example.csv --save-ir output\sentry_ir.json --output output\permission_migration.sh
```

### 3. 只生成 IR

```powershell
python -m src.cli ranger --input Ranger_export_example.json --output output\ranger_ir.json
python -m src.cli sentry --input sentry_export_example.csv --output output\sentry_ir.json
```

### 4. 已有 IR 生成 Guardian 脚本

```powershell
python -m src.cli guardian --input output\ranger_ir.json --output output\permission_migration.sh
```

### 5. 指定 Guardian 地址

```powershell
python -m src.cli guardian --input output\ranger_ir.json --output output\permission_migration.sh --base-url https://guardian.example:8380
```

如果不传 `--base-url`，脚本会优先读取环境变量 `GUARDIAN_URL`，否则使用 `src/constants.py` 里的默认值。

## 当前支持范围

### Ranger

已支持：

- Hive database/table/column 权限。
- Hive URL/URI 类资源，按 HDFS `PATH` 格式生成。
- HDFS path 权限。
- `users`、`groups`、`roles` 三类授权主体。
- 多资源值笛卡尔展开，例如一个策略中多个 table。
- `delegateAdmin` 映射为 Guardian `grantable`。
- `additionalResources`。
- 禁用策略跳过。

会跳过或暂不转换：

- `denyPolicyItems`、`allowExceptions`、`denyExceptions`。
- `dataMaskPolicyItems`、`rowFilterPolicyItems`。
- 无法映射到 Guardian 样例 `TABLE_OR_VIEW` 或 `PATH` 格式的组件资源，例如 Kafka topic、Atlas entity、YARN queue 等。
- `isExcludes` 的排除语义。Guardian 样例中没有等价负向授权格式，不能安全转换为正向授权。

### Sentry CSV/TSV

已支持：

- Hive database/table/partition/column。
- HDFS/URI 路径。
- `ROLE`、`USER`、`GROUP`。
- 逗号分隔和制表符分隔。
- `ALL`、`*` 权限展开。
- 逗号分隔的多权限字段，例如 `SELECT,INSERT`。
- `grant_option=TRUE` 映射为 Guardian `grantable=true`。

暂不支持：

- Cloudera `authzmigrator` 原生 `permissions.json`。后续可以新增 `sentry_json_to_ir.py` 或在 `sentry_to_ir.py` 内自动识别 JSON。
- Sentry Kafka/Kudu 等非 Hive/URI 权限到 Guardian 的映射，除非补充明确的 Guardian `dataSource` 样例。

## 验证

运行单元测试：

```powershell
python -m unittest tests.test_permission_migration
```

用示例文件跑完整链路：

```powershell
python -m src.cli migrate --source sentry --source-input sentry_export_example.csv --save-ir output\sentry_ir.json --output output\sentry_guardian.sh
python -m src.cli migrate --source ranger --source-input Ranger_export_example.json --save-ir output\ranger_ir.json --output output\ranger_guardian.sh
```

生成脚本后，建议先抽查：

- 文件第一行是否是 `#!/usr/bin/env bash`。
- `principalType` 是否符合源文件中的 `ROLE`、`USER`、`GROUP`。
- Hive `dataSource` 是否是 `TABLE_OR_VIEW`。
- HDFS/URI `dataSource` 是否是 `PATH`。
- `component` 是否符合预期：Hive 使用 `quark1`，HDFS/URI 使用 `tdfs1`。

## 开发约定

- 每次功能修改或修复都需要提交 Git commit。
- 不提交 `output/`、`.vscode/`、`__pycache__/`。
- 修改转换逻辑前，优先补充 `tests/test_permission_migration.py` 中的回归测试。
- Guardian API payload 格式不能随意改变；需要新增组件映射时，应先拿到对应 Guardian 样例。

## 参考资料

- Apache Ranger API `RangerPolicyList`: https://ranger.apache.org/apidocs/json_RangerPolicyList.html
- Cloudera Ranger policy import/export: https://docs.cloudera.com/runtime/7.3.1/security-ranger-authorization/topics/security-ranger-resource-policies-importing-exporting.html
- Cloudera Sentry permissions export: https://docs.cloudera.com/cdp-private-cloud-upgrade/latest/security-authorization/topics/rm-dc-authzmigrator-tool-step1.html
- Cloudera Sentry privilege model: https://docs-archive.cloudera.com/documentation/enterprise/6/6.0/topics/cm_sg_sentry_service.html
