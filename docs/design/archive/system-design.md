# 系统设计文档（已归档）

> 📦 **本文档已归档。** 这是早期按主题组织的整体设计，其内容已**拆分进按业务域组织的新文档集**
> （见 [`../README.md`](../README.md) 与 `01~10` 各域设计）。请以新文档为准；本文保留作历史快照。

## 1. 系统概述

check-manage 是一个基于动态配置的企业级数据管理平台。核心设计理念是**配置驱动**：管理员通过菜单管理和页面配置定义业务数据结构，系统自动渲染对应的表单和表格，无需编写前端代码即可扩展新的业务页面。

### 技术架构

```
┌──────────────────────────────────────────────────────┐
│                  浏览器 (Vue 3.4)                     │
│  Element Plus + Pinia + Vue Router + Univer          │
└────────────────────┬─────────────────────────────────┘
                     │ HTTP (JWT Bearer Token)
                     │ Vite Proxy /api → :3001
┌────────────────────▼─────────────────────────────────┐
│               Flask 后端 (:3001)                      │
│  24 Blueprint 路由 + JWT认证 + 沙箱脚本引擎            │
└────────────────────┬─────────────────────────────────┘
                     │ psycopg2 连接池
┌────────────────────▼─────────────────────────────────┐
│               PostgreSQL 数据库                       │
│  22 张表 + JSONB 灵活存储 + 版本控制                  │
└──────────────────────────────────────────────────────┘
```

### 核心特性

| 特性 | 说明 |
|------|------|
| **配置驱动** | 无需编码，通过页面配置自动生成表单和表格 |
| **版本控制** | 支持数据快照、分支管理、合并冲突解决 |
| **Excel视图** | 基于 Univer 的真实电子表格体验 |
| **AI查询** | 自然语言转查询条件（集成 Qwen API） |
| **ETL管道** | 可视化数据导入管道，支持 HTTP/脚本/映射/过滤 |
| **备份还原** | 全量/表级备份，定时策略，数据对比 |

---

## 2. 数据库设计

### 2.1 表结构总览

系统共 **22 张业务表**，分为七大类：

| 分类 | 表名 | 用途 |
|------|------|------|
| **业务核心** | menus | 菜单层级结构 |
| | page_configs | 页面字段配置（数据 Schema） |
| | dynamic_data | 所有业务数据的统一存储（JSONB） |
| | data_relations | 多对多关联关系 |
| **版本控制** | collection_versions | 集合版本（快照/分支） |
| | version_snapshots | 版本数据快照 |
| | version_relations | 版本关联数据 |
| | user_current_branch | 用户当前分支 |
| **脚本引擎** | export_scripts | 自定义导出脚本 |
| | validation_scripts | 数据校验脚本 |
| **ETL 数据管道** | etl_tasks | ETL 任务定义 |
| | etl_logs | ETL 执行日志 |
| **对外接口** | api_keys | Open API 密钥管理 |
| | ai_settings | AI 服务配置 |
| **系统管理** | users | 用户账号和角色 |
| | operation_logs | 操作审计日志 |
| | backups | 备份元数据 |
| | backup_settings | 定时备份配置 |
| **自动化** | trigger_rules | 触发器规则 |
| | trigger_logs | 触发器执行日志 |
| **协作** | comments | 记录评论 |
| | notifications | 用户通知 |
| | dashboards | 仪表盘配置 |

### 2.2 核心表详细结构

#### menus — 菜单配置

```sql
id          VARCHAR(100) PRIMARY KEY
name        VARCHAR(200) NOT NULL
icon        VARCHAR(100)
page_id     VARCHAR(100)              -- 关联的页面配置 ID
parent_id   VARCHAR(100)              -- 父菜单 ID
"order"     INTEGER DEFAULT 0
path        VARCHAR(500)
roles       JSONB DEFAULT '["admin","developer","guest"]'
export_script TEXT                    -- 菜单级导出脚本 ID
```

#### page_configs — 页面配置

```sql
id                  VARCHAR(100) PRIMARY KEY
name                VARCHAR(200) NOT NULL
description         TEXT
api_endpoint        VARCHAR(500)
fields              JSONB DEFAULT '[]'          -- 字段配置数组
export_scripts      JSONB DEFAULT '[]'          -- 页面级导出脚本
row_export_scripts  JSONB DEFAULT '[]'          -- 行级导出脚本
api_public          BOOLEAN DEFAULT FALSE
validation_script   TEXT
kanban_config       JSONB                        -- 看板配置
delete_binding      JSONB                        -- 删除绑定配置
created_at          TIMESTAMPTZ DEFAULT NOW()
updated_at          TIMESTAMPTZ DEFAULT NOW()
```

