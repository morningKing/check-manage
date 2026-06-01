# Check-Manage 全量功能说明文档

## 文档概述

本文档详细描述 Check-Manage 系统的所有功能模块，涵盖系统架构、核心功能、管理功能、以及高级特性。文档重点阐述数据版本管理、备份还原、分支管理等关键功能的完整业务逻辑和技术实现细节。

---

## 1. 系统概述

### 1.1 系统定位

Check-Manage 是一个**配置驱动的企业级动态数据管理平台**。核心设计理念：管理员通过菜单管理和页面配置定义业务数据结构，系统自动渲染对应的表单和表格，无需编写前端代码即可扩展新的业务页面。

### 1.2 技术架构

```
┌──────────────────────────────────────────────────────┐
│                  浏览器 (Vue 3.4)                     │
│  Element Plus + Pinia + Vue Router + Univer          │
└────────────────────┬─────────────────────────────────┘
                     │ HTTP (JWT Bearer Token)
                     │ Vite Proxy /api → :3001
┌────────────────────▼─────────────────────────────────┐
│               Flask 后端 (:3001)                      │
│  22 Blueprint 路由 + JWT认证 + 沙箱脚本引擎            │
└────────────────────┬─────────────────────────────────┘
                     │ psycopg2 连接池 (1-10 连接)
┌────────────────────▼─────────────────────────────────┐
│               PostgreSQL 数据库                       │
│  25 张表 + JSONB 灵活存储 + 分支版本控制              │
└──────────────────────────────────────────────────────┘
```

### 1.3 核心特性

| 特性 | 说明 |
|------|------|
| **配置驱动** | 无需编码，通过页面配置自动生成表单和表格 |
| **版本控制** | 支持数据快照、分支管理、合并冲突解决（类似Git） |
| **分支管理** | 用户级分支隔离，支持并行开发，数据互不影响 |
| **Excel视图** | 基于 Univer 的真实电子表格体验 |
| **AI查询** | 自然语言转查询条件（集成 Qwen API） |
| **ETL管道** | 可视化数据导入管道，支持 HTTP/脚本/映射/过滤 |
| **备份还原** | 全量/表级备份，定时策略，数据对比 |
| **触发器规则** | 跨集合自动化触发，支持字段变更检测 |
| **关系图谱** | 实体关系可视化展示，支持 BFS 全关联扫描 |

---

## 2. 数据版本管理系统

### 2.1 版本类型

系统支持两种版本类型：

| 类型 | 标识 | 特性 | 用途 |
|------|------|------|------|
| **快照 (Snapshot)** | `snapshot` | 不可修改的静态版本 | 保存历史状态，用于回滚和对比 |
| **分支 (Branch)** | `branch` | 可在其上继续修改数据 | 并行开发，用户隔离，类似Git分支 |

### 2.2 版本创建流程

**API**: `POST /versions`

**请求参数**:
```json
{
  "collection": "inspection-case",
  "name": "v1.0-release",
  "description": "发布版本快照",
  "versionType": "snapshot",
  "parentVersion": null
}
```

**创建流程**（`server/utils/version.py:create_version_snapshot`）：

1. **递归扫描关联数据**
   - 从起始 Collection 开始，使用 BFS 算法扫描所有关联的 Collection
   - 通过 `data_relations` 表获取关联记录
   - 支持跨 Collection 的完整快照
   - 最大扫描限制：10000 条记录（防止超大快照）

2. **计算数据哈希**
   - 对所有记录和关联数据计算 SHA256 哈希
   - 用于快速判断两个版本是否相同

3. **保存快照数据**
   - `version_snapshots` 表：存储每条记录的完整 JSONB 数据
   - `version_relations` 表：存储所有 M:N 关联关系
   - `version_collections` 表：记录版本涉及的 Collection 列表

4. **更新版本元数据**
   - `collection_versions` 表：记录版本信息（名称、状态、记录数、创建者等）

**跨 Collection 快照机制**：

```
起始Collection: inspection-case
      │
      ├── 关联到 inspection-plan（通过 data_relations）
      │       │
      │       └── 关联到 inspection-record
      │
      └── 关联到 check-items
              │
              └── 关联到 check-templates

最终快照包含: 5个 Collection 的所有数据
```

### 2.3 版本列表与查询

**API**: `GET /versions?collection=<name>&status=<status>`

