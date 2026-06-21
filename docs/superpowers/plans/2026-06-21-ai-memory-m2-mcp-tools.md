# AI 长期记忆 — M2 实现计划（MCP 主动记忆工具）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`).

**Goal:** 让 OpenCode agent 能主动读写用户长期记忆（`memory_search` / `memory_add` / `memory_delete`），且不与 Flask 双写 Chroma —— 经 Flask 内部端点转发，Flask 仍是 mem0/Chroma 唯一所有者。

**Architecture:** Flask 暴露 `/ai/memory/internal/{search,add,delete}`（共享内部 token 鉴权，仅供 MCP 调），复用 M1 的 `utils/memory.py`。MCP server 新增 3 个工具，用标准库 `urllib` POST 到该端点，作用域为 `ctx.user_id`。

**Tech Stack:** Flask（内部端点），mcp-server（FastAPI/MCP，独立 venv，标准库 urllib），pytest。

**Spec:** `docs/superpowers/specs/2026-06-20-ai-session-longterm-memory-design.md` §7。依赖 M1（`utils/memory.py`，已合在分支 `feat/ai-session-longterm-memory`）。

---

## File Structure（M2）
- `server/config.py` — 加 `MCP_INTERNAL_TOKEN`（env）
- `server/routes/ai_memory_internal.py`（新）— 内部端点 blueprint
- `server/app.py` — 注册该 blueprint（`dynamic_bp` 之前）
- `server/tests/test_ai_memory_internal.py`（新）
- `mcp-server/tools/memory.py`（新）— 3 个工具
- `mcp-server/tools/__init__.py` — 注册
- `mcp-server/memory_client.py`（新）— urllib 封装（Flask 内部调用）
- `mcp-server/tests/test_memory_tools.py`（新）
- `server/.env.example` — `MCP_INTERNAL_TOKEN` + `FLASK_INTERNAL_URL`
- `docs/user-guide/ai/long-term-memory.md` + `CLAUDE.md` — 补充

---

## Task 1：Flask 内部 memory 端点 + token 鉴权

**Files:** Create `server/routes/ai_memory_internal.py`; Modify `server/config.py`, `server/app.py`; Test `server/tests/test_ai_memory_internal.py`.

- [ ] **Step 1: config 加内部 token**

在 `server/config.py` 末尾加：
```python
# Shared secret for the MCP server -> Flask internal memory endpoints.
# Empty (default) disables the internal endpoints (returns 403).
MCP_INTERNAL_TOKEN = os.getenv('MCP_INTERNAL_TOKEN', '')
```

- [ ] **Step 2: 写失败测试** `server/tests/test_ai_memory_internal.py`:
```python
import sys, os
from unittest.mock import patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from app import app

def _client():
    app.config['TESTING'] = True
    return app.test_client()

def test_search_requires_internal_token():
    with patch('routes.ai_memory_internal.MCP_INTERNAL_TOKEN', 'secret'):
        r = _client().post('/ai/memory/internal/search', json={'userId': 'u', 'query': 'q'})
    assert r.status_code == 403

def test_search_forwards_to_memory():
    with patch('routes.ai_memory_internal.MCP_INTERNAL_TOKEN', 'secret'), \
         patch('routes.ai_memory_internal.search_memory', return_value=[{'id': '1', 'memory': '喜欢 Python'}]) as s:
        r = _client().post('/ai/memory/internal/search',
                           headers={'X-Internal-Token': 'secret'},
                           json={'userId': 'alice', 'query': '技术', 'limit': 3})
    assert r.status_code == 200
    assert r.get_json()['results'][0]['memory'] == '喜欢 Python'
    s.assert_called_once_with('alice', '技术', 3)

def test_add_forwards_messages():
    with patch('routes.ai_memory_internal.MCP_INTERNAL_TOKEN', 'secret'), \
         patch('routes.ai_memory_internal.add_memory') as a:
        r = _client().post('/ai/memory/internal/add',
                           headers={'X-Internal-Token': 'secret'},
                           json={'userId': 'alice', 'messages': [{'role': 'user', 'content': '记住X'}]})
    assert r.status_code == 200
    a.assert_called_once_with('alice', [{'role': 'user', 'content': '记住X'}])

def test_delete_forwards():
    with patch('routes.ai_memory_internal.MCP_INTERNAL_TOKEN', 'secret'), \
         patch('routes.ai_memory_internal.delete_memory') as d:
        r = _client().post('/ai/memory/internal/delete',
                           headers={'X-Internal-Token': 'secret'},
                           json={'memoryId': 'm1'})
    assert r.status_code == 200
    d.assert_called_once_with('m1')

def test_disabled_when_token_empty():
    with patch('routes.ai_memory_internal.MCP_INTERNAL_TOKEN', ''):
        r = _client().post('/ai/memory/internal/search',
                           headers={'X-Internal-Token': ''}, json={'userId': 'u', 'query': 'q'})
    assert r.status_code == 403
```

