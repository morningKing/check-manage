"""Tests for proxy.py config helpers (_backend_port, _cors_origin)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import proxy


def test_backend_port_parses_url(monkeypatch):
    monkeypatch.setattr(proxy, 'BACKEND_URL', 'http://127.0.0.1:3005')
    assert proxy._backend_port() == 3005


def test_backend_port_defaults_when_no_port(monkeypatch):
    monkeypatch.setattr(proxy, 'BACKEND_URL', 'http://backend-host')
    assert proxy._backend_port() == 3001


def test_cors_origin_empty_list_falls_back_to_star(monkeypatch):
    monkeypatch.setenv('CORS_ALLOWED_ORIGINS', '')
    assert proxy._cors_origin('http://evil.example') == '*'


def test_cors_origin_allowed_echoes_origin(monkeypatch):
    monkeypatch.setenv('CORS_ALLOWED_ORIGINS', 'http://a.example, http://b.example')
    assert proxy._cors_origin('http://b.example') == 'http://b.example'


def test_cors_origin_disallowed_returns_empty(monkeypatch):
    monkeypatch.setenv('CORS_ALLOWED_ORIGINS', 'http://a.example')
    assert proxy._cors_origin('http://evil.example') == ''
