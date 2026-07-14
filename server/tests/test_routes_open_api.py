"""
Open API 路由单元测试

测试 GET/POST/PUT 端点、权限控制和数据校验。
"""

import sys
import os
import json
import pytest
from unittest.mock import MagicMock, patch, call
from contextlib import contextmanager
from io import BytesIO
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from auth import create_token, hash_api_key


def _make_mock_db(mock_conn):
    @contextmanager
    def fake_get_db():
        yield mock_conn
    return fake_get_db


VALID_API_KEY = 'cm_test_key_for_unit_testing_1234'
VALID_KEY_HASH = hash_api_key(VALID_API_KEY)
now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def setup(mock_conn, mock_cursor):
    fake_db = _make_mock_db(mock_conn)
    patches = [
        patch('db.get_db', fake_db),
        patch('routes.auth.get_db', fake_db),
        patch('routes.menus.get_db', fake_db),
        patch('routes.etl_tasks.get_db', fake_db),
        patch('routes.dynamic.get_db', fake_db),
        patch('routes.page_configs.get_db', fake_db),
        patch('routes.users.get_db', fake_db),
        patch('routes.relations.get_db', fake_db),
        patch('routes.api_keys.get_db', fake_db),
        patch('routes.open_api.get_db', fake_db),
        patch('auth.get_db', fake_db),
        patch('db.pool', MagicMock()),
        patch('utils.operation_log.log_operation'),
    ]
    for p in patches:
        p.start()

    from app import app
    app.config['TESTING'] = True
    api_headers = {'X-API-Key': VALID_API_KEY}

    yield app.test_client(), mock_cursor, api_headers

    for p in patches:
        p.stop()


def _setup_api_key_auth(mock_cursor, active=True):
    """Configure mock cursor to pass API key authentication.

    The api_key_required decorator performs:
      1. SELECT id, name, is_active FROM api_keys WHERE key_hash = %s
      2. UPDATE api_keys SET last_used_at = NOW() WHERE id = %s
    """
    original_side_effect = mock_cursor.fetchone.side_effect

    def auth_then_delegate(*args, **kwargs):
        # First call: api key lookup
        mock_cursor.fetchone.side_effect = original_side_effect
        return ('ak-test', 'Test Key', active)

    mock_cursor.fetchone.side_effect = auth_then_delegate


def _setup_auth_and_returns(mock_cursor, fetchone_returns=None, fetchall_returns=None):
    """Setup auth + subsequent query return values."""
    call_count = [0]

    def fetchone_side(*args, **kwargs):
        idx = call_count[0]
        call_count[0] += 1
        if idx == 0:
            # Auth: api key lookup
            return ('ak-test', 'Test Key', True)
        if fetchone_returns and idx - 1 < len(fetchone_returns):
            return fetchone_returns[idx - 1]
        return None

    mock_cursor.fetchone.side_effect = fetchone_side
    if fetchall_returns is not None:
        mock_cursor.fetchall.return_value = fetchall_returns


class TestAuthentication:
    def test_missing_api_key(self, setup):
        client, _, _ = setup
        resp = client.get('/api/v1/collections')
        assert resp.status_code == 401
        assert 'Missing' in resp.get_json()['error']

    def test_invalid_api_key(self, setup):
        client, mock_cursor, _ = setup
        mock_cursor.fetchone.return_value = None
        resp = client.get('/api/v1/collections',
                          headers={'X-API-Key': 'cm_invalid'})
        assert resp.status_code == 401

    def test_revoked_api_key(self, setup):
        client, mock_cursor, _ = setup
        mock_cursor.fetchone.return_value = ('ak-1', 'Key', False)
        resp = client.get('/api/v1/collections',
                          headers={'X-API-Key': VALID_API_KEY})
        assert resp.status_code == 401
        assert 'revoked' in resp.get_json()['error']


