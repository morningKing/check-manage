# proxy.py / mcp-server 配置纳入 .env

**Date:** 2026-06-06
**Status:** Approved (design)

## 问题背景

`server/proxy.py` 与 `mcp-server/`（`main.py`/`db.py`）有几处配置硬编码或"名义上读 env、实际没被 .env 管理"：

- **两个进程都不 `load_dotenv`**：`config.py`（Flask）会加载 `server/.env`，但 proxy.py 和 mcp-server 只 `os.getenv` 而从不 `load_dotenv` → `server/.env` 对它们**不生效**，只有 shell 显式 export 才有效。
- **`mcp-server/main.py:95`**：`uvicorn.run(app, host="127.0.0.1", port=3003)` 绑定地址**完全硬编码**（连 `os.getenv` 都没有），与客户端用的 `MCP_SERVER_URL` 脱钩。
- **`mcp-server/db.py:11-15`**：读 `os.getenv("DB_*", 默认)`，但因无 `load_dotenv`，实际回退到硬编码默认（含 `password "jay123"`）。
- **`server/proxy.py:218`**：`start_backend()` 里 `app.app.run(host="0.0.0.0", port=3001 …)` 端口硬编码，与 `BACKEND_URL`(默认:3001) 和 `FLASK_PORT`(默认3002) 三者不一致。
- **`server/proxy.py:340`**：代理监听 host `0.0.0.0` 硬编码。
- **`server/proxy.py:198`**：OPTIONS 预检 `Access-Control-Allow-Origin: '*'` 硬编码，与后端 `CORS_ALLOWED_ORIGINS` 不一致。
- `.env.example` 未登记 proxy 已读取的 `PROXY_PORT/BACKEND_URL/MCP_HEALTH_URL/MCP_PYTHON`。

## 目标 / 非目标

- **目标**：让上述配置统一由 `server/.env` 管理；消除端口失配；CORS 预检与后端一致。
- **非目标**：不 env 化各处超时（YAGNI）；不改 `config.py` 的 `FLASK_PORT` 默认（dev/prod 故意不同）；不动 relation 等无关项；不改启动后端的 bind host（维持 `0.0.0.0`）。

## 已定决策

1. **MCP 绑定端口** → 新增 `MCP_HOST`(默认 `127.0.0.1`) / `MCP_PORT`(默认 `3003`)；客户端仍用 `MCP_SERVER_URL`（两处端口需保持一致，与 `FLASK_PORT` vs `BACKEND_URL` 同理）。
2. **proxy 启动的后端端口** → 从 `BACKEND_URL` 解析端口（启动端口==代理目标端口，单一来源）。
3. **CORS 预检** → 读 `CORS_ALLOWED_ORIGINS`；**列表为空时回退 `*`**（保持现状不破坏）。

## 方案

### A. 统一加载 `server/.env`

- **proxy.py** 顶部、在读取任何 `os.environ` 之前：
  ```python
  from pathlib import Path
  from dotenv import load_dotenv
  load_dotenv(Path(__file__).resolve().parent / '.env', override=False)
  ```
- **mcp-server**：新增 `mcp-server/app_config.py`，import 时加载共享 `server/.env` 并暴露绑定配置：
  ```python
  import os
  from pathlib import Path
  from dotenv import load_dotenv
  load_dotenv(Path(__file__).resolve().parent.parent / 'server' / '.env', override=False)

  def bind_config():
      return os.getenv('MCP_HOST', '127.0.0.1'), int(os.getenv('MCP_PORT', '3003'))
  ```
  - `db.py` 顶部 `import app_config  # noqa: F401  确保建连接池前已加载 .env`（在 `_pool = …` 之前）。
  - `main.py` 首个本地导入 `from app_config import bind_config`（早于 `from context import …`，保证 db 建池前 env 已就绪）。
  - **mcp-server 的 venv 当前未装 `python-dotenv`（已确认）**：必须在 `mcp-server/pyproject.toml` 的 `dependencies` 加 `python-dotenv>=1.0`，并在其 venv 安装：`mcp-server/.venv/Scripts/pip install python-dotenv`（Windows）。

