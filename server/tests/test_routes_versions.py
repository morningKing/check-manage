"""
版本管理路由单元测试

测试版本列表 API 的分页和搜索功能。
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _make_mock_db(mock_conn):
    @contextmanager
    def fake_get_db():
        yield mock_conn
    return fake_get_db


@pytest.fixture
def setup_versions(mock_conn, mock_cursor):
    fake_db = _make_mock_db(mock_conn)
    patches = [
        patch('db.get_db', fake_db),
        patch('utils.version.get_db', fake_db),
        patch('db.pool', MagicMock()),
        patch('utils.operation_log.log_operation'),
    ]
    for p in patches:
        p.start()

    from app import app
    app.config['TESTING'] = True
    from auth import create_token
    admin = create_token({'id': 'user-admin', 'username': 'admin', 'role': 'admin'})

    yield (
        app.test_client(),
        mock_cursor,
        {'Authorization': f'Bearer {admin}'},
    )

    for p in patches:
        p.stop()


class TestListVersions:
    """测试版本列表 API"""

    def test_list_versions_basic(self, setup_versions):
        """测试基本版本列表获取"""
        client, mock_cursor, admin_headers = setup_versions
        mock_cursor.fetchall.return_value = [
            ('ver-1', 'project', 'v1.0', 'Test version', 'snapshot', None, 'active', None, 10, 5, 'admin', None, None, None, None, False)
        ]
        mock_cursor.fetchone.return_value = None

        response = client.get('/versions', headers=admin_headers)

        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]['id'] == 'ver-1'

    def test_list_versions_with_collection_filter(self, setup_versions):
        """测试按 Collection 筛选版本"""
        client, mock_cursor, admin_headers = setup_versions
        mock_cursor.fetchall.return_value = [
            ('ver-1', 'project', 'v1.0', '', 'snapshot', None, 'active', None, 10, 5, 'admin', None, None, None, None, False)
        ]
        mock_cursor.fetchone.return_value = None

        response = client.get('/versions?collection=project', headers=admin_headers)

        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)

    def test_list_versions_with_status_filter(self, setup_versions):
        """测试按状态筛选版本"""
        client, mock_cursor, admin_headers = setup_versions
        mock_cursor.fetchall.return_value = [
            ('ver-1', 'project', 'v1.0', '', 'snapshot', None, 'active', None, 10, 5, 'admin', None, None, None, None, False)
        ]
        mock_cursor.fetchone.return_value = None

        response = client.get('/versions?status=active', headers=admin_headers)

        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)

    def test_list_versions_with_pagination(self, setup_versions):
        """测试版本列表 API 支持分页"""
        client, mock_cursor, admin_headers = setup_versions

        # Mock paginated response - fetchone for total count, fetchall for items
        mock_cursor.fetchone.return_value = (25,)  # total count
        mock_cursor.fetchall.return_value = [
            ('ver-1', 'project', 'v1.0', '', 'snapshot', None, 'active', None, 10, 5, 'admin', None, None, None, None, False)
        ]

        response = client.get('/versions?collection=project&page=1&pageSize=10', headers=admin_headers)

        assert response.status_code == 200
        data = response.get_json()
        assert 'items' in data
        assert 'total' in data
        assert data['total'] == 25

    def test_list_versions_with_page_only(self, setup_versions):
        """测试只提供 page 参数，pageSize 默认为 10"""
        client, mock_cursor, admin_headers = setup_versions

        mock_cursor.fetchone.return_value = (15,)
        mock_cursor.fetchall.return_value = [
            ('ver-1', 'project', 'v1.0', '', 'snapshot', None, 'active', None, 10, 5, 'admin', None, None, None, None, False)
        ]

        response = client.get('/versions?page=1', headers=admin_headers)

        assert response.status_code == 200
        data = response.get_json()
        assert 'items' in data
        assert 'total' in data

    def test_list_versions_with_keyword_search(self, setup_versions):
        """测试版本列表 API 支持关键词搜索"""
        client, mock_cursor, admin_headers = setup_versions

        mock_cursor.fetchone.return_value = (1,)
        mock_cursor.fetchall.return_value = [
            ('ver-1', 'project', 'v1.0-release', 'Release version', 'snapshot', None, 'active', None, 10, 5, 'admin', None, None, None, None, False)
        ]

        response = client.get('/versions?collection=project&keyword=release&page=1', headers=admin_headers)

        assert response.status_code == 200
        data = response.get_json()
        assert data['total'] == 1

    def test_list_versions_keyword_without_pagination(self, setup_versions):
        """测试关键词搜索无分页参数时返回列表"""
        client, mock_cursor, admin_headers = setup_versions

        mock_cursor.fetchall.return_value = [
            ('ver-1', 'project', 'hotfix-123', 'Hotfix', 'snapshot', None, 'active', None, 10, 5, 'admin', None, None, None, None, False)
        ]
        mock_cursor.fetchone.return_value = None

        response = client.get('/versions?keyword=hotfix', headers=admin_headers)

        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)

    def test_list_versions_all_params(self, setup_versions):
        """测试同时使用分页、筛选和搜索"""
        client, mock_cursor, admin_headers = setup_versions

        mock_cursor.fetchone.return_value = (5,)
        mock_cursor.fetchall.return_value = [
            ('ver-1', 'project', 'release-v1', 'Release', 'snapshot', None, 'active', None, 10, 5, 'admin', None, None, None, None, False),
            ('ver-2', 'project', 'release-v2', 'Release', 'snapshot', None, 'active', None, 10, 5, 'admin', None, None, None, None, False),
        ]

        response = client.get('/versions?collection=project&status=active&keyword=release&page=1&pageSize=10', headers=admin_headers)

        assert response.status_code == 200
        data = response.get_json()
        assert 'items' in data
        assert 'total' in data
        assert data['total'] == 5

    def test_list_versions_no_auth(self, setup_versions):
        """测试未认证请求返回 401"""
        client, _, _ = setup_versions

        response = client.get('/versions')

        assert response.status_code == 401


if __name__ == '__main__':
    pytest.main([__file__, '-v'])