`fields` JSONB 数组元素结构：

```json
{
  "id": "field-1",
  "label": "用例名称",
  "fieldName": "caseName",
  "controlType": "text",
  "required": true,
  "order": 1,
  "hidden": false,
  "isPrimaryKey": false,
  "placeholder": "请输入",
  "options": [],
  "optionsSource": { "type": "static" },
  "relationConfig": null,
  "referenceConfig": null,
  "sequenceConfig": { "prefix": "IC-", "max": 999 }
}
```

`kanban_config` 结构（可选）：

```json
{
  "enabled": true,
  "groupField": "status",
  "orderField": "order",
  "columns": [
    { "value": "todo", "label": "待处理", "color": "#909399" },
    { "value": "doing", "label": "进行中", "color": "#409EFF" },
    { "value": "done", "label": "已完成", "color": "#67C23A" }
  ]
}
```

`delete_binding` 结构（可选）：

```json
{
  "enabled": true,
  "targetCollection": "sub-items",
  "dialogTitle": "删除确认",
  "dialogWidth": "500px",
  "autoFillOperator": true,
  "inheritFields": [
    { "sourceField": "parentId", "targetField": "parentRef" }
  ]
}
```

#### dynamic_data — 动态业务数据

```sql
id          VARCHAR(100) PRIMARY KEY
collection  VARCHAR(200) NOT NULL       -- 集合名
data        JSONB DEFAULT '{}'          -- 业务数据
version     INTEGER DEFAULT 1           -- 乐观锁版本号
created_at  TIMESTAMPTZ DEFAULT NOW()
updated_at  TIMESTAMPTZ DEFAULT NOW()
```

索引：`idx_dynamic_data_collection ON (collection)`

#### collection_versions — 版本控制

```sql
id              VARCHAR(100) PRIMARY KEY
collection      VARCHAR(200) NOT NULL       -- 所属集合
name            VARCHAR(200) NOT NULL       -- 版本名称
version_type    VARCHAR(50) NOT NULL        -- snapshot / branch
parent_version  VARCHAR(100)                -- 父版本 ID（分支用）
status          VARCHAR(50) DEFAULT 'active'
data_hash       VARCHAR(64)                 -- 数据哈希
records_count   INTEGER DEFAULT 0
relations_count INTEGER DEFAULT 0
created_by      VARCHAR(100)
created_at      TIMESTAMPTZ DEFAULT NOW()
is_protected    BOOLEAN DEFAULT FALSE
```

#### version_snapshots — 版本数据

```sql
version_id      VARCHAR(100) NOT NULL
record_id       VARCHAR(100) NOT NULL
record_data     JSONB NOT NULL
PRIMARY KEY (version_id, record_id)
```

#### trigger_rules — 触发器规则

```sql
id              VARCHAR(100) PRIMARY KEY
name            VARCHAR(200) NOT NULL
collection      VARCHAR(200) NOT NULL
trigger_field   VARCHAR(200)               -- 触发字段
trigger_value   VARCHAR(500)               -- 触发值
action_type     VARCHAR(50)                -- update_field / run_script
action_config   JSONB                      -- 动作配置
conditions      JSONB                      -- 附加条件
enabled         BOOLEAN DEFAULT TRUE
created_at      TIMESTAMPTZ DEFAULT NOW()
```

#### ai_settings — AI 服务配置

```sql
id              INTEGER PRIMARY KEY CHECK (id = 1)
enabled         BOOLEAN DEFAULT FALSE
api_key         TEXT                        -- 加密存储
endpoint        VARCHAR(500)
model           VARCHAR(100)
timeout         INTEGER DEFAULT 30
max_tokens      INTEGER DEFAULT 1024
```

### 2.3 数据依赖关系

