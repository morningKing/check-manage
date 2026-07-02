from contextlib import contextmanager
from unittest.mock import patch
import utils.kefu_repo as repo


def _cm(conn):
    @contextmanager
    def cm():
        yield conn
    return cm()


def _row():  # 13 columns matching _COLS order (panel_blocks last)
    return ('kf_1', 'presale', '售前', None, None, None, None,
            [], {}, 'kefu-bot', True, {}, [{'id': 'b1', 'type': 'links'}])


def test_row_to_instance_includes_panel_blocks():
    inst = repo._row_to_instance(_row())
    assert inst['panel_blocks'] == [{'id': 'b1', 'type': 'links'}]


def test_update_instance_writes_panel_blocks(mock_conn, mock_cursor):
    mock_cursor.fetchone.return_value = _row()
    blocks = [{'id': 'b1', 'type': 'faq', 'title': '热点', 'enabled': True, 'config': {'limit': 5}}]
    with patch('utils.kefu_repo.get_db', lambda: _cm(mock_conn)):
        repo.update_instance('kf_1', {'panel_blocks': blocks})
    sql = ' '.join(c.args[0] for c in mock_cursor.execute.call_args_list)
    assert 'panel_blocks=%s' in sql
    # value serialized as JSON
    import json
    params = [p for c in mock_cursor.execute.call_args_list if c.args[1] for p in c.args[1]]
    assert json.dumps(blocks) in params
