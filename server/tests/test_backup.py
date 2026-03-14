"""
备份工具单元测试

测试 create_backup、restore_backup、is_backup_due、cleanup_old_backups。
"""

import os
import sys
import json
import zipfile
import tempfile
import pytest
from unittest.mock import MagicMock, patch, mock_open
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _make_mock_db(mock_conn):
    @contextmanager
    def fake_get_db():
        yield mock_conn
    return fake_get_db


class TestCreateBackup:
    """测试 create_backup 函数"""

    @patch('utils.backup._ensure_backup_dir')
    @patch('utils.backup.get_db')
    @patch('utils.backup.os.path.getsize')
    def test_create_backup_exports_all_tables(self, mock_getsize, mock_get_db, mock_ensure_dir):
        """备份应导出所有配置的表"""
        from utils.backup import create_backup, BACKUP_TABLES

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = lambda self: mock_conn
        mock_conn.__exit__ = lambda self, *args: None
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        # 模拟每个表返回空数据
        mock_cursor.fetchall.return_value = []

        mock_getsize.return_value = 1024

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('utils.backup.BACKUP_DIR', tmpdir):
                result = create_backup(backup_type='manual', created_by='admin')

                assert result['type'] == 'manual'
                assert result['status'] == 'completed'
                assert result['createdBy'] == 'admin'
                # 应该调用 fetchall 每个表一次
                assert mock_cursor.fetchall.call_count >= len(BACKUP_TABLES)

    @patch('utils.backup._ensure_backup_dir')
    @patch('utils.backup.get_db')
    @patch('utils.backup.os.path.getsize')
    def test_backup_zip_structure(self, mock_getsize, mock_get_db, mock_ensure_dir):
        """备份 ZIP 应包含 manifest.json 和各表 JSON"""
        from utils.backup import create_backup

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = lambda self: mock_conn
        mock_conn.__exit__ = lambda self, *args: None
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []
        mock_get_db.return_value = mock_conn
        mock_getsize.return_value = 1024

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('utils.backup.BACKUP_DIR', tmpdir):
                result = create_backup()
                zip_path = result['filePath']

                # 验证 ZIP 文件存在
                assert os.path.isfile(zip_path)

                # 验证 ZIP 内容
                with zipfile.ZipFile(zip_path, 'r') as zf:
                    names = zf.namelist()
                    assert 'manifest.json' in names
                    # 读取 manifest
                    manifest = json.loads(zf.read('manifest.json'))
                    assert 'version' in manifest
                    assert 'id' in manifest
                    assert 'tables' in manifest

    @patch('utils.backup._ensure_backup_dir')
    @patch('utils.backup.get_db')
    @patch('utils.backup.os.path.getsize')
    def test_backup_contains_table_data(self, mock_getsize, mock_get_db, mock_ensure_dir):
        """备份应正确包含表数据"""
        from utils.backup import create_backup

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = lambda self: mock_conn
        mock_conn.__exit__ = lambda self, *args: None
        mock_conn.cursor.return_value = mock_cursor

        # 模拟 menus 表有数据
        mock_cursor.fetchall.side_effect = [
            [('menu-1', '菜单1', 'icon-menu', None, None, 1, None, None)],  # menus
            [],  # page_configs
            [],  # dynamic_data
            [],  # data_relations
            [],  # users
            [],  # operation_logs
            [],  # export_scripts
            [],  # api_keys
            [],  # validation_scripts
            [],  # etl_tasks
            [],  # etl_logs
            [],  # collection_versions
            [],  # version_snapshots
            [],  # version_relations
            [],  # user_current_branch
        ]
        mock_get_db.return_value = mock_conn
        mock_getsize.return_value = 2048

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('utils.backup.BACKUP_DIR', tmpdir):
                result = create_backup()

                zip_path = result['filePath']
                with zipfile.ZipFile(zip_path, 'r') as zf:
                    menus_data = json.loads(zf.read('menus.json'))
                    assert len(menus_data) == 1


