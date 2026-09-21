# PermissionMigration 完整使用手册

本文档说明如何把 Ranger、Sentry 或人工 Excel 中的用户、组、角色和权限转换为 Guardian REST API shell 脚本。

程序只生成脚本，不会在转换阶段连接或修改 Guardian。只有人工执行生成的 `.sh` 文件时，才会向 Guardian 发起创建和授权请求。

## 1. 工作流程

```text
Ranger JSON ───────┐
Sentry CSV/TSV ────┤
Sentry SQL dump ───┼─> 统一 IR JSON ─> Guardian API shell ─> 人工检查并执行
Guardian Excel ────┘
```

统一 IR 是中间结果，便于审计、保存和二次筛选。常规情况下可以直接使用 `migrate` 一步生成 IR 和 shell。

## 2. 运行环境与安装

要求：

- Python 3.9 或更高版本。
- Excel 输入需要 `openpyxl`。
- 执行最终脚本的机器需要 `bash` 和 `curl`。
- 不需要连接 Ranger、Sentry 数据库；所有来源都从本地导出文件读取。

进入项目目录后安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

确认程序入口：

```bash
python3 -m src.cli --help
python3 -m src.cli migrate --help
```

运行回归测试：

```bash
python3 -m unittest discover -s tests -v
```

## 3. 项目目录

| 路径 | 说明 |
|---|---|
| `src/cli.py` | 统一命令行入口 |
| `src/ranger_to_ir.py` | Ranger JSON 解析器 |
| `src/sentry_to_ir.py` | Sentry CSV/TSV 解析器 |
| `src/sentry_sql_to_ir.py` | Sentry MySQL dump 解析和格式自动识别 |
| `src/excel_to_ir.py` | Guardian 批量权限 Excel 解析与校验 |
| `src/models.py` | 统一 IR 数据模型 |
| `src/ir_to_guardian.py` | Guardian API shell 生成器 |
| `templates/Guardian_Batch_Permission_Template.xlsx` | 人工批量录入模板 |
| `Ranger_export_example.json` | Ranger 输入样例 |
| `sentry_export_example.csv` | Sentry CSV 输入样例 |
| `sentry_dump_example.sql` | 脱敏的 Sentry dump 输入样例 |
| `user_groups.example.csv` | 外部用户—组关系样例 |

## 4. 所有输入入口

### 4.1 Ranger JSON

适用于 Ranger Service Manager 导出的 policy JSON。当前转换允许类 `policyItems`，支持 Hive 数据库/表/列、Hive URI 和 HDFS path，以及 `USER`、`GROUP`、`ROLE` 三类主体。

一条命令生成 IR 和 Guardian shell：

```bash
python3 -m src.cli migrate \
  --source ranger \
  --source-input Ranger_export_example.json \
  --save-ir output/ranger_ir.json \
  --output output/ranger_guardian.sh \
  --base-url 'https://guardian.example:8380' \
  --access-token '<guardian_access_token>' \
  --hive-component ylhive1 \
  --hdfs-component ylhdfs1
```

Ranger 输入限制：

- 不把 deny、allow exception、deny exception 转换为正向授权。
- 不转换 data mask、row filter。
- 禁用 policy 会被跳过。
- Kafka、Atlas、YARN 等没有明确 Guardian `dataSource` 映射的资源不会转换。
- `isExcludes` 没有安全的 Guardian 正向授权等价物，因此不会猜测转换。

### 4.2 Sentry CSV/TSV

CSV 和 TSV 使用相同入口，程序会自动识别逗号或制表符。推荐表头：

```csv
database,table,partition,column,principal_name,principal_type,privilege,grant_option,grant_time,grantor
sales,orders,,,finance_role,ROLE,SELECT,FALSE,,
/user/finance,,,,finance_role,ROLE,READ,FALSE,,
```

字段说明：

| 字段 | 说明 |
|---|---|
| `database` | Hive database，或 HDFS/URI 路径 |
| `table` | Hive table/view，可空 |
| `partition` | Hive partition，可空；来自旧格式时保留层级 |
| `column` | Hive column，可空 |
| `principal_name` | 用户、组或角色名称 |
| `principal_type` | `USER`、`GROUP`、`ROLE` |
| `privilege` | 单个或英文逗号分隔权限；支持 `ALL`/`*` |
| `grant_option` | 是否允许转授权，例如 `TRUE`/`FALSE` |
| `grant_time`、`grantor` | 可选审计字段 |

执行命令：

```bash
python3 -m src.cli migrate \
  --source sentry \
  --source-input sentry_export_example.csv \
  --save-ir output/sentry_csv_ir.json \
  --output output/sentry_csv_guardian.sh \
  --base-url 'https://guardian.example:8380' \
  --access-token '<guardian_access_token>' \
  --hive-component ylhive1 \
  --hdfs-component ylhdfs1
```

`file:///tmp`、`FILE:/opt/...` 等表示源节点本地文件系统，不属于 Guardian/TDFS 管理范围。Sentry 解析器会跳过这些权限并在 IR 元数据中记录统计，不会把它们改成 HDFS 路径。

