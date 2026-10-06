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

# ---------------------------------------------------------------------------
# 独立测试库（2026-10-06 P0）：此前单测与 dev 后端(:3002)共享 casemanage 库，
# 后端 batch worker 会认领/推进测试刚插入的 pending 行，把断言弄脏成偶发失败
# （test_batch_auto_retry 一整族；claim_guard 夹具即为此而设，但覆盖不全）。
# 这里默认把整套单测指到 casemanage_test，单测从此不碰 dev 数据；显式设置
# DB_NAME 环境变量者仍然赢。建库/迁移引导见 _bootstrap_test_db（sessionstart）。
TEST_DB_NAME = 'casemanage_test'
os.environ.setdefault('DB_NAME', TEST_DB_NAME)


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


_SESSION_STARTED_AT = None


def _maintenance_dsn():
    """连 maintenance 库（postgres）做建库判断；参数处理同压测栈 _base_dsn。"""
    import psycopg2
    from config import DB_CONFIG
    dsn = dict(DB_CONFIG)
    dsn['dbname'] = 'postgres'
    dsn.pop('options', None)
    return dsn


def _bootstrap_test_db():
    """确保独立测试库存在且 schema 与 migrations/ 齐平（只在控制器进程跑）。

    三档：
      - 库缺失   -> CREATE DATABASE casemanage_test + 子进程全量 init_db
                    （DDL+seed+迁移）
      - 库已存在 -> 只跑一遍幂等 dated migrations 补漂移（新合并的迁移文件）
      - TEST_DB_REBUILD=1 -> 先删后建（schema 怀疑脏时的逃生门；会踢掉
        该库上的存量连接，勿与其他 pytest 会话并行使用）

    自动引导只覆盖固定库名 casemanage_test（DDL 用全字面量，零拼接）；
    显式自定义 DB_NAME 的会话不做自动建库，缺库时给出手工引导指引。
    xdist：worker 携带 PYTEST_XDIST_WORKER 环境变量，在 pytest_sessionstart
    里被跳过——控制器的 sessionstart 先于收集/派发完成，worker 起来时库必然
    就绪；worker 若仍连不上会以明显的关系缺失错误暴露，不会静默。
    """
    import io
    import subprocess
    import time
    from contextlib import redirect_stdout
    import psycopg2
    import pytest as _pytest

    effective = os.environ.get('DB_NAME', TEST_DB_NAME)
    server_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    if effective != TEST_DB_NAME:
        # 自定义库名：不代建（DDL 拒绝动态标识符），给手工路径。
        print(f"[test-db] DB_NAME={effective} 为自定义库，跳过自动引导；"
              f"若库不存在请手工执行：CREATE DATABASE {effective}; "
              f"然后 DB_NAME={effective} python init_db.py")
        return

    try:
        conn = psycopg2.connect(**_maintenance_dsn())
    except psycopg2.OperationalError as e:
        raise _pytest.UsageError(
            f'[test-db] 连不上 Postgres maintenance 库（{e}）。'
            f'请核对 DB_HOST/DB_PORT/DB_USER/DB_PASSWORD（config.DB_CONFIG）。')
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT 1 FROM pg_database WHERE datname=%s',
                        (TEST_DB_NAME,))
            exists = cur.fetchone() is not None
            if os.environ.get('TEST_DB_REBUILD') == '1' and exists:
                cur.execute(
                    'SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
                    'WHERE datname=%s AND pid <> pg_backend_pid()',
                    (TEST_DB_NAME,))
                cur.execute('DROP DATABASE casemanage_test')
                exists = False
                print(f'[test-db] TEST_DB_REBUILD=1 -> 已删除 {TEST_DB_NAME}')
            if exists:
                created = False
            else:
                cur.execute('CREATE DATABASE casemanage_test')
                created = True
                print(f'[test-db] 测试库不存在 -> 已创建 {TEST_DB_NAME}')
    finally:
        conn.close()

    if created:
        print('[test-db] 全量 init_db 引导中（一次性，DDL+seed+迁移）...')
        proc = subprocess.run(
            [sys.executable, 'init_db.py'], cwd=server_dir,
            env={**os.environ, 'DB_NAME': TEST_DB_NAME, 'PYTHONUTF8': '1'},
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=600)
        if proc.returncode != 0:
            raise _pytest.UsageError(
                f'[test-db] init_db 引导失败（DB_NAME={TEST_DB_NAME}），'
                f'输出尾部：\n{proc.stdout[-2000:]}\n{proc.stderr[-1000:]}')
        print('[test-db] init_db 引导完成。')
        return

    # 已存在 -> 幂等迁移校对漂移；迁移各自带存在性检查，现势 schema 下快跳过。
    # _run_dated_migrations 会逐个 print，捕获后只在失败时展开，避免刷屏。
    t0 = time.time()
    import init_db as _initdb
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            _initdb._run_dated_migrations()
    except SystemExit as e:
        raise _pytest.UsageError(
            f'[test-db] {TEST_DB_NAME} schema 迁移失败'
            f'（可 TEST_DB_REBUILD=1 重建）：\n{e}\n{buf.getvalue()[-1500:]}')
    print(f'[test-db] {TEST_DB_NAME} schema 就绪'
          f'（幂等迁移校对 {time.time() - t0:.1f}s）')

    _ensure_canonical_users()


