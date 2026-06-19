"""
动态数据路由单元测试

测试 RESERVED 集合拦截、基本 CRUD 路由和乐观锁。
"""

import sys
import os
import json
import pytest
from unittest.mock import MagicMock, patch, call
from contextlib import contextmanager
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from auth import create_token


def _make_mock_db(mock_conn):
    @contextmanager
    def fake_get_db():
        yield mock_conn
    return fake_get_db


@pytest.fixture
def setup(mock_conn, mock_cursor):
    """创建带 mock DB 的 Flask 测试应用"""
    fake_db = _make_mock_db(mock_conn)
    patches = [
        patch('db.get_db', fake_db),
        patch('routes.dynamic.get_db', fake_db),
        patch('routes.menus.get_db', fake_db),
        patch('routes.etl_tasks.get_db', fake_db),
        patch('routes.page_configs.get_db', fake_db),
        patch('routes.users.get_db', fake_db),
        patch('routes.relations.get_db', fake_db),
        patch('utils.permissions.get_db', fake_db),
        patch('db.pool', MagicMock()),
        patch('utils.operation_log.log_operation'),
        patch('utils.operation_log.get_page_info', return_value=('测试页面', [])),
        patch('routes.dynamic.get_page_info', return_value=('测试页面', [])),
        patch('utils.operation_log.pick_display_name', return_value='记录名'),
        patch('routes.dynamic.pick_display_name', return_value='记录名'),
        patch('utils.operation_log.get_field_label_map', return_value={}),
        patch('routes.dynamic.get_field_label_map', return_value={}),
        patch('routes.dynamic.log_operation'),
        patch('routes.dynamic.get_validation_script', return_value=None),
        patch('routes.dynamic.check_branch_lock', return_value=None),
        patch('routes.dynamic._get_current_user_branch', return_value='main'),
        patch('utils.webhook_engine.fire_webhooks', return_value={
            'beforeErrors': [],
            'afterErrors': [],
            'beforeBlocked': False,
            'rollbackNeeded': False,
        }),
    ]
    for p in patches:
        p.start()

    # Per-page RBAC gating (Task 3.3/3.4) resolves the role's permissions via
    # utils.permissions, which queries the DB. Prime the cache so the 'admin'
    # role resolves as a superuser (bypasses every page check) without consuming
    # entries from the per-test mock cursor's fetchone/fetchall side effects.
    import utils.permissions as _perms
    _perms.invalidate_cache()
    _perms._cache['admin'] = {
        'is_superuser': True,
        'default_page_access': 'write',
        'admin_keys': set(),
        'page_perms': {},
    }

    from app import app
    app.config['TESTING'] = True
    token = create_token({'id': 'u1', 'username': 'admin', 'role': 'admin'})
    headers = {'Authorization': f'Bearer {token}'}

    yield app.test_client(), mock_cursor, mock_conn, headers

    _perms.invalidate_cache()
    for p in patches:
        p.stop()


class TestReservedCollections:
    """验证保留的集合名被正确拒绝"""

    def test_menus_reserved(self, setup):
        """menus 有自己的蓝图，GET /menus 不走 dynamic"""
        client, _, _, headers = setup
        resp = client.get('/menus', headers=headers)
        assert resp.status_code != 404

    def test_etlTasks_reserved(self, setup):
        """etlTasks 有自己的蓝图"""
        client, _, _, headers = setup
        resp = client.get('/etlTasks', headers=headers)
        assert resp.status_code != 404


