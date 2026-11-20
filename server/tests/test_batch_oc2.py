"""批任务 V2 化单测（OC2 阶段四）：facade 按绑定栈路由、create kind 透传、
无人值守 reject V2 分支、batch_repo 子会话行落列。"""
from unittest.mock import patch, MagicMock

from utils import batch_engine as eng
from utils.opencode_client import OpenCodeClient
from utils.opencode_client_v2 import OpenCodeClientV2


def test_facade_client_routes_v2_by_kind():
    facade = eng._OpenCodeFacade()
    with patch('utils.runtime.registry.runtime_for') as rf:
        rf.return_value.get_client.return_value = 'v2client'
        assert facade._client(kind='v2') == 'v2client'
        rf.assert_called_once_with('v2')


def test_facade_client_routes_by_oc_id_binding():
    facade = eng._OpenCodeFacade()
    with patch('utils.runtime.registry.runtime_kind_by_oc_id',
               return_value='v2'), \
            patch('utils.runtime.registry.runtime_for') as rf:
        rf.return_value.get_client.return_value = 'v2client'
        assert facade._client(oc_session_id='ses_x') == 'v2client'


def test_facade_client_unbound_falls_back_to_get_runtime_chain():
    """查不到绑定的 oc id → 走原 get_runtime() 链（测试打桩点保持有效——
    facade 内 get_runtime 是惰性 import，桩在 utils.runtime 模块属性上）。"""
    facade = eng._OpenCodeFacade()
    stub = MagicMock()
    with patch('utils.runtime.registry.runtime_kind_by_oc_id',
               return_value=''), \
            patch('utils.runtime.get_runtime',
                  return_value=MagicMock(get_client=lambda: stub)):
        assert facade._client(oc_session_id='ses_unknown') is stub
    with patch('utils.runtime.get_runtime',
               return_value=MagicMock(get_client=lambda: stub)):
        assert facade._client() is stub


def test_facade_create_session_passes_kind():
    facade = eng._OpenCodeFacade()
    with patch.object(facade, '_client') as _c:
        _c.return_value.create_session.return_value = 'ses_new'
        out = facade.create_session(directory='/ws', title='t', kind='v2')
    assert out == 'ses_new'
    _c.assert_called_once_with(kind='v2')


def test_facade_list_agents_wiring_not_broken():
    """OC2 合并回归护栏（2026-11-20）：合并曾把 `self._client()` 改成
    `self._client(oc_session_id)` 却漏加形参 → list_agents 每次调用必
    NameError，被 _check_agent 的 except 吞掉 → 批任务 agent 校验静默
    fail-open（bogus agent 直达 OC）。本测试直接走真 facade（只桩 _client），
    形参丢失会在此暴露，而打在 facade 方法上的旧 mock 测不出来。"""
    facade = eng._OpenCodeFacade()
    c = MagicMock()
    c.list_agents.return_value = [{'name': 'build', 'mode': 'primary'}]
    with patch.object(facade, '_client', return_value=c):
        assert facade.list_agents(directory='/ws') == [
            {'name': 'build', 'mode': 'primary'}]
    c.list_agents.assert_called_once_with(directory='/ws')


def test_facade_list_questions_wiring_not_broken():
    """同上：list_questions 丢形参 → pendingQuestions 恒 0（except: pass
    静默），无人值守提问自动拒绝的入口失明。"""
    facade = eng._OpenCodeFacade()
    c = MagicMock()
    q = {'id': 'q1', 'sessionID': 'ses_1'}
    c.list_questions.return_value = [q]
    with patch.object(facade, '_client', return_value=c):
        assert facade.list_questions(directory='/ws') == [q]
    c.list_questions.assert_called_once_with(directory='/ws')


def test_facade_list_permissions_forwarding_exists():
    """facade 缺 list_permissions 转发（引擎 3287/4433 两处调用意图明确、
    底层 V1/V2 客户端都有该方法）→ except 静默吞掉：pendingPermissions
    恒 0、无人值守权限询问自动 reject 整条链路死路。补转发后打通。"""
    facade = eng._OpenCodeFacade()
    c = MagicMock()
    p = {'id': 'p1', 'sessionID': 'ses_1'}
    c.list_permissions.return_value = [p]
    with patch.object(facade, '_client', return_value=c):
        assert facade.list_permissions(directory='/ws') == [p]
    c.list_permissions.assert_called_once_with(directory='/ws')


def test_facade_reject_question_v2_branch_passes_owning_sid():
    facade = eng._OpenCodeFacade()
    v2c = MagicMock()
    with patch.object(facade, '_client', return_value=v2c), \
            patch('utils.runtime.registry.runtime_kind_by_oc_id',
                  return_value='v2'):
        facade.reject_question('frm_1', directory='/ws',
                               oc_session_id='ses_own')
    v2c.reject_question.assert_called_once_with(
        'frm_1', directory='/ws', oc_session_id='ses_own')


def test_facade_reject_question_v1_ignores_sid_param():
    facade = eng._OpenCodeFacade()
    v1c = MagicMock()
    with patch.object(facade, '_client', return_value=v1c), \
            patch('utils.runtime.registry.runtime_kind_by_oc_id',
                  return_value='v1'):
        facade.reject_question('req_1', directory='/ws',
                               oc_session_id='ses_own')
    v1c.reject_question.assert_called_once_with('req_1', directory='/ws')


def test_batch_repo_stamps_oc_runtime():
    """create_batch 子会话行落 oc_runtime（真测试库：casemanage_test）。"""
    import uuid
    from db import get_db
    from utils import batch_repo
    uid = 'u-bat2-' + uuid.uuid4().hex[:6]
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                    "VALUES (%s,%s,'x',%s,'developer')", (uid, uid, uid))
        conn.commit()
    try:
        with patch('utils.runtime.registry.default_kind', return_value='v2'):
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