### 4.3 Sentry MySQL dump

支持 MySQL 5.7 `mysqldump` 文本 `.sql` 和 gzip 压缩的 `.sql.gz`。程序只读取 DDL 和 INSERT，不执行 SQL，也不需要启动 MySQL。

```bash
python3 -m src.cli migrate \
  --source sentry \
  --source-input sentry_20260904.sql \
  --export-users output/all_users.txt \
  --save-ir output/sentry_full_ir.json \
  --output output/sentry_full_guardian.sh \
  --base-url 'https://guardian.example:8380' \
  --access-token '<guardian_access_token>' \
  --hive-component ylhive1 \
  --hdfs-component ylhdfs1
```

主要读取以下关系：

- `sentry_db_privilege`：Hive/URI 权限。
- `sentry_role_db_privilege_map`：角色—权限。
- `sentry_user_db_privilege_map`：用户直授权。
- `sentry_role_group_map`：角色—组。
- `sentry_role_user_map`：角色—用户。
- `sentry_role`、`sentry_group`、`sentry_user`：主体实体。

Sentry 元数据库通常不保存 LDAP/AD/操作系统中的“用户属于组”关系。若需要恢复完整继承链，必须另行提供用户—组文件：

```csv
user,group
alice,finance_group
alice,analytics_group
bob,audit_group
```

然后生成所选用户及其组、角色和继承权限：

```bash
python3 -m src.cli guardian \
  --input output/sentry_full_ir.json \
  --users-file selected_users.txt \
  --user-groups-file user_groups.csv \
  --output output/guardian_selected_users.sh \
  --base-url 'https://guardian.example:8380' \
  --access-token '<guardian_access_token>' \
  --hive-component ylhive1 \
  --hdfs-component ylhdfs1
```

### 4.4 Guardian 批量权限 Excel

模板位置：`templates/Guardian_Batch_Permission_Template.xlsx`。

每张业务表的第 1 行是填写说明，第 2 行是固定字段名，第 3 行起是数据。不要修改工作表名称和第 2 行字段名。

#### UserGroups

| 列 | 必填 | 说明 |
|---|---|---|
| `user_name` | 是 | Guardian 用户名 |
| `email` | 是 | 创建用户时使用的邮箱 |
| `initial_password` | 是 | 明文初始密码；前导零密码必须设置为文本单元格 |
| `groups` | 否 | 用户所属组，多个值用英文逗号分隔 |
| `direct_roles` | 否 | 直接授予用户的角色，多个值用英文逗号分隔 |

#### GroupRoles

| 列 | 必填 | 说明 |
|---|---|---|
| `group_name` | 是 | 组名；出现后会创建该组 |
| `roles` | 否 | 组所属角色，多个值用英文逗号分隔 |

#### Permissions

| 列 | 说明 |
|---|---|
| `principal_type` | `USER`、`GROUP` 或 `ROLE` |
| `principal_name` | 被授权主体名称 |
| `database` | Hive 数据库；允许 `*` 或 `GLOBAL` 表示全局 |
| `table` | Hive 表；不能使用 `*` 或 `GLOBAL` |
| `column` | Hive 列；不能使用 `*` 或 `GLOBAL` |
| `path` | HDFS 路径；允许绝对路径、`hdfs://...`、`/` 或 `GLOBAL` |
| `actions` | 单个或英文逗号分隔权限；允许 `ALL`，不允许 `OWNER` |

资源类型根据填写内容自动判断，不需要 `resource_type`：

| 填写方式 | 识别结果 |
|---|---|
| 仅 `database` | Hive 数据库权限 |
| `database + table` | Hive 表权限 |
| `database + table + column` | Hive 列权限 |
| 仅 `path` | HDFS 路径权限 |

同一行不能同时填写 Hive 资源和 `path`。Guardian 无法设置分区权限，因此模板没有 partition 列。Excel 中 `file:` 本地 URI、`OWNER`、未知动作、非法通配符、大小写冲突等都会被当作错误。

完整命令：

```bash
python3 -m src.cli migrate \
  --source excel \
  --source-input templates/Guardian_Batch_Permission_Template.xlsx \
  --validation-report output/excel_validation.json \
  --save-ir output/excel_ir.json \
  --output output/guardian_excel_import.sh \
  --base-url 'https://guardian.example:8380' \
  --access-token '<guardian_access_token>' \
  --hive-component ylhive1 \
  --hdfs-component ylhdfs1
```

解析器会校验整本工作簿。只要存在一个错误，就只写校验报告，不生成新的 IR 或 Guardian shell；警告不阻断生成。`USER` 类型权限的用户必须在 `UserGroups` 中声明邮箱和密码，`GROUP` 和 `ROLE` 权限主体可以由权限行自动创建。

#### Excel 权限动作

| 资源 | 允许动作 |
|---|---|
| Hive 数据库 | `CREATE, SELECT, INSERT, UPDATE, DELETE, ADMIN, ACCESS` |
| Hive 表/列 | `SELECT, INSERT, UPDATE, DELETE, ADMIN` |
| HDFS 路径 | `READ, WRITE, EXECUTE, ADMIN, ACCESS` |