class TestRestoreBackup:
    """测试 restore_backup 函数"""

    def test_restore_nonexistent_file_raises_error(self):
        """还原不存在的文件应抛出错误"""
        from utils.backup import restore_backup

        with pytest.raises(FileNotFoundError):
            restore_backup('/nonexistent/path.zip')

    @patch('db.pool')
    def test_restore_valid_backup(self, mock_pool):
        """还原有效备份应成功"""
        from utils.backup import restore_backup, BACKUP_VERSION

        # 创建临时备份文件
        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = os.path.join(tmpdir, 'test-backup.zip')

            # 创建模拟的备份数据
            manifest = {
                'version': BACKUP_VERSION,
                'id': 'backup-test',
                'name': '测试备份',
                'type': 'manual',
                'createdAt': datetime.now(timezone.utc).isoformat(),
                'tables': {'menus': 0},
                'totalRecords': 0,
            }

            with zipfile.ZipFile(zip_path, 'w') as zf:
                zf.writestr('manifest.json', json.dumps(manifest))
                zf.writestr('menus.json', '[]')
                zf.writestr('page_configs.json', '[]')
                zf.writestr('dynamic_data.json', '[]')
                zf.writestr('data_relations.json', '[]')
                zf.writestr('users.json', '[]')
                zf.writestr('operation_logs.json', '[]')
                zf.writestr('export_scripts.json', '[]')
                zf.writestr('api_keys.json', '[]')
                zf.writestr('validation_scripts.json', '[]')
                zf.writestr('etl_tasks.json', '[]')
                zf.writestr('etl_logs.json', '[]')
                zf.writestr('collection_versions.json', '[]')
                zf.writestr('version_snapshots.json', '[]')
                zf.writestr('version_relations.json', '[]')
                zf.writestr('user_current_branch.json', '[]')

            # Mock 数据库连接
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_pool.getconn.return_value = mock_conn
            mock_pool.putconn.return_value = None

            result = restore_backup(zip_path)

            assert result['id'] == 'backup-test'
            # 应该执行 TRUNCATE 和 INSERT
            assert mock_cursor.execute.call_count > 0
            mock_conn.commit.assert_called_once()

    @patch('db.pool')
    def test_restore_rollback_on_error(self, mock_pool):
        """还原失败应回滚事务"""
        from utils.backup import restore_backup

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = os.path.join(tmpdir, 'test-backup.zip')

            manifest = {'version': 1, 'id': 'bk-1', 'name': 'test', 'type': 'manual', 'createdAt': '', 'tables': {}, 'totalRecords': 0}
            with zipfile.ZipFile(zip_path, 'w') as zf:
                zf.writestr('manifest.json', json.dumps(manifest))
                zf.writestr('menus.json', '[]')
                zf.writestr('page_configs.json', '[]')
                zf.writestr('dynamic_data.json', '[]')
                zf.writestr('data_relations.json', '[]')
                zf.writestr('users.json', '[]')
                zf.writestr('operation_logs.json', '[]')
                zf.writestr('export_scripts.json', '[]')
                zf.writestr('api_keys.json', '[]')
                zf.writestr('validation_scripts.json', '[]')
                zf.writestr('etl_tasks.json', '[]')
                zf.writestr('etl_logs.json', '[]')
                zf.writestr('collection_versions.json', '[]')
                zf.writestr('version_snapshots.json', '[]')
                zf.writestr('version_relations.json', '[]')
                zf.writestr('user_current_branch.json', '[]')

            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            # 模拟执行失败
            mock_cursor.execute.side_effect = Exception('Database error')
            mock_pool.getconn.return_value = mock_conn

            with pytest.raises(Exception, match='Database error'):
                restore_backup(zip_path)

            # 应该回滚
            mock_conn.rollback.assert_called_once()