- [ ] **Step 3: Run, confirm FAIL** (blueprint not registered → 404, not 403):
`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_ai_memory_internal.py -v`

- [ ] **Step 4: 写 blueprint** `server/routes/ai_memory_internal.py`:
```python
"""Internal memory endpoints for the MCP server (NOT for browsers).

The MCP server runs in its own process; letting it open mem0/Chroma directly
would double-write Chroma's single-writer store. So memory ops route here and
Flask stays the sole owner. Guarded by a shared MCP_INTERNAL_TOKEN.
"""
from flask import Blueprint, request, jsonify
from config import MCP_INTERNAL_TOKEN
from utils.memory import search_memory, add_memory, delete_memory

ai_memory_internal_bp = Blueprint('ai_memory_internal', __name__, url_prefix='/ai/memory/internal')


def _authorized():
    token = request.headers.get('X-Internal-Token', '')
    return bool(MCP_INTERNAL_TOKEN) and token == MCP_INTERNAL_TOKEN


@ai_memory_internal_bp.route('/search', methods=['POST'])
def internal_search():
    if not _authorized():
        return jsonify({'error': 'forbidden'}), 403
    body = request.get_json(force=True) or {}
    user_id = body.get('userId', '')
    query = body.get('query', '')
    limit = int(body.get('limit', 5))
    return jsonify({'results': search_memory(user_id, query, limit)})


@ai_memory_internal_bp.route('/add', methods=['POST'])
def internal_add():
    if not _authorized():
        return jsonify({'error': 'forbidden'}), 403
    body = request.get_json(force=True) or {}
    add_memory(body.get('userId', ''), body.get('messages') or [])
    return jsonify({'ok': True})


@ai_memory_internal_bp.route('/delete', methods=['POST'])
def internal_delete():
    if not _authorized():
        return jsonify({'error': 'forbidden'}), 403
    body = request.get_json(force=True) or {}
    delete_memory(body.get('memoryId', ''))
    return jsonify({'ok': True})
```

- [ ] **Step 5: 注册 blueprint** in `server/app.py` — import it and `app.register_blueprint(ai_memory_internal_bp)` alongside the other `ai_*` blueprints, BEFORE `dynamic_bp`. Follow the exact registration pattern already used for `ai_chat_bp`.

- [ ] **Step 6: Run, confirm PASS** (same command as Step 3; expect 5 passed).

- [ ] **Step 7: Commit:**
```
cd E:/wsl/check/check-manage
git add server/config.py server/routes/ai_memory_internal.py server/app.py server/tests/test_ai_memory_internal.py
git commit -m "feat(memory): internal Flask endpoints for MCP memory tools (M2)"
```

## Context
- ALREADY on branch `feat/ai-session-longterm-memory`. Repo `E:\wsl\check\check-manage`, backend `server/`.
- `utils/memory.py` (from M1) already has `search_memory(user_id, query, limit)`, `add_memory(user_id, messages)`, `delete_memory(memory_id)` — all degrade safely. Reuse them.
- `app.py` registers blueprints in order; `dynamic_bp` MUST stay last. READ how `ai_chat_bp` is registered and mirror it.
- These endpoints have NO browser auth decorator on purpose — they're guarded by the shared `X-Internal-Token`. Do NOT add `@login_required`.

---

## Task 2：MCP memory 工具

**Files:** Create `mcp-server/memory_client.py`, `mcp-server/tools/memory.py`; Modify `mcp-server/tools/__init__.py`; Test `mcp-server/tests/test_memory_tools.py`.