class TestListCollections:
    def test_includes_writable_flag(self, setup):
        client, mock_cursor, api_h = setup
        _setup_auth_and_returns(mock_cursor, fetchall_returns=[
            ('page-devices', '设备台账', '设备信息', True),
            ('page-cases', '用例管理', '用例', False),
        ])
        resp = client.get('/api/v1/collections', headers=api_h)
        assert resp.status_code == 200
        data = resp.get_json()['data']
        assert len(data) == 2
        assert data[0]['writable'] is True
        assert data[1]['writable'] is False


class TestListCollectionData:
    def test_pagination_orders_by_created_at_and_id(self, setup):
        """List query must use a deterministic ORDER BY (created_at + unique id
        tiebreaker), otherwise rows with identical created_at can repeat across
        pages or be skipped under LIMIT/OFFSET. Regression for duplicate-rows bug.
        """
        client, mock_cursor, api_h = setup
        _setup_auth_and_returns(
            mock_cursor,
            fetchone_returns=[
                (True, None),  # check_collection_public -> api_public=True
                (5,),          # COUNT(*) -> total
            ],
            fetchall_returns=[
                ('rec-1', 'devices', {'name': 'A'}, now),
            ],
        )
        resp = client.get('/api/v1/collections/devices?page=2&pageSize=20',
                          headers=api_h)
        assert resp.status_code == 200

        # Find the data SELECT (the one with LIMIT/OFFSET) among executed SQL.
        data_sql = next(
            call_args[0][0]
            for call_args in mock_cursor.execute.call_args_list
            if 'LIMIT' in call_args[0][0] and 'OFFSET' in call_args[0][0]
        )
        assert 'ORDER BY created_at, id' in data_sql


class TestCreateRecord:
    def test_collection_not_public(self, setup):
        client, mock_cursor, api_h = setup
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (False, False),  # api_public=False, api_writable=False
        ])
        resp = client.post('/api/v1/collections/devices',
                           data=json.dumps({'name': 'Device1'}),
                           content_type='application/json',
                           headers=api_h)
        assert resp.status_code == 404

    def test_collection_not_writable(self, setup):
        client, mock_cursor, api_h = setup
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, False),  # api_public=True, api_writable=False
        ])
        resp = client.post('/api/v1/collections/devices',
                           data=json.dumps({'name': 'Device1'}),
                           content_type='application/json',
                           headers=api_h)
        assert resp.status_code == 403
        assert 'read-only' in resp.get_json()['error']

    def test_missing_body(self, setup):
        client, mock_cursor, api_h = setup
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),  # writable
        ])
        resp = client.post('/api/v1/collections/devices',
                           headers=api_h)
        assert resp.status_code == 400

    def test_required_field_missing(self, setup):
        client, mock_cursor, api_h = setup
        fields = [
            {'fieldName': 'name', 'label': '名称', 'controlType': 'text', 'required': True},
        ]
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),      # writable check
            (fields,),         # get_page_fields
            None,              # ID uniqueness check (not found = OK)
            ([],),             # pk_fields (no pk fields)
        ])
        resp = client.post('/api/v1/collections/devices',
                           data=json.dumps({'status': 'active'}),
                           content_type='application/json',
                           headers=api_h)
        assert resp.status_code == 400
        assert 'Validation failed' in resp.get_json()['error']

    def test_create_success(self, setup):
        client, mock_cursor, api_h = setup
        fields = [
            {'fieldName': 'name', 'label': '名称', 'controlType': 'text', 'required': True},
        ]
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),      # writable check
            (fields,),         # get_page_fields
            None,              # ID uniqueness (not found)
            ([],),             # pk_fields
            ('rec-1', 'devices', {'name': 'Device1'}, now),  # RETURNING
        ])
        resp = client.post('/api/v1/collections/devices',
                           data=json.dumps({'name': 'Device1'}),
                           content_type='application/json',
                           headers=api_h)
        assert resp.status_code == 201
        data = resp.get_json()['data']
        assert data['name'] == 'Device1'

    def test_duplicate_id(self, setup):
        client, mock_cursor, api_h = setup
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),      # writable check
            ([],),             # get_page_fields (no fields)
            ('existing-id',),  # ID uniqueness check: found!
        ])
        resp = client.post('/api/v1/collections/devices',
                           data=json.dumps({'id': 'existing-id', 'name': 'X'}),
                           content_type='application/json',
                           headers=api_h)
        assert resp.status_code == 409


