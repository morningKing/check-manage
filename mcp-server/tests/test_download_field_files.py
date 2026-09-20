"""Tests for tools.download_field_files."""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest


FIELDS = [
    {'fieldName': 'title', 'label': '标题', 'controlType': 'text'},
    {'fieldName': 'attachments', 'label': '附件', 'controlType': 'file'},
    {'fieldName': 'photo', 'label': '照片', 'controlType': 'image'},
]


def _ctx(role="developer"):
    from context import ToolContext
    return ToolContext(session_id="s1", user_id="u1", role=role)


def _fake_db(fetchones, fetchalls):
    """get_db stub scripted by call order: every fetchone pops from `fetchones`,
    every fetchall pops from `fetchalls`, matching handle()'s SQL sequence:
    page_configs → (resolve_collection, only when the slug misses) → menu roles
    → COUNT → records → data_files ANY."""
    cur = MagicMock()
    fo, fa = list(fetchones), list(fetchalls)
    cur.fetchone.side_effect = lambda: fo.pop(0)
    cur.fetchall.side_effect = lambda: fa.pop(0)
    conn = MagicMock()
    conn.cursor.return_value = cur

    @contextmanager
    def _get():
        yield conn
    return _get


def _run(tmp_path, records, file_rows, *, fields=None, roles=None, role="developer",
         config_page='page-ic', inp=None):
    """Drive handle() with a scripted DB. records = [(id, data)], file_rows =
    [(id, original_name, mime, size, storage_path)]. config_page=None means the
    collection is in neither page_configs nor the data menus."""
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    fields = FIELDS if fields is None else fields
    if roles is None:
        roles = ['admin', 'developer']     # default: caller's role passes the gate
    roles_row = None if roles == '__no_menu__' else (roles,)
    fetchones = [
        roles_row,                                     # menu roles
        (len(records),),                               # COUNT(*)
    ]
    if config_page is None:
        fetchones.insert(0, None)                      # resolve_collection → no menu
    fetchalls = [
        [(config_page, 'IC', fields)] if config_page else [],      # page_configs
        records,
        file_rows,
    ]
    with patch('tools.download_field_files.get_db', _fake_db(fetchones, fetchalls)), \
         patch('tools.download_field_files._workspace_for_session', return_value=str(ws)):
        from tools.download_field_files import handle
        res = handle(inp or {'collection': 'ic'}, _ctx(role))
    return res, ws


def test_downloads_all_file_fields_to_workspace(tmp_path):
    src1 = tmp_path / 'a.txt'
    src1.write_text('hello', encoding='utf-8')
    src2 = tmp_path / 'p.png'
    src2.write_bytes(b'\x89PNG fake')
    res, ws = _run(
        tmp_path,
        [('IC-001', {'attachments': [{'uid': 'f1', 'name': 'a.txt'}],
                     'photo': [{'uid': 'f2', 'name': 'p.png'}]})],
        [('f1', 'a.txt', 'text/plain', 5, str(src1)),
         ('f2', 'p.png', 'image/png', 10, str(src2))],
    )
    assert res['downloaded_count'] == 2
    assert res['matched'] == 1
    assert res['dest'] == str(ws)
    paths = sorted(d['path'] for d in res['downloaded'])
    assert paths == ['a.txt', 'p.png']
    assert (ws / 'a.txt').read_text(encoding='utf-8') == 'hello'
    assert (ws / 'p.png').read_bytes().startswith(b'\x89PNG')


def test_field_param_restricts_and_accepts_chinese_label(tmp_path):
    src = tmp_path / 'a.txt'
    src.write_text('x', encoding='utf-8')
    res, ws = _run(
        tmp_path,
        [('IC-001', {'attachments': [{'uid': 'f1', 'name': 'a.txt'}],
                     'photo': [{'uid': 'f2', 'name': 'p.png'}]})],
        [('f1', 'a.txt', 'text/plain', 1, str(src))],
        inp={'collection': 'ic', 'field': '附件'},
    )
    assert res['fields'] == ['attachments']
    assert res['downloaded_count'] == 1
    assert (ws / 'a.txt').exists()
    assert not (ws / 'p.png').exists()


