from unittest.mock import patch, MagicMock
from contextlib import contextmanager


def _ctx(role="developer"):
    from context import ToolContext
    return ToolContext(session_id="s1", user_id="u1", role=role)


def _fake_db(rows_by_call):
    calls = {"i": 0}
    cur = MagicMock()
    cur.execute.side_effect = lambda sql, params=None: None
    def fetchall():
        r = rows_by_call[calls["i"]]; calls["i"] += 1; return r
    cur.fetchall.side_effect = fetchall
    conn = MagicMock(); conn.cursor.return_value = cur
    @contextmanager
    def _get():
        yield conn
    return _get


def test_list_only_bound_scripts_for_role():
    # call order: 1) bound scripts list; 2) s1 target menu roles; 3) s2 target menu roles
    rows = [
        [('s1', '巡检导出', 'desc', 'page', 'inspection-case', None),
         ('s2', '机密导出', 'd2', 'page', 'secret-col', None)],
        [(['admin', 'developer'],)],   # s1 target roles
        [(['admin'],)],                # s2 target roles (developer can't see)
    ]
    with patch('tools.list_export_scripts.get_db', _fake_db(rows)):
        from tools.list_export_scripts import handle
        out = handle({}, _ctx('developer'))
    ids = [s['id'] for s in out['scripts']]
    assert ids == ['s1']
    assert out['scripts'][0]['target'] == 'page:inspection-case'