class TestBatchCreateRecords:
    def test_collection_not_writable(self, setup):
        client, mock_cursor, api_h = setup
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, False),  # public but not writable
        ])
        resp = client.post('/api/v1/collections/devices/batch',
                           data=json.dumps({'records': [{'name': 'A'}]}),
                           content_type='application/json',
                           headers=api_h)
        assert resp.status_code == 403

    def test_missing_records_field(self, setup):
        client, mock_cursor, api_h = setup
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),
        ])
        resp = client.post('/api/v1/collections/devices/batch',
                           data=json.dumps({}),
                           content_type='application/json',
                           headers=api_h)
        assert resp.status_code == 400
        assert 'records is required' in resp.get_json()['error']

    def test_batch_size_exceeds_limit(self, setup):
        client, mock_cursor, api_h = setup
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),  # writable check
        ])
        records = [{'name': f'item-{i}'} for i in range(1001)]
        resp = client.post('/api/v1/collections/devices/batch',
                           data=json.dumps({'records': records}),
                           content_type='application/json',
                           headers=api_h)
        assert resp.status_code == 400
        assert 'exceeds maximum' in resp.get_json()['error']

    def test_batch_create_success(self, setup):
        client, mock_cursor, api_h = setup
        fields = [{'fieldName': 'name', 'label': '名称', 'controlType': 'text', 'required': False}]
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),   # writable check
            (fields,),      # get_page_fields
            ([],),          # get_primary_key_fields -> pk_fields = []
        ])
        mock_cursor.fetchall.side_effect = [
            [],  # existing-ids check: 'rec-1' not found in DB
            [('rec-1', 'devices', {'name': 'A'}, now),
             ('api-generated123', 'devices', {'name': 'B'}, now)],  # RETURNING rows
            [],  # reseed_sequences -> _autoseq_fields_by_collection (no autoSequence fields)
        ]
        records = [
            {'id': 'rec-1', 'name': 'A'},
            {'name': 'B'},
        ]
        resp = client.post('/api/v1/collections/devices/batch',
                           data=json.dumps({'records': records}),
                           content_type='application/json',
                           headers=api_h)
        assert resp.status_code == 201
        body = resp.get_json()
        assert body['created'] == 2
        assert body['failed'] == 0
        assert len(body['data']) == 2
        assert body['data'][0]['id'] == 'rec-1'
        assert body['data'][0]['name'] == 'A'
        assert 'errors' not in body

    def test_continue_on_error_false_aborts_whole_batch(self, setup):
        client, mock_cursor, api_h = setup
        fields = [{'fieldName': 'name', 'label': '名称', 'controlType': 'text', 'required': False}]
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),
            (fields,),
            ([],),
        ])
        mock_cursor.fetchall.side_effect = [
            [('rec-1',)],  # existing-ids check: rec-1 already exists
        ]
        records = [
            {'id': 'rec-1', 'name': 'A'},
            {'name': 'B'},
        ]
        resp = client.post('/api/v1/collections/devices/batch',
                           data=json.dumps({'records': records}),
                           content_type='application/json',
                           headers=api_h)
        assert resp.status_code == 400
        body = resp.get_json()
        assert body['failed'] == 1
        assert body['errors'][0]['index'] == 0
        assert body['errors'][0]['error'] == 'Record ID already exists'
        # Nothing should have been inserted
        insert_calls = [
            c for c in mock_cursor.execute.call_args_list
            if c.args and 'INSERT INTO dynamic_data' in str(c.args[0])
        ]
        assert len(insert_calls) == 0

    def test_continue_on_error_true_partial_success(self, setup):
        client, mock_cursor, api_h = setup
        fields = [{'fieldName': 'name', 'label': '名称', 'controlType': 'text', 'required': False}]
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),
            (fields,),
            ([],),
        ])
        mock_cursor.fetchall.side_effect = [
            [('rec-1',)],  # existing-ids check: rec-1 already exists
            [('api-generated456', 'devices', {'name': 'B'}, now)],  # RETURNING (only the successful one)
            [],  # reseed_sequences -> _autoseq_fields_by_collection (no autoSequence fields)
        ]
        records = [
            {'id': 'rec-1', 'name': 'A'},  # will fail: already exists
            {'name': 'B'},                 # will succeed
        ]
        resp = client.post('/api/v1/collections/devices/batch',
                           data=json.dumps({'records': records, 'options': {'continueOnError': True}}),
                           content_type='application/json',
                           headers=api_h)
        assert resp.status_code == 201
        body = resp.get_json()
        assert body['created'] == 1
        assert body['failed'] == 1
        assert body['errors'][0]['index'] == 0
        assert len(body['data']) == 1
        assert body['data'][0]['name'] == 'B'

    def test_duplicate_id_within_batch(self, setup):
        client, mock_cursor, api_h = setup
        fields = [{'fieldName': 'name', 'label': '名称', 'controlType': 'text', 'required': False}]
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),
            (fields,),
            ([],),
        ])
        mock_cursor.fetchall.side_effect = [
            [],  # existing-ids check: 'dup-1' not found in DB
        ]
        records = [
            {'id': 'dup-1', 'name': 'A'},
            {'id': 'dup-1', 'name': 'B'},
        ]
        resp = client.post('/api/v1/collections/devices/batch',
                           data=json.dumps({'records': records}),
                           content_type='application/json',
                           headers=api_h)
        assert resp.status_code == 400
        body = resp.get_json()
        assert body['failed'] == 2
        assert all(e['error'] == 'Duplicate ID in batch' for e in body['errors'])

    def test_primary_key_conflict(self, setup):
        client, mock_cursor, api_h = setup
        fields = [
            {'fieldName': 'sku', 'label': 'SKU', 'controlType': 'text', 'required': True, 'isPrimaryKey': True},
        ]
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),           # writable
            (fields,),              # get_page_fields
            (fields,),              # get_primary_key_fields -> pk_fields = ['sku']
            ('existing-rec-id',),   # check_primary_key_unique -> conflict found
        ])
        records = [{'sku': 'SKU-001'}]
        resp = client.post('/api/v1/collections/devices/batch',
                           data=json.dumps({'records': records}),
                           content_type='application/json',
                           headers=api_h)
        assert resp.status_code == 400
        body = resp.get_json()
        assert body['failed'] == 1
        assert 'Primary key conflict' in body['errors'][0]['error']