class TestIsBackupDue:
    """测试 is_backup_due 函数"""

    def test_no_last_backup_returns_true(self):
        """无上次备份时间时应立即执行"""
        from utils.backup import is_backup_due

        settings = {'lastBackupAt': None, 'interval': 'daily'}
        assert is_backup_due(settings) is True

    def test_invalid_last_backup_returns_true(self):
        """无效的上次备份时间应返回 True"""
        from utils.backup import is_backup_due

        settings = {'lastBackupAt': 'invalid-date', 'interval': 'daily'}
        assert is_backup_due(settings) is True

    def test_daily_interval(self):
        """每日备份间隔测试"""
        from utils.backup import is_backup_due

        now = datetime.now(timezone.utc)

        # 23小时前，不应备份
        last = now - timedelta(hours=23)
        settings = {'lastBackupAt': last.isoformat(), 'interval': 'daily'}
        assert is_backup_due(settings) is False

        # 25小时前，应备份
        last = now - timedelta(hours=25)
        settings = {'lastBackupAt': last.isoformat(), 'interval': 'daily'}
        assert is_backup_due(settings) is True

    def test_weekly_interval(self):
        """每周备份间隔测试"""
        from utils.backup import is_backup_due

        now = datetime.now(timezone.utc)

        # 6天前，不应备份
        last = now - timedelta(days=6)
        settings = {'lastBackupAt': last.isoformat(), 'interval': 'weekly'}
        assert is_backup_due(settings) is False

        # 8天前，应备份
        last = now - timedelta(days=8)
        settings = {'lastBackupAt': last.isoformat(), 'interval': 'weekly'}
        assert is_backup_due(settings) is True

    def test_monthly_interval(self):
        """每月备份间隔测试"""
        from utils.backup import is_backup_due

        now = datetime.now(timezone.utc)

        # 29天前，不应备份
        last = now - timedelta(days=29)
        settings = {'lastBackupAt': last.isoformat(), 'interval': 'monthly'}
        assert is_backup_due(settings) is False

        # 31天前，应备份
        last = now - timedelta(days=31)
        settings = {'lastBackupAt': last.isoformat(), 'interval': 'monthly'}
        assert is_backup_due(settings) is True

    def test_naive_datetime_treated_as_utc(self):
        """无时区的日期时间应视为 UTC"""
        from utils.backup import is_backup_due

        now = datetime.now(timezone.utc)

        # 创建无时区的日期时间
        last_naive = (now - timedelta(hours=25)).replace(tzinfo=None)
        settings = {'lastBackupAt': last_naive, 'interval': 'daily'}
        assert is_backup_due(settings) is True


class TestCleanupOldBackups:
    """测试 cleanup_old_backups 函数"""

    @patch('utils.backup.get_db')
    @patch('utils.backup.delete_backup_file')
    def test_cleanup_removes_excess_backups(self, mock_delete_file, mock_get_db):
        """清理应删除超出保留数量的备份"""
        from utils.backup import cleanup_old_backups

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = lambda self: mock_conn
        mock_conn.__exit__ = lambda self, *args: None
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        # 模拟有 5 个定时备份，保留 3 个
        mock_cursor.fetchall.return_value = [
            ('bk-1', '/backups/bk-1.zip'),
            ('bk-2', '/backups/bk-2.zip'),
            ('bk-3', '/backups/bk-3.zip'),
            ('bk-4', '/backups/bk-4.zip'),
            ('bk-5', '/backups/bk-5.zip'),
        ]

        cleanup_old_backups(3)

        # 应删除 bk-4 和 bk-5
        assert mock_delete_file.call_count == 2
        assert mock_cursor.execute.call_count >= 2  # 至少删除两条记录

    @patch('utils.backup.get_db')
    @patch('utils.backup.delete_backup_file')
    def test_cleanup_keeps_all_if_under_limit(self, mock_delete_file, mock_get_db):
        """备份数量未超限不应删除"""
        from utils.backup import cleanup_old_backups

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = lambda self: mock_conn
        mock_conn.__exit__ = lambda self, *args: None
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        # 模拟只有 2 个备份，保留 5 个
        mock_cursor.fetchall.return_value = [
            ('bk-1', '/backups/bk-1.zip'),
            ('bk-2', '/backups/bk-2.zip'),
        ]

        cleanup_old_backups(5)

        # 不应删除任何文件
        mock_delete_file.assert_not_called()


class TestDeleteBackupFile:
    """测试 delete_backup_file 函数"""

    def test_delete_existing_file(self):
        """删除存在的文件"""
        from utils.backup import delete_backup_file

        with tempfile.NamedTemporaryFile(delete=False) as f:
            temp_path = f.name

        assert os.path.isfile(temp_path)
        delete_backup_file(temp_path)
        assert not os.path.isfile(temp_path)

    def test_delete_nonexistent_file_no_error(self):
        """删除不存在的文件不应报错"""
        from utils.backup import delete_backup_file

        # 不应抛出异常
        delete_backup_file('/nonexistent/path/file.zip')

    def test_delete_none_path_no_error(self):
        """传入 None 不应报错"""
        from utils.backup import delete_backup_file

        delete_backup_file(None)


