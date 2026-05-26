# AI Agent Frontend — M1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the minimum-viable closed loop of the AI Agent side drawer: a user can open a fresh session, send a message, and receive a streamed Agent reply that may call the `list_collections` MCP tool. Messages and the OpenCode session are persisted in the database; UI-level resume (loading prior sessions back into the drawer) ships in M2 with the SessionList component.

**Architecture:** Browser ↔ Flask (gateway + SSE proxy) ↔ OpenCode (`opencode serve`, server-hosted) ↔ MCP server (standalone FastAPI). No file uploads, no session list UI, no tool-result custom renderers — those are M2/M3.

**Tech Stack:** Vue 3 + TypeScript + Element Plus + Pinia, Flask + psycopg2, FastAPI + `mcp` Python SDK, PostgreSQL, Playwright.

**Spec:** `docs/superpowers/specs/2026-05-26-ai-agent-frontend-design.md` (§§1-5, §6.1-6.2, §6.5, §7 items 1/3/7/8/9/11, §8, §9).

**Operational pre-req for any developer running this plan locally:**
- `npm i -g opencode-ai` and a valid LLM provider API key configured in `~/.config/opencode/config.json`
- `opencode serve --port 4096` running before backend tests that hit it
- Tests that exercise the OpenCode HTTP layer mock `requests`; only the smoke test (Task 21) needs a real OpenCode

---

## File Structure

**Created:**
| Path | Responsibility |
|------|---------------|
| `mcp-server/pyproject.toml` | Standalone Python project metadata + deps |
| `mcp-server/main.py` | FastAPI app + MCP HTTP transport registration |
| `mcp-server/db.py` | Own psycopg2 pool (same DSN env vars as Flask) |
| `mcp-server/auth.py` | `validate_session_token(token) → user_dict` |
| `mcp-server/context.py` | `ToolContext` dataclass + helpers |
| `mcp-server/tools/__init__.py` | `register_all(server)` |
| `mcp-server/tools/list_collections.py` | The one M1 tool |
| `mcp-server/tests/conftest.py` | DB fixture pattern matching Flask side |
| `mcp-server/tests/test_auth.py` | Token validation cases |
| `mcp-server/tests/test_list_collections.py` | Tool behaviour + RBAC |
| `server/utils/opencode_client.py` | Thin HTTP/SSE wrapper around `opencode serve` |
| `server/utils/workspace.py` | Workspace dir mkdir/cleanup/path-traversal defense |
| `server/utils/session_token.py` | opaque token generate/validate/revoke/expire |
| `server/routes/ai_chat.py` | Flask blueprint with 5 M1 routes |
| `server/tests/test_opencode_client.py` | mock'd HTTP + SSE iterator tests |
| `server/tests/test_workspace.py` | Path traversal defense |
| `server/tests/test_ai_session_token.py` | Token lifecycle |
| `server/tests/test_routes_ai_chat.py` | All 5 routes + RBAC |
| `src/api/aiChat.ts` | Axios + EventSource wrappers |
| `src/api/__tests__/aiChat.test.ts` | EventSource reconnect test |
| `src/stores/aiChat.ts` | Pinia store |
| `src/stores/__tests__/aiChat.test.ts` | Store behaviour |
| `src/components/ai-chat/AiChatDrawer.vue` | el-drawer shell |
| `src/components/ai-chat/MessageList.vue` | Scrollable message stream |
| `src/components/ai-chat/MessageItem.vue` | Single message dispatch (user/assistant only in M1) |
| `src/components/ai-chat/MarkdownView.vue` | md-editor-v3 readonly wrapper |
| `src/components/ai-chat/ChatInput.vue` | Text input + send button |
| `e2e/ai-chat-smoke.spec.ts` | Playwright single smoke |
| `playwright.config.ts` | Minimum Playwright config |

**Modified:**
| Path | Change |
|------|--------|
| `server/init_db.py` | Append DDL for `ai_chat_sessions` + `ai_chat_messages` |
| `server/config.py` | Add 5 env vars (workspace root, OpenCode URL, MCP URL, TTL, quota) |
| `server/app.py` | Register `ai_chat_bp` BEFORE `dynamic_bp` |
| `src/api/index.ts` | Re-export `./aiChat` |
| `src/layouts/MainLayout.vue` | Add "AI 助手" header button + mount `<AiChatDrawer />` |
| `package.json` | Add `test:e2e` script + `@playwright/test` devDep |

---

## Task 1 — DDL: add `ai_chat_sessions` + `ai_chat_messages`

**Files:**
- Modify: `server/init_db.py` (append to the `DDL` string)
- Test: manual via `python init_db.py` against an empty DB

- [ ] **Step 1: Read current `init_db.py` tail to find safe append location**

Open `server/init_db.py`. The `DDL = """..."""` block lists every `CREATE TABLE IF NOT EXISTS`. Find the closing `"""` of that block.

- [ ] **Step 2: Append the two table definitions inside the `DDL` string**

Insert just before the closing `"""`:

```sql

CREATE TABLE IF NOT EXISTS ai_chat_sessions (
    id                  VARCHAR(100) PRIMARY KEY,
    user_id             VARCHAR(100) NOT NULL,
    title               VARCHAR(500),
    opencode_session_id VARCHAR(200),
    workspace_path      TEXT NOT NULL,
    session_token       VARCHAR(64) NOT NULL UNIQUE,
    token_expires_at    TIMESTAMPTZ NOT NULL,
    project_menu_id     VARCHAR(100),
    branch_id           VARCHAR(100) DEFAULT 'main',
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    last_active_at      TIMESTAMPTZ DEFAULT NOW(),
    status              VARCHAR(20) DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_chat_sess_user
    ON ai_chat_sessions(user_id, last_active_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_sess_token
    ON ai_chat_sessions(session_token);

CREATE TABLE IF NOT EXISTS ai_chat_messages (
    id          VARCHAR(100) PRIMARY KEY,
    session_id  VARCHAR(100) NOT NULL REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
    role        VARCHAR(20) NOT NULL,
    content     JSONB NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chat_msg_sess
    ON ai_chat_messages(session_id, created_at);
```

- [ ] **Step 3: Run init_db.py and verify both tables exist**

```bash
cd server && python init_db.py
psql -h localhost -U postgres -d casemanage -c "\dt ai_chat_*"
```

Expected output includes both `ai_chat_sessions` and `ai_chat_messages`.

- [ ] **Step 4: Commit**

```bash
git add server/init_db.py
git commit -m "feat(ai-chat): add ai_chat_sessions and ai_chat_messages DDL"
```

---

## Task 2 — MCP server skeleton (FastAPI + `mcp` SDK)

**Files:**
- Create: `mcp-server/pyproject.toml`
- Create: `mcp-server/main.py`
- Test: `curl http://127.0.0.1:3003/health`

- [ ] **Step 1: Create `mcp-server/pyproject.toml`**

```toml
[project]
name = "check-manage-mcp"
version = "0.1.0"
description = "Standalone MCP server exposing platform data to AI agents"
requires-python = ">=3.10"
dependencies = [
    "mcp>=1.0.0",
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
    "psycopg2-binary>=2.9",
]

[project.optional-dependencies]
test = ["pytest>=8", "httpx>=0.27"]
```

- [ ] **Step 2: Install deps in a venv**

```bash
cd mcp-server
python -m venv .venv
.venv\Scripts\activate          # PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[test]"
```

- [ ] **Step 3: Create `mcp-server/main.py` with health endpoint only**

```python
"""MCP server entry point.

Hosts an MCP HTTP transport at /mcp; exposes /health for liveness.
Tools are registered in tools/__init__.py (added in Task 6).
"""

from fastapi import FastAPI
import uvicorn

app = FastAPI(title="check-manage MCP server")


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=3003)
```

- [ ] **Step 4: Run and curl health**

```bash
cd mcp-server
python main.py &
curl http://127.0.0.1:3003/health
```

Expected: `{"status":"ok"}`. Stop the process.

- [ ] **Step 5: Commit**

```bash
git add mcp-server/pyproject.toml mcp-server/main.py
git commit -m "feat(mcp): scaffold FastAPI MCP server with /health"
```

---

## Task 3 — MCP server: DB connection pool

**Files:**
- Create: `mcp-server/db.py`
- Create: `mcp-server/tests/conftest.py`
- Test: `mcp-server/tests/test_db_smoke.py` (will be deleted after Task 4)

- [ ] **Step 1: Write `mcp-server/db.py`**

```python
"""DB pool for MCP server. Same DSN env vars as Flask `server/config.py`."""

import os
from contextlib import contextmanager
import psycopg2
import psycopg2.extras
import psycopg2.pool

_pool = psycopg2.pool.SimpleConnectionPool(
    1, 5,
    host=os.getenv("DB_HOST", "localhost"),
    dbname=os.getenv("DB_NAME", "casemanage"),
    user=os.getenv("DB_USER", "postgres"),
    password=os.getenv("DB_PASSWORD", "jay123"),
    port=int(os.getenv("DB_PORT", "5432")),
)


@contextmanager
def get_db():
    conn = _pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)
```

- [ ] **Step 2: Write `mcp-server/tests/conftest.py`**

```python
import sys
import os
import pytest
from unittest.mock import MagicMock
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def mock_cursor():
    cur = MagicMock()
    cur.fetchall.return_value = []
    cur.fetchone.return_value = None
    return cur


@pytest.fixture
def mock_conn(mock_cursor):
    conn = MagicMock()
    conn.cursor.return_value = mock_cursor
    conn.__enter__ = lambda self: conn
    conn.__exit__ = lambda self, *a: None
    return conn


@pytest.fixture
def fake_db(mock_conn):
    @contextmanager
    def _fake():
        yield mock_conn
    return _fake
```

