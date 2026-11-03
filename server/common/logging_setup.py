"""Centralised logging: console + rotating file, for AI-chat observability.

Configured once at app startup (idempotent). Without this, AI-chat code paths
(send_message, the SSE proxy, the persistence listener, OpenCode HTTP calls) had
no logging at all, so a stuck session left no trace and couldn't be diagnosed.

Env overrides:
  AI_CHAT_LOG_LEVEL        log level (default INFO)
  AI_CHAT_LOG_FILE         rotating file path (default <server>/ai-chat.log)
  AI_CHAT_LOG_MAX_BYTES    rotate threshold (default 10485760 = 10 MiB)
  AI_CHAT_LOG_BACKUP_COUNT kept rotations (default 5)
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_CONFIGURED = False

# Third-party loggers that are chatty at INFO; pin to WARNING to keep the AI-chat
# signal readable.
_NOISY = ('werkzeug', 'urllib3', 'httpx', 'httpcore', 'chromadb', 'waitress',
          'apscheduler')

_FORMAT = '%(asctime)s %(levelname)-7s [%(name)s] %(message)s'
_DATEFMT = '%Y-%m-%d %H:%M:%S'


def _default_log_file():
    # logging_setup.py lives in server/utils/, so two dirnames up is server/.
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ai-chat.log')


def setup_logging(*, level=None, log_file=None, to_file=True):
    """Attach a console + rotating-file handler to the root logger. Idempotent:
    safe to call from each process (Flask reloader child, waitress) — subsequent
    calls are no-ops so handlers don't stack."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    level_name = (level or os.environ.get('AI_CHAT_LOG_LEVEL', 'INFO')).upper()
    lvl = getattr(logging, level_name, logging.INFO)
    fmt = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    root = logging.getLogger()
    root.setLevel(lvl)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    root.addHandler(console)

    if to_file:
        path = log_file or os.environ.get('AI_CHAT_LOG_FILE') or _default_log_file()
        try:
            max_bytes = int(os.environ.get('AI_CHAT_LOG_MAX_BYTES', 10 * 1024 * 1024))
            backups = int(os.environ.get('AI_CHAT_LOG_BACKUP_COUNT', 5))
            file_handler = RotatingFileHandler(
                path, maxBytes=max_bytes, backupCount=backups, encoding='utf-8',
            )
            file_handler.setFormatter(fmt)
            root.addHandler(file_handler)
        except OSError as e:
            # Never let a bad log path stop the app from starting.
            root.warning('logging_setup: could not open log file %s: %s', path, e)

    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)

    _CONFIGURED = True
