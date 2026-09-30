"""Agent 动作账本与到位门禁测试(设计:docs/design/AI子任务动作账本与到位门禁设计.md)。

纯函数部分(extract/validate/message)不触库;落账/登记/核对部分跑真实共享
开发库(与 test_ai_batch_admin_repo 同约定):fixture 建数据、收尾全删,
迁移幂等可重复执行。
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from db import get_db                                    # noqa: E402
from utils import agent_ledger                           # noqa: E402
from utils import action_check_extractor                 # noqa: E402

# migrations 目录下文件名以数字开头,不能直接 import;走 app.py boot 同款动态加载。
import importlib.util                                    # noqa: E402


def _load_migration():
    path = os.path.join(os.path.dirname(__file__), '..',
                        'migrations', '2026_09_20_agent_action_gate.py')
    spec = importlib.util.spec_from_file_location('_gate_migration_test', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope='module', autouse=True)
def _migration():
    """幂等迁移。xdist 多 worker 并发收集时用 advisory lock 串行化,
    避免与其他 worker 的事务在 DDL 锁上互相卡死。"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT pg_advisory_lock(920200)')
        try:
            conn.commit()
            _load_migration().run()
        finally:
            cur.execute('SELECT pg_advisory_unlock(920200)')
            conn.commit()


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------

def _msg(parts):
    return {'info': {'id': 'm1', 'role': 'assistant'}, 'parts': parts}


def test_extract_tool_parts_basic():
    msgs = [_msg([
        {'id': 'p1', 'type': 'tool', 'tool': 'bash',
         'state': {'status': 'completed', 'input': {'command': 'git clone https://x/y.git'}}},
        {'id': 'p2', 'type': 'tool', 'tool': 'read',
         'state': {'status': 'completed', 'input': {'path': 'docs/knowledge/a.md'}}},
        {'id': 'p3', 'type': 'text', 'text': 'not a tool'},
        {'type': 'tool', 'tool': 'no-id'},   # 缺 id,跳过
    ])]
    rows = agent_ledger.extract_tool_parts(msgs)
    by_id = {r[0]: r for r in rows}
    assert set(by_id) == {'p1', 'p2'}
    assert by_id['p1'][1] == 'bash'
    assert 'git clone https://x/y.git' in by_id['p1'][2]
    assert by_id['p1'][3] == 'completed'
    assert 'docs/knowledge/a.md' in by_id['p2'][2]


def test_extract_dedupes_by_part_id_keeping_latest_state():
    msgs = [
        _msg([{'id': 'p1', 'type': 'tool', 'tool': 'bash',
               'state': {'status': 'pending', 'input': {'command': 'run.sh'}}}]),
        _msg([{'id': 'p1', 'type': 'tool', 'tool': 'bash',
               'state': {'status': 'completed', 'input': {'command': 'run.sh'}}}]),
    ]
    rows = agent_ledger.extract_tool_parts(msgs)
    assert len(rows) == 1
    assert rows[0][3] == 'completed'


def test_args_to_text_shapes():
    assert agent_ledger.args_to_text(None) == ''
    assert agent_ledger.args_to_text('plain') == 'plain'
    assert agent_ledger.args_to_text({'path': 'a.md'}) == 'path=a.md'
    assert 'k=1' in agent_ledger.args_to_text({'k': 1})


def test_validate_checks_rejects_bad_regex():
    with pytest.raises(ValueError, match='合法正则'):
        agent_ledger.validate_checks(
            [{'name': 'x', 'tool': 'bash', 'args_pattern': '([bad'}])


def test_validate_checks_rejects_duplicate_names():
    with pytest.raises(ValueError, match='重复'):
        agent_ledger.validate_checks([
            {'name': 'same', 'tool': 'bash', 'args_pattern': 'a'},
            {'name': 'same', 'tool': 'read', 'args_pattern': 'b'},
        ])


def test_validate_checks_normalizes():
    out = agent_ledger.validate_checks([
        {'name': '克隆仓库', 'tool': 'bash', 'args_pattern': 'git clone'},
    ])
    # F2：规范化输出保留 apply_to（未声明为 None）——此前被剥离导致
    # 定向门禁在内部路径整体失效。
    assert out == [{'name': '克隆仓库', 'tool': 'bash', 'args_pattern': 'git clone',
                    'require_state': 'completed', 'min_count': 1, 'scope': 'tree',
                    'check_type': 'tool', 'effect_spec': None,
                    'subagents': None, 'apply_to': None}]


