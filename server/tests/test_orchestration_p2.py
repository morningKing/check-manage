# -*- coding: utf-8 -*-
"""P2 编排与 Runtime 回归（ai-harness-p2 spec §12.1 核心）。

覆盖：定义校验（环/悬空边/join 语义/审批节点）、run 冻结版本、线性 DAG
推进（子会话由批 worker 认领→终态回调推进 DAG）、条件分支、并行 fan-out/
join、审批（等待/通过/拒绝/超时）、artifact store 去重与鉴权、runtime
adapter、claim 谓词覆盖编排子会话。
"""
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
            "VALUES (%s, %s, %s, %s, 'developer')",
            (uid, f'p2_user_{uid[:8]}', 'x', f'P2 User {uid[:8]}'),
        )
    db_conn.commit()
    yield uid
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_chat_sessions WHERE user_id = %s", (uid,))
        # 12 号 §4.2：ai_batch_events 无 FK 且挂在 run id 上，必须先于
        # run 删除回收（该 fixture 此前无任何事件回收 → 每轮泄漏）
        cur.execute(
            "DELETE FROM ai_batch_events WHERE batch_id IN "
            "(SELECT id FROM ai_orchestration_runs WHERE requested_by = %s)",
            (uid,))
        cur.execute("DELETE FROM ai_orchestration_runs WHERE requested_by = %s",
                    (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    db_conn.commit()


# M8（12 号 §4.1）：清理判定改为「归属用户已消失 / requested_by IS NULL」
# （见 _clear_other_pending docstring），无需名称前缀约定。


# 显式保留模式（M8，12 号 §4.1）：不再用宽 LIKE（e2e%/%-test 会误删真实
# 用户）——编排臂只认 fixture 生成的用户名模式（含 uuid 片段，真实用户
# 不会命中）与 requested_by IS NULL 的 run（create_run 恒有请求者，NULL
# 只可能来自测试直插 SQL）。
TEST_USER_PREFIXES = ('p2_user_%', 'gap_user_%')


def _clear_other_pending(db_conn, keep_run_id=None):
    """清掉残留 pending 行（共享库确定性认领）。编排臂按 TEST_USER_PREFIXES
    （fixture 保留模式）+ NULL requested_by run 收敛，不触碰真实用户数据。"""
    like = ' OR '.join("u.username LIKE %s" for _ in TEST_USER_PREFIXES)
    with db_conn.cursor() as cur:
        # 批次臂：保留 AITEST-/具名保留批（e2e/openapi 测试经 admin API 创建，
        # 用户是真实 admin，只能按批次保留前缀识别）；不再含宽匹配 e2e%/%-test
        cur.execute("DELETE FROM ai_chat_sessions s USING ai_chat_batches b "
                    "WHERE s.batch_id = b.id AND s.status='pending' "
                    "  AND (b.name LIKE 'AITEST-%' "
                    "       OR b.name IN ('engine-test', 'pause-test', 'gap-test'))")
        if keep_run_id:
            cur.execute(
                "DELETE FROM ai_chat_sessions s "
                "WHERE s.status='pending' AND s.batch_id IS NULL "
                "  AND s.api_key_id IS NULL AND s.orchestration_run_id IS NOT NULL "
                "  AND s.orchestration_run_id <> %s "
                "  AND s.orchestration_run_id IN ("
                "      SELECT r.id FROM ai_orchestration_runs r "
                "      LEFT JOIN users u ON u.id = r.requested_by "
                f"      WHERE r.requested_by IS NULL OR ({like}))",
                (keep_run_id, *TEST_USER_PREFIXES))
        else:
            cur.execute(
                "DELETE FROM ai_chat_sessions s "
                "WHERE s.status='pending' AND s.batch_id IS NULL "
                "  AND s.api_key_id IS NULL AND s.orchestration_run_id IS NOT NULL "
                "  AND s.orchestration_run_id IN ("
                "      SELECT r.id FROM ai_orchestration_runs r "
                "      LEFT JOIN users u ON u.id = r.requested_by "
                f"      WHERE r.requested_by IS NULL OR ({like}))",
                (*TEST_USER_PREFIXES,))
    db_conn.commit()


# ---------------------------------------------------------------------------
# 1. 定义校验与发布（spec §6.2）
# ---------------------------------------------------------------------------

def test_definition_validation_rejects_bad_graphs():
    from utils import orchestration_defs as defs
    with pytest.raises(ValueError):  # 环
        defs.validate_definition(
            [{'id': 'a', 'kind': 'agent', 'prompt_template': 'x'},
             {'id': 'b', 'kind': 'agent', 'prompt_template': 'x'}],
            [{'source': 'a', 'target': 'b'}, {'source': 'b', 'target': 'a'}])
    with pytest.raises(ValueError):  # 悬空边
        defs.validate_definition(
            [{'id': 'a', 'kind': 'agent', 'prompt_template': 'x'}],
            [{'source': 'a', 'target': 'ghost'}])
    with pytest.raises(ValueError):  # join 单入边
        defs.validate_definition(
            [{'id': 'a', 'kind': 'agent', 'prompt_template': 'x'},
             {'id': 'j', 'kind': 'join'}],
            [{'source': 'a', 'target': 'j', 'kind': 'join'}])
    with pytest.raises(ValueError):  # agent 缺 prompt
        defs.validate_definition([{'id': 'a', 'kind': 'agent'}], [])
    with pytest.raises(ValueError):  # 审批节点缺对象声明
        defs.validate_definition([{'id': 'ap', 'kind': 'approval'}], [])
    nodes, edges = defs.validate_definition(
        [{'id': 'a', 'kind': 'agent', 'prompt_template': 'x'},
         {'id': 'b', 'kind': 'join'}],
        [{'source': 'a', 'target': 'b', 'kind': 'join'},
         {'source': 'a', 'target': 'b'}])
    assert nodes[0]['prompt_template'] == 'x'


def test_publish_and_get_latest(db_conn, user_id):
    from utils import orchestration_defs as defs
    out = defs.publish_definition(
        f'p2-def-{uuid.uuid4().hex[:6]}', description=None,
        nodes=[{'id': 'solo', 'kind': 'agent', 'prompt_template': '做 {{input.task}}'}],
        edges=[], owner_user_id=user_id)
    d = defs.get_definition(out['id'])
    assert d['version'] == 1
    assert d['nodes'][0]['prompt_template'] == '做 {{input.task}}'
    assert defs.get_definition(out['id'], version=1)['id'] == out['id']
    assert defs.get_definition(out['id'], version=99) is None  # 不可变：无该版本


# ---------------------------------------------------------------------------
# 2. 线性 DAG：run 创建 → 推进 → 子会话执行 → 终态回调 → run completed
# ---------------------------------------------------------------------------

def test_linear_dag_end_to_end(db_conn, user_id, monkeypatch, tmp_path):
    from utils import orchestration_defs as defs, orchestration_engine as eng
    from utils.batch_engine import BatchWorker
    from unittest.mock import MagicMock
    import utils.batch_engine as batch_engine_mod

    root = tmp_path
    monkeypatch.setattr(batch_engine_mod, '_workspace_root', lambda: str(root))
    d = defs.publish_definition(
        f'p2-linear-{uuid.uuid4().hex[:6]}', description=None,
        owner_user_id=user_id,
        nodes=[
            {'id': 'extract', 'kind': 'agent', 'prompt_template': '抽取 {{input.file}}'},
            {'id': 'report', 'kind': 'agent', 'prompt_template': '基于 {{steps.extract}} 写报告'},
        ],
        edges=[{'source': 'extract', 'target': 'report', 'kind': 'advance'}],
    )

    run = eng.create_run(d['id'], user_id, run_input={'file': 'data.csv'})
    _clear_other_pending(db_conn, run['id'])
    assert run['status'] in ('pending', 'running')
    eng._advance_run(run['id'])
    run = eng.get_run(run['id'])
    step_map = {s['node_id']: s for s in run['steps']}
    assert step_map['extract']['status'] == 'running'
    assert step_map['report']['status'] == 'blocked'
    assert step_map['extract']['session_id']

    # 批 worker 认领编排子会话并执行（OpenCode mock 一轮完成）
    fake = MagicMock()
    fake.create_session.return_value = 'oc-' + uuid.uuid4().hex[:6]
    fake.list_agents.return_value = [{'name': 'build', 'mode': 'primary'}]
    fake.send_message.return_value = {'id': 'm'}
    fake.list_messages.return_value = [
        {'role': 'assistant', 'finished': True,
         'content': [{'type': 'text', 'text': '抽取完成'}]}]
    fake.get_messages.return_value = []
    monkeypatch.setattr(batch_engine_mod, 'opencode_client', fake)

    w = BatchWorker()
    claimed = w._claim_pending_sessions(limit=1)
    assert claimed and claimed[0]['orchestration_run_id'] == run['id']
    w._run_one(claimed[0])

    run = eng.get_run(run['id'])
    step_map = {s['node_id']: s for s in run['steps']}
    assert step_map['extract']['status'] == 'succeeded'
    assert step_map['report']['status'] == 'running'  # 下游被解锁

    # 第二步执行完 → run completed
    _clear_other_pending(db_conn, run['id'])
    claimed2 = w._claim_pending_sessions(limit=1)
    assert claimed2 and claimed2[0]['id'] == step_map['report']['session_id']
    w._run_one(claimed2[0])
    run = eng.get_run(run['id'])
    assert run['status'] == 'completed'
    assert all(s['status'] == 'succeeded' for s in run['steps'])


# ---------------------------------------------------------------------------
# 3. 条件分支与并行 fan-out / join
# ---------------------------------------------------------------------------

def _seed_def(db_conn, name, nodes, edges, user_id):
    """edges 直接进 publish_definition 走同一套校验（绕过校验的改库写法
    会让 join 语义等约束形同虚设）。"""
    from utils import orchestration_defs as defs
    d = defs.publish_definition(name, description=None, nodes=nodes,
                                edges=edges, owner_user_id=user_id)
    return d['id']


def test_conditional_branch(db_conn, user_id):
    from utils import orchestration_engine as eng
    did = _seed_def(
        db_conn, f'p2-cond-{uuid.uuid4().hex[:6]}',
        nodes=[
            {'id': 'split', 'kind': 'agent', 'prompt_template': '评估'},
            {'id': 'big', 'kind': 'agent', 'prompt_template': '大数据路径'},
            {'id': 'small', 'kind': 'agent', 'prompt_template': '小数据路径'},
        ],
        edges=[
            {'source': 'split', 'target': 'big', 'kind': 'advance',
             'condition': {'field': 'size', 'op': '>', 'value': 100}},
            {'source': 'split', 'target': 'small', 'kind': 'advance'},
        ], user_id=user_id)
    run = eng.create_run(did, user_id)
    # 预置 split 已成功且输出 size=200 → big 可达，small 不可达 → skipped
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE ai_orchestration_steps SET status='succeeded', "
            "output='{\"size\": 200, \"text\": \"ok\"}'::jsonb, finished_at=NOW() "
            "WHERE run_id=%s AND node_id='split'", (run['id'],))
    db_conn.commit()
    eng._advance_run(run['id'])
    run = eng.get_run(run['id'])
    step_map = {s['node_id']: s for s in run['steps']}
    assert step_map['big']['status'] == 'running'
    assert step_map['small']['status'] == 'skipped'


def test_parallel_fanout_join(db_conn, user_id):
    from utils import orchestration_engine as eng
    did = _seed_def(
        db_conn, f'p2-join-{uuid.uuid4().hex[:6]}',
        nodes=[
            {'id': 'a', 'kind': 'agent', 'prompt_template': 'A'},
            {'id': 'b', 'kind': 'agent', 'prompt_template': 'B'},
            {'id': 'merge', 'kind': 'join'},
        ],
        edges=[
            {'source': 'a', 'target': 'merge', 'kind': 'join'},
            {'source': 'b', 'target': 'merge', 'kind': 'join'},
        ], user_id=user_id)
    run = eng.create_run(did, user_id)
    eng._advance_run(run['id'])
    step_map = {s['node_id']: s for s in eng.get_run(run['id'])['steps']}
    assert step_map['a']['status'] == 'running'
    assert step_map['b']['status'] == 'running'
    # a 成功，b 仍跑 → join 不能提前通过
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_orchestration_steps SET status='succeeded', "
                    "finished_at=NOW() WHERE run_id=%s AND node_id='a'",
                    (run['id'],))
    db_conn.commit()
    eng._advance_run(run['id'])
    step_map = {s['node_id']: s for s in eng.get_run(run['id'])['steps']}
    assert step_map['merge']['status'] == 'blocked'
    # b 成功 → join 立即 succeeded
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_orchestration_steps SET status='succeeded', "
                    "finished_at=NOW() WHERE run_id=%s AND node_id='b'",
                    (run['id'],))
    db_conn.commit()
    eng._advance_run(run['id'])
    step_map = {s['node_id']: s for s in eng.get_run(run['id'])['steps']}
    assert step_map['merge']['status'] == 'succeeded'
    run = eng.get_run(run['id'])
    assert run['status'] == 'completed'  # 全部终态成功