class TestTableLevelBackup:
    """测试表级备份功能"""

    @patch('utils.backup._ensure_backup_dir')
    @patch('utils.backup.get_db')
    @patch('utils.backup.os.path.getsize')
    def test_create_partial_backup_with_tables(self, mock_getsize, mock_get_db, mock_ensure_dir):
        """创建表级备份应只备份指定的表"""
        from utils.backup import create_backup

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = lambda self: mock_conn
        mock_conn.__exit__ = lambda self, *args: None
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []
        mock_get_db.return_value = mock_conn
        mock_getsize.return_value = 1024

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('utils.backup.BACKUP_DIR', tmpdir):
                # 只备份 menus 和 users 两张表
                result = create_backup(
                    backup_type='manual',
                    created_by='admin',
                    tables=['menus', 'users']
                )

                # 验证返回结果
                assert result['backupScope'] == 'partial'
                assert result['backupTables'] == ['menus', 'users']
                assert result['tablesCount'] == 2

                # 验证 ZIP 内容
                zip_path = result['filePath']
                with zipfile.ZipFile(zip_path, 'r') as zf:
                    names = zf.namelist()
                    assert 'manifest.json' in names
                    assert 'menus.json' in names
                    assert 'users.json' in names
                    # 其他表不应存在
                    assert 'page_configs.json' not in names
                    assert 'dynamic_data.json' not in names

                    # 验证 manifest
                    manifest = json.loads(zf.read('manifest.json'))
                    assert manifest['scope'] == 'partial'
                    assert manifest['tables'] == ['menus', 'users']

    @patch('utils.backup._ensure_backup_dir')
    @patch('utils.backup.get_db')
    @patch('utils.backup.os.path.getsize')
    def test_create_full_backup_without_tables(self, mock_getsize, mock_get_db, mock_ensure_dir):
        """不传 tables 参数应创建全量备份"""
        from utils.backup import create_backup, BACKUP_TABLES

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = lambda self: mock_conn
        mock_conn.__exit__ = lambda self, *args: None
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []
        mock_get_db.return_value = mock_conn
        mock_getsize.return_value = 1024

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('utils.backup.BACKUP_DIR', tmpdir):
                result = create_backup(backup_type='manual')

                # 验证返回结果
                assert result['backupScope'] == 'full'
                assert result['tablesCount'] == len(BACKUP_TABLES)

    @patch('utils.backup._ensure_backup_dir')
    @patch('utils.backup.get_db')
    @patch('utils.backup.os.path.getsize')
    def test_create_backup_filters_invalid_tables(self, mock_getsize, mock_get_db, mock_ensure_dir):
        """创建表级备份应过滤无效的表名"""
        from utils.backup import create_backup

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = lambda self: mock_conn
        mock_conn.__exit__ = lambda self, *args: None
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []
        mock_get_db.return_value = mock_conn
        mock_getsize.return_value = 1024

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('utils.backup.BACKUP_DIR', tmpdir):
                # 传入无效表名和有效表名
                result = create_backup(
                    tables=['menus', 'invalid_table', 'users', 'another_invalid']
                )

                # 只有有效的表被备份
                assert result['backupTables'] == ['menus', 'users']

    @patch('utils.backup._ensure_backup_dir')
    @patch('utils.backup.get_db')
    @patch('utils.backup.os.path.getsize')
    def test_create_backup_raises_error_for_no_valid_tables(self, mock_getsize, mock_get_db, mock_ensure_dir):
        """所有表名都无效时应抛出错误"""
        from utils.backup import create_backup

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = lambda self: mock_conn
        mock_conn.__exit__ = lambda self, *args: None
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('utils.backup.BACKUP_DIR', tmpdir):
                with pytest.raises(ValueError, match='没有有效的表需要备份'):
                    create_backup(tables=['invalid_table', 'another_invalid'])