**响应字段**:
```json
{
  "id": "ver-abc12345",
  "collection": "inspection-case",
  "name": "功能分支A",
  "versionType": "branch",
  "status": "active",
  "recordsCount": 150,
  "relationsCount": 45,
  "createdBy": "admin",
  "createdAt": "2026-04-16T10:30:00.000Z",
  "mergedAt": null,
  "isProtected": false
}
```

**状态值**:
- `active` - 活跃，可操作
- `merged` - 已合并，不可再操作
- `archived` - 已归档

### 2.4 版本对比（Diff）

**API**: `POST /versions/diff`

**请求参数**:
```json
{
  "collection": "inspection-case",
  "baseVersion": "current",
  "targetVersion": "ver-abc12345"
}
```

**对比维度**:
- 记录级别：新增、删除、修改、未变化
- 字段级别：逐字段对比，显示 oldValue → newValue
- 关联级别：对比 M:N 关联关系的变化

**响应结构**:
```json
{
  "added": [{ "id": "rec-new1", ... }],
  "removed": [{ "id": "rec-old1", ... }],
  "modified": [
    {
      "id": "rec-mod1",
      "fields": [
        { "fieldName": "status", "oldValue": "todo", "newValue": "done" }
      ]
    }
  ],
  "unchangedCount": 120
}
```

### 2.5 版本合并机制

#### 2.5.1 全量合并

**API**: `POST /versions/merge`

**策略**:
- `theirs` - 使用源版本数据覆盖当前数据
- `ours` - 保留当前数据，忽略源版本

**合并流程**（`server/utils/version.py:merge_version_to_current`）：

1. 加载源版本数据（快照从 `version_snapshots`，分支从 `dynamic_data`）
2. 加载目标数据（当前分支）
3. 计算差异
4. 根据策略执行合并：
   - 删除目标中不在源版本的记录
   - 插入源版本新增的记录
   - 更新源版本修改的记录
  重建双向关联关系
5. 标记版本为 `merged` 状态

#### 2.5.2 部分合并（字段级选择）

**API**: `POST /versions/partial-merge`

**请求参数**:
```json
{
  "source_version_id": "ver-abc12345",
  "target_branch": "current",
  "decisions": {
    "added_record_ids": ["rec-new1", "rec-new2"],
    "removed_record_ids": ["rec-old1"],
    "modified_records": [
      {
        "record_id": "rec-mod1",
        "field_values": {
          "status": "done",
          "priority": "high"
        }
      }
    ]
  }
}
```

**特性**:
- 用户可选择每条记录的处理方式
- 对修改记录可逐字段选择保留值
- 自动创建合并前快照作为安全保护
- 合并后更新版本哈希值

### 2.6 版本删除机制

**API**: `DELETE /versions/<version_id>?confirmed=true/false`

**两阶段删除**：

**阶段1 - 获取影响报告** (`confirmed=false`):
```json
{
  "requiresConfirmation": true,
  "data": {
    "versionInfo": { "name": "功能分支A", ... },
    "affectedCollections": [
      { "collection": "inspection-case", "recordCount": 150 }
    ],
    "totalRecords": 150,
    "hasCrossCollectionData": true,
    "warningMessage": "该版本涉及 3 个 Collection..."
  }
}
```

**阶段2 - 确认删除** (`confirmed=true`):
- 检查是否有子版本（禁止删除）
- 检查是否受保护（禁止删除）
- 清理分支数据：`dynamic_data`、`data_relations`
- 清理版本元数据（CASCADE 删除 `version_snapshots` 等）

### 2.7 版本恢复

**API**: `POST /versions/<version_id>/restore`

**流程**:
1. 获取目标分支（用户当前分支）
2. 加载版本快照数据
3. 清空目标分支当前数据
4. 插入快照数据（带目标分支 ID）
5. 重建关联关系

---

## 3. 分支管理系统

### 3.1 分支概念

**分支机制**: 类似 Git 的分支系统，每个用户可以在自己的分支上独立工作，不同用户的分支数据互不影响。

**主分支**: `MAIN_BRANCH_ID = 'main'`

**用户分支**: 存储在 `user_current_branch` 表，记录每个用户在每个 Collection 的当前工作分支。

### 3.2 分支切换

**切换到分支**: `POST /versions/<version_id>/switch`

**切换到主分支**: `POST /versions/switch-main`

**切换流程**（`server/utils/version.py:switch_to_version`）：

