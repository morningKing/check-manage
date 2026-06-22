# 巡检用例管理系统

配置驱动的动态数据管理平台。通过菜单配置和页面字段定义，**无需编码**即可创建业务数据页面，并内置数据关联、导入导出、脚本扩展、ETL 管道、版本分支、AI 智能助手等能力。

> 核心理念：不为每个业务实体写新的 Vue 页面或建数据库表，而是定义一份 `PageConfig`（字段 schema）+ 一条 `Menu` 菜单，系统自动生成 UI 与 API。

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | Vue 3 + TypeScript + Element Plus + Pinia + Vite |
| 后端 | Python Flask + psycopg2 |
| 数据库 | PostgreSQL（JSONB 单表灵活存储） |
| AI 运行时 | OpenCode（Agent 运行时）+ 独立 MCP Server |
| AI 长期记忆 | mem0 + Chroma（本地嵌入式向量库，按用户分区） |
| 代码编辑 | CodeMirror 6 |
| 表格/电子表格 | Univer.js |
| 认证 | JWT（Bearer Token） |
| 测试 | Vitest（前端）+ Pytest（后端）+ Playwright（E2E） |

## 功能概览

### 数据建模与展示
- **动态数据页面** — 配置字段结构即自动生成表单、表格；支持表格 / 看板 / Excel / 日历 / 甘特 多种视图
- **丰富控件类型** — 文本、数字、富文本、选择、日期、文件/图片、关联、引用、自增序列、自动时间戳、复合文本等
- **菜单管理** — 可视化树形编辑，3 级嵌套（工作空间 → 项目 → 数据页），基于角色的可见性控制
- **数据关联** — 多对多双向关联（relation）、一对多引用（reference）、单向引用选择（quoteSelect），自动同步
- **自动字段** — autoTimestamp / autoSequence / compositeText，新增编辑时自动填充
- **行内复制新增** — 操作栏「复制」按钮，预填充整行数据（跳过自增/时间戳字段），人工检视后入库

### 数据流转
- **导入导出** — Excel 模板导入、Excel 全量导出、自定义 Python 脚本导出（json/csv/xml/txt/html）；脚本支持页面级与菜单级两种维度
- **导出脚本调试** — 内置「测试」面板可选真实数据页执行；另提供 `scripts/debug_export_script.py` 本地调试工具（拉真实数据、完整 traceback）
- **数据校验** — Python 校验脚本绑定页面，新增/编辑时自动执行，支持关联数据校验
- **ETL 数据管道** — 可视化步骤编排：HTTP 抽取、脚本转换、字段映射、条件过滤、写入集合
- **Open API** — API Key 认证，外部系统按集合读写数据
- **Webhook 规则** — `create`/`update`/`delete`/`merge` 事件触发，条件过滤 + HMAC-SHA256 签名 + 重试

### 版本与协作
- **项目版本管理** — 项目级分支/快照，创建、合并、锁定，跨分支数据隔离
- **跨项目依赖** — 三种依赖类型（track-main / read-write / read-only），依赖校验与通知机制
- **操作审计** — 全量操作日志，批次聚合、筛选、导出
- **系统备份** — 手动/定时备份，下载、还原、跨环境迁移；当前数据与备份的逐字段差异对比

### 流程编排（工作流）
- **跨页工作流引擎** — 把多个数据页串成一条命名业务流程：不同**角色**在不同**阶段**处理；记录推进时**自动在下一页生成下游记录**（按字段映射携带上游数据并反向关联），支持**驳回**回退
- **图形化流程设计器** — 全屏画布**拖拽创建节点、拉连线定义流向（DAG）**、拖动布局；点节点编阶段、点连线编流向
- **条件路由** — 推进边可配**单条件**（`字段 op 值`，运算符 `== != > >= < <= contains`，留空=默认边）：命中条件的边优先，否则走默认边
- **并行多分支（v2）** — 一个阶段可**并行扇出**到多个分支（命中的多条条件边/多条默认边全取），各分支独立推进，**所有分支结束**实例才完成
- **待办收件箱** — 按「当前阶段办理角色 ∋ 我」聚合运行中实例的待办；每个并发活动分支一条
- **状态机驱动** — 推进/驳回复用字段的 `workflowConfig` 状态机；推进失败即回滚整笔事务，杜绝「状态已改、实例未推进」

