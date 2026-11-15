"""批任务 V2 化单测（OC2 阶段四）：facade 按绑定栈路由、create kind 透传、
无人值守 reject V2 分支、batch_repo 子会话行落列。"""
from unittest.mock import patch, MagicMock

from ai.batch import engine as eng
from ai.opencode.opencode_client import OpenCodeClient
from ai.opencode.opencode_client_v2 import OpenCodeClientV2


def test_facade_client_routes_v2_by_kind():
    facade = eng._OpenCodeFacade()
    with patch('ai.runtime.registry.runtime_for') as rf:
        rf.return_value.get_client.return_value = 'v2client'
        assert facade._client(kind='v2') == 'v2client'
        rf.assert_called_once_with('v2')


def test_facade_client_routes_by_oc_id_binding():
    facade = eng._OpenCodeFacade()
    with patch('ai.runtime.registry.runtime_kind_by_oc_id',
               return_value='v2'), \
            patch('ai.runtime.registry.runtime_for') as rf:
        rf.return_value.get_client.return_value = 'v2client'
        assert facade._client(oc_session_id='ses_x') == 'v2client'


def test_facade_client_unbound_falls_back_to_get_runtime_chain():
    """查不到绑定的 oc id → 走原 get_runtime() 链（测试打桩点保持有效——
    facade 内 get_runtime 是惰性 import，桩在 utils.runtime 模块属性上）。"""
    facade = eng._OpenCodeFacade()
    stub = MagicMock()
    with patch('ai.runtime.registry.runtime_kind_by_oc_id',
               return_value=''), \
            patch('ai.runtime.get_runtime',
                  return_value=MagicMock(get_client=lambda: stub)):
        assert facade._client(oc_session_id='ses_unknown') is stub
    with patch('ai.runtime.get_runtime',
               return_value=MagicMock(get_client=lambda: stub)):
        assert facade._client() is stub


def test_facade_create_session_passes_kind():
    facade = eng._OpenCodeFacade()
    with patch.object(facade, '_client') as _c:
        _c.return_value.create_session.return_value = 'ses_new'
        out = facade.create_session(directory='/ws', title='t', kind='v2')
    assert out == 'ses_new'
    _c.assert_called_once_with(kind='v2')


def test_facade_reject_question_v2_branch_passes_owning_sid():
    facade = eng._OpenCodeFacade()
    v2c = MagicMock()
    with patch.object(facade, '_client', return_value=v2c), \
            patch('ai.runtime.registry.runtime_kind_by_oc_id',
                  return_value='v2'):
        facade.reject_question('frm_1', directory='/ws',
                               oc_session_id='ses_own')
    v2c.reject_question.assert_called_once_with(
        'frm_1', directory='/ws', oc_session_id='ses_own')


def test_facade_reject_question_v1_ignores_sid_param():
    facade = eng._OpenCodeFacade()
    v1c = MagicMock()
    with patch.object(facade, '_client', return_value=v1c), \
            patch('ai.runtime.registry.runtime_kind_by_oc_id',
                  return_value='v1'):
        facade.reject_question('req_1', directory='/ws',
                               oc_session_id='ses_own')
    v1c.reject_question.assert_called_once_with('req_1', directory='/ws')


def test_batch_repo_stamps_oc_runtime():
    """create_batch 子会话行落 oc_runtime（真测试库：casemanage_test）。"""
    import uuid
    from db import get_db
    from ai.batch import repo as batch_repo
    uid = 'u-bat2-' + uuid.uuid4().hex[:6]
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                    "VALUES (%s,%s,'x',%s,'developer')", (uid, uid, uid))
        conn.commit()
    try:
        with patch('ai.runtime.registry.default_kind', return_value='v2'):
            out = batch_repo.create_batch(
                uid, name='n', prompt='p', template_id=None,
                files=[{'name': 'a', 'path': 'p/a'}],
                agent='', model='', provision_repo=None, provision_ref=None,
            )
        bid = out['batch']['id']
        assert all(s.get('oc_runtime') == 'v2' for s in out['sessions']), \
            '子会话行未落 v2'
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT oc_runtime FROM ai_chat_sessions WHERE batch_id = %s",
                        (bid,))
            kinds = [r[0] for r in cur.fetchall()]
        assert kinds and all(k == 'v2' for k in kinds), kinds
    finally:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM ai_chat_sessions WHERE user_id = %s", (uid,))
            cur.execute("DELETE FROM ai_chat_batches WHERE user_id = %s", (uid,))
            cur.execute("DELETE FROM users WHERE id = %s", (uid,))
            conn.commit()
