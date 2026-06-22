# 设计文档索引

本目录按 [`功能架构图.svg`](./功能架构图.svg) 的 **9 个业务域**组织系统设计文档，并补充 1 篇**横切关注点**文档（数据并发控制）。总览见 [`功能架构说明.md`](./功能架构说明.md)。

## 架构总览

| 文档 | 说明 |
|------|------|
| [功能架构图.svg](./功能架构图.svg) | UML 组件视图（前端 SPA / 9 业务域 / 引擎调度 / 数据存储 / AI 子系统） |
| [功能架构说明.md](./功能架构说明.md) | 分层与跨域数据流、依赖关系、外部集成点总览 |

## 业务域设计文档

| # | 域 | 主要模块 | 文档 |
|---|----|----------|------|
| ① | 认证与权限 | auth · users · roles + JWT/RBAC | [01-认证与权限.md](./01-认证与权限.md) |
| ② | 配置中心 | menus · page_configs · column_views · system_config · home_widgets | [02-配置中心.md](./02-配置中心.md) |
| ③ | 动态数据核心 | dynamic · relations · query · comments · timeline（含乐观锁） | [03-动态数据核心.md](./03-动态数据核心.md) |
| ④ | 脚本扩展 | export_scripts · validation_scripts + 沙箱 | [04-脚本扩展.md](./04-脚本扩展.md) |
| ⑤ | 数据流转 | etl_tasks · menu_export · data_files | [05-数据流转.md](./05-数据流转.md) |
| ⑥ | 开放集成 | api_keys · open_api · webhook · trigger_rules | [06-开放集成.md](./06-开放集成.md) |
| ⑦ | 版本协作 | project_versions · cross_project_deps | [07-版本协作.md](./07-版本协作.md) |
| ⑧ | 运维监控 | operation_logs · backups · notifications · dashboards | [08-运维监控.md](./08-运维监控.md) |
| ⑨ | AI 智能助手 | ai_chat · batches · prompt_templates · ai_scan_tasks · 长期记忆 mem0 + OpenCode/MCP | [09-AI智能助手.md](./09-AI智能助手.md) |

## 横切关注点

| 文档 | 说明 |
|------|------|
| [10-数据并发控制.md](./10-数据并发控制.md) | 跨所有写入路径的并发一致性权威设计：乐观锁、序号原子分配、手填主键 advisory 锁、序号计数器全局不变式、工作流推进并发与分支隔离、关系一致性、分支锁定、触发器/Webhook 最终一致。配 4 张 SVG 场景图（`assets/concurrency-*.svg`）。 |

> 跨页**工作流引擎**（`workflow_definitions` / `workflow_instances` / 收件箱 / 设计器）是横切③数据写入 + ⑧通知的编排能力：状态字段转换驱动阶段推进、原子分配下游记录、分支隔离与「推进失败即回滚」一致性。并发与一致性见 [10-数据并发控制.md](./10-数据并发控制.md) §7，使用说明见 [工作流引擎使用指南](../user-guide/data/workflow.md)。

## 文档结构约定

每篇业务域文档遵循统一模板：

1. **模块职责** — 一句话定位
2. **模块组成** — 蓝图 / 前端 / 引擎，与架构图对应
3. **数据模型** — 相关数据库表
4. **核心流程** — 关键时序与逻辑
5. **关键接口** — API 端点与鉴权
6. **依赖与协作关系** — 上下游域
7. **设计决策** — 选择与理由

## 历史文档

旧版设计文档（按主题而非业务域组织）已迁移至 [`archive/`](./archive/)，其中有价值的设计内容已吸收进对应的业务域文档：

每篇归档文档顶部均带「已归档」横幅，指向其当前权威文档：

| 旧文档（已重命名为英文 slug） | 内容去向 |
|--------|----------|
| [system-design.md](./archive/system-design.md) | 拆分进各业务域（README + 01~10） |
| [concurrency-control-design.md](./archive/concurrency-control-design.md) | → ⑩ 数据并发控制（已被完全取代，仅存重定向 + 历史背景） |
| [project-management-design.md](./archive/project-management-design.md) | → ③看板/工作流（+ 跨页工作流引擎）· ⑥联动 · ⑧通知/仪表盘 |
| [cross-project-dependency-design.md](./archive/cross-project-dependency-design.md) | → ⑦ 版本协作 |
| [ai-chat-design.md](./archive/ai-chat-design.md) | → ⑨ AI 智能助手（已更新多 agent/批任务/定时） |
| [inspection-skill-design.md](./archive/inspection-skill-design.md) | AI 应用示例（保留备查） |
| [advantages-analysis.md](./archive/advantages-analysis.md) | 非设计文档（保留备查） |