class TestUpdateRecord:
    def test_collection_not_writable(self, setup):
        client, mock_cursor, api_h = setup
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, False),  # public but not writable
        ])
        resp = client.put('/api/v1/collections/devices/rec-1',
                          data=json.dumps({'name': 'Updated'}),
                          content_type='application/json',
                          headers=api_h)
        assert resp.status_code == 403

    def test_record_not_found(self, setup):
        client, mock_cursor, api_h = setup
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),  # writable
            None,          # record lookup: not found
        ])
        resp = client.put('/api/v1/collections/devices/nonexistent',
                          data=json.dumps({'name': 'Updated'}),
                          content_type='application/json',
                          headers=api_h)
        assert resp.status_code == 404

    def test_update_success(self, setup):
        client, mock_cursor, api_h = setup
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),                                    # writable
            ('rec-1', {'name': 'Old', 'status': 'active'}, 1),  # existing record
            ([],),                                           # get_page_fields
            ([],),                                           # pk_fields
            ('rec-1', 'devices', {'name': 'Updated', 'status': 'active'}, now),  # updated row
        ])
        mock_cursor.rowcount = 1
        resp = client.put('/api/v1/collections/devices/rec-1',
                          data=json.dumps({'name': 'Updated'}),
                          content_type='application/json',
                          headers=api_h)
        assert resp.status_code == 200
        data = resp.get_json()['data']
        assert data['name'] == 'Updated'

    def test_version_conflict(self, setup):
        client, mock_cursor, api_h = setup
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),                           # writable
            ('rec-1', {'name': 'Old'}, 5),          # existing, version=5
        ])
        resp = client.put('/api/v1/collections/devices/rec-1',
                          data=json.dumps({'name': 'Updated', '_version': 3}),
                          content_type='application/json',
                          headers=api_h)
        assert resp.status_code == 409
        assert resp.get_json()['code'] == 'VERSION_CONFLICT'

    def test_partial_update_merges(self, setup):
        """PUT with partial fields should merge with existing data."""
        client, mock_cursor, api_h = setup
        old_data = {'name': 'Device1', 'status': 'active', 'location': 'A'}
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),                                        # writable
            ('rec-1', old_data, 1),                              # existing record
            ([],),                                               # get_page_fields
            ([],),                                               # pk_fields
            ('rec-1', 'devices', {'name': 'Device1', 'status': 'inactive', 'location': 'A'}, now),
        ])
        mock_cursor.rowcount = 1
        resp = client.put('/api/v1/collections/devices/rec-1',
                          data=json.dumps({'status': 'inactive'}),
                          content_type='application/json',
                          headers=api_h)
        assert resp.status_code == 200


