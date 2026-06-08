# proxy.py / mcp-server 配置纳入 .env Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `server/proxy.py` 与 `mcp-server` 的配置统一由 `server/.env` 管理（加载 .env、env 化 MCP 绑定与代理后端端口、CORS 预检读 env），并补全 `.env.example`。

**Architecture:** 两个进程顶部加 `load_dotenv(server/.env)`；mcp-server 新增 `app_config.py` 提供 `bind_config()`；proxy.py 新增 `_backend_port()`（解析 BACKEND_URL）和 `_cors_origin()`（读 CORS_ALLOWED_ORIGINS）。纯配置改动，附单测。

**Tech Stack:** Python（Flask 后端 venv + mcp-server 独立 venv）、pytest、python-dotenv。

---

## File Structure

- `mcp-server/app_config.py`（新）— 加载 `server/.env` + `bind_config()`。
- `mcp-server/main.py`（改）— 用 `bind_config()` 绑定。
- `mcp-server/db.py`（改）— import app_config（建池前加载 .env）。
- `mcp-server/pyproject.toml`（改）— 加 `python-dotenv` 依赖。
- `mcp-server/tests/test_app_config.py`（新）。
- `server/proxy.py`（改）— load_dotenv + `_backend_port()` / `_cors_origin()` / `PROXY_HOST` + 接线。
- `server/tests/test_proxy.py`（新）。
- `server/.env.example`（改）— 补登键。

测试命令：
- mcp-server：`cd mcp-server && .venv\Scripts\python.exe -m pytest tests/test_app_config.py -v`（Bash 工具里用 `mcp-server/.venv/Scripts/python.exe -m pytest mcp-server/tests/test_app_config.py -v`）
- server：`cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_proxy.py -v`

---

## Task 1: mcp-server 加载 .env + env 化绑定

**Files:**
- Create: `mcp-server/app_config.py`, `mcp-server/tests/test_app_config.py`
- Modify: `mcp-server/main.py`, `mcp-server/db.py`, `mcp-server/pyproject.toml`

- [ ] **Step 1: Install python-dotenv into the mcp venv + add the dep**

In `mcp-server/pyproject.toml`, add `"python-dotenv>=1.0",` to the `dependencies` list (alongside `mcp`, `fastapi`, `uvicorn[standard]`, `psycopg2-binary`).

Then install it into the mcp venv (Bash tool):
```bash
mcp-server/.venv/Scripts/python.exe -m pip install "python-dotenv>=1.0"
```
Verify: `mcp-server/.venv/Scripts/python.exe -c "import dotenv; print('dotenv', dotenv.__version__)"` → prints a version (no ImportError).

- [ ] **Step 2: Write the failing test** — create `mcp-server/tests/test_app_config.py`:

```python
"""Tests for app_config.bind_config (reads MCP_HOST / MCP_PORT)."""
import app_config


def test_bind_config_defaults(monkeypatch):
    monkeypatch.delenv('MCP_HOST', raising=False)
    monkeypatch.delenv('MCP_PORT', raising=False)
    host, port = app_config.bind_config()
    assert host == '127.0.0.1'
    assert port == 3003 and isinstance(port, int)


def test_bind_config_from_env(monkeypatch):
    monkeypatch.setenv('MCP_HOST', '0.0.0.0')
    monkeypatch.setenv('MCP_PORT', '4555')
    host, port = app_config.bind_config()
    assert host == '0.0.0.0'
    assert port == 4555 and isinstance(port, int)
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd "E:/wsl/check/check-manage" && mcp-server/.venv/Scripts/python.exe -m pytest mcp-server/tests/test_app_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app_config'`.

- [ ] **Step 4: Create `mcp-server/app_config.py`**

```python
"""MCP server config — loads the shared server/.env and exposes bind settings.

The MCP server runs as its own process (own venv) and previously never loaded
server/.env, so DB_* and bind host/port fell back to hardcoded defaults. Import
this module first (before db / context) so env vars are available before the DB
pool is created."""
import os
from pathlib import Path

from dotenv import load_dotenv

# Shared with the Flask backend: mcp-server/.. -> server/.env. Real environment
# variables (docker/CI/shell) win over the file (override=False).
load_dotenv(Path(__file__).resolve().parent.parent / 'server' / '.env', override=False)


def bind_config():
    """(host, port) for the MCP HTTP server. Clients reach it via MCP_SERVER_URL;
    keep MCP_PORT in sync with that URL's port."""
    return os.getenv('MCP_HOST', '127.0.0.1'), int(os.getenv('MCP_PORT', '3003'))
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd "E:/wsl/check/check-manage" && mcp-server/.venv/Scripts/python.exe -m pytest mcp-server/tests/test_app_config.py -v`
Expected: PASS (2).

- [ ] **Step 6: Wire `db.py` to load env before the pool**