# ---------------------------------------------------------------------------
# 4. 审批（Phase B）：等待 → inbox 投影 → 通过/拒绝/超时
# ---------------------------------------------------------------------------

def _approval_def(db_conn, user_id):
    return _seed_def(
        db_conn, f'p2-appr-{uuid.uuid4().hex[:6]}',
        nodes=[
            {'id': 'prepare', 'kind': 'agent', 'prompt_template': '准备'},
            {'id': 'gate', 'kind': 'approval',
             'approval': {'requested_roles': ['admin'], 'risk_level': 'high'}},
            {'id': 'publish', 'kind': 'agent', 'prompt_template': '发布'},
        ],
        edges=[
            {'source': 'prepare', 'target': 'gate', 'kind': 'advance'},
            {'source': 'gate', 'target': 'publish', 'kind': 'advance'},
        ], user_id=user_id)


def test_approval_flow_approve(db_conn, user_id):
    from utils import orchestration_engine as eng, approval_repo
    did = _approval_def(db_conn, user_id)
    run = eng.create_run(did, user_id)
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_orchestration_steps SET status='succeeded', "
                    "finished_at=NOW() WHERE run_id=%s AND node_id='prepare'",
                    (run['id'],))
    db_conn.commit()
    eng._advance_run(run['id'])
    run = eng.get_run(run['id'])
    assert run['status'] == 'waiting_approval'
    step_map = {s['node_id']: s for s in run['steps']}
    assert step_map['gate']['status'] == 'waiting_approval'
    # inbox 投影（admin 可见）
    with db_conn.cursor() as cur:
        cur.execute("SELECT id FROM ai_approval_requests WHERE run_id=%s",
                    (run['id'],))
        apr_id = cur.fetchone()[0]
    inbox = approval_repo.get_pending_for_inbox(
        {'username': 'nope', 'role': 'admin'})
    assert any(i['kind'] == 'ai_approval' and i['approvalId'] == apr_id
               for i in inbox)
    # 通过 → publish 解锁
    res = approval_repo.decide(apr_id, 'approved', user_id, comment='ok')
    assert res['status'] == 'approved'
    run = eng.get_run(run['id'])
    step_map = {s['node_id']: s for s in run['steps']}
    assert step_map['gate']['status'] == 'succeeded'
    assert step_map['publish']['status'] == 'running'
    # 幂等：重复决策返回 duplicate
    res2 = approval_repo.decide(apr_id, 'approved', user_id)
    assert res2.get('duplicate') is True