class TestStatusBadgeTimestampOpenApi:
    """PUT /api/v1/collections/<collection>/<id> — 第三方系统回写 statusBadge 字段
    变化时，data 里应写入 `_statusBadge_<field>_changedAt` 时间戳。"""

    STATUS_FIELDS = [{'fieldName': 'status', 'controlType': 'statusBadge'}]

    def _find_update_call(self, mock_cursor):
        return next(
            c for c in mock_cursor.execute.call_args_list
            if c.args and 'UPDATE dynamic_data' in str(c.args[0])
        )

    def _find_insert_call(self, mock_cursor):
        return next(
            c for c in mock_cursor.execute.call_args_list
            if c.args and 'INSERT INTO dynamic_data' in str(c.args[0])
        )

    def test_create_with_initial_status_stamps_timestamp(self, setup):
        """POST 创建记录时 statusBadge 字段带初始值，应写入变化时间戳——
        否则这条记录会永远匹配不到超时兜底任务的扫描条件，安全网出现漏洞"""
        client, mock_cursor, api_h = setup
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),                                          # writable
            (self.STATUS_FIELDS,),                                 # get_page_fields
            None,                                                  # ID uniqueness (not found)
            ([],),                                                 # pk_fields
            ('rec-1', 'devices', {'status': 'pending'}, now),      # RETURNING
        ])
        resp = client.post('/api/v1/collections/devices',
                           data=json.dumps({'id': 'rec-1', 'status': 'pending'}),
                           content_type='application/json',
                           headers=api_h)
        assert resp.status_code == 201
        insert_call = self._find_insert_call(mock_cursor)
        inserted_data = insert_call.args[1][2].adapted  # (id, collection, data, branch_id)
        assert inserted_data['status'] == 'pending'
        assert '_statusBadge_status_changedAt' in inserted_data

    def test_create_without_status_value_does_not_stamp(self, setup):
        """创建时该字段没有值（未传/空）不应凭空写入时间戳"""
        client, mock_cursor, api_h = setup
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),
            (self.STATUS_FIELDS,),
            None,
            ([],),
            ('rec-1', 'devices', {'name': 'X'}, now),
        ])
        resp = client.post('/api/v1/collections/devices',
                           data=json.dumps({'id': 'rec-1', 'name': 'X'}),
                           content_type='application/json',
                           headers=api_h)
        assert resp.status_code == 201
        insert_call = self._find_insert_call(mock_cursor)
        inserted_data = insert_call.args[1][2].adapted
        assert '_statusBadge_status_changedAt' not in inserted_data

    def test_update_status_value_changed_stamps_timestamp(self, setup):
        """第三方系统通过 Open API PUT 回写 statusBadge 字段值变化时，应写入变化时间戳"""
        client, mock_cursor, api_h = setup
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),                                          # writable
            ('rec-1', {'status': 'pending'}, 1),                   # existing record
            (self.STATUS_FIELDS,),                                 # get_page_fields
            ([],),                                                 # pk_fields
            ('rec-1', 'devices', {'status': 'processing'}, now),   # updated row
        ])
        mock_cursor.rowcount = 1
        resp = client.put('/api/v1/collections/devices/rec-1',
                          data=json.dumps({'status': 'processing'}),
                          content_type='application/json',
                          headers=api_h)
        assert resp.status_code == 200
        update_call = self._find_update_call(mock_cursor)
        merged_data = update_call.args[1][0].adapted  # (data, version, collection, id, db_version, branch_id)
        assert merged_data['status'] == 'processing'
        assert '_statusBadge_status_changedAt' in merged_data

    def test_update_status_value_unchanged_does_not_restamp(self, setup):
        """值没变时不重复刷新时间戳（沿用旧值）"""
        client, mock_cursor, api_h = setup
        old_data = {'status': 'pending', '_statusBadge_status_changedAt': '2026-01-01T00:00:00+00:00'}
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),                                          # writable
            ('rec-1', dict(old_data), 1),                          # existing record
            (self.STATUS_FIELDS,),                                 # get_page_fields
            ([],),                                                 # pk_fields
            ('rec-1', 'devices', dict(old_data), now),             # updated row
        ])
        mock_cursor.rowcount = 1
        resp = client.put('/api/v1/collections/devices/rec-1',
                          data=json.dumps({'status': 'pending'}),
                          content_type='application/json',
                          headers=api_h)
        assert resp.status_code == 200
        update_call = self._find_update_call(mock_cursor)
        merged_data = update_call.args[1][0].adapted
        assert merged_data['_statusBadge_status_changedAt'] == '2026-01-01T00:00:00+00:00'


