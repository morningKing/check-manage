# -*- coding: utf-8 -*-
"""verifier 判官单元（设计 docs/design/ai/AI动作门禁verifier判官核对设计.md §6-§9）。
打桩 opencode_client；会话/期望行用真库种子并在 user fixture 里清理。"""
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def user_id(db_conn):
    uid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, 'x', %s, 'developer')",
            (uid, f'ver_{uid[:8]}', f'VER {uid[:8]}'),
        )
    db_conn.commit()
    yield uid
    with db_conn.cursor() as cur:
        # 账本行按 oc_session_id 幂等唯一，不清理会让下一次套件跑批
        # 在 _seed_child 处撞 uq_agent_tool_call_part——按本 fixture 的
        # 子会话精确回收（须先于会话删除）。
        cur.execute(
            "DELETE FROM agent_tool_calls WHERE root_session_id IN "
            "(SELECT id FROM ai_chat_sessions WHERE user_id = %s)", (uid,))
        cur.execute("DELETE FROM ai_chat_sessions WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM ai_chat_batches WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    db_conn.commit()


def _seed_child(db_conn, user_id, *, oc_sid, with_reply=True, with_trace=True):
    """种子批 + running 子会话 + （可选）assistant 回复与账本轨迹。返回 sid。"""
    from db import get_db
    bid, sid = str(uuid.uuid4()), str(uuid.uuid4())
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
                "VALUES (%s, %s, 'ver', 'p', 1)", (bid, user_id))
            cur.execute(
                "INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, "
                "  batch_seq, opencode_session_id, workspace_path, session_token) "
                "VALUES (%s, %s, 'running', %s, 0, %s, %s, %s)",
                (sid, user_id, bid, oc_sid, f'C:\\ver-e2e\\{sid}', f'tok-{sid[:12]}'))
            if with_reply:
                cur.execute(
                    "INSERT INTO ai_chat_messages (id, session_id, role, content) "
                    "VALUES (%s, %s, 'assistant', %s::jsonb)",
                    (f'm-{sid[:8]}', sid, json.dumps(
                        [{'type': 'text', 'text': '结果已写入,包含 DONE-MARK'}])))
            if with_trace:
                cur.execute(
                    "INSERT INTO agent_tool_calls (oc_session_id, root_session_id, "
                    "  subtask_id, agent, part_id, tool, args_text, state) "
                    "VALUES (%s, %s, NULL, NULL, 'p1', 'write', 'filePath=out.md', 'completed')",
                    (oc_sid, sid))
    db_conn.commit()
    return sid


def _register(sid, name='结论含标记', rubric='回复包含 DONE-MARK'):
    from utils.agent_ledger import register_session_expectations
    from db import get_db
    return register_session_expectations(
        sid, [{'name': name, 'check_type': 'verifier', 'rubric': rubric}], get_db=get_db)


class _FakeClient:
    """按脚本回放的 OpenCode 客户端桩：script 逐次弹出，弹尽后回放 _last。"""
    def __init__(self, script, last=None):
        self.script = list(script)
        self._last = last or []
        self.created, self.sent, self.aborted = [], [], []

    def create_session(self, *, directory, title=''):
        self.created.append(directory)
        return 'oc-verifier-1'

    def send_prompt_async(self, oc, content, model='', directory='', agent='', agent_parts=None):
        self.sent.append((oc, agent, model, directory))

    def get_messages(self, oc, directory=''):
        return self.script.pop(0) if self.script else self._last

    def abort_session(self, oc, directory=''):
        self.aborted.append(oc)


def _finished(text):
    return [{'role': 'user', 'finished': True, 'parts': [{'type': 'text', 'text': 'judge'}]},
            {'role': 'assistant', 'finished': True, 'parts': [{'type': 'text', 'text': text}]}]


def test_collect_materials_and_noop(db_conn, user_id):
    from utils import verifier
    from db import get_db
    sid = _seed_child(db_conn, user_id, oc_sid='oc-m1')
    assert verifier.collect_materials(sid, get_db=get_db) is None  # 未登记期望
    _register(sid)
    m = verifier.collect_materials(sid, get_db=get_db)
    assert 'DONE-MARK' in m['reply'] and m['trace'][0]['tool'] == 'write'
    assert m['checks'] == [{'name': '结论含标记', 'rubric': '回复包含 DONE-MARK'}]


