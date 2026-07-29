"""
导入历史 API 路由测试

真实 DB 集成测试（不 mock 游标）：验证 import_runs/import_run_failures 的
写入、分页列表、详情、重试同步计数逻辑。测试数据用 `_t_` 前缀自建自清理。
admin token 是 superuser，绕过 require_page_action 的具体权限查询，不需要
额外 seed page_configs/角色权限——跟 test_dynamic_batch_pk_check.py 的
route_client 集成测试模式一致。
"""
import sys
import os

import psycopg2
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config import DB_CONFIG  # noqa: E402
import db as _db_module  # noqa: E402
from auth import create_token  # noqa: E402

COLLECTION = '_t_import_runs_coll'
PAGE_ID = f'page-{COLLECTION}'


@pytest.fixture
def client():
    from app import app as flask_app
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if getattr(mod, 'get_db', None) is not None and (
            mod_name.startswith('routes.') or mod_name.startswith('utils.') or mod_name == 'auth'
        ):
            try:
                mod.get_db = _db_module.get_db
            except (AttributeError, TypeError):
                pass
    flask_app.config['TESTING'] = True
    return flask_app.test_client()


def _admin_headers():
    token = create_token({'id': 'admin', 'username': 'admin', 'role': 'admin'})
    return {'Authorization': f'Bearer {token}'}


def _cleanup():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("DELETE FROM import_runs WHERE collection = %s", (COLLECTION,))
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _around_each():
    _cleanup()
    yield
    _cleanup()


def _create_run(client, **overrides):
    body = {
        'pageId': PAGE_ID, 'collection': COLLECTION, 'branchId': 'main', 'fileName': 'a.xlsx',
        'successCount': 8, 'createdCount': 8, 'updatedCount': 0, 'failedCount': 2,
        'failures': [
            {'recordId': 'rec-1', 'originalRecord': {'n': 1}, 'payload': {'id': 'rec-1', 'data': {'n': 1}, 'relations': {}}, 'reason': '主键重复'},
            {'recordId': 'rec-2', 'originalRecord': {'n': 2}, 'payload': {'id': 'rec-2', 'data': {'n': 2}, 'relations': {}}, 'reason': '校验失败'},
        ],
    }
    body.update(overrides)
    resp = client.post('/importRuns', json=body, headers=_admin_headers())
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()['id']


class TestCreateImportRun:
    def test_creates_run_and_failures_with_partial_status(self, client):
        run_id = _create_run(client)

        resp = client.get(f'/importRuns/{run_id}', headers=_admin_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['run']['status'] == 'partial'
        assert body['run']['failedCount'] == 2
        assert len(body['failures']) == 2
        assert {f['recordId'] for f in body['failures']} == {'rec-1', 'rec-2'}

    def test_zero_failures_gives_success_status(self, client):
        run_id = _create_run(client, failedCount=0, failures=[])

        resp = client.get(f'/importRuns/{run_id}', headers=_admin_headers())
        body = resp.get_json()
        assert body['run']['status'] == 'success'
        assert body['failures'] == []


class TestListImportRuns:
    def test_filters_by_page_and_orders_by_created_at_desc(self, client):
        run1 = _create_run(client, fileName='first.xlsx')
        run2 = _create_run(client, fileName='second.xlsx')

        resp = client.get(f'/importRuns?pageId={PAGE_ID}&collection={COLLECTION}', headers=_admin_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['total'] == 2
        assert [r['id'] for r in body['runs']] == [run2, run1]

    def test_pagination_limit_offset(self, client):
        for i in range(3):
            _create_run(client, fileName=f'f{i}.xlsx')

        resp = client.get(f'/importRuns?pageId={PAGE_ID}&collection={COLLECTION}&limit=2&offset=1', headers=_admin_headers())
        body = resp.get_json()
        assert body['total'] == 3
        assert len(body['runs']) == 2


class TestGetImportRunDetail:
    def test_unknown_id_returns_404(self, client):
        resp = client.get('/importRuns/does-not-exist', headers=_admin_headers())
        assert resp.status_code == 404


class TestSyncRetryResult:
    def test_resolved_failures_removed_and_counts_updated(self, client):
        run_id = _create_run(client)

        resp = client.post(
            f'/importRuns/{run_id}/retry-result',
            json={'resolvedRecordIds': ['rec-1'], 'successDelta': 1, 'createdDelta': 1, 'updatedDelta': 0},
            headers=_admin_headers(),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['failedCount'] == 1
        assert body['successCount'] == 9
        assert body['createdCount'] == 9
        assert body['status'] == 'partial'

        detail = client.get(f'/importRuns/{run_id}', headers=_admin_headers()).get_json()
        assert {f['recordId'] for f in detail['failures']} == {'rec-2'}

    def test_resolving_all_failures_flips_status_to_success(self, client):
        run_id = _create_run(client)

        resp = client.post(
            f'/importRuns/{run_id}/retry-result',
            json={'resolvedRecordIds': ['rec-1', 'rec-2'], 'successDelta': 2, 'createdDelta': 2, 'updatedDelta': 0},
            headers=_admin_headers(),
        )
        body = resp.get_json()
        assert body['failedCount'] == 0
        assert body['status'] == 'success'

    def test_empty_resolved_ids_is_a_harmless_noop(self, client):
        run_id = _create_run(client)

        resp = client.post(
            f'/importRuns/{run_id}/retry-result',
            json={'resolvedRecordIds': [], 'successDelta': 0, 'createdDelta': 0, 'updatedDelta': 0},
            headers=_admin_headers(),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['failedCount'] == 2
        assert body['status'] == 'partial'

    def test_unknown_run_id_returns_404(self, client):
        resp = client.post(
            '/importRuns/does-not-exist/retry-result',
            json={'resolvedRecordIds': [], 'successDelta': 0, 'createdDelta': 0, 'updatedDelta': 0},
            headers=_admin_headers(),
        )
        assert resp.status_code == 404
