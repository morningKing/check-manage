# AI 助手自然语言查询数据 — 设计

- 日期: 2026-05-29
- 状态: 已批准（待实现）
- 范围: MCP 服务（新工具 + 移植查询引擎）+ 前端结果渲染 + agent 提示

## 背景

数据工具里已有「AI 查询」：`POST /ai/query`（用配置好的 qwen 模型 `nl_to_mongo_filter` 把自然语言译成
MongoDB 风格 filter）→ `POST /query/execute`（filter + 可选 lookup 跑在 `dynamic_data` 上）。这套属于
Flask 后端，与 AI 助手（OpenCode + MCP，模型为较弱的 MiMo）是两套系统。

AI 助手目前的能力来自 MCP 工具（`list_collections`、`export_collection_excel`、`run_python`、
`read_upload`、`save_artifact`）。`list_collections` 已按角色返回每个集合的字段 schema（含 select 选项）。

需求：让用户在 AI 助手里用自然语言查询平台数据，支持跨集合关联，结果默认渲染成表格。

## 关键决策（已与用户确认）

1. **谁翻译 NL→filter**：让 **agent 自己翻译**（用 `list_collections` 的 schema），不复用 qwen。
   MCP 工具只负责「执行一个 filter」。理由：解耦、不依赖 qwen 的 `ai_settings`；代价是依赖 MiMo 翻译质量
   （靠工具描述 + schema 缓解）。
2. **查询范围**：支持**跨集合关联**（lookup），对齐 `/query/execute`。
3. **实现策略**：把查询逻辑**移植进 MCP 服务**（直连数据库），保持 MCP「独立解耦」。代价：与 Flask 的
   查询逻辑约 400 行重复——两处文件顶部加注释互相指引。
4. **结果渲染**：默认渲染成表格。`总行数 ≤ 400` → 表格预览 + 下载；`> 400` → 仅 xlsx 下载、不预览。

## 目标 / 非目标

**目标**
- 新增 MCP 工具 `query_collection`，按 MongoDB 风格 filter（+lookup/select/sort）只读查询，RBAC 受控。
- 前端把 `query_collection` 的结果渲染成表格（≤400 预览+下载；>400 仅 xlsx）。
- 引导 agent 在回答数据问题时使用该工具。

**非目标**
- 不改动数据工具里现有的 `/ai/query`、`/query/execute`（保持原样）。
- 不引入 qwen 到 agent 流程；不做写操作。
- 不实现完整的 M2 tool-renderers 注册表——仅为 `query_collection` 加一个专用渲染分支。

## 设计

### 1. MCP 工具 `query_collection`（`mcp-server/tools/query_collection.py`）

