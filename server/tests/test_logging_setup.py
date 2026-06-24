"""Tests for utils/logging_setup.py (console + rotating file logging)."""

import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import utils.logging_setup as ls
from logging.handlers import RotatingFileHandler


def _reset():
    """Drop handlers we added and reset the idempotency flag so each test starts
    from a clean root logger."""
    ls._CONFIGURED = False
    root = logging.getLogger()
    for h in list(root.handlers):
        if isinstance(h, (logging.StreamHandler, RotatingFileHandler)):
            root.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass


def test_setup_adds_console_and_rotating_file(tmp_path):
    _reset()
    log = tmp_path / 'ai-chat.log'
    ls.setup_logging(level='DEBUG', log_file=str(log))
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert any(isinstance(h, RotatingFileHandler) for h in root.handlers)
    # a plain StreamHandler (console) that is NOT the rotating file handler
    assert any(isinstance(h, logging.StreamHandler)
               and not isinstance(h, RotatingFileHandler) for h in root.handlers)
    _reset()


def test_file_handler_actually_writes(tmp_path):
    _reset()
    log = tmp_path / 'out.log'
    ls.setup_logging(level='INFO', log_file=str(log))
    logging.getLogger('aichat.test').info('hello-rotating-file')
    for h in logging.getLogger().handlers:
        h.flush()
    assert log.exists()
    assert 'hello-rotating-file' in log.read_text(encoding='utf-8')
    _reset()


def test_setup_is_idempotent(tmp_path):
    _reset()
    log = tmp_path / 'x.log'
    ls.setup_logging(log_file=str(log))
    n = len(logging.getLogger().handlers)
    ls.setup_logging(log_file=str(log))  # second call must be a no-op
    assert len(logging.getLogger().handlers) == n
    _reset()


def test_to_file_false_skips_file_handler(tmp_path):
    _reset()
    ls.setup_logging(to_file=False)
    root = logging.getLogger()
    assert not any(isinstance(h, RotatingFileHandler) for h in root.handlers)
    assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)
    _reset()


def test_level_from_env(monkeypatch, tmp_path):
    _reset()
    monkeypatch.setenv('AI_CHAT_LOG_LEVEL', 'WARNING')
    ls.setup_logging(log_file=str(tmp_path / 'e.log'))
    assert logging.getLogger().level == logging.WARNING
    _reset()


def test_noisy_third_party_loggers_pinned_to_warning(tmp_path):
    _reset()
    ls.setup_logging(level='DEBUG', log_file=str(tmp_path / 'n.log'))
    assert logging.getLogger('werkzeug').level == logging.WARNING
    assert logging.getLogger('urllib3').level == logging.WARNING
    _reset()