- [ ] **Step 1: 写 Flask 内部调用封装** `mcp-server/memory_client.py`:
```python
"""Thin urllib client for Flask's internal memory endpoints. Stdlib only (the
MCP server's venv has no `requests`)."""
import os
import json
import urllib.request
import urllib.error

FLASK_INTERNAL_URL = os.getenv('FLASK_INTERNAL_URL', 'http://127.0.0.1:3002')
MCP_INTERNAL_TOKEN = os.getenv('MCP_INTERNAL_TOKEN', '')


def _post(path: str, payload: dict) -> dict:
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        FLASK_INTERNAL_URL + path, data=data, method='POST',
        headers={'Content-Type': 'application/json', 'X-Internal-Token': MCP_INTERNAL_TOKEN},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'memory endpoint {path} failed ({e.code})')
    except urllib.error.URLError as e:
        raise RuntimeError(f'memory endpoint {path} unreachable: {e.reason}')


def search(user_id: str, query: str, limit: int = 5) -> list:
    return _post('/ai/memory/internal/search', {'userId': user_id, 'query': query, 'limit': limit}).get('results', [])


def add(user_id: str, text: str) -> None:
    _post('/ai/memory/internal/add', {'userId': user_id, 'messages': [{'role': 'user', 'content': text}]})


def delete(memory_id: str) -> None:
    _post('/ai/memory/internal/delete', {'memoryId': memory_id})
```

- [ ] **Step 2: 写失败测试** `mcp-server/tests/test_memory_tools.py`:
```python
import sys, os
from unittest.mock import patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from context import ToolContext
from tools import memory as memtool

CTX = ToolContext(session_id='s', user_id='alice', role='developer')

def test_search_scopes_to_user():
    with patch('tools.memory.memory_client.search', return_value=[{'id': '1', 'memory': '喜欢 Python'}]) as s:
        out = memtool.handle_search({'query': '技术', 'limit': 3}, CTX)
    s.assert_called_once_with('alice', '技术', 3)
    assert '喜欢 Python' in out

def test_add_uses_ctx_user():
    with patch('tools.memory.memory_client.add') as a:
        memtool.handle_add({'text': '记住X'}, CTX)
    a.assert_called_once_with('alice', '记住X')

def test_delete():
    with patch('tools.memory.memory_client.delete') as d:
        memtool.handle_delete({'memoryId': 'm1'}, CTX)
    d.assert_called_once_with('m1')
```

- [ ] **Step 3: Run, confirm FAIL.** From `mcp-server`, use its venv. Determine the test command the repo uses for mcp-server (check `mcp-server/tests/conftest.py` / any `pytest.ini`); typically:
`cd mcp-server && python -m pytest tests/test_memory_tools.py -v`

- [ ] **Step 4: 写工具** `mcp-server/tools/memory.py`:
```python
"""Tools: per-user long-term memory (search/add/delete). Routed through Flask's
internal endpoints so Flask stays the sole mem0/Chroma owner. Scope = ctx.user_id."""
import mcp.types as types
from context import ToolContext
import memory_client

SEARCH = types.Tool(
    name="memory_search",
    description="检索当前用户的长期记忆（偏好/习惯/事实）。参数：query(必填)、limit(可选,默认5)。",
    inputSchema={"type": "object", "properties": {
        "query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]},
)
ADD = types.Tool(
    name="memory_add",
    description="为当前用户新增一条长期记忆。参数：text(必填，要记住的事实/偏好)。",
    inputSchema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
)
DELETE = types.Tool(
    name="memory_delete",
    description="删除一条长期记忆。参数：memoryId(必填，来自 memory_search 的 id)。",
    inputSchema={"type": "object", "properties": {"memoryId": {"type": "string"}}, "required": ["memoryId"]},
)


def handle_search(args: dict, ctx: ToolContext) -> str:
    rows = memory_client.search(ctx.user_id, args.get("query", ""), int(args.get("limit", 5)))
    if not rows:
        return "（无相关记忆）"
    return "\n".join(f"- [{r.get('id')}] {r.get('memory')}" for r in rows)


def handle_add(args: dict, ctx: ToolContext) -> str:
    memory_client.add(ctx.user_id, args.get("text", ""))
    return "已记住。"


def handle_delete(args: dict, ctx: ToolContext) -> str:
    memory_client.delete(args.get("memoryId", ""))
    return "已删除。"
```