class TestSearchTextStampingOpenApi:
    """Open API 的 create/update 也要维护 search_text 预计算列，与内部 UI 写路径
    （routes/dynamic.py 的 create_item/update_item）保持一致，否则第三方系统
    写入的记录搜不到（见 utils/search_text.py）。"""

    TEXT_FIELDS = [
        {'fieldName': 'name', 'controlType': 'text'},
        {'fieldName': 'note', 'controlType': 'textarea'},
        {'fieldName': 'ref', 'controlType': 'reference'},
    ]

    def _find_insert_call(self, mock_cursor):
        return next(
            c for c in mock_cursor.execute.call_args_list
            if c.args and 'INSERT INTO dynamic_data' in str(c.args[0])
        )

    def _find_update_call(self, mock_cursor):
        return next(
            c for c in mock_cursor.execute.call_args_list
            if c.args and 'UPDATE dynamic_data' in str(c.args[0])
        )

    def test_create_stamps_search_text_from_direct_searchable_fields_only(self, setup):
        client, mock_cursor, api_h = setup
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),                                          # writable
            (self.TEXT_FIELDS,),                                   # get_page_fields
            None,                                                  # ID uniqueness (not found)
            ([],),                                                 # pk_fields
            ('rec-1', 'devices', {'name': '张三', 'note': '备注'}, now),  # RETURNING
        ])
        resp = client.post('/api/v1/collections/devices',
                           data=json.dumps({'id': 'rec-1', 'name': '张三', 'note': '备注', 'ref': 'x'}),
                           content_type='application/json',
                           headers=api_h)
        assert resp.status_code == 201
        insert_call = self._find_insert_call(mock_cursor)
        search_text = insert_call.args[1][4]  # (id, collection, data, branch_id, search_text)
        assert search_text == '张三 备注'

    def test_update_refreshes_search_text_with_merged_data(self, setup):
        client, mock_cursor, api_h = setup
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),                                              # writable
            ('rec-1', {'name': '旧名字', 'note': '旧备注'}, 1),         # existing record
            (self.TEXT_FIELDS,),                                       # get_page_fields
            ([],),                                                     # pk_fields
            ('rec-1', 'devices', {'name': '新名字', 'note': '旧备注'}, now),  # updated row
        ])
        mock_cursor.rowcount = 1
        resp = client.put('/api/v1/collections/devices/rec-1',
                          data=json.dumps({'name': '新名字'}),
                          content_type='application/json',
                          headers=api_h)
        assert resp.status_code == 200
        update_call = self._find_update_call(mock_cursor)
        search_text = update_call.args[1][2]  # (data, version, search_text, collection, id, db_version, branch_id)
        assert search_text == '新名字 旧备注'


