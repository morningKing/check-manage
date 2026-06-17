from unittest.mock import patch, MagicMock
from contextlib import contextmanager


def _ctx(role="admin"):
    from context import ToolContext
    return ToolContext(session_id="s1", user_id="u1", role=role)


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