def test_validate_checks_normalizes_subagents():
    out = agent_ledger.validate_checks([
        {'name': '读取知识库', 'tool': 'read', 'args_pattern': 'k\.md',
         'subagents': ['general', 'explore']},
    ])
    assert out[0]['subagents'] == ['general', 'explore']
    with pytest.raises(ValueError, match='subagents'):
        agent_ledger.validate_checks([
            {'name': 'x', 'tool': 'read', 'args_pattern': 'a', 'subagents': 'general'}])


def test_check_applies_to_child_conditions():
    from utils.agent_ledger import check_applies_to_child as applies
    assert applies({}, 0, 'a.txt') is True                      # 无条件 → 全部生效
    check = {'apply_to': {'batch_seq': [0, 2]}}
    assert applies(check, 0, 'a.txt') is True
    assert applies(check, 1, 'a.txt') is False
    check = {'apply_to': {'input_file_glob': 'report-*.docx'}}
    assert applies(check, 0, 'batch-staging/x/report-3.docx') is True
    assert applies(check, 0, 'batch-staging/x/other.docx') is False


def test_gate_failure_message_lists_missing_items():
    msg = agent_ledger.gate_failure_message({
        'status': 'failed',
        'results': [
            {'name': '克隆仓库', 'tool': 'bash', 'args_pattern': 'git clone',
             'min_count': 1, 'evidence': 0, 'status': 'failed'},
            {'name': '读知识库', 'tool': 'read', 'args_pattern': 'a.md',
             'min_count': 1, 'evidence': 2, 'status': 'passed'},
        ]})
    assert msg.startswith('action_gate: ')
    assert '克隆仓库' in msg and '命中 0/1' in msg
    assert '读知识库' not in msg


# ---------------------------------------------------------------------------
# 真实库集成:落账 / 登记 / 门禁 / dry-run
# ---------------------------------------------------------------------------

@pytest.fixture
def gate_fixture():
    """一个批任务 + 一个子会话(带 oc id)+ 两个子代理;收尾全删。"""
    suffix = uuid.uuid4().hex[:8]
    uid, bid, sid = (f'u-gate-{suffix}', f'b-gate-{suffix}', f's-gate-{suffix}')
    oc_sid = f'oc-gate-{suffix}'
    child1, child2 = f'oc-child1-{suffix}', f'oc-child2-{suffix}'
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s,%s,'x',%s,'developer')", (uid, 'gate-' + uid, uid))
            cur.execute("INSERT INTO ai_chat_batches (id,user_id,name,prompt,status,total) "
                        "VALUES (%s,%s,'g','p','running',1)", (bid, uid))
            cur.execute("INSERT INTO ai_chat_sessions "
                        "(id,user_id,status,batch_id,batch_seq,batch_input_file,opencode_session_id) "
                        "VALUES (%s,%s,'running',%s,0,'uploads/a.txt',%s)",
                        (sid, uid, bid, oc_sid))
            for c in (child1, child2):
                cur.execute("INSERT INTO ai_chat_subtasks "
                            "(id, root_session_id, agent, description, status) "
                            "VALUES (%s,%s,'worker','子任务','completed')", (c, sid))
    yield {'uid': uid, 'bid': bid, 'sid': sid, 'oc_sid': oc_sid,
           'child1': child1, 'child2': child2}
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_batches WHERE id=%s", (bid,))
            cur.execute("DELETE FROM users WHERE id=%s", (uid,))
        conn.commit()
    # ai_chat_sessions/ai_chat_subtasks 随批任务 FK CASCADE 级联删除;
    # agent_tool_calls.subtask_id 也 CASCADE,期望行按 scope_id 手工清。
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM action_expectations WHERE scope_id=%s", (sid,))
            cur.execute("DELETE FROM agent_tool_calls WHERE root_session_id=%s",
                        (sid,))
        conn.commit()


def _tool_part(pid, tool, command, status='completed'):
    return {'id': pid, 'type': 'tool', 'tool': tool,
            'state': {'status': status, 'input': {'command': command}}}


