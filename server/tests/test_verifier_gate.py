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


def _register(sid, name='结论含标记', rubric='回复包含 DONE-MARK', subagents=None,
              markers=None):
    from ai.batch.ledger import register_session_expectations
    from db import get_db
    check = {'name': name, 'check_type': 'verifier', 'rubric': rubric}
    if subagents:
        check['subagents'] = subagents
    if markers:
        check['markers'] = markers
    return register_session_expectations(sid, [check], get_db=get_db)


def _seed_subtask(db_conn, sid, oc_sid, *, agent='dev', subtask_id=None,
                  msgs=(('user', '报告一号'), ('assistant', '一号OK'))):
    """种子一个子代理会话（ai_chat_subtasks）与其消息，返回 subtask_id。"""
    from db import get_db
    st_id = subtask_id or f'ses_{str(uuid.uuid4())[:8]}'
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ai_chat_subtasks (id, root_session_id, agent, status) "
                "VALUES (%s, %s, %s, 'completed')", (st_id, sid, agent))
            for i, (role, text) in enumerate(msgs):
                cur.execute(
                    "INSERT INTO ai_chat_subtask_messages (id, subtask_id, role, content) "
                    "VALUES (%s, %s, %s, %s::jsonb)",
                    (f'sm-{st_id[4:12]}-{i}', st_id, role,
                     json.dumps([{'type': 'text', 'text': text}])))
            cur.execute(
                "INSERT INTO agent_tool_calls (oc_session_id, root_session_id, "
                "  subtask_id, agent, part_id, tool, args_text, state) "
                "VALUES (%s, %s, %s, %s, %s, 'read', 'filePath=src/a.py', 'completed')",
                (oc_sid, sid, st_id, agent, f'sp-{st_id[4:12]}'))
    db_conn.commit()
    return st_id


class _FakeClient:
    """按脚本回放的 OpenCode 客户端桩：script 逐次弹出，弹尽后回放 _last。"""
    def __init__(self, script, last=None):
        self.script = list(script)
        self._last = last or []
        self.created, self.sent, self.aborted = [], [], []

    def list_agents(self, directory=''):
        from ai.batch.verifier import VERIFIER_AGENT_NAME
        return [{'name': VERIFIER_AGENT_NAME, 'mode': 'primary'}]

    def create_session(self, *, directory, title=''):
        self.created.append(directory)
        return 'oc-verifier-1'

    def send_prompt_async(self, oc, content, model='', directory='', agent='', agent_parts=None):
        self.sent.append((oc, agent, model, directory))

    def get_messages(self, oc, directory=''):
        return self.script.pop(0) if self.script else self._last

    def abort_session(self, oc, directory=''):
        self.aborted.append(oc)


class _FakeClientNoAgent(_FakeClient):
    """预检不过：OC 侧未注册 baize-verifier（agent md 部署后 serve 未重启）。"""
    def list_agents(self, directory=''):
        return []


def _finished(text):
    """判官终态消息（OpenCode 原始形状）：finish='stop' 且 time.completed 非空。"""
    return [{'info': {'role': 'user', 'finish': 'stop', 'time': {'completed': 1}},
             'parts': [{'type': 'text', 'text': 'judge'}]},
            {'info': {'role': 'assistant', 'finish': 'stop', 'time': {'completed': 1}},
             'parts': [{'type': 'text', 'text': text}]}]


def _running():
    """判官中间态消息：finish='tool-calls'（continuation）且无 completed——未终了。
    故意返回裸 dict（非列表）：顺带覆盖 get_messages 回裸对象时的归一防御。"""
    return {'info': {'role': 'assistant', 'finish': 'tool-calls'}, 'parts': []}


def test_collect_materials_and_noop(db_conn, user_id):
    from ai.batch import verifier as verifier
    from db import get_db
    sid = _seed_child(db_conn, user_id, oc_sid='oc-m1')
    assert verifier.collect_materials(sid, get_db=get_db) is None  # 未登记期望
    _register(sid)
    m = verifier.collect_materials(sid, get_db=get_db)
    # 无 subagents → 单一全树组（agents=None）
    assert len(m['groups']) == 1 and m['groups'][0]['agents'] is None
    g = m['groups'][0]
    assert 'DONE-MARK' in g['reply'] and g['trace'][0]['tool'] == 'write'
    assert g['checks'] == [{'name': '结论含标记', 'rubric': '回复包含 DONE-MARK'}]