- [ ] **Step 3: Sanity test that conftest loads**

Create `mcp-server/tests/test_smoke.py`:

```python
def test_fixtures_load(mock_cursor, mock_conn):
    assert mock_cursor.fetchall() == []
    assert mock_conn.cursor() is mock_cursor
```

Run:

```bash
cd mcp-server
pytest tests/test_smoke.py -v
```

Expected: 1 passed.

- [ ] **Step 4: Delete the smoke test (it served its purpose)**

```bash
rm mcp-server/tests/test_smoke.py
```

- [ ] **Step 5: Commit**

```bash
git add mcp-server/db.py mcp-server/tests/conftest.py
git commit -m "feat(mcp): add psycopg2 pool and pytest conftest"
```

---

## Task 4 — MCP server: `validate_session_token`

**Files:**
- Create: `mcp-server/auth.py`
- Create: `mcp-server/tests/test_auth.py`

- [ ] **Step 1: Write failing test `mcp-server/tests/test_auth.py`**

```python
"""Tests for mcp-server auth.validate_session_token."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


def test_valid_token_returns_user(fake_db, mock_cursor):
    mock_cursor.fetchone.return_value = (
        "sess_123", "user-1", "developer",
        datetime.now(timezone.utc) + timedelta(hours=1),
    )
    with patch("auth.get_db", fake_db):
        from auth import validate_session_token
        result = validate_session_token("tok_valid")
    assert result == {
        "session_id": "sess_123",
        "user_id": "user-1",
        "role": "developer",
    }


def test_expired_token_raises(fake_db, mock_cursor):
    mock_cursor.fetchone.return_value = (
        "sess_123", "user-1", "developer",
        datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    with patch("auth.get_db", fake_db):
        from auth import validate_session_token, TokenExpired
        with pytest.raises(TokenExpired):
            validate_session_token("tok_expired")


def test_unknown_token_raises(fake_db, mock_cursor):
    mock_cursor.fetchone.return_value = None
    with patch("auth.get_db", fake_db):
        from auth import validate_session_token, TokenInvalid
        with pytest.raises(TokenInvalid):
            validate_session_token("tok_missing")
```

- [ ] **Step 2: Run, confirm it fails**

```bash
cd mcp-server && pytest tests/test_auth.py -v
```

Expected: 3 failed (module `auth` not found).

- [ ] **Step 3: Implement `mcp-server/auth.py`**

```python
"""Validate opaque session tokens issued by Flask, by looking them up
in ai_chat_sessions joined to users.

Pure function over DB — no state, no side effects beyond a SELECT.
"""

from datetime import datetime, timezone
from db import get_db


class TokenInvalid(Exception):
    pass


class TokenExpired(Exception):
    pass


def validate_session_token(token: str) -> dict:
    if not token:
        raise TokenInvalid("missing token")

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT s.id, s.user_id, u.role, s.token_expires_at
            FROM ai_chat_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.session_token = %s
              AND s.status = 'active'
            """,
            (token,),
        )
        row = cur.fetchone()

    if not row:
        raise TokenInvalid("unknown token")

    session_id, user_id, role, expires_at = row
    if expires_at <= datetime.now(timezone.utc):
        raise TokenExpired("token expired")

    return {"session_id": session_id, "user_id": user_id, "role": role}
```

- [ ] **Step 4: Run, confirm pass**

```bash
cd mcp-server && pytest tests/test_auth.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add mcp-server/auth.py mcp-server/tests/test_auth.py
git commit -m "feat(mcp): validate_session_token with TokenInvalid/Expired"
```

---

## Task 5 — MCP server: `ToolContext` + tool registration plumbing

**Files:**
- Create: `mcp-server/context.py`
- Create: `mcp-server/tools/__init__.py`
- Modify: `mcp-server/main.py`

- [ ] **Step 1: Write `mcp-server/context.py`**

```python
"""ToolContext: per-call user identity passed to each tool handler."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolContext:
    session_id: str
    user_id: str
    role: str


def context_from_token(token: str) -> ToolContext:
    from auth import validate_session_token
    d = validate_session_token(token)
    return ToolContext(
        session_id=d["session_id"],
        user_id=d["user_id"],
        role=d["role"],
    )
```

- [ ] **Step 2: Write `mcp-server/tools/__init__.py` (registry shell)**

```python
"""Tool registry. Each tool module exposes `register(server)`."""

from mcp.server import Server


def register_all(server: Server) -> None:
    from tools.list_collections import register as register_list_collections
    register_list_collections(server)
```

- [ ] **Step 3: Update `mcp-server/main.py` to mount MCP transport**

Replace the entire file with:

```python
"""MCP server entry point.

Hosts an MCP Streamable-HTTP transport at /mcp; exposes /health for liveness.
Tools are registered in tools/__init__.py.
"""

from fastapi import FastAPI
from mcp.server import Server
from mcp.server.streamable_http import StreamableHTTPSessionManager
import uvicorn

mcp_server = Server("check-manage-mcp")

# Defer tool registration to keep import graph clean
from tools import register_all
register_all(mcp_server)

session_manager = StreamableHTTPSessionManager(app=mcp_server, stateless=True)

app = FastAPI(title="check-manage MCP server")


@app.get("/health")
def health():
    return {"status": "ok"}


# Mount MCP at /mcp — the StreamableHTTPSessionManager exposes an ASGI handler
app.mount("/mcp", session_manager.handle_request)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=3003)
```

> **NOTE for implementer:** the exact MCP-SDK API for mounting Streamable-HTTP under FastAPI may differ across SDK minor versions. If the import path above fails, consult the installed `mcp` package's `__init__.py` and adjust. The interface contract for us: `POST /mcp?token=…` → MCP protocol exchange.

- [ ] **Step 4: Start the server, confirm /health still works**

```bash
cd mcp-server && python main.py &
curl http://127.0.0.1:3003/health
```