In `mcp-server/db.py`, add an import right after the existing `import` block (before `_pool = psycopg2.pool.SimpleConnectionPool(`):
```python
import app_config  # noqa: F401  — loads server/.env before the pool is built
```

- [ ] **Step 7: Wire `main.py` bind**

In `mcp-server/main.py`, add `from app_config import bind_config` as the FIRST local import (immediately above `from context import context_from_token, ToolContext` at line 19), so env is loaded before `context`→`db` build the pool.

Then replace the bottom:
```python
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=3003)
```
with:
```python
if __name__ == "__main__":
    host, port = bind_config()
    uvicorn.run(app, host=host, port=port)
```

- [ ] **Step 8: Run the full mcp-server test suite (no regressions)**

Run: `cd "E:/wsl/check/check-manage" && mcp-server/.venv/Scripts/python.exe -m pytest mcp-server/tests -q`
Expected: PASS (existing tests + the 2 new). If the integration DB test needs a live DB and was already skipping/failing before this change, note it but don't let it block — confirm your changes didn't introduce new failures (run once on the parent commit if unsure).

- [ ] **Step 9: Commit**

```bash
git add mcp-server/app_config.py mcp-server/tests/test_app_config.py mcp-server/main.py mcp-server/db.py mcp-server/pyproject.toml
git commit -m "feat(mcp): load server/.env + env-ize bind host/port (MCP_HOST/MCP_PORT)"
```

---

## Task 2: proxy.py 加载 .env + env 化端口/CORS

**Files:**
- Modify: `server/proxy.py`
- Create: `server/tests/test_proxy.py`

- [ ] **Step 1: Write the failing test** — create `server/tests/test_proxy.py`:

```python
"""Tests for proxy.py config helpers (_backend_port, _cors_origin)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import proxy


def test_backend_port_parses_url(monkeypatch):
    monkeypatch.setattr(proxy, 'BACKEND_URL', 'http://127.0.0.1:3005')
    assert proxy._backend_port() == 3005


def test_backend_port_defaults_when_no_port(monkeypatch):
    monkeypatch.setattr(proxy, 'BACKEND_URL', 'http://backend-host')
    assert proxy._backend_port() == 3001


def test_cors_origin_empty_list_falls_back_to_star(monkeypatch):
    monkeypatch.setenv('CORS_ALLOWED_ORIGINS', '')
    assert proxy._cors_origin('http://evil.example') == '*'


def test_cors_origin_allowed_echoes_origin(monkeypatch):
    monkeypatch.setenv('CORS_ALLOWED_ORIGINS', 'http://a.example, http://b.example')
    assert proxy._cors_origin('http://b.example') == 'http://b.example'


def test_cors_origin_disallowed_returns_empty(monkeypatch):
    monkeypatch.setenv('CORS_ALLOWED_ORIGINS', 'http://a.example')
    assert proxy._cors_origin('http://evil.example') == ''
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_proxy.py -v`
Expected: FAIL — `AttributeError: module 'proxy' has no attribute '_backend_port'` (and `_cors_origin`).

- [ ] **Step 3: Add `load_dotenv` + `urlparse` import at the top of proxy.py**

In `server/proxy.py`, the current top imports end around line 17 (`from http.server import ...`). Add below them:
```python
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv

# Load server/.env so PROXY_*/BACKEND_URL/MCP_*/CORS_* take effect from the file
# (this process is separate from Flask's config.py, which loads it for the app).
load_dotenv(Path(__file__).resolve().parent / '.env', override=False)
```
This must be ABOVE the `# Configuration` block (lines ~19-31) that reads `os.environ`.

- [ ] **Step 4: Add `PROXY_HOST` to the Configuration block**

In the Configuration block, next to `PROXY_PORT = int(os.environ.get('PROXY_PORT', 8080))`, add:
```python
PROXY_HOST = os.environ.get('PROXY_HOST', '0.0.0.0')
```

- [ ] **Step 5: Add the helper functions**

Add these near the Configuration block (e.g. right after the `mimetypes.add_type(...)` lines, before `class ProxyHandler`):
```python
def _backend_port():
    """Port the proxy launches the backend on = the port it proxies to (from
    BACKEND_URL), so they can never drift apart."""
    return urlparse(BACKEND_URL).port or 3001


def _allowed_origins():
    return [o.strip() for o in os.environ.get('CORS_ALLOWED_ORIGINS', '').split(',') if o.strip()]


def _cors_origin(request_origin):
    """Resolve the Access-Control-Allow-Origin value for a preflight: echo the
    request Origin if it's in CORS_ALLOWED_ORIGINS; if the list is empty, fall
    back to '*' (unchanged default); otherwise return '' (not allowed)."""
    allowed = _allowed_origins()
    if not allowed:
        return '*'
    return request_origin if request_origin in allowed else ''
```

- [ ] **Step 6: Use `_backend_port()` in `start_backend()`**

