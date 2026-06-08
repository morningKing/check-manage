"""MCP server entry point.

Hosts an MCP Streamable-HTTP transport at /mcp; exposes /health for liveness.
Tools are registered in tools/__init__.py.
"""

import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

import uvicorn

from app_config import bind_config
from context import context_from_token, ToolContext

mcp_server = Server("check-manage-mcp")


def _resolve_context() -> ToolContext:
    """Derive the caller's identity from the per-request ?token= query param.

    The token is read from the MCP request context's HTTP request — which the
    Streamable-HTTP transport sets in the SAME task as the tool handler, right
    before dispatch — NOT from a middleware-set ContextVar. A ContextVar set in
    BaseHTTPMiddleware.dispatch is reset when call_next returns, but the tool
    handler runs later in the session manager's lifespan task group, so by then
    the ContextVar reads as None ("no token in request"). request_context.request
    is reliable because it travels with the message into the handler's task.
    """
    try:
        request = mcp_server.request_context.request
    except LookupError:
        request = None
    token = request.query_params.get("token", "") if request is not None else ""
    return context_from_token(token)


# Defer tool registration so /health is reachable even if a tool module errors
from tools import register_all, tool_specs
register_all(mcp_server)

session_manager = StreamableHTTPSessionManager(app=mcp_server, stateless=True)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with session_manager.run():
        yield


app = FastAPI(title="check-manage MCP server", lifespan=lifespan)


class TokenMiddleware(BaseHTTPMiddleware):
    """Reject /mcp requests whose ?token= is missing or invalid (HTTP 401).

    Per-call identity is resolved separately in _resolve_context from the request
    context (see its docstring); this middleware only guards the door so bad
    tokens never reach MCP processing.
    """

    async def dispatch(self, request, call_next):
        if request.url.path.startswith("/mcp"):
            token = request.query_params.get("token", "")
            try:
                context_from_token(token)
            except Exception as e:
                return JSONResponse({"error": str(e)}, status_code=401)
        return await call_next(request)


app.add_middleware(TokenMiddleware)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/tools")
def tools():
    return [{"name": n, "description": d} for n, d in tool_specs()]


# Mount MCP at /mcp as an ASGI handler
app.mount("/mcp", session_manager.handle_request)


if __name__ == "__main__":
    host, port = bind_config()
    uvicorn.run(app, host=host, port=port)
