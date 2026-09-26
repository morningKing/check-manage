# -*- coding: utf-8 -*-
"""子代理会话复用回归（2026-09-24 特性）。

覆盖：配置校验与落库（create/_UNSET 语义）、复用锚定表（pins upsert/查询）、
内部端点鉴权与 enabled 三态、引擎复用指令注入（新建分支）、
turn_segments 跨轮合并幂等、插件部署幂等。
"""
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config import MCP_INTERNAL_TOKEN  # noqa: E402


@pytest.fixture
def user_id(db_conn):
    uid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, %s, %s, 'developer')",
            (uid, f'sru_user_{uid[:8]}', 'x', f'SRU {uid[:8]}'),
        )
    db_conn.commit()
    yield uid
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_chat_sessions WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM ai_chat_batches WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    db_conn.commit()


def _seed_batch(db_conn, user_id, *, reuse=None, oc_sid=None):
    """种子批 + 一个子会话。oc_sid 非 None 时子会话已绑定 OpenCode 会话。"""
    bid = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total, subagent_reuse) "
            "VALUES (%s, %s, 'sru', 'p', 1, %s)",
            (bid, user_id, json.dumps(reuse) if reuse else None),
        )
        cur.execute(
            "INSERT INTO ai_chat_sessions "
            "  (id, user_id, status, batch_id, batch_seq, batch_input_file, "
            "   opencode_session_id, workspace_path) "
            "VALUES (%s, %s, 'pending', %s, 0, 'f0.csv', %s, %s)",
            (sid, user_id, bid, oc_sid, f'C:\\Users\\admin\\.check-manage\\ai-workspaces\\user-admin\\{sid}'),
        )
    db_conn.commit()
    return bid, sid


# ---------------------------------------------------------------------------
# 1. 配置校验
# ---------------------------------------------------------------------------

def test_validate_subagent_reuse():
    from utils.batch_repo import validate_subagent_reuse
    assert validate_subagent_reuse(None) == []
    assert validate_subagent_reuse([]) == []
    assert validate_subagent_reuse(['dev', ' dev ', 'dev']) == ['dev']
    assert validate_subagent_reuse('dev,reviewer') == ['dev', 'reviewer']  # 字符串容错
    with pytest.raises(ValueError):
        validate_subagent_reuse([''])          # 空名
    with pytest.raises(ValueError):
        validate_subagent_reuse([123])         # 非字符串
    with pytest.raises(ValueError):
        validate_subagent_reuse({'dev': 1})    # 非数组


# ---------------------------------------------------------------------------
# 2. 配置落库（create + _UNSET 更新语义）
# ---------------------------------------------------------------------------

def test_create_and_update_subagent_reuse(db_conn, user_id):
    from utils.batch_repo import create_batch, update_batch_config
    out = create_batch(user_id, name='sru', prompt='p', template_id=None,
                       files=[], subagent_reuse=['dev', 'reviewer'])
    bid = out['batch']['id']
    with db_conn.cursor() as cur:
        cur.execute("SELECT subagent_reuse FROM ai_chat_batches WHERE id=%s", (bid,))
        assert cur.fetchone()[0] == ['dev', 'reviewer']
    # _UNSET：未传保持原值
    update_batch_config(user_id, bid, agent='build', model='m1')
    with db_conn.cursor() as cur:
        cur.execute("SELECT subagent_reuse FROM ai_chat_batches WHERE id=%s", (bid,))
        assert cur.fetchone()[0] == ['dev', 'reviewer']
    # 显式传空数组 = 清空
    update_batch_config(user_id, bid, agent='build', model='m1', subagent_reuse=[])
    with db_conn.cursor() as cur:
        cur.execute("SELECT subagent_reuse FROM ai_chat_batches WHERE id=%s", (bid,))
        assert cur.fetchone()[0] is None


# ---------------------------------------------------------------------------
# 3. 内部端点（插件契约）：鉴权 / enabled 三态 / pins 登记与查询
# ---------------------------------------------------------------------------

@pytest.fixture
def internal_client(db_conn):
    import db as db_module
    db_module.pool = None
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if getattr(mod, 'get_db', None) is not None and (
            mod_name.startswith('routes.') or mod_name.startswith('utils.')
                or mod_name == 'auth'):
            try:
                mod.get_db = db_module.get_db
            except (AttributeError, TypeError):
                pass
    from app import app
    app.config['TESTING'] = True
    return app.test_client()


def _hdr():
    return {'X-Internal-Token': MCP_INTERNAL_TOKEN, 'Content-Type': 'application/json'}


def test_internal_requires_token(internal_client):
    r = internal_client.get('/ai/subagent-internal/reuse?session=oc-x&agent=dev')
    assert r.status_code == 403
    r = internal_client.post('/ai/subagent-internal/pins', json={})
    assert r.status_code == 403