class TestTableLevelRestore:
    """测试表级还原功能"""

    @patch('db.pool')
    def test_restore_partial_tables(self, mock_pool):
        """只还原指定的表"""
        from utils.backup import restore_backup, BACKUP_VERSION

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = os.path.join(tmpdir, 'test-backup.zip')

            # 创建包含多张表的备份
            manifest = {
                'version': BACKUP_VERSION,
                'id': 'backup-test',
                'name': '测试备份',
                'type': 'manual',
                'scope': 'partial',
                'tables': ['menus', 'users', 'dynamic_data'],
                'createdAt': datetime.now(timezone.utc).isoformat(),
                'totalRecords': 3,
            }

            with zipfile.ZipFile(zip_path, 'w') as zf:
                zf.writestr('manifest.json', json.dumps(manifest))
                zf.writestr('menus.json', json.dumps([{'id': 'm1', 'name': 'menu1'}]))
                zf.writestr('users.json', json.dumps([{'id': 'u1', 'username': 'user1'}]))
                zf.writestr('dynamic_data.json', json.dumps([{'id': 'd1', 'collection': 'test'}]))

            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_pool.getconn.return_value = mock_conn
            mock_pool.putconn.return_value = None

            # 只还原 menus 和 users
            result = restore_backup(zip_path, tables=['menus', 'users'])

            assert result['id'] == 'backup-test'
            # 应该只执行了 2 张表的 TRUNCATE
            truncate_calls = [call for call in mock_cursor.execute.call_args_list
                            if 'TRUNCATE' in str(call)]
            assert len(truncate_calls) == 2

    @patch('db.pool')
    def test_restore_filters_tables_not_in_backup(self, mock_pool):
        """还原时指定的表不在备份中应被过滤"""
        from utils.backup import restore_backup, BACKUP_VERSION

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = os.path.join(tmpdir, 'test-backup.zip')

            manifest = {
                'version': BACKUP_VERSION,
                'id': 'backup-test',
                'name': '测试备份',
                'type': 'manual',
                'tables': ['menus', 'users'],
                'createdAt': datetime.now(timezone.utc).isoformat(),
                'totalRecords': 0,
            }

            with zipfile.ZipFile(zip_path, 'w') as zf:
                zf.writestr('manifest.json', json.dumps(manifest))
                zf.writestr('menus.json', '[]')
                zf.writestr('users.json', '[]')

            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_pool.getconn.return_value = mock_conn
            mock_pool.putconn.return_value = None

            # 请求还原 menus 和不在备份中的 page_configs
            result = restore_backup(zip_path, tables=['menus', 'page_configs'])

            # 只有 menus 被还原
            truncate_calls = [call for call in mock_cursor.execute.call_args_list
                            if 'TRUNCATE' in str(call)]
            assert len(truncate_calls) == 1

    @patch('db.pool')
    def test_restore_raises_error_when_no_tables_match(self, mock_pool):
        """还原时指定的表都不在备份中应抛出错误"""
        from utils.backup import restore_backup, BACKUP_VERSION

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = os.path.join(tmpdir, 'test-backup.zip')

            manifest = {
                'version': BACKUP_VERSION,
                'id': 'backup-test',
                'name': '测试备份',
                'type': 'manual',
                'tables': ['menus', 'users'],
                'createdAt': datetime.now(timezone.utc).isoformat(),
                'totalRecords': 0,
            }

            with zipfile.ZipFile(zip_path, 'w') as zf:
                zf.writestr('manifest.json', json.dumps(manifest))
                zf.writestr('menus.json', '[]')
                zf.writestr('users.json', '[]')

            # 请求还原不在备份中的表
            with pytest.raises(ValueError, match='指定的表不在备份中'):
                restore_backup(zip_path, tables=['dynamic_data', 'page_configs'])

    @patch('db.pool')
    def test_restore_legacy_backup_without_tables_field(self, mock_pool):
        """还原旧版备份（无 tables 字段）应从文件名推断"""
        from utils.backup import restore_backup

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = os.path.join(tmpdir, 'legacy-backup.zip')

            # 旧版 manifest 无 tables 字段
            manifest = {
                'version': 1,
                'id': 'legacy-backup',
                'name': '旧版备份',
                'type': 'manual',
                'createdAt': datetime.now(timezone.utc).isoformat(),
                'totalRecords': 0,
            }

            with zipfile.ZipFile(zip_path, 'w') as zf:
                zf.writestr('manifest.json', json.dumps(manifest))
                zf.writestr('menus.json', '[]')
                zf.writestr('users.json', '[]')

            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_pool.getconn.return_value = mock_conn
            mock_pool.putconn.return_value = None

            # 应该能正常还原
            result = restore_backup(zip_path, tables=['menus'])
            assert result['id'] == 'legacy-backup'


class TestGetBackupTableNames:
    """测试 get_backup_table_names 函数"""

    def test_returns_list_of_tables(self):
        """应返回可备份的表列表"""
        from utils.backup import get_backup_table_names, BACKUP_TABLES

        result = get_backup_table_names()

        assert isinstance(result, list)
        assert len(result) == len(BACKUP_TABLES)

        # 每个元素应有 name 和 label
        for item in result:
            assert 'name' in item
            assert 'label' in item

    def test_includes_common_tables(self):
        """应包含常见的关键表"""
        from utils.backup import get_backup_table_names

        result = get_backup_table_names()
        table_names = [t['name'] for t in result]

        assert 'menus' in table_names
        assert 'page_configs' in table_names
        assert 'dynamic_data' in table_names
        assert 'users' in table_names

    def test_table_has_chinese_label(self):
        """每个表应有中文标签"""
        from utils.backup import get_backup_table_names

        result = get_backup_table_names()

        for item in result:
            # 中文标签不应为空且不应等于表名
            assert item['label']
            assert item['label'] != item['name']