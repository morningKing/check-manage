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


@pytest.fixture
def db_conn():
    """Create a real database connection for integration tests."""
    import psycopg2
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from config import DB_CONFIG
    conn = psycopg2.connect(**DB_CONFIG)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _reset_and_prime_permission_cache():
    """Keep RBAC permission resolution deterministic across the whole suite.

    The `@require_permission` decorator and per-page gating resolve the current
    role's permissions via `utils.permissions.get_role_perms`, which is cached
    in a module-global dict and (when not patched) queries the DB. Route-test
    modules patch `utils.permissions.get_db` inconsistently, so without a reset
    the cache leaks across modules (e.g. an admin row resolved under one mock
    cursor lingers, or a developer role gets a polluted entry). That made dozens
    of admin-route tests flip between 200/403 depending on collection order.

    Prime the three built-in roles directly into the cache before each test so
    resolution never depends on whatever `get_db` happens to point at:
      - admin     -> superuser (bypasses every check)
      - developer -> default write access, no admin keys
      - guest     -> default read-only access
    Then clear the cache on teardown so a test that wants custom resolution
    (by patching `utils.permissions.get_db` itself) starts clean.
    """
    import utils.permissions as _perms
    _perms.invalidate_cache()
    _perms._cache['admin'] = {
        'is_superuser': True,
        'default_page_access': 'write',
        'admin_keys': set(),
        'page_perms': {},
    }
    _perms._cache['developer'] = {
        'is_superuser': False,
        'default_page_access': 'write',
        'admin_keys': set(),
        'page_perms': {},
    }
    _perms._cache['guest'] = {
        'is_superuser': False,
        'default_page_access': 'read',
        'admin_keys': set(),
        'page_perms': {},
    }
    yield
    _perms.invalidate_cache()


@pytest.fixture(autouse=True)
def _rebind_module_get_db_to_real():
    """Heal `from db import get_db` bindings polluted by earlier tests.

    Some route-test fixtures do `patch('utils.prompt_template.get_db', fake_db)`
    AFTER the importer module already bound its own local `get_db = db.get_db`.
    `patch.stop()` only restores the attribute on `utils.prompt_template` to
    whatever it was when the patch started — which might already be the mock if
    the patches stack across tests. Force-rebind every module that does
    `from db import get_db` back to the real `db.get_db` before each test.
    Also drop a possibly-mocked `db.pool` so the next call recreates the real
    ThreadedConnectionPool.
    """
    import importlib
    import db as db_module
    # If pool was previously mocked (MagicMock left from a `patch('db.pool',
    # MagicMock())` call), reset it to None so the next get_db() rebuilds the
    # real ThreadedConnectionPool against the dev DB.
    if hasattr(db_module.pool, '_mock_name'):
        db_module.pool = None
    for mod_name in ('utils.prompt_template', 'utils.batch_repo',
                     'utils.batch_engine'):
        try:
            mod = importlib.import_module(mod_name)
            if hasattr(mod, 'get_db'):
                mod.get_db = db_module.get_db
        except ImportError:
            pass
    yield