1. 验证目标版本是否为 `branch` 类型
2. 检查分支数据是否已初始化：
   - **未初始化**: 从主分支复制数据到该分支
   - **已初始化**: 直接切换
3. 更新用户当前分支设置
4. 返回切换结果（包含关联的 Collection 列表）

**分支数据初始化**:
```python
# 复制 dynamic_data
INSERT INTO dynamic_data (id, collection, data, branch_id)
SELECT id, collection, data, '<branch_id>'
FROM dynamic_data WHERE collection = %s AND branch_id = 'main'
ON CONFLICT (id, branch_id) DO NOTHING

# 复制 data_relations
INSERT INTO data_relations (... , branch_id)
SELECT ... , '<branch_id>'
FROM data_relations WHERE ... AND branch_id = 'main'
```

### 3.3 分支数据隔离

**数据隔离机制**:

所有数据操作（CRUD）都基于 `branch_id` 字段过滤：

```sql
-- 查询时按分支过滤
SELECT * FROM dynamic_data 
WHERE collection = 'inspection-case' AND branch_id = '<user_branch>'

-- 主键唯一性检查在分支范围内
WHERE collection = %s AND branch_id = %s AND id != %s

-- 关联关系也按分支隔离
SELECT * FROM data_relations WHERE branch_id = '<user_branch>'
```

**复合主键设计**:
```sql
CREATE TABLE dynamic_data (
    id          VARCHAR(100) NOT NULL,
    collection  VARCHAR(200) NOT NULL,
    branch_id   VARCHAR(100) NOT NULL DEFAULT 'main',
    PRIMARY KEY (id, branch_id)  -- 同一ID可在不同分支存在
);
```

### 3.4 用户分支状态查询

**API**: `GET /versions/user-branch?collection=<name>`

**响应**:
```json
{
  "branchId": "ver-branch123",
  "branchName": "功能分支A"
}
```

**主分支响应**:
```json
{
  "branchId": null,
  "branchName": "主分支"
}
```

### 3.5 跨 Collection 分支切换

当切换分支时，系统自动识别该版本涉及的所有 Collection，并同时切换：

```python
# version_collections 表记录涉及的 Collection
SELECT collection FROM version_collections WHERE version_id = %s

# 切换时更新所有相关 Collection 的用户分支
for coll in affected_collections:
    set_user_current_branch(user_id, collection, branch_id)
```

---

## 4. 数据备份系统

### 4.1 备份范围

**全量备份**: 覆盖所有业务表（16张核心表）

| 表分类 | 表名 | 说明 |
|--------|------|------|
| 业务核心 | menus, page_configs, dynamic_data, data_relations, users | 核心业务数据 |
| 版本相关 | collection_versions, version_snapshots, version_relations, user_current_branch | 版本管理数据 |
| 系统管理 | operation_logs, export_scripts, api_keys, validation_scripts, etl_tasks, etl_logs | 配置和日志 |

**表级备份**: 仅备份指定的表或 Collection

- 可选择单个表：`['menus', 'users']`
- 可选择特定 Collection：`['dynamic_data:inspection-case']`

### 4.2 备份创建

**API**: `POST /backups`

**请求参数**:
```json
{
  "note": "部署前备份",
  "tables": null  // null=全量备份, ['menus']=表级备份
}
```

**创建流程**（`server/utils/backup.py:create_backup`）：

1. **生成备份 ID**: `backup-{uuid12位}`
2. **导出表数据**:
   - 对每张表执行 `SELECT *`
   - JSONB 字段序列化为 JSON
   - datetime 字段转为 ISO 格式
3. **自动关联数据**:
   - 如果备份 `dynamic_data`，自动备份对应的 `data_relations`
   - 自动备份对应的 `version_snapshots` 和 `version_relations`
4. **打包 ZIP**:
   - manifest.json（元数据）
   - 各表对应的 JSON 文件
5. **写入备份记录**: 存入 `backups` 表

**manifest.json 结构**:
```json
{
  "version": 1,
  "id": "backup-abc12345",
  "name": "手动备份(全量) 2026-04-16 10:30:00",
  "type": "manual",
  "scope": "full",
  "tables": ["menus", "page_configs", ...],
  "tableStats": { "menus": 25, "dynamic_data": 150 },
  "totalRecords": 500,
  "createdAt": "2026-04-16T10:30:00.000Z",
  "createdBy": "admin"
}
```

### 4.3 定时备份调度