- [ ] **Step 5: 注册到** `mcp-server/tools/__init__.py`:
- add `memory` to the `from tools import (...)` import block
- add three entries to `_TOOLS`:
```python
    memory.SEARCH.name: (memory.SEARCH, lambda a, c: memory.handle_search(a, c)),
    memory.ADD.name: (memory.ADD, lambda a, c: memory.handle_add(a, c)),
    memory.DELETE.name: (memory.DELETE, lambda a, c: memory.handle_delete(a, c)),
```
> Match the EXACT shape of existing `_TOOLS` entries — each value is `(spec, handler)` where `handler(arguments, ctx)`. If existing entries use `module.handle` directly, expose a single `handle`-style callable per tool instead; adapt to the real registry shape you see in the file.

- [ ] **Step 6: Run, confirm PASS** (same command as Step 3; expect 3 passed). Also run the full mcp-server suite to confirm no regression: `cd mcp-server && python -m pytest tests/ -q`.

- [ ] **Step 7: Commit:**
```
cd E:/wsl/check/check-manage
git add mcp-server/memory_client.py mcp-server/tools/memory.py mcp-server/tools/__init__.py mcp-server/tests/test_memory_tools.py
git commit -m "feat(memory): MCP memory_search/add/delete tools via Flask (M2)"
```

## Context
- `mcp-server/` is a separate process with its OWN venv. Stdlib only for the new client (no `requests`).
- Tool registry (`tools/__init__.py`): `_TOOLS` maps `name -> (spec, handler)`; `handler(arguments, ctx)`. `ctx` is a `ToolContext(session_id, user_id, role)` from the request token. READ the file to match the exact entry shape.
- `app_config.py` loads `server/.env`, so `os.getenv('FLASK_INTERNAL_URL')` / `MCP_INTERNAL_TOKEN` resolve from there. The new `memory_client.py` reads them at import — that's fine for the server process.
- `tests/conftest.py` already sets up the mcp-server test path/venv; follow its conventions.

---

## Task 3：配置 + 文档

**Files:** Modify `server/.env.example`, `docs/user-guide/ai/long-term-memory.md`, `CLAUDE.md`.

- [ ] **Step 1: .env.example** — append:
```
# MCP <-> Flask internal memory channel (M2). Set a random secret to enable
# the agent's memory_* tools; leave empty to disable the internal endpoints.
MCP_INTERNAL_TOKEN=
FLASK_INTERNAL_URL=http://127.0.0.1:3002
```

- [ ] **Step 2: 用户指南** — append a short section to `docs/user-guide/ai/long-term-memory.md`:
```markdown
## 让助手主动管理记忆（M2）

开启内部通道（`server/.env` 设置 `MCP_INTERNAL_TOKEN`）后，助手在对话中可主动：
- **memory_search**：查你的相关记忆；
- **memory_add**：把你明确要它记住的事实存入；
- **memory_delete**：删除一条记忆。

这些操作作用域仍按用户，且经后端统一管理（不直接写记忆库）。
```

- [ ] **Step 3: CLAUDE.md** — in the AI Agent Chat section's memory sentence, append:
```
M2：MCP 工具 `memory_search/add/delete`（`mcp-server/tools/memory.py`）经 Flask 内部端点 `/ai/memory/internal/*`（`MCP_INTERNAL_TOKEN` 鉴权，`routes/ai_memory_internal.py`）转发，Flask 仍是 Chroma 唯一写入方。
```

- [ ] **Step 4: Commit:**
```
cd E:/wsl/check/check-manage
git add server/.env.example docs/user-guide/ai/long-term-memory.md CLAUDE.md
git commit -m "docs(memory): MCP memory tools config + guide (M2)"
```

---

## 验收（M2）
- [ ] `pytest tests/test_ai_memory_internal.py`（Flask）全绿；`mcp-server` 套件全绿。
- [ ] token 缺失/为空 → 内部端点 403（默认禁用，安全）。
- [ ] 既有后端 + mcp-server 测试无回归。
- [ ] 手动（可选）：设 `MCP_INTERNAL_TOKEN`，启 Flask+MCP+OpenCode，让 agent 调 `memory_add` 再 `memory_search` 验证闭环。
