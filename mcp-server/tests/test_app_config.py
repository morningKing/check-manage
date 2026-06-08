"""Tests for app_config.bind_config (reads MCP_HOST / MCP_PORT)."""
import app_config


def test_bind_config_defaults(monkeypatch):
    monkeypatch.delenv('MCP_HOST', raising=False)
    monkeypatch.delenv('MCP_PORT', raising=False)
    host, port = app_config.bind_config()
    assert host == '127.0.0.1'
    assert port == 3003 and isinstance(port, int)


def test_bind_config_from_env(monkeypatch):
    monkeypatch.setenv('MCP_HOST', '0.0.0.0')
    monkeypatch.setenv('MCP_PORT', '4555')
    host, port = app_config.bind_config()
    assert host == '0.0.0.0'
    assert port == 4555 and isinstance(port, int)
