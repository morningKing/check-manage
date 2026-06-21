import os
from pathlib import Path

from dotenv import load_dotenv

# Load the per-deployment .env that sits next to this file (i.e. `server/.env`).
# `override=False` keeps real environment variables (set by the shell, CI, or
# docker-compose) winning over file contents — useful so prod can layer secrets
# on top of the dev-friendly defaults checked into .env.example.
load_dotenv(dotenv_path=Path(__file__).resolve().parent / '.env', override=False)


def _to_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _to_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _split_csv(value):
    if not value:
        return []
    return [item.strip() for item in str(value).split(',') if item.strip()]


DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'dbname': os.getenv('DB_NAME', 'casemanage'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', 'jay123'),
    'port': _to_int(os.getenv('DB_PORT'), 5432),
}

FLASK_PORT = _to_int(os.getenv('FLASK_PORT'), 3002)
FLASK_DEBUG = _to_bool(os.getenv('FLASK_DEBUG'), False)  # Disabled for now to fix module loading issue

JWT_SECRET = os.getenv('JWT_SECRET', 'dev-only-change-me')
JWT_EXPIRY_HOURS = _to_int(os.getenv('JWT_EXPIRY_HOURS'), 24)

CORS_ALLOWED_ORIGINS = _split_csv(os.getenv('CORS_ALLOWED_ORIGINS', ''))
OPEN_API_BRANCH = os.getenv('OPEN_API_BRANCH', 'main').strip() or 'main'

# AI chat / Agent integration
# Workspace root lives OUTSIDE the repo on purpose: OpenCode's built-in file
# tools (read/glob/bash) run from the server's launch cwd (the repo root), so
# keeping session workspaces outside that tree stops the agent from globbing
# into other sessions' uploads. Agents reach uploads only via the read_upload
# MCP tool (session-scoped) or server-side inlining. Override with AI_WORKSPACE_ROOT.
AI_WORKSPACE_ROOT     = os.getenv(
    'AI_WORKSPACE_ROOT',
    os.path.join(os.path.expanduser('~'), '.check-manage', 'ai-workspaces'),
)
OPENCODE_BASE_URL     = os.getenv('OPENCODE_BASE_URL', 'http://127.0.0.1:4096')
MCP_SERVER_URL        = os.getenv('MCP_SERVER_URL',    'http://127.0.0.1:3003')
AI_SESSION_TTL_HOURS  = _to_int(os.getenv('AI_SESSION_TTL_HOURS'), 24)
AI_WORKSPACE_QUOTA_MB = _to_int(os.getenv('AI_WORKSPACE_QUOTA_MB'), 200)
# Default OpenCode model id, "<providerID>/<modelID>". Used by:
#   - single chat: when the user picks "default" (or doesn't pick anything)
#     in the composer's model dropdown,
#   - batch tasks: always (per spec, batch doesn't expose a per-task picker).
# Leave empty (the default) to let OpenCode pick from the first connected
# provider's default model — that lets the deployment swap providers without
# editing this file. Override via OPENCODE_MODEL env var or `server/.env`.
OPENCODE_MODEL        = os.getenv('OPENCODE_MODEL', '').strip()

# Data-page file/image field storage. Files live OUTSIDE the repo (same
# reasoning as ai-workspaces: keeps user-uploaded blobs out of OpenCode's
# file-tool reach and out of git). Override via DATA_FILES_ROOT.
DATA_FILES_ROOT       = os.getenv(
    'DATA_FILES_ROOT',
    os.path.join(os.path.expanduser('~'), '.check-manage', 'data-files'),
)
DATA_FILE_MAX_MB      = _to_int(os.getenv('DATA_FILE_MAX_MB'), 50)

# Shared secret for the MCP server -> Flask internal memory endpoints.
# Empty (default) disables the internal endpoints (returns 403).
MCP_INTERNAL_TOKEN = os.getenv('MCP_INTERNAL_TOKEN', '')
