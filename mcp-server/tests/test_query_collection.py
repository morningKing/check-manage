"""Unit tests for tools.query_collection (mocked DB)."""
from unittest.mock import patch, MagicMock
import json
import pytest


def _ctx(role="developer"):
    from context import ToolContext
    return ToolContext(session_id="s1", user_id="u1", role=role)


def _configs():
    return {"orders": {"name": "订单", "fields": [
        {"fieldName": "no", "label": "单号", "controlType": "text"},
        {"fieldName": "status", "label": "状态", "controlType": "text"},
    ]}}


def test_denies_when_collection_not_visible(fake_db, mock_cursor):
    mock_cursor.fetchall.side_effect = [[("page-orders", "订单", _configs()["orders"]["fields"])]]
    mock_cursor.fetchone.side_effect = [(["admin"],)]
    with patch("tools.query_collection.get_db", fake_db):
        from tools.query_collection import handle, QueryCollectionError
        with pytest.raises(QueryCollectionError):
            handle({"collection": "orders"}, _ctx("developer"))


def test_table_mode_returns_rows_json(fake_db, mock_cursor):
    fields = _configs()["orders"]["fields"]
    mock_cursor.fetchall.side_effect = [
        [("page-orders", "订单", fields)],
        [("id1", {"no": "A1", "status": "open"}, None, None)],
    ]
    mock_cursor.fetchone.side_effect = [(["developer"],), (1,), (1,)]
    with patch("tools.query_collection.get_db", fake_db):
        from tools.query_collection import handle
        out = json.loads(handle({"collection": "orders", "filter": {"status": "open"}}, _ctx("developer")))
    assert out["mode"] == "table"
    assert out["total"] == 1
    assert out["rows"][0]["no"] == "A1"


def test_resolves_by_data_menu_display_name(fake_db, mock_cursor):
    """collection 参数传数据页显示名称（不在 page_configs 里直接匹配的
    collection 键）时，回退按数据类型菜单名称解析成真正的 collection。"""
    fields = _configs()["orders"]["fields"]
    mock_cursor.fetchall.side_effect = [
        [("page-orders", "订单", fields)],  # load_page_configs
        [("id1", {"no": "A1", "status": "open"}, None, None)],  # run_query rows
    ]
    mock_cursor.fetchone.side_effect = [
        ("page-orders",),   # resolve_collection: 订单管理 -> page-orders
        (["developer"],),   # roles
        (1,),                # count_rows
        (1,),                # run_query internal count
    ]
    with patch("tools.query_collection.get_db", fake_db):
        from tools.query_collection import handle
        out = json.loads(handle({"collection": "订单管理"}, _ctx("developer")))
    assert out["mode"] == "table"
    assert out["collection"] == "orders"
    assert out["rows"][0]["no"] == "A1"


def test_raises_when_name_does_not_resolve_to_a_visible_collection(fake_db, mock_cursor):
    mock_cursor.fetchall.side_effect = [[("page-orders", "订单", _configs()["orders"]["fields"])]]
    mock_cursor.fetchone.side_effect = [None]  # resolve_collection finds nothing
    with patch("tools.query_collection.get_db", fake_db):
        from tools.query_collection import handle, QueryCollectionError
        with pytest.raises(QueryCollectionError):
            handle({"collection": "不存在的名字"}, _ctx("developer"))


def test_file_mode_when_over_threshold(fake_db, mock_cursor, tmp_path, monkeypatch):
    fields = _configs()["orders"]["fields"]
    mock_cursor.fetchall.side_effect = [
        [("page-orders", "订单", fields)],
        [("id%d" % i, {"no": "A%d" % i}, None, None) for i in range(3)],
    ]
    # roles; count_rows() count (decides mode); run_query() internal COUNT
    mock_cursor.fetchone.side_effect = [(["developer"],), (401,), (3,)]
    import tools.query_collection as qc
    monkeypatch.setattr(qc, "_workspace_for_session", lambda cur, sid: str(tmp_path))
    (tmp_path / "outputs").mkdir()
    with patch("tools.query_collection.get_db", fake_db):
        out = json.loads(qc.handle({"collection": "orders"}, _ctx("developer")))
    assert out["mode"] == "file"
    assert out["total"] == 401
    assert out["file"].startswith("outputs/")
