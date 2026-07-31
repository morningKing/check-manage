"""Tests for tools.read_data_file."""

from contextlib import contextmanager
from unittest.mock import patch, MagicMock
import pytest


def _ctx(role="developer"):
    from context import ToolContext
    return ToolContext(session_id="s1", user_id="u1", role=role)


def _fake_get_db(record_data, file_row, menu_roles=None, resolved_page_id='page-ic'):
    """Four-step get_db: first SELECT resolves the `collection` identifier
    (slug or data-menu display name) to its page_id, second fetches the menus
    roles (menu-gate check), third fetches the dynamic_data row, fourth
    fetches the data_files row. menu_roles defaults to ['admin', 'developer']
    so existing tests (role='developer') pass the gate without changes to
    their call sites. file_row=None → 404 on file. resolved_page_id defaults
    to 'page-ic' matching the 'ic' collection every existing test passes."""
    if menu_roles is None:
        menu_roles = ['admin', 'developer']
    cur = MagicMock()
    sequence = [(resolved_page_id,), (menu_roles,), record_data, file_row]
    state = {"i": 0}

    def fetchone():
        r = sequence[state["i"]]
        state["i"] += 1
        return r
    cur.fetchone.side_effect = fetchone
    cur.execute = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value = cur

    @contextmanager
    def _get():
        yield conn
    return _get


def test_read_text_file_returns_utf8_content(tmp_path):
    sample = tmp_path / 'guide.txt'
    sample.write_text('hello world\n中文也行', encoding='utf-8')
    record = ({'attachment': [{'uid': 'fid1', 'name': 'guide.txt'}]},)
    file_row = ('guide.txt', 'text/plain', sample.stat().st_size, str(sample))
    with patch('tools.read_data_file.get_db', _fake_get_db(record, file_row)):
        from tools.read_data_file import handle
        res = handle({'collection': 'ic', 'record_id': 'r1',
                      'field': 'attachment'}, _ctx())
    assert res['found'] is True
    assert res['encoding'] == 'utf-8'
    assert 'hello world' in res['content']


def test_read_binary_file_returns_base64(tmp_path):
    sample = tmp_path / 'pic.png'
    sample.write_bytes(b'\x89PNG\r\n\x1a\n\x00\x00\x00\x0DIHDR' + b'\x00' * 32)
    record = ({'image': [{'uid': 'fid2', 'name': 'pic.png'}]},)
    file_row = ('pic.png', 'image/png', sample.stat().st_size, str(sample))
    with patch('tools.read_data_file.get_db', _fake_get_db(record, file_row)):
        from tools.read_data_file import handle
        res = handle({'collection': 'ic', 'record_id': 'r1', 'field': 'image'},
                     _ctx())
    assert res['found'] is True
    assert res['encoding'] == 'base64'
    import base64 as b64
    assert b64.b64decode(res['content']).startswith(b'\x89PNG')


def test_record_not_found():
    with patch('tools.read_data_file.get_db', _fake_get_db(None, None)):
        from tools.read_data_file import handle
        res = handle({'collection': 'ic', 'record_id': 'ghost',
                      'field': 'attachment'}, _ctx())
    assert res['found'] is False
    assert '记录不存在' in res['error']


def test_field_not_a_list():
    record = ({'attachment': 'wrong-type'},)
    with patch('tools.read_data_file.get_db', _fake_get_db(record, None)):
        from tools.read_data_file import handle
        res = handle({'collection': 'ic', 'record_id': 'r1',
                      'field': 'attachment'}, _ctx())
    assert res['found'] is False
    assert '不是文件列表' in res['error']


def test_data_files_row_missing():
    record = ({'attachment': [{'uid': 'gone', 'name': 'x'}]},)
    with patch('tools.read_data_file.get_db', _fake_get_db(record, None)):
        from tools.read_data_file import handle
        res = handle({'collection': 'ic', 'record_id': 'r1',
                      'field': 'attachment'}, _ctx())
    assert res['found'] is False
    assert 'data_files 表无此文件' in res['error']


