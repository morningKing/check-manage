from unittest.mock import patch, MagicMock
from contextlib import contextmanager


def _ctx(role="admin"):
    from context import ToolContext
    return ToolContext(session_id="s1", user_id="u1", role=role)


def test_run_rejects_kefu_guest():
    """kefu-guest must be rejected before any DB call."""
    import pytest
    from tools.run_export_script import handle, RunExportError
    with pytest.raises(RunExportError, match="not available for public customer-service sessions"):
        handle({'script_id': 'any'}, _ctx('kefu-guest'))


def test_run_writes_output_and_summary(tmp_path):
    # script_row (SCRIPT_SELECT order) + workspace_path
    script_row = ('s1', 'JSON导出', "result = '[1,2,3]'", 'json', 'page', 'col-x', None)
    cur = MagicMock()
    seq = {"i": 0}
    def fetchone():
        vals = [script_row, (str(tmp_path),)]
        v = vals[seq["i"]]; seq["i"] += 1; return v
    cur.fetchone.side_effect = fetchone
    conn = MagicMock(); conn.cursor.return_value = cur
    @contextmanager
    def _get():
        yield conn
    def fake_exec(cur_, row, **kw):
        return (b'[1,2,3]', 'col-x.json', 'application/json')
    with patch('tools.run_export_script.get_db', _get), \
         patch('tools.run_export_script.execute_bound_export', fake_exec):
        from tools.run_export_script import handle
        out = handle({'script_id': 's1'}, _ctx('admin'))
    assert out['saved'] is True
    assert out['path'].startswith('outputs/')
    assert out['preview'] == '[1,2,3]'
    files = list((tmp_path / 'outputs').glob('*.json'))
    assert len(files) == 1


def test_run_resolves_script_by_name(tmp_path):
    """script_id 参数可以传脚本名称（export_scripts.name，现在全局唯一）——
    断言真正执行的 SQL 同时按 id 和 name 匹配，而不是只信任 mock 的返回值
    （mock 不管 SQL 文本/参数是什么都会返回同一个值，光看返回结果测不出
    WHERE 子句到底有没有真的加宽）。"""
    script_row = ('s1', 'JSON导出', "result = '[1,2,3]'", 'json', 'page', 'col-x', None)
    cur = MagicMock()
    seq = {"i": 0}
    def fetchone():
        vals = [script_row, (str(tmp_path),)]
        v = vals[seq["i"]]; seq["i"] += 1; return v
    cur.fetchone.side_effect = fetchone
    conn = MagicMock(); conn.cursor.return_value = cur
    @contextmanager
    def _get():
        yield conn
    def fake_exec(cur_, row, **kw):
        return (b'[1,2,3]', 'col-x.json', 'application/json')
    with patch('tools.run_export_script.get_db', _get), \
         patch('tools.run_export_script.execute_bound_export', fake_exec):
        from tools.run_export_script import handle
        out = handle({'script_id': 'JSON导出'}, _ctx('admin'))
    assert out['saved'] is True
    assert out['preview'] == '[1,2,3]'

    first_call_sql, first_call_params = cur.execute.call_args_list[0][0]
    assert 'id = %s' in first_call_sql
    assert 'name = %s' in first_call_sql
    assert first_call_params == ('JSON导出', 'JSON导出')


def test_run_rejects_unbound_script(tmp_path):
    script_row = ('s2', '未绑定', "result='x'", 'json', 'page', None, None)
    cur = MagicMock(); cur.fetchone.return_value = script_row
    conn = MagicMock(); conn.cursor.return_value = cur
    @contextmanager
    def _get():
        yield conn
    with patch('tools.run_export_script.get_db', _get):
        from tools.run_export_script import handle, RunExportError
        import pytest
        with pytest.raises(RunExportError):
            handle({'script_id': 's2'}, _ctx('admin'))


def test_run_sanitizes_traversal_filename(tmp_path):
    script_row = ('s3', 'evil', "result='x'", 'json', 'page', 'col-x', None)
    cur = MagicMock()
    seq = {"i": 0}
    def fetchone():
        vals = [script_row, (str(tmp_path),)]
        v = vals[seq["i"]]; seq["i"] += 1; return v
    cur.fetchone.side_effect = fetchone
    conn = MagicMock(); conn.cursor.return_value = cur
    @contextmanager
    def _get():
        yield conn
    def fake_exec(cur_, row, **kw):
        return (b'x', '../../evil.json', 'application/json')
    with patch('tools.run_export_script.get_db', _get), \
         patch('tools.run_export_script.execute_bound_export', fake_exec):
        from tools.run_export_script import handle
        out = handle({'script_id': 's3'}, _ctx('admin'))
    # file must land INSIDE outputs/, not escape it
    assert out['path'] == 'outputs/evil.json'
    import os
    assert os.path.exists(os.path.join(str(tmp_path), 'outputs', 'evil.json'))
