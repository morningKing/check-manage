# 智能客服 — Phase 1.5（安全加固）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 修复最终评审发现的合并阻断项：公开匿名入口经 MCP 可达 `run_python`(RCE) 等危险工具；并修 DoS 默认限速与文档授权路径错误。

**Architecture:** 双重防护。(a) 在 MCP 单一分发口 `tools/__init__.py:_call` 加中央工具白名单——公开 kefu 身份只允许只读工具集；(b) 加固各危险工具的角色守卫（`== "guest"` → 只读角色判定），给 `read_data_file` 补菜单角色门禁，给 `memory_*` 补公开身份门禁。附带修 Flask 侧默认限速 + SSE 并发上限 + 用户文档授权路径。

**Tech Stack:** MCP 服务（FastAPI + `mcp` streamable-http，独立 `.venv`，pytest）；Flask（kefu_public）；Markdown 文档。

## Global Constraints

- 根因：设计的「MCP 零改动」前提错误——bot 角色 `kefu-guest` 但 MCP 守卫硬编码 `"guest"`，故新只读角色反而绕过守卫。本期显式修正该前提。
- 公开 kefu 身份的工具白名单 = `{"query_collection", "list_collections", "read_upload"}`（只读数据查询 + 会话级上传读取，满足 spec 能力：知识问答/白名单数据查询/文件分析）。
- MCP tests 从 `mcp-server/` 自己的 venv 运行（大多 mock `get_db`）。实现前先确认运行命令：优先 `mcp-server/.venv/Scripts/python -m pytest tests/ -q`（Windows）；若无该 venv 用可用的 `python`（依赖已装）。测试模式见 `mcp-server/tests/test_run_python.py`：`_ctx(role)` 构造 `ToolContext(session_id, user_id, role)`，直接调 `handle(input, ctx)`。
- Flask 后端 tests 从 `server/` 运行，需 env `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`。
- 提交信息用中文 `feat:`/`fix:`/`test:`/`docs:` 前缀。
- 不改动 kefu 会话现有 Flask 逻辑（Phase 1 已评审通过），仅新增/加固。

---

### Task H1: MCP 中央工具白名单（防护 a）

**Files:**
- Create: `mcp-server/rbac.py`
- Modify: `mcp-server/tools/__init__.py`（`_call` 分发口）
- Test: `mcp-server/tests/test_rbac.py`, `mcp-server/tests/test_tool_dispatch_allowlist.py`

**Interfaces:**
- Produces `mcp-server/rbac.py`:
  - `PUBLIC_KEFU_ROLE = "kefu-guest"`
  - `READONLY_ROLES = frozenset({"guest", "kefu-guest"})`
  - `KEFU_TOOL_ALLOWLIST = frozenset({"query_collection", "list_collections", "read_upload"})`
  - `is_readonly(role: str) -> bool` → `role in READONLY_ROLES`
  - `is_public_kefu(role: str) -> bool` → `role == PUBLIC_KEFU_ROLE`
  - `tool_allowed(name: str, role: str) -> bool` → `True` unless `is_public_kefu(role)` and `name not in KEFU_TOOL_ALLOWLIST`

- [ ] **Step 1: 写失败测试 `test_rbac.py`**

```python
# mcp-server/tests/test_rbac.py
from rbac import is_readonly, is_public_kefu, tool_allowed, KEFU_TOOL_ALLOWLIST


def test_is_readonly():
    assert is_readonly("guest") and is_readonly("kefu-guest")
    assert not is_readonly("developer") and not is_readonly("admin")


def test_is_public_kefu():
    assert is_public_kefu("kefu-guest")
    assert not is_public_kefu("guest") and not is_public_kefu("admin")


def test_tool_allowed_public_kefu_restricted():
    assert tool_allowed("query_collection", "kefu-guest")
    assert tool_allowed("read_upload", "kefu-guest")
    assert not tool_allowed("run_python", "kefu-guest")
    assert not tool_allowed("save_artifact", "kefu-guest")
    assert not tool_allowed("read_data_file", "kefu-guest")
    assert not tool_allowed("memory_add", "kefu-guest")


def test_tool_allowed_others_unrestricted():
    for role in ("developer", "admin", "guest"):
        assert tool_allowed("run_python", role)
```