def test_run_verifier_completed(db_conn, user_id):
    from utils import verifier
    from db import get_db
    sid = _seed_child(db_conn, user_id, oc_sid='oc-m2')
    _register(sid)
    verdict = json.dumps({'results': [{'name': '结论含标记', 'verdict': 'passed',
                                       'reasons': ['原文包含'], 'evidence': '回复第 1 段'}]},
                         ensure_ascii=False)
    fake = _FakeClient([_finished('思考中…'), _finished(verdict)])
    run = verifier.run_verifier(sid, workspace_path='C:\\ver-e2e\\x', model='p/m',
                                client=fake, get_db=get_db)
    assert run['status'] == 'completed' and run['results'][0]['status'] == 'passed'
    assert fake.created == ['C:\\ver-e2e\\x']
    assert fake.sent[0][1] == 'baize-verifier' and fake.sent[0][2] == 'p/m'
    assert fake.aborted == []


def test_run_verifier_noop_without_expectations(db_conn, user_id):
    from utils import verifier
    from db import get_db
    sid = _seed_child(db_conn, user_id, oc_sid='oc-m3', with_reply=False, with_trace=False)
    fake = _FakeClient([])
    assert verifier.run_verifier(sid, workspace_path='x', model='p/m',
                                 client=fake, get_db=get_db) is None
    assert fake.created == []  # 未开 OC 会话（零开销路径）


def test_run_verifier_timeout_aborts(db_conn, user_id, monkeypatch):
    from utils import verifier
    from db import get_db
    sid = _seed_child(db_conn, user_id, oc_sid='oc-m4')
    _register(sid)
    fake = _FakeClient([{'role': 'assistant', 'finished': False, 'parts': []}])
    monkeypatch.setattr(verifier, 'POLL_INTERVAL_SEC', 0.01)
    run = verifier.run_verifier(sid, workspace_path='x', model='p/m',
                                timeout_sec=0.05, client=fake, get_db=get_db)
    assert run['status'] == 'error'
    assert fake.aborted == ['oc-verifier-1']
    assert all(r['status'] == 'inconclusive' for r in run['results'])


def test_parse_verdicts_contract():
    from utils.verifier import parse_verdicts
    ok = parse_verdicts(
        '前置说明\n{"results": [{"name": "a", "verdict": "failed", "reasons": ["缺"], "evidence": "-"}]}',
        ['a', 'b'])
    assert ok['results'][0]['status'] == 'failed'
    assert ok['unresolved'] == ['b']                       # 缺名 → inconclusive
    bad = parse_verdicts('完全不是 JSON', ['a'])
    assert bad['error'] and all(r['status'] == 'inconclusive' for r in bad['results'])


def test_merge_verifier_results_status_and_persistence(db_conn, user_id):
    from utils import verifier
    from db import get_db
    sid = _seed_child(db_conn, user_id, oc_sid='oc-m5')
    _register(sid)
    gate = {'status': 'passed', 'results': []}
    run = {'status': 'completed', 'error': None, 'results': [
        {'name': '结论含标记', 'kind': 'verifier', 'status': 'failed',
         'reasons': ['缺少 DONE-MARK'], 'evidence': '全篇未见', 'min_count': 1,
         'check_type': 'verifier', 'effect_spec': {'rubric': '回复包含 DONE-MARK'}}]}
    merged = verifier.merge_verifier_results(gate, run, sid, get_db=get_db)
    assert merged['status'] == 'failed' and merged['results'][0]['kind'] == 'verifier'
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT last_status, last_evidence FROM action_expectations WHERE scope_id=%s", (sid,))
            assert cur.fetchone() == ('failed', 0)


def test_ensure_verifier_agent_idempotent(tmp_path):
    from utils.verifier import ensure_verifier_agent, VERIFIER_AGENT_NAME
    p1 = ensure_verifier_agent(str(tmp_path))
    assert p1 and os.path.isfile(p1) and VERIFIER_AGENT_NAME in p1
    first = open(p1, encoding='utf-8').read()
    mtime1 = os.path.getmtime(p1)
    assert ensure_verifier_agent(str(tmp_path)) == p1
    assert os.path.getmtime(p1) == mtime1 and open(p1, encoding='utf-8').read() == first
    assert 'mode: primary' in first and 'task: false' in first