def test_name_collision_within_run_dedupes(tmp_path):
    src1 = tmp_path / '1.txt'
    src1.write_text('one', encoding='utf-8')
    src2 = tmp_path / '2.txt'
    src2.write_text('two', encoding='utf-8')
    res, ws = _run(
        tmp_path,
        [('IC-001', {'attachments': [{'uid': 'f1', 'name': 'a.txt'}]}),
         ('IC-002', {'attachments': [{'uid': 'f2', 'name': 'a.txt'}]})],
        [('f1', 'a.txt', 'text/plain', 3, str(src1)),
         ('f2', 'a.txt', 'text/plain', 3, str(src2))],
    )
    paths = [d['path'] for d in res['downloaded']]
    assert paths == ['a.txt', 'a_1.txt']
    assert (ws / 'a.txt').read_text(encoding='utf-8') == 'one'
    assert (ws / 'a_1.txt').read_text(encoding='utf-8') == 'two'


def test_never_overwrites_preexisting_file(tmp_path):
    ws = tmp_path / 'ws'
    ws.mkdir()
    (ws / 'a.txt').write_text('old', encoding='utf-8')
    src = tmp_path / 'src.txt'
    src.write_text('new', encoding='utf-8')
    res, ws = _run(
        tmp_path,
        [('IC-001', {'attachments': [{'uid': 'f1', 'name': 'a.txt'}]})],
        [('f1', 'a.txt', 'text/plain', 3, str(src))],
    )
    assert res['downloaded'][0]['path'] == 'a_1.txt'
    assert (ws / 'a.txt').read_text(encoding='utf-8') == 'old'
    assert (ws / 'a_1.txt').read_text(encoding='utf-8') == 'new'


def test_limit_truncates_with_hint(tmp_path):
    src = tmp_path / 'src.txt'
    src.write_text('x', encoding='utf-8')
    rec = [('IC-001', {'attachments': [{'uid': f'f{i}', 'name': f'a{i}.txt'}]})
           for i in range(3)]
    rows = [(f'f{i}', f'a{i}.txt', 'text/plain', 1, str(src)) for i in range(3)]
    res, _ = _run(tmp_path, rec, rows, inp={'collection': 'ic', 'limit': 2})
    assert res['truncated'] is True
    assert res['downloaded_count'] == 2
    assert 'limit' in res['hint']


def test_field_not_file_type_raises(tmp_path):
    with pytest.raises(Exception, match='不是文件/图片字段'):
        _run(tmp_path, [], [], inp={'collection': 'ic', 'field': '标题'})


def test_no_file_fields_raises(tmp_path):
    only_text = [{'fieldName': 'title', 'label': '标题', 'controlType': 'text'}]
    with pytest.raises(Exception, match='没有文件/图片字段'):
        _run(tmp_path, [], [], fields=only_text)


def test_menu_role_gate_denies_non_admin(tmp_path):
    with pytest.raises(Exception, match='无权限下载'):
        _run(tmp_path, [], [], roles=['admin'])


def test_admin_bypasses_empty_menu_roles(tmp_path):
    src = tmp_path / 'a.txt'
    src.write_text('ok', encoding='utf-8')
    res, _ = _run(tmp_path,
                  [('IC-001', {'attachments': [{'uid': 'f1', 'name': 'a.txt'}]})],
                  [('f1', 'a.txt', 'text/plain', 2, str(src))],
                  roles=[], role='admin')
    assert res['downloaded_count'] == 1


def test_unknown_collection_raises(tmp_path):
    with pytest.raises(Exception, match='未找到数据集合'):
        _run(tmp_path, [], [], config_page=None)