`ALL` 会展开成该资源对应的全部动作。重复的主体、资源和动作会合并。

### 4.5 已有 IR JSON

如果已经保存 IR，可以不再读取大型 dump，直接重复生成脚本：

```bash
python3 -m src.cli guardian \
  --input output/sentry_full_ir.json \
  --output output/guardian_import.sh \
  --base-url 'https://guardian.example:8380' \
  --access-token '<guardian_access_token>' \
  --hive-component ylhive1 \
  --hdfs-component ylhdfs1
```

## 5. 按用户筛选

`sentry`、`guardian` 和 `migrate` 支持：

- `--users alice,bob`：命令行直接指定。
- `--users-file selected_users.txt`：文件中每行一个用户名，也支持英文逗号和 `#` 注释。
- `--export-users output/all_users.txt`：筛选前导出全部解析到的用户名。
- `--user-groups-file user_groups.csv`：补充外部用户—组关系。

筛选不是只保留用户直授权，而是计算当前数据中能够确定的权限闭包：

1. 用户直授权。
2. 直接授予用户的角色和角色权限。
3. 用户所在组的组权限。
4. 授予这些组的角色和角色权限。

外部用户—组文件中不属于 Sentry 解析结果的组会被忽略并计数，避免意外创建无关目录组。指定不存在的用户名时，程序会报错退出。

## 6. Guardian 参数与输出

常用参数：

| 参数 | 说明 |
|---|---|
| `--base-url` | Guardian 地址，例如 `https://host:8380` |
| `--access-token` | Guardian access token |
| `--hive-component` | 目标集群 Hive/Quark component 名 |
| `--hdfs-component` | 目标集群 HDFS/TDFS component 名 |
| `--save-ir` | 保存统一 IR JSON |
| `--output` | 输出 Guardian shell 路径 |
| `--validation-report` | Excel 行级校验报告路径 |

也可以通过环境变量提供连接信息：

```bash
export GUARDIAN_URL='https://guardian.example:8380'
export GUARDIAN_ACCESS_TOKEN='<guardian_access_token>'
```

生产环境仍建议显式指定 component，因为不同集群名称不同。未指定地址或 token 时，程序使用不可直接生产执行的安全占位值。

生成脚本依次执行：

1. 创建用户。
2. 创建组。
3. 创建角色。
4. 用户加入组。
5. 用户或组加入角色。
6. 为用户、组或角色授权。

## 7. 生产执行步骤

先生成，不要直接边转换边执行：

```bash
bash -n output/guardian_import.sh
```

人工检查以下内容：

- Guardian URL 和 token 是否属于目标环境。
- `component` 是否是目标集群真实的 Hive/HDFS component。
- `principalType`、用户名、组名、角色名是否正确。
- Hive `dataSource` 是否为 `GLOBAL` 或 `TABLE_OR_VIEW` 层级。
- HDFS `dataSource` 是否为 `PATH` 层级。
- 脚本是否只包含预期用户和权限。

确认后执行并保存终端输出：

```bash
bash output/guardian_import.sh 2>&1 | tee output/guardian_import.log
```

当前迁移逻辑是增加实体、关系和授权，不负责撤销目标环境中已有权限。重复执行前应先在测试环境确认 Guardian 对“已存在实体/关系”的返回行为。

## 8. 安全注意事项

- access token 会出现在生成脚本的 URL 中，不要提交生成脚本、日志或 IR 到 Git。
- Excel 用户初始密码会以明文出现在 Excel、IR 和 shell 中，应限制文件权限并按内部规范及时清理。
- Sentry 原始 dump、真实用户名、库表路径属于内部数据，不要提交到公共仓库。
- 建议在隔离目录生成产物，并设置严格权限：`chmod 600`。
- 本仓库的示例地址和 token 是占位值，不能用于生产。

## 9. 常见问题

### 为什么 Sentry dump 中有角色—组，却没有用户—组？

Sentry 依赖 LDAP/AD/操作系统目录解析用户组，元数据库通常不保存该关系。需要通过 `--user-groups-file` 另行补充。

### `file:///tmp` 为什么不导入？

它是源 HiveServer2/Impala 节点的本地路径，不是 HDFS。Guardian/TDFS 不管理该本地资源，不能安全改写成 `/tmp`。

### 为什么 Excel 不允许 `OWNER`？

Guardian 目标权限没有 `OWNER`。人工 Excel 应直接填写 `ADMIN`，避免模板中出现含义不清的隐式转换。历史 Sentry 数据中的 `OWNER` 会按已有迁移规则映射为 `ADMIN`。

### 为什么用户权限行要求先在 UserGroups 声明？

创建 Guardian 用户需要邮箱和初始密码。组和角色不需要这两个字段，因此可以由关系或权限行自动创建。

### Excel 校验失败后为什么没有生成脚本？

这是为了避免部分有效行已经授权、错误行却被遗漏。修复校验报告中的所有错误后重新运行即可。