class TestSchemaIncludesWritable:
    def test_schema_writable_flag(self, setup):
        client, mock_cursor, api_h = setup
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            ('设备台账', '设备信息', [
                {'fieldName': 'name', 'label': '名称', 'controlType': 'text', 'required': True},
            ], True, True),  # api_public=True, api_writable=True
        ])
        resp = client.get('/api/v1/collections/devices/schema', headers=api_h)
        assert resp.status_code == 200
        data = resp.get_json()['data']
        assert data['writable'] is True


class TestUploadFile:
    """POST /api/v1/files — Open API 文件上传（写入 file/image 字段）。"""

    def test_missing_collection(self, setup):
        client, mock_cursor, api_h = setup
        _setup_auth_and_returns(mock_cursor)  # auth only
        resp = client.post(
            '/api/v1/files',
            data={'file': (BytesIO(b'hello'), 'a.txt')},
            content_type='multipart/form-data', headers=api_h)
        assert resp.status_code == 400
        assert 'collection' in resp.get_json()['error']

    def test_non_public_collection(self, setup):
        client, mock_cursor, api_h = setup
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (False, False),  # check_collection_public -> not public
        ])
        resp = client.post(
            '/api/v1/files',
            data={'collection': 'devices', 'file': (BytesIO(b'hello'), 'a.txt')},
            content_type='multipart/form-data', headers=api_h)
        assert resp.status_code == 404

    def test_read_only_collection(self, setup):
        client, mock_cursor, api_h = setup
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, False),  # public but not writable
        ])
        resp = client.post(
            '/api/v1/files',
            data={'collection': 'devices', 'file': (BytesIO(b'hello'), 'a.txt')},
            content_type='multipart/form-data', headers=api_h)
        assert resp.status_code == 403

    def test_upload_success_returns_uid(self, setup):
        client, mock_cursor, api_h = setup
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),  # public + writable
        ])
        fake_meta = {'id': 'file-xyz', 'name': '巡检.txt', 'size': 5,
                     'mimeType': 'text/plain', 'url': '/api/data-files/file-xyz/download'}
        with patch('routes.open_api.save_data_file', return_value=(fake_meta, None)):
            resp = client.post(
                '/api/v1/files',
                data={'collection': 'devices', 'file': (BytesIO(b'hello'), '巡检.txt')},
                content_type='multipart/form-data', headers=api_h)
        assert resp.status_code == 201
        data = resp.get_json()['data']
        assert data['uid'] == 'file-xyz'
        assert data['name'] == '巡检.txt'
        assert data['downloadUrl'] == '/api/v1/files/file-xyz/download'

    def test_missing_file(self, setup):
        client, mock_cursor, api_h = setup
        _setup_auth_and_returns(mock_cursor, fetchone_returns=[
            (True, True),  # public + writable
        ])
        resp = client.post(
            '/api/v1/files',
            data={'collection': 'devices'},
            content_type='multipart/form-data', headers=api_h)
        assert resp.status_code == 400
