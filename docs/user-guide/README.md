# 用户使用手册 · 目录

Check-Manage 用户使用文档，按功能域分子目录组织。文件名采用英文 slug，便于后续国际化（按 `en/`、`zh/` 等语言目录平移）。

> 新用户请从 [快速入门总览](./getting-started/overview.md) 开始。需要完整功能参考见 [reference.md](./reference.md)。

## 🚀 快速入门 · getting-started

| 文档 | 说明 |
|------|------|
| [overview.md](./getting-started/overview.md) | 登录、首页、数据页基本操作、各功能快速入门 |

## 📊 数据功能 · data

| 文档 | 说明 |
|------|------|
| [views.md](./data/views.md) | 表格 / 看板 / 日历 / 甘特 / Excel 视图、评论与变更历史 |
| [relations.md](./data/relations.md) | 关联（M:N）、引用（1:N）、引用选择三种数据关系 |
| [query-console.md](./data/query-console.md) | 数据查询控制台操作说明 |
| [query-syntax.md](./data/query-syntax.md) | MongoDB / SQL 风格查询语法手册 |
| [ai-query.md](./data/ai-query.md) | AI 自然语言查询配置与使用 |
| [dashboards.md](./data/dashboards.md) | 仪表盘配置与各类图表使用 |
| [workflow.md](./data/workflow.md) | 跨页工作流引擎：图形化 DAG 设计器、条件路由、并行分支、推进/驳回、收件箱 |

## 🔐 管理功能 · admin

| 文档 | 说明 |
|------|------|
| [roles-rbac.md](./admin/roles-rbac.md) | 自定义角色与 RBAC 权限（管理功能开关 / 数据页 CRUD / 菜单可见性） |
| [backups.md](./admin/backups.md) | 系统备份：手动 / 定时全量备份、还原、差异对比 |
| [notifications.md](./admin/notifications.md) | 通知中心：依赖告警、工作流待办等系统通知 |
| [column-views.md](./admin/column-views.md) | 列视图：每页保存列的显隐、顺序、宽度与筛选 |
| [scripts.md](./admin/scripts.md) | 导出 / 校验脚本上传、编辑与断点调试 |

## 🔌 集成与扩展 · integration

| 文档 | 说明 |
|------|------|
| [open-api.md](./integration/open-api.md) | 外部系统经 API Key 按集合读写数据 |
| [webhooks.md](./integration/webhooks.md) | Webhook 规则：事件触发 + HMAC 签名 + 重试 |
| [etl.md](./integration/etl.md) | ETL 任务：抽取 / 转换 / 字段映射 / 过滤 / 写入 |
| [trigger-rules.md](./integration/trigger-rules.md) | 触发规则：写入事件驱动的数据联动 |
| [cross-project-deps.md](./integration/cross-project-deps.md) | 跨项目依赖：track-main / read-write / read-only |
| [data-migration.md](./integration/data-migration.md) | 数据库幂等迁移指南 |

## 🤖 AI 能力 · ai

| 文档 | 说明 |
|------|------|
| [assistant.md](./ai/assistant.md) | AI 助手对话：@子智能体、模型选择、工具调用 |
| [batch-tasks.md](./ai/batch-tasks.md) | AI 批任务：N 文件 + 1 Prompt 并发处理 |
| [scan-tasks.md](./ai/scan-tasks.md) | 定时 AI 数据流水线：扫描数据页 → AI 处理 → 结构化回写 |

## 📚 参考

| 文档 | 说明 |
|------|------|
| [reference.md](./reference.md) | 全量功能参考文档（综合性，含历史细节） |

---

> 📸 截图存放于 [`_images/`](./_images/)。
> 📌 维护约定：**每次用户可见功能变更都需同步更新对应文档**（见根目录 `CLAUDE.md` 的 Documentation Sync 规范）。
