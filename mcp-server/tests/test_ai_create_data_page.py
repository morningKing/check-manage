"""Tests for tools.ai_create_data_page(AI 建表 MCP 封装)。"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

DRAFT = {
    'name': '订货表', 'description': '记录客户订货信息',
    'collectionSlug': 'purchase-orders', 'menuName': '订货表',
    'menuPath': '/purchase-orders',
    'fields': [
        {'fieldName': 'orderNo', 'label': '订单号', 'controlType': 'autoSequence',
         'required': True, 'sequenceConfig': {'prefix': 'PO-', 'max': 9999}},
        {'fieldName': 'customer', 'label': '客户', 'controlType': 'text', 'required': True},
    ],
}


def _ctx(role="admin"):
    from context import ToolContext
    return ToolContext(session_id="sess-1", user_id="u1", role=role)


def _fake_db(fetchones=None):
    """捕获全部 execute 的 fake 连接;fetchone 按 calls 次序消费 fetchones。"""
    captured = []
    fo = list(fetchones or [])
    cur = MagicMock()
    cur.fetchone.side_effect = lambda: fo.pop(0) if fo else None
    cur.execute.side_effect = lambda sql, params=None: captured.append((sql, params))
    conn = MagicMock()
    conn.cursor.return_value = cur

    @contextmanager
    def _get():
        yield conn
    return _get, captured


def _patch_draft(monkeypatch, draft=DRAFT, error=None):
    from utils.ai_schema_designer import draft_page_schema as _real  # noqa: F401
    def _fake(description):
        if error:
            raise RuntimeError(error)
        return dict(draft)
    monkeypatch.setattr('utils.ai_schema_designer.draft_page_schema', _fake)


def test_admin_creates_page_and_menu(monkeypatch):
    from tools.ai_create_data_page import handle
    _patch_draft(monkeypatch)
    get_db, captured = _fake_db([None, None])  # 占用检查:page/menu 均不存在
    with patch('tools.ai_create_data_page.get_db', get_db):
        res = handle({'description': '我要创建一张订货表'}, _ctx())
    assert res['created'] is True
    assert res['pageId'] == 'page-purchase-orders'
    assert res['collection'] == 'purchase-orders'
    assert res['fieldCount'] == 2
    assert res['menu']['roles'] == ['admin', 'developer', 'guest']
    sqls = [sql for sql, _ in captured]
    assert any('INSERT INTO page_configs' in s for s in sqls)
    assert any('INSERT INTO menus' in s for s in sqls)
    # apiEndpoint 与 UI 同款:/{slug};menu 挂 page-x、data 类型
    page_params = [p for sql, p in captured if 'INSERT INTO page_configs' in sql][0]
    assert page_params[0] == 'page-purchase-orders'
    assert page_params[3] == '/purchase-orders'
    menu_params = [p for sql, p in captured if 'INSERT INTO menus' in sql][0]
    assert menu_params[3] == 'page-purchase-orders'
    assert menu_params[7] == 'data'
    # 字段补 id/order,autoSequence 兜底配置(Json 包装,解出原始 list)
    fields = page_params[4].adapted
    assert fields[0]['id'] and fields[0]['order'] == 1
    assert fields[0]['sequenceConfig'] == {'prefix': 'PO-', 'max': 9999}


def test_non_admin_rejected():
    from tools.ai_create_data_page import handle, AiCreateDataPageError
    with pytest.raises(PermissionError, match='仅管理员'):
        handle({'description': 'x'}, _ctx('developer'))


def test_missing_description_rejected():
    from tools.ai_create_data_page import handle, AiCreateDataPageError
    with pytest.raises(AiCreateDataPageError, match='description 不能为空'):
        handle({}, _ctx())


def test_ai_disabled_maps_to_clear_error(monkeypatch):
    from tools.ai_create_data_page import handle, AiCreateDataPageError
    _patch_draft(monkeypatch, error='AI 建表功能未启用，请在系统配置中开启')
    with pytest.raises(AiCreateDataPageError, match='未启用'):
        handle({'description': '订货表'}, _ctx())


def test_duplicate_menu_name_rejected_with_hint(monkeypatch):
    from tools.ai_create_data_page import handle, AiCreateDataPageError
    _patch_draft(monkeypatch)
    get_db, _ = _fake_db([None, ('row',)])  # 菜单名占用
    with patch('tools.ai_create_data_page.get_db', get_db):
        with pytest.raises(AiCreateDataPageError, match='已被占用'):
            handle({'description': '订货表'}, _ctx())


def test_duplicate_slug_rejected(monkeypatch):
    from tools.ai_create_data_page import handle, AiCreateDataPageError
    _patch_draft(monkeypatch)
    get_db, _ = _fake_db([('row',)])  # page_configs 已存在
    with patch('tools.ai_create_data_page.get_db', get_db):
        with pytest.raises(AiCreateDataPageError, match='已被占用'):
            handle({'description': '订货表'}, _ctx())


def test_slug_override_and_bad_slug(monkeypatch):
    from tools.ai_create_data_page import handle, AiCreateDataPageError
    _patch_draft(monkeypatch)
    get_db, captured = _fake_db([None, None])
    with patch('tools.ai_create_data_page.get_db', get_db):
        res = handle({'description': '订货表', 'collection_slug': 'my-orders'},
                     _ctx())
    assert res['collection'] == 'my-orders'
    page_params = [p for sql, p in captured if 'INSERT INTO page_configs' in sql][0]
    assert page_params[3] == '/my-orders'

    get_db2, _ = _fake_db([None, None])
    with patch('tools.ai_create_data_page.get_db', get_db2):
        with pytest.raises(AiCreateDataPageError, match='不合法'):
            handle({'description': 'x', 'collection_slug': 'Bad Slug!'}, _ctx())


def test_kefu_guest_blocked_by_allowlist():
    from rbac import tool_allowed
    assert not tool_allowed('ai_create_data_page', 'kefu-guest')
    assert tool_allowed('ai_create_data_page', 'admin')
