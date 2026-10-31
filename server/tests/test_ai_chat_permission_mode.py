"""ai_chat_sessions.permission_mode 列迁移 + 会话 bypass 开关测试。"""
import sys, os, uuid
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_permission_mode_column_exists():
    """迁移幂等 + 新会话默认 'ask'。"""
    import importlib, psycopg2
    from config import DB_CONFIG
    mig = importlib.import_module('migrations.2026_10_31_session_permission_mode')
    mig.run()
    mig.run()  # 幂等可重跑
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT column_name, column_default FROM information_schema.columns "
                "WHERE table_name='ai_chat_sessions' AND column_name='permission_mode'")
    name, default = cur.fetchone()
    assert name == 'permission_mode' and "'ask'" in (default or '')
    sid = f'sess_pm_{uuid.uuid4().hex[:12]}'
    cur.execute("INSERT INTO ai_chat_sessions (id, user_id, title, status) "
                "VALUES (%s, 'user-owner', 'pm-test', 'active')", (sid,))
    conn.commit()
    cur.execute("SELECT permission_mode FROM ai_chat_sessions WHERE id=%s", (sid,))
    assert cur.fetchone()[0] == 'ask'
    cur.execute("DELETE FROM ai_chat_sessions WHERE id=%s", (sid,))
    conn.commit()
    conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# Task 2：PATCH /ai/chat/sessions/<sid>/permission-mode（会话级 bypass 开关）
#
# setup 夹具复制自 test_routes_ai_chat.py（该文件单独执行挂起，
# 根因=boot 迁移回填循环×MagicMock：2026_09_28_message_search_text.py 的
# `while True: ... if cur.rowcount == 0: break` 在 MagicMock cursor 上
# rowcount 恒非 0 → 死循环）。故按 test_ai_chat_image_send.py 同款备用路径：
# 模块顶层（打补丁前）先 import app 命中 sys.modules 缓存，fixture 内的
# `from app import app` 走模块缓存，不再触发启动迁移。
# ═════════════════════════════════════════════════════════════════════════════
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

# 必须先于任何 db.get_db 打补丁完成 app 导入（启动迁移对真测试库跑一次，
# 之后 fixture 内的导入走缓存）——同 test_ai_chat_image_send.py。
from app import app  # noqa: F401,E402
from auth import create_token


def _make_mock_db(mock_conn):
    @contextmanager
    def fake_get_db():
        yield mock_conn
    return fake_get_db


@pytest.fixture
def setup(mock_conn, mock_cursor, tmp_path):
    fake_db = _make_mock_db(mock_conn)
    fake_client = MagicMock()
    fake_client.create_session.return_value = "oc_sess_42"
    fake_client.register_mcp.return_value = None

    class _RuntimeStub:
        """get_runtime() 的测试替身：交互路径（create_session/send_message/
        sse_events）接线 runtime 门面后不再直构 OpenCodeClient，打桩点从
        routes.ai_chat.OpenCodeClient 相应迁到这里——get_client/subscribe_events
        全部委派 fake_client，既有断言（oc.send_prompt_async / oc.subscribe_events）
        保持有效。"""
        def __init__(self, c):
            self._c = c
        def get_client(self):
            return self._c
        def subscribe_events(self, directory='', read_timeout=None):
            return self._c.subscribe_events(directory=directory,
                                            read_timeout=read_timeout)
        def get_messages(self, opencode_session_id, directory=''):
            return self._c.get_messages(opencode_session_id, directory=directory)

    patches = [
        patch('db.get_db', fake_db),
        patch('db.pool', MagicMock()),
        patch('routes.ai_chat.get_db', fake_db),
        patch('utils.session_token.get_db', fake_db),
        patch('utils.chat_persist.get_db', fake_db),
        patch('routes.ai_chat.OpenCodeClient', return_value=fake_client),
        patch('routes.ai_chat.get_runtime',
              lambda: _RuntimeStub(fake_client)),
        patch('config.AI_WORKSPACE_ROOT', str(tmp_path)),
        patch('routes.ai_chat.AI_WORKSPACE_ROOT', str(tmp_path)),
    ]
    for p in patches:
        p.start()

    # Isolate the global persist-listener registry: a prior send test may leave
    # a listener registered for 'sess_x', which would make has_listener() True
    # and skip the SSE proxy's fallback persist under test here.
    from utils import chat_persist
    chat_persist._listeners.clear()

    from app import app
    app.config['TESTING'] = True
    dev = create_token({'id': 'user-1', 'username': 'dev', 'role': 'developer'})
    guest = create_token({'id': 'user-2', 'username': 'g', 'role': 'guest'})

    yield (
        app.test_client(), mock_cursor, fake_client,
        {'Authorization': f'Bearer {dev}'},
        {'Authorization': f'Bearer {guest}'},
        tmp_path,
    )

    for p in patches:
        p.stop()