def test_record_messages_idempotent_and_state_transition(gate_fixture):
    f = gate_fixture
    msgs = [_msg([_tool_part('p1', 'bash', 'git clone https://x/y.git',
                             status='pending')])]
    assert agent_ledger.record_messages(f['oc_sid'], msgs,
                                        root_session_id=f['sid'])
    # 同 part 状态跃迁 → 更新而非新增
    msgs2 = [_msg([_tool_part('p1', 'bash', 'git clone https://x/y.git')])]
    assert agent_ledger.record_messages(f['oc_sid'], msgs2,
                                        root_session_id=f['sid'])
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM agent_tool_calls "
                        "WHERE oc_session_id=%s", (f['oc_sid'],))
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT state FROM agent_tool_calls "
                        "WHERE oc_session_id=%s AND part_id='p1'", (f['oc_sid'],))
            assert cur.fetchone()[0] == 'completed'


def test_gate_tree_scope_passes_on_subagent_action(gate_fixture):
    """期望登记在子会话,动作发生在子代理(tree 作用域)→ 门禁放行。"""
    f = gate_fixture
    agent_ledger.register_session_expectations(f['sid'], [
        {'name': '克隆仓库', 'tool': 'bash', 'args_pattern': 'git clone\\s+\\S*x/y'},
    ])
    # 动作由 child1(子代理)执行,根会话自己没有任何 bash 调用
    assert agent_ledger.record_messages(
        f['oc_sid'], [_msg([_tool_part('r1', 'read', 'path=docs/a.md')])],
        root_session_id=f['sid'])
    assert agent_ledger.record_messages(
        f['child1'], [_msg([_tool_part('c1', 'bash',
                                       'git clone https://github.com/acme/x/y.git')])],
        root_session_id=f['sid'], subtask_id=f['child1'])
    res = agent_ledger.check_session_gate(f['sid'])
    assert res['status'] == 'passed'
    assert res['results'][0]['evidence'] == 1


def test_gate_fails_when_action_missing(gate_fixture):
    f = gate_fixture
    agent_ledger.register_session_expectations(f['sid'], [
        {'name': '克隆仓库', 'tool': 'bash', 'args_pattern': 'git clone\\s+\\S*acme/y'},
        {'name': '执行对账脚本', 'tool': 'bash',
         'args_pattern': 'scripts/reconcile\\.py', 'min_count': 1},
    ])
    # 只有 git clone 且指向别的仓库;state=error 的不算到位
    agent_ledger.record_messages(
        f['oc_sid'], [_msg([
            _tool_part('r1', 'bash', 'git clone https://github.com/other/x.git'),
            _tool_part('r2', 'bash', 'python scripts/reconcile.py', status='error'),
        ])], root_session_id=f['sid'])
    res = agent_ledger.check_session_gate(f['sid'])
    assert res['status'] == 'failed'
    assert [r['name'] for r in res['results'] if r['status'] == 'failed'] == \
        ['克隆仓库', '执行对账脚本']
    msg = agent_ledger.gate_failure_message(res)
    assert msg.startswith('action_gate: ')
    assert '克隆仓库' in msg


def test_gate_passed_when_no_expectations(gate_fixture):
    res = agent_ledger.check_session_gate(gate_fixture['sid'])
    assert res == {'status': 'passed', 'results': []}


def test_gate_session_scope_ignores_subagent(gate_fixture):
    f = gate_fixture
    agent_ledger.register_session_expectations(f['sid'], [
        {'name': '根会话自己克隆', 'tool': 'bash', 'args_pattern': 'git clone',
         'scope': 'session'},
    ])
    agent_ledger.record_messages(
        f['child1'], [_msg([_tool_part('c1', 'bash', 'git clone x')])],
        root_session_id=f['sid'], subtask_id=f['child1'])
    res = agent_ledger.check_session_gate(f['sid'])
    assert res['status'] == 'failed'
    assert res['results'][0]['evidence'] == 0


def test_gate_reregistration_is_idempotent(gate_fixture):
    f = gate_fixture
    checks = [{'name': '克隆仓库', 'tool': 'bash', 'args_pattern': 'git clone'}]
    agent_ledger.register_session_expectations(f['sid'], checks)
    agent_ledger.register_session_expectations(f['sid'], checks)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM action_expectations WHERE scope_id=%s",
                        (f['sid'],))
            assert cur.fetchone()[0] == 1


def test_dry_run_counts_and_samples(gate_fixture):
    f = gate_fixture
    agent_ledger.record_messages(
        f['child1'], [_msg([_tool_part('c1', 'bash', 'git clone https://x/y.git')])],
        root_session_id=f['sid'], subtask_id=f['child1'])
    out = agent_ledger.count_tree_tool_calls(f['sid'], 'bash', 'git clone')
    assert out['evidence'] == 1
    assert 'git clone' in out['samples'][0]['args']


