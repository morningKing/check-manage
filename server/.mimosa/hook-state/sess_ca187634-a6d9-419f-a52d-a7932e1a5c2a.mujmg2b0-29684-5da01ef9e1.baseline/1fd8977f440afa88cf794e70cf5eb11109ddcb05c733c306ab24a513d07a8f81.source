"""
备份数据对比功能单元测试

测试 _compute_diff、_merge_relations、diff_collection 路由端点。
"""

import sys
import os
import json
import pytest
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ==================== _compute_diff 纯函数测试 ====================


class TestComputeDiff:
    def test_all_added(self):
        from routes.backups import _compute_diff

        base = []
        target = [
            {'id': 'r1', 'name': 'A'},
            {'id': 'r2', 'name': 'B'},
        ]
        result = _compute_diff(base, target, ['name'])
        assert len(result['added']) == 2
        assert len(result['removed']) == 0
        assert len(result['modified']) == 0
        assert result['unchangedCount'] == 0

    def test_all_removed(self):
        from routes.backups import _compute_diff

        base = [
            {'id': 'r1', 'name': 'A'},
            {'id': 'r2', 'name': 'B'},
        ]
        target = []
        result = _compute_diff(base, target, ['name'])
        assert len(result['added']) == 0
        assert len(result['removed']) == 2
        assert len(result['modified']) == 0

    def test_modified_records(self):
        from routes.backups import _compute_diff

        base = [{'id': 'r1', 'name': 'old', 'status': 'draft'}]
        target = [{'id': 'r1', 'name': 'new', 'status': 'draft'}]
        result = _compute_diff(base, target, ['name', 'status'])
        assert len(result['modified']) == 1
        mod = result['modified'][0]
        assert mod['id'] == 'r1'
        assert len(mod['fields']) == 1
        assert mod['fields'][0]['fieldName'] == 'name'
        assert mod['fields'][0]['oldValue'] == 'old'
        assert mod['fields'][0]['newValue'] == 'new'

    def test_unchanged_records(self):
        from routes.backups import _compute_diff

        base = [{'id': 'r1', 'name': 'same'}]
        target = [{'id': 'r1', 'name': 'same'}]
        result = _compute_diff(base, target, ['name'])
        assert len(result['modified']) == 0
        assert result['unchangedCount'] == 1

    def test_mixed_changes(self):
        from routes.backups import _compute_diff

        base = [
            {'id': 'r1', 'name': 'A'},
            {'id': 'r2', 'name': 'B'},
            {'id': 'r3', 'name': 'C'},
        ]
        target = [
            {'id': 'r2', 'name': 'B-modified'},
            {'id': 'r3', 'name': 'C'},
            {'id': 'r4', 'name': 'D'},
        ]
        result = _compute_diff(base, target, ['name'])
        assert len(result['added']) == 1  # r4
        assert result['added'][0]['id'] == 'r4'
        assert len(result['removed']) == 1  # r1
        assert result['removed'][0]['id'] == 'r1'
        assert len(result['modified']) == 1  # r2
        assert result['modified'][0]['id'] == 'r2'
        assert result['unchangedCount'] == 1  # r3

    def test_empty_both(self):
        from routes.backups import _compute_diff

        result = _compute_diff([], [], ['name'])
        assert result == {
            'added': [],
            'removed': [],
            'modified': [],
            'unchangedCount': 0,
        }

    def test_multiple_field_changes(self):
        from routes.backups import _compute_diff

        base = [{'id': 'r1', 'name': 'old', 'status': 'A', 'count': 1}]
        target = [{'id': 'r1', 'name': 'new', 'status': 'B', 'count': 1}]
        result = _compute_diff(base, target, ['name', 'status', 'count'])
        assert len(result['modified']) == 1
        assert len(result['modified'][0]['fields']) == 2  # name + status changed, count same


# ==================== _merge_relations 测试 ====================


