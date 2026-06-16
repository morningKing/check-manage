# Check-Manage 文档中心

本目录包含 Check-Manage 系统的完整技术文档，按类型分类组织。

## 目录结构

### 📘 [user-guide/](./user-guide/README.md) — 用户使用手册
面向最终用户的功能使用文档，按功能域分子目录组织（英文 slug 文件名，便于国际化）：

- `getting-started/` — 快速入门总览
- `data/` — 数据页视图、关系、查询、仪表盘、工作流
- `admin/` — 角色权限、备份、通知、列视图、脚本
- `integration/` — Open API、Webhook、ETL、触发规则、跨项目依赖、数据迁移
- `ai/` — AI 助手、批任务、定时扫描

完整目录见 [user-guide/README.md](./user-guide/README.md)。

### 📕 [design/](./design/README.md) — 设计文档
面向开发人员的技术设计文档，按 [功能架构图](./design/功能架构图.svg) 的 9 个业务域组织（01~10 + 架构总览），历史设计文档归档于 [`design/archive/`](./design/archive/)。

### 📙 [superpowers/](./superpowers/) — 开发流程文档
使用 Superpowers 流程产出的规范文档：`plans/`（实现计划）、`specs/`（设计规范）。

## 快速导航

### 新用户入门
1. 快速入门：[user-guide/getting-started/overview.md](./user-guide/getting-started/overview.md)
2. 了解数据关系：[user-guide/data/relations.md](./user-guide/data/relations.md)
3. 学习查询语法：[user-guide/data/query-syntax.md](./user-guide/data/query-syntax.md)
4. 全量功能参考：[user-guide/reference.md](./user-guide/reference.md)

### 开发人员
1. 架构总览：[design/功能架构说明.md](./design/功能架构说明.md)
2. 并发控制：[design/10-数据并发控制.md](./design/10-数据并发控制.md)
3. 业务域设计：[design/README.md](./design/README.md)（① 认证 ~ ⑨ AI）
4. Open API：[user-guide/integration/open-api.md](./user-guide/integration/open-api.md)
