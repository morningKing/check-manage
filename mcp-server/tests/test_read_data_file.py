"""Tests for tools.read_data_file."""

from contextlib import contextmanager
from unittest.mock import patch, MagicMock
import pytest


def _ctx(role="developer"):
    from context import ToolContext
    return ToolContext(session_id="s1", user_id="u1", role=role)


def _fake_get_db(record_data, file_row):
    """Two-step get_db: first SELECT fetches the dynamic_data row (one tuple),
    second fetches the data_files row. file_row=None → 404 on file."""
    cur = MagicMock()
    sequence = [record_data, file_row]
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