**配置表**: `backup_settings`（单行配置）

```sql
CREATE TABLE backup_settings (
    id              INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    enabled         BOOLEAN DEFAULT FALSE,
    interval        VARCHAR(50) DEFAULT 'daily',  -- daily/weekly/monthly
    retention_count INTEGER DEFAULT 10,
    last_backup_at  TIMESTAMPTZ
);
```

**调度器实现**（`server/utils/backup.py:start_backup_scheduler`）：

```python
def scheduler_loop():
    while True:
        time.sleep(60)  # 每分钟检查
        settings = get_backup_settings()
        if settings['enabled'] and is_backup_due(settings):
            create_backup(backup_type='scheduled', created_by='系统定时')
            cleanup_old_backups(settings['retentionCount'])
```

**备份到期判断**:
```python
def is_backup_due(settings):
    last = settings['lastBackupAt']
    interval = settings['interval']
    
    if interval == 'daily':   return (now - last) >= 24h
    if interval == 'weekly':  return (now - last) >= 7 days
    if interval == 'monthly': return (now - last) >= 30 days
```

**保留策略清理**:
```python
def cleanup_old_backups(retention_count):
    # 获取所有定时备份（按时间倒序）
    # 保留最新的 retention_count 个
    # 删除超出数量的备份文件和记录
```

### 4.4 备份还原

**API**: `POST /backups/<backup_id>/restore`

**请求参数**:
```json
{
  "tables": null  // null=还原所有, ['menus']=选择性还原
}
```

**还原流程**（`server/utils/backup.py:restore_backup`）：

1. **解压并校验 ZIP**
2. **读取 manifest 和表数据**
3. **按外键依赖顺序还原** (`RESTORE_ORDER`):
   ```
   Level 1: users, page_configs, export_scripts, validation_scripts, api_keys, etl_tasks, dynamic_data
   Level 2: collection_versions, menus, etl_logs
   Level 3: data_relations, operation_logs, user_current_branch, version_snapshots, version_relations
   ```
4. **禁用触发器**: `SET session_replication_role = replica`
5. **清空目标表**: `TRUNCATE CASCADE` 或 `DELETE WHERE collection=...`
6. **批量插入数据**
7. **恢复触发器**: `SET session_replication_role = DEFAULT`
8. **事务提交**（失败则全部回滚）

**Collection 级还原**:
```python
# 仅还原指定 Collection 的数据
DELETE FROM dynamic_data WHERE collection = 'inspection-case'
DELETE FROM data_relations WHERE collection = 'inspection-case'
# 然后插入备份数据
```

### 4.5 备份上传还原

**API**: `POST /backups/upload-restore`

**用途**: 从外部 ZIP 文件还原数据

**流程**:
1. 上传 ZIP 到临时文件
2. 调用 `restore_backup` 函数
3. 清理临时文件

### 4.6 数据对比功能

**API**: `POST /backups/diff`

**对比维度**:
- 当前数据 vs 备份
- 备份 vs 备份
- 当前数据 vs 版本快照

**请求参数**:
```json
{
  "collection": "inspection-case",
  "baseSource": "current",
  "targetSource": "backup-abc12345"
}
```

**响应**:
```json
{
  "added": [...],
  "removed": [...],
  "modified": [
    {
      "id": "rec-mod1",
      "record": { ... },
      "oldRecord": { ... },
      "fields": [
        { "fieldName": "status", "oldValue": "todo", "newValue": "done" }
      ]
    }
  ],
  "unchangedCount": 100,
  "fields": [...]  // 字段定义
}
```

---

## 5. 动态数据管理

### 5.1 单表动态存储

**核心设计**: 所有业务数据存储在 `dynamic_data` 表，通过 `collection` 字段区分业务实体。

```sql
CREATE TABLE dynamic_data (
    id          VARCHAR(100) NOT NULL,
    collection  VARCHAR(200) NOT NULL,
    data        JSONB NOT NULL DEFAULT '{}',  -- 所有业务字段
    version     INTEGER DEFAULT 1,            -- 乐观锁版本号
    branch_id   VARCHAR(100) DEFAULT 'main',  -- 分支隔离
    PRIMARY KEY (id, branch_id)
);
```

**优势**:
- 新增业务实体无需建表
- 字段扩展无需 DDL
- JSONB 索引支持高效查询

### 5.2 CRUD 操作

**列表查询**: `GET /<collection>?page=1&pageSize=20&keyword=xxx&q={}`

