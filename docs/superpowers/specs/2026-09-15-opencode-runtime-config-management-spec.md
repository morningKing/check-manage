# OpenCode Skill/Agent 在线配置治理开发 Spec

**版本**：v1.0  
**状态**：待评审  
**日期**：2026-09-15  
**范围**：OpenCode 全局 `skill/` 与 `agent/` 配置线上化管理  
**本阶段**：仅定义开发规格，不实施代码修改

---

## 1. 背景

当前系统已经存在 OpenCode 全局配置管理能力，相关配置位于：

```text
OPENCODE_GLOBAL_DIR/skill/<name>/SKILL.md
OPENCODE_GLOBAL_DIR/agent/<name>.md
```

现有管理接口可以对部分文件进行查看和修改，但尚未形成完整的线上化配置闭环：

- OpenCode serve 启动后不会可靠地自动加载文件变更。
- 保存成功不等于运行时已经生效。
- `OPENCODE_RESTART_POLICY=auto` 尚未形成完整的自动应用流程。
- 当前 OpenCode 运行实例由多个会话和任务共享，重启可能影响全部用户。
- 没有完整的配置版本、并发冲突检测、发布、回滚和生效确认机制。
- Skill、Agent、MCP 和运行时重启共用较粗粒度权限。
- 自定义 `OPENCODE_GLOBAL_DIR` 与 OpenCode 实际读取目录可能不一致。

本 Spec 只针对 OpenCode 运行时全局配置。它与已有的“平台级 Skill 库”不是同一能力：

| 能力 | 现有位置 | 作用 |
|---|---|---|
| 平台级 Skill 库 | `global-skills/`、`global_skills` | 新建会话时注入 Skill |
| OpenCode 全局运行时配置 | `OPENCODE_GLOBAL_DIR/skill`、`OPENCODE_GLOBAL_DIR/agent` | 由 OpenCode serve 在运行时加载 Skill/Agent |

两者必须在产品界面、存储模型和生效语义上明确区分。

## 2. 建设目标

实现以下完整闭环：

```text
编辑 → 校验 → 生成版本 → 发布 → 应用到运行时 → 验证 → 审计/回滚
```

系统必须能够明确区分：

- 文件已保存。
- 配置已通过校验。
- 配置已发布。
- 配置等待生效。
- 配置正在应用。
- 配置已被运行时加载。
- 配置应用失败。

## 3. 范围

### 3.1 本期范围

- 全局 Skill 创建、查看、编辑、删除。
- 全局 Agent 创建、查看、编辑、删除。
- Skill 附属文件管理。
- Skill/Agent 启用和禁用。
- 配置草稿、发布、版本和回滚。
- OpenCode 运行时受控重启。
- 配置应用状态查询。
- 权限拆分。
- 操作审计。
- 路径、文件和压缩包安全校验。

### 3.2 非本期范围

- 修改 OpenCode 内核以实现真正的进程内热加载。
- 用户级独立 Skill/Agent 配置。
- 多租户配置隔离。
- OpenCode 多实例滚动发布。
- MCP 工具功能重构。
- AI Agent 业务能力调整。

## 4. 现有实现约束

实现前需要以以下文件为现状基线进行兼容性评估：

- `server/routes/ai_opencode_admin.py`
- `server/utils/opencode_global.py`
- `server/utils/opencode_launch.py`
- `server/utils/mcp_servers.py`
- `server/routes/ai_chat.py`
- `server/utils/workspace.py`
- `server/proxy.py`
- `docs/superpowers/specs/2026-08-15-global-skill-management.md`

不得把平台级 Skill 的会话注入机制误认为 OpenCode 全局配置热加载机制。平台级 Skill 的复制或符号链接更新，不代表 OpenCode 已重新解析全局 Agent 或全局 Skill 配置。

## 5. 目标架构

### 5.1 配置控制面

配置控制面负责：

- 配置编辑。
- 版本创建。
- 格式校验。
- 差异查看。
- 发布。
- 回滚。
- 审计。

建议在现有 `ai_opencode_admin` 路由和 `opencode_global` 工具之上增加统一的 Runtime Configuration 服务，不再让管理路由直接承担文件写入、发布和进程控制的全部职责。

### 5.2 配置版本仓库

建议使用受控配置目录：

```text
runtime-config/
  revisions/<revision_id>/
    skill/
    agent/
  active/
```

约束如下：

- `revisions/<revision_id>` 是不可变版本。
- `active` 只存放当前运行版本的物化结果。
- 管理员编辑内容先进入草稿版本。
- OpenCode 运行时只读取已发布并物化的版本。
- 回滚通过重新发布旧版本实现，不直接破坏历史版本。

### 5.3 运行时控制器

Runtime Manager 负责：

- 检查 OpenCode 进程状态。
- 判断进程是否由平台托管。
- 执行 drain。
- 启动、停止和重启 OpenCode。
- 校验运行时实际加载的配置版本。
- 应用失败时恢复上一可用版本。

运行时控制器必须与配置编辑接口解耦，避免普通保存操作直接触发不可控的进程重启。

## 6. 配置生命周期