def test_toggle_validation_and_roles(setup):
    client, cursor, oc, dev_h, guest_h, _ = setup
    # mock 形状修正（实测 200==404）：fetchone 静态 return_value 对任意 sid 都
    # 返回行——'other-sess' 也会被当成属主会话加载，非属主 404 永远测不到。
    # 按套件惯例（test_routes_ai_chat.py:142、governance _db([None])）改用
    # side_effect：第 1 次 fetchone=other-sess 查询→None→404；第 2 次=
    # sess_x 'yolo' 查询→行→BAD_MODE 400。guest 403 在装饰器内拦截、不碰库。
    cursor.fetchone.side_effect = [
        None,
        ('sess_x', 'user-1', 'oc_sess_42', 'active', '/tmp/ws', None, None),
    ]
    # 控制器裁定：端点 bypass 分支会调 _child_session_agents（try 只包
    # get_messages 本身，返回值在 try 外迭代）——裸 MagicMock 返回值迭代
    # TypeError → 500，必须给空列表。
    oc.get_messages.return_value = []
    # guest 403（write_required）
    assert client.patch('/ai/chat/sessions/sess_x/permission-mode',
                        json={'mode': 'bypass'}, headers=guest_h).status_code == 403
    # 非属主 404
    assert client.patch('/ai/chat/sessions/other-sess/permission-mode',
                        json={'mode': 'bypass'}, headers=dev_h).status_code == 404
    # 非法值 400
    assert client.patch('/ai/chat/sessions/sess_x/permission-mode',
                        json={'mode': 'yolo'}, headers=dev_h).status_code == 400


def test_toggle_bypass_backfills_pending(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active',
                                    '/tmp/ws', None, None)
    # 控制器裁定：同上，_child_session_agents 迭代 get_messages 返回值在 try 外
    oc.get_messages.return_value = []
    oc.list_permissions.return_value = [
        {'id': 'req1', 'sessionID': 'oc_sess_42', 'permission': 'external_directory'},
        {'id': 'req2', 'sessionID': 'oc_child_x', 'permission': 'bash'},   # 他人会话
    ]
    r = client.patch('/ai/chat/sessions/sess_x/permission-mode',
                     json={'mode': 'bypass'}, headers=dev_h)
    assert r.status_code == 200 and r.get_json() == {'sessionId': 'sess_x', 'mode': 'bypass'}
    calls = [c for c in oc.reply_permission.call_args_list]
    assert len(calls) == 1
    assert calls[0].args[0] == 'req1' and calls[0].args[1] == 'once'
    assert calls[0].kwargs.get('directory') == '/tmp/ws'
    # UPDATE 落库发生
    assert any('UPDATE ai_chat_sessions SET permission_mode' in c.args[0]
               for c in cursor.execute.call_args_list)


def test_toggle_ask_no_backfill(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active',
                                    '/tmp/ws', None, None)
    # 控制器裁定：同上（ask 分支虽不触客户端调用，统一防护）
    oc.get_messages.return_value = []
    oc.list_permissions.return_value = [{'id': 'req1', 'sessionID': 'oc_sess_42'}]
    r = client.patch('/ai/chat/sessions/sess_x/permission-mode',
                     json={'mode': 'ask'}, headers=dev_h)
    assert r.status_code == 200
    oc.reply_permission.assert_not_called()