def test_reuse_enabled_with_pin(db_conn, internal_client, user_id):
    bid, sid = _seed_batch(db_conn, user_id, reuse=['dev'], oc_sid='oc-live-1')
    r = internal_client.get('/ai/subagent-internal/reuse?session=oc-live-1&agent=dev',
                            headers=_hdr())
    body = r.get_json()
    assert body['enabled'] is True
    assert body['taskId'] is None            # 首次委派：未钉住
    # OC 插件 after 回调登记
    r = internal_client.post('/ai/subagent-internal/pins', headers=_hdr(),
                             json={'session': 'oc-live-1', 'agent': 'dev',
                                   'taskId': 'ses_dev_a1'})
    assert r.get_json()['pinned'] is True
    r = internal_client.get('/ai/subagent-internal/reuse?session=oc-live-1&agent=dev',
                            headers=_hdr())
    assert r.get_json()['taskId'] == 'ses_dev_a1'
    # pins 表锚定到平台子会话行
    with db_conn.cursor() as cur:
        cur.execute("SELECT agent, task_id FROM ai_subagent_pins "
                    "WHERE root_session_id=%s", (sid,))
        assert cur.fetchall() == [('dev', 'ses_dev_a1')]


def test_reuse_disabled_cases(db_conn, internal_client, user_id):
    # 未配置 agent 的批次
    bid, sid = _seed_batch(db_conn, user_id, reuse=None, oc_sid='oc-live-2')
    r = internal_client.get('/ai/subagent-internal/reuse?session=oc-live-2&agent=dev',
                            headers=_hdr())
    assert r.get_json()['enabled'] is False
    # 配置了别的 agent
    bid2, sid2 = _seed_batch(db_conn, user_id, reuse=['reviewer'], oc_sid='oc-live-3')
    r = internal_client.get('/ai/subagent-internal/reuse?session=oc-live-3&agent=dev',
                            headers=_hdr())
    assert r.get_json()['enabled'] is False
    # 未知会话
    r = internal_client.get('/ai/subagent-internal/reuse?session=oc-ghost&agent=dev',
                            headers=_hdr())
    assert r.get_json()['enabled'] is False
    # 未启用时 POST pins 是 no-op
    r = internal_client.post('/ai/subagent-internal/pins', headers=_hdr(),
                             json={'session': 'oc-live-3', 'agent': 'dev',
                                   'taskId': 'ses_x'})
    assert r.get_json()['pinned'] is False
    # 共享开发库可能有其他测试的残留 pin——只断言本批/本会话范围内无登记
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ai_subagent_pins "
                    "WHERE root_session_id IN (%s, %s, %s)", (sid, sid2, sid2))
        assert cur.fetchone()[0] == 0


# ---------------------------------------------------------------------------
# 4. 引擎：复用指令注入（新建分支，端到端 _run_one）
# ---------------------------------------------------------------------------

def test_directive_injected_for_pinned_agent(db_conn, user_id, monkeypatch,
                                             tmp_path):
    """复用名单 + 已钉住：派发 prompt 必须包含 task_id 指令（新建分支）。"""
    from pathlib import Path
    from utils.batch_engine import BatchWorker
    import utils.batch_engine as eng

    root = Path(tmp_path)
    monkeypatch.setattr(eng, '_workspace_root', lambda: str(root))
    staged = root / 'batch-staging' / user_id / 'u1'
    staged.mkdir(parents=True)
    (staged / 'f0.csv').write_text('x', encoding='utf-8')

    bid, sid = _seed_batch(db_conn, user_id, reuse=['dev'], oc_sid=None)
    rel = f'batch-staging/{user_id}/u1/f0.csv'
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET batch_input_file=%s WHERE id=%s",
                    (rel, sid))
        # 钉住 dev → ses_dev9
        cur.execute(
            "INSERT INTO ai_subagent_pins (id, root_session_id, batch_id, agent, task_id) "
            "VALUES (%s, %s, %s, 'dev', 'ses_dev9')",
            ('spin-test-' + uuid.uuid4().hex[:6], sid, bid))
    db_conn.commit()

    sent = []
    from unittest.mock import MagicMock
    fake = MagicMock()
    fake.create_session.return_value = 'oc-' + uuid.uuid4().hex[:8]
    fake.list_agents.return_value = [{'name': 'build', 'mode': 'primary'}]

    def _send(oc, prompt, directory='', agent='', model=''):
        sent.append(prompt)
    fake.send_message.side_effect = _send
    fake.list_messages.return_value = [
        {'role': 'assistant', 'finished': True,
         'content': [{'type': 'text', 'text': 'done'}]}]
    fake.get_messages.return_value = []
    monkeypatch.setattr(eng, 'opencode_client', fake)

    w = BatchWorker()
    claimed = w._claim_pending_sessions(limit=1)
    assert claimed and claimed[0]['id'] == sid
    w._run_one(claimed[0])
    assert sent, 'prompt 未派发'
    assert 'task_id: ses_dev9' in sent[0]
    assert '子代理会话复用规则' in sent[0]
    # 该 fake 轮次没有真实委派 → 无新子会话，锚点保持不变
    with db_conn.cursor() as cur:
        cur.execute("SELECT task_id FROM ai_subagent_pins "
                    "WHERE root_session_id=%s AND agent='dev'", (sid,))
        assert cur.fetchone()[0] == 'ses_dev9'


