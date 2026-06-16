# 数据迁移操作指南

**适用版本**：`feat/neutral-slate-ui-redesign` 分支（CRUD 并发安全 + 设置中心菜单重构）  
**受众**：将此分支部署到**现有生产数据库**的运维/开发人员  
**发布日期**：2026-06-13

---

## 1. 概述

本次发布包含三个**幂等**数据库迁移，均可安全重复执行：

| 迁移文件 | 作用 | 为什么需要 |
|---|---|---|
| `migrations/2026_06_13_settings_hub_menu.py` | 将旧的"数据工具"（`menu-3-b`）和"系统配置"（`menu-3`）两棵菜单子树合并为单一"设置中心"（`/admin`）菜单 | 菜单结构重构，旧节点残留会产生重复入口和路由冲突 |
| `migrations/2026_06_13_dynamic_sequences.py` | 创建 `dynamic_sequences` 表并按现有数据播种计数器；此后所有 autoSequence 值由服务端原子分配 | 解决并发创建时序号重复的问题；客户端不再自行生成序号 |
| `migrations/2026_06_14_workflow_tables.py` | 创建 `workflow_definitions` / `workflow_instances` 两张表 | 跨页工作流引擎的存储（详见 `../data/workflow.md`） |

三个迁移均**幂等**（可重复执行不产生副作用）：

- 设置中心迁移：每次先删旧子树再 upsert 目标节点。
- 序列计数迁移：建表语句含 `IF NOT EXISTS`；播种使用 `GREATEST` 语义，计数器只升不降。
- 工作流表迁移：建表语句含 `IF NOT EXISTS`，纯建表无数据改动。

**全新安装**（通过 `python init_db.py` 初始化）已包含这两项，无需单独运行迁移。

---

## 2. 迁移前检查清单

### 2.1 备份数据库（必须）

在任何迁移操作之前，先做完整备份。

**方法 A：使用应用内置备份**

登录管理界面 → `/admin` → 系统运维 → 系统备份 → 立即备份。

**方法 B：pg_dump（推荐，更可靠）**

```bash
pg_dump -h localhost -U <db_user> -d casemanage -F c -f pre_migration_backup.dump
```

数据库名为 `casemanage`（见 `server/config.py`）。`-F c` 表示自定义格式，可用 `pg_restore` 恢复。请妥善保存此文件。

### 2.2 静默写入（可选但推荐）

两个迁移本身是安全的，但在播种序列计数器时若有并发写入，可能导致计数器需要再次播种。建议在低峰期或短暂维护窗口内执行。如果无法停机，迁移后可再执行一次 `2026_06_13_dynamic_sequences` 以确保计数器准确（幂等，无害）。

### 2.3 部署最新后端代码

迁移脚本导入 `utils/sequences.py`（本次新增）。**必须先部署新代码**，再运行迁移。

```bash
# 拉取并部署新版本后端代码（具体方式视部署环境而定）
git pull
pip install -r requirements.txt  # 若有新依赖
```

---

## 3. 迁移步骤（按顺序执行）

所有命令均在 `server/` 目录下执行。

### 步骤 1：设置中心菜单迁移

```bash
cd server
python -m migrations.2026_06_13_settings_hub_menu
```

**预期输出**（`deleted` 列表内容因数据库实际状态而异）：

```
{'deleted': ['menu-3', 'menu-3-b', ...], 'inserted': 'menu-settings'}
```

- `deleted`：所有被删除的旧节点 ID（包括 `menu-3`、`menu-3-b` 及其全部后代）。若旧节点已不存在，`deleted` 为空列表 `[]`。
- `inserted`：新插入（或更新）的设置中心菜单 ID，固定为 `'menu-settings'`。

### 步骤 2：动态序列计数表迁移

```bash
python -m migrations.2026_06_13_dynamic_sequences
```

**预期输出**：

```
{'status': 'ok'}
```

此步骤会：
1. 创建 `dynamic_sequences (collection, branch_id, field_name, current_value)` 表（若已存在则跳过）。
2. 扫描所有 `page_configs` 中的 `autoSequence` 字段，按各 collection + branch_id 的现有数据最大值播种计数器。

### 步骤 3：工作流表迁移

```bash
python -m migrations.2026_06_14_workflow_tables
```

**预期输出**：

```
{'status': 'ok'}
```

