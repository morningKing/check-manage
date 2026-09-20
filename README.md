# BKB · 数据智能平台

AI 原生的配置驱动数据管理平台。通过菜单配置和页面字段定义，**无需编码**即可创建业务数据页面；再交给内置的 **AI Agent（OpenCode 运行时 + MCP 工具体系）** 去对话处理、批量加工、定时巡检你的数据——从单条问答到全页自动化，AI 能力贯穿始终。

> 核心理念：不为每个业务实体写新的 Vue 页面或建数据库表，而是定义一份 `PageConfig`（字段 schema）+ 一条 `Menu` 菜单，系统自动生成 UI 与 API；AI 则通过 MCP 工具直接理解并操作这些动态页面，无需为每个页面单独开发集成。

## AI 能力（核心亮点）

### AI 对话助手
- **Claude 风格对话页** — SSE 流式回复，对接 OpenCode Agent 运行时；可选模型与 Agent，支持 `@` 子智能体委派、`/` 命令面板（含上下文压缩）
- **MCP 工具体系** — 12 个内置工具（数据查询/导出 Excel/读上传/存产物/沙箱跑 Python/长期记忆/轨迹分析等），AI 经独立 MCP Server 直接读写平台数据，**按角色鉴权**；还可注册外部 MCP Server
- **产物与变更可见** — AI 写的文件、代码改动（git diff 视图）、生成的图表/文档直接在会话里预览与下载；工具调用以人类可读摘要渲染、敏感字段脱敏
- **子代理轨迹** — AI 委派子代理的完整执行轨迹逐级展开、服务端持久化（深度上限 5 层），失败原因透明可查
- **会话韧性** — 服务端独立持久化（关浏览器不丢消息）、SSE 断线重连与状态收敛、OpenCode 会话失效自动重建续跑
- **长期记忆** — mem0 按用户管理跨会话记忆：发送前向量召回注入、每轮后台自动提炼；「我的记忆」抽屉可查看/删除/手动补写（提炼或原样保存）；随系统备份

### AI 批任务
- **N 个文件 × 1 个 Prompt → N 个隔离会话**并发处理（限流 3 并发），实时进度、逐子会话查看完整对话
- **全生命周期治理** — 追加文件、重试失败、单子会话重新执行/继续对话、编辑 Agent/模型、暂停/恢复、完成回调（站内信 + Webhook）
- **Prompt 模板** — 按用户管理，对话与批任务共用
- **管理端** — `/admin/ai-batches` 跨用户监控全部批任务，产出文件可预览、批量导入数据页

### AI 定时任务（定时 AI 数据流水线）
- 按计划扫描数据页，把**待处理记录**（字段 + 附件）交给 AI，结构化 JSON 结果**原子回写**记录列，状态流转 待处理 → 处理中 → 已处理/处理失败
- 数据页行操作可绑定「AI 按钮」手动触发单条处理；支持产出文件自动挂回记录文件字段
- 管理页 `/admin/ai-scan` 配置任务（扫描范围、字段映射、计划间隔）

### AI OpenAPI（对外集成）
- API Key 鉴权的完整对外家族：`/v1/ai-batches/*`（批任务全生命周期 + 文件产出 + 结果获取）、`/v1/ai-sessions/*`（单会话）、`/v1/ai-scan-tasks/*/run-now`（触发扫描）、`/v1/prompt-templates/*`、`/v1/memories/*`、行操作触发
- 批任务完成 **HMAC-SHA256 签名回调**；密钥按用户绑定、数据按密钥隔离、路径归属与请求体双重防护
- 交互式 API 文档：`docs/user-guide/integration/ai-api-reference.html`