def _ensure_canonical_users():
    """确保三个规范用户行存在（user-admin/user-dev/user-guest）。

    套件约定：conftest 的 admin_token/dev_token 夹具伪造这三个 id 的 JWT，
    23 个测试文件直接拿这些 id 写库（FK 指向 users）。dev 库的 user-admin
    是历史积累，init_db 不播种（管理员由应用首启创建）——全新测试库必须
    自带，否则 FK 违反。幂等：只在缺行时插入。
    """
    import psycopg2
    from config import DB_CONFIG
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            for uid, username, role in (
                    ('user-admin', 'admin', 'admin'),
                    ('user-dev', 'developer', 'developer'),
                    ('user-guest', 'guest', 'guest')):
                cur.execute(
                    'INSERT INTO users (id, username, password_hash, '
                    'display_name, role) '
                    'SELECT %s, %s, %s, %s, %s '
                    'WHERE NOT EXISTS (SELECT 1 FROM users WHERE id = %s)',
                    (uid, username, 'x', f'test-{username}', role, uid))
        conn.commit()
    finally:
        conn.close()


def pytest_sessionstart(session):
    """记录会话起点（14-12 §2.1：GC 限定本会话创建的行，不触碰并行
    进程/会话的孤儿——跨会话删除是全局副作用）。"""
    global _SESSION_STARTED_AT
    import datetime
    _SESSION_STARTED_AT = datetime.datetime.now(datetime.timezone.utc)
    if not os.environ.get('PYTEST_XDIST_WORKER'):
        _bootstrap_test_db()


def pytest_sessionfinish(session, exitstatus):
    """会话结束回收**本会话期间**产生的孤儿 ai_batch_events（12 号 §4.2
    兜底；14-12 §2.1 收紧：加 created_at 会话起点下限，不再删除并行
    进程/更早历史的孤儿——那类行由其所属会话自己的 GC 负责）。

    ai_batch_events 无 FK，个别用例/夹具删除批次或 run 行后会遗留事件。
    各文件夹具已按各自维度回收；此钩子按「batch_id 已脱离 batches ∪ runs」
    收掉本会话残余，防历史再积累。只清测试遗留形态，不触碰任何存活归属。
    """
    if _SESSION_STARTED_AT is None:
        return
    try:
        import psycopg2
        from config import DB_CONFIG
        conn = psycopg2.connect(**DB_CONFIG)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM ai_batch_events e WHERE "
                    "e.created_at >= %s "
                    "AND NOT EXISTS (SELECT 1 FROM ai_chat_batches b "
                    "            WHERE b.id = e.batch_id) "
                    "AND NOT EXISTS (SELECT 1 FROM ai_orchestration_runs r "
                    "                WHERE r.id = e.batch_id)",
                    (_SESSION_STARTED_AT,))
                deleted = cur.rowcount
            conn.commit()
            if deleted:
                print(f'\n[conftest] reclaimed {deleted} orphan ai_batch_events')
        finally:
            conn.close()
    except Exception:
        pass


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
                     'utils.batch_engine', 'utils.subtask_repo'):
        try:
            mod = importlib.import_module(mod_name)
            if hasattr(mod, 'get_db'):
                mod.get_db = db_module.get_db
        except ImportError:
            pass
    yield