Expected `{"status":"ok"}`. (MCP endpoint will 4xx on a bare GET — that's fine; real verification comes in Task 6.)

- [ ] **Step 5: Commit**

```bash
git add mcp-server/context.py mcp-server/tools/__init__.py mcp-server/main.py
git commit -m "feat(mcp): wire ToolContext + tool registry + Streamable-HTTP mount"
```

---

## Task 6 — MCP tool: `list_collections`

**Files:**
- Create: `mcp-server/tools/list_collections.py`
- Create: `mcp-server/tests/test_list_collections.py`

- [ ] **Step 1: Write failing test**

```python
"""Tests for tools.list_collections."""

from unittest.mock import patch
import pytest


def _ctx(role="developer"):
    from context import ToolContext
    return ToolContext(session_id="s1", user_id="u1", role=role)


def test_returns_collections_visible_to_role(fake_db, mock_cursor):
    # Two menus; one allows developer, the other only admin
    mock_cursor.fetchall.return_value = [
        ("page-orders",  "订单", ["developer", "admin"], [{"fieldName": "no", "label": "单号"}]),
        ("page-secrets", "保密", ["admin"],              [{"fieldName": "x"}]),
    ]
    with patch("tools.list_collections.get_db", fake_db):
        from tools.list_collections import handle
        result = handle({}, _ctx("developer"))
    assert [r["collection"] for r in result] == ["orders"]
    assert result[0]["label"] == "订单"
    assert result[0]["fields"][0]["fieldName"] == "no"


def test_admin_sees_all(fake_db, mock_cursor):
    mock_cursor.fetchall.return_value = [
        ("page-orders",  "订单", ["developer"], []),
        ("page-secrets", "保密", ["admin"],     []),
    ]
    with patch("tools.list_collections.get_db", fake_db):
        from tools.list_collections import handle
        result = handle({}, _ctx("admin"))
    assert sorted(r["collection"] for r in result) == ["orders", "secrets"]
```

- [ ] **Step 2: Run, confirm failure**

```bash
cd mcp-server && pytest tests/test_list_collections.py -v
```

Expected: 2 failed (module not found).

- [ ] **Step 3: Implement `mcp-server/tools/list_collections.py`**

```python
"""Tool: list_collections — returns data pages visible to the current user."""

import mcp.types as types
from mcp.server import Server
from db import get_db
from context import ToolContext


_TOOL_NAME = "list_collections"


def handle(_input: dict, ctx: ToolContext) -> list[dict]:
    """Return [{collection, label, fields[]}] for pages this role can see."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT m.page_id, m.name, m.roles, pc.fields
            FROM menus m
            JOIN page_configs pc ON pc.id = m.page_id
            WHERE m.page_id IS NOT NULL
            """
        )
        rows = cur.fetchall()

    result = []
    for page_id, name, roles, fields in rows:
        if ctx.role not in (roles or []):
            continue
        collection = page_id[5:] if page_id.startswith("page-") else page_id
        result.append({
            "collection": collection,
            "label": name,
            "fields": fields or [],
        })
    return result


def register(server: Server) -> None:
    @server.list_tools()
    async def _list_tools():
        return [
            types.Tool(
                name=_TOOL_NAME,
                description="List business data collections visible to the caller.",
                inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
            ),
        ]

    @server.call_tool()
    async def _call(name: str, arguments: dict):
        if name != _TOOL_NAME:
            raise ValueError(f"unknown tool: {name}")
        from main import _resolve_context  # imported lazily to avoid cycle
        ctx = _resolve_context()
        result = handle(arguments or {}, ctx)
        return [types.TextContent(type="text", text=str(result))]
```

- [ ] **Step 4: Add `_resolve_context` helper to `mcp-server/main.py`**

Open `main.py`, after `mcp_server = Server(...)`, add:

```python
from contextvars import ContextVar
from context import context_from_token, ToolContext

_current_ctx: ContextVar[ToolContext | None] = ContextVar("mcp_ctx", default=None)


def _resolve_context() -> ToolContext:
    ctx = _current_ctx.get()
    if ctx is None:
        raise PermissionError("no token in request")
    return ctx
```

And wrap the `/mcp` mount in middleware that extracts `?token=…`, validates, and sets the context var:

```python
from starlette.middleware.base import BaseHTTPMiddleware


class TokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path.startswith("/mcp"):
            token = request.query_params.get("token", "")
            try:
                ctx = context_from_token(token)
            except Exception as e:
                from starlette.responses import JSONResponse
                return JSONResponse({"error": str(e)}, status_code=401)
            tok = _current_ctx.set(ctx)
            try:
                return await call_next(request)
            finally:
                _current_ctx.reset(tok)
        return await call_next(request)


app.add_middleware(TokenMiddleware)
```

- [ ] **Step 5: Run unit tests, confirm pass**

```bash
cd mcp-server && pytest tests/ -v
```

Expected: all 5 pass (3 auth + 2 list_collections).

- [ ] **Step 6: Commit**

```bash
git add mcp-server/tools/list_collections.py mcp-server/tests/test_list_collections.py mcp-server/main.py
git commit -m "feat(mcp): list_collections tool with role-based RBAC + token middleware"
```

---

## Task 7 — Flask: `session_token` utility

**Files:**
- Create: `server/utils/session_token.py`
- Create: `server/tests/test_ai_session_token.py`

- [ ] **Step 1: Write failing test**

```python
"""Tests for server/utils/session_token.py."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock


def _patch_db(mock_conn):
    from contextlib import contextmanager
    @contextmanager
    def fake():
        yield mock_conn
    return patch("utils.session_token.get_db", fake)


def test_generate_returns_unique_urlsafe(mock_conn, mock_cursor):
    with _patch_db(mock_conn):
        from utils.session_token import generate_token
        a = generate_token("sess_1", ttl_hours=24)
        b = generate_token("sess_2", ttl_hours=24)
    assert a != b
    assert len(a) >= 32
    assert "/" not in a and "+" not in a


def test_renew_extends_expiry(mock_conn, mock_cursor):
    with _patch_db(mock_conn):
        from utils.session_token import renew_token
        renew_token("sess_1", ttl_hours=24)
    args, _ = mock_cursor.execute.call_args
    sql = args[0]
    assert "UPDATE ai_chat_sessions" in sql
    assert "token_expires_at" in sql


def test_revoke_marks_status_revoked(mock_conn, mock_cursor):
    with _patch_db(mock_conn):
        from utils.session_token import revoke_token
        revoke_token("sess_1")
    args, _ = mock_cursor.execute.call_args
    assert "status" in args[0]
    assert "revoked" in args[1] or "revoked" in args[0]
```

- [ ] **Step 2: Confirm fail**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_session_token.py -v
```

Expected: 3 errors / failed.

- [ ] **Step 3: Implement `server/utils/session_token.py`**

```python
"""Opaque session token: generate, renew, revoke.

Tokens are urlsafe base64, 32 bytes of entropy (~43 chars).
Stored in ai_chat_sessions.session_token with token_expires_at.
"""

import secrets
from datetime import datetime, timedelta, timezone
from db import get_db


def generate_token(session_id: str, ttl_hours: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ai_chat_sessions "
            "SET session_token = %s, token_expires_at = %s "
            "WHERE id = %s",
            (token, expires, session_id),
        )
    return token


def renew_token(session_id: str, ttl_hours: int) -> None:
    expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ai_chat_sessions "
            "SET token_expires_at = %s, last_active_at = NOW() "
            "WHERE id = %s",
            (expires, session_id),
        )


def revoke_token(session_id: str) -> None:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ai_chat_sessions "
            "SET status = 'revoked', token_expires_at = NOW() "
            "WHERE id = %s",
            (session_id,),
        )
```

- [ ] **Step 4: Run, confirm pass**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_session_token.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add server/utils/session_token.py server/tests/test_ai_session_token.py
git commit -m "feat(ai-chat): session_token generate/renew/revoke utility"
```

---

## Task 8 — Flask: `workspace` utility

**Files:**
- Create: `server/utils/workspace.py`
- Create: `server/tests/test_workspace.py`

- [ ] **Step 1: Write failing test**

```python
"""Tests for server/utils/workspace.py path-traversal defense and mkdir."""

import os
import pytest
from pathlib import Path


def test_create_session_workspace_makes_uploads_outputs(tmp_path):
    from utils.workspace import create_session_workspace
    p = create_session_workspace(str(tmp_path), "user-1", "sess-1")
    assert (Path(p) / "uploads").is_dir()
    assert (Path(p) / "outputs").is_dir()
    assert Path(p).name == "sess-1"


def test_safe_resolve_rejects_traversal(tmp_path):
    from utils.workspace import safe_resolve, WorkspacePathError
    root = str(tmp_path)
    with pytest.raises(WorkspacePathError):
        safe_resolve(root, "../../etc/passwd")


def test_safe_resolve_rejects_absolute(tmp_path):
    from utils.workspace import safe_resolve, WorkspacePathError
    with pytest.raises(WorkspacePathError):
        safe_resolve(str(tmp_path), "/etc/passwd")


def test_safe_resolve_accepts_inside(tmp_path):
    from utils.workspace import safe_resolve
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "x.txt").write_text("hi")
    p = safe_resolve(str(tmp_path), "uploads/x.txt")
    assert Path(p).read_text() == "hi"


def test_cleanup_removes_session_dir(tmp_path):
    from utils.workspace import create_session_workspace, cleanup_session_workspace
    p = create_session_workspace(str(tmp_path), "u", "s")
    assert Path(p).exists()
    cleanup_session_workspace(str(tmp_path), "u", "s")
    assert not Path(p).exists()
```

- [ ] **Step 2: Confirm fail**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_workspace.py -v
```

Expected: 5 errors.

- [ ] **Step 3: Implement `server/utils/workspace.py`**

```python
"""Per-session workspace directory: create, cleanup, path-traversal defense."""

import shutil
from pathlib import Path


class WorkspacePathError(ValueError):
    """Raised when a user-supplied path escapes the workspace root."""


def session_path(workspace_root: str, user_id: str, session_id: str) -> Path:
    return Path(workspace_root) / user_id / session_id


def create_session_workspace(workspace_root: str, user_id: str, session_id: str) -> str:
    p = session_path(workspace_root, user_id, session_id)
    (p / "uploads").mkdir(parents=True, exist_ok=True)
    (p / "outputs").mkdir(parents=True, exist_ok=True)
    return str(p.resolve())


def cleanup_session_workspace(workspace_root: str, user_id: str, session_id: str) -> None:
    p = session_path(workspace_root, user_id, session_id)
    if p.exists():
        shutil.rmtree(p)


def safe_resolve(root: str, rel_path: str) -> str:
    """Resolve `rel_path` under `root`; raise if it escapes."""
    root_p = Path(root).resolve()
    if Path(rel_path).is_absolute():
        raise WorkspacePathError("absolute path not allowed")
    target = (root_p / rel_path).resolve()
    try:
        target.relative_to(root_p)
    except ValueError:
        raise WorkspacePathError(f"path escapes workspace: {rel_path}")
    return str(target)
```

- [ ] **Step 4: Run, confirm pass**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_workspace.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add server/utils/workspace.py server/tests/test_workspace.py
git commit -m "feat(ai-chat): workspace utility with path-traversal defense"
```

---

## Task 9 — Flask: `opencode_client` HTTP wrapper

**Files:**
- Create: `server/utils/opencode_client.py`
- Create: `server/tests/test_opencode_client.py`

- [ ] **Step 1: Write failing test**

```python
"""Tests for server/utils/opencode_client.py — mocked HTTP and SSE."""

from unittest.mock import patch, MagicMock
import pytest
import json


def test_create_session_posts_session_and_returns_id():
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"id": "oc_sess_42"}
    fake_resp.raise_for_status = MagicMock()

    with patch("utils.opencode_client.requests.post", return_value=fake_resp) as post:
        from utils.opencode_client import OpenCodeClient
        client = OpenCodeClient("http://127.0.0.1:4096")
        sid = client.create_session(cwd="/tmp/ws")
    assert sid == "oc_sess_42"
    args, kwargs = post.call_args
    assert args[0].endswith("/session")
    assert kwargs["json"].get("cwd") == "/tmp/ws"


def test_register_mcp_posts_url():
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.raise_for_status = MagicMock()
    with patch("utils.opencode_client.requests.post", return_value=fake_resp) as post:
        from utils.opencode_client import OpenCodeClient
        OpenCodeClient("http://127.0.0.1:4096").register_mcp(
            session_id="oc_sess_42",
            url="http://127.0.0.1:3003/mcp?token=t1",
        )
    args, kwargs = post.call_args
    assert args[0].endswith("/mcp")
    assert kwargs["json"]["url"].endswith("?token=t1")


def test_send_prompt_async_returns_immediately():
    fake_resp = MagicMock()
    fake_resp.status_code = 204
    fake_resp.raise_for_status = MagicMock()
    with patch("utils.opencode_client.requests.post", return_value=fake_resp):
        from utils.opencode_client import OpenCodeClient
        OpenCodeClient("http://127.0.0.1:4096").send_prompt_async(
            "oc_sess_42", "hello",
        )


def test_subscribe_events_yields_parsed_events():
    """SSE lines come as `event: ...\\n` and `data: {...}\\n\\n`."""
    raw = [
        b"event: message.part.delta\n",
        b'data: {"text":"hi"}\n',
        b"\n",
        b"event: message.finished\n",
        b'data: {}\n',
        b"\n",
    ]
    fake_resp = MagicMock()
    fake_resp.iter_lines = MagicMock(return_value=iter(raw))
    fake_resp.raise_for_status = MagicMock()
    fake_resp.__enter__ = lambda self: self
    fake_resp.__exit__ = lambda self, *a: None

    with patch("utils.opencode_client.requests.get", return_value=fake_resp):
        from utils.opencode_client import OpenCodeClient
        events = list(OpenCodeClient("http://127.0.0.1:4096").subscribe_events())
    assert len(events) == 2
    assert events[0]["event"] == "message.part.delta"
    assert events[0]["data"] == {"text": "hi"}
    assert events[1]["event"] == "message.finished"
```