def test_approval_reject_fails_run(db_conn, user_id):
    from utils import orchestration_engine as eng, approval_repo
    did = _approval_def(db_conn, user_id)
    run = eng.create_run(did, user_id)
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_orchestration_steps SET status='succeeded', "
                    "finished_at=NOW() WHERE run_id=%s AND node_id='prepare'",
                    (run['id'],))
    db_conn.commit()
    eng._advance_run(run['id'])
    with db_conn.cursor() as cur:
        cur.execute("SELECT id FROM ai_approval_requests WHERE run_id=%s",
                    (run['id'],))
        apr_id = cur.fetchone()[0]
    approval_repo.decide(apr_id, 'rejected', user_id, comment='不允许发布')
    run = eng.get_run(run['id'])
    step_map = {s['node_id']: s for s in run['steps']}
    assert step_map['gate']['status'] == 'failed'
    assert step_map['publish']['status'] in ('blocked', 'skipped')
    assert run['status'] in ('failed', 'partial')


def test_approval_timeout_expires(db_conn, user_id):
    from utils import orchestration_engine as eng, approval_repo
    did = _approval_def(db_conn, user_id)
    run = eng.create_run(did, user_id)
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_orchestration_steps SET status='succeeded', "
                    "finished_at=NOW() WHERE run_id=%s AND node_id='prepare'",
                    (run['id'],))
        # 直接造一个已过期的 pending 审批
        cur.execute(
            "INSERT INTO ai_approval_requests (id, run_id, step_id, status, "
            "  expires_at) VALUES (%s, %s, %s || ':gate', 'pending', "
            "  NOW() - interval '1 minute')",
            ('apr-exp-' + uuid.uuid4().hex[:6], run['id'], run['id']))
    db_conn.commit()
    n = approval_repo.expire_overdue()
    assert n >= 1
    run = eng.get_run(run['id'])
    step_map = {s['node_id']: s for s in run['steps']}
    assert step_map['gate']['status'] == 'failed'  # 超时等同拒绝