- [ ] **Step 2: 运行确认失败** — `cd mcp-server && <venv-python> -m pytest tests/test_rbac.py -v` → FAIL (ModuleNotFoundError: rbac)

- [ ] **Step 3: 写 `mcp-server/rbac.py`**

```python
# mcp-server/rbac.py
"""公开 kefu 身份的 MCP 访问钳制。

设计修正：kefu 会话 bot 用户角色为 'kefu-guest'（公开匿名身份）。此前危险工具
只守卫字面量 'guest'，导致 kefu-guest 绕过。此模块集中定义只读/公开身份判定与
公开身份的工具白名单，供分发口（防护 a）与各工具守卫（防护 b）共用。"""

PUBLIC_KEFU_ROLE = "kefu-guest"
READONLY_ROLES = frozenset({"guest", "kefu-guest"})
# 公开匿名客服只允许的只读工具：数据查询 + 会话级上传读取。
KEFU_TOOL_ALLOWLIST = frozenset({"query_collection", "list_collections", "read_upload"})


def is_readonly(role: str) -> bool:
    return role in READONLY_ROLES


def is_public_kefu(role: str) -> bool:
    return role == PUBLIC_KEFU_ROLE


def tool_allowed(name: str, role: str) -> bool:
    """公开 kefu 身份仅允许白名单工具；其余身份不受此层限制。"""
    if is_public_kefu(role):
        return name in KEFU_TOOL_ALLOWLIST
    return True
```

- [ ] **Step 4: 写失败测试 `test_tool_dispatch_allowlist.py`**（分发口拦截）

```python
# mcp-server/tests/test_tool_dispatch_allowlist.py
import asyncio
import pytest
from unittest.mock import patch
from context import ToolContext


def _dispatch(name, role):
    """驱动 register_all 注册的 call_tool 处理器，注入指定角色的 ctx。"""
    from mcp.server import Server
    from tools import register_all
    srv = Server("t")
    register_all(srv)
    handler = srv.request_handlers  # not used directly; call via captured closure below
    # register_all defines _call as the @server.call_tool() handler; retrieve it:
    # simplest: re-implement dispatch through the same path using the module-level _TOOLS
    from tools import __init__ as reg
    ctx = ToolContext(session_id="s1", user_id="kefu-bot", role=role)
    with patch("main._resolve_context", return_value=ctx):
        # call the private _call via a fresh registration is awkward; instead assert
        # tool_allowed gate is invoked. We test the gate wiring by calling the guarded path.
        from rbac import tool_allowed
        return tool_allowed(name, role)


def test_public_kefu_blocked_from_run_python():
    # dispatch-level gate mirrors tool_allowed; verify the guard denies run_python
    assert _dispatch("run_python", "kefu-guest") is False
    assert _dispatch("query_collection", "kefu-guest") is True
```

> 注：MCP `call_tool` 处理器是闭包，直接单测较绕。分发口的**行为**由 `tool_allowed` 决定，已在 `test_rbac.py` 覆盖；此文件锁定「分发口确实调用 `tool_allowed` 并在拒绝时 raise」——实现时若能简洁地捕获 `_call` 闭包并断言 raise 最好，否则保留对 `tool_allowed` 的断言并在实现步骤中人工确认 `_call` 已接线（见 Step 5）。实现者可将本测试改写为直接 import 并调用 `_call`（若把 `_call` 提为模块级函数更易测——见 Step 5 的可选重构）。

- [ ] **Step 5: 在 `tools/__init__.py` 接入白名单守卫**

把 `_call` 中解析 ctx 后、分发前加守卫。为可测性，将分发逻辑提为模块级函数：

