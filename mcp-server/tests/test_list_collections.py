"""Tests for tools.list_collections."""

from unittest.mock import patch
import pytest


def _ctx(role="developer"):
    from context import ToolContext
    return ToolContext(session_id="s1", user_id="u1", role=role)


def test_returns_collections_visible_to_role(fake_db, mock_cursor):
    # Two menus; one allows developer, the other only admin
    mock_cursor.fetchall.return_value = [
        ("page-orders",  "订单", ["developer", "admin"], [{"fieldName": "no", "label": "单号"}]),
        ("page-secrets", "保密", ["admin"],              [{"fieldName": "x"}]),
    ]
    with patch("tools.list_collections.get_db", fake_db):
        from tools.list_collections import handle
        result = handle({}, _ctx("developer"))
    assert [r["collection"] for r in result] == ["orders"]
    assert result[0]["label"] == "订单"
    assert result[0]["fields"][0]["fieldName"] == "no"


def test_admin_sees_all(fake_db, mock_cursor):
    mock_cursor.fetchall.return_value = [
        ("page-orders",  "订单", ["developer"], []),
        ("page-secrets", "保密", ["admin"],     []),
    ]
    with patch("tools.list_collections.get_db", fake_db):
        from tools.list_collections import handle
        result = handle({}, _ctx("admin"))
    assert sorted(r["collection"] for r in result) == ["orders", "secrets"]