### AI 智能助手
- **AI 对话** — Claude 风格聊天抽屉，对接 OpenCode Agent 运行时，可 @ 选择子智能体、挑选模型，工具调用经独立 MCP Server 接入平台能力
- **批任务** — 选 N 个文件 + 1 个 Prompt → N 个隔离会话并发处理（限流 3 并发），支持 Prompt 模板、失败重试、指定 Agent
- **定时 AI 流水线** — 按计划扫描数据页，把待处理记录交给 AI 处理并把结构化结果回写记录，状态流转 待处理 → 处理中 → 已处理/处理失败
- **长期记忆** — 基于 mem0（本地 Chroma 向量库）按用户管理跨会话记忆：发送前向量召回注入、每轮对话后台自动抽取提炼；支持在「我的记忆」里手动补写（默认提炼 / 「原样保存」不提炼）与查看/删除；默认关闭、随系统备份
- **会话治理与审计** — 个人只能关闭/重开会话（不可物理删除、历史永久保留），管理员可归档，生命周期写操作日志可审计溯源

### 首页与仪表盘
- **可配置首页区块** — 欢迎卡片、统计概览、快捷入口、Markdown、数据卡片、**快速录入表单**（点击弹出表单直接入库到关联数据页）
- **仪表盘** — 可视化图表配置

## 快速开始

### 环境要求