### 6.1 状态定义

| 状态 | 说明 |
|---|---|
| `draft` | 编辑中的草稿 |
| `validated` | 已通过静态校验 |
| `published` | 已发布但尚未应用 |
| `pending_apply` | 等待运行时应用 |
| `applying` | 正在应用 |
| `active` | 当前运行时已加载 |
| `failed` | 应用失败 |
| `rolled_back` | 已回滚 |

### 6.2 状态流转

```text
draft
  → validated
  → published
  → pending_apply
  → applying
  → active
```

异常流转：

```text
validated → failed
applying → failed
failed → rolled_back
active → draft
```

### 6.3 发布规则

发布前必须完成：

- Skill 目录结构校验。
- `SKILL.md` 存在性校验。
- Agent 文件格式校验。
- 文件名和资源名校验。
- 路径穿越校验。
- 软链接校验。
- 文件大小校验。
- 附属文件引用校验。
- 资源名称冲突校验。

## 7. 数据模型

以下为建议模型，最终字段需结合现有数据库迁移规范确定。

### 7.1 配置资源表

建议表名：`ai_runtime_resources`

主要字段：

- `id`
- `resource_type`
- `resource_name`
- `enabled`
- `current_revision_id`
- `published_revision_id`
- `created_by`
- `updated_by`
- `created_at`
- `updated_at`

`resource_type` 取值：

- `skill`
- `agent`

### 7.2 配置版本表

建议表名：`ai_runtime_revisions`

主要字段：

- `id`
- `resource_id`
- `revision`
- `content_hash`
- `content_manifest`
- `status`
- `created_by`
- `published_by`
- `created_at`
- `published_at`
- `validation_result`
- `failure_reason`

### 7.3 运行时状态表

建议表名：`ai_runtime_state`

主要字段：

- `runtime_id`
- `process_id`
- `process_owner`
- `config_generation`
- `runtime_generation`
- `status`
- `last_health_check_at`
- `last_apply_error`
- `started_at`
- `updated_at`

### 7.4 审计记录

可以复用 `operation_logs`，但必须补充配置专用字段；如现有表不适合，增加独立表 `ai_runtime_audit_logs`。

至少记录：

- 操作者。
- 操作类型。
- 资源类型。
- 资源名称。
- 原版本。
- 新版本。
- 原内容摘要。
- 新内容摘要。
- 发布结果。
- 应用结果。
- 回滚结果。
- 失败原因。
- 请求来源。
- 操作时间。

## 8. 并发控制

所有编辑接口必须支持乐观锁。

请求必须携带：

- `revision`
- `contentHash`

处理规则：

- 版本一致：允许保存。
- 版本不一致：返回 `409 VERSION_CONFLICT`。
- 不允许后写覆盖前写。
- 前端展示当前版本与用户版本的差异。

## 9. API 规格

