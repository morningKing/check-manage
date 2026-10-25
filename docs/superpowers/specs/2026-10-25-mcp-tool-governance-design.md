# 内置 MCP 工具治理（Tool Governance）设计

> 状态：待用户评审
> 日期：2026-10-25
> 前置讨论：brainstorming 会话确认四个决策（见「决策记录」）

## 1. 背景与问题

平台内置 MCP 服务器（`mcp-server/`，FastAPI，Streamable-HTTP `/mcp`，token 鉴权）目前以单一名称 `check-manage` 注入每个 AI 会话工作区的 `opencode.json`，对外呈现为一个黑盒：**27 个工具**（25 个模块，`memory` 模块贡献 3 个）平铺在一个命名空间里，横跨 7 个不相关的域（数据查询/数据写入/长期记忆/轨迹分析/批任务观测/图谱/通用执行）。

「乱」与「没法管理」的具体证据：

1. **无分组无元数据**——工具前缀只是命名习惯（`memory_`、`data_`、`batch_children_`、`graph_`），没有声明式的分组、读写属性；注册是 `mcp-server/tools/__init__.py:20` 的硬编码 dict。
2. **权限只有一档**——`mcp-server/rbac.py:21` 唯一的细粒度控制是 kefu-guest 的 3 工具白名单（代码写死）；**guest 角色现状全放行 27 个工具**（含 `run_python`、`data_delete_record`），与角色表里「访客=只读」的语义不符（治理缺口，本设计顺带收紧）。
3. **管理面只有总闸**——后台 `McpServersCard.vue` 只能整体开关内置 MCP + 管理外部 MCP；27 个工具没有清单页、没有逐工具开关、没有分组展示（`/tools` 端点存在但 UI 未用）。
4. **会话侧黑盒**——`McpServicesBlock.vue` 把 27 个工具名一字排开，无分类。
5. **描述质量参差**——`memory_*` 有完整中文参数说明，`ai_create_data_page` 的 description 是残缺的参数拼接。
6. **名称硬编码四处**——注入名 `check-manage` 在 `server/routes/ai_chat.py:81`、`server/utils/kefu_repo.py:23`、`server/utils/batch_engine.py:352` 各写一遍，MCP 协议内服务器名又是 `check-manage-mcp`（`mcp-server/main.py:22`）。

## 2. 决策记录（用户已确认）

| # | 决策 | 选择 |
|---|------|------|
| D1 | 痛点定位 | **工具级治理+可见性**——不拆服务器，`check-manage` 单一注入名保持（兼容存量会话），黑盒靠分组展示与治理打开 |
| D2 | 控制粒度 | **角色×工具矩阵**——4 内置角色 × 27 工具可配置，替代写死的 kefu 白名单（白名单降级为兜底常量） |
| D3 | 生效时机 | **即时生效**——`call_tool` 每调用校验（禁用即刻拒绝），`list_tools` 过滤（新会话/重连生效）；传播窗口 ≤ TTL 3s |
| D4 | 架构归属 | **共享库表 + 双侧分工**——矩阵/快照落共享 PostgreSQL；mcp-server 执法；Flask 管理端点+审计；元数据真相源在 mcp-server 代码 manifest。模式与第一方插件架构（`2026-10-25-first-party-plugin-architecture-design.md`）同构：manifest 单一真相源 + TTL 缓存 + 种子不覆盖管理员选择 |

明确排除：按域拆成多个 MCP server（D1 未选）、会话级工具覆写（粒度问题未选）、配置文件矩阵（不满足 D3）。

## 3. 架构总览

```
                    ┌─ Flask 后端 (3002) ─────────────────┐
 管理员 ──浏览器──▶ │ GET/PUT /ai/mcp-tool-rules          │──▶ ai_mcp_tools（快照，读）
                    │ GET     /ai/mcp-tools               │──▶ ai_mcp_tool_rules（全量替换，写）
                    │ log_operation 审计                  │
                    └──────────────┬──────────────────────┘
                                   │ 共享 PostgreSQL
                    ┌─ mcp-server (3003) ─────────────────┐
 启动时 upsert ────▶│ tools/registry.py（manifest 真相源） │
                    │ rbac.py 矩阵查询（TTL 3s 缓存）      │──▶ ai_mcp_tool_rules（读）
 AI 会话 ──OC─────▶ │ list_tools 按 role 过滤              │
                    │ call_tool 每调用校验 → 即时拒绝      │
                    └─────────────────────────────────────┘
```