创建 `workflow_definitions` / `workflow_instances` 两张表（若已存在则跳过），纯建表、无数据改动。工作流功能用法见 `../data/workflow.md`。

---

## 4. 验证

### 4.1 设置中心菜单

```sql
-- 新菜单节点已插入
SELECT id, name, path FROM menus WHERE id = 'menu-settings';
-- 应返回一行：menu-settings | 设置中心 | /admin

-- 旧节点已清除（menu-3 开头的节点）
SELECT count(*) FROM menus WHERE id LIKE 'menu-3%';
-- 应返回 0
```

### 4.2 动态序列表

```sql
-- 表存在且有记录（若数据库中有 autoSequence 字段数据）
SELECT count(*) FROM dynamic_sequences;
-- 应返回 >= 0（有 autoSequence 数据的 collection 会有对应行）

-- 针对具体 collection 检查（将 '<coll>' 替换为实际 collection 名，如 'orders'）
SELECT * FROM dynamic_sequences WHERE collection = '<coll>';
-- current_value 应 >= 该 collection 中对应字段的最大数值部分
```

### 4.3 功能冒烟测试

在 UI 中打开一个含 `autoSequence` 字段的数据页，新建一条记录（不填序号字段），保存后确认：
- 序号由系统自动生成（表单中显示"保存后生成"占位符）。
- 新记录的序号 = 已有最大序号 + 1，无重复。

---

## 5. 重复运行迁移

两个迁移均为幂等，可安全重复执行：

- **设置中心迁移**：重复运行会先删除（已存在的）旧子树，再重新 upsert 设置中心节点。若旧节点已不存在，`deleted` 为空列表；`menu-settings` 会被删除后重新插入（内容不变）。
- **序列计数迁移**：重复运行会再次扫描数据并以 `GREATEST` 语义更新计数器（只升不降）。在高峰期写入后重跑可用于修正计数器。

---

## 6. 回滚

> **重要**：迁移是前向操作，唯一可靠的回滚手段是恢复备份。这正是第 2.1 步备份为必须项的原因。

### 通过 pg_restore 恢复备份

```bash
# 停止应用
# 恢复数据库（会覆盖现有数据）
pg_restore -h localhost -U <db_user> -d casemanage --clean -F c pre_migration_backup.dump
# 回滚代码到上一个版本
# 重新启动应用
```

### 各迁移的特殊说明

**设置中心迁移**：旧菜单结构（`menu-3`/`menu-3-b` 子树）无法从迁移脚本本身重建；必须通过备份恢复。迁移设计为前向单向，没有 down 路径。

**序列计数迁移**：`dynamic_sequences` 表可以直接 `DROP TABLE dynamic_sequences;` 删除（数据不丢失，只是计数器消失）。但回滚代码后需同时回滚代码，否则新版后端代码会在下次分配时重新创建并播种计数器（`allocate_sequence` 有自愈逻辑）。建议配合备份恢复而非单独删表。

---

## 7. 迁移后注意事项

### autoSequence 字段行为变化

| 场景 | 迁移前（旧版） | 迁移后（新版） |
|---|---|---|
| 表单新建记录 | 客户端生成序号并填入表单 | 表单显示"保存后生成"，服务端在提交时分配 |
| 并发创建 | 可能出现序号重复 | 服务端原子分配，不重复 |
| 批量导入 | 保留导入文件中的序号值 | 保留导入值；导入后自动 reseed 计数器（GREATEST 语义） |
| Open API 写入 | 客户端可指定序号 | 客户端可指定序号；写入后自动 reseed 计数器 |
| 分支合并 / 备份还原 | — | 自动 reseed 计数器，与上述行为一致 |

### 计数器一致性

所有绕过 `create_item`（标准创建端点）的写入路径（批量导入、Open API、分支合并、备份还原）在写入后均会调用 `reseed_sequences`，保持计数器与实际数据一致。删除记录**不会**减少计数器——序号不重用，这是有意设计。

### 设置中心菜单权限

新的"设置中心"菜单（`/admin`）的 `roles` 为 `["admin"]`。若你的数据库中有自定义角色需要访问此菜单，请在迁移后手动更新：

```sql
UPDATE menus SET roles = roles || '["your-custom-role"]'::jsonb WHERE id = 'menu-settings';
```