- Node.js >= 18
- Python >= 3.9
- PostgreSQL >= 13
- （可选，使用 AI 功能时）[OpenCode](https://opencode.ai) 运行时

### 安装依赖

```bash
# 前端
npm install

# 后端
pip install flask flask-cors psycopg2-binary PyJWT pytest apscheduler

# （可选）AI MCP Server 依赖，在其独立虚拟环境中安装
cd mcp-server && python -m venv .venv && .venv\Scripts\pip install -e .
```

### 配置数据库

编辑 `server/config.py`（或 `server/.env`），设置 PostgreSQL 连接：

```python
DB_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'dbname': 'casemanage',
    'user': 'postgres',
    'password': 'your_password',
}
```

### 初始化数据库

```bash
cd server
python init_db.py
```

自动创建所有数据表并初始化系统菜单、默认管理员账号。

### 启动服务

```bash
# 同时启动 后端 + 前端 + MCP Server
npm run dev:all

# 或分别启动
npm run server   # 后端（端口 3002）
npm run dev      # 前端（端口 5173，自动代理 /api → 后端）
npm run mcp      # AI MCP Server（端口 3003，使用 AI 功能时需要）
```

> 使用 AI 对话/批任务/定时任务前，还需另行启动 OpenCode：`opencode serve --port 4096`。

访问 `http://localhost:5173`，使用 **admin / admin123** 登录。

## 测试

```bash
npm run test          # 前端单元测试（Vitest）
npm run test:server   # 后端测试（Pytest）
npm run test:all      # 前后端全部
npm run test:e2e      # E2E（Playwright，需服务运行）
```

## 项目结构

```
check-manage/
├── server/                      # Flask 后端
│   ├── app.py                   # 应用入口（注册 35 个蓝图 + 后台调度器）
│   ├── config.py / .env         # 配置
│   ├── init_db.py               # 数据库初始化
│   ├── routes/                  # 路由模块（auth/menus/dynamic/relations/...）
│   └── utils/                   # 工具（db 连接池、JWT、脚本沙箱、ETL/批任务/扫描引擎等）
├── mcp-server/                  # 独立 MCP Server（FastAPI + MCP Streamable-HTTP）
├── src/                         # Vue 前端
│   ├── api/                     # API 请求层
│   ├── components/              # 公共组件（common / dynamic-form / home / ai-chat / layout）
│   ├── router/                  # 路由（动态路由由菜单生成）
│   ├── stores/                  # Pinia 状态
│   ├── types/                   # TypeScript 类型
│   ├── utils/                   # 工具函数
│   └── views/                   # 页面（admin / dynamic / home / ai-chat / workflow / login）
├── scripts/
│   └── debug_export_script.py   # 导出脚本本地调试工具
├── e2e/                         # Playwright E2E 用例
├── docs/                        # 设计文档、使用手册、规格与计划
└── package.json
```

## 架构要点

- **单表动态数据**：所有业务数据存于 `dynamic_data` 表的 JSONB 列，按 `collection`（由 `pageId` 推导）区分实体，新增实体/字段无需迁移。
- **动态路由**：`src/router/dynamicRoutes.ts` 运行时读取 `menus` 表生成 Vue Router 路由，统一指向 `DynamicPage.vue`。
- **字段驱动行为**：`controlType` 决定渲染组件与逻辑（如 `relation` 触发 M:N 处理、`reference` 触发字段继承）。
- **API 代理**：Vite 将 `/api` 代理到后端 3002 并去掉 `/api` 前缀，故后端路由不带 `/api`。
- **后台调度器**：备份调度、跨项目依赖巡检、批任务 Worker、AI 扫描调度均在 `app.py` 启动（`WERKZEUG_RUN_MAIN` 守卫避免重载双启）。

## 用户角色

数据驱动、可自定义的 RBAC：

- **admin** — 永久超级管理员，全部权限，不可删除
- **developer** — 读写数据，无管理功能
- **guest** — 只读

权限分三个粒度：管理功能开关（`admin.*` 能力键）、数据页 CRUD、菜单可见性。后端为权威（`@require_permission` / `@write_required`），前端仅做 UX 门禁。

## 数据库主要表

| 表名 | 说明 |
|------|------|
| menus / page_configs / dynamic_data | 菜单树 / 页面配置 / 业务数据（核心三表） |
| data_relations | 多对多关联关系 |
| users | 用户账号 |
| project_versions / project_dependencies / project_dependency_relations | 项目分支、跨项目依赖 |
| webhook_rules / webhook_logs | Webhook 规则与日志 |
| export_scripts / validation_scripts | 导出脚本、校验脚本 |
| etl_tasks / etl_logs | ETL 任务与日志 |
| api_keys / operation_logs | Open API 密钥、操作审计 |
| backups / backup_settings | 备份记录、定时备份配置 |
| workflow_definitions / workflow_instances | 工作流定义（阶段 + 流向边 + 条件）、运行实例（并发活动分支 + 轨迹） |
| notifications / record_comments / trigger_rules | 通知、记录评论、联动规则 |
| dashboards / home_widgets / system_config | 仪表盘、首页区块、系统配置 |
| ai_settings / ai_chat_sessions / ai_chat_messages | AI 配置、对话会话与消息 |
| ai_chat_batches / ai_chat_prompt_templates / ai_scan_tasks | AI 批任务、Prompt 模板、定时扫描任务 |
| column_views | 数据页列视图配置 |

## 构建部署

```bash
# 构建前端（含 vue-tsc 类型检查）
npm run build      # 产出 dist/

# 一键构建并启动（前端 + 后端 + 反向代理，默认 8080）
npm run start
```

生产部署：前端静态文件交由 Nginx 等服务，后端以 `python app.py` 或 gunicorn 运行；启用 AI 功能时另起 MCP Server 与 OpenCode。

## 文档

- `docs/user-guide/` — 用户操作手册（全量功能说明、数据查询、脚本上传等）
- `docs/design/` — 系统设计、架构与数据库结构
- `docs/superpowers/specs/` 与 `docs/superpowers/plans/` — 各特性的设计规格与实施计划
- `docs/user-guide/ai/scan-tasks.md` — 定时 AI 数据流水线指南
- `docs/user-guide/data/workflow.md` — 跨页工作流引擎使用指南（图形化 DAG 设计器、条件路由、并行分支）

## License

MIT
