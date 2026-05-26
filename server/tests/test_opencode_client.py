"""Tests for server/utils/opencode_client.py — mocked HTTP and SSE."""

from unittest.mock import patch, MagicMock
import pytest
import json


def test_create_session_posts_session_and_returns_id():
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"id": "oc_sess_42"}
    fake_resp.raise_for_status = MagicMock()

    with patch("utils.opencode_client.requests.post", return_value=fake_resp) as post:
        from utils.opencode_client import OpenCodeClient
        client = OpenCodeClient("http://127.0.0.1:4096")
        sid = client.create_session(cwd="/tmp/ws")
    assert sid == "oc_sess_42"
    args, kwargs = post.call_args
    assert args[0].endswith("/session")
    assert kwargs["json"].get("cwd") == "/tmp/ws"


def test_register_mcp_posts_url():
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.raise_for_status = MagicMock()
    with patch("utils.opencode_client.requests.post", return_value=fake_resp) as post:
        from utils.opencode_client import OpenCodeClient
        OpenCodeClient("http://127.0.0.1:4096").register_mcp(
            session_id="oc_sess_42",
            url="http://127.0.0.1:3003/mcp?token=t1",
        )
    args, kwargs = post.call_args
    assert args[0].endswith("/mcp")
    assert kwargs["json"]["url"].endswith("?token=t1")


def test_send_prompt_async_returns_immediately():
    fake_resp = MagicMock()
    fake_resp.status_code = 204
    fake_resp.raise_for_status = MagicMock()
    with patch("utils.opencode_client.requests.post", return_value=fake_resp):
        from utils.opencode_client import OpenCodeClient
        OpenCodeClient("http://127.0.0.1:4096").send_prompt_async(
            "oc_sess_42", "hello",
        )


def test_subscribe_events_yields_parsed_events():
    """SSE lines come as `event: ...\\n` and `data: {...}\\n\\n`."""
    raw = [
        b"event: message.part.delta\n",
        b'data: {"text":"hi"}\n',
        b"\n",
        b"event: message.finished\n",
        b'data: {}\n',
        b"\n",
    ]
    fake_resp = MagicMock()
    fake_resp.iter_lines = MagicMock(return_value=iter(raw))
    fake_resp.raise_for_status = MagicMock()
    fake_resp.__enter__ = lambda self: self
    fake_resp.__exit__ = lambda self, *a: None

    with patch("utils.opencode_client.requests.get", return_value=fake_resp):
        from utils.opencode_client import OpenCodeClient
        events = list(OpenCodeClient("http://127.0.0.1:4096").subscribe_events())
    assert len(events) == 2
    assert events[0]["event"] == "message.part.delta"
    assert events[0]["data"] == {"text": "hi"}
    assert events[1]["event"] == "message.finished"