def test_toggle_batch_controlled_409(setup):
    client, cursor, oc, dev_h, _, _ = setup
    # batch_id 非空 + 非终态 status → _execution_controlled True（AI_BATCH_STRICT_OWNERSHIP 默认严格）
    # mock 形状修正（实测 200==409）：_execution_controlled 的非终态白名单是
    # ('pending','running','paused')，'active' 不在内——改用 'running'，与
    # 既有批受控行形状一致（test_routes_ai_chat.py:496）。
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'running',
                                    '/tmp/ws', 'batch_x', None)
    r = client.patch('/ai/chat/sessions/sess_x/permission-mode',
                     json={'mode': 'bypass'}, headers=dev_h)
    assert r.status_code == 409 and r.get_json()['code'] == 'BATCH_CONTROLLED'


def test_toggle_terminal_batch_child_409(setup):
    """终态批子会话同样 409：batch/编排子会话（含终态）的 continue 走批 worker
    权限链（自动拒绝 asks），bypass 开关对其一律拒绝——不受
    _execution_controlled 仅覆盖非终态（pending/running/paused）的局限。"""
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'completed',
                                    '/tmp/ws', 'batch_x', None)
    r = client.patch('/ai/chat/sessions/sess_x/permission-mode',
                     json={'mode': 'bypass'}, headers=dev_h)
    assert r.status_code == 409 and r.get_json()['code'] == 'BATCH_CONTROLLED'


def test_clear_resets_permission_mode(setup):
    """clear 原地重置：permission_mode 一并回默认 'ask'（新 OpenCode 会话
    不应继承旧会话的 bypass——上下文已清空，自动放行语义随之失效）。"""
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active',
                                    '/tmp/ws', None, None)
    oc.delete_session.return_value = None
    r = client.post('/ai/chat/sessions/sess_x/clear', headers=dev_h)
    assert r.status_code == 200
    clear_updates = [c for c in cursor.execute.call_args_list
                     if 'UPDATE ai_chat_sessions' in (c.args[0] or '')]
    assert any('permission_mode' in c.args[0] and "'ask'" in c.args[0]
               for c in clear_updates)


def test_toggle_backfill_failure_rolls_back(setup):
    client, cursor, oc, dev_h, _, _ = setup
    # mock 形状修正（对计划稿的偏差）：修复后的端点在 UPDATE 前多一次
    # SELECT permission_mode 以取回滚基准 _prev，静态 return_value 会让该
    # 次读取复用会话行、_row[0] 拿到 id 列 'sess_x' 而非模式值——按套件
    # 惯例改 side_effect：第 1 次=会话行加载，第 2 次=_prev 读取（'ask'）。
    cursor.fetchone.side_effect = [
        ('sess_x', 'user-1', 'oc_sess_42', 'ask', '/tmp/ws', None, None),
        ('ask',),
    ]
    oc.get_messages.return_value = []
    oc.list_permissions.side_effect = RuntimeError('oc down')
    # mock 形状修正（对计划稿的偏差）：本文件夹具设了 TESTING=True，Flask 由此
    # PROPAGATE_EXCEPTIONS=True——raise 会穿透 test_client 而不是折成 500 响应。
    # 本用例要锁「报错=500 响应」语义，仅此处临时关掉传播（用完恢复）。
    prev_prop = app.config.get('PROPAGATE_EXCEPTIONS')
    app.config['PROPAGATE_EXCEPTIONS'] = False
    try:
        r = client.patch('/ai/chat/sessions/sess_x/permission-mode',
                         json={'mode': 'bypass'}, headers=dev_h)
    finally:
        app.config['PROPAGATE_EXCEPTIONS'] = prev_prop
    assert r.status_code == 500
    updates = [c.args[0] for c in cursor.execute.call_args_list
               if 'UPDATE ai_chat_sessions SET permission_mode' in (c.args[0] or '')]
    assert len(updates) == 2          # 先写 bypass，失败后回滚
    rollback = [c for c in cursor.execute.call_args_list
                if 'UPDATE ai_chat_sessions SET permission_mode' in (c.args[0] or '')][-1]
    assert rollback.args[1][0] == 'ask' and rollback.args[1][1] == 'sess_x'


# ═════════════════════════════════════════════════════════════════════════════
# GET /ai/chat/sessions/<sid>/pending-permission：bypass 自愈放行（第二道网）
#
# fetchone 次序（通读 get_pending_permission 全文核实）：_load_session_for_user
# 1 次 SELECT（其后 UPDATE 不取行）→ list_permissions/_child_session_agents
# 只碰 oc 客户端（无 DB）→ 端点内 mode 读取 1 次。故 side_effect=[会话行, 模式]。
# ═════════════════════════════════════════════════════════════════════════════