```
                    ┌──────────┐
                    │  users   │
                    └────┬─────┘
                         │ operator_id
                         ▼
                  ┌──────────────┐
                  │operation_logs│
                  └──────────────┘

  ┌──────────┐  page_id    ┌──────────────┐  定义 Schema   ┌──────────────┐
  │  menus   │────────────▶│ page_configs  │──────────────▶│ dynamic_data │
  └──────────┘             └──────┬───────┘               └──────┬───────┘
       │                          │                              │
       │ parent_id                │ fields                       │
       ▼                          ▼                              ▼
  ┌──────────┐             ┌────────────┐               ┌──────────────┐
  │  menus   │             │ 16种控件    │               │data_relations│
  └──────────┘             └────────────┘               └──────────────┘

  ┌──────────────┐                    ┌──────────────┐
  │ collection_  │◄───────────────────│ dynamic_data │
  │   versions   │     快照/分支       └──────────────┘
  └──────┬───────┘                           │
         │ version_id                        │
         ▼                                   ▼
  ┌──────────────┐                   ┌──────────────┐
  │  version_    │                   │   trigger_   │
  │  snapshots   │                   │    rules     │
  └──────────────┘                   └──────────────┘
```

---

## 3. 字段控件类型

系统支持 **17 种字段控件类型**：

| 控件类型 | 标识 | 存储类型 | 特性 |
|---------|------|---------|------|
| 单行文本 | text | string | 基础文本输入 |
| 多行文本 | textarea | string | 支持换行 |
| 富文本 | richText | string (HTML) | Quill 编辑器 |
| 数值 | number | number | 整数/小数 |
| 单选下拉 | select | string | 静态/API/数据页选项 |
| 多选下拉 | multiSelect | string[] | Tag 标签展示 |
| 单选按钮 | radio | string | 按钮组 |
| 复选框 | checkbox | string[] | 多选框组 |
| 日期 | date | string | YYYY-MM-DD |
| 日期时间 | datetime | string | ISO 8601 |
| 文件上传 | file | object[] | 最多 5 个，单个 10MB |
| 图片上传 | image | object[] | 最多 9 张，单张 5MB |
| 多对多关联 | relation | data_relations | 双向同步 |
| 一对多引用 | reference | string | 父记录 ID，删除保护 |
| 引用选择 | quoteSelect | string[] | 单向多选引用 |
| 自动时间戳 | autoTimestamp | string | 新增/编辑自动填充 |
| 自增序列 | autoSequence | string | 带前缀递增编号 |

---

## 4. 认证与权限模型

### 4.1 JWT 认证流程

```
登录请求 → 验证密码 → 生成 JWT Token (userId, username, role, exp)
         → 前端存储 Token → 后续请求携带 Authorization: Bearer {token}
         → 后端装饰器解码验证 → g.current_user
```

### 4.2 角色权限矩阵

| 功能 | admin | developer | guest |
|------|:-----:|:---------:|:-----:|
| 查看数据页 | Y | Y | Y |
| 新增/编辑/删除数据 | Y | Y | N |
| 导入/导出 | Y | Y | Y |
| 批量删除 / 数据对比 | Y | | |
| 版本管理 | Y | | |
| 菜单管理 | Y | | |
| 页面配置 | Y | | |
| 用户管理 | Y | | |
| 操作日志 | Y | | |
| 系统备份 | Y | | |
| 脚本管理 | Y | | |
| Open API 管理 | Y | | |
| ETL 管理 | Y | | |
| AI 设置 | Y | | |
| 触发器规则 | Y | | |

### 4.3 Open API 认证

```
请求携带 X-API-Key: cm_xxxxxxxxxx
  → 查询 api_keys 表，匹配 key_hash
  → 校验 is_active = true
  → 仅允许访问 api_public = true 的数据集合
```

---

## 5. 脚本执行沙箱

导出脚本、校验脚本和 ETL 脚本在隔离沙箱中执行。

### 5.1 安全模型

```
1. 语法检查（正则）
   - 禁止 import / from...import 语句
   - 禁止 open(), exec(), eval(), compile() 等危险函数
   - 禁止 __dunder__ 属性访问

2. 受限全局
   - 仅注入白名单模块：json, csv, io, re, math, collections, xml.etree, datetime, pandas (pd), numpy (np)
   - 仅注入安全内置函数：len, str, int, float, list, dict, sorted, range 等

3. 超时保护
   - 独立线程执行，60 秒超时自动终止（菜单级导出 300 秒）
```

### 5.2 三种脚本类型

| 类型 | 注入变量 | 输出要求 |
|------|---------|---------|
| 导出脚本 | data, fields, page_name | result (str/bytes) |
| 校验脚本 | data, fields, all_data, is_edit | add_error()/add_warning() |
| ETL 脚本 | records | result (list[dict]) |

---

## 6. ETL 数据管道

### 6.1 架构概述