### B. mcp-server/main.py 绑定地址 env 化

`if __name__ == "__main__":` 中：
```python
host, port = bind_config()
uvicorn.run(app, host=host, port=port)
```

### C. proxy.py 启动后端端口从 BACKEND_URL 解析

新增：
```python
from urllib.parse import urlparse
def _backend_port():
    return urlparse(BACKEND_URL).port or 3001
```
`start_backend()` 内联脚本改为用该端口（host 维持 `0.0.0.0`）：
```python
[sys.executable, '-c',
 f'import app; app.app.run(host="0.0.0.0", port={_backend_port()}, debug=False)']
```

### D. proxy.py CORS 预检读 env

新增（解析逗号分隔列表 + 选定回显 Origin）：
```python
def _allowed_origins():
    return [o.strip() for o in os.environ.get('CORS_ALLOWED_ORIGINS', '').split(',') if o.strip()]

def _cors_origin(request_origin):
    allowed = _allowed_origins()
    if not allowed:
        return '*'                       # 未配置 → 回退放行（保持现状）
    return request_origin if request_origin in allowed else ''
```
`do_OPTIONS` 改为：
```python
origin = _cors_origin(self.headers.get('Origin', ''))
self.send_response(204)
if origin:
    self.send_header('Access-Control-Allow-Origin', origin)
self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, PATCH, OPTIONS')
self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-API-Key')
self.send_header('Access-Control-Max-Age', '86400')
self.end_headers()
```
（命中或回退 `*` 时下发头；非空列表且不命中则不下发 `Allow-Origin`，浏览器据此拒绝。）

### E. proxy.py 代理 bind host env 化

```python
PROXY_HOST = os.environ.get('PROXY_HOST', '0.0.0.0')
...
server = ThreadingHTTPServer((PROXY_HOST, PROXY_PORT), ProxyHandler)
```

### F. `.env.example` + 文档

新增/补登键（保持 `.env.example` 的"键=空值"风格）：
- 新增：`MCP_HOST`、`MCP_PORT`、`PROXY_HOST`
- 补登：`PROXY_PORT`、`BACKEND_URL`、`MCP_HEALTH_URL`、`MCP_PYTHON`
- 注释说明：`MCP_PORT` 须与 `MCP_SERVER_URL`/`MCP_HEALTH_URL` 端口一致；`BACKEND_URL` 端口即 proxy 启动后端的端口；`FLASK_PORT` 仅管 dev 独立启动。

## 测试

- **`server/tests/test_proxy.py`**（导入 proxy 仅触发模块级常量，不启动服务）：
  - `_backend_port()`：`BACKEND_URL=http://127.0.0.1:3005` → 3005；无端口 URL → 3001（monkeypatch `proxy.BACKEND_URL`）。
  - `_cors_origin()`：空列表 → `'*'`；命中（origin 在 `CORS_ALLOWED_ORIGINS`）→ 回显该 origin；不命中 → `''`（monkeypatch env）。
- **`mcp-server/tests/test_app_config.py`**（只导入 `app_config`，不碰 DB）：
  - `bind_config()` 默认 → `('127.0.0.1', 3003)`；设 `MCP_HOST`/`MCP_PORT` → 对应值且 port 为 int。

## 涉及文件

- 修改：`server/proxy.py`、`mcp-server/main.py`、`mcp-server/db.py`、`server/.env.example`、`mcp-server/pyproject.toml`（加 `python-dotenv>=1.0` 依赖）
- 新增：`mcp-server/app_config.py`、`server/tests/test_proxy.py`、`mcp-server/tests/test_app_config.py`
- 安装步骤：`mcp-server/.venv/Scripts/pip install python-dotenv`