def test_legacy_mock_data_missing_uid():
    """Old blob: data may have file entries without uid/id; surface a clear hint."""
    record = ({'attachment': [{'name': 'old.txt', 'url': 'blob:http://...'}]},)
    with patch('tools.read_data_file.get_db', _fake_get_db(record, None)):
        from tools.read_data_file import handle
        res = handle({'collection': 'ic', 'record_id': 'r1',
                      'field': 'attachment'}, _ctx())
    assert res['found'] is False
    assert '缺少 uid/id' in res['error']


def test_missing_arguments_raise():
    from tools.read_data_file import handle, ReadDataFileError
    with pytest.raises(ReadDataFileError):
        handle({}, _ctx())


def test_admin_bypasses_menu_roles(tmp_path):
    """Admin bypasses the menu-role gate even when menu_roles is empty."""
    sample = tmp_path / 'doc.txt'
    sample.write_text('admin can read this', encoding='utf-8')
    record = ({'attachment': [{'uid': 'fid99', 'name': 'doc.txt'}]},)
    file_row = ('doc.txt', 'text/plain', sample.stat().st_size, str(sample))
    with patch('tools.read_data_file.get_db', _fake_get_db(record, file_row, menu_roles=[])):
        from tools.read_data_file import handle
        res = handle({'collection': 'ic', 'record_id': 'r1', 'field': 'attachment'},
                     _ctx('admin'))
    assert res['found'] is True
    assert 'admin can read this' in res['content']


def test_denied_when_menu_row_missing():
    """No matching data-menu (by slug or name) at all → 未找到数据集合, before any role check."""
    from tools.read_data_file import handle, ReadDataFileError
    from contextlib import contextmanager
    cur = MagicMock()
    cur.fetchone.return_value = None   # resolve step finds no menu row
    conn = MagicMock()
    conn.cursor.return_value = cur
    @contextmanager
    def _get():
        yield conn
    with patch('tools.read_data_file.get_db', _get):
        with pytest.raises(ReadDataFileError, match='未找到数据集合'):
            handle({'collection': 'secret', 'record_id': 'R1', 'field': 'f'},
                   _ctx('developer'))


def test_kefu_guest_denied_when_not_in_menu_roles(monkeypatch):
    from tools.read_data_file import handle, ReadDataFileError
    from context import ToolContext
    from unittest.mock import MagicMock
    from contextlib import contextmanager
    cur = MagicMock()
    # resolve step, then roles lookup
    sequence = [('page-secret',), (["admin", "developer"],)]  # roles for the menu; no kefu-guest
    state = {'i': 0}
    def fetchone():
        r = sequence[state['i']]; state['i'] += 1
        return r
    cur.fetchone.side_effect = fetchone
    conn = MagicMock()
    conn.cursor.return_value = cur
    @contextmanager
    def _get():
        yield conn
    monkeypatch.setattr('tools.read_data_file.get_db', _get)
    with pytest.raises(ReadDataFileError):
        handle({'collection': 'secret', 'record_id': 'R1', 'field': 'f'},
               ToolContext(session_id='s', user_id='kefu-bot', role='kefu-guest'))


def test_resolves_collection_by_data_menu_display_name(tmp_path):
    """collection 参数传数据页显示名称（而不是 slug）时也能正确解析定位。"""
    sample = tmp_path / 'guide.txt'
    sample.write_text('hello by name', encoding='utf-8')
    record = ({'attachment': [{'uid': 'fid1', 'name': 'guide.txt'}]},)
    file_row = ('guide.txt', 'text/plain', sample.stat().st_size, str(sample))
    with patch('tools.read_data_file.get_db',
               _fake_get_db(record, file_row, resolved_page_id='page-inspection-case')):
        from tools.read_data_file import handle
        res = handle({'collection': '巡检记录', 'record_id': 'r1',
                      'field': 'attachment'}, _ctx())
    assert res['found'] is True
    assert 'hello by name' in res['content']
