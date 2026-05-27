import os


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
AI_WORKSPACE_ROOT     = os.getenv('AI_WORKSPACE_ROOT', os.path.join(os.path.dirname(__file__), '..', 'ai-workspaces'))
OPENCODE_BASE_URL     = os.getenv('OPENCODE_BASE_URL', 'http://127.0.0.1:4096')
MCP_SERVER_URL        = os.getenv('MCP_SERVER_URL',    'http://127.0.0.1:3003')
AI_SESSION_TTL_HOURS  = _to_int(os.getenv('AI_SESSION_TTL_HOURS'), 24)
AI_WORKSPACE_QUOTA_MB = _to_int(os.getenv('AI_WORKSPACE_QUOTA_MB'), 200)
# OpenCode model id, "<providerID>/<modelID>". Written into each session's
# opencode.json so the agent uses this model.
OPENCODE_MODEL        = os.getenv('OPENCODE_MODEL', 'opencode/deepseek-v4-flash-free')
