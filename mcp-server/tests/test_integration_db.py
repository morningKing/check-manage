"""Opt-in integration test: real Postgres, real token validation, real
list_collections RBAC. Unlike the other tests in this dir (which mock the DB),
this one hits a live database.

Run explicitly:

    RUN_DB_INTEGRATION=1 .venv/Scripts/python.exe -m pytest tests/test_integration_db.py -v

Skipped by default so the normal unit suite stays hermetic. Seeds rows
prefixed 'itest-' and removes them in fixture teardown.
"""

import os
import json
import secrets
from datetime import datetime, timezone, timedelta

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1",
    reason="set RUN_DB_INTEGRATION=1 to run the real-DB integration test",
)

GOOD_TOKEN = "itest_" + secrets.token_urlsafe(24)
EXPIRED_TOKEN = "itest_" + secrets.token_urlsafe(24)


def _cleanup():
    from db import get_db
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM ai_chat_sessions WHERE id LIKE 'itest-%'")
        cur.execute("DELETE FROM menus WHERE id LIKE 'menu-itest-%'")
        cur.execute("DELETE FROM page_configs WHERE id LIKE 'page-itest-%'")
        cur.execute("DELETE FROM users WHERE id = 'itest-user-dev'")


@pytest.fixture(scope="module")
def seeded():
    from db import get_db
    _cleanup()  # in case a prior run left rows
    now = datetime.now(timezone.utc)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s,%s,%s,%s,%s)",
            ("itest-user-dev", "itest_dev", "x", "ITest Dev", "developer"),
        )
        for pid, nm in (("page-itest-public", "ITest Public"),
                        ("page-itest-adminonly", "ITest AdminOnly")):
            cur.execute(
                "INSERT INTO page_configs (id, name, fields) VALUES (%s,%s,%s::jsonb)",
                (pid, nm, json.dumps([{"fieldName": "title", "label": "标题", "controlType": "text"}])),
            )
        cur.execute(
            "INSERT INTO menus (id, name, page_id, roles, menu_type) "
            "VALUES (%s,%s,%s,%s::jsonb,'data')",
            ("menu-itest-public", "ITest Public", "page-itest-public",
             json.dumps(["admin", "developer", "guest"])),
        )
        cur.execute(
            "INSERT INTO menus (id, name, page_id, roles, menu_type) "
            "VALUES (%s,%s,%s,%s::jsonb,'data')",
            ("menu-itest-adminonly", "ITest AdminOnly", "page-itest-adminonly",
             json.dumps(["admin"])),
        )
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, workspace_path, session_token, token_expires_at, status) "
            "VALUES (%s,%s,%s,%s,%s,'active')",
            ("itest-sess-1", "itest-user-dev", "/tmp/itest-ws", GOOD_TOKEN, now + timedelta(hours=2)),
        )
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, workspace_path, session_token, token_expires_at, status) "
            "VALUES (%s,%s,%s,%s,%s,'active')",
            ("itest-sess-2", "itest-user-dev", "/tmp/itest-ws2", EXPIRED_TOKEN, now - timedelta(hours=1)),
        )
    yield
    _cleanup()


def test_validate_token_returns_identity(seeded):
    from auth import validate_session_token
    d = validate_session_token(GOOD_TOKEN)
    assert d["user_id"] == "itest-user-dev"
    assert d["role"] == "developer"
    assert d["session_id"] == "itest-sess-1"


def test_context_from_token_builds_toolcontext(seeded):
    from context import context_from_token, ToolContext
    ctx = context_from_token(GOOD_TOKEN)
    assert isinstance(ctx, ToolContext)
    assert ctx.role == "developer"


def test_developer_rbac(seeded):
    from context import ToolContext
    from tools import list_collections
    cols = {c["collection"] for c in list_collections.handle({}, ToolContext("itest-sess-1", "itest-user-dev", "developer"))}
    assert "itest-public" in cols
    assert "itest-adminonly" not in cols


def test_admin_sees_all(seeded):
    from context import ToolContext
    from tools import list_collections
    cols = {c["collection"] for c in list_collections.handle({}, ToolContext("s", "u", "admin"))}
    assert {"itest-public", "itest-adminonly"} <= cols


def test_guest_rbac(seeded):
    from context import ToolContext
    from tools import list_collections
    cols = {c["collection"] for c in list_collections.handle({}, ToolContext("s", "u", "guest"))}
    assert "itest-public" in cols
    assert "itest-adminonly" not in cols


def test_fields_carried_through(seeded):
    from context import ToolContext
    from tools import list_collections
    pub = next(c for c in list_collections.handle({}, ToolContext("s", "u", "admin"))
               if c["collection"] == "itest-public")
    assert len(pub["fields"]) == 1
    assert pub["fields"][0]["fieldName"] == "title"


def test_expired_token_raises(seeded):
    from auth import validate_session_token, TokenExpired
    with pytest.raises(TokenExpired):
        validate_session_token(EXPIRED_TOKEN)


def test_bogus_token_raises(seeded):
    from auth import validate_session_token, TokenInvalid
    with pytest.raises(TokenInvalid):
        validate_session_token("itest_does_not_exist")