### 会话治理与 AI 审计
- 个人关闭/重开/清空会话，管理员归档；生命周期全量写操作日志可审计溯源；消息永久保留
- **执行审计** — 每次 AI 执行的 Agent/模型解析、Prompt 快照（默认脱敏）、工具调用事件、七态步骤判定与确定性评分
- **轨迹分析** — 一键对任意会话发起 AI 事故分析（分析会话自动创建），证据分级 confirmed/inferred/unknown；SkillOpt 按 Skill 版本聚合成功率与耗时，插件上报 confirmed 级证据

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | Vue 3 + TypeScript + Element Plus + Pinia + Vite |
| 后端 | Python Flask + psycopg2 |
| 数据库 | PostgreSQL（JSONB 单表灵活存储） |
| AI 运行时 | OpenCode（Agent 运行时）+ 独立 MCP Server（FastAPI + Streamable-HTTP） |
| AI 长期记忆 | mem0 + Chroma（本地嵌入式向量库，按用户分区） |
| 代码编辑 | CodeMirror 6 |
| 表格/电子表格 | Univer.js |
| 认证 | JWT（Bearer Token） |
| 测试 | Vitest（前端）+ Pytest（后端）+ Playwright（E2E，含 `e2e/ai-full/` 全量 AI 回归套件） |

## 平台功能

### 数据建模与展示
- **动态数据页面** — 配置字段结构即自动生成表单、表格；支持表格 / 看板 / Excel / 日历 / 甘特 多种视图
- **丰富控件类型** — 文本、数字、富文本、选择、日期、文件/图片、关联、引用、自增序列、自动时间戳、复合文本等
- **菜单管理** — 可视化树形编辑，3 级嵌套（工作空间 → 项目 → 数据页），基于角色的可见性控制
- **数据关联** — 多对多双向关联（relation）、一对多引用（reference）、单向引用选择（quoteSelect），自动同步
- **自动字段** — autoTimestamp / autoSequence / compositeText，新增编辑时自动填充
- **行内复制新增** — 操作栏「复制」按钮，预填充整行数据（跳过自增/时间戳字段），人工检视后入库

### 数据流转
- **导入导出** — Excel 模板导入、Excel 全量导出、自定义 Python 脚本导出（json/csv/xml/txt/html）；脚本支持页面级与菜单级两种维度，也可**在 AI 对话中直接导出**
- **导出脚本调试** — 内置「测试」面板可选真实数据页执行；另提供 `scripts/debug_export_script.py` 本地调试工具（拉真实数据、完整 traceback）
- **数据校验** — Python 校验脚本绑定页面，新增/编辑时自动执行，支持关联数据校验
- **ETL 数据管道** — 可视化步骤编排：HTTP 抽取、脚本转换、字段映射、条件过滤、写入集合
- **Open API** — API Key 认证，外部系统按集合读写数据
- **Webhook 规则** — `create`/`update`/`delete`/`merge` 事件触发，条件过滤 + HMAC-SHA256 签名 + 重试

### 版本与协作
- **项目版本管理** — 项目级分支/快照，创建、合并、锁定，跨分支数据隔离
- **跨项目依赖** — 三种依赖类型（track-main / read-write / read-only），依赖校验与通知机制
- **操作审计** — 全量操作日志，批次聚合、筛选、导出
- **系统备份** — 手动/定时备份，下载、还原、跨环境迁移；当前数据与备份的逐字段差异对比；AI 向量库（长期记忆）随备份

### 流程编排（工作流）
- **跨页工作流引擎** — 把多个数据页串成一条命名业务流程：不同**角色**在不同**阶段**处理；记录推进时**自动在下一页生成下游记录**（按字段映射携带上游数据并反向关联），支持**驳回**回退
- **图形化流程设计器** — 全屏画布**拖拽创建节点、拉连线定义流向（DAG）**、拖动布局；点节点编阶段、点连线编流向
- **条件路由** — 推进边可配**单条件**（`字段 op 值`，运算符 `== != > >= < <= contains`，留空=默认边）：命中条件的边优先，否则走默认边
- **并行多分支（v2）** — 一个阶段可**并行扇出**到多个分支（命中的多条条件边/多条默认边全取），各分支独立推进，**所有分支结束**实例才完成
- **待办收件箱** — 按「当前阶段办理角色 ∋ 我」聚合运行中实例的待办；每个并发活动分支一条
- **状态机驱动** — 推进/驳回复用字段的 `workflowConfig` 状态机；推进失败即回滚整笔事务，杜绝「状态已改、实例未推进」

