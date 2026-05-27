"""Tests for server/utils/opencode_client.py — mocked HTTP and SSE.

Contracts assert the real OpenCode 1.2.26 API (see spec §12):
- directory binds via the ?directory= query param (not body cwd)
- prompt body is {parts:[{type:text,text}]}
- there is no per-session MCP registration (handled via per-dir opencode.json)
"""

from unittest.mock import patch, MagicMock
import pytest
import json


def test_create_session_posts_title_and_binds_directory_via_query():
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"id": "ses_42", "directory": "/tmp/ws"}
    fake_resp.raise_for_status = MagicMock()

    with patch("utils.opencode_client.requests.post", return_value=fake_resp) as post:
        from utils.opencode_client import OpenCodeClient
        client = OpenCodeClient("http://127.0.0.1:4096")
        sid = client.create_session(directory="/tmp/ws", title="t")
    assert sid == "ses_42"
    args, kwargs = post.call_args
    assert args[0].endswith("/session")
    # directory must be a query param, NOT in the body (OpenCode ignores body cwd)
    assert kwargs["params"] == {"directory": "/tmp/ws"}
    assert kwargs["json"].get("title") == "t"
    assert "cwd" not in kwargs["json"]


def test_send_prompt_async_sends_parts_array():
    fake_resp = MagicMock()
    fake_resp.status_code = 204
    fake_resp.raise_for_status = MagicMock()
    with patch("utils.opencode_client.requests.post", return_value=fake_resp) as post:
        from utils.opencode_client import OpenCodeClient
        OpenCodeClient("http://127.0.0.1:4096").send_prompt_async("ses_42", "hello")
    args, kwargs = post.call_args
    assert args[0].endswith("/session/ses_42/prompt_async")
    assert kwargs["json"] == {"parts": [{"type": "text", "text": "hello"}]}


def test_no_register_mcp_method():
    """MCP is wired via per-directory opencode.json, not an API call."""
    from utils.opencode_client import OpenCodeClient
    assert not hasattr(OpenCodeClient("http://x"), "register_mcp")


def test_delete_session_calls_delete():
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.raise_for_status = MagicMock()
    with patch("utils.opencode_client.requests.delete", return_value=fake_resp) as dele:
        from utils.opencode_client import OpenCodeClient
        OpenCodeClient("http://127.0.0.1:4096").delete_session("ses_42")
    args, _ = dele.call_args
    assert args[0].endswith("/session/ses_42")


def test_subscribe_events_yields_parsed_events_and_passes_directory():
    # OpenCode emits data:-only frames; the event name is the JSON `type` field.
    raw = [
        b'data: {"type":"message.part.updated","properties":{"part":{"text":"hi"}}}\n',
        b"\n",
        b'data: {"type":"session.idle","properties":{"sessionID":"ses_42"}}\n',
        b"\n",
    ]
    fake_resp = MagicMock()
    fake_resp.iter_lines = MagicMock(return_value=iter(raw))
    fake_resp.raise_for_status = MagicMock()
    fake_resp.__enter__ = lambda self: self
    fake_resp.__exit__ = lambda self, *a: None

    with patch("utils.opencode_client.requests.get", return_value=fake_resp) as get:
        from utils.opencode_client import OpenCodeClient
        events = list(OpenCodeClient("http://127.0.0.1:4096").subscribe_events(directory="/tmp/ws"))
    # directory forwarded as query param
    _, kwargs = get.call_args
    assert kwargs["params"] == {"directory": "/tmp/ws"}
    assert len(events) == 2
    assert events[0]["event"] == "message.part.updated"
    assert events[0]["data"]["properties"]["part"]["text"] == "hi"
    assert events[1]["event"] == "session.idle"
