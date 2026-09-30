"""
跨项目依赖 API 端点单元测试

测试 merge-check, merge-order, update-dependencies-after-merge 端点
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class FakeConnection:
    """模拟数据库连接"""
    def __init__(self, cursor_mock):
        self.cursor_mock = cursor_mock

    def cursor(self):
        return self.cursor_mock

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@contextmanager
def fake_get_db(conn):
    yield conn


@pytest.fixture
def admin_token():
    """生成 admin 角色的 JWT token"""
    from auth import create_token
    return create_token({
        'id': 'user-admin',
        'username': 'admin',
        'role': 'admin',
    })


@pytest.fixture
def admin_headers(admin_token):
    """包含 admin JWT 的请求头"""
    return {'Authorization': f'Bearer {admin_token}'}


@pytest.fixture
def dev_token():
    """生成 developer 角色的 JWT token"""
    from auth import create_token
    return create_token({
        'id': 'user-dev',
        'username': 'developer',
        'role': 'developer',
    })


@pytest.fixture
def dev_headers(dev_token):
    """包含 developer JWT 的请求头"""
    return {'Authorization': f'Bearer {dev_token}'}


# ==================== /projects/:id/merge-check ====================

class TestMergeCheckAPI:
    """测试合并依赖检查 API"""

    @pytest.fixture
    def mock_cursor(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = None
        return cursor

    @pytest.fixture
    def mock_conn(self, mock_cursor):
        return FakeConnection(mock_cursor)

    @pytest.fixture
    def app(self, mock_conn):
        """创建 Flask 测试应用"""
        with patch('db.get_db', lambda: fake_get_db(mock_conn)), \
             patch('db.pool', MagicMock()):
            from app import app as flask_app
            flask_app.config['TESTING'] = True
            yield flask_app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    def test_merge_check_requires_source_branch(self, client, admin_headers):
        """缺少 sourceBranch 参数返回 400"""
        resp = client.get('/projects/project-1/merge-check', headers=admin_headers)
        assert resp.status_code == 400
        assert '缺少' in resp.get_json()['error']

    def test_merge_check_returns_can_merge(self, client, admin_headers, mock_conn, mock_cursor):
        """返回 canMerge 结果"""
        mock_cursor.fetchall.return_value = []

        with patch('utils.cross_project_dependency.get_db', lambda: fake_get_db(mock_conn)):
            resp = client.get(
                '/projects/project-1/merge-check?sourceBranch=branch-1',
                headers=admin_headers
            )

        assert resp.status_code == 200
        data = resp.get_json()
        assert 'canMerge' in data
        assert data['canMerge'] is True

    def test_merge_check_with_blocking_deps(self, client, admin_headers, mock_conn, mock_cursor):
        """有阻塞依赖时返回 canMerge=false"""
        mock_cursor.fetchall.return_value = [
            ('dep-1', 'project-2', 'branch-feat', 'read-write', None, '项目B')
        ]
        mock_cursor.fetchone.return_value = ('active', None)

        with patch('utils.cross_project_dependency.get_db', lambda: fake_get_db(mock_conn)):
            resp = client.get(
                '/projects/project-1/merge-check?sourceBranch=branch-1',
                headers=admin_headers
            )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['canMerge'] is False
        assert len(data['blockingDependencies']) == 1

    def test_merge_check_requires_auth(self, client):
        """未登录返回 401"""
        resp = client.get('/projects/project-1/merge-check?sourceBranch=branch-1')
        assert resp.status_code == 401


# ==================== /projects/:id/merge-order ====================

class TestMergeOrderAPI:
    """测试联合合并顺序 API"""

    @pytest.fixture
    def mock_cursor(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = None
        return cursor

    @pytest.fixture
    def mock_conn(self, mock_cursor):
        return FakeConnection(mock_cursor)

    @pytest.fixture
    def app(self, mock_conn):
        with patch('db.get_db', lambda: fake_get_db(mock_conn)), \
             patch('db.pool', MagicMock()):
            from app import app as flask_app
            flask_app.config['TESTING'] = True
            yield flask_app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    def test_merge_order_requires_source_branch(self, client, admin_headers):
        """缺少 sourceBranch 参数返回 400"""
        resp = client.get('/projects/project-1/merge-order', headers=admin_headers)
        assert resp.status_code == 400

    def test_merge_order_returns_list(self, client, admin_headers, mock_conn, mock_cursor):
        """返回 mergeOrder 列表"""
        # 版本存在
        mock_cursor.fetchone.return_value = ('测试分支',)
        mock_cursor.fetchall.return_value = []

        with patch('utils.cross_project_dependency.get_db', lambda: fake_get_db(mock_conn)):
            resp = client.get(
                '/projects/project-1/merge-order?sourceBranch=branch-1',
                headers=admin_headers
            )

        assert resp.status_code == 200
        data = resp.get_json()
        assert 'mergeOrder' in data
        assert 'total' in data
        assert isinstance(data['mergeOrder'], list)

    def test_merge_order_requires_auth(self, client):
        """未登录返回 401"""
        resp = client.get('/projects/project-1/merge-order?sourceBranch=branch-1')
        assert resp.status_code == 401


# ==================== /projects/:id/update-dependencies-after-merge ====================

class TestUpdateDependenciesAfterMergeAPI:
    """测试合并后更新依赖 API"""

    @pytest.fixture
    def mock_cursor(self):
        cursor = MagicMock()
        cursor.rowcount = 0
        return cursor

    @pytest.fixture
    def mock_conn(self, mock_cursor):
        return FakeConnection(mock_cursor)

    @pytest.fixture
    def app(self, mock_conn):
        with patch('db.get_db', lambda: fake_get_db(mock_conn)), \
             patch('db.pool', MagicMock()):
            from app import app as flask_app
            flask_app.config['TESTING'] = True
            yield flask_app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    def test_update_requires_source_branch(self, client, admin_headers):
        """缺少 sourceBranch 参数返回 400"""
        resp = client.post(
            '/projects/project-1/update-dependencies-after-merge',
            headers=admin_headers,
            json={}
        )
        assert resp.status_code == 400

    def test_update_returns_success(self, client, admin_headers, mock_conn, mock_cursor):
        """返回成功和更新数量"""
        mock_cursor.rowcount = 2

        with patch('utils.cross_project_dependency.get_db', lambda: fake_get_db(mock_conn)):
            resp = client.post(
                '/projects/project-1/update-dependencies-after-merge',
                headers=admin_headers,
                json={'sourceBranch': 'branch-1'}
            )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['updatedCount'] == 2

    def test_update_requires_admin(self, client, dev_headers):
        """非 admin 返回 403"""
        resp = client.post(
            '/projects/project-1/update-dependencies-after-merge',
            headers=dev_headers,
            json={'sourceBranch': 'branch-1'}
        )
        assert resp.status_code == 403

    def test_update_requires_auth(self, client):
        """未登录返回 401"""
        resp = client.post(
            '/projects/project-1/update-dependencies-after-merge',
            json={'sourceBranch': 'branch-1'}
        )
        assert resp.status_code == 401


# ==================== 合并端点集成依赖检查 ====================

class TestMergeIntegration:
    """测试合并端点的依赖检查集成"""

    @pytest.fixture
    def mock_cursor(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = None
        cursor.rowcount = 0
        return cursor

    @pytest.fixture
    def mock_conn(self, mock_cursor):
        return FakeConnection(mock_cursor)

    @pytest.fixture
    def app(self, mock_conn):
        with patch('db.get_db', lambda: fake_get_db(mock_conn)), \
             patch('db.pool', MagicMock()):
            from app import app as flask_app
            flask_app.config['TESTING'] = True
            yield flask_app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    def test_merge_with_blocking_dependency(self, client, admin_headers, mock_conn, mock_cursor):
        """有阻塞依赖时合并返回 400"""
        # 设置依赖检查返回阻塞
        mock_cursor.fetchall.return_value = [
            ('dep-1', 'project-2', 'branch-feat', 'read-write', None, '项目B')
        ]
        mock_cursor.fetchone.return_value = ('active', None)

        with patch('utils.cross_project_dependency.get_db', lambda: fake_get_db(mock_conn)):
            resp = client.post(
                '/project-versions/merge',
                headers=admin_headers,
                json={
                    'versionId': 'version-1',
                    'projectMenuId': 'project-1',
                    'strategy': 'theirs'
                }
            )

        assert resp.status_code == 400
        data = resp.get_json()
        assert '阻塞依赖' in data['error']
        assert 'dependencyCheck' in data

    def test_merge_skip_dependency_check(self, client, admin_headers, mock_conn, mock_cursor):
        """skipDependencyCheck=true 时跳过依赖检查"""
        # mock 版本信息
        mock_cursor.fetchone.return_value = ('版本1', 'branch', 'active')
        mock_cursor.fetchall.return_value = []
        mock_cursor.rowcount = 1

        # Mock merge_project_version 返回成功（需要 patch 在 routes 模块导入的位置）
        with patch('routes.project_versions.merge_project_version') as mock_merge:
            mock_merge.return_value = {'success': True, 'mergeId': 'merge-1'}

            resp = client.post(
                '/project-versions/merge',
                headers=admin_headers,
                json={
                    'versionId': 'version-1',
                    'projectMenuId': 'project-1',
                    'strategy': 'theirs',
                    'skipDependencyCheck': True
                }
            )

        # 跳过依赖检查，应该调用合并函数
        assert mock_merge.called
        # 返回成功
        assert resp.status_code == 200

    def test_merge_detailed_with_blocking_dependency(self, client, admin_headers, mock_conn, mock_cursor):
        """详细合并有阻塞依赖时返回 400"""
        mock_cursor.fetchall.return_value = [
            ('dep-1', 'project-2', 'branch-feat', 'read-write', None, '项目B')
        ]
        mock_cursor.fetchone.return_value = ('active', None)

        with patch('utils.cross_project_dependency.get_db', lambda: fake_get_db(mock_conn)):
            resp = client.post(
                '/project-versions/merge-detailed',
                headers=admin_headers,
                json={
                    'versionId': 'version-1',
                    'projectMenuId': 'project-1',
                    'collections': [{'collection': 'test', 'added': ['id-1']}]
                }
            )

        assert resp.status_code == 400
        assert '阻塞依赖' in resp.get_json()['error']