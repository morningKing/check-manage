"""Tests for utils.opencode_launch — the shared `opencode serve` launch config
used by both proxy.py (managed startup) and the admin restart orchestration."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils import opencode_launch as launch


def test_default_is_argv_bin_serve(monkeypatch):
    for var in ('OPENCODE_BIN', 'OPENCODE_SERVE_CMD', 'OPENCODE_SERVE_CWD',
                'OPENCODE_AUTOSTART'):
        monkeypatch.delenv(var, raising=False)
    target, use_shell = launch.serve_launch()
    assert target == ['opencode', 'serve'] and use_shell is False
    assert launch.serve_cmd_display() == 'opencode serve'
    assert launch.opencode_bin() == 'opencode'
    assert launch.serve_cwd() == os.path.expanduser('~')
    assert launch.autostart_enabled() is True


def test_bin_path_used_verbatim(monkeypatch):
    monkeypatch.setenv('OPENCODE_BIN', r'C:\tools\opencode.exe')
    target, use_shell = launch.serve_launch()
    assert target == [r'C:\tools\opencode.exe', 'serve'] and use_shell is False
    # spaces survive: argv form, never split
    monkeypatch.setenv('OPENCODE_BIN', r'C:\Program Files\opencode\opencode.exe')
    assert launch.serve_launch()[0] == [r'C:\Program Files\opencode\opencode.exe', 'serve']


def test_serve_cmd_override_wins(monkeypatch):
    monkeypatch.setenv('OPENCODE_BIN', r'C:\tools\opencode.exe')
    monkeypatch.setenv('OPENCODE_SERVE_CMD', 'systemctl start opencode')
    target, use_shell = launch.serve_launch()
    assert target == 'systemctl start opencode' and use_shell is True
    assert launch.serve_cmd_display() == 'systemctl start opencode'


def test_cwd_and_autostart_parsing(monkeypatch):
    monkeypatch.setenv('OPENCODE_SERVE_CWD', r'E:\somewhere')
    assert launch.serve_cwd() == r'E:\somewhere'

    for val in ('0', 'false', 'no', ' false '):
        monkeypatch.setenv('OPENCODE_AUTOSTART', val)
        assert launch.autostart_enabled() is False, val
    for val in ('1', 'true', '', 'yes'):
        monkeypatch.setenv('OPENCODE_AUTOSTART', val)
        assert launch.autostart_enabled() is True, repr(val)


def test_module_is_zero_dependency():
    """proxy.py imports this from a standalone process — it must never grow
    flask/db imports (same contract as utils.upload_limits)."""
    src = open(launch.__file__, encoding='utf-8').read()
    for banned in ('import flask', 'from flask', 'from db', 'import config'):
        assert banned not in src, banned