分工原则：执法在 mcp-server（即时生效的天然位置）；权限判定、操作日志、管理 UI 全部复用 Flask 现成体系；两进程只通过共享表协作，不新增进程间 HTTP 依赖。

## 4. 数据模型

迁移文件 `server/migrations/2026_10_25c_mcp_tool_governance.py`（避开同日已占的 `2026_10_25_batch_takeover.py` 与 `2026_10_25b_platform_plugins.py`），**双注册**：migrations 文件 + `server/app.py` 显式 boot 块（照抄 batch_takeover 块样式）。

### 4.1 `ai_mcp_tools`（工具元数据快照）

| 列 | 类型 | 说明 |
|----|------|------|
| tool_name | TEXT PK | 工具名（如 `query_collection`） |
| group_id | TEXT NOT NULL | 组 id（见 §5.1 分组草案） |
| group_name | TEXT NOT NULL | 组中文显示名（冗余，避免管理读路径 join） |
| sort_order | INT NOT NULL | 组内排序 |
| description | TEXT NOT NULL DEFAULT '' | 代码内 description 的快照 |
| access | TEXT NOT NULL CHECK (access IN ('read','write')) | 读写属性 |
| updated_at | TIMESTAMPTZ DEFAULT NOW() | |

快照语义：mcp-server 启动时 upsert 全量，并**删除不在当前注册集里的行**（工具改名/下架后不留尾巴）。同步失败仅记 warning 不阻断启动（快照只服务管理展示，不影响执法）。

### 4.2 `ai_mcp_tool_rules`（角色×工具矩阵）

| 列 | 类型 | 说明 |
|----|------|------|
| role | TEXT NOT NULL | `admin` / `developer` / `guest` / `kefu-guest` |
| tool_name | TEXT NOT NULL | |
| allowed | BOOLEAN NOT NULL | |
| updated_at | TIMESTAMPTZ DEFAULT NOW() | |
| | | PRIMARY KEY (role, tool_name) |

全量存储（4×27=108 行），无「只记例外」的隐式语义——UI 就是 4×27 网格，每格显式开/关，行为无歧义。

### 4.3 种子（ON CONFLICT DO NOTHING，永不重置管理员选择）

| 角色 | 种子 |
|------|------|
| admin | 27 工具全开 |
| developer | 27 工具全开（「可读写所有数据」语义） |
| guest | **仅 `access='read'` 的工具开，全部 write 工具关**（收紧！现状 guest 全放行是 fail-open 的意外，见 §8 开放问题 Q1） |
| kefu-guest | 现状白名单平移：`query_collection` / `list_collections` / `read_upload` 三工具开，其余关 |

新工具上线（矩阵无行）的代码默认：**admin/developer 放行，guest/kefu-guest 拒绝**——「新工具默认对受限身份隐藏」写死在 `rbac.py` 默认层。

## 5. mcp-server 侧

### 5.1 `tools/registry.py`（新增，manifest 真相源）

- `GROUPS: dict[str, GroupDef]`——组定义（id / 中文名 / 排序）。分组草案（实施时可微调，27 工具全覆盖）：

| group_id | 中文名 | 工具（27） | access |
|----------|--------|-----------|--------|
| data-query | 数据查询 | query_collection, list_collections, read_upload, read_data_file, export_collection_excel, download_field_files | 全 read |
| data-write | 数据写入 | data_create_records, data_update_record, data_delete_record, data_attach_menu, ai_create_data_page | 全 write |
| memory | 长期记忆 | memory_search (read), memory_add (write), memory_delete (write) | 混合，按工具标 |
| trace | 轨迹与会话分析 | analyze_trace, query_sessions | 全 read |
| batch | 批任务观测 | batch_children_status, batch_child_changes, batch_children_search, batch_tool_audit | 全 read |
| graph | 图谱查询 | graph_neighbors, graph_traverse | 全 read |
| exec | 通用执行与产物 | run_python (write), save_artifact (write), list_export_scripts (read), run_export_script (write), register_action_check (write) | 混合，按工具标 |