- [ ] **Step 2: Confirm fail**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_opencode_client.py -v
```

Expected: 4 errors.

- [ ] **Step 3: Implement `server/utils/opencode_client.py`**

```python
"""Thin wrapper over `opencode serve` HTTP API.

Methods cover only what M1 needs. SSE is exposed as an iterator of
{"event": str, "data": dict} dicts so the route layer can re-emit them.
"""

import json
import requests
from typing import Iterator


class OpenCodeError(RuntimeError):
    pass


class OpenCodeClient:
    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def create_session(self, *, cwd: str, title: str = "") -> str:
        resp = requests.post(
            self._url("/session"),
            json={"cwd": cwd, "title": title},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["id"]

    def delete_session(self, opencode_session_id: str) -> None:
        try:
            resp = requests.delete(
                self._url(f"/session/{opencode_session_id}"),
                timeout=self.timeout,
            )
            if resp.status_code not in (200, 204, 404):
                resp.raise_for_status()
        except requests.RequestException as e:
            raise OpenCodeError(str(e))

    def register_mcp(self, *, session_id: str, url: str) -> None:
        resp = requests.post(
            self._url("/mcp"),
            json={"sessionId": session_id, "url": url, "type": "http"},
            timeout=self.timeout,
        )
        resp.raise_for_status()

    def send_prompt_async(self, opencode_session_id: str, content: str) -> None:
        resp = requests.post(
            self._url(f"/session/{opencode_session_id}/prompt_async"),
            json={"content": content},
            timeout=self.timeout,
        )
        resp.raise_for_status()

    def subscribe_events(self) -> Iterator[dict]:
        """Yield parsed SSE events. Caller is responsible for filtering by session."""
        with requests.get(
            self._url("/event"),
            stream=True,
            timeout=None,
            headers={"Accept": "text/event-stream"},
        ) as resp:
            resp.raise_for_status()
            event_name = None
            data_buf: list[str] = []
            for raw in resp.iter_lines():
                if raw is None:
                    continue
                line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                if line == "":
                    if event_name is not None:
                        joined = "".join(data_buf)
                        try:
                            data = json.loads(joined) if joined else {}
                        except json.JSONDecodeError:
                            data = {"_raw": joined}
                        yield {"event": event_name, "data": data}
                    event_name = None
                    data_buf = []
                elif line.startswith("event:"):
                    event_name = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    data_buf.append(line[len("data:"):].strip())
                # other SSE fields (id:, retry:) are ignored in M1
```

- [ ] **Step 4: Run, confirm pass**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_opencode_client.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add server/utils/opencode_client.py server/tests/test_opencode_client.py
git commit -m "feat(ai-chat): OpenCodeClient with sessions, prompt_async, SSE iterator"
```

---

## Task 10 — Flask: config additions

**Files:**
- Modify: `server/config.py`

- [ ] **Step 1: Add 5 env vars**

Open `server/config.py`. After the existing `OPEN_API_BRANCH` line, append:

```python

# AI chat / Agent integration
AI_WORKSPACE_ROOT     = os.getenv('AI_WORKSPACE_ROOT', os.path.join(os.path.dirname(__file__), '..', 'ai-workspaces'))
OPENCODE_BASE_URL     = os.getenv('OPENCODE_BASE_URL', 'http://127.0.0.1:4096')
MCP_SERVER_URL        = os.getenv('MCP_SERVER_URL',    'http://127.0.0.1:3003')
AI_SESSION_TTL_HOURS  = _to_int(os.getenv('AI_SESSION_TTL_HOURS'), 24)
AI_WORKSPACE_QUOTA_MB = _to_int(os.getenv('AI_WORKSPACE_QUOTA_MB'), 200)
```

- [ ] **Step 2: Verify Python loads it**

```bash
cd server && python -c "import config; print(config.OPENCODE_BASE_URL, config.AI_SESSION_TTL_HOURS)"
```

Expected: `http://127.0.0.1:4096 24`

- [ ] **Step 3: Commit**

```bash
git add server/config.py
git commit -m "feat(ai-chat): add 5 env vars for OpenCode/MCP/workspace"
```

---

## Task 11 — Flask route: `POST /ai/chat/sessions`

**Files:**
- Create: `server/routes/ai_chat.py`
- Create: `server/tests/test_routes_ai_chat.py`

- [ ] **Step 1: Write failing test for the create-session route**

```python
"""Tests for server/routes/ai_chat.py."""

import json
import sys
import os
import pytest
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from auth import create_token


def _make_mock_db(mock_conn):
    @contextmanager
    def fake_get_db():
        yield mock_conn
    return fake_get_db


@pytest.fixture
def setup(mock_conn, mock_cursor, tmp_path):
    fake_db = _make_mock_db(mock_conn)
    fake_client = MagicMock()
    fake_client.create_session.return_value = "oc_sess_42"
    fake_client.register_mcp.return_value = None

    patches = [
        patch('db.get_db', fake_db),
        patch('db.pool', MagicMock()),
        patch('routes.ai_chat.get_db', fake_db),
        patch('utils.session_token.get_db', fake_db),
        patch('routes.ai_chat.OpenCodeClient', return_value=fake_client),
        patch('config.AI_WORKSPACE_ROOT', str(tmp_path)),
        patch('routes.ai_chat.AI_WORKSPACE_ROOT', str(tmp_path)),
    ]
    for p in patches:
        p.start()

    from app import app
    app.config['TESTING'] = True
    dev = create_token({'id': 'user-1', 'username': 'dev', 'role': 'developer'})
    guest = create_token({'id': 'user-2', 'username': 'g', 'role': 'guest'})

    yield (
        app.test_client(), mock_cursor, fake_client,
        {'Authorization': f'Bearer {dev}'},
        {'Authorization': f'Bearer {guest}'},
        tmp_path,
    )

    for p in patches:
        p.stop()


def test_create_session_201_returns_id_title_workspace(setup):
    client, cursor, oc, dev_h, _, ws_root = setup
    resp = client.post('/ai/chat/sessions', json={}, headers=dev_h)
    assert resp.status_code == 201
    body = resp.get_json()
    assert body['id'].startswith('sess_')
    assert body['title'] == '新会话'
    assert 'workspacePath' in body
    oc.create_session.assert_called_once()
    oc.register_mcp.assert_called_once()


def test_create_session_guest_403(setup):
    client, *_, _, guest_h, _ = setup
    resp = client.post('/ai/chat/sessions', json={}, headers=guest_h)
    assert resp.status_code == 403
```

- [ ] **Step 2: Confirm fail**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_ai_chat.py::test_create_session_201_returns_id_title_workspace -v
```

Expected: ImportError / ModuleNotFoundError for `routes.ai_chat`.

- [ ] **Step 3: Implement minimal `server/routes/ai_chat.py`**

```python
"""AI chat blueprint (M1: sessions + messages + SSE + history).

Routes registered:
    POST   /ai/chat/sessions             create_session
    GET    /ai/chat/sessions/:id/messages history
    POST   /ai/chat/sessions/:id/messages send_message
    GET    /ai/chat/sessions/:id/events   sse_proxy
    DELETE /ai/chat/sessions/:id          delete_session
"""

import json
import secrets
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, g as flask_g, Response, stream_with_context
from db import get_db
from auth import login_required, write_required
from utils.opencode_client import OpenCodeClient
from utils.workspace import create_session_workspace, cleanup_session_workspace
from utils.session_token import generate_token, revoke_token
from config import (
    AI_WORKSPACE_ROOT, OPENCODE_BASE_URL, MCP_SERVER_URL,
    AI_SESSION_TTL_HOURS,
)

ai_chat_bp = Blueprint('ai_chat', __name__, url_prefix='/ai/chat')


def _new_session_id() -> str:
    return 'sess_' + secrets.token_hex(6)


@ai_chat_bp.route('/sessions', methods=['POST'])
@write_required
def create_session():
    user = flask_g.current_user
    body = request.get_json(silent=True) or {}
    project_menu_id = body.get('projectMenuId')

    session_id = _new_session_id()
    workspace_path = create_session_workspace(
        AI_WORKSPACE_ROOT, user['userId'], session_id,
    )

    # 1) insert row (need a row before session_token utility can UPDATE it)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ai_chat_sessions "
            "(id, user_id, title, workspace_path, session_token, "
            " token_expires_at, project_menu_id, status) "
            "VALUES (%s, %s, %s, %s, %s, NOW() + INTERVAL '1 hour', %s, 'active')",
            (session_id, user['userId'], '新会话', workspace_path,
             '_pending_', project_menu_id),
        )
    # 2) overwrite the token via the dedicated utility (single source of truth for TTL math)
    token = generate_token(session_id, AI_SESSION_TTL_HOURS)

    # 3) ask OpenCode to start a session bound to this workspace
    client = OpenCodeClient(OPENCODE_BASE_URL)
    opencode_session_id = client.create_session(cwd=workspace_path, title='新会话')

    # 4) register our MCP server, scoped by token
    mcp_url = f"{MCP_SERVER_URL}/mcp?token={token}"
    client.register_mcp(session_id=opencode_session_id, url=mcp_url)

    # 5) persist opencode_session_id
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ai_chat_sessions SET opencode_session_id = %s WHERE id = %s",
            (opencode_session_id, session_id),
        )

    return jsonify({
        'id': session_id,
        'title': '新会话',
        'workspacePath': workspace_path,
    }), 201
