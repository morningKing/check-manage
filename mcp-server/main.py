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