- 每工具模块新增模块级 `META = ToolMeta(group=..., access=...)`。
- **注册校验（启动即抛）**：`register_all()` 遍历 `_TOOLS`，任何工具缺 `META`、`META.group` 不在 `GROUPS`、`access` 非法 → `RuntimeError`，拒绝启动（同插件架构 `gate_plugin` 校验模式——新工具忘了标注就不许上线）。
- `sync_metadata_to_db()`：启动时 upsert 快照表 + 清理消失行（§4.1）。

### 5.2 `rbac.py` 升级（执法点）

```python
def tool_allowed(name: str, role: str) -> bool
```

判定顺序：

1. 查矩阵缓存（TTL 3s，照抄 `plugin_registry` 缓存模式）——行命中 → 返回 `allowed`。
2. 行缺失（新工具/行被删）→ 代码默认：`admin`/`developer` → True；`guest`/`kefu-guest` 及未知角色 → False。
3. **DB 读取失败 → 分层回落（用代码 manifest 的 access 属性，保持与种子同语义）**：`kefu-guest` → 代码内 `KEFU_TOOL_ALLOWLIST`（3 工具，fail-closed，公开身份安全底线，现状行为保留）；`guest` → 仅 `access='read'` 的工具（manifest 在进程内，无需 DB）；`admin`/`developer`/未知角色 → 全放行（fail-open，DB 抖动不打瘫核心会话，与 `internal_mcp_enabled` fail-open 理由同构）。

`KEFU_TOOL_ALLOWLIST` 常量保留在 `rbac.py`，语义从「唯一防线」降级为「DB 不可用时 kefu-guest 的兜底」。

### 5.3 `main.py` / `tools/__init__.py`

- `/tools` 端点扩展为返回 `[{name, description, group, group_name, access, sort_order}]`（排障用；Flask 管理读走快照表，不依赖此端点）。
- `list_tools` / `_dispatch_tool` 两条路径继续经 `tool_allowed`（现状结构不变，`tools/__init__.py:67` 已是每调用校验形态）。

## 6. Flask 侧（`server/routes/ai.py`）

| 端点 | 方法 | 行为 | 权限 |
|------|------|------|------|
| `/ai/mcp-tools` | GET | 快照表 join 矩阵，返回 `{groups: [{id, name, order, tools: [{name, description, access, rules: {admin, developer, guest, kefu-guest}}]}]}` | `admin.ai_settings`（与现有 MCP 管理端点同权限，`ai.py:200-285` 惯例） |
| `/ai/mcp-tool-rules` | PUT | 全量替换矩阵（事务内 DELETE + bulk INSERT）；校验 body 的 role ∈ 四内置角色、tool_name ∈ 快照表；`log_operation('update', 'ai_mcp_tool_rules', 'matrix', '<N 项变更: ...>')` 记 diff 摘要 | `admin.ai_settings` |

角色集为代码常量 `('admin', 'developer', 'guest', 'kefu-guest')`（对齐 `init_db.py:1738` 内置角色种子）；动态自建角色不进矩阵，走 §5.2 代码默认（未知角色 → False，比现状的「未知角色全放行」更安全，见 §8 Q2）。

## 7. 前端

### 7.1 管理侧（`McpServersCard.vue` 扩展）

- 内置 MCP 区新增「工具治理」按钮 → `el-drawer`（约 720px）：
  - 按组分区（中文组名标题 + 组描述），每工具一行：工具名（mono）/ 描述 / `read|write` tag / 四角色开关列（表头 admin/开发/访客/客服）。
  - 组级批量开关：每组标题行右侧放 4 个角色微开关（与列对应），点击即切「整组 × 该角色」，再点恢复；单项开关仍可独立微调。
  - 底部「保存」→ 全量 PUT；未保存改动离开抽屉需确认。
- MCP 卡片内置区描述文案补一句「工具级开关见抽屉，改动即时生效（≤3s）」。

### 7.2 会话侧（`McpServicesBlock.vue`）