```

- [ ] **Step 4: Register blueprint in `server/app.py`**

In `server/app.py`, add the import alongside other route imports:

```python
from routes.ai_chat import ai_chat_bp
```

In the `register_blueprint` sequence, insert **before** `app.register_blueprint(dynamic_bp)`:

```python
app.register_blueprint(ai_chat_bp)
```

- [ ] **Step 5: Run, confirm both tests pass**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_ai_chat.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add server/routes/ai_chat.py server/app.py server/tests/test_routes_ai_chat.py
git commit -m "feat(ai-chat): POST /ai/chat/sessions creates session + MCP registration"
```

---

## Task 12 — Flask route: `POST /ai/chat/sessions/:id/messages` + history

**Files:**
- Modify: `server/routes/ai_chat.py`
- Modify: `server/tests/test_routes_ai_chat.py`

- [ ] **Step 1: Add failing tests**

Append to `test_routes_ai_chat.py`:

```python
def test_send_message_persists_user_and_calls_opencode(setup):
    client, cursor, oc, dev_h, _, _ = setup
    # Make the session lookup succeed
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active')
    resp = client.post(
        '/ai/chat/sessions/sess_x/messages',
        json={'content': 'hello agent'},
        headers=dev_h,
    )
    assert resp.status_code == 202
    oc.send_prompt_async.assert_called_once_with('oc_sess_42', 'hello agent')

    # An INSERT into ai_chat_messages must have happened
    inserts = [c.args[0] for c in cursor.execute.call_args_list]
    assert any("INSERT INTO ai_chat_messages" in s for s in inserts)


def test_send_message_other_users_session_404(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = None  # not found for this user
    resp = client.post(
        '/ai/chat/sessions/sess_other/messages',
        json={'content': 'hi'},
        headers=dev_h,
    )
    assert resp.status_code == 404


def test_get_messages_returns_history(setup):
    client, cursor, oc, dev_h, _, _ = setup
    # owner check + history fetch
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active')
    cursor.fetchall.return_value = [
        ('msg_1', 'user',      [{'type': 'text', 'text': 'hi'}],   None),
        ('msg_2', 'assistant', [{'type': 'text', 'text': 'hey'}],  None),
    ]
    resp = client.get('/ai/chat/sessions/sess_x/messages', headers=dev_h)
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body['messages']) == 2
    assert body['messages'][0]['role'] == 'user'
```

