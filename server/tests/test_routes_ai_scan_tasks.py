import sys, os, json
import pytest
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from auth import create_token


def _make_db(mock_conn):
    @contextmanager
    def fake():
        yield mock_conn
    return fake


@pytest.fixture
def setup(mock_conn, mock_cursor):
    fake = _make_db(mock_conn)
    patches = [patch('db.get_db', fake), patch('routes.ai_scan_tasks.get_db', fake),
               patch('utils.permissions.get_db', fake), patch('utils.ai_scan_repo.get_db', fake),
               patch('db.pool', MagicMock()), patch('utils.operation_log.log_operation')]
    for p in patches:
        p.start()
    import utils.permissions as perms
    perms.invalidate_cache()
    mock_cursor.fetchone.return_value = ('admin', True, 'write')
    from app import app
    app.config['TESTING'] = True
    admin = create_token({'id': 'user-admin', 'username': 'admin', 'role': 'admin'})
    yield app.test_client(), mock_cursor, {'Authorization': f'Bearer {admin}'}
    for p in patches:
        p.stop()


def test_list_requires_admin_ai_scan(setup):
    client, cur, headers = setup
    cur.fetchall.return_value = []
    resp = client.get('/ai-scan-tasks', headers=headers)
    assert resp.status_code == 200


def test_create_task(setup):
    client, cur, headers = setup
    cur.fetchone.side_effect = [('admin', True, 'write'),  # permission
                                # get_task after insert returns a row tuple of 22 fields
                                tuple(['scan-x', 'n', True, 'user-admin', 'orders', 'main',
                                       '审核状态', '', '处理中', '已处理', '处理失败', {}, {}, 'p',
                                       [], 15, 20, None, 0, None, None, None])]
    body = {'name': 'n', 'collection': 'orders', 'statusField': '审核状态',
            'promptTemplate': 'p', 'fieldMapping': []}
    resp = client.post('/ai-scan-tasks', data=json.dumps(body), content_type='application/json',
                       headers=headers)
    assert resp.status_code == 201