# ---------------------------------------------------------------------------
# M2 交互态采集(record_state)与 M3 效果断言原语
# ---------------------------------------------------------------------------

def test_record_state_walks_root_and_subtasks(gate_fixture):
    """交互态:root 的 parts_by_id + 每个子代理自己的 parts_by_id 各落一账。"""
    f = gate_fixture
    state = {
        'parts_by_id': {'r1': _tool_part('r1', 'read', 'path=docs/k.md')},
        'subtasks': {
            f['child1']: {'parts_by_id': {'c1': _tool_part('c1', 'bash', 'npm test')}},
        },
    }
    assert agent_ledger.record_state(f['sid'], f['oc_sid'], state)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM agent_tool_calls WHERE root_session_id=%s",
                        (f['sid'],))
            assert cur.fetchone()[0] == 2
            cur.execute("SELECT COUNT(*) FROM agent_tool_calls "
                        "WHERE oc_session_id=%s AND subtask_id=%s",
                        (f['child1'], f['child1']))
            assert cur.fetchone()[0] == 1


def test_file_effect_check_counts_workspace_glob(gate_fixture, tmp_path):
    """file 原语:会话工作区内 glob 命中 → passed;缺失 → failed。"""
    f = gate_fixture
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE ai_chat_sessions SET workspace_path=%s WHERE id=%s",
                        (str(tmp_path), f['sid']))
        conn.commit()
    (tmp_path / 'outputs').mkdir()
    (tmp_path / 'outputs' / 'summary.xlsx').write_text('x', encoding='utf-8')
    agent_ledger.register_session_expectations(f['sid'], [
        {'name': '产出汇总表', 'check_type': 'file',
         'effect_spec': {'path': 'outputs/*.xlsx'}},
    ])
    res = agent_ledger.check_session_gate(f['sid'])
    assert res['status'] == 'passed'
    assert res['results'][0]['evidence'] == 1
    # 删掉产物 → failed
    (tmp_path / 'outputs' / 'summary.xlsx').unlink()
    res = agent_ledger.check_session_gate(f['sid'])
    assert res['status'] == 'failed'


def test_db_record_effect_check(gate_fixture):
    """db_record 原语:dynamic_data 按集合+Mongo 过滤命中 → passed。"""
    f = gate_fixture
    col = 'gate-test-' + f['sid'][:8]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO dynamic_data (id, collection, data) "
                        "VALUES (%s, %s, %s)",
                        ('dd-' + f['sid'][:8], col, '{"status": "done"}'))
        conn.commit()
    try:
        agent_ledger.register_session_expectations(f['sid'], [
            {'name': '回写扫描结果', 'check_type': 'db_record',
             'effect_spec': {'collection': col, 'filter': {'status': 'done'}}},
        ])
        res = agent_ledger.check_session_gate(f['sid'])
        assert res['status'] == 'passed'
        assert res['results'][0]['evidence'] == 1
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (col,))
            conn.commit()


def test_validate_checks_rejects_bad_effect_path():
    with pytest.raises(ValueError, match='相对路径'):
        agent_ledger.validate_checks([
            {'name': 'x', 'check_type': 'file',
             'effect_spec': {'path': '../../etc/passwd'}}])


def test_finalize_interactive_turn_records_and_checks(gate_fixture):
    f = gate_fixture
    agent_ledger.register_session_expectations(f['sid'], [
        {'name': '执行对账脚本', 'tool': 'bash',
         'args_pattern': 'scripts/reconcile\\.py'},
    ])
    state = {'parts_by_id': {
        'r1': _tool_part('r1', 'bash', 'python scripts/reconcile.py'),
    }, 'subtasks': {}}
    gate = agent_ledger.finalize_interactive_turn(f['sid'], f['oc_sid'], state)
    assert gate['status'] == 'passed'
    assert gate['results'][0]['evidence'] == 1


def test_wait_subtasks_drained_polls_until_converged(gate_fixture, monkeypatch):
    """有 running 子代理时轮询等待,收敛后返回 True。"""
    f = gate_fixture
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE ai_chat_subtasks SET status='running' "
                        "WHERE id=%s", (f['child1'],))
    conn.commit()
    calls = {'n': 0}

    def flip_when_polled(seconds):
        calls['n'] += 1
        if calls['n'] >= 1:  # 第一次 sleep 后把子代理翻转为已完成
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE ai_chat_subtasks SET status='completed' "
                                "WHERE id=%s", (f['child1'],))
            conn.commit()

    monkeypatch.setattr('utils.agent_ledger.time.sleep', flip_when_polled)
    assert agent_ledger.wait_subtasks_drained(f['sid'], timeout_sec=10,
                                              poll_sec=0.01) is True