def test_collect_materials_subagent_grouping(db_conn, user_id):
    """定向分组：subagents 名单 → 材料=该 agent 名下子代理会话消息+其轨迹；
    无名单期望独立成全树组；同一名单合并为一组。"""
    from ai.batch import verifier as verifier
    from db import get_db
    sid = _seed_child(db_conn, user_id, oc_sid='oc-mg')
    st_dev = _seed_subtask(db_conn, sid, 'oc-mg', agent='dev',
                           msgs=(('user', '报告一号'), ('assistant', '一号OK')))
    _seed_subtask(db_conn, sid, 'oc-mg', agent='qa',
                  subtask_id='ses_qa_group', msgs=(('user', 'qa 任务'),))
    _register(sid)  # 全树组
    _register(sid, name='dev 语义', rubric='dev 回答一号OK', subagents=['dev'])
    _register(sid, name='dev again', rubric='另一条 dev 期望', subagents=['dev'])
    _register(sid, name='qa 语义', rubric='qa 完成', subagents=['qa'])
    m = verifier.collect_materials(sid, get_db=get_db)
    groups = {tuple(g['agents'] or []): g for g in m['groups']}
    assert set(groups.keys()) == {(), ('dev',), ('qa',)}
    tree, dev, qa = groups[()], groups[('dev',)], groups[('qa',)]
    # 全树组：最终回复 + 全树轨迹（含子代理行）
    assert 'DONE-MARK' in tree['reply']
    assert len(tree['trace']) == 3          # 根 write + dev read + qa read
    # dev 定向组：两条期望合并一组、材料只有 dev 的子代理消息与其轨迹
    assert [c['name'] for c in dev['checks']] == ['dev 语义', 'dev again']
    assert [s['subtask_id'] for s in dev['subagent_segments']] == [st_dev]
    dev_text = json.dumps(dev['subagent_segments'], ensure_ascii=False)
    assert '报告一号' in dev_text and '一号OK' in dev_text and 'qa 任务' not in dev_text
    assert len(dev['trace']) == 1 and dev['trace'][0]['tool'] == 'read'
    # qa 定向组互不混料
    assert 'qa 任务' in json.dumps(qa['subagent_segments'], ensure_ascii=False)


def test_build_prompt_subagent_group(db_conn, user_id):
    """定向组 prompt：含子代理会话消息与 agent 标注；空子代理时给证据提示。"""
    from ai.batch import verifier as verifier
    group = {'agents': ('dev',), 'checks': [{'name': 'n', 'rubric': 'r'}],
             'subagent_segments': [{'agent': 'dev', 'subtask_id': 'ses_x',
                                    'messages': [{'role': 'user', 'text': '任务A'},
                                                 {'role': 'assistant', 'text': '完成A'}]}],
             'trace': [{'oc': 'ses_x', 'tool': 'read', 'args': 'f', 'state': 'completed',
                        'at': None}]}
    p = verifier.build_verifier_prompt(group)
    assert '子代理 dev' in p and '任务A' in p and '完成A' in p and 'read' in p
    empty = verifier.build_verifier_prompt(
        {'agents': ('dev',), 'checks': group['checks'],
         'subagent_segments': [], 'trace': []})
    assert '没有发现任何子代理会话' in empty


def test_run_verifier_multi_group(db_conn, user_id):
    """分组核对：全树组 + 定向组各一轮判官会话；verdict 行带 agent 标注。
    组内正常判定 failed 不是组错误——整体 status 仍 completed（failed 由
    merge 聚合为 gate failed）。"""
    from ai.batch import verifier as verifier
    from db import get_db
    sid = _seed_child(db_conn, user_id, oc_sid='oc-m9')
    _seed_subtask(db_conn, sid, 'oc-m9', agent='dev')
    _register(sid)
    _register(sid, name='dev 语义', rubric='dev 回答正确', subagents=['dev'])
    v_tree = json.dumps({'results': [{'name': '结论含标记', 'verdict': 'passed',
                                      'reasons': ['ok'], 'evidence': 'r1'}]},
                        ensure_ascii=False)
    v_dev = json.dumps({'results': [{'name': 'dev 语义', 'verdict': 'failed',
                                     'reasons': ['回答不符'], 'evidence': 'msg#2'}]},
                       ensure_ascii=False)
    fake = _FakeClient([_finished(v_tree), _finished(v_dev)])
    run = verifier.run_verifier(sid, workspace_path='C:\\ver-e2e\\x', model='p/m',
                                client=fake, get_db=get_db)
    assert run['status'] == 'completed' and run['error'] is None
    by_name = {r['name']: r for r in run['results']}
    assert by_name['结论含标记']['status'] == 'passed'
    assert by_name['结论含标记']['agent'] is None
    assert by_name['dev 语义']['status'] == 'failed'
    assert by_name['dev 语义']['agent'] == 'dev'
    assert len(fake.created) == 2            # 每组一次判官会话



