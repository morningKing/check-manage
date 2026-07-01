"""Tests for tools.read_data_file."""

from contextlib import contextmanager
from unittest.mock import patch, MagicMock
import pytest


def _ctx(role="developer"):
    from context import ToolContext
    return ToolContext(session_id="s1", user_id="u1", role=role)


def _fake_get_db(record_data, file_row, menu_roles=None):
    """Three-step get_db: first SELECT fetches the menus roles (menu-gate check),
    second fetches the dynamic_data row, third fetches the data_files row.
    menu_roles defaults to ['admin', 'developer'] so existing tests (role='developer')
    pass the gate without changes to their call sites. file_row=None → 404 on file."""
    if menu_roles is None:
        menu_roles = ['admin', 'developer']
    cur = MagicMock()
    sequence = [(menu_roles,), record_data, file_row]
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
    """Non-admin is denied when the menu row is missing (no row = deny)."""
    from tools.read_data_file import handle, ReadDataFileError
    from contextlib import contextmanager
    cur = MagicMock()
    cur.fetchone.return_value = None   # no menu row → roles is None → deny
    conn = MagicMock()
    conn.cursor.return_value = cur
    @contextmanager
    def _get():
        yield conn
    with patch('tools.read_data_file.get_db', _get):
        with pytest.raises(ReadDataFileError):
            handle({'collection': 'secret', 'record_id': 'R1', 'field': 'f'},
                   _ctx('developer'))


def test_kefu_guest_denied_when_not_in_menu_roles(monkeypatch):
    from tools.read_data_file import handle, ReadDataFileError
    from context import ToolContext
    from unittest.mock import MagicMock
    from contextlib import contextmanager
    cur = MagicMock()
    cur.fetchone.return_value = (["admin", "developer"],)  # roles for the menu; no kefu-guest
    conn = MagicMock()
    conn.cursor.return_value = cur
    @contextmanager
    def _get():
        yield conn
    monkeypatch.setattr('tools.read_data_file.get_db', _get)
    with pytest.raises(ReadDataFileError):
        handle({'collection': 'secret', 'record_id': 'R1', 'field': 'f'},
               ToolContext(session_id='s', user_id='kefu-bot', role='kefu-guest'))
