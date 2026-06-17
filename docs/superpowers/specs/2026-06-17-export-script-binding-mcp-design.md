# 导出脚本绑定 + MCP 导出工具 设计

> 状态：已与用户确认设计方向，待评审 spec 后进入 writing-plans。
> 日期：2026-06-17

## 目标

1. **导出脚本「专项专用」绑定**：让每个导出脚本绑定到一个具体的数据页（collection）或菜单，
   而不是对任意数据都能跑。新建脚本必须绑定；存量未绑定脚本宽容放行（仍可跑，但标记未绑定）。
2. **MCP 导出工具**：在 AI 助手对话里可调用导出脚本，导出结果作为可下载文件出现在会话中，
   并附结构化摘要。

## 背景与现状

- `export_scripts` 表：`id / name / description / language / script / output_format / scope(page|menu) / created_at / updated_at`。
  **没有任何到 collection/菜单的目标绑定**——`/exportScripts/execute` 接受 `scriptId + collection`，
  任意脚本可跑任意 collection，无校验。
- `page_configs.export_scripts`（JSONB 数组）/ `row_export_scripts`：**页面侧**声明本页要展示哪些导出脚本，
  是反方向的关联（页面挑脚本），不是脚本声明归属，也不做执行期限定。
- `menus.export_script_id`：菜单可挂一个菜单级导出脚本。
- 脚本执行编排分散在 `server/routes/export_scripts.py`（execute / test / debug / batchExport）
  与 `server/utils/menu_export.py`（菜单导出），各自 fetch 数据 + `resolve_references` + 调 `script_runner`。
- MCP 服务器 `mcp-server/`：独立进程/venv，直连同一 DB（`mcp-server/db.py`，`get_db()`），
  `app_config.py` 已把仓库根下的 `server/.env` 加载进来；工具写文件到会话 `outputs/` 目录后，
  AI 会话即可下载（见 `mcp-server/tools/export_collection_excel.py` 的既有范式：返回
  `{saved, path, rows, columns, label}`）。MCP venv 已含 `pandas`。

## 设计决策（已与用户确认）

1. **绑定模型**：按导出维度绑定——`scope='page'` → 绑定一个 collection；`scope='menu'` → 绑定一个菜单。一脚本一目标。
   （`scope` 取值只有 `page|menu`；行级导出是「page 维度脚本 + `record_id` 入参」，仍归属其绑定的 collection，无独立 row scope。）
2. **强制性**：新建必绑；存量未绑定脚本宽容（仍可执行，UI 标「未绑定」；MCP 只暴露已绑定脚本）。
3. **结果呈现**：写入会话 `outputs/` 生成可下载文件 + 返回结构化摘要（文件名/行数/前若干行预览）。
4. **绑定列**：用两个显式列 `bound_collection` / `bound_menu_id`（而非单一泛化列）。
5. **MCP 执行方式**：抽取**共享、传入游标**的 `export_runner` 工具函数，Flask 路由与 MCP 工具都调它，
   单一执行权威（含我们刚修的 references 注入），不走 HTTP、不跨服务鉴权。

## 数据模型

`export_scripts` 新增两列（`init_db.py` 迁移，nullable，存量即「未绑定」）：

```sql
ALTER TABLE export_scripts ADD COLUMN bound_collection VARCHAR(100);  -- page/row 维度绑定的 collection
ALTER TABLE export_scripts ADD COLUMN bound_menu_id   VARCHAR(100);   -- menu 维度绑定的菜单 id
```

约束（应用层校验，不加 DB 约束以便宽容存量）：
- `scope='menu'` 的脚本绑定写入 `bound_menu_id`，`bound_collection` 必为空；
- `scope='page'` 的脚本（含其行级导出用法）绑定写入 `bound_collection`，`bound_menu_id` 必为空；
- 一个脚本至多一个绑定。

「已绑定」判定：`bound_collection` 或 `bound_menu_id` 非空。