### 首页与仪表盘
- **可配置首页区块** — 欢迎卡片、统计概览、快捷入口、Markdown、数据卡片、**快速录入表单**（点击弹出表单直接入库到关联数据页）
- **仪表盘** — 可视化图表配置

## 快速开始

### 环境要求

- Node.js >= 18
- Python >= 3.9
- PostgreSQL >= 13
- （使用 AI 功能时）[OpenCode](https://opencode.ai) 运行时

### 安装依赖

```bash
# 前端
npm install

# 后端
pip install flask flask-cors psycopg2-binary PyJWT pytest apscheduler

# （AI MCP Server 依赖，在其独立虚拟环境中安装）
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
npm run mcp      # AI MCP Server（端口 3003，AI 功能必需）
```

> 使用 AI 对话/批任务/定时任务前，还需另行启动 OpenCode：`opencode serve --port 4096`。

访问 `http://localhost:5173`，使用 **admin / admin123** 登录。

## 测试

```bash
npm run test              # 前端单元测试（Vitest）
npm run test:server       # 后端测试（Pytest）
npm run test:server:fast  # 后端测试（pytest-xdist 并行档，勿与 dev 后端同跑）
npm run test:all          # 前后端全部
npm run test:e2e          # E2E（Playwright，需服务运行）
npx playwright test e2e/ai-full/   # 全量 AI 能力回归套件（真实 OpenCode 链路）
```

AI 全量测试的方案、用例与执行报告见 [`docs/ai-testing/`](docs/ai-testing/README.md)。

## 项目结构

```
check-manage/
├── server/                      # Flask 后端
│   ├── app.py                   # 应用入口（注册 50 个蓝图 + 后台调度器）
│   ├── config.py / .env         # 配置
│   ├── init_db.py               # 数据库初始化
│   ├── routes/                  # 路由模块（auth/menus/dynamic/ai_chat/open_api_*/...）
│   └── utils/                   # 工具（db 连接池、JWT、脚本沙箱、批任务/扫描引擎、执行审计等）
├── mcp-server/                  # 独立 MCP Server（FastAPI + MCP Streamable-HTTP，12 内置工具）
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
├── e2e/                         # Playwright E2E 用例（ai-full/ 为全量 AI 回归套件）
├── docs/                        # 设计文档、使用手册、规格与计划、AI 全量测试归档
└── package.json
```

## 架构要点

- **单表动态数据**：所有业务数据存于 `dynamic_data` 表的 JSONB 列，按 `collection`（由 `pageId` 推导）区分实体，新增实体/字段无需迁移。
- **AI 经 MCP 理解动态页面**：MCP 工具按菜单角色鉴权读取 PageConfig 与数据，AI 无需为每个新页面做任何适配即可查询/导出/回写。
- **动态路由**：`src/router/dynamicRoutes.ts` 运行时读取 `menus` 表生成 Vue Router 路由，统一指向 `DynamicPage.vue`。
- **字段驱动行为**：`controlType` 决定渲染组件与逻辑（如 `relation` 触发 M:N 处理、`reference` 触发字段继承）。
- **API 代理**：Vite 将 `/api` 代理到后端 3002 并去掉 `/api` 前缀，故后端路由不带 `/api`；生产入口 `server/proxy.py`（:8080）对 SSE/请求体上限有同源双门。
- **后台调度器**：备份调度、跨项目依赖巡检、批任务 Worker、AI 扫描调度均在 `app.py` 启动（`WERKZEUG_RUN_MAIN` 守卫避免重载双启）。

## 用户角色

数据驱动、可自定义的 RBAC：

- **admin** — 永久超级管理员，全部权限，不可删除
- **developer** — 读写数据，无管理功能
- **guest** — 只读

权限分三个粒度：管理功能开关（`admin.*` 能力键）、数据页 CRUD、菜单可见性。后端为权威（`@require_permission` / `@write_required`），前端仅做 UX 门禁；AI 的工具调用同样按调用者角色鉴权（MCP 分发层白名单 + 只读角色限制）。

## 数据库主要表

| 表名 | 说明 |
|------|------|
| menus / page_configs / dynamic_data | 菜单树 / 页面配置 / 业务数据（核心三表） |
| data_relations | 多对多关联关系 |
| users / roles / role_permissions | 用户账号、角色与权限 |
| project_versions / project_dependencies / project_dependency_relations | 项目分支、跨项目依赖 |
| webhook_rules / webhook_logs | Webhook 规则与日志 |
| export_scripts / validation_scripts | 导出脚本、校验脚本 |
| etl_tasks / etl_logs | ETL 任务与日志 |
| api_keys / operation_logs | Open API 密钥、操作审计 |
| backups / backup_settings | 备份记录、定时备份配置 |
| workflow_definitions / workflow_instances | 工作流定义（阶段 + 流向边 + 条件）、运行实例（并发活动分支 + 轨迹） |
| notifications / record_comments / trigger_rules | 通知、记录评论、联动规则 |
| dashboards / home_widgets / system_config | 仪表盘、首页区块、系统配置 |
| ai_settings / ai_chat_sessions / ai_chat_messages / ai_chat_subtasks | AI 配置、对话会话/消息、子代理轨迹 |
| ai_chat_batches / ai_chat_prompt_templates / ai_scan_tasks | AI 批任务、Prompt 模板、定时扫描任务 |
| ai_execution_* / ai_skill_invocations | AI 执行审计（尝试/事件/Prompt 快照/契约/诊断）、Skill 调用采集 |
| global_skills / mcp_servers | 全局技能库、外部 MCP 服务注册 |
| column_views | 数据页列视图配置 |

## 构建部署

```bash
# 构建前端（含 vue-tsc 类型检查）
npm run build      # 产出 dist/

# 一键构建并启动（前端 + 后端 + 反向代理，默认 8080）
npm run start
```

生产部署：前端静态文件交由 Nginx 等服务，后端以 `python app.py` 或 gunicorn 运行；启用 AI 功能时另起 MCP Server 与 OpenCode（详见 `docs/design/OpenCode运行时依赖与部署.md`）。

## 文档

### AI 能力
- `docs/user-guide/ai/assistant.md` — AI 对话助手使用指南
- `docs/user-guide/ai/batch-tasks.md` — AI 批任务指南
- `docs/user-guide/ai/scan-tasks.md` — 定时 AI 数据流水线指南
- `docs/user-guide/ai/long-term-memory.md` — 长期记忆指南
- `docs/user-guide/ai/execution-audit.md` / `trace-analysis.md` / `session-groups.md` — 执行审计、轨迹分析、会话治理
- `docs/user-guide/integration/ai-architecture.md` — AI 总体架构
- `docs/user-guide/integration/ai-batch-api.md` / `ai-session-api.md` + `ai-api-reference.html` — 对外 AI API

### 平台与工程
- `docs/user-guide/` — 用户操作手册（数据查询、脚本上传、工作流等）
- `docs/user-guide/data/workflow.md` — 跨页工作流引擎使用指南（图形化 DAG 设计器、条件路由、并行分支）
- `docs/design/` — 系统设计、架构与数据库结构
- `docs/ai-testing/` — AI 能力全量测试方案、用例与执行报告
- `docs/superpowers/specs/` 与 `docs/superpowers/plans/` — 各特性的设计规格与实施计划

## License

MIT