- **入参**（inputSchema）：
  - `collection` (string, 必填)
  - `filter` (object, 选填；MongoDB 风格，缺省=全部)
  - `lookup` (array, 选填；元素 `{from, localField, as}`，同 `/query/execute`)
  - `select` (array<string>, 选填；fieldName 或 label)
  - `sort` (object, 选填；`{field: 1|-1}`)
  - `skip` (int, 选填，默认 0)
  - `limit` (int, 选填，默认 400，**上限 400**——表格模式的取行上限；总行数 ≤400 时即返回全部匹配行）
- **RBAC**：集合必须对 `ctx.role` 可见——`admin` 可见全部；否则该集合对应菜单（`page_id='page-'+collection`）
  的 `menus.roles` 含 `ctx.role`。与 `list_collections` 同口径。**只读**，任何角色（含 guest）可读其可见集合。
- **行为**：
  1. 校验集合存在且可见，否则返回错误 JSON（`{error}`）。
  2. label→fieldName 重映射（filter / select / sort / lookup.localField）。
  3. `COUNT` 得到 `total`。
  4. 分支：
     - `total ≤ 400`：取行（受 `limit`≤400 限制，并按 `sort`/`skip`），跑 lookup，组列，返回
       `{mode:"table", collection, total, columns, rows}`。
     - `total > 400`：取全部匹配行（**硬上限 50000** 防 OOM）写 xlsx 到 `outputs/query-<collection>-<ts>.xlsx`，
       返回 `{mode:"file", collection, total, file:"outputs/query-...xlsx", capped: <bool>}`，**不含 rows**。
  5. 返回值是 **JSON 字符串**（`json.dumps`），这样前端可 `JSON.parse`，agent 也能读取。
     （注意：`tools/__init__.py` 用 `str(result)` 包装；handle 直接返回 JSON 字符串，`str()` 即其本身。）

### 2. 移植查询引擎

- `mcp-server/mongo_query.py`：从 `server/utils/mongo_query.py` **复制** `translate` / `remap_labels`
  及其辅助（自包含、仅用 `re`）。顶部注释标明「与 server/utils/mongo_query.py 保持同步」。
- `mcp-server/query_engine.py`：把 `server/routes/query.py` 的执行逻辑移植为纯函数
  `run_query(conn, collection, query, lookups, select, sort, skip, limit, configs)`，含
  relation（`data_relations`）/ reference / quoteSelect / generic 四种 lookup 与列元数据构建，使用传入的
  psycopg2 连接（来自 mcp `db.get_db()`）。顶部注释指向 `server/routes/query.py`。
- xlsx 写出：复用 `mcp-server/tools/export_collection_excel.py` 既有的 pandas/openpyxl 写法（抽到可复用函数
  或在 query_collection 内调用），把 rows 写入 `outputs/`。

### 3. 前端结果渲染（`src/components/ai-chat/QueryResultBlock.vue`）

- Props：`result`（已解析对象）、`downloadUrl`（`(path)=>url`，复用现有 `downloadFileUrl`）。
- `mode==='table'`：
  - `el-table`，列来自 `columns`（`{key,label}`），行来自 `rows`；lookup 关联列**紧凑展示**为名称拼接或
    计数（对象取常见 name 字段；数组显示「N 项」并可 title 展开），避免巨大单元格。
  - 「下载 Excel」按钮：用项目已有的 `xlsx`(SheetJS) **客户端**从 `rows` 生成并下载（`XLSX.utils.json_to_sheet`
    → `XLSX.writeFile`），文件名 `<collection>.xlsx`。
- `mode==='file'`：展示一个下载卡片/链接（文件名 + 行数 `total`），`href = downloadUrl(result.file)`，
  **不渲染表格**；`capped` 为真时提示「已截断至 5 万行」。
- 解析失败或 `mode` 缺失/进行中 → 回退到 `ToolCallBubble`（不报错）。
- **接线**（`AiChatView.vue`）：`tool_use` 分支改为——当 `p.name==='query_collection'` 且 `p.status` 为完成
  且 `p.result` 可解析为带 `mode` 的对象时渲染 `QueryResultBlock`，否则 `ToolCallBubble`。

### 4. agent 引导

- `query_collection` 工具描述（中文）：说明「先用 `list_collections` 获取字段；filter 用 fieldName（英文）；
  select 类型的值用 option 的 value；支持 lookup 关联」。
- `server/routes/ai_chat.py` 的 `_AGENT_DIRECTIVE` 增加一句：回答数据查询类问题时调用 `query_collection`，
  不要臆造数据、不要写直连数据库的脚本。

## 数据流

1. 用户问「找出所有待评审的用例并按创建时间排序」。
2. agent 调 `list_collections` 拿字段（若尚不了解）。
3. agent 生成 `filter`（如 `{"status":{"$regex":"待评审"}}` 或 select 的 value）、可选 sort，调 `query_collection`。
4. 工具执行（RBAC、只读、限量），返回 table/file JSON。
5. SSE 把 tool_use（含 result）推给前端；`QueryResultBlock` 渲染表格或 xlsx；agent 文本作答。

## 测试

- **mcp 单测**：
  - `test_mongo_query.py`：translate 基本算子（等值/$regex/$in/$or/比较）、remap_labels。
  - `test_query_collection.py`：可见性 RBAC（admin vs 受限角色 vs 不可见→错误）；filter 命中；lookup（relation）；
    `total ≤ 400` 走 table、`> 400` 走 file（造数据或对计数打桩）；limit 上限 400；只读不改库。
- **前端单测**：`QueryResultBlock.test.ts`：table 模式渲染 el-table 行/列、点击下载调用 SheetJS；file 模式只渲染
  下载链接、无表格；非法 result 回退。
- **真机验证**：AI 助手问一个数据问题，确认 agent 调用 `query_collection` 且表格正确渲染、可下载；造 >400 行场景确认仅出 xlsx。
- 后端无回归：现有 ai_chat / query 测试不受影响。

## 风险与缓解

- **逻辑重复**：mongo_query + 查询执行在 MCP 与 Flask 各一份。缓解：两处文件顶部互指注释；逻辑稳定、变动不频繁。
- **MiMo 翻译质量**：靠工具描述 + list_collections schema；复杂查询可能需用户澄清。
- **上下文膨胀**：表格限 400 行；大结果走 xlsx 文件不入上下文。
- **安全**：参数化 SQL（mongo_query 用 params）、只读、RBAC 可见性、xlsx 5 万行硬上限。
- **tool 结果格式**：必须返回 JSON 字符串（非 Python repr），否则前端无法解析。

## 影响文件清单

- 新增：`mcp-server/tools/query_collection.py`、`mcp-server/mongo_query.py`、`mcp-server/query_engine.py`
- 改：`mcp-server/tools/__init__.py`（注册工具）
- 新增：`src/components/ai-chat/QueryResultBlock.vue`；改 `src/views/ai-chat/AiChatView.vue`（接线）
- 改：`server/routes/ai_chat.py`（`_AGENT_DIRECTIVE`）
- 测试：`mcp-server/tests/test_mongo_query.py`、`mcp-server/tests/test_query_collection.py`、
  `src/components/ai-chat/__tests__/QueryResultBlock.test.ts`