class TestMergeRelations:
    def test_merge_adds_relation_to_records(self):
        from routes.backups import _merge_relations

        records = [
            {'id': 'r1', 'name': 'A'},
            {'id': 'r2', 'name': 'B'},
        ]
        rel_map = {
            'r1': {'tags': ['t1', 't2']},
        }
        relation_fields = [{'fieldName': 'tags'}]

        _merge_relations(records, rel_map, relation_fields)

        assert records[0]['tags'] == ['t1', 't2']
        assert records[1]['tags'] == []  # no relation data

    def test_merge_empty_rel_map(self):
        from routes.backups import _merge_relations

        records = [{'id': 'r1', 'name': 'A'}]
        _merge_relations(records, {}, [{'fieldName': 'related'}])
        assert records[0]['related'] == []

    def test_merge_multiple_relation_fields(self):
        from routes.backups import _merge_relations

        records = [{'id': 'r1'}]
        rel_map = {
            'r1': {
                'cases': ['c1', 'c2'],
                'templates': ['t1'],
            },
        }
        relation_fields = [
            {'fieldName': 'cases'},
            {'fieldName': 'templates'},
        ]

        _merge_relations(records, rel_map, relation_fields)
        assert records[0]['cases'] == ['c1', 'c2']
        assert records[0]['templates'] == ['t1']


# ==================== diff_collection 路由端点测试 ====================


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
        patch('routes.backups.get_db', fake_db),
        patch('routes.menus.get_db', fake_db),
        patch('routes.dynamic.get_db', fake_db),
        patch('routes.etl_tasks.get_db', fake_db),
        patch('routes.page_configs.get_db', fake_db),
        patch('routes.users.get_db', fake_db),
        patch('routes.relations.get_db', fake_db),
        patch('db.pool', MagicMock()),
        patch('utils.operation_log.log_operation'),
        patch('utils.backup.get_db', fake_db),
    ]
    for p in patches:
        p.start()

    from app import app
    from auth import create_token

    app.config['TESTING'] = True
    token = create_token({'id': 'u1', 'username': 'admin', 'role': 'admin'})
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

    yield app.test_client(), mock_cursor, mock_conn, headers

    for p in patches:
        p.stop()


class TestDiffRoute:
    def test_missing_params_returns_400(self, setup):
        client, _, _, headers = setup
        resp = client.post('/backups/diff', headers=headers, json={})
        assert resp.status_code == 400

    def test_same_source_returns_400(self, setup):
        client, _, _, headers = setup
        resp = client.post('/backups/diff', headers=headers, json={
            'collection': 'test',
            'baseSource': 'current',
            'targetSource': 'current',
        })
        assert resp.status_code == 400
        data = resp.get_json()
        assert '不能相同' in data['error']

    def test_diff_current_vs_current_rejected(self, setup):
        client, _, _, headers = setup
        resp = client.post('/backups/diff', headers=headers, json={
            'collection': 'test',
            'baseSource': 'current',
            'targetSource': 'current',
        })
        assert resp.status_code == 400

    def test_non_admin_rejected(self, mock_conn, mock_cursor):
        """developer 角色不能访问 diff 端点"""
        fake_db = _make_mock_db(mock_conn)
        patches = [
            patch('db.get_db', fake_db),
            patch('routes.backups.get_db', fake_db),
            patch('routes.menus.get_db', fake_db),
            patch('routes.dynamic.get_db', fake_db),
            patch('routes.etl_tasks.get_db', fake_db),
            patch('routes.page_configs.get_db', fake_db),
            patch('routes.users.get_db', fake_db),
            patch('routes.relations.get_db', fake_db),
            patch('db.pool', MagicMock()),
            patch('utils.operation_log.log_operation'),
            patch('utils.backup.get_db', fake_db),
        ]
        for p in patches:
            p.start()

        from app import app
        from auth import create_token

        app.config['TESTING'] = True
        dev_token = create_token({'id': 'u2', 'username': 'dev', 'role': 'developer'})
        dev_headers = {
            'Authorization': f'Bearer {dev_token}',
            'Content-Type': 'application/json',
        }

        client = app.test_client()
        resp = client.post('/backups/diff', headers=dev_headers, json={
            'collection': 'test',
            'baseSource': 'current',
            'targetSource': 'bk-1',
        })
        assert resp.status_code == 403

        for p in patches:
            p.stop()

    def test_diff_current_vs_backup_not_found(self, setup):
        """备份不存在时返回 400"""
        client, mock_cursor, _, headers = setup
        # 模拟 page_configs 查询返回空（备份查找之后还会查 page_configs）
        # 第一次 fetchone 是查 backup file_path -> None
        mock_cursor.fetchone.return_value = None

        resp = client.post('/backups/diff', headers=headers, json={
            'collection': 'test',
            'baseSource': 'current',
            'targetSource': 'bk-nonexist',
        })
        assert resp.status_code == 400
