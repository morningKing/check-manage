# 设计文档索引

本目录按 [`功能架构图.svg`](./功能架构图.svg) 的 **9 个业务域**组织系统设计文档。总览见 [`功能架构说明.md`](./功能架构说明.md)。

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
| ⑨ | AI 智能助手 | ai_chat · batches · prompt_templates · ai_scan_tasks + OpenCode/MCP | [09-AI智能助手.md](./09-AI智能助手.md) |

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

| 旧文档 | 内容去向 |
|--------|----------|
| 系统设计文档.md | 拆分进各业务域 |
| 数据并发控制设计文档.md | → ③ 动态数据核心（乐观锁详解） |
| 项目管理功能设计文档.md | → ②看板/工作流 · ⑥联动 · ⑧通知/仪表盘 |
| 跨项目依赖设计文档.md | → ⑦ 版本协作 |
| ai-chat-功能说明与方案设计.md | → ⑨ AI 智能助手（已更新多 agent/批任务/定时） |
| 巡检用例开发 Skill 设计文档.md | AI 应用示例（保留备查） |
| 优势分析报告.md | 非设计文档（保留备查） |