def test_run_verifier_completed(db_conn, user_id):
    from ai.batch import verifier as verifier
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
    from ai.batch import verifier as verifier
    from db import get_db
    sid = _seed_child(db_conn, user_id, oc_sid='oc-m3', with_reply=False, with_trace=False)
    fake = _FakeClient([])
    assert verifier.run_verifier(sid, workspace_path='x', model='p/m',
                                 client=fake, get_db=get_db) is None
    assert fake.created == []  # 未开 OC 会话（零开销路径）


def test_run_verifier_timeout_aborts(db_conn, user_id, monkeypatch):
    from ai.batch import verifier as verifier
    from db import get_db
    sid = _seed_child(db_conn, user_id, oc_sid='oc-m4')
    _register(sid)
    fake = _FakeClient([_running()])
    monkeypatch.setattr(verifier, 'POLL_INTERVAL_SEC', 0.01)
    run = verifier.run_verifier(sid, workspace_path='x', model='p/m',
                                timeout_sec=0.05, client=fake, get_db=get_db)
    assert run['status'] == 'error'
    assert fake.aborted == ['oc-verifier-1']
    assert all(r['status'] == 'inconclusive' for r in run['results'])


def test_run_verifier_default_client_construction(db_conn, user_id, monkeypatch):
    """client=None 走生产默认构造点（惰性 OpenCodeClient(OPENCODE_BASE_URL) 惯例）：
    构造恰一次；create_session/send_prompt_async/abort 拿的是同一个构造实例。"""
    from ai.batch import verifier as verifier
    from db import get_db
    sid = _seed_child(db_conn, user_id, oc_sid='oc-m6')
    _register(sid)
    fake = _FakeClient([_running()])
    built = []

    def _factory():
        built.append(1)
        return fake

    monkeypatch.setattr(verifier, '_default_client', _factory)
    monkeypatch.setattr(verifier, 'POLL_INTERVAL_SEC', 0.01)
    run = verifier.run_verifier(sid, workspace_path='x', model='p/m',
                                client=None, timeout_sec=0.05, get_db=get_db)
    assert built == [1]                          # 默认路径恰一次构造
    assert fake.created == ['x']                 # create_session 经构造实例调用
    assert fake.sent[0][1] == 'baize-verifier'   # send_prompt_async 同一实例
    assert fake.aborted == ['oc-verifier-1']     # _abort_quiet 亦同一实例
    assert run['status'] == 'error'
    assert all(r['status'] == 'inconclusive' for r in run['results'])


def test_run_verifier_missing_agent_fails_closed(db_conn, user_id):
    """预检不过（OC 侧未注册 baize-verifier）→ 不建会话、不发 prompt，
    直接故障路径全 inconclusive（防判官轮以默认 primary agent 跑）。"""
    from ai.batch import verifier as verifier
    from db import get_db
    sid = _seed_child(db_conn, user_id, oc_sid='oc-m7')
    _register(sid)
    fake = _FakeClientNoAgent([])
    run = verifier.run_verifier(sid, workspace_path='x', model='p/m',
                                client=fake, get_db=get_db)
    assert run['status'] == 'error'
    assert '重启 OC serve' in run['error']
    assert all(r['status'] == 'inconclusive' for r in run['results'])
    assert fake.created == [] and fake.sent == []   # 预检不过不碰会话


def test_parse_verdicts_contract():
    from ai.batch.verifier import parse_verdicts
    ok = parse_verdicts(
        '前置说明\n{"results": [{"name": "a", "verdict": "failed", "reasons": ["缺"], "evidence": "-"}]}',
        ['a', 'b'])
    assert ok['results'][0]['status'] == 'failed'
    assert ok['unresolved'] == ['b']                       # 缺名 → inconclusive
    bad = parse_verdicts('完全不是 JSON', ['a'])
    assert bad['error'] and all(r['status'] == 'inconclusive' for r in bad['results'])


def test_merge_verifier_results_status_and_persistence(db_conn, user_id):
    from ai.batch import verifier as verifier
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
    from ai.batch.verifier import ensure_verifier_agent, VERIFIER_AGENT_NAME
    p1 = ensure_verifier_agent(str(tmp_path))
    assert p1 and os.path.isfile(p1) and VERIFIER_AGENT_NAME in p1
    first = open(p1, encoding='utf-8').read()
    mtime1 = os.path.getmtime(p1)
    assert ensure_verifier_agent(str(tmp_path)) == p1
    assert os.path.getmtime(p1) == mtime1 and open(p1, encoding='utf-8').read() == first
    assert 'mode: primary' in first and 'task: false' in first


