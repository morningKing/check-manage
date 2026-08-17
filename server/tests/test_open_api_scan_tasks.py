import os
import sys
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = '/v1/ai-scan-tasks'
HDR = {'X-API-Key': 'cm_x'}


@contextmanager
def _fake_auth_db(mock_conn):
    yield mock_conn


def _auth_passes(mock_cursor, owner='user-42'):
    mock_cursor.fetchone.return_value = ('ak-1', '集成密钥', True, owner)


def _key(owner='user-42'):
    return {'id': 'ak-1', 'name': 'k', 'ownerUserId': owner}


def _task(task_id='scan-1', **overrides):
    t = {
        'id': task_id, 'name': '方案审核', 'enabled': True,
        'ownerUserId': 'user-42', 'collection': 'orders', 'branchId': 'main',
        'scheduleIntervalMinutes': 15, 'maxRecordsPerScan': 20,
        'lastRunAt': None, 'lastScanCount': 0, 'lastError': None,
        'statusField': 'status', 'promptTemplate': '...',
    }
    t.update(overrides)
    return t


def test_list_scoped_to_owner(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_scan_tasks._current_key', return_value=_key()), \
         patch('utils.ai_scan_repo.list_tasks_for_owner', return_value=[_task()]) as lt:
        resp = client.get(BASE, headers=HDR)
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body['tasks']) == 1
    assert body['tasks'][0]['id'] == 'scan-1'
    # 内部配置细节不外泄
    assert 'promptTemplate' not in body['tasks'][0]
    assert 'ownerUserId' not in body['tasks'][0]
    lt.assert_called_once_with('user-42')


def test_detail_not_found_is_404(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_scan_tasks._current_key', return_value=_key()), \
         patch('utils.ai_scan_repo.get_task_for_owner', return_value=None):
        resp = client.get(f'{BASE}/scan-999', headers=HDR)
    assert resp.status_code == 404


def test_detail_returns_contract_fields(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_scan_tasks._current_key', return_value=_key()), \
         patch('utils.ai_scan_repo.get_task_for_owner', return_value=_task()):
        resp = client.get(f'{BASE}/scan-1', headers=HDR)
    assert resp.status_code == 200
    assert resp.get_json()['collection'] == 'orders'


def test_run_now_not_found_is_404(client, mock_conn, mock_cursor):
    """不存在，或存在但不属于这把密钥——两种情况都 404，不用 403。"""
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_scan_tasks._current_key', return_value=_key()), \
         patch('utils.ai_scan_repo.get_task_for_owner', return_value=None), \
         patch('utils.ai_scan_engine.run_task') as rt:
        resp = client.post(f'{BASE}/scan-999/run-now', headers=HDR)
    assert resp.status_code == 404
    rt.assert_not_called()


def test_run_now_success_returns_claimed_count(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    before = _task(lastScanCount=0)
    after = _task(lastScanCount=3, lastError=None)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_scan_tasks._current_key', return_value=_key()), \
         patch('utils.ai_scan_repo.get_task_for_owner', side_effect=[before, after]), \
         patch('utils.ai_scan_engine.run_task') as rt:
        resp = client.post(f'{BASE}/scan-1/run-now', headers=HDR)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {'triggered': True, 'claimedCount': 3, 'lastError': None}
    rt.assert_called_once_with(before)


def test_run_now_engine_exception_is_500(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_scan_tasks._current_key', return_value=_key()), \
         patch('utils.ai_scan_repo.get_task_for_owner', return_value=_task()), \
         patch('utils.ai_scan_engine.run_task', side_effect=RuntimeError('boom')):
        resp = client.post(f'{BASE}/scan-1/run-now', headers=HDR)
    assert resp.status_code == 500