# ---------------------------------------------------------------------------
# 5. Artifact Store（Phase D）
# ---------------------------------------------------------------------------

def test_artifact_put_dedup_and_auth(user_id, tmp_path):
    from utils import artifact_store
    f1 = tmp_path / 'out.csv'
    f1.write_text('a,b\n1,2')
    a1 = artifact_store.put_file(str(f1), owner_user_id=user_id, name='out.csv',
                                 run_id='run-x', step_id='run-x:s')
    a2 = artifact_store.put_file(str(f1), owner_user_id=user_id, name='out.csv')
    assert a1 and a1 == a2  # (sha256, name) 去重
    art = artifact_store.get_artifact(a1)
    assert art['sha256'] and art['name'] == 'out.csv'
    assert os.path.isfile(artifact_store.artifact_path(art))


def test_ingest_session_outputs(user_id, tmp_path):
    from utils import artifact_store
    ws = tmp_path / 'ws'
    (ws / 'outputs').mkdir(parents=True)
    (ws / 'outputs' / 'result.md').write_text('# 结果')
    ids = artifact_store.ingest_session_outputs('sess-x', str(ws),
                                                run_id='run-y',
                                                owner_user_id=user_id)
    assert len(ids) == 1


# ---------------------------------------------------------------------------
# 6. Runtime Adapter（Phase D）
# ---------------------------------------------------------------------------

def test_runtime_adapter_default_and_capabilities():
    from utils import runtime
    rt = runtime.get_runtime()
    assert rt.kind == 'opencode_local'
    caps = rt.capabilities()
    assert caps['checkpoint'] is True and caps['network_isolation'] is False
    with pytest.raises(runtime.RuntimeCapabilityError):
        import os as _os
        _os.environ['AI_AGENT_RUNTIME'] = 'docker'
        try:
            runtime.get_runtime.__globals__['_default'] and None
            import importlib
            runtime._default = None  # 重置默认
            runtime.get_runtime()
        finally:
            _os.environ['AI_AGENT_RUNTIME'] = 'opencode_local'
            runtime._default = None


def test_opencode_local_wraps_client(monkeypatch):
    from utils.runtime import OpenCodeLocalRuntime
    from unittest.mock import MagicMock
    rt = OpenCodeLocalRuntime()
    fake_client = MagicMock()
    fake_client.create_session.return_value = 'oc-rt'
    monkeypatch.setattr(rt, '_client', lambda: fake_client)
    assert rt.create_session('/tmp/x') == 'oc-rt'