以下接口均需要 JWT 和后端权限校验。

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/ai/opencode/runtime` | 获取运行时总状态 |
| `GET` | `/ai/opencode/resources` | 获取 Skill/Agent 列表 |
| `GET` | `/ai/opencode/resources/<type>/<name>` | 获取资源详情 |
| `POST` | `/ai/opencode/resources` | 创建资源 |
| `PUT` | `/ai/opencode/resources/<type>/<name>` | 保存草稿 |
| `POST` | `/ai/opencode/resources/<type>/<name>/validate` | 校验草稿 |
| `POST` | `/ai/opencode/resources/<type>/<name>/publish` | 发布版本 |
| `GET` | `/ai/opencode/resources/<type>/<name>/revisions` | 查看历史版本 |
| `GET` | `/ai/opencode/revisions/<id>/diff` | 查看版本差异 |
| `POST` | `/ai/opencode/revisions/<id>/rollback` | 回滚到指定版本 |
| `POST` | `/ai/opencode/runtime/apply` | 应用指定配置版本 |
| `POST` | `/ai/opencode/runtime/restart` | 受控重启运行时 |

接口返回结果必须包含：

- 配置版本。
- 内容摘要。
- 配置状态。
- `configGeneration`。
- `runtimeGeneration`。
- 是否已生效。
- 失败原因。
- 当前运行时状态。

## 10. 运行时应用策略

### 10.1 手动模式

保存和发布后：

- 配置进入 `pending_apply`。
- 管理员手动执行“应用”。
- 不自动重启 OpenCode。

### 10.2 自动模式

发布后执行：

1. 配置进入 `pending_apply`。
2. 运行时进入 `draining`。
3. 停止新会话和新任务认领。
4. 等待当前任务完成。
5. 重启 OpenCode。
6. 健康检查新配置。
7. 成功后标记 `active`。
8. 失败时恢复旧版本。

### 10.3 强制模式

仅允许具备高危权限的管理员执行：

- 中断当前任务。
- 将相关会话标记为 `interrupted_by_runtime_restart`。
- 立即切换配置。
- 记录完整审计。

默认禁止强制模式。

## 11. OpenCode 进程治理

必须明确采用以下一种运行模式。

### 11.1 平台托管模式

系统负责：

- 启动 OpenCode。
- 保存 PID。
- 保存 ownership 标识。
- 健康检查。
- 受控停止和重启。

### 11.2 外部托管模式

系统只负责：

- 检查 OpenCode 是否可用。
- 发布配置。
- 标记配置等待外部运行时加载。

外部托管模式下不得根据端口直接杀进程。

## 12. 权限设计

建议拆分现有 `admin.ai_settings`：

| 权限 | 用途 |
|---|---|
| `admin.ai_runtime_read` | 查看配置和运行时 |
| `admin.ai_skill_write` | 修改 Skill |
| `admin.ai_agent_write` | 修改 Agent |
| `admin.ai_runtime_publish` | 发布配置 |
| `admin.ai_runtime_apply` | 应用配置 |
| `admin.ai_runtime_rollback` | 回滚配置 |
| `admin.ai_runtime_restart` | 重启运行时 |
| `admin.ai_runtime_force` | 强制中断并重启 |

权限校验必须由后端完成，前端权限仅用于界面控制。

## 13. 安全要求

必须满足：

- 所有路径经过 `realpath` 校验。
- 禁止软链接越界。
- 禁止绝对路径和 `..`。
- 限制单文件和总目录大小。
- 限制 Skill 附件数量。
- ZIP 解压限制总大小和压缩比。
- 禁止写入受控目录以外的位置。
- 资源名称使用白名单。
- 新建、修改、删除和发布均记录审计。
- 敏感内容只保存摘要或加密存储。
- 备份恢复不得直接覆盖运行中的配置。
- MCP Token 不应作为长期配置内容暴露给 Skill/Agent。

## 14. 前端要求

管理页面需要展示：

- 草稿和发布状态。
- 当前版本与运行版本。
- 配置差异。
- 发布确认。
- 应用进度。
- 失败原因。
- 历史版本。
- 回滚操作。
- 运行时健康状态。
- 当前活跃任务数量。
- 是否允许安全重启。
- 强制重启二次确认。

界面必须明确区分：

```text
保存成功 ≠ 发布成功 ≠ 运行时生效
```

## 15. 测试要求

### 15.1 单元测试

覆盖：

- 路径穿越。
- 软链接越界。
- 非法资源名。
- 文件大小限制。
- Skill 结构校验。
- Agent 格式校验。
- 版本冲突。
- 发布状态流转。
- 回滚状态流转。
- 进程 ownership 校验。

### 15.2 后端集成测试

覆盖：

- 创建、修改和删除 Skill。
- 创建、修改和删除 Agent。
- 发布配置。
- 应用配置。
- 应用失败自动回滚。
- 并发编辑返回 `409`。
- 无权限用户无法操作。
- 外部 OpenCode 进程不会被误杀。

### 15.3 运行时测试

覆盖：

- 新配置确实被 OpenCode 加载。
- 运行时版本与配置版本一致。
- 安全重启期间不接收新任务。
- 强制重启会记录中断状态。
- OpenCode 启动失败时恢复旧版本。
- 已有会话和批任务行为符合预期。

### 15.4 前端测试

必须通过浏览器验证：

- 在线编辑。
- 发布。
- 应用状态刷新。
- 版本差异。
- 回滚。
- 冲突提示。
- 权限控制。
- 重启确认。

## 16. 实施阶段

### P0：安全和运行时一致性

- 明确真实配置目录。
- 显式传递 `OPENCODE_GLOBAL_DIR`。
- 拆分权限。
- 增加路径和文件安全校验。
- 增加运行时版本状态。
- 修正进程 ownership。

### P1：版本化配置管理

- 增加草稿、版本和发布机制。
- 增加乐观锁。
- 增加差异查看。
- 增加历史版本和回滚。
- 增加完整审计。

### P2：受控运行时应用

- 增加 drain。
- 增加安全重启。
- 增加失败自动恢复。
- 完善批任务和交互会话中断处理。

### P3：多实例演进

- OpenCode 实例池。
- 配置灰度发布。
- 滚动更新。
- 按项目或租户使用不同配置版本。

## 17. 验收标准

1. Skill 和 Agent 可以在线编辑。
2. 草稿不会影响当前运行时。
3. 发布后可以查看明确的生效状态。
4. 配置版本和运行时版本可追溯。
5. 并发编辑不会互相覆盖。
6. 可以查看差异和历史版本。
7. 可以回滚到任意可用版本。
8. OpenCode 重启不会误杀外部进程。
9. 运行中的任务不会被默认强制中断。
10. 应用失败会自动恢复旧配置。
11. 所有配置变更和运行时操作均有审计。
12. Skill、Agent、MCP 和运行时重启权限彼此隔离。
13. 路径穿越、软链接和压缩炸弹无法突破受控目录。
14. 前端能够真实展示“已保存、已发布、已生效”的区别。

## 18. 相关文档

- `docs/superpowers/specs/2026-08-15-global-skill-management.md`
- `docs/superpowers/specs/2026-05-26-ai-agent-frontend-design.md`
- `docs/superpowers/specs/2026-05-30-ai-chat-session-skill-upload-design.md`