```python
# tools/__init__.py — register_all 内
    @server.call_tool()
    async def _call(name: str, arguments: dict):
        from main import _resolve_context
        ctx = _resolve_context()
        return _dispatch_tool(name, arguments or {}, ctx)

# 新增模块级函数（可单测，无需 MCP server）
def _dispatch_tool(name, arguments, ctx):
    import mcp.types as types
    from rbac import tool_allowed
    entry = _TOOLS.get(name)
    if entry is None:
        raise ValueError(f"unknown tool: {name}")
    if not tool_allowed(name, ctx.role):
        raise PermissionError(f"tool '{name}' not available for this session")
    result = entry[1](arguments, ctx)
    return [types.TextContent(type="text", text=str(result))]
```

并把 Step 4 测试改为直接调用 `_dispatch_tool`：

```python
def test_dispatch_blocks_public_kefu_run_python():
    from tools import _dispatch_tool
    from context import ToolContext
    import pytest
    ctx = ToolContext(session_id="s1", user_id="kefu-bot", role="kefu-guest")
    with pytest.raises(PermissionError):
        _dispatch_tool("run_python", {}, ctx)

def test_dispatch_allows_public_kefu_query(monkeypatch):
    from tools import _dispatch_tool, _TOOLS
    from context import ToolContext
    called = {}
    monkeypatch.setitem(_TOOLS, "query_collection",
                        (_TOOLS["query_collection"][0], lambda a, c: called.setdefault("ok", True) or {"rows": []}))
    ctx = ToolContext(session_id="s1", user_id="kefu-bot", role="kefu-guest")
    _dispatch_tool("query_collection", {}, ctx)
    assert called.get("ok")
```

(Replace the earlier awkward `_dispatch` helper test with these two.)

- [ ] **Step 6: 运行确认通过** — `cd mcp-server && <venv-python> -m pytest tests/test_rbac.py tests/test_tool_dispatch_allowlist.py -v` → PASS

- [ ] **Step 7: 提交**

```bash
git add mcp-server/rbac.py mcp-server/tools/__init__.py mcp-server/tests/test_rbac.py mcp-server/tests/test_tool_dispatch_allowlist.py
git commit -m "feat(kefu-mcp): 公开 kefu 身份中央工具白名单（堵 run_python 等）"
```

---

### Task H2: 加固各工具角色守卫（防护 b）

**Files:**
- Modify: `mcp-server/tools/run_python.py`, `mcp-server/tools/save_artifact.py`, `mcp-server/tools/read_data_file.py`, `mcp-server/tools/memory.py`
- Test: extend `mcp-server/tests/test_run_python.py`, `test_save_artifact.py`, `test_read_data_file.py`, `test_memory_tools.py`

**Interfaces:**
- Consumes `rbac.is_readonly`, `rbac.is_public_kefu`.
- `run_python.handle` / `save_artifact.handle`: reject when `is_readonly(ctx.role)` (was `== "guest"`).
- `read_data_file.handle`: add a menu-role gate mirroring `query_collection.py:78-81` — look up `SELECT roles FROM menus WHERE page_id='page-'+collection`; reject if `ctx.role != "admin"` and role not in that roles list.
- `memory.handle_search/add/delete`: reject when `is_public_kefu(ctx.role)` (anonymous shared bot must not read/write cross-visitor memory).

- [ ] **Step 1 (run_python): write failing test** — add to `test_run_python.py`:

```python
def test_kefu_guest_blocked(tmp_path):
    from tools.run_python import handle, RunPythonError
    import pytest
    with pytest.raises(RunPythonError):
        handle({'code': 'print(1)'}, _ctx('kefu-guest'))
```

Run: FAIL (kefu-guest currently allowed). Then change `run_python.py:79`:

```python
from rbac import is_readonly
...
    if is_readonly(ctx.role):
        raise RunPythonError("read-only role is not allowed to run code")
```

Keep existing `test_guest_blocked` green (guest ∈ READONLY_ROLES). Run both → PASS.

- [ ] **Step 2 (save_artifact): same pattern** — add `test_kefu_guest_blocked` mirroring above (expect `SaveArtifactError`); change `save_artifact.py:48` to `if is_readonly(ctx.role): raise SaveArtifactError("read-only role is not allowed to write files")`. RED→GREEN.