## 组件

### A. 共享执行器 `server/utils/export_runner.py`（新增，Flask-free，游标注入）

单一职责：给定脚本与目标，校验绑定 + RBAC，取数、解引用、跑沙箱，返回结果字节。
不 import Flask、不 import 任何 db 模块——**调用方传入游标**（Flask 用 server 的 cur，MCP 用 mcp 的 cur）。

```python
def execute_bound_export(cur, script_row, *, collection=None, menu_id=None,
                         branch_id='main', role=None, record_id=None):
    """
    script_row: (id, name, script, output_format, scope, bound_collection, bound_menu_id)
    返回:
      - page/row 维度: (result_bytes, filename, content_type)
      - menu 维度:     list[(result_bytes, filename, content_type)]
    抛 ExportBindingError（目标与绑定不符）/ ExportPermissionError（RBAC 不通过）。
    """
```

职责拆解（内部小函数）：
- `check_binding(script_row, collection, menu_id)`：已绑定脚本，目标必须等于绑定；未绑定脚本放行（宽容）。
- `check_rbac(cur, target, role)`：取目标（collection→其菜单 / menu）roles，`role=='admin'` 或 role ∈ roles 才放行。
- 取数 + `resolve_page_references` / `resolve_references`（复用现有引用解析）+ `run_export_script` / `run_menu_export_script`。

`server/routes/export_scripts.py` 与 `server/utils/menu_export.py` 重构为调用该执行器（消除现有重复编排）。

### B. 后端路由 `server/routes/export_scripts.py`

- `GET/POST/PUT /exportScripts`：读写新增 `boundCollection` / `boundMenuId`（camelCase）。
  **POST（新建）校验**：绑定必须与 `scope` 一致且非空（new-must-bind），否则 400。
  PUT 允许修改绑定（同样校验一致性；允许把存量脚本补绑）。
- `POST /exportScripts/execute`、`/test`、`/debug`、`/batchExport`：调用 `execute_bound_export`，
  对已绑定脚本校验 `collection`/`menuId` 与绑定相符，不符返回 400「该脚本仅限于其绑定目标」。
- `GET /exportScripts/for-collection/<collection>`（新增）：返回该 collection 可用脚本
  = 绑定到它的脚本 ∪ 该页 `page_configs.export_scripts` 里的未绑定脚本（向后兼容）。
  供数据页导出菜单与 MCP `list` 复用。

### C. 前端 `src/views/admin/ExportScriptManager.vue`

- 表单加「绑定目标」项，随 `scope` 切换控件：
  - `scope=page/row` → 数据页选择器（collectionOptions）；
  - `scope=menu` → 菜单选择器。
- 新建保存时绑定必填（前端校验 + 后端兜底）；编辑存量脚本可补绑。
- 列表/详情给未绑定脚本标「未绑定」灰标签。
- `DynamicPage` 导出菜单改用 `GET /exportScripts/for-collection/<collection>`（绑定驱动 + 兼容旧 opt-in）。

### D. MCP 服务器 `mcp-server/tools/`（两个新工具）

通过 `sys.path` 追加 `<repo>/server`，`from utils.export_runner import execute_bound_export`
（`export_runner` 只依赖 `utils.script_runner` + `utils.export_references`，均 Flask-free、stdlib+pandas）。

- `list_export_scripts`：返回调用者角色可访问、**已绑定**的脚本
  `[{id, name, description, target, outputFormat}]`（target 形如 `page:inspection-case` / `menu:<id>`）。
  RBAC 按绑定目标的菜单 roles 过滤。
- `run_export_script(script_id)`：
  1. 取脚本行；要求已绑定（未绑定脚本不经 MCP 暴露 / 调用拒绝）。
  2. `execute_bound_export(cur, row, collection=bound_collection 或 menu_id=bound_menu_id, role=ctx.role)`。
  3. 把结果写入会话 `outputs/<name>-<时间戳>.<ext>`（menu 维度多文件则逐个写）。
  4. 返回 `{saved: true, path: "outputs/...", filename, output_format, rows?, preview}`（preview = 文本类前 ~1000 字；二进制不预览）。
  入参仅 `script_id`——目标由绑定推导，符合「专项专用」。