def test_pending_rehydrate_bypass_auto_replies(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.side_effect = [
        ('sess_x', 'user-1', 'oc_sess_42', 'active', '/tmp/ws', None, None),  # session load
        ('bypass',),                                                          # mode read
    ]
    oc.get_messages.return_value = []
    oc.list_permissions.return_value = [
        {'id': 'req1', 'sessionID': 'oc_sess_42', 'permission': 'external_directory'},
        {'id': 'req2', 'sessionID': 'oc_sess_42', 'permission': 'bash'},
    ]
    r = client.get('/ai/chat/sessions/sess_x/pending-permission', headers=dev_h)
    assert r.status_code == 200
    replied = [c.args[0] for c in oc.reply_permission.call_args_list]
    assert replied == ['req1', 'req2']
    assert r.get_json()['items'] == []


def test_pending_rehydrate_ask_untouched(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.side_effect = [
        ('sess_x', 'user-1', 'oc_sess_42', 'active', '/tmp/ws', None, None),
        ('ask',),
    ]
    oc.get_messages.return_value = []
    oc.list_permissions.return_value = [
        {'id': 'req1', 'sessionID': 'oc_sess_42', 'permission': 'external_directory'}]
    r = client.get('/ai/chat/sessions/sess_x/pending-permission', headers=dev_h)
    assert r.status_code == 200
    oc.reply_permission.assert_not_called()
    assert len(r.get_json()['items']) == 1


def test_pending_rehydrate_bypass_reply_failure_keeps_item(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.side_effect = [
        ('sess_x', 'user-1', 'oc_sess_42', 'active', '/tmp/ws', None, None),
        ('bypass',),
    ]
    oc.get_messages.return_value = []
    oc.list_permissions.return_value = [
        {'id': 'req1', 'sessionID': 'oc_sess_42', 'permission': 'external_directory'}]
    oc.reply_permission.side_effect = RuntimeError('oc down')
    r = client.get('/ai/chat/sessions/sess_x/pending-permission', headers=dev_h)
    assert r.status_code == 200
    assert len(r.get_json()['items']) == 1        # fail-open 手动卡


def test_toggle_bypass_backfill_covers_kids(setup):
    """toggle backfill 覆盖 kids 正向：_child_session_agents 从父会话消息流的
    task 工具 part（state.metadata.sessionId + state.input.subagent_type）识别
    出本会话委派的子代理 oc_kid，挂在其名下的询问同样被自动放行（负向已由
    test_toggle_bypass_backfills_pending 的 req2='oc_child_x' 他人会话覆盖）。"""
    client, cursor, oc, dev_h, _, _ = setup
    # fetchone 次序（通读 set_permission_mode 全文）：_load_session_for_user
    # 1 次 → _prev 读取 1 次。bypass 分支不回滚，共 2 次。
    cursor.fetchone.side_effect = [
        ('sess_x', 'user-1', 'oc_sess_42', 'active', '/tmp/ws', None, None),
        ('ask',),
    ]
    # _child_session_agents 扫描形状（ai_chat.py ~:2345 _scan）：assistant 消息
    # 的 parts 里，state.metadata.sessionId/sessionID 记为子会话 id，
    # state.input.subagent_type 记为 agent 名。
    oc.get_messages.return_value = [
        {'info': {'role': 'assistant'}, 'parts': [
            {'type': 'tool',
             'state': {'metadata': {'sessionId': 'oc_kid'},
                       'input': {'subagent_type': 'general'}}},
        ]},
    ]
    oc.list_permissions.return_value = [
        {'id': 'reqk', 'sessionID': 'oc_kid', 'permission': 'bash'},
    ]
    r = client.patch('/ai/chat/sessions/sess_x/permission-mode',
                     json={'mode': 'bypass'}, headers=dev_h)
    assert r.status_code == 200 and r.get_json() == {'sessionId': 'sess_x', 'mode': 'bypass'}
    oc.reply_permission.assert_called_once_with('reqk', 'once', directory='/tmp/ws')