def test_config_only_collection_without_menu_works_for_admin(tmp_path):
    """AI-scan style collections exist in page_configs but have no data menu;
    admin may still download from them, non-admin is denied by the gate."""
    src = tmp_path / 'a.txt'
    src.write_text('scan doc', encoding='utf-8')
    rec = [('rec_1', {'input_doc': [{'uid': 'f1', 'name': 'a.txt'}]})]
    rows = [('f1', 'a.txt', 'text/plain', 8, str(src))]
    scan_fields = [{'fieldName': 'input_doc', 'label': '输入文档', 'controlType': 'file'}]
    res, ws = _run(tmp_path, rec, rows, fields=scan_fields,
                   config_page='page-scan_1', role='admin',
                   inp={'collection': 'scan_1'})
    assert res['downloaded_count'] == 1
    assert (ws / 'a.txt').read_text(encoding='utf-8') == 'scan doc'
    with pytest.raises(Exception, match='无权限下载'):
        _run(tmp_path, rec, rows, config_page='page-scan_1', roles='__no_menu__',
             inp={'collection': 'scan_1'})


def test_missing_uid_reported_in_skipped(tmp_path):
    res, _ = _run(
        tmp_path,
        [('IC-001', {'attachments': [{'name': 'legacy.txt', 'url': 'blob:...'}]})],
        [],
    )
    assert res['downloaded_count'] == 0
    assert '缺少 uid' in res['skipped'][0]['reason']


def test_data_files_row_missing_reported(tmp_path):
    res, _ = _run(
        tmp_path,
        [('IC-001', {'attachments': [{'uid': 'gone', 'name': 'x.txt'}]})],
        [],
    )
    assert res['downloaded_count'] == 0
    assert 'data_files 表无此文件' in res['skipped'][0]['reason']


def test_missing_on_disk_reported(tmp_path):
    res, _ = _run(
        tmp_path,
        [('IC-001', {'attachments': [{'uid': 'f1', 'name': 'x.txt'}]})],
        [('f1', 'x.txt', 'text/plain', 1, str(tmp_path / 'no-such-file'))],
    )
    assert res['downloaded_count'] == 0
    assert '磁盘上文件已不存在' in res['skipped'][0]['reason']


def test_dest_dir_relative_subdir(tmp_path):
    src = tmp_path / 'a.txt'
    src.write_text('x', encoding='utf-8')
    res, ws = _run(
        tmp_path,
        [('IC-001', {'attachments': [{'uid': 'f1', 'name': 'a.txt'}]})],
        [('f1', 'a.txt', 'text/plain', 1, str(src))],
        inp={'collection': 'ic', 'dest_dir': 'files/附件'},
    )
    assert res['downloaded'][0]['path'] == 'files/附件/a.txt'
    assert (ws / 'files' / '附件' / 'a.txt').read_text(encoding='utf-8') == 'x'


def test_dest_dir_absolute_and_traversal_rejected(tmp_path):
    from tools.download_field_files import handle, DownloadFieldFilesError
    with patch('tools.download_field_files.get_db', _fake_db([('page-ic',)], [])), \
         patch('tools.download_field_files._workspace_for_session',
               return_value=str(tmp_path / 'ws')):
        for bad in ('E:\\evil', '../../outside'):
            with pytest.raises(DownloadFieldFilesError):
                handle({'collection': 'ic', 'dest_dir': bad}, _ctx())


def test_no_workspace_raises(tmp_path):
    from tools.download_field_files import handle, DownloadFieldFilesError
    with patch('tools.download_field_files._workspace_for_session', return_value=None):
        with pytest.raises(DownloadFieldFilesError, match='workspace'):
            handle({'collection': 'ic'}, _ctx())


def test_missing_collection_arg_raises():
    from tools.download_field_files import handle, DownloadFieldFilesError
    with pytest.raises(DownloadFieldFilesError):
        handle({}, _ctx())