- check-manage 的工具列表按组分区渲染：组中文标题 + 组内工具行（名称+描述）。
- 数据来源：`GET /ai/chat/sessions/:id/mcp`（`ai_chat.py`）响应中，check-manage 条目的 tools 合并快照表元数据（后端 join，前端不感知表结构）。未知工具（快照缺失，如 mcp-server 未重启先升级了后端）归「其他」组，不隐藏。

## 8. 开放问题（评审时定）

- **Q1 guest 收紧**：种子把 guest 从「全放行 27 工具」收紧为「仅 read 工具（约 16 个）」。若现状有访客身份的 AI 会话在用 write 工具，上线即受影响——按角色定义「访客=只读」这是修 bug 不是破坏；需用户确认无此依赖。
- **Q2 未知角色默认拒绝**：现状未知/自建角色全放行；本设计改为默认拒绝（矩阵没行且不在四内置角色 → False）。更安全，但若有依赖「自建角色也能用全部工具」的场景需先补矩阵行。

## 9. 顺手修复（纳入实施任务）

1. **描述补齐**：27 工具中文描述全部达到 `memory_search` 的质量线（一句话用途 + 参数说明）；`ai_create_data_page` 残缺 description 重写。真相在代码，UI 只读。
2. **`MCP_NAME` 收敛**：`ai_chat.py:81` / `kefu_repo.py:23` / `batch_engine.py:352` 三处硬编码收敛为单一常量（放 `server/utils/mcp_servers.py` 导出，三处 import）。注入名保持 `check-manage` 不变（兼容存量会话的 opencode.json）；`check-manage-mcp` 协议内服务器名不动。

## 10. 错误处理语义汇总

| 场景 | 行为 |
|------|------|
| 工具缺 META / 组不存在 / access 非法 | mcp-server 启动 RuntimeError（拒绝上线） |
| 矩阵行缺失（新工具） | 代码默认：admin/developer 允许，guest/kefu-guest/未知角色拒绝 |
| mcp-server 读矩阵 DB 失败 | kefu-guest → 代码白名单；guest → 仅 read 工具（manifest）；其余 → 全放行（fail-open）；缓存过期后重试 |
| 快照同步失败 | warning 继续，管理页可能显示旧快照，执法不受影响 |
| PUT body 含非法 role/tool | 400 + 指明非法项；事务整体回滚 |
| 存量会话中调用已禁用工具 | `call_tool` 抛 PermissionError（工具列表展示要等重连刷新，但调用即刻被拒——fail-closed on call） |
| 管理端改动传播 | ≤ TTL 3s（mcp-server 缓存自然过期，无进程间通知） |

## 11. 测试策略

- **mcp-server pytest**（`mcp-server/tests/`，真库）：registry 启动校验（缺 META/坏组名/坏 access 三种抛）；`tool_allowed` 语义三分支（行命中 / 行缺失默认 / DB 失败分层回落——monkeypatch get_db 抛错验证 kefu fail-closed 与其余 fail-open）；快照 upsert + 消失行清理。
- **Flask pytest**（`server/tests/`，真库 casemanage_test）：GET 结构（分组+矩阵 join）；PUT 全量替换原子性（含非法 role 400 回滚）；`log_operation` 落账；无权限 403。新测试文件带 `_heal_leaked_get_db_bindings` autouse 夹具（conftest `get_db` 绑定污染坑，照抄 `test_open_api_takeover_link.py`）。
- **vitest**：治理抽屉分组渲染 + 矩阵开关交互 + 组级批量；McpServicesBlock 分组渲染（未知工具归「其他」）。
- **Playwright E2E**（mock 栈）：管理员在抽屉禁用某工具×developer → 新会话 MCP 面板该工具消失；恢复后回归；kefu 会话只见 3 工具（现状行为回归保护）。

## 12. 范围外（本期不做）

- 按域拆分 MCP server / 更改对外注入名（D1 已排除）
- 外部 MCP 服务器的工具可见性/治理（外部 MCP 管理保持现状）
- 会话级/用户级工具覆写
- 工具调用量统计与审计报表（`batch_tool_audit` 已有批任务侧审计，平台级用量分析另立主题）
- 动态自建角色进矩阵（走默认语义，见 §8 Q2）
