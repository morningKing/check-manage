"""MCP server entry point.

Hosts an MCP Streamable-HTTP transport at /mcp; exposes /health for liveness.
Tools are registered in tools/__init__.py.
"""

import contextlib
from contextvars import ContextVar
from collections.abc import AsyncIterator

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

import uvicorn

from context import context_from_token, ToolContext

mcp_server = Server("check-manage-mcp")

# Per-request context (token-derived user identity)
_current_ctx: ContextVar[ToolContext | None] = ContextVar("mcp_ctx", default=None)


def _resolve_context() -> ToolContext:
    ctx = _current_ctx.get()
    if ctx is None:
        raise PermissionError("no token in request")
    return ctx


# Defer tool registration so /health is reachable even if a tool module errors
from tools import register_all
register_all(mcp_server)

session_manager = StreamableHTTPSessionManager(app=mcp_server, stateless=True)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with session_manager.run():
        yield


app = FastAPI(title="check-manage MCP server", lifespan=lifespan)


class TokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path.startswith("/mcp"):
            token = request.query_params.get("token", "")
            try:
                ctx = context_from_token(token)
            except Exception as e:
                return JSONResponse({"error": str(e)}, status_code=401)
            tok = _current_ctx.set(ctx)
            try:
                return await call_next(request)
            finally:
                _current_ctx.reset(tok)
        return await call_next(request)


app.add_middleware(TokenMiddleware)


@app.get("/health")
def health():
    return {"status": "ok"}


# Mount MCP at /mcp as an ASGI handler
app.mount("/mcp", session_manager.handle_request)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=3003)