- [ ] **Step 2: Confirm 3 new failures**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_ai_chat.py -v
```

Expected: 2 pass, 3 fail (routes not yet implemented).

- [ ] **Step 3: Add the two routes to `routes/ai_chat.py`**

Append:

```python
def _load_session_for_user(session_id: str, user_id: str):
    """Return (id, user_id, opencode_session_id, status) or None."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, user_id, opencode_session_id, status "
            "FROM ai_chat_sessions "
            "WHERE id = %s AND user_id = %s",
            (session_id, user_id),
        )
        return cur.fetchone()


@ai_chat_bp.route('/sessions/<sid>/messages', methods=['POST'])
@write_required
def send_message(sid):
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404

    body = request.get_json(force=True)
    content = (body.get('content') or '').strip()
    if not content:
        return jsonify({'error': 'content required', 'code': 'CONTENT_REQUIRED'}), 400

    msg_id = 'msg_' + secrets.token_hex(6)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ai_chat_messages (id, session_id, role, content) "
            "VALUES (%s, %s, 'user', %s)",
            (msg_id, sid, json.dumps([{'type': 'text', 'text': content}])),
        )

    OpenCodeClient(OPENCODE_BASE_URL).send_prompt_async(sess[2], content)
    return jsonify({'messageId': msg_id}), 202


@ai_chat_bp.route('/sessions/<sid>/messages', methods=['GET'])
@login_required
def get_messages(sid):
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404

    since = request.args.get('since')
    with get_db() as conn:
        cur = conn.cursor()
        if since:
            cur.execute(
                "SELECT id, role, content, created_at FROM ai_chat_messages "
                "WHERE session_id = %s AND id > %s "
                "ORDER BY created_at ASC",
                (sid, since),
            )
        else:
            cur.execute(
                "SELECT id, role, content, created_at FROM ai_chat_messages "
                "WHERE session_id = %s ORDER BY created_at ASC",
                (sid,),
            )
        rows = cur.fetchall()

    return jsonify({
        'messages': [
            {'id': r[0], 'role': r[1], 'content': r[2],
             'createdAt': r[3].isoformat() if r[3] else None}
            for r in rows
        ],
    })
```

- [ ] **Step 4: Run all 5 route tests**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_ai_chat.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add server/routes/ai_chat.py server/tests/test_routes_ai_chat.py
git commit -m "feat(ai-chat): POST/GET messages routes with ownership check"
```

---

## Task 13 — Flask route: `GET /ai/chat/sessions/:id/events` (SSE proxy)

**Files:**
- Modify: `server/routes/ai_chat.py`
- Modify: `server/tests/test_routes_ai_chat.py`

- [ ] **Step 1: Add failing test**

Append to `test_routes_ai_chat.py`:

```python
def test_sse_events_returns_event_stream_headers(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active')

    # OpenCode iterator yields one event then stops
    oc.subscribe_events.return_value = iter([
        {'event': 'message.part.delta', 'data': {'text': 'hi'}},
    ])

    resp = client.get('/ai/chat/sessions/sess_x/events', headers=dev_h)
    assert resp.status_code == 200
    assert resp.headers['Content-Type'].startswith('text/event-stream')
    body = b''.join(resp.response).decode('utf-8')
    assert 'event: message.part.delta' in body
    assert '"text": "hi"' in body
```

- [ ] **Step 2: Confirm fail**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_ai_chat.py::test_sse_events_returns_event_stream_headers -v
```

Expected: 404 — route doesn't exist.

- [ ] **Step 3: Add SSE proxy route to `routes/ai_chat.py`**

Append:

```python
def _format_sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _persist_assistant_message(session_id: str, content_parts: list[dict]) -> None:
    """Called when a message.finished event arrives. Records to DB."""
    msg_id = 'msg_' + secrets.token_hex(6)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ai_chat_messages (id, session_id, role, content) "
            "VALUES (%s, %s, 'assistant', %s)",
            (msg_id, session_id, json.dumps(content_parts)),
        )


@ai_chat_bp.route('/sessions/<sid>/events', methods=['GET'])
@login_required
def sse_events(sid):
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404

    opencode_session_id = sess[2]
    client = OpenCodeClient(OPENCODE_BASE_URL)

    def generate():
        buffered_parts: list[dict] = []
        try:
            for evt in client.subscribe_events():
                # Filter: only forward events for this session
                payload = evt.get('data') or {}
                if payload.get('sessionId') and payload['sessionId'] != opencode_session_id:
                    continue

                # Accumulate parts in memory for DB persistence at end-of-message
                if evt['event'] == 'message.part.delta':
                    buffered_parts.append({'type': 'text', 'text': payload.get('text', '')})
                elif evt['event'] == 'tool.use':
                    buffered_parts.append({
                        'type': 'tool_use',
                        'name': payload.get('name'),
                        'input': payload.get('input'),
                        'result': payload.get('result'),
                    })
                elif evt['event'] == 'message.finished':
                    try:
                        _persist_assistant_message(sid, buffered_parts)
                    except Exception:
                        pass  # don't break the stream on DB hiccup (§7 #9)
                    buffered_parts = []

                yield _format_sse(evt['event'], payload)
        except GeneratorExit:
            return

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )
```

- [ ] **Step 4: Run all route tests**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_ai_chat.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add server/routes/ai_chat.py server/tests/test_routes_ai_chat.py
git commit -m "feat(ai-chat): SSE proxy with per-session filter + assistant persist"
```

---

## Task 14 — Flask route: `DELETE /ai/chat/sessions/:id`

**Files:**
- Modify: `server/routes/ai_chat.py`
- Modify: `server/tests/test_routes_ai_chat.py`

- [ ] **Step 1: Add failing test**

Append to `test_routes_ai_chat.py`:

```python
def test_delete_session_cleans_everything(setup):
    client, cursor, oc, dev_h, _, ws_root = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active')
    # Pre-create the workspace dir so cleanup has something to remove
    target = ws_root / 'user-1' / 'sess_x' / 'uploads'
    target.mkdir(parents=True, exist_ok=True)

    resp = client.delete('/ai/chat/sessions/sess_x', headers=dev_h)
    assert resp.status_code == 204
    oc.delete_session.assert_called_once_with('oc_sess_42')
    assert not (ws_root / 'user-1' / 'sess_x').exists()

    # DB updates: revoke token + soft-flag (or DELETE)
    statements = [c.args[0] for c in cursor.execute.call_args_list]
    assert any('UPDATE ai_chat_sessions' in s and 'status' in s for s in statements) or \
           any('DELETE FROM ai_chat_sessions' in s for s in statements)
```

- [ ] **Step 2: Confirm fail**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_ai_chat.py::test_delete_session_cleans_everything -v
```

Expected: 405 / 404 / 500.

- [ ] **Step 3: Add the route**

Append to `routes/ai_chat.py`:

```python
@ai_chat_bp.route('/sessions/<sid>', methods=['DELETE'])
@write_required
def delete_session(sid):
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404

    opencode_session_id = sess[2]
    if opencode_session_id:
        try:
            OpenCodeClient(OPENCODE_BASE_URL).delete_session(opencode_session_id)
        except Exception:
            pass  # 404 from OpenCode = already gone (§7 #11)

    cleanup_session_workspace(AI_WORKSPACE_ROOT, user['userId'], sid)
    revoke_token(sid)

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ai_chat_sessions SET status = 'deleted' WHERE id = %s",
            (sid,),
        )
    return '', 204
```

- [ ] **Step 4: Run all route tests**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_ai_chat.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add server/routes/ai_chat.py server/tests/test_routes_ai_chat.py
git commit -m "feat(ai-chat): DELETE session — OpenCode + workspace + token + status"
```

---

## Task 15 — Frontend: `src/api/aiChat.ts`

**Files:**
- Create: `src/api/aiChat.ts`
- Modify: `src/api/index.ts`
- Create: `src/api/__tests__/aiChat.test.ts`

- [ ] **Step 1: Write failing test for `subscribeEvents` reconnect**

Create `src/api/__tests__/aiChat.test.ts`:

```typescript
/**
 * aiChat API tests: REST shims + EventSource reconnect logic.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('@/utils/request', () => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
}))

import { createEventStream } from '../aiChat'
import { post } from '@/utils/request'

class FakeEventSource {
  static last: FakeEventSource | null = null
  url: string
  onopen: ((e: Event) => void) | null = null
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: ((e: Event) => void) | null = null
  addEventListener = vi.fn()
  removeEventListener = vi.fn()
  close = vi.fn()
  constructor(url: string) {
    this.url = url
    FakeEventSource.last = this
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  // @ts-expect-error global
  globalThis.EventSource = FakeEventSource
})

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('createEventStream', () => {
  it('opens an EventSource at the right URL', () => {
    createEventStream('sess_1', { onEvent: () => {}, onError: () => {} })
    expect(FakeEventSource.last?.url).toMatch(/\/api\/ai\/chat\/sessions\/sess_1\/events/)
  })

  it('reconnects with exponential backoff after error (1s,2s,5s,10s)', async () => {
    const onError = vi.fn()
    const stream = createEventStream('sess_1', { onEvent: () => {}, onError })
    const delays = [1000, 2000, 5000, 10000]

    for (const d of delays) {
      FakeEventSource.last!.onerror?.(new Event('error'))
      await vi.advanceTimersByTimeAsync(d)
    }
    expect(onError).toHaveBeenCalledTimes(4)
    stream.close()
  })
})
```

- [ ] **Step 2: Confirm fail**

```bash
npx vitest run src/api/__tests__/aiChat.test.ts
```

Expected: module not found.

- [ ] **Step 3: Implement `src/api/aiChat.ts`**

```typescript
/**
 * AI Chat API layer.
 *
 * - REST shims over /api/ai/chat/* via the shared axios `request` util.
 * - `createEventStream` opens an EventSource that auto-reconnects on error
 *   with delays 1s → 2s → 5s → 10s (then stops and reports). Caller may
 *   close at any time.
 */

import { get, post, del } from '@/utils/request'

export interface AiSession {
  id: string
  title: string
  workspacePath: string
}

export interface AiMessage {
  id: string
  role: 'user' | 'assistant' | 'tool'
  content: AiContentPart[]
  createdAt?: string
}

export type AiContentPart =
  | { type: 'text'; text: string }
  | { type: 'tool_use'; name: string; input: unknown; result?: unknown }

export function createSession(projectMenuId?: string) {
  return post<AiSession>('/ai/chat/sessions', { projectMenuId })
}

export function deleteSession(id: string) {
  return del<void>(`/ai/chat/sessions/${encodeURIComponent(id)}`)
}

export function getMessages(id: string, since?: string) {
  const q = since ? `?since=${encodeURIComponent(since)}` : ''
  return get<{ messages: AiMessage[] }>(`/ai/chat/sessions/${encodeURIComponent(id)}/messages${q}`)
}

export function sendMessage(id: string, content: string) {
  return post<{ messageId: string }>(
    `/ai/chat/sessions/${encodeURIComponent(id)}/messages`,
    { content },
  )
}

export interface StreamHandlers {
  onEvent: (event: { event: string; data: unknown }) => void
  onError: (err: Event) => void
}

const RECONNECT_DELAYS_MS = [1000, 2000, 5000, 10000]

export function createEventStream(sessionId: string, h: StreamHandlers) {
  const url = `/api/ai/chat/sessions/${encodeURIComponent(sessionId)}/events`
  let es: EventSource | null = null
  let closed = false
  let attempt = 0
  let timer: ReturnType<typeof setTimeout> | null = null

  const open = () => {
    if (closed) return
    es = new EventSource(url)
    es.onmessage = (e) => {
      try {
        h.onEvent({ event: 'message', data: JSON.parse(e.data) })
      } catch {
        h.onEvent({ event: 'message', data: e.data })
      }
    }
    // Listen to known event names from spec §6.2
    for (const name of ['message.part.start', 'message.part.delta', 'tool.use', 'message.finished', 'error']) {
      es.addEventListener(name, (e: MessageEvent) => {
        try {
          h.onEvent({ event: name, data: JSON.parse(e.data) })
        } catch {
          h.onEvent({ event: name, data: e.data })
        }
      })
    }
    es.onerror = (err) => {
      h.onError(err)
      es?.close()
      if (closed) return
      if (attempt < RECONNECT_DELAYS_MS.length) {
        timer = setTimeout(open, RECONNECT_DELAYS_MS[attempt])
        attempt += 1
      }
    }
    es.onopen = () => { attempt = 0 }
  }

  open()

  return {
    close() {
      closed = true
      if (timer) clearTimeout(timer)
      es?.close()
    },
  }
}
```

- [ ] **Step 4: Re-export from `src/api/index.ts`**

Open `src/api/index.ts` and add at the end:

```typescript
export * from './aiChat'
```

- [ ] **Step 5: Run, confirm pass**

```bash
npx vitest run src/api/__tests__/aiChat.test.ts
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add src/api/aiChat.ts src/api/index.ts src/api/__tests__/aiChat.test.ts
git commit -m "feat(ai-chat): aiChat API layer with EventSource auto-reconnect"
```

---

## Task 16 — Frontend: `src/stores/aiChat.ts` Pinia store

**Files:**
- Create: `src/stores/aiChat.ts`
- Create: `src/stores/__tests__/aiChat.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
/**
 * aiChat store tests.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/aiChat', () => ({
  createSession: vi.fn(),
  deleteSession: vi.fn(),
  getMessages: vi.fn(),
  sendMessage: vi.fn(),
  createEventStream: vi.fn(() => ({ close: vi.fn() })),
}))

import { useAiChatStore } from '../aiChat'
import * as api from '@/api/aiChat'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('useAiChatStore', () => {
  it('createSession populates activeSession and opens stream', async () => {
    vi.mocked(api.createSession).mockResolvedValue({
      id: 'sess_1', title: '新会话', workspacePath: '/ws',
    })
    vi.mocked(api.getMessages).mockResolvedValue({ messages: [] })

    const store = useAiChatStore()
    await store.startNewSession()

    expect(store.activeSessionId).toBe('sess_1')
    expect(api.createEventStream).toHaveBeenCalledWith('sess_1', expect.any(Object))
  })

  it('SSE message.part.delta appends to streaming assistant message', async () => {
    vi.mocked(api.createSession).mockResolvedValue({
      id: 'sess_1', title: '新会话', workspacePath: '/ws',
    })
    vi.mocked(api.getMessages).mockResolvedValue({ messages: [] })

    let handlers: any
    vi.mocked(api.createEventStream).mockImplementation((_id, h) => {
      handlers = h
      return { close: vi.fn() }
    })

    const store = useAiChatStore()
    await store.startNewSession()

    handlers.onEvent({ event: 'message.part.delta', data: { text: 'he' } })
    handlers.onEvent({ event: 'message.part.delta', data: { text: 'llo' } })

    const msgs = store.messages['sess_1']
    expect(msgs).toHaveLength(1)
    expect(msgs[0].role).toBe('assistant')
    expect((msgs[0].content[0] as any).text).toBe('hello')
  })

  it('message.finished flips streaming flag off', async () => {
    vi.mocked(api.createSession).mockResolvedValue({
      id: 'sess_1', title: '新会话', workspacePath: '/ws',
    })
    vi.mocked(api.getMessages).mockResolvedValue({ messages: [] })

    let handlers: any
    vi.mocked(api.createEventStream).mockImplementation((_id, h) => {
      handlers = h
      return { close: vi.fn() }
    })

    const store = useAiChatStore()
    await store.startNewSession()
    handlers.onEvent({ event: 'message.part.delta', data: { text: 'hi' } })
    handlers.onEvent({ event: 'message.finished', data: {} })

    expect(store.streaming['sess_1']).toBe(false)
  })

  it('sendUserMessage pushes a user msg then calls API', async () => {
    vi.mocked(api.createSession).mockResolvedValue({
      id: 'sess_1', title: '新会话', workspacePath: '/ws',
    })
    vi.mocked(api.getMessages).mockResolvedValue({ messages: [] })
    vi.mocked(api.sendMessage).mockResolvedValue({ messageId: 'msg_1' })

    const store = useAiChatStore()
    await store.startNewSession()
    await store.sendUserMessage('how are you')

    expect(store.messages['sess_1'][0].role).toBe('user')
    expect(api.sendMessage).toHaveBeenCalledWith('sess_1', 'how are you')
    expect(store.streaming['sess_1']).toBe(true)
  })
})
```

- [ ] **Step 2: Confirm fail**

```bash
npx vitest run src/stores/__tests__/aiChat.test.ts
```

Expected: module not found.

- [ ] **Step 3: Implement `src/stores/aiChat.ts`**

```typescript
/**
 * AI Chat Pinia store.
 *
 * State shape per spec §5.4. M1 only handles a single active session
 * (no SessionList), but the state is already keyed by sessionId so M2
 * can add multi-session without rewrite.
 */

import { defineStore } from 'pinia'
import {
  createSession, deleteSession, getMessages, sendMessage, createEventStream,
  type AiMessage, type AiContentPart,
} from '@/api/aiChat'

interface AiSessionMeta {
  id: string
  title: string
  workspacePath: string
}

interface State {
  sessions: AiSessionMeta[]
  activeSessionId: string | null
  messages: Record<string, AiMessage[]>
  streaming: Record<string, boolean>
  drawerOpen: boolean
  _stream: { close(): void } | null
}

let _streamingAssistantMsgId: Record<string, string | null> = {}

export const useAiChatStore = defineStore('aiChat', {
  state: (): State => ({
    sessions: [],
    activeSessionId: null,
    messages: {},
    streaming: {},
    drawerOpen: false,
    _stream: null,
  }),

  actions: {
    toggleDrawer(open?: boolean) {
      this.drawerOpen = open ?? !this.drawerOpen
    },

    async startNewSession(projectMenuId?: string) {
      const meta = await createSession(projectMenuId)
      this.sessions.push(meta)
      this.activeSessionId = meta.id
      this.messages[meta.id] = []
      this.streaming[meta.id] = false
      const history = await getMessages(meta.id)
      this.messages[meta.id] = history.messages
      this._openStream(meta.id)
    },

    async sendUserMessage(content: string) {
      if (!this.activeSessionId) throw new Error('no active session')
      const sid = this.activeSessionId
      const userMsg: AiMessage = {
        id: 'local_' + Date.now(),
        role: 'user',
        content: [{ type: 'text', text: content }],
      }
      this.messages[sid].push(userMsg)
      this.streaming[sid] = true
      _streamingAssistantMsgId[sid] = null
      await sendMessage(sid, content)
    },

    async closeSession(id: string) {
      this._closeStream()
      await deleteSession(id)
      this.sessions = this.sessions.filter(s => s.id !== id)
      delete this.messages[id]
      delete this.streaming[id]
      if (this.activeSessionId === id) this.activeSessionId = null
    },

    _openStream(sid: string) {
      this._closeStream()
      this._stream = createEventStream(sid, {
        onEvent: ({ event, data }) => this._handleEvent(sid, event, data as any),
        onError: () => { /* api layer handles reconnect; UI banner in M2 */ },
      })
    },

    _closeStream() {
      this._stream?.close()
      this._stream = null
    },

    _handleEvent(sid: string, event: string, data: any) {
      switch (event) {
        case 'message.part.delta':
          this._appendAssistantDelta(sid, data?.text ?? '')
          break
        case 'tool.use':
          this._appendAssistantPart(sid, {
            type: 'tool_use',
            name: data?.name,
            input: data?.input,
            result: data?.result,
          })
          break
        case 'message.finished':
          this.streaming[sid] = false
          _streamingAssistantMsgId[sid] = null
          break
        case 'error':
          this.streaming[sid] = false
          break
      }
    },

    _appendAssistantDelta(sid: string, text: string) {
      const list = this.messages[sid] ?? (this.messages[sid] = [])
      let id = _streamingAssistantMsgId[sid]
      if (!id) {
        id = 'streaming_' + Date.now()
        _streamingAssistantMsgId[sid] = id
        list.push({ id, role: 'assistant', content: [{ type: 'text', text: '' }] })
      }
      const msg = list[list.length - 1]
      const lastPart = msg.content[msg.content.length - 1] as AiContentPart
      if (lastPart && lastPart.type === 'text') {
        lastPart.text += text
      } else {
        msg.content.push({ type: 'text', text })
      }
    },

    _appendAssistantPart(sid: string, part: AiContentPart) {
      const list = this.messages[sid] ?? (this.messages[sid] = [])
      let id = _streamingAssistantMsgId[sid]
      if (!id) {
        id = 'streaming_' + Date.now()
        _streamingAssistantMsgId[sid] = id
        list.push({ id, role: 'assistant', content: [] })
      }
      list[list.length - 1].content.push(part)
    },
  },
})
```

- [ ] **Step 4: Run, confirm pass**

```bash
npx vitest run src/stores/__tests__/aiChat.test.ts
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/stores/aiChat.ts src/stores/__tests__/aiChat.test.ts
git commit -m "feat(ai-chat): Pinia store with streaming delta accumulation"
```

---

## Task 17 — Frontend: `MarkdownView` + `MessageItem` + `MessageList`

**Files:**
- Create: `src/components/ai-chat/MarkdownView.vue`
- Create: `src/components/ai-chat/MessageItem.vue`
- Create: `src/components/ai-chat/MessageList.vue`

(No isolated unit tests for these — they're rendered cumulatively by the drawer integration test in Task 19. M2 adds tool-renderers and at that point ToolCallBubble gets its own test per spec §8.1.)

- [ ] **Step 1: Create `MarkdownView.vue` (readonly md-editor-v3 wrapper)**

```vue
<script setup lang="ts">
import { MdPreview } from 'md-editor-v3'
import 'md-editor-v3/lib/preview.css'

defineProps<{ text: string }>()
</script>

<template>
  <MdPreview :modelValue="text" />
</template>

<style scoped>
:deep(.md-editor-preview) {
  font-size: 14px;
}
</style>
```

- [ ] **Step 2: Create `MessageItem.vue`**

```vue
<script setup lang="ts">
import { computed } from 'vue'
import MarkdownView from './MarkdownView.vue'
import type { AiMessage, AiContentPart } from '@/api/aiChat'

const props = defineProps<{ message: AiMessage }>()

const textParts = computed(() =>
  props.message.content.filter((p): p is Extract<AiContentPart, { type: 'text' }> => p.type === 'text'),
)

const toolParts = computed(() =>
  props.message.content.filter((p): p is Extract<AiContentPart, { type: 'tool_use' }> => p.type === 'tool_use'),
)
</script>

<template>
  <div class="ai-message" :class="['ai-message--' + message.role]">
    <div v-for="(p, i) in textParts" :key="'t' + i" class="ai-message__text">
      <MarkdownView :text="p.text" />
    </div>
    <!-- M1: tool_use parts render as plain JSON; M2 introduces ToolCallBubble -->
    <pre v-for="(p, i) in toolParts" :key="'u' + i" class="ai-message__tool">
{{ '调用工具 ' + p.name + ': ' + JSON.stringify(p.input, null, 2) }}
{{ p.result !== undefined ? '结果: ' + JSON.stringify(p.result, null, 2) : '' }}
    </pre>
  </div>
</template>

<style scoped lang="scss">
.ai-message {
  padding: 8px 12px;
  margin: 6px 0;
  border-radius: 6px;
  &--user      { background: var(--el-color-primary-light-9); }
  &--assistant { background: var(--el-bg-color-page); }
  &--tool      { background: var(--el-color-info-light-9); }
}
.ai-message__tool {
  font-family: monospace;
  font-size: 12px;
  background: var(--el-color-info-light-9);
  padding: 6px;
  border-radius: 4px;
  white-space: pre-wrap;
}
</style>
```

- [ ] **Step 3: Create `MessageList.vue`**

```vue
<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import MessageItem from './MessageItem.vue'
import type { AiMessage } from '@/api/aiChat'

const props = defineProps<{ messages: AiMessage[] }>()
const scroller = ref<HTMLElement | null>(null)

watch(() => props.messages.length, async () => {
  await nextTick()
  if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
})
</script>

<template>
  <div ref="scroller" class="ai-message-list">
    <MessageItem v-for="m in messages" :key="m.id" :message="m" />
  </div>
</template>

<style scoped lang="scss">
.ai-message-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}
</style>
```

- [ ] **Step 4: Visually verify in isolation (manual)**

Open Storybook? No — project has none. Skip. Visual confirmation happens in Task 19 once mounted.

- [ ] **Step 5: Commit**

```bash
git add src/components/ai-chat/MarkdownView.vue src/components/ai-chat/MessageItem.vue src/components/ai-chat/MessageList.vue
git commit -m "feat(ai-chat): MarkdownView, MessageItem, MessageList components"
```

---

## Task 18 — Frontend: `ChatInput.vue`

**Files:**
- Create: `src/components/ai-chat/ChatInput.vue`

(M1: text-only, no file upload. The component is small enough that its behaviour is covered by the smoke test in Task 21.)

- [ ] **Step 1: Create `ChatInput.vue`**

```vue
<script setup lang="ts">
import { ref } from 'vue'
import { ElButton, ElInput } from 'element-plus'

const props = defineProps<{ disabled?: boolean }>()
const emit = defineEmits<{ (e: 'send', text: string): void }>()

const text = ref('')

function send() {
  const t = text.value.trim()
  if (!t || props.disabled) return
  emit('send', t)
  text.value = ''
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}
</script>

<template>
  <div class="ai-chat-input">
    <ElInput
      v-model="text"
      type="textarea"
      :rows="3"
      :disabled="disabled"
      placeholder="询问 Agent (Enter 发送, Shift+Enter 换行)"
      @keydown="onKey"
    />
    <div class="ai-chat-input__bar">
      <ElButton type="primary" :disabled="disabled || !text.trim()" @click="send">
        发送
      </ElButton>
    </div>
  </div>
</template>

<style scoped lang="scss">
.ai-chat-input {
  padding: 8px;
  border-top: 1px solid var(--el-border-color-light);
  &__bar { display: flex; justify-content: flex-end; margin-top: 6px; }
}
</style>
```

- [ ] **Step 2: Commit**

```bash
git add src/components/ai-chat/ChatInput.vue
git commit -m "feat(ai-chat): ChatInput with Enter-to-send"
```

---

## Task 19 — Frontend: `AiChatDrawer.vue` + mount in `MainLayout`

**Files:**
- Create: `src/components/ai-chat/AiChatDrawer.vue`
- Modify: `src/layouts/MainLayout.vue`

- [ ] **Step 1: Create `AiChatDrawer.vue`**

```vue
<script setup lang="ts">
import { computed, watch } from 'vue'
import { ElDrawer, ElButton, ElEmpty } from 'element-plus'
import { useAiChatStore } from '@/stores/aiChat'
import MessageList from './MessageList.vue'
import ChatInput from './ChatInput.vue'

const store = useAiChatStore()

const open = computed({
  get: () => store.drawerOpen,
  set: (v: boolean) => store.toggleDrawer(v),
})

const sid = computed(() => store.activeSessionId)
const messages = computed(() => (sid.value ? store.messages[sid.value] ?? [] : []))
const streaming = computed(() => (sid.value ? !!store.streaming[sid.value] : false))

async function onSend(text: string) {
  if (!sid.value) {
    await store.startNewSession()
  }
  await store.sendUserMessage(text)
}

async function startFresh() {
  await store.startNewSession()
}

watch(() => store.drawerOpen, async (v) => {
  // Auto-start a session the first time the drawer is opened, for a friendlier first run
  if (v && !sid.value) {
    try {
      await store.startNewSession()
    } catch {
      // surfaces via per-action error UI later; M1 swallows here so the drawer still opens
    }
  }
})
</script>

<template>
  <ElDrawer
    v-model="open"
    title="AI 助手"
    direction="rtl"
    size="480px"
    :destroy-on-close="false"
  >
    <div class="ai-drawer">
      <div v-if="!sid" class="ai-drawer__empty">
        <ElEmpty description="尚未开启会话">
          <ElButton type="primary" @click="startFresh">开启新会话</ElButton>
        </ElEmpty>
      </div>
      <template v-else>
        <MessageList :messages="messages" />
        <ChatInput :disabled="streaming" @send="onSend" />
      </template>
    </div>
  </ElDrawer>
</template>

<style scoped lang="scss">
.ai-drawer {
  display: flex;
  flex-direction: column;
  height: 100%;
  &__empty { display: flex; align-items: center; justify-content: center; height: 100%; }
}
</style>
```

- [ ] **Step 2: Read existing `MainLayout.vue` to find a safe injection point**

```bash
# locate the header bar block + the layout root
```

Find the file's header bar `<div>` (or `<el-header>`) and the layout's root template.

- [ ] **Step 3: Add the AI button + drawer to `MainLayout.vue`**

In the header bar add a button (place it next to other header actions; the exact selector depends on current markup):

```vue
<ElButton text @click="aiChat.toggleDrawer()">
  <el-icon><ChatDotRound /></el-icon>
  <span style="margin-left:4px">AI 助手</span>
</ElButton>
```

At the bottom of the root template (sibling to header/main), add:

```vue
<AiChatDrawer />
```

And in the script block:

```ts
import { ChatDotRound } from '@element-plus/icons-vue'
import { useAiChatStore } from '@/stores/aiChat'
import AiChatDrawer from '@/components/ai-chat/AiChatDrawer.vue'
const aiChat = useAiChatStore()
```

- [ ] **Step 4: Manually start dev server and verify**

```bash
npm run dev:all
```

In another terminal, ensure OpenCode + MCP server are up (see prerequisite notes at top of plan). Open `http://localhost:5173`, log in as admin, click "AI 助手", send "hello", verify a streamed response appears.

- [ ] **Step 5: Commit**

```bash
git add src/components/ai-chat/AiChatDrawer.vue src/layouts/MainLayout.vue
git commit -m "feat(ai-chat): AiChatDrawer mounted in MainLayout with header button"
```

---

## Task 20 — Playwright smoke test setup

**Files:**
- Create: `playwright.config.ts`
- Modify: `package.json`

- [ ] **Step 1: Install Playwright test runner**

```bash
npm i -D @playwright/test
npx playwright install chromium
```

- [ ] **Step 2: Create `playwright.config.ts` at repo root**

```typescript
import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  retries: 0,
  use: {
    baseURL: 'http://localhost:5173',
    headless: true,
  },
  webServer: {
    command: 'npm run dev:all',
    url: 'http://localhost:5173',
    reuseExistingServer: true,
    timeout: 120_000,
  },
})
```

- [ ] **Step 3: Add `test:e2e` script to `package.json`**

In `scripts`, add:

```json
"test:e2e": "playwright test"
```

- [ ] **Step 4: Commit**

```bash
git add playwright.config.ts package.json package-lock.json
git commit -m "chore(ai-chat): set up Playwright runner and config"
```

---

## Task 21 — Playwright: AI chat smoke

**Files:**
- Create: `e2e/ai-chat-smoke.spec.ts`

**Manual pre-req:** OpenCode + MCP server must be running; the test user `admin/admin123` must exist (seeded by `init_db.py`).

- [ ] **Step 1: Write the smoke test**

```typescript
import { test, expect } from '@playwright/test'

test('AI chat M1 smoke: open drawer, send message, receive streamed reply', async ({ page }) => {
  await page.goto('/')

  // Log in
  await page.fill('input[placeholder*="用户名"]', 'admin')
  await page.fill('input[type="password"]', 'admin123')
  await page.getByRole('button', { name: /登录|Login/ }).click()

  // Land on home, open AI drawer
  await page.getByRole('button', { name: /AI 助手/ }).click()

  // First-open auto-creates a session; wait for input to be enabled
  const input = page.getByPlaceholder(/询问 Agent/)
  await input.waitFor({ state: 'visible', timeout: 15_000 })

  // Send "hello"
  await input.fill('hello')
  await page.getByRole('button', { name: '发送' }).click()

  // User echo
  await expect(page.locator('.ai-message--user').last()).toContainText('hello')

  // Wait for an assistant reply (at least one .ai-message--assistant with non-empty content)
  await expect(page.locator('.ai-message--assistant').first()).toBeVisible({ timeout: 60_000 })
  const replyText = await page.locator('.ai-message--assistant').first().innerText()
  expect(replyText.trim().length).toBeGreaterThan(0)
})
```

- [ ] **Step 2: Run smoke**

```bash
# Pre-req: opencode serve and mcp-server are running
npm run test:e2e
```

Expected: 1 passed.

If it fails because Agent never replies, check (in order): (1) Flask logs show `POST /ai/chat/sessions` 201, (2) OpenCode logs show session created and prompt received, (3) browser DevTools shows EventSource open at `/api/ai/chat/sessions/.../events`.

- [ ] **Step 3: Commit**

```bash
git add e2e/ai-chat-smoke.spec.ts
git commit -m "test(ai-chat): Playwright smoke test for M1 send-and-receive"
```

---

## Final Step — Sanity sweep

- [ ] **Run full test suites end-to-end:**

```bash
# Frontend
npm run test
# Backend
npm run test:server
# MCP
cd mcp-server && pytest -v
# E2E (requires opencode + mcp running)
npm run test:e2e
```

All four should be green.

- [ ] **Smoke the dev environment manually** (UI + drawer + first session + one tool call). If anything regressed, fix forward — don't squash earlier commits.

- [ ] **Update CLAUDE.md** with a short paragraph in the Backend Structure / Frontend Structure sections noting the new `ai_chat_bp`, `mcp-server/`, and `src/components/ai-chat/` directories so future Claude Code sessions discover them.

- [ ] **Commit CLAUDE.md update:**

```bash
git add CLAUDE.md
git commit -m "docs: note AI chat M1 components in CLAUDE.md"
```

---

## Self-Review Notes

- **Spec coverage**: M1 scope per spec §10 milestone 1 — covered by Tasks 1-21. Out-of-M1 items (FilePanel, ToolCallBubble registry, SessionList, generate_inspection_case) explicitly deferred and noted in component comments.
- **Constraint test (§3.1)**: deferred to M2 (introduced together with `ToolCallBubble.vue` and `tool-renderers/`). M1 renders tool calls as plain JSON in MessageItem so there's nothing to constrain yet — call this out in the M2 plan.
- **Files that compose into a coherent unit**: Tasks 7-9 each deliver one isolated utility with its own test; Tasks 11-14 each add one route layered onto the previous; Task 17 ships a trio of presentational components together because they're trivial and untestable in isolation.
- **No placeholders**: every code step shows real code; every command is runnable; no "TODO" or "implement later" markers.
