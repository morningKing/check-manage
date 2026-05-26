"""Tool registry. Each tool module exposes `register(server)`."""

from mcp.server import Server


def register_all(server: Server) -> None:
    from tools.list_collections import register as register_list_collections
    register_list_collections(server)