# ---------------------------------------------------------------------------
# 5. turn_segments：跨轮合并幂等
# ---------------------------------------------------------------------------

def _child_msgs(user_text, uid):
    return [
        {'info': {'role': 'user', 'id': f'um-{uid}'},
         'parts': [{'type': 'text', 'text': user_text}]},
        {'info': {'role': 'assistant', 'id': f'am-{uid}'},
         'parts': [{'type': 'text', 'text': 'done'}]},
    ]


def test_turn_segments_merge_and_idempotent(db_conn, user_id):
    from utils.batch_engine import BatchWorker
    bid, sid = _seed_batch(db_conn, user_id)
    w = BatchWorker()
    info = {'agent': 'dev', 'status': 'completed'}
    msgs1 = _child_msgs('task one', '1')
    w._write_subtask(sid, 'ses_dev_a1', info, {'ses_dev_a1': 'completed'},
                     msgs1, turn_no=0, turn_label='round one',
                     reuse_agents=['dev'])
    # 重复落库（进度快照）→ 不重复追加
    w._write_subtask(sid, 'ses_dev_a1', info, {'ses_dev_a1': 'completed'},
                     msgs1, turn_no=0, turn_label='round one',
                     reuse_agents=['dev'])
    with db_conn.cursor() as cur:
        cur.execute("SELECT turn_segments FROM ai_chat_subtasks WHERE id=%s",
                    ('ses_dev_a1',))
        segs = cur.fetchone()[0]
    assert len(segs) == 1
    assert segs[0]['label'] == 'task one'
    assert segs[0]['turn'] == 0
    # 新一轮委派（同子会话续跑，多了一条 user 消息）→ 追加任务段
    msgs2 = msgs1 + _child_msgs('task two', '2')
    w._write_subtask(sid, 'ses_dev_a1', info, {'ses_dev_a1': 'completed'},
                     msgs2, turn_no=3, turn_label='round two',
                     reuse_agents=['dev'])
    with db_conn.cursor() as cur:
        cur.execute("SELECT turn_segments FROM ai_chat_subtasks WHERE id=%s",
                    ('ses_dev_a1',))
        segs = cur.fetchone()[0]
    assert len(segs) == 2
    assert segs[1]['turn'] == 3 and segs[1]['label'] == 'task two'
    assert segs[1]['firstMsgId'] == 'um-2'


def test_write_subtask_pins_latest_session(db_conn, user_id):
    """复用名单内的 agent：最新子会话 id 固化为锚点（双保险路径）。"""
    from utils.batch_engine import BatchWorker
    bid, sid = _seed_batch(db_conn, user_id, reuse=['dev'])
    w = BatchWorker()
    info = {'agent': 'dev', 'status': 'completed'}
    w._write_subtask(sid, 'ses_dev_a1', info, {}, _child_msgs('t1', '1'),
                     reuse_agents=['dev'])
    w._write_subtask(sid, 'ses_dev_b2', info, {}, _child_msgs('t2', '2'),
                     reuse_agents=['dev'])
    with db_conn.cursor() as cur:
        cur.execute("SELECT task_id FROM ai_subagent_pins "
                    "WHERE root_session_id=%s AND agent='dev'", (sid,))
        assert cur.fetchone()[0] == 'ses_dev_b2'  # 最新者胜


# ---------------------------------------------------------------------------
# 6. 插件部署
# ---------------------------------------------------------------------------

def test_plugin_deploy_idempotent(tmp_path):
    from utils.subagent_reuse_plugin import ensure_subagent_reuse_plugin
    gd = str(tmp_path / 'oc-global')
    ep = 'http://127.0.0.1:3002/ai/subagent-internal'
    p1 = ensure_subagent_reuse_plugin(gd, ep, 'tok-1')
    assert p1 and os.path.isfile(p1)
    src1 = open(p1, encoding='utf-8').read()
    assert ep in src1
    assert '/reuse' in src1 and '/pins' in src1
    # callID intent 流程：/reuse 带 callId 登记、/pins 按 callId 直写 pin
    # （取代旧 /resolve-agent 两跳解析，消除 pin 登记与持久化的时序竞态 R1）
    assert 'callId' in src1 and 'callID' in src1
    assert 'task_id' in src1 and 'tool.execute.before' in src1
    p2 = ensure_subagent_reuse_plugin(gd, ep, 'tok-1')
    assert p1 == p2
    # 内容变化（token 轮换）→ 重写
    ensure_subagent_reuse_plugin(gd, ep, 'tok-2')
    src2 = open(p1, encoding='utf-8').read()
    assert src2 != src1 and 'tok-2' in src2
