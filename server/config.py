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

# --- JWT 签名密钥 ------------------------------------------------------------
# HS256 的密钥长度有硬性下界：PyJWT 自 2.10 起按 RFC 7518 §3.2 拒绝短于摘要长度
# （SHA256 = 32 字节）的密钥。requirements.txt 写的是 `PyJWT>=2.8.0` 且无上界，
# 所以同一份代码在旧环境能跑、在新装环境登录即炸，报一句
# "The HMAC key is 18 bytes long..." —— 与真实问题毫不相干的密码学细节。
#
# 旧默认值 'dev-only-change-me' 正好 18 字节，两个毛病叠在一起：
#   1. 新版 PyJWT 下直接不可用；
#   2. 更要命的是，它是**仓库里公开可见的常量**。任何未设 JWT_SECRET 的部署，
#      都在用一把人人可读的钥匙签发身份令牌 —— 谁都能伪造管理员 token。
# 所以这里不能只是"把默认值改长"了事：默认值仅供本地开发，且必须让"正在用默认值"
# 这件事在启动时可被察觉（见 app.py 的启动告警）。
_JWT_MIN_BYTES = 32
_JWT_DEV_DEFAULT = 'dev-only-insecure-jwt-secret-do-not-use-in-production'

_jwt_secret_env = (os.getenv('JWT_SECRET') or '').strip()
JWT_SECRET_IS_DEFAULT = not _jwt_secret_env

if _jwt_secret_env and len(_jwt_secret_env.encode('utf-8')) < _JWT_MIN_BYTES:
    # 显式配了但太短：**启动期**就失败，别拖到第一次登录才炸一句看不懂的话。
    raise RuntimeError(
        f'JWT_SECRET 太短：当前 {len(_jwt_secret_env.encode("utf-8"))} 字节，'
        f'HS256 要求至少 {_JWT_MIN_BYTES} 字节（RFC 7518 §3.2）。\n'
        f'生成一个：python -c "import secrets;print(secrets.token_urlsafe(48))"\n'
        f'然后写进 server/.env 的 JWT_SECRET=。\n'
        f'注意：更换密钥会使所有已签发的登录令牌失效，用户需要重新登录。'
    )

JWT_SECRET = _jwt_secret_env or _JWT_DEV_DEFAULT
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