# ---------------------------------------------------------------------------
# 机械复核护栏（2026-11-08，fail-closed）：期望声明 markers（材料须含标记）
# 而判官给 passed 时，逐标记复核判官材料全文——标记不在 → 强制 failed。
# 只收紧不放宽：LLM failed 一律不动；无 markers 的纯语义期望不碰。
# ---------------------------------------------------------------------------

def _passed_verdict(name, reasons=('材料包含',)):
    return json.dumps({'results': [{'name': name, 'verdict': 'passed',
                                    'reasons': list(reasons), 'evidence': 'r1'}]},
                      ensure_ascii=False)


def test_run_verifier_guardrail_overrides_pass_on_missing_marker(db_conn, user_id):
    """判官 pass + 声明标记未出现在材料（回复/轨迹/子代理消息）→ 强制 failed。
    材料语料不含 rubric 自身——rubric 里写了标记字样不算「出现在材料」。"""
    from ai.batch import verifier as verifier
    from db import get_db
    sid = _seed_child(db_conn, user_id, oc_sid='oc-gr1')   # 回复只含 DONE-MARK
    _register(sid, name='含标记核对', rubric='回复包含 NEVER-MARK-XYZ',
              markers=['NEVER-MARK-XYZ'])
    fake = _FakeClient([_finished(_passed_verdict('含标记核对'))])
    run = verifier.run_verifier(sid, workspace_path='x', model='p/m',
                                client=fake, get_db=get_db)
    assert run['status'] == 'completed' and run['error'] is None  # 正常判定非组错误
    row = run['results'][0]
    assert row['status'] == 'failed'
    assert any('机械复核否决判官 pass' in x for x in row['reasons'])
    assert any('NEVER-MARK-XYZ' in x for x in row['reasons'])
    # 合并进 gate：failed 聚合 + 期望行回写 failed（gate_failed 计数来源）
    merged = verifier.merge_verifier_results(
        {'status': 'passed', 'results': []}, run, sid, get_db=get_db)
    assert merged['status'] == 'failed'
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT last_status FROM action_expectations WHERE scope_id=%s", (sid,))
            assert cur.fetchone() == ('failed',)


def test_run_verifier_guardrail_respects_marker_in_materials(db_conn, user_id):
    """标记确实出现在材料（回复或轨迹 args）→ 判官 pass 原样放行（不误伤）。"""
    from ai.batch import verifier as verifier
    from db import get_db
    sid = _seed_child(db_conn, user_id, oc_sid='oc-gr2')   # 回复含 DONE-MARK
    _register(sid, name='含标记核对', rubric='回复包含 DONE-MARK',
              markers=['DONE-MARK'])
    fake = _FakeClient([_finished(_passed_verdict('含标记核对'))])
    run = verifier.run_verifier(sid, workspace_path='x', model='p/m',
                                client=fake, get_db=get_db)
    assert run['results'][0]['status'] == 'passed'
    # 标记只在轨迹 args（不在回复）→ 同样放行
    sid2 = _seed_child(db_conn, user_id, oc_sid='oc-gr2b',
                       with_reply=False, with_trace=False)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_tool_calls (oc_session_id, root_session_id, "
                "  subtask_id, agent, part_id, tool, args_text, state) "
                "VALUES (%s, %s, NULL, NULL, 'p1', 'write', "
                "  'filePath=out/trace.md content=TRACE-ONLY-MARK', 'completed')",
                ('oc-gr2b', sid2))
    db_conn.commit()
    _register(sid2, name='轨迹标记', rubric='须写入 TRACE-ONLY-MARK',
              markers=['TRACE-ONLY-MARK'])
    fake2 = _FakeClient([_finished(_passed_verdict('轨迹标记'))])
    run2 = verifier.run_verifier(sid2, workspace_path='x', model='p/m',
                                 client=fake2, get_db=get_db)
    assert run2['results'][0]['status'] == 'passed'


def test_run_verifier_guardrail_never_upgrades_failed(db_conn, user_id):
    """红线：判官 failed 一律保持 failed——护栏只收紧不放宽。"""
    from ai.batch import verifier as verifier
    from db import get_db
    sid = _seed_child(db_conn, user_id, oc_sid='oc-gr3')
    _register(sid, name='含标记核对', rubric='回复包含 DONE-MARK',
              markers=['DONE-MARK'])
    verdict = json.dumps({'results': [{'name': '含标记核对', 'verdict': 'failed',
                                       'reasons': ['判官认定不符'], 'evidence': '-'}]},
                         ensure_ascii=False)
    fake = _FakeClient([_finished(verdict)])
    run = verifier.run_verifier(sid, workspace_path='x', model='p/m',
                                client=fake, get_db=get_db)
    row = run['results'][0]
    assert row['status'] == 'failed'
    assert row['reasons'] == ['判官认定不符']   # 原理由原样，无机械复核字样