class TestListCollection:
    def test_list_returns_records(self, setup):
        client, mock_cursor, _, headers = setup
        now = datetime.now(timezone.utc)
        mock_cursor.fetchone.return_value = (1,)  # total count
        mock_cursor.fetchall.return_value = [
            ('rec-1', 'test-collection', {'name': '记录1'}, now, now, 1),
        ]
        resp = client.get('/test-collection', headers=headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, dict)
        assert 'data' in data
        assert 'total' in data
        assert data['total'] == 1

    def test_list_returns_version(self, setup):
        """列表接口返回 _version 字段"""
        client, mock_cursor, _, headers = setup
        now = datetime.now(timezone.utc)
        mock_cursor.fetchone.return_value = (1,)  # total count
        mock_cursor.fetchall.return_value = [
            ('rec-1', 'test-collection', {'name': '记录1'}, now, now, 3),
        ]
        resp = client.get('/test-collection', headers=headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['data'][0]['_version'] == 3

    def test_list_empty_collection(self, setup):
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchone.return_value = (0,)  # total count = 0
        mock_cursor.fetchall.return_value = []
        resp = client.get('/my-data', headers=headers)
        assert resp.status_code == 200
        result = resp.get_json()
        assert result['data'] == []
        assert result['total'] == 0

    def test_list_pagination(self, setup):
        """测试分页参数"""
        client, mock_cursor, _, headers = setup
        now = datetime.now(timezone.utc)
        mock_cursor.fetchone.return_value = (100,)  # total count
        mock_cursor.fetchall.return_value = [
            ('rec-1', 'test-collection', {'name': '记录1'}, now, now, 1),
        ]
        resp = client.get('/test-collection?page=2&pageSize=20', headers=headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['page'] == 2
        assert data['pageSize'] == 20
        assert data['total'] == 100

    def _executed_sql(self, mock_cursor):
        """返回本次请求实际执行过的所有 SQL 文本（用于断言 ORDER BY）。"""
        return ' || '.join(
            str(c.args[0]) for c in mock_cursor.execute.call_args_list if c.args
        )

    def test_list_default_order_ascending(self, setup):
        """默认按 created_at 升序排列"""
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchone.return_value = (0,)
        mock_cursor.fetchall.return_value = []
        resp = client.get('/test-collection', headers=headers)
        assert resp.status_code == 200
        assert 'ORDER BY created_at ASC, id ASC' in self._executed_sql(mock_cursor)

    def test_list_ids_filter(self, setup):
        """ids= 按真实 id 列过滤（id 不在 data JSONB，不能用 q）"""
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchone.return_value = (0,)
        mock_cursor.fetchall.return_value = []
        resp = client.get('/test-collection?ids=a,b,c&all=true', headers=headers)
        assert resp.status_code == 200
        sql = self._executed_sql(mock_cursor)
        assert 'id = ANY(' in sql
        # 该 id 列表作为参数传入（防注入）
        params = [c.args[1] for c in mock_cursor.execute.call_args_list if c.args and len(c.args) > 1]
        assert any(['a', 'b', 'c'] in (p or []) for p in params)

    def test_list_ids_empty_matches_nothing(self, setup):
        """ids= 传了但为空 → id = ANY('{}') 不匹配任何记录"""
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchone.return_value = (0,)
        mock_cursor.fetchall.return_value = []
        resp = client.get('/test-collection?ids=', headers=headers)
        assert resp.status_code == 200
        assert 'id = ANY(' in self._executed_sql(mock_cursor)

    def test_list_sort_desc(self, setup):
        """sort=createdAt&order=desc 生成降序 ORDER BY（最近记录在前）"""
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchone.return_value = (0,)
        mock_cursor.fetchall.return_value = []
        resp = client.get('/test-collection?sort=createdAt&order=desc', headers=headers)
        assert resp.status_code == 200
        assert 'ORDER BY created_at DESC, id DESC' in self._executed_sql(mock_cursor)

    def test_list_sort_invalid_field_falls_back(self, setup):
        """非法 sort 列名被白名单拒绝，回退到 created_at（防 SQL 注入）"""
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchone.return_value = (0,)
        mock_cursor.fetchall.return_value = []
        resp = client.get('/test-collection?sort=data;DROP TABLE&order=desc', headers=headers)
        assert resp.status_code == 200
        sql = self._executed_sql(mock_cursor)
        assert 'DROP TABLE' not in sql
        assert 'ORDER BY created_at DESC, id DESC' in sql


class TestCreateRecord:
    def test_create_returns_201(self, setup):
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchone.return_value = None
        resp = client.post('/test-collection',
                           data=json.dumps({'name': '新记录'}),
                           content_type='application/json',
                           headers=headers)
        assert resp.status_code == 201

    def test_create_returns_version_1(self, setup):
        """新建记录返回 _version=1"""
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchone.return_value = None
        resp = client.post('/test-collection',
                           data=json.dumps({'name': '新记录'}),
                           content_type='application/json',
                           headers=headers)
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['_version'] == 1


class TestUpdateRecord:
    def test_update_increments_version(self, setup):
        """更新记录后 version 递增"""
        client, mock_cursor, _, headers = setup
        # fetchone 调用序列：
        # 1. before webhook: SELECT data → 返回旧数据
        # 2. SELECT data, version → 返回旧数据和版本号
        # 3. get_primary_key_fields → None (无主键配置)
        mock_cursor.fetchone.side_effect = [
            {'name': '旧记录'},                     # 1. before webhook: old_data
            ({'name': '旧记录'}, 2),               # 2. old data + version
            None,                                  # 3. pk_fields: no page config
        ]
        mock_cursor.rowcount = 1  # UPDATE affected 1 row
        resp = client.put('/test-collection/rec-1',
                          data=json.dumps({'name': '新名', '_version': 2}),
                          content_type='application/json',
                          headers=headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['_version'] == 3

    def test_update_version_conflict(self, setup):
        """客户端版本不匹配时返回 409"""
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchone.side_effect = [
            {'name': '旧记录'},                     # 1. before webhook: old_data
            ({'name': '旧记录'}, 5),               # 2. old data + version=5
            None,                                  # 3. pk_fields
        ]
        # 客户端携带 _version=3，但数据库已经是 version=5
        resp = client.put('/test-collection/rec-1',
                          data=json.dumps({'name': '新名', '_version': 3}),
                          content_type='application/json',
                          headers=headers)
        assert resp.status_code == 409
        data = resp.get_json()
        assert data['code'] == 'VERSION_CONFLICT'

    def test_update_without_version_still_works(self, setup):
        """不携带 _version 的更新请求仍然成功（向后兼容）"""
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchone.side_effect = [
            {'name': '旧记录'},                     # 1. before webhook: old_data
            ({'name': '旧记录'}, 1),               # 2. old data + version
            None,                                  # 3. pk_fields
        ]
        mock_cursor.rowcount = 1
        resp = client.put('/test-collection/rec-1',
                          data=json.dumps({'name': '新名'}),
                          content_type='application/json',
                          headers=headers)
        assert resp.status_code == 200

    def test_update_race_condition(self, setup):
        """版本匹配但 UPDATE rowcount=0 时返回 409（竞态条件）"""
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchone.side_effect = [
            {'name': '旧记录'},                     # 1. before webhook: old_data
            ({'name': '旧记录'}, 2),               # 2. old data + version
            None,                                  # 3. pk_fields
        ]
        mock_cursor.rowcount = 0  # UPDATE 匹配0行（被其他请求先改了）
        resp = client.put('/test-collection/rec-1',
                          data=json.dumps({'name': '新名', '_version': 2}),
                          content_type='application/json',
                          headers=headers)
        assert resp.status_code == 409

    def test_update_nonexistent_record(self, setup):
        """更新不存在的记录返回 404"""
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchone.side_effect = [
            None,   # before webhook: old_data (record not found)
            None,   # pk_fields
            None,   # old data → record not found
        ]
        resp = client.put('/test-collection/rec-999',
                          data=json.dumps({'name': '新名', '_version': 1}),
                          content_type='application/json',
                          headers=headers)
        assert resp.status_code == 404


class TestUnauthorized:
    def test_no_token(self, setup):
        client, _, _, _ = setup
        resp = client.get('/test-collection')
        assert resp.status_code == 401


class TestBatchCreate:
    """批量创建接口测试"""

    def test_batch_create_success(self, setup):
        """批量创建成功"""
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchall.return_value = []  # no existing IDs

        records = [
            {'id': 'rec-1', 'data': {'name': '记录1'}, 'relations': {}},
            {'id': 'rec-2', 'data': {'name': '记录2'}, 'relations': {}},
        ]
        resp = client.post('/test-collection/batch-create',
                          data=json.dumps({'records': records}),
                          content_type='application/json',
                          headers=headers)

        assert resp.status_code == 201
        data = resp.get_json()
        assert data['success'] is True
        assert data['created'] == 2
        assert data['failed'] == 0

    def test_batch_create_duplicate_id_in_batch(self, setup):
        """批量创建时批内有重复 ID"""
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchall.return_value = []  # no existing IDs in DB

        records = [
            {'id': 'rec-1', 'data': {'name': '记录1'}, 'relations': {}},
            {'id': 'rec-1', 'data': {'name': '记录2'}, 'relations': {}},  # duplicate ID
        ]
        resp = client.post('/test-collection/batch-create',
                          data=json.dumps({'records': records}),
                          content_type='application/json',
                          headers=headers)

        assert resp.status_code == 409
        data = resp.get_json()
        assert 'failed' in data
        assert len(data['errors']) == 2

    def test_batch_create_existing_id_in_db(self, setup):
        """批量创建时数据库已有相同 ID → UPSERT 而不是失败。

        旧行为是把存在的 id 标 failed,但用户报告导入场景需要"主键冲突则更新"。
        现在 batch-create 在 ON CONFLICT (id, branch_id) 上 DO UPDATE,响应里
        把 created 和 updated 分开计数,failed 应为 0。
        """
        client, mock_cursor, _, headers = setup
        # Mock database has 'rec-1' already.
        # 末尾的空列表对应批量写入后 reseed_sequences 的 page_configs 扫描查询
        # （test-collection 无 autoSequence 字段 → 返回空 → 不再有后续查询）。
        mock_cursor.fetchall.side_effect = [
            [('rec-1',)],  # existing IDs query
            [],            # reseed_sequences: page_configs autoSequence-fields scan
        ]

        records = [
            {'id': 'rec-1', 'data': {'name': '记录1'}, 'relations': {}},
            {'id': 'rec-2', 'data': {'name': '记录2'}, 'relations': {}},
        ]
        resp = client.post('/test-collection/batch-create',
                          data=json.dumps({
                              'records': records,
                              'options': {'continueOnError': True}
                          }),
                          content_type='application/json',
                          headers=headers)

        assert resp.status_code == 201
        data = resp.get_json()
        assert data['created'] == 1   # rec-2 inserted
        assert data['updated'] == 1   # rec-1 upserted (was previously rejected)
        assert data['failed'] == 0

    def test_batch_create_with_relations(self, setup):
        """批量创建包含关联关系"""
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchall.return_value = []  # no existing IDs

        # Mock field config with relation
        with patch('routes.dynamic.get_page_info', return_value=('测试页面', [
            {'fieldName': 'relatedItems', 'controlType': 'relation',
             'relationConfig': {'targetCollection': 'other-collection', 'targetField': 'parentItems'}}
        ])):
            records = [
                {
                    'id': 'rec-1',
                    'data': {'name': '记录1'},
                    'relations': {'relatedItems': ['other-1', 'other-2']}
                },
            ]
            resp = client.post('/test-collection/batch-create',
                              data=json.dumps({'records': records}),
                              content_type='application/json',
                              headers=headers)

            assert resp.status_code == 201
            data = resp.get_json()
            assert data['created'] == 1

    def test_batch_create_continue_on_error(self, setup):
        """批量创建部分失败时继续处理"""
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchall.return_value = []

        # First record will fail validation
        with patch('routes.dynamic.get_validation_script', return_value='raise Exception("test")'):
            records = [
                {'id': 'rec-1', 'data': {'name': '记录1'}, 'relations': {}},
                {'id': 'rec-2', 'data': {'name': '记录2'}, 'relations': {}},
            ]
            resp = client.post('/test-collection/batch-create',
                              data=json.dumps({
                                  'records': records,
                                  'options': {'continueOnError': True}
                              }),
                              content_type='application/json',
                              headers=headers)

            assert resp.status_code == 201
            data = resp.get_json()
            assert data['created'] >= 0
            assert data['failed'] >= 0

    def test_batch_create_empty_records(self, setup):
        """批量创建时记录为空"""
        client, _, _, headers = setup
        resp = client.post('/test-collection/batch-create',
                          data=json.dumps({'records': []}),
                          content_type='application/json',
                          headers=headers)

        assert resp.status_code == 400
        data = resp.get_json()
        assert 'error' in data

    def test_batch_create_validation_error(self, setup):
        """批量创建时校验失败"""
        client, mock_cursor, _, headers = setup
        mock_cursor.fetchall.return_value = []

        # Mock validation script that returns errors
        def mock_run_script(*args, **kwargs):
            return (['字段不能为空'], [], [])

        with patch('routes.dynamic.get_validation_script', return_value='script'):
            with patch('utils.script_runner.run_validation_script', side_effect=mock_run_script):
                records = [
                    {'id': 'rec-1', 'data': {'name': ''}, 'relations': {}},
                ]
                resp = client.post('/test-collection/batch-create',
                                  data=json.dumps({
                                      'records': records,
                                      'options': {'continueOnError': True}
                                  }),
                                  content_type='application/json',
                                  headers=headers)

                assert resp.status_code == 201
                data = resp.get_json()
                assert data['failed'] == 1
                assert len(data['errors']) == 1

    def test_batch_create_reserved_collection(self, setup):
        """批量创建保留集合返回 404"""
        client, _, _, headers = setup
        records = [{'id': 'r1', 'data': {}, 'relations': {}}]
        resp = client.post('/menus/batch-create',
                          data=json.dumps({'records': records}),
                          content_type='application/json',
                          headers=headers)

        assert resp.status_code == 404
