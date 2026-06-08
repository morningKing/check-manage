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