def test_wait_subtasks_drained_times_out(gate_fixture, monkeypatch):
    f = gate_fixture
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE ai_chat_subtasks SET status='running' "
                        "WHERE id=%s", (f['child2'],))
    conn.commit()
    monkeypatch.setattr('utils.agent_ledger.time.sleep', lambda s: None)
    assert agent_ledger.wait_subtasks_drained(f['sid'], timeout_sec=0) is False


# ---------------------------------------------------------------------------
# M1.5 提炼器(LLM mock,不发真实请求)
# ---------------------------------------------------------------------------

def _patch_llm(monkeypatch, content):
    import utils.action_check_extractor as ex
    class _Resp:
        status_code = 200
        def json(self):
            return {'choices': [{'message': {'content': content}}]}
    monkeypatch.setattr(ex, 'get_ai_settings', lambda: {
        'enabled': True, 'apiKey': 'k', 'endpoint': 'http://x',
        'model': 'm', 'timeout': 5, 'maxTokens': 1024})
    monkeypatch.setattr(ex, 'get_http_session', lambda: type('S', (), {
        'post': staticmethod(lambda *a, **k: _Resp())})())


def test_extractor_parses_fenced_array(monkeypatch):
    content = '```json\n[{"name": "克隆目标仓库", "tool": "bash", "args_pattern": "git clone\\\\s+\\\\S*y"}]\n```'
    _patch_llm(monkeypatch, content)
    checks = action_check_extractor.extract_action_checks('克隆 acme/y 并跑测试')
    assert checks[0]['name'] == '克隆目标仓库'
    assert checks[0]['min_count'] == 1


def test_extractor_rejects_invalid_regex(monkeypatch):
    _patch_llm(monkeypatch, '[{"name": "x", "tool": "bash", "args_pattern": "([bad"}]')
    with pytest.raises(ValueError):
        action_check_extractor.extract_action_checks('task')


def test_extractor_requires_ai_enabled(monkeypatch):
    monkeypatch.setattr(action_check_extractor, 'get_ai_settings', lambda: {
        'enabled': False, 'apiKey': '', 'endpoint': '', 'model': '',
        'timeout': 5, 'maxTokens': 1024})
    with pytest.raises(RuntimeError, match='未启用'):
        action_check_extractor.extract_action_checks('task')


def test_gate_subagent_filter_only_counts_target_agent(gate_fixture):
    """subagents 定向:只有指定子代理的动作参与核对,不相关子代理不误报。"""
    f = gate_fixture
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE ai_chat_subtasks SET agent='general' WHERE id=%s",
                        (f['child1'],))
            cur.execute("UPDATE ai_chat_subtasks SET agent='explore' WHERE id=%s",
                        (f['child2'],))
    conn.commit()
    agent_ledger.register_session_expectations(f['sid'], [
        {'name': 'general 读取输入文件', 'tool': 'read',
         'args_pattern': 'one\.txt', 'subagents': ['general']},
    ])
    # explore 读同文件 + general 没读 → 不满足(general 未参与)
    assert agent_ledger.record_messages(
        f['child2'], [_msg([_tool_part('c2', 'read', 'path=x/one.txt')])],
        root_session_id=f['sid'], subtask_id=f['child2'], agent_name='explore')
    res = agent_ledger.check_session_gate(f['sid'])
    assert res['status'] == 'failed'
    assert res['results'][0]['evidence'] == 0
    # general 也读了 → 满足
    assert agent_ledger.record_messages(
        f['child1'], [_msg([_tool_part('c1', 'read', 'path=x/one.txt')])],
        root_session_id=f['sid'], subtask_id=f['child1'], agent_name='general')
    res = agent_ledger.check_session_gate(f['sid'])
    assert res['status'] == 'passed'
    assert res['results'][0]['evidence'] == 1


def test_record_messages_stores_agent_name(gate_fixture):
    f = gate_fixture
    agent_ledger.record_messages(
        f['child1'], [_msg([_tool_part('c1', 'bash', 'ls')])],
        root_session_id=f['sid'], subtask_id=f['child1'], agent_name='general')
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT agent FROM agent_tool_calls WHERE oc_session_id=%s",
                        (f['child1'],))
            assert cur.fetchone()[0] == 'general'