**特性**:
- 分页支持
- 关键字搜索（跨关联 Collection）
- MongoDB 风格查询（`q` 参数）
- 分支数据过滤

**新增记录**: `POST /<collection>`

**流程**:
1. 获取用户当前分支
2. 检查主键唯一性（在分支范围内）
3. 执行校验脚本（可选）
4. 插入数据（带分支 ID）
5. 建立关联关系
6. 触发触发器规则
7. 记录操作日志

**修改记录**: `PUT /<collection>/<id>`

**特性**:
- 乐观锁检查（version 字段）
- 工作流状态转换验证
- 字段变更检测
- 触发器执行

**删除记录**: `DELETE /<collection>/<id>`

**检查**:
- 引用依赖检查（被其他 Collection 引用时禁止删除）
- 关联关系清理
- 触发器执行

### 5.3 字段控件类型

系统支持 **17 种字段控件类型**：

| 控件类型 | 标识 | 存储类型 | 特性 |
|---------|------|---------|------|
| 单行文本 | `text` | string | 基础文本输入 |
| 多行文本 | `textarea` | string | 支持换行 |
| 富文本 | `richText` | string (HTML) | Quill 编辑器 |
| 数值 | `number` | number | 整数/小数 |
| 单选下拉 | `select` | string | 静态/API/数据页选项 |
| 多选下拉 | `multiSelect` | string[] | Tag 标签展示 |
| 单选按钮 | `radio` | string | 按钮组 |
| 复选框 | `checkbox` | string[] | 多选框组 |
| 日期 | `date` | string | YYYY-MM-DD |
| 日期时间 | `datetime` | string | ISO 8601 |
| 文件上传 | `file` | object[] | 最多 5 个，单个 10MB |
| 图片上传 | `image` | object[] | 最多 9 张，单张 5MB |
| **多对多关联** | `relation` | data_relations 表 | 双向同步 M:N |
| **一对多引用** | `reference` | string | 父记录 ID，删除保护 |
| **引用选择** | `quoteSelect` | string[] | 单向多选引用 |
| 自动时间戳 | `autoTimestamp` | string | 新增/编辑自动填充 |
| 自增序列 | `autoSequence` | string | 带前缀递增编号 |

### 5.4 关联关系管理

**M:N 关联** (`relation` 类型):

```sql
CREATE TABLE data_relations (
    collection          VARCHAR(200) NOT NULL,      -- 源 Collection
    record_id           VARCHAR(100) NOT NULL,      -- 源记录 ID
    field_name          VARCHAR(200) NOT NULL,      -- 关联字段名
    related_collection  VARCHAR(200) NOT NULL,      -- 目标 Collection
    related_id          VARCHAR(100) NOT NULL,      -- 目标记录 ID
    branch_id           VARCHAR(100) DEFAULT 'main',
    PRIMARY KEY (collection, record_id, field_name, related_id, branch_id)
);
```

**双向同步机制**:
```python
# 更新关联时自动维护双向关系
# 正向：A.field -> [B, C]
# 反向：B.targetField -> [A]

# 删除正向关系 → 同步删除反向关系
# 新增正向关系 → 同步新增反向关系
```

### 5.5 乐观锁机制

```python
# 更新时检查版本号
UPDATE dynamic_data 
SET data = %s, version = version + 1 
WHERE id = %s AND version = %s

# 如果 version 不匹配，影响行数为 0，表示并发冲突
```

---

## 6. 触发器规则系统

### 6.1 触发器配置

**API**: `POST /triggerRules`

**请求参数**:
```json
{
  "name": "状态变更创建记录",
  "sourceCollection": "inspection-case",
  "triggerEvent": "fieldChange",
  "triggerCondition": {
    "field": "status",
    "value": "done"
  },
  "targetCollection": "inspection-record",
  "actionType": "create",
  "actionConfig": {
    "fieldMapping": {
      "caseId": "$source.id",
      "caseName": "$source.caseName",
      "operator": "$operator",
      "completedAt": "$NOW"
    }
  },
  "enabled": true
}
```

### 6.2 触发事件类型

| 事件类型 | 标识 | 说明 |
|---------|------|------|
| 创建 | `create` | 记录创建时触发 |
| 更新 | `update` | 记录更新时触发 |
| 字段变更 | `fieldChange` | 特定字段值变更时触发 |
| 删除 | `delete` | 记录删除时触发 |