In `start_backend()`, replace the hardcoded launch line:
```python
        [sys.executable, '-c',
         'import app; app.app.run(host="0.0.0.0", port=3001, debug=False)'],
```
with:
```python
        [sys.executable, '-c',
         f'import app; app.app.run(host="0.0.0.0", port={_backend_port()}, debug=False)'],
```

- [ ] **Step 7: Use `_cors_origin()` in `do_OPTIONS`**

Replace the `do_OPTIONS` body:
```python
    def do_OPTIONS(self):
        # Handle CORS preflight
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, PATCH, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-API-Key')
        self.send_header('Access-Control-Max-Age', '86400')
        self.end_headers()
```
with:
```python
    def do_OPTIONS(self):
        # Handle CORS preflight (origin gated by CORS_ALLOWED_ORIGINS; empty list -> '*')
        origin = _cors_origin(self.headers.get('Origin', ''))
        self.send_response(204)
        if origin:
            self.send_header('Access-Control-Allow-Origin', origin)
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, PATCH, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-API-Key')
        self.send_header('Access-Control-Max-Age', '86400')
        self.end_headers()
```

- [ ] **Step 8: Use `PROXY_HOST` for the proxy bind**

In `main()`, replace:
```python
    server = ThreadingHTTPServer(('0.0.0.0', PROXY_PORT), ProxyHandler)
```
with:
```python
    server = ThreadingHTTPServer((PROXY_HOST, PROXY_PORT), ProxyHandler)
```

- [ ] **Step 9: Run to verify it passes**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_proxy.py -v`
Expected: PASS (5).

- [ ] **Step 10: Commit**

```bash
git add server/proxy.py server/tests/test_proxy.py
git commit -m "feat(proxy): load server/.env; backend port from BACKEND_URL; CORS + host from env"
```

---

## Task 3: 补全 .env.example

**Files:**
- Modify: `server/.env.example`

- [ ] **Step 1: Add the MCP bind keys next to MCP_SERVER_URL**

In `server/.env.example`, in the `# ===== AI chat / Agent integration =====` section, find:
```
MCP_SERVER_URL=http://127.0.0.1:3003
```
and add directly below it:
```
# MCP server bind address (used by mcp-server/main.py). Keep MCP_PORT in sync
# with the port in MCP_SERVER_URL / MCP_HEALTH_URL above.
MCP_HOST=127.0.0.1
MCP_PORT=3003
```

- [ ] **Step 2: Add a reverse-proxy section at the end of the file**

Append to `server/.env.example`:
```
# ===== Reverse proxy (production, server/proxy.py) =====
# Public listen address of the reverse proxy.
PROXY_HOST=0.0.0.0
PROXY_PORT=8080
# Where the proxy reaches the Flask backend; its PORT is also the port the proxy
# launches the backend on (so they can't drift). FLASK_PORT only governs the
# standalone dev server (npm run server).
BACKEND_URL=http://127.0.0.1:3001
# MCP server health endpoint the proxy probes on startup.
MCP_HEALTH_URL=http://127.0.0.1:3003/health
# Optional: explicit interpreter for the MCP server's own venv; auto-probed if unset.
# MCP_PYTHON=
```

- [ ] **Step 3: Sanity-check the file parses (no syntax surprises)**

Run (Bash): `grep -E '^(MCP_HOST|MCP_PORT|PROXY_HOST|PROXY_PORT|BACKEND_URL|MCP_HEALTH_URL)=' server/.env.example`
Expected: all six keys present.

- [ ] **Step 4: Commit**

```bash
git add server/.env.example
git commit -m "docs(config): document MCP_HOST/PORT, PROXY_HOST/PORT, BACKEND_URL, MCP_HEALTH_URL in .env.example"
```

---

## Self-Review 结果

- **Spec 覆盖：** A 统一 load_dotenv（Task 1 app_config + db/main 接线；Task 2 proxy 顶部）；B MCP 绑定 env（Task 1 Step 7）；C 后端端口解析 BACKEND_URL（Task 2 Step 5/6）；D CORS 读 env + 空回退 `*`（Task 2 Step 5/7）；E PROXY_HOST（Task 2 Step 4/8）；F .env.example（Task 3）；python-dotenv 依赖+安装（Task 1 Step 1）；测试（Task 1 Step 2、Task 2 Step 1）。全覆盖。
- **Placeholder 扫描：** 无 TBD/TODO；每个代码步骤含完整 before/after 与确切命令。Task 1 Step 8 关于既有 DB 集成测试的说明是操作提示，非占位。
- **类型/命名一致：** `bind_config()`→`(host, port)`，`MCP_HOST/MCP_PORT`；`_backend_port()`/`_allowed_origins()`/`_cors_origin()`/`PROXY_HOST`/`BACKEND_URL`/`CORS_ALLOWED_ORIGINS` 在定义、接线、测试处一致；`.env.example` 键与代码读取键一致。