- [ ] **Step 3 (read_data_file): add menu-role gate** — failing test in `test_read_data_file.py`:

```python
def test_kefu_guest_denied_when_not_in_menu_roles(monkeypatch):
    from tools.read_data_file import handle, ReadDataFileError
    from context import ToolContext
    import pytest
    from unittest.mock import MagicMock
    from contextlib import contextmanager
    cur = MagicMock()
    cur.fetchone.return_value = (["admin", "developer"],)  # roles for the menu; no kefu-guest
    conn = MagicMock(); conn.cursor.return_value = cur
    @contextmanager
    def _get(): yield conn
    monkeypatch.setattr('tools.read_data_file.get_db', _get)
    with pytest.raises(ReadDataFileError):
        handle({'collection': 'secret', 'record_id': 'R1', 'field': 'f'},
               ToolContext(session_id='s', user_id='kefu-bot', role='kefu-guest'))
```

Implement the gate at the top of `read_data_file.handle` (after arg validation, before dereferencing), querying `menus.roles` for `'page-'+collection`:

```python
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT roles FROM menus WHERE page_id = %s", ('page-' + collection,))
        mrow = cur.fetchone()
        roles = mrow[0] if mrow else None
        if ctx.role != "admin" and (roles is None or ctx.role not in roles):
            raise ReadDataFileError(f"role {ctx.role} 无权读取 {collection} 的文件")
```

Also fix the docstring (lines 12-14) that claims "any non-guest already has read access" — it's now menu-gated. Keep existing tests green (they use role 'developer'/admin — ensure their mocked `menus.roles` includes that role, or adjust the mock to return a roles list containing the tested role). RED→GREEN.

> Interface note: the existing `test_read_data_file.py` tests may now need the mocked cursor to answer the extra `SELECT roles FROM menus` call first. Adjust those tests' mock `fetchone`/`side_effect` so the menu-roles lookup returns a list containing the tested role, then the existing record/file lookups follow. Update them in this step.

- [ ] **Step 4 (memory): block public kefu** — failing tests in `test_memory_tools.py` (one per handler):

```python
def test_memory_search_blocked_for_kefu(monkeypatch):
    from tools.memory import handle_search
    from context import ToolContext
    import pytest
    with pytest.raises(PermissionError):
        handle_search({'query': 'x'}, ToolContext(session_id='s', user_id='kefu-bot', role='kefu-guest'))
```
(and analogous `handle_add`, `handle_delete`.) Implement at the top of each memory handler:

```python
from rbac import is_public_kefu
...
    if is_public_kefu(ctx.role):
        raise PermissionError("memory is not available for public customer-service sessions")
```

RED→GREEN. Keep existing memory tests (developer role) green.

- [ ] **Step 5: run the full mcp-server suite** — `cd mcp-server && <venv-python> -m pytest tests/ -q` → all green (no regressions).

- [ ] **Step 6: 提交**

```bash
git add mcp-server/tools/run_python.py mcp-server/tools/save_artifact.py mcp-server/tools/read_data_file.py mcp-server/tools/memory.py mcp-server/tests/
git commit -m "fix(kefu-mcp): 危险工具守卫改只读角色判定 + read_data_file 菜单门禁 + memory 拒公开身份"
```

---

### Task H3: Flask 侧限速默认值 + SSE 并发上限（Important #2）

**Files:**
- Modify: `server/routes/kefu_public.py`
- Test: extend `server/tests/test_kefu_public_chat.py` / `test_kefu_public_routes.py`

**Interfaces:**
- `_rate_ok`: when the instance's `rate_limit` lacks `perMinute`/`perDay`, apply a safe default floor (`DEFAULT_PER_MINUTE = 30`, `DEFAULT_PER_DAY = 500`). Explicit `0` in config still means "unlimited" (admin opt-out); only *absent* keys get the default.
- Apply `_rate_ok` (rate limiting) also to `send_message` (already) and add a lightweight per-`(instance,visitor)` **concurrent SSE cap** (`MAX_SSE_PER_VISITOR = 3`) in `events()` using a module-level counter dict guarded by a lock; increment on stream open, decrement in `finally`; return 429 when exceeded.