### 6.3 动作类型

| 动作类型 | 标识 | 说明 |
|---------|------|------|
| 创建记录 | `create` | 在目标 Collection 创建新记录 |
| 更新记录 | `update` | 更新目标 Collection 的匹配记录 |
| 执行脚本 | `runScript` | 执行 Python 脚本 |

### 6.4 执行流程

```python
def fire_triggers(event, collection, record_id, old_data, new_data, operator, cur):
    # 1. 查询匹配的触发器规则
    rules = cur.execute('SELECT ... FROM trigger_rules WHERE source_collection = %s')
    
    # 2. 检查事件匹配
    if trigger_event != event: continue
    
    # 3. 检查字段变更条件
    if trigger_event == 'fieldChange':
        if new_val == old_val: continue
        if cond_value and new_val != cond_value: continue
    
    # 4. 执行动作
    _execute_action(cur, action_type, action_config, ...)
    
    # 5. 记录执行日志
    _log_trigger(cur, rule_id, ...)
```

---

## 7. ETL 数据管道

### 7.1 管道步骤类型

| 步骤类型 | 标识 | 说明 |
|---------|------|------|
| HTTP 请求 | `http_request` | 调用外部 API 获取 JSON 数据 |
| JSON 输入 | `json_input` | 手动输入 JSON 数据 |
| Python 脚本 | `script` | 自定义转换逻辑 |
| 字段映射 | `field_mapping` | 源字段→目标字段重命名 |
| 条件过滤 | `filter` | Python 表达式过滤记录 |
| 写入集合 | `save_to_collection` | 写入 dynamic_data 表 |

### 7.2 写入模式

| 模式 | 标识 | 说明 |
|------|------|------|
| 全部新增 | `insert` | 无条件插入所有记录 |
| 有则更新 | `upsert` | 匹配字段查找，存在则更新，不存在则新增 |
| 仅更新 | `update` | 仅更新已存在的记录 |

### 7.3 数据流转

```
┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
│ HTTP 请求 │───▶│ 脚本转换 │───▶│ 字段映射 │───▶│ 写入集合 │
└─────────┘    └─────────┘    └─────────┘    └─────────┘
     records ────▶ records ────▶ records ────▶ records
```

---

## 8. AI 智能查询

### 8.1 功能说明

将自然语言查询转换为 MongoDB 风格的查询条件。

**API**: `POST /ai/query`

**请求**:
```json
{
  "collection": "inspection-case",
  "query": "查找所有高风险的待处理用例"
}
```

**响应**:
```json
{
  "filter": {
    "priority": "high",
    "status": "todo"
  },
  "explanation": "匹配优先级为高且状态为待处理"
}
```

### 8.2 配置

**API 设置表**: `ai_settings`

```sql
CREATE TABLE ai_settings (
    enabled     BOOLEAN DEFAULT FALSE,
    api_key     TEXT,          -- 加密存储
    endpoint    VARCHAR(500),  -- 默认阿里云 Qwen
    model       VARCHAR(100),  -- 默认 qwen-plus
    timeout     INTEGER DEFAULT 30,
    max_tokens  INTEGER DEFAULT 1024
);
```

---

## 9. 用户权限系统

### 9.1 角色定义

| 角色 | 标识 | 权限范围 |
|------|------|---------|
| 管理员 | `admin` | 全部功能，包括系统管理 |
| 开发者 | `developer` | 数据 CRUD，导入导出，无系统管理 |
| 访客 | `guest` | 只读，禁止所有写操作 |

### 9.2 权限矩阵

| 功能 | admin | developer | guest |
|------|:-----:|:---------:|:-----:|
| 查看数据 | ✓ | ✓ | ✓ |
| 编辑数据 | ✓ | ✓ | ✗ |
| 版本管理 | ✓ | ✓ | ✗ |
| 备份管理 | ✓ | ✗ | ✗ |
| 菜单配置 | ✓ | ✗ | ✗ |
| 用户管理 | ✓ | ✗ | ✗ |
| 触发器规则 | ✓ | ✗ | ✗ |
| ETL 管理 | ✓ | ✗ | ✗ |

### 9.3 认证装饰器

```python
@login_required     # 需要登录
@write_required     # 需要登录且非 guest
@admin_required     # 需要登录且为 admin
@api_key_required   # 需要有效的 API Key
```

---

## 10. 操作日志系统