ETL 执行引擎按顺序执行管道步骤，数据在步骤间通过 `context['records']` 流转。

```
┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
│ HTTP 请求 │───▶│ 脚本转换 │───▶│ 字段映射 │───▶│ 写入集合 │
└─────────┘    └─────────┘    └─────────┘    └─────────┘
     records ────▶ records ────▶ records ────▶ records
```

### 6.2 步骤类型

| 类型 | 标识 | 说明 |
|------|------|------|
| HTTP 请求 | http_request | 调用外部 API 获取 JSON 数据 |
| JSON 输入 | json_input | 手动输入 JSON 数据 |
| Python 脚本 | script | 自定义转换逻辑 |
| 字段映射 | field_mapping | 源字段→目标字段重命名 |
| 条件过滤 | filter | Python 表达式过滤记录 |
| 写入集合 | save_to_collection | 写入 dynamic_data 表 |

### 6.3 写入模式

| 模式 | 说明 |
|------|------|
| insert | 全部新增 |
| upsert | 有则更新无则新增 |
| update | 仅更新已有记录 |

---

## 7. 备份与还原

### 7.1 备份范围

支持**全量备份**和**表级备份**：

- 全量：覆盖所有业务表
- 表级：仅备份指定的表（如 menus + dynamic_data）

### 7.2 备份格式

ZIP 文件内含：
- `manifest.json` — 版本号、时间戳、各表记录数
- 各表对应的 JSON 文件

### 7.3 数据对比

支持三种对比场景：
- 当前数据 vs 备份
- 备份 vs 备份
- 当前数据 vs 版本快照

对比结果包括：新增、删除、修改（字段级差异）、未变记录数。

---

## 8. 版本控制系统

### 8.1 版本类型

| 类型 | 说明 |
|------|------|
| snapshot | 点-in-time 快照，不可修改 |
| branch | 分支，可在其上继续修改数据 |

### 8.2 合并机制

支持**部分合并**，允许字段级选择：

```
源版本（合并来源）───▶ 目标版本（当前分支）
     │
     ├── 新增记录 → 选择要合并的记录
     ├── 删除记录 → 选择要删除的记录
     └── 修改记录 → 逐字段选择保留哪个值
```

### 8.3 合并冲突解决

`MergeConflictDialog` 组件提供单页面统一界面：

1. **记录选择区** — 选择要处理的记录
2. **字段选择区** — 对修改记录逐字段决策
3. **操作按钮** — 确认合并 / 取消

---

## 9. 前端架构

### 9.1 技术栈

| 类别 | 技术 |
|------|------|
| 框架 | Vue 3.4 + TypeScript 5.4 |
| 构建 | Vite 5.1 |
| UI 库 | Element Plus 2.6 |
| 状态管理 | Pinia 2.1 |
| 电子表格 | Univer 0.16 |
| 代码编辑 | CodeMirror 6 |
| 富文本 | Quill |
| 图谱 | force-graph |

### 9.2 路由结构

```
/login                          公开页面
/                               主布局（需认证）
├── /home                       首页
├── /page/:pageId               动态数据页
│   ├── 表格视图
│   ├── Excel 视图 (Univer)
│   └── 看板视图 (Kanban)
├── /admin/menu                 菜单管理
├── /admin/page-config          页面配置
├── /admin/users                用户管理
├── /admin/operation-log        操作日志
├── /admin/backup               系统备份
├── /admin/export-scripts       导出脚本
├── /admin/validation-scripts   校验脚本
├── /admin/api-keys             Open API
├── /admin/etl-tasks            ETL 管理
├── /admin/ai-settings          AI 设置
├── /admin/trigger-rules        触发器规则
└── /admin/query                查询控制台
```

### 9.3 状态管理（Pinia Stores）

| Store | 职责 |
|-------|------|
| authStore | JWT Token、用户信息、路由权限 |
| appStore | 应用初始化、主题 |
| menuStore | 菜单树构建、菜单 CRUD |
| pageConfigStore | 页面配置 CRUD、字段管理、关联解析 |
| tabStore | 多标签页工作区 |
| exportScriptStore | 导出脚本状态 |

### 9.4 核心组件