def test_sync_batch_expectations_replaces_running_keeps_terminal(gate_fixture):
    """编辑批任务门禁后同步:running 子任务替换为新期望;
    已完成子任务的历史核对结果保持原样(审计不重写)。"""
    f = gate_fixture
    sid2 = f['sid'] + '-done'
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO ai_chat_sessions "
                        "(id,user_id,status,batch_id,batch_seq,batch_input_file) "
                        "VALUES (%s,%s,'completed',%s,1,'x.txt')", (sid2, f['uid'], f['bid']))
            cur.execute("INSERT INTO action_expectations "
                        "(scope_type,scope_id,name,tool,args_pattern,source,last_status) "
                        "VALUES ('session',%s,'老检查','bash','old','batch','failed')", (sid2,))
        conn.commit()
    try:
        agent_ledger.register_session_expectations(f['sid'], [
            {'name': '旧期望', 'tool': 'bash', 'args_pattern': 'old\.sh'}])
        n = agent_ledger.sync_batch_expectations(f['bid'], [
            {'name': '新期望', 'tool': 'read', 'args_pattern': 'new\.md'}])
        assert n == 1  # 仅 running 子任务被同步
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT name FROM action_expectations "
                            "WHERE scope_id=%s AND source='batch'", (f['sid'],))
                assert [r[0] for r in cur.fetchall()] == ['新期望']
                cur.execute("SELECT name, last_status FROM action_expectations "
                            "WHERE scope_id=%s", (sid2,))
                old = cur.fetchone()
                assert old[0] == '老检查' and old[1] == 'failed'
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM action_expectations WHERE scope_id=%s", (sid2,))
            conn.commit()


def test_gate_retry_batch_flag_overrides_env(gate_fixture, monkeypatch):
    """批级 gate_retry 开关优先于全局 env:批开启时即使 env=0 也会修正一次;
    批显式关闭时即使 env 开启也不修正。"""
    import os
    from utils.batch_engine import BatchWorker
    f = gate_fixture
    monkeypatch.setenv('AI_BATCH_GATE_RETRY', '0')
    # 给 running 子任务补 oc 会话与期望
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE ai_chat_sessions SET opencode_session_id=%s "
                        "WHERE id=%s", (f['oc_sid'], f['sid']))
    conn.commit()
    agent_ledger.register_session_expectations(f['sid'], [
        {'name': '读输入文件', 'tool': 'read', 'args_pattern': 'one\.txt'}])
    gate = {'status': 'failed', 'results': [
        {'name': '读输入文件', 'tool': 'read', 'args_pattern': 'one\.txt',
         'evidence': 0, 'min_count': 1, 'status': 'failed'}]}

    w = BatchWorker()
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE ai_chat_batches SET gate_retry=TRUE WHERE id=%s", (f['bid'],))
    conn.commit()
    assert w._maybe_gate_retry(f['sid'], gate, batch_id=f['bid']) is True
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, continue_prompt FROM ai_chat_sessions WHERE id=%s",
                        (f['sid'],))
            st, prompt = cur.fetchone()
    assert st == 'pending' and 'action_gate' in prompt

    # 预算(1)已用掉 → 第二次不再修正
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE ai_chat_sessions SET status='running' WHERE id=%s", (f['sid'],))
    conn.commit()
    assert w._maybe_gate_retry(f['sid'], gate, batch_id=f['bid']) is False


def test_gate_retry_batch_flag_off_blocks_env(gate_fixture, monkeypatch):
    import os
    from utils.batch_engine import BatchWorker
    f = gate_fixture
    monkeypatch.setenv('AI_BATCH_GATE_RETRY', '3')
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE ai_chat_batches SET gate_retry=FALSE "
                        "WHERE id=%s", (f['bid'],))
            cur.execute("UPDATE ai_chat_sessions SET batch_id=%s, status='running' "
                        "WHERE id=%s", (f['sid'], f['bid']))
    conn.commit()
    gate = {'status': 'failed', 'results': [
        {'name': 'x', 'tool': 'read', 'args_pattern': 'a',
         'evidence': 0, 'min_count': 1, 'status': 'failed'}]}
    w = BatchWorker()
    assert w._maybe_gate_retry(f['sid'], gate, batch_id=f['bid']) is False
