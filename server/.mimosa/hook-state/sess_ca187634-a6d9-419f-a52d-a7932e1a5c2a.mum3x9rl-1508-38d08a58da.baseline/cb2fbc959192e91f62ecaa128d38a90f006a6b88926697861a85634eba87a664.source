"""
备份路由 API 测试

测试 /backups 相关的所有端点。
"""

import os
import sys
import json
import zipfile
import tempfile
import pytest
from unittest.mock import MagicMock, patch
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
    fake_db = _make_mock_db(mock_conn)
    patches = [
        patch('db.get_db', fake_db),
        patch('routes.backups.get_db', fake_db),
        patch('utils.backup.get_db', fake_db),
        patch('db.pool', MagicMock()),
    ]
    for p in patches:
        p.start()

    from app import app
    app.config['TESTING'] = True
    admin = create_token({'id': 'user-admin', 'username': 'admin', 'role': 'admin'})
    dev = create_token({'id': 'user-dev', 'username': 'dev', 'role': 'developer'})

    yield (
        app.test_client(),
        mock_cursor,
        {'Authorization': f'Bearer {admin}'},
        {'Authorization': f'Bearer {dev}'},
    )

    for p in patches:
        p.stop()


now = datetime(2024, 1, 1, tzinfo=timezone.utc)


class TestListBackups:
    """测试 GET /backups"""

    def test_list_backups_returns_empty_list(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchall.return_value = []

        res = client.get('/backups', headers=admin_h)

        assert res.status_code == 200
        assert res.json == []

    def test_list_backups_returns_backups(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchall.return_value = [
            ('bk-1', '手动备份', 'manual', 'completed', '/tmp/bk-1.zip', 1024, 12, 100, 'admin', now, None, 'full', []),
            ('bk-2', '表级备份', 'manual', 'completed', '/tmp/bk-2.zip', 512, 2, 10, 'admin', now, None, 'partial', ['menus', 'users']),
        ]

        res = client.get('/backups', headers=admin_h)

        assert res.status_code == 200
        assert len(res.json) == 2
        assert res.json[0]['id'] == 'bk-1'
        assert res.json[0]['backupScope'] == 'full'
        assert res.json[1]['backupScope'] == 'partial'
        assert res.json[1]['backupTables'] == ['menus', 'users']


class TestCreateBackup:
    """测试 POST /backups"""

    @patch('routes.backups.create_backup')
    def test_create_full_backup(self, mock_create, setup):
        client, _, admin_h, _ = setup
        mock_create.return_value = {
            'id': 'bk-new',
            'name': '手动备份(全量)',
            'type': 'manual',
            'status': 'completed',
            'filePath': '/tmp/bk-new.zip',
            'fileSize': 1024,
            'tablesCount': 12,
            'recordsCount': 100,
            'createdBy': 'admin',
            'createdAt': now.isoformat(),
            'note': None,
            'backupScope': 'full',
            'backupTables': [],
        }

        res = client.post('/backups', headers=admin_h, json={})

        assert res.status_code == 201
        assert res.json['backupScope'] == 'full'
        # 仅全量：不再向 create_backup 传 tables
        mock_create.assert_called_once_with(backup_type='manual', created_by='admin')

    @patch('routes.backups.create_backup')
    def test_create_backup_with_note(self, mock_create, setup):
        client, _, admin_h, _ = setup
        mock_create.return_value = {
            'id': 'bk-note',
            'name': '手动备份(全量)',
            'type': 'manual',
            'status': 'completed',
            'filePath': '/tmp/bk-note.zip',
            'fileSize': 1024,
            'tablesCount': 12,
            'recordsCount': 100,
            'createdBy': 'admin',
            'createdAt': now.isoformat(),
            'note': None,
            'backupScope': 'full',
            'backupTables': [],
        }

        res = client.post('/backups', headers=admin_h, json={
            'note': '部署前备份',
        })

        assert res.status_code == 201
        mock_create.assert_called_once()


class TestRestoreBackup:
    """测试 POST /backups/:id/restore"""

    @patch('routes.backups.restore_backup')
    @patch('routes.backups.os.path.isfile')
    def test_restore_full_backup(self, mock_isfile, mock_restore, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = ('/tmp/bk-1.zip',)
        mock_isfile.return_value = True  # 模拟文件存在
        mock_restore.return_value = {
            'id': 'bk-1',
            'name': '手动备份',
            'tables': ['menus', 'users', 'dynamic_data'],
        }

        res = client.post('/backups/bk-1/restore', headers=admin_h, json={})

        assert res.status_code == 200
        assert res.json['message'] == '还原成功'
        # 仅整包全量还原：始终 mode='replace'，不传 tables
        mock_restore.assert_called_once_with('/tmp/bk-1.zip', mode='replace')

    def test_restore_nonexistent_backup(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = None

        res = client.post('/backups/nonexistent/restore', headers=admin_h, json={})

        assert res.status_code == 404
        assert '备份不存在' in res.json['error']


class TestDeleteBackup:
    """测试 DELETE /backups/:id"""

    @patch('routes.backups.delete_backup_file')
    def test_delete_backup(self, mock_delete, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = ('/tmp/bk-1.zip',)

        res = client.delete('/backups/bk-1', headers=admin_h)

        assert res.status_code == 200
        mock_delete.assert_called_once_with('/tmp/bk-1.zip')

    def test_delete_nonexistent_backup(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = None

        res = client.delete('/backups/nonexistent', headers=admin_h)

        assert res.status_code == 404


class TestDownloadBackup:
    """测试 GET /backups/:id/download"""

    def test_download_nonexistent_backup(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = None

        res = client.get('/backups/nonexistent/download', headers=admin_h)

        assert res.status_code == 404

    def test_download_missing_file(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = ('/nonexistent/path.zip', '备份名称')

        res = client.get('/backups/bk-1/download', headers=admin_h)

        assert res.status_code == 404


class TestBackupSettings:
    """测试 GET/PUT /backups/settings"""

    def test_get_settings(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = (True, 'daily', 10, None, None)

        res = client.get('/backups/settings', headers=admin_h)

        assert res.status_code == 200
        assert res.json['enabled'] is True
        assert res.json['interval'] == 'daily'
        assert res.json['retentionCount'] == 10

    def test_update_settings(self, setup):
        client, mock_cursor, admin_h, _ = setup
        mock_cursor.fetchone.return_value = (True, 'weekly', 5, None, now)

        res = client.put('/backups/settings', headers=admin_h, json={
            'enabled': True,
            'interval': 'weekly',
            'retentionCount': 5,
        })

        assert res.status_code == 200

    def test_update_settings_invalid_interval(self, setup):
        client, _, admin_h, _ = setup
        res = client.put('/backups/settings', headers=admin_h, json={
            'enabled': True,
            'interval': 'invalid',
            'retentionCount': 5,
        })

        assert res.status_code == 400

    def test_update_settings_invalid_retention(self, setup):
        client, _, admin_h, _ = setup
        res = client.put('/backups/settings', headers=admin_h, json={
            'enabled': True,
            'interval': 'daily',
            'retentionCount': 0,
        })

        assert res.status_code == 400