### E. AI 会话呈现

复用既有机制：`outputs/` 文件在会话内可下载（与 `export_collection_excel` 一致），
工具结果 JSON（M1 以 JSON 气泡呈现）给出摘要/预览。**无需新增会话渲染代码。**

## 数据流（MCP 调用导出）

```
用户在 AI 助手:「把巡检用例导出」
  → Agent 调 list_export_scripts → 看到绑定到 inspection-case 的脚本
  → Agent 调 run_export_script(script_id)
      → execute_bound_export(mcp_cur, row, collection='inspection-case', role=ctx.role)
          → check_binding ✓  check_rbac ✓  取数 + 解引用 + 沙箱
          → (bytes, filename, content_type)
      → 写 outputs/inspection-case-20260617-xxxx.xlsx
      → 返回 {saved, path, filename, rows, preview}
  → 会话出现可下载文件 + 摘要
```

## 错误处理

- 绑定不符：执行端点 / MCP 返回明确错误「该脚本仅限其绑定目标（<target>），不能用于 <requested>」。
- RBAC 不通过：`ExportPermissionError` → 路由 403 / MCP 工具错误「无权限导出 <target>」。
- 新建缺绑定：400「<scope> 维度脚本必须绑定<数据页/菜单>」。
- 引用解析失败：沿用现状——告警不阻断，退化为裸 ID。
- MCP 跑未绑定脚本：拒绝并提示去管理端补绑。

## 测试

- **export_runner 单测**（真实 DB，沿用 `test_export_references.py` 模式）：绑定相符跑通、绑定不符抛错、
  未绑定宽容跑通、RBAC 拒绝、references 注入仍生效。
- **路由测**：新建缺绑定 400、绑定与 scope 不一致 400、execute 目标不符 400、`for-collection` 返回集合正确。
- **MCP 工具测**（`mcp-server/tests/`，沿用现有工具测模式）：`list` 按角色/绑定过滤、`run` 写出 outputs 文件 +
  返回摘要、未绑定脚本被拒、RBAC 拒绝。
- 现有导出回归全绿（execute/test/debug/batch/menu-export 重构后行为不变）。

## 文档同步（随实现 PR）

- `docs/user-guide/admin/scripts.md`：导出脚本绑定（新建必绑、按维度选目标、未绑定说明）。
- `docs/user-guide/ai/`：AI 助手中调用导出脚本（list/run、结果下载）。
- 截图：脚本绑定表单、AI 会话中的导出结果。

## 不做（YAGNI）

- 行级（单条记录）导出经 MCP 暴露——MCP 仅暴露 collection/菜单维度。
- 一脚本绑多目标。
- 自动迁移/强制存量脚本绑定（保持宽容）。
- 新的会话内联富渲染（沿用下载 + JSON 摘要）。

## 影响文件清单

- 后端：`server/init_db.py`（迁移）、`server/utils/export_runner.py`（新增）、
  `server/routes/export_scripts.py`、`server/utils/menu_export.py`（重构调用执行器）。
- 前端：`src/views/admin/ExportScriptManager.vue`、`src/api/exportScript*.ts`、
  `src/views/dynamic/DynamicPage.vue`（导出菜单数据源）。
- MCP：`mcp-server/tools/list_export_scripts.py`、`mcp-server/tools/run_export_script.py`、
  `mcp-server/tools/__init__.py`（注册）。
- 测试：`server/tests/test_export_runner.py`（新增）、`server/tests/test_routes_export_scripts.py`（补绑定用例）、
  `mcp-server/tests/`（新增工具测）。
- 文档：见「文档同步」。
```
