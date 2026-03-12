"""
公共测试 fixtures

提供 Flask 测试应用、mock 数据库连接、认证 token 等。
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

# 让 import 能找到 server 下的模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def mock_cursor():
    """创建 mock psycopg2 cursor"""
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None
    cursor.connection.encoding = 'UTF8'
    cursor.mogrify.return_value = b'(mock_values)'
    return cursor


@pytest.fixture
def mock_conn(mock_cursor):
    """创建 mock psycopg2 connection"""
    conn = MagicMock()
    conn.cursor.return_value = mock_cursor
    # 实现上下文管理器协议
    conn.__enter__ = lambda self: conn
    conn.__exit__ = lambda self, *args: None
    return conn


@contextmanager
def _fake_get_db(mock_conn):
    """模拟 get_db 上下文管理器"""
    yield mock_conn


@pytest.fixture
def app(mock_conn):
    """创建 Flask 测试应用，mock 掉数据库"""
    with patch('db.get_db', lambda: _fake_get_db(mock_conn)), \
         patch('db.pool', MagicMock()):
        from app import app as flask_app
        flask_app.config['TESTING'] = True
        yield flask_app


@pytest.fixture
def client(app):
    """Flask 测试客户端"""
    return app.test_client()


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
def dev_token():
    """生成 developer 角色的 JWT token"""
    from auth import create_token
    return create_token({
        'id': 'user-dev',
        'username': 'developer',
        'role': 'developer',
    })


@pytest.fixture
def admin_headers(admin_token):
    """包含 admin JWT 的请求头"""
    return {'Authorization': f'Bearer {admin_token}'}


@pytest.fixture
def dev_headers(dev_token):
    """包含 developer JWT 的请求头"""
    return {'Authorization': f'Bearer {dev_token}'}