- [ ] **Step 1: failing tests** — add:

```python
def test_events_concurrency_cap(client):
    # 4th concurrent stream for same visitor+instance is rejected
    ...  # patch load_kefu_session to SESS; open 3 (mock generator), assert 4th -> 429
```
and a default-floor test:
```python
def test_rate_default_floor_applies_when_unset(client):
    inst = {**INST, 'rate_limit': {}}
    with patch('routes.kefu_public.kefu_repo.get_instance_by_slug', return_value=inst):
        # 31st create within a minute (default 30) -> 429
        ...
```

> The exact test shape depends on how `_rate_ok`/counter are structured; the implementer writes deterministic tests (inject `now`/patch the limiter or counter) rather than timing-dependent ones. If a true concurrency test is impractical with the test client, unit-test the counter helper directly (increment past cap → False) and assert `events()` returns 429 when the helper says full.

- [ ] **Step 2: implement** the default floor in `_rate_ok` and the SSE counter (module-level `_sse_active: dict[str,int]` + `threading.Lock`, a `try/finally` around the stream in `events()`), returning 429 when over `MAX_SSE_PER_VISITOR`.

- [ ] **Step 3: run** `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_kefu_public_chat.py tests/test_kefu_public_routes.py -v` → green; then full suite once (same known pre-existing failure only).

- [ ] **Step 4: 提交**

```bash
git add server/routes/kefu_public.py server/tests/
git commit -m "fix(kefu): 默认限速下限 + SSE 每访客并发上限，防匿名 DoS"
```

---

### Task H4: 修正用户文档授权路径（Important #3）

**Files:**
- Modify: `docs/user-guide/ai/smart-customer-service.md`

**Interface:** doc §4 grant-flow and §7 security table must state the REAL mechanism: MCP data tools gate on the data page menu's `roles` array. To grant the assistant access to a data page, add `kefu-guest` to that menu's `roles` (via menu management), NOT `/admin/roles` per-page CRUD. `default_page_access='none'` governs the Flask-side page CRUD, not MCP query gating (which is menu-roles based). Also update §7 to reflect the new tool-allowlist clamp (public kefu can only reach query_collection/list_collections/read_upload; run_python/save_artifact/read_data_file/memory are denied at the MCP dispatch layer).

- [ ] **Step 1:** read the current §4/§7, rewrite per the interface above; keep the rest accurate. Verify against `mcp-server/tools/query_collection.py:78-81` (menu-roles gate) and the new `rbac.py` allowlist.

- [ ] **Step 2: 提交**

```bash
git add docs/user-guide/ai/smart-customer-service.md
git commit -m "docs(kefu): 修正数据授权路径（菜单 roles）与安全边界描述"
```

---

## Self-Review

- Critical #1 (RCE): Task H1 (central allowlist — primary airtight fix for public kefu) + Task H2 (per-tool guard hardening + read_data_file menu gate + memory block — defense-in-depth) ✓
- Important #2 (DoS): Task H3 (default rate floor + SSE concurrency cap) ✓
- Important #3 (doc): Task H4 ✓
- Minors from final review (RESERVED '/kefu', session-reuse not implemented) intentionally deferred — noted in ledger for a future pass; not security-blocking.
- Placeholder scan: two test steps (H1 Step 4, H3 Step 1) describe the test shape and hand the implementer latitude on the exact MCP-closure / concurrency harness because those are awkward to over-specify; the behavioral assertions are concrete (allowlist denies run_python, default floor triggers 429). Not open-ended "add tests" — the asserts are named.
- Type consistency: `rbac` API (`is_readonly`/`is_public_kefu`/`tool_allowed`) used identically in H1 (dispatch) and H2 (tool guards); `_dispatch_tool(name, arguments, ctx)` signature defined in H1 Step 5 and tested there.

## After H1–H4

Re-run the final whole-branch review (opus) over the full branch to confirm the Critical is closed and no regressions, then proceed to finishing-a-development-branch.