### 10.1 日志记录

**表结构**:
```sql
CREATE TABLE operation_logs (
    id              VARCHAR(100) PRIMARY KEY,
    action          VARCHAR(50),   -- create/update/delete/batch_delete/restore
    target_type     VARCHAR(100),  -- collection 名称
    target_id       VARCHAR(100),
    target_name     VARCHAR(500),  -- 显示名称
    description     VARCHAR(1000),
    operator_id     VARCHAR(100),
    operator_name   VARCHAR(200),
    operator_role   VARCHAR(50),
    batch_id        VARCHAR(100),  -- 批量操作分组
    batch_desc      VARCHAR(500),
    branch_id       VARCHAR(100),
    created_at      TIMESTAMPTZ
);
```

### 10.2 批量操作分组

批量删除、批量导出等操作使用 `batch_id` 关联，便于追溯。

---

## 11. 关系图谱

### 11.1 功能说明

可视化展示记录的所有关联关系，支持 BFS 递归扫描。

**API**: `GET /relation-graph/<collection>/<record_id>`

### 11.2 扫描算法

```python
def scan_all_related_data(start_collection, branch_id, max_records=10000):
    visited = set()       # 防止循环
    all_data = {}         # 结果
    queue = [(start_collection, 'collection')]
    
    while queue:
        coll, _ = queue.pop(0)
        if coll in all_data: continue
        
        # 查询该 Collection 的所有数据
        records = query_collection_all_data(coll, branch_id)
        all_data[coll] = records
        
        # 扫描关联
        for record in records:
            relations = query_record_relations(coll, record['id'], branch_id)
            for rel in relations:
                if rel['related_collection'] not in all_data:
                    queue.append((rel['related_collection'], 'collection'))
```

---

## 12. 前端架构

### 12.1 核心视图

| 视图 | 路径 | 功能 |
|------|------|------|
| DynamicPage | `/page/:pageId` | 动态数据页，支持表格/看板/Excel 三种视图 |
| VersionManager | 弹窗组件 | 版本管理面板 |
| BackupManager | `/admin/backup` | 备份管理页面 |
| BeyondCompareMerge | 弹窗组件 | Beyond Compare 风格合并界面 |

### 12.2 状态管理

| Store | 职责 |
|-------|------|
| authStore | JWT Token、用户信息、角色权限 |
| pageConfigStore | 页面配置、字段定义、数据 CRUD、关联解析 |
| menuStore | 菜单树、动态路由 |
| appStore | 主题、布局、初始化 |

---

## 13. Open API 外部接口

### 13.1 API Key 认证

请求头：`X-API-Key: cm_xxxxxxxxxx`

### 13.2 端点

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/v1/collections` | 列出公开的 Collection |
| GET | `/api/v1/collections/<name>` | 查询数据 |
| POST | `/api/v1/collections/<name>` | 创建数据（需 `api_writable=true`） |

### 13.3 权限控制

- 仅访问 `api_public=true` 的 Collection
- 仅写入 `api_writable=true` 的 Collection

---

## 附录：数据库表清单

| 分类 | 表名 | 说明 |
|------|------|------|
| 业务核心 | `dynamic_data` | 主数据表（JSONB） |
| | `page_configs` | 页面配置 |
| | `menus` | 菜单树 |
| | `data_relations` | M:N 关联 |
| | `users` | 用户账号 |
| 版本管理 | `collection_versions` | 版本/分支元数据 |
| | `version_snapshots` | 快照数据 |
| | `version_relations` | 快照关联 |
| | `version_collections` | 跨 Collection 追踪 |
| | `user_current_branch` | 用户分支状态 |
| 备份系统 | `backups` | 备份记录 |
| | `backup_settings` | 定时备份配置 |
| 系统管理 | `operation_logs` | 操作日志 |
| | `export_scripts` | 导出脚本 |
| | `validation_scripts` | 校验脚本 |
| | `api_keys` | API 密钥 |
| | `etl_tasks` | ETL 任务 |
| | `etl_logs` | ETL 日志 |
| | `ai_settings` | AI 配置 |
| | `trigger_rules` | 触发器规则 |
| | `trigger_logs` | 触发器日志 |
| 协作功能 | `record_comments` | 记录评论 |
| | `notifications` | 用户通知 |
| | `dashboards` | 仪表盘配置 |

---

*文档版本: 2026-04-16*
*适用系统版本: Check-Manage v2.0*