| 组件 | 路径 | 功能 |
|------|------|------|
| DynamicPage | views/dynamic/ | 数据页面入口，切换视图 |
| DynamicForm | components/dynamic-form/ | 表单渲染器 |
| DataTable | components/common/ | 表格视图 |
| ExcelView | components/common/ | Univer 电子表格 |
| KanbanBoard | components/common/ | 看板视图 |
| VersionManager | components/common/ | 版本管理 |
| MergeConflictDialog | components/common/ | 合并冲突解决 |
| CommandPalette | components/common/ | 命令面板 (Cmd+K) |
| RelationGraphDialog | components/common/ | 关系图谱 |
| BackupDiffDialog | components/common/ | 数据对比 |

---

## 10. API 端点汇总

### 10.1 认证

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /auth/login | 登录 |
| GET | /auth/me | 当前用户 |
| PUT | /auth/password | 修改密码 |

### 10.2 动态数据

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /:collection | 数据列表（分页） |
| GET | /:collection/:id | 单条数据 |
| POST | /:collection | 新增数据 |
| PUT | /:collection/:id | 修改数据 |
| DELETE | /:collection/:id | 删除数据 |
| POST | /:collection/batch | 批量操作 |

### 10.3 版本管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /versions | 版本列表 |
| POST | /versions | 创建版本 |
| GET | /versions/:id | 版本详情 |
| DELETE | /versions/:id | 删除版本 |
| POST | /versions/:id/restore | 还原版本 |
| POST | /versions/:id/switch | 切换分支 |
| POST | /versions/diff | 版本对比 |
| POST | /versions/partial-merge | 部分合并 |

### 10.4 备份

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /backups | 备份列表 |
| POST | /backups | 创建备份 |
| DELETE | /backups/:id | 删除备份 |
| GET | /backups/:id/download | 下载 |
| POST | /backups/:id/restore | 还原 |
| POST | /backups/upload-restore | 上传还原 |
| GET | /backups/settings | 定时设置 |
| PUT | /backups/settings | 更新设置 |
| POST | /backups/diff | 数据对比 |
| GET | /backups/tables | 可备份表列表 |

### 10.5 AI 查询

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /ai/query | AI 自然语言查询 |
| GET | /ai/settings | AI 配置 |
| PUT | /ai/settings | 更新配置 |

### 10.6 查询控制台

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /query/collections | 可查询集合列表 |
| POST | /query/execute | 执行查询 |

### 10.7 触发器

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /triggerRules | 规则列表 |
| POST | /triggerRules | 创建规则 |
| PUT | /triggerRules/:id | 修改规则 |
| DELETE | /triggerRules/:id | 删除规则 |
| GET | /triggerRules/:id/logs | 执行日志 |

### 10.8 其他模块

| 模块 | 端点前缀 | 说明 |
|------|---------|------|
| 用户 | /users | CRUD + 重置密码 |
| 菜单 | /menus | CRUD + 导出脚本绑定 |
| 页面配置 | /pageConfigs | CRUD |
| 关联关系 | /relations | GET/PUT/DELETE |
| 导出脚本 | /exportScripts | CRUD + 测试 + 执行 |
| 校验脚本 | /validationScripts | CRUD + 测试 |
| API Key | /apiKeys | CRUD + 启用禁用 |
| ETL | /etlTasks | CRUD + 执行 + 日志 |
| 操作日志 | /operationLogs | 查询 + 导出 |
| 评论 | /comments | CRUD |
| 通知 | /notifications | 查询 + 已读 |
| 仪表盘 | /dashboards | CRUD + 聚合 |
| Open API | /api/v1/collections | 外部访问 |

---

## 11. 扩展性设计

### 新增业务数据页

无需修改代码，仅需配置：
1. 页面配置中创建新页面，定义字段
2. 菜单管理中创建菜单项，关联页面配置
3. 系统自动渲染对应的表单和表格

### 新增视图类型

在 `DynamicPage.vue` 中注册新视图组件，实现 `viewMode` 切换逻辑。

### 新增字段控件

1. 在 `src/components/dynamic-form/controls/` 创建控件组件
2. 在 `DynamicForm.vue` 和 `DataTable.vue` 中注册
3. 更新 `FieldConfig` 类型定义

### 动态路由保护

`server/routes/dynamic.py` 中的 `RESERVED` 集合维护系统保留路径名：

```python
RESERVED = {
  'menus', 'pageConfigs', 'auth', 'users', 'relations',
  'operationLogs', 'backups', 'exportScripts', 'apiKeys',
  'validationScripts', 'etlTasks', 'ai', 'query',
  'versions', 'triggerRules', 'comments', 'notifications',
  'dashboards', 'relation-graph', 'timeline', 'menu-export'
}
```