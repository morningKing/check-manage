# -*- coding: utf-8 -*-
"""SkillOpt 任务性能分析（spec 2026-10-09）：覆盖切分/任务指标/诊断规则/端点。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.perf_analysis import coverage_split


class TestCoverageSplit:
    def test_disjoint_no_overlap(self):
        # 墙钟 0..1000；模型 [0,400]，子代理 [600,1000]（与模型不重叠）
        r = coverage_split(0, 1000, [(0, 400)], [(600, 1000)])
        assert r == {'wallMs': 1000, 'modelMs': 400, 'subagentWaitMs': 400, 'idleMs': 200}

    def test_subagent_under_model_is_wait_zero(self):
        # 子代理区间完全落在模型活跃内（父轮次在等 task 返回）→ 等待为 0
        r = coverage_split(0, 1000, [(0, 1000)], [(200, 800)])
        assert r['subagentWaitMs'] == 0 and r['modelMs'] == 1000 and r['idleMs'] == 0

    def test_partial_overlap_counts_only_exposed_wait(self):
        # 子代理 [300,900] 与模型 [0,500] 重叠 200 → 等待只算露出的 [500,900]=400
        r = coverage_split(0, 1000, [(0, 500)], [(300, 900)])
        assert r['subagentWaitMs'] == 400 and r['idleMs'] == 100

    def test_overlapping_model_turns_unioned_not_summed(self):
        # 两轮模型区间重叠 [200,400]：并集 [0,600]=600，不是 400+400
        r = coverage_split(0, 1000, [(0, 400), (200, 600)], [])
        assert r['modelMs'] == 600 and r['idleMs'] == 400

    def test_empty_intervals_all_idle(self):
        r = coverage_split(0, 500, [], [])
        assert r == {'wallMs': 500, 'modelMs': 0, 'subagentWaitMs': 0, 'idleMs': 500}

    def test_intervals_outside_wall_clamped(self):
        r = coverage_split(100, 500, [(0, 300)], [(400, 900)])
        assert r['modelMs'] == 200 and r['subagentWaitMs'] == 100 and r['wallMs'] == 400

    def test_sum_equals_wall(self):
        r = coverage_split(0, 9999, [(0, 3000), (2500, 7000)], [(1000, 2000), (6500, 9999)])
        assert r['modelMs'] + r['subagentWaitMs'] + r['idleMs'] == r['wallMs']


# 追加到 server/tests/test_perf_analysis.py
import json, uuid
import pytest

from utils.perf_analysis import (load_attempt_metrics, list_definition_tasks,
                                 list_slow_tasks, definition_overview)


@pytest.fixture
def user_id(db_conn):
    uid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, 'x', 'PF', 'developer')", (uid, f'pf_{uid[:8]}'))
    db_conn.commit()
    yield uid
    db_conn.rollback()      # 用例中途失败可能留下中止事务，先复位再清扫
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_chat_sessions WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM ai_chat_batches WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
        # agent_tool_calls 无 FK 不随会话级联，且无用户维度，按本套件前缀回收
        cur.execute("DELETE FROM agent_tool_calls WHERE oc_session_id LIKE 'oc-x-%'")
    db_conn.commit()


def _seed_perf(db_conn, user_id, *, source_type='batch', oc_sid=None,
               model_turns=None, subtasks=None, reuse=None):
    """种子 批+会话+attempt+manifests(+消息+子代理)。时间用 NOW() 偏移秒。"""
    bid, sid, aid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    oc = oc_sid or ('oc-' + uuid.uuid4().hex[:8])
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total, subagent_reuse) "
            "VALUES (%s,%s,'pf','p',1,%s)",
            (bid, user_id, json.dumps(reuse) if reuse is not None else None))
        cur.execute(
            "INSERT INTO ai_chat_sessions (id,user_id,status,batch_id,batch_seq,"
            " opencode_session_id,workspace_path) "
            "VALUES (%s,%s,'completed',%s,0,%s,'C:\\tmp\\pf')", (sid, user_id, bid, oc))
        cur.execute(
            "INSERT INTO ai_execution_attempts (id, session_id, source_type, source_id,"
            " status, started_at, finished_at) "
            "VALUES (%s,%s,%s,%s,'completed', NOW() - interval '600 seconds',"
            "        NOW() - interval '100 seconds')", (aid, sid, source_type, bid))
        cur.execute(
            "INSERT INTO ai_execution_manifests (id, attempt_id, kind, name, source,"
            " path, content_hash, injected) "
            "VALUES (%s,%s,'skill','stock-analysis','session',"
            " 'C:\\tmp\\pf\\SKILL.md','h1',true)",
            ('man_' + aid, aid))
        for i, (dur, tin, tout, offs) in enumerate(model_turns or []):
            cur.execute(
                "INSERT INTO ai_chat_messages (id, session_id, role, content, meta,"
                " created_at) VALUES (%s,%s,'assistant',%s::jsonb,%s::jsonb,"
                " NOW() - interval '600 seconds' + interval '%s seconds')",
                (f'{aid}-m{i}', sid,
                 json.dumps([{'type': 'text', 'text': f'轮次{i}结论'}]),
                 json.dumps({'durationMs': dur, 'tokensInput': tin,
                             'tokensOutput': tout}), offs))
        for i, (agent, start_off, end_off) in enumerate(subtasks or []):
            cur.execute(
                "INSERT INTO ai_chat_subtasks (id, root_session_id, agent, description,"
                " status, created_at, completed_at) VALUES (%s,%s,%s,'d','completed',"
                " NOW() - interval '600 seconds' + interval '%s seconds',"
                " NOW() - interval '600 seconds' + interval '%s seconds')",
                (f'ses_pf_{aid[:6]}_{i}', sid, agent, start_off, end_off))
    db_conn.commit()
    return bid, sid, aid


class TestAttemptMetrics:
    def test_metrics_coverage_tokens_subtasks(self, db_conn, user_id):
        # 墙钟 500s：模型 [0,200]s 一轮(dur 200s, tin 5000)；子代理 [100,300]s
        # → 等待只算 [200,300]=100s，间隙 200s
        _bid, _sid, aid = _seed_perf(
            db_conn, user_id, model_turns=[(200_000, 5000, 800, 0)],
            subtasks=[('general', 100, 300)])
        m = load_attempt_metrics(db_conn, aid)
        assert m['wallMs'] == 500_000
        assert m['modelMs'] == 200_000
        assert m['subagentWaitMs'] == 100_000
        assert m['idleMs'] == 200_000
        assert m['turns'] == 1 and m['tokensIn'] == 5000 and m['tokensOut'] == 800
        assert m['subtaskCount'] == 1
        assert m['subtasks'][0]['agent'] == 'general'
        assert m['turnDetails'][0]['durationMs'] == 200_000
        assert m['completeness'] == {'turnsWithoutDuration': 0, 'runningSubtasks': 0}

    def test_missing_meta_counted_in_completeness(self, db_conn, user_id):
        _bid, _sid, aid = _seed_perf(db_conn, user_id, model_turns=[(None, None, None, 0)])
        m = load_attempt_metrics(db_conn, aid)
        assert m['turns'] == 1 and m['modelMs'] == 0
        assert m['completeness']['turnsWithoutDuration'] == 1

    def test_tools_error_and_repeat_aggregation(self, db_conn, user_id):
        _bid, _sid, aid = _seed_perf(db_conn, user_id)
        with db_conn.cursor() as cur:
            for i in range(3):
                cur.execute(
                    "INSERT INTO agent_tool_calls (oc_session_id, root_session_id,"
                    " part_id, tool, args_text, state, occurred_at) VALUES (%s,%s,%s,"
                    "'read', ' {\"path\": \"a.py\"} ', %s, NOW() - interval '500 seconds')",
                    (f'oc-x-{i}', _sid, f'p{i}', 'completed' if i else 'error'))
        m = load_attempt_metrics(db_conn, aid)
        assert m['tools']['errorCount'] == 1
        assert m['tools']['repeats'][0]['tool'] == 'read'
        assert m['tools']['repeats'][0]['count'] == 3

    def test_batch_reuse_agents_loaded(self, db_conn, user_id):
        bid, _sid, aid = _seed_perf(db_conn, user_id, reuse=['dev'])
        m = load_attempt_metrics(db_conn, aid)
        assert m['sourceType'] == 'batch'
        assert m['batchReuseAgents'] == ['dev']

    def test_kefu_attempt_returns_none(self, db_conn, user_id):
        # 全链路排除 kefu（spec §2）：详情路径同样不返回指标 → 端点 404 语义
        _bid, _sid, aid = _seed_perf(db_conn, user_id, source_type='kefu')
        assert load_attempt_metrics(db_conn, aid) is None


class TestDefinitionAggregates:
    def test_definition_tasks_and_overview_exclude_kefu(self, db_conn, user_id):
        _b1, _s1, a1 = _seed_perf(db_conn, user_id, model_turns=[(100_000, 10, 10, 0)])
        _b2, _s2, a2 = _seed_perf(db_conn, user_id, source_type='kefu')
        tasks = list_definition_tasks(db_conn, 'skill', 'stock-analysis')
        assert [t['attemptId'] for t in tasks] == [a1]     # kefu 被排除
        ov = definition_overview(db_conn)
        entry = next(d for d in ov if d['defName'] == 'stock-analysis')
        assert entry['tasks'] == 1 and entry['defKind'] == 'skill'
        assert entry['p50Ms'] == tasks[0]['wallMs']

    def test_slow_tasks_carry_def_attribution(self, db_conn, user_id):
        _b, _s, aid = _seed_perf(db_conn, user_id)
        slow = list_slow_tasks(db_conn, limit=10)
        hit = next(t for t in slow if t['attemptId'] == aid)
        assert hit['defKind'] == 'skill' and hit['defName'] == 'stock-analysis'

    def test_running_attempt_excluded_from_p50_but_listed(self, db_conn, user_id):
        _b, _s, a1 = _seed_perf(db_conn, user_id)
        with db_conn.cursor() as cur:
            cur.execute("UPDATE ai_execution_attempts SET finished_at = NULL,"
                        " status='running' WHERE id=%s", (a1,))
        db_conn.commit()
        tasks = list_definition_tasks(db_conn, 'skill', 'stock-analysis')
        assert len(tasks) == 1 and tasks[0]['status'] == 'running'
        ov = definition_overview(db_conn)
        entry = next(d for d in ov if d['defName'] == 'stock-analysis')
        assert entry['tasks'] == 0        # 分母只计已完结


# 追加到 server/tests/test_perf_analysis.py
from utils.perf_analysis import diagnose


def _bd(**over):
    base = {
        'wallMs': 100_000, 'modelMs': 10_000, 'subagentWaitMs': 10_000,
        'idleMs': 80_000, 'sourceType': 'batch', 'batchReuseAgents': None,
        'turnDetails': [{'messageId': 'm1', 'durationMs': 5_000,
                         'tokensIn': 1_000, 'tokensOut': 100}],
        'subtasks': [{'subtaskId': 'ses_a', 'agent': 'general', 'wallMs': 10_000,
                      'status': 'completed', 'startedAt': None, 'finishedAt': None,
                      'description': 'd'}],
        'tools': {'errorCount': 0, 'repeats': []},
    }
    base.update(over)
    return base


def _ts_st(sid, agent, start_s, end_s):
    """带真实 ISO 时间戳的子代理（秒偏移 → 2026-10-09T10:00:SS）。"""
    return {'subtaskId': sid, 'agent': agent, 'wallMs': 5_000,
            'status': 'completed', 'description': 'd',
            'startedAt': f'2026-10-09T10:00:{start_s:02d}',
            'finishedAt': f'2026-10-09T10:00:{end_s:02d}'}


class TestDiagnose:
    def test_subagent_wait_dominant_and_reuse_hint(self):
        bd = _bd(subagentWaitMs=70_000, idleMs=20_000,
                 subtasks=[{'subtaskId': 'ses_a', 'agent': 'general', 'wallMs': 70_000,
                            'status': 'completed', 'description': 'd'}])
        rules = [d['ruleId'] for d in diagnose(bd)]
        assert 'subagent_wait_dominant' in rules
        # 批 + 未配复用 → 追加提示
        assert 'subagent_reuse_hint' in rules
        # 已配复用 → 不提示
        bd2 = _bd(subagentWaitMs=70_000, idleMs=20_000, batchReuseAgents=['general'])
        assert 'subagent_reuse_hint' not in [d['ruleId'] for d in diagnose(bd2)]

    def test_model_dominant_is_info(self):
        bd = _bd(modelMs=80_000, idleMs=10_000)
        hit = next(d for d in diagnose(bd) if d['ruleId'] == 'model_dominant')
        assert hit['severity'] == 'info'

    def test_slow_turn_big_context(self):
        bd = _bd(turnDetails=[{'messageId': 'm2', 'durationMs': 45_000,
                               'tokensIn': 180_000, 'tokensOut': 0}])
        hit = next(d for d in diagnose(bd) if d['ruleId'] == 'slow_turn_big_context')
        assert hit['anchor'] == {'type': 'turn', 'ref': 'm2'}

    def test_repeated_tools_and_error_storm(self):
        bd = _bd(tools={'errorCount': 4, 'repeats': [
            {'tool': 'read', 'argsPreview': 'a.py', 'count': 5}]})
        rules = [d['ruleId'] for d in diagnose(bd)]
        assert 'repeated_tool_calls' in rules and 'tool_error_storm' in rules

    def test_engine_overhead_threshold(self):
        # idle 80% 但墙钟只有 100s（<120s 下限）→ 不触发
        assert 'engine_overhead' not in [d['ruleId'] for d in diagnose(_bd())]
        bd = _bd(wallMs=200_000, modelMs=10_000, subagentWaitMs=10_000, idleMs=180_000)
        assert 'engine_overhead' in [d['ruleId'] for d in diagnose(bd)]

    def test_outlier_vs_peers(self):
        assert 'outlier_vs_peers' in [d['ruleId']
                                      for d in diagnose(_bd(), peer_p50_ms=20_000)]
        assert 'outlier_vs_peers' not in [d['ruleId']
                                          for d in diagnose(_bd(), peer_p50_ms=50_000)]

    def test_sequential_subagents(self):
        st = [{'subtaskId': f'ses_{i}', 'agent': 'a', 'wallMs': 10_000,
               'status': 'completed', 'description': 'd'} for i in range(3)]
        assert 'sequential_subagents' in [d['ruleId'] for d in diagnose(_bd(subtasks=st))]

    def test_sequential_subagents_parallel_overlap_not_flagged(self):
        # 真实时间戳分支：(a) 区间重叠大（并行执行）→ 不提示串行
        st = [_ts_st('p1', 'x', 0, 5), _ts_st('p2', 'y', 0, 5)]
        assert 'sequential_subagents' not in [
            d['ruleId'] for d in diagnose(_bd(subtasks=st))]

    def test_sequential_subagents_serial_disjoint_flagged(self):
        # 真实时间戳分支：(b) 区间不重叠（串行执行）→ 提示串行
        st = [_ts_st('s1', 'x', 0, 5), _ts_st('s2', 'y', 5, 10)]
        assert 'sequential_subagents' in [
            d['ruleId'] for d in diagnose(_bd(subtasks=st))]

    def test_healthy_task_no_warn(self):
        bd = _bd(modelMs=90_000, idleMs=10_000)   # 模型主导 info，无 warn
        ds = diagnose(bd)
        assert not [d for d in ds if d['severity'] == 'warn']