def test_run_verifier_no_markers_leaves_llm_verdict(db_conn, user_id):
    """未声明 markers 的纯语义期望：判官判定权不变（passed 原样放行）。"""
    from ai.batch import verifier as verifier
    from db import get_db
    sid = _seed_child(db_conn, user_id, oc_sid='oc-gr4')
    _register(sid)   # 无 markers
    fake = _FakeClient([_finished(_passed_verdict('结论含标记'))])
    run = verifier.run_verifier(sid, workspace_path='x', model='p/m',
                                client=fake, get_db=get_db)
    assert run['results'][0]['status'] == 'passed'
    assert run['results'][0]['reasons'] == ['材料包含']


def test_collect_materials_carries_markers(db_conn, user_id):
    """collect_materials 把 effect_spec.markers 带进核对条目（护栏数据源）；
    未声明的条目形状不变（两键，向后兼容既有断言/读者）。"""
    from ai.batch import verifier as verifier
    from db import get_db
    sid = _seed_child(db_conn, user_id, oc_sid='oc-gr5')
    _register(sid)
    _register(sid, name='带标记', rubric='回复包含 X-MARK-1', markers=['X-MARK-1'])
    m = verifier.collect_materials(sid, get_db=get_db)
    checks = {c['name']: c for c in m['groups'][0]['checks']}
    assert checks['结论含标记'] == {'name': '结论含标记', 'rubric': '回复包含 DONE-MARK'}
    assert checks['带标记'] == {'name': '带标记', 'rubric': '回复包含 X-MARK-1',
                               'markers': ['X-MARK-1']}


def test_validate_verifier_markers_contract():
    """markers 契约：合法列表归一进 effect_spec（strip/去空）；非法形状 400 化；
    登记落库形状再次校验幂等（roundtrip）。"""
    from ai.batch.ledger import validate_checks
    ok = validate_checks([{'name': 'x', 'check_type': 'verifier',
                           'rubric': '回复包含 A-MARK-1',
                           'markers': [' A-MARK-1 ', 'B-MARK-2']}])
    assert ok[0]['effect_spec'] == {'rubric': '回复包含 A-MARK-1',
                                    'markers': ['A-MARK-1', 'B-MARK-2']}
    # 幂等：归一化形状再进 validate_checks 原样通过（派发登记再校验路径）
    assert validate_checks(ok) == ok
    # 空/全空 → 视为未声明（effect_spec 无 markers 键）
    empty = validate_checks([{'name': 'x', 'check_type': 'verifier',
                              'rubric': 'r', 'markers': ['', '  ']}])
    assert empty[0]['effect_spec'] == {'rubric': 'r'}
    # 非法形状 → ValueError（路由 400）
    base = {'name': 'x', 'check_type': 'verifier', 'rubric': 'r'}
    for bad in ('not-a-list', {'a': 1}, [42], [None], ['x' * 201]):
        with pytest.raises(ValueError, match='markers'):
            validate_checks([dict(base, markers=bad)])
    with pytest.raises(ValueError, match='markers'):
        validate_checks([dict(base, markers=['a'] * 11)])


def test_validate_verifier_markers_contract_documents_scope_and_case():
    """markers 契约文案（2026-11-08 审查裁定，Task 15 补写）：validate_checks
    契约说明必须写明两点——① 复核语料是判官材料文本（含截断限度：回复
    8000 字 / 子代理消息 40 条×500 字 / 轨迹 args 100 行×200 字），≠ 判官
    取证范围且未告知判官（否则判官凭文件取证 pass 会被复核必然否决）；
    ② 匹配是大小写敏感精确子串。"""
    import inspect
    from ai.batch.ledger.record import validate_checks
    src = inspect.getsource(validate_checks)
    # 点①：语料范围 = 判官材料文本 + 三段截断限度 + 与判官取证范围的关系
    assert '判官材料' in src
    assert '8000' in src and '40' in src and '500' in src \
        and '100' in src and '200' in src
    assert '判官取证范围' in src and '未告知' in src
    # 点②：大小写敏感精确子串
    assert '大小写敏感' in src and '精确子串' in src

