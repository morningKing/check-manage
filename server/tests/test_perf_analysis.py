# -*- coding: utf-8 -*-
"""SkillOpt 任务性能分析（spec 2026-10-09）：覆盖切分/任务指标/诊断规则/端点。"""
import json
import sys
import os
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.perf_analysis import (coverage_split, load_attempt_metrics,
                                 list_definition_tasks, list_slow_tasks,
                                 definition_overview, diagnose)


class TestCoverageSplit:
    def test_disjoint_no_overlap(self):
        # 墙钟 0..1000；模型 [0,400]，子代理 [600,1000]（与模型不重叠）
        r = coverage_split(0, 1000, [(0, 400)], [(600, 1000)])
        assert r == {'wallMs': 1000, 'modelMs': 400, 'toolMs': 0,
                     'subagentWaitMs': 400, 'idleMs': 200}

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
        assert r == {'wallMs': 500, 'modelMs': 0, 'toolMs': 0,
                     'subagentWaitMs': 0, 'idleMs': 500}

    def test_intervals_outside_wall_clamped(self):
        r = coverage_split(100, 500, [(0, 300)], [(400, 900)])
        assert r['modelMs'] == 200 and r['subagentWaitMs'] == 100 and r['wallMs'] == 400

    def test_sum_equals_wall(self):
        r = coverage_split(0, 9999, [(0, 3000), (2500, 7000)], [(1000, 2000), (6500, 9999)])
        assert r['modelMs'] + r['subagentWaitMs'] + r['idleMs'] == r['wallMs']

    def test_tool_intervals_split_from_model(self):
        # 工具 [200,600] 完全落在模型 [0,1000] 内 → toolMs=400，modelMs=600
        # （600+400 已铺满墙钟 → idle=0，四类之和恒等于 wallMs）
        r = coverage_split(0, 1000, [(0, 1000)], [], [(200, 600)])
        assert r == {'wallMs': 1000, 'modelMs': 600, 'toolMs': 400,
                     'subagentWaitMs': 0, 'idleMs': 0}

    def test_tool_outside_model_ignored(self):
        # 工具只发生在模型回合内；模型外的「工具区间」不计（防御）
        r = coverage_split(0, 1000, [(0, 400)], [], [(500, 900)])
        assert r['toolMs'] == 0 and r['modelMs'] == 400

    def test_tool_partial_overlap(self):
        r = coverage_split(0, 1000, [(0, 500)], [], [(300, 800)])
        assert r['toolMs'] == 200 and r['modelMs'] == 300 and r['idleMs'] == 500

    def test_default_no_tool_param_backwards_compatible(self):
        r = coverage_split(0, 1000, [(0, 400)], [(600, 1000)])
        assert r['toolMs'] == 0
        assert r['modelMs'] == 400 and r['subagentWaitMs'] == 400


# ---- 任务指标与聚合（DB 播种）----


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
        # （oc-agg-*/oc-old 为时长聚合用例的固定幂等键，一并回收防重跑撞唯一索引）
        cur.execute("DELETE FROM agent_tool_calls WHERE oc_session_id LIKE 'oc-x-%'"
                    " OR oc_session_id LIKE 'oc-agg-%' OR oc_session_id = 'oc-old'")
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

    def test_detail_coverage_includes_tool_ms(self, db_conn, user_id):
        _bid, _sid, aid = _seed_perf(
            db_conn, user_id, model_turns=[(400_000, 10_000, 100, 0)])
        # 模型轮 [0,400]s；工具 [100,300]s 在其中 → toolMs 200s，纯模型 200s
        # （t0 = NOW()-600s，故 started NOW()-500s；墙钟 500s → idle 100s）
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_tool_calls (oc_session_id, root_session_id,"
                " part_id, tool, args_text, state, occurred_at,"
                " started_at, duration_ms) VALUES (%s, %s, 'pd1', 'bash', '{}',"
                " 'completed', NOW() - interval '500 seconds',"
                " NOW() - interval '500 seconds', 200000)",
                ('oc-x-pd1', _sid))
        db_conn.commit()
        m = load_attempt_metrics(db_conn, aid)
        assert m['coverage']['toolMs'] == 200_000
        assert set(m['coverage']) == {'wallMs', 'modelMs', 'toolMs',
                                      'subagentWaitMs', 'idleMs'}
        assert m['modelMs'] == 200_000          # 400s 轮次 - 200s 工具
        assert m['subagentWaitMs'] == 0 and m['idleMs'] == 100_000

    def test_tool_aggregates_with_duration(self, db_conn, user_id):
        _bid, _sid, aid = _seed_perf(db_conn, user_id)
        rows = [
            ('pa1', 'bash', '{}', 'completed', 40_000),
            ('pa2', 'bash', '{}', 'completed', 30_000),
            ('pa3', 'read', '{"p":"a"}', 'completed', 5_000),
            ('pa4', 'read', '{"p":"a"}', 'completed', 5_000),
            ('pa5', 'read', '{"p":"a"}', 'error', None),      # 旧数据无时长
        ]
        with db_conn.cursor() as cur:
            for i, (pid, tool, args, st, dur) in enumerate(rows):
                cur.execute(
                    "INSERT INTO agent_tool_calls (oc_session_id, root_session_id,"
                    " part_id, tool, args_text, state, occurred_at, duration_ms)"
                    " VALUES (%s, %s, %s, %s, %s, %s,"
                    " NOW() - interval '300 seconds', %s)",
                    (f'oc-agg-{i}', _sid, pid, tool, args, st, dur))
        db_conn.commit()
        m = load_attempt_metrics(db_conn, aid)
        tools = m['tools']
        assert tools['errorCount'] == 1
        assert tools['durationAvailable'] is True
        by = {t['tool']: t for t in tools['byTool']}
        assert by['bash'] == {'tool': 'bash', 'count': 2, 'totalMs': 70_000}
        assert by['read']['count'] == 3 and by['read']['totalMs'] == 10_000
        rep = next(r for r in tools['repeats'] if r['tool'] == 'read')
        assert rep['count'] == 3 and rep['totalMs'] == 10_000

    def test_tool_duration_unavailable_flag(self, db_conn, user_id):
        _bid, _sid, aid = _seed_perf(db_conn, user_id)
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_tool_calls (oc_session_id, root_session_id,"
                " part_id, tool, args_text, state, occurred_at)"
                " VALUES ('oc-old', %s, 'px', 'read', '{}', 'completed',"
                " NOW() - interval '300 seconds')", (_sid,))
        db_conn.commit()
        m = load_attempt_metrics(db_conn, aid)
        assert m['tools']['durationAvailable'] is False


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

    def test_null_started_at_attempt_not_listed(self, db_conn, user_id):
        """未派发（started_at NULL）的 attempt 不进任务列表——否则 wallMs
        以 epoch 起点计成天文数字（T2 审查 Minor 的回归钉）。"""
        _b, _s, a1 = _seed_perf(db_conn, user_id)
        with db_conn.cursor() as cur:
            cur.execute("UPDATE ai_execution_attempts SET started_at = NULL"
                        " WHERE id=%s", (a1,))
        db_conn.commit()
        assert list_definition_tasks(db_conn, 'skill', 'stock-analysis') == []
        assert all(t['attemptId'] != a1 for t in list_slow_tasks(db_conn, limit=50))


# ---- 诊断规则（纯函数）----


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

    def test_tool_hotspot_rule(self):
        bd = _bd(wallMs=100_000,
                 tools={'errorCount': 0, 'durationAvailable': True,
                        'byTool': [{'tool': 'bash', 'count': 2, 'totalMs': 50_000}],
                        'repeats': []})
        hit = next(d for d in diagnose(bd) if d['ruleId'] == 'tool_hotspot')
        assert hit['severity'] == 'warn'
        assert hit['anchor'] == {'type': 'segment', 'ref': 'tools'}
        # 占比不足不触发
        bd2 = _bd(wallMs=100_000,
                  tools={'errorCount': 0, 'durationAvailable': True,
                         'byTool': [{'tool': 'bash', 'count': 2, 'totalMs': 30_000}],
                         'repeats': []})
        assert 'tool_hotspot' not in [d['ruleId'] for d in diagnose(bd2)]

    def test_repeated_tool_calls_text_carries_duration(self):
        bd = _bd(tools={'errorCount': 0, 'durationAvailable': True,
                        'byTool': [{'tool': 'read', 'count': 5, 'totalMs': 9_000}],
                        'repeats': [{'tool': 'read', 'argsPreview': 'a.py',
                                     'count': 5, 'totalMs': 9_000}]})
        hit = next(d for d in diagnose(bd) if d['ruleId'] == 'repeated_tool_calls')
        assert '9.0s' in hit['text']


@pytest.fixture
def admin_h():
    from auth import create_token
    tok = create_token({'id': 'user-admin', 'username': 'admin', 'role': 'admin'})
    return {'Authorization': f'Bearer {tok}'}


@pytest.fixture
def pf_client(db_conn):
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


class TestPerfEndpoints:
    def test_overview_tasks_slow_attempt_contract(self, db_conn, user_id,
                                                  pf_client, admin_h):
        _b, _s, aid = _seed_perf(db_conn, user_id)
        r = pf_client.get('/ai/chat/admin/perf/overview', headers=admin_h)
        assert r.status_code == 200
        assert any(d['defName'] == 'stock-analysis' for d in r.get_json()['defs'])

        r = pf_client.get('/ai/chat/admin/perf/defs/skill/stock-analysis/tasks',
                          headers=admin_h)
        assert r.status_code == 200
        tasks = r.get_json()['tasks']
        assert tasks and tasks[0]['attemptId'] == aid
        assert {'attemptId', 'wallMs', 'modelRatio', 'completeness'} <= set(tasks[0])

        r = pf_client.get(f'/ai/chat/admin/perf/attempts/{aid}', headers=admin_h)
        body = r.get_json()
        assert r.status_code == 200
        assert body['attempt']['attemptId'] == aid
        assert set(body['coverage']) == {'wallMs', 'modelMs', 'subagentWaitMs', 'idleMs'}
        assert isinstance(body['turns'], list) and isinstance(body['subtasks'], list)

        r = pf_client.get(f'/ai/chat/admin/perf/attempts/{aid}/diagnosis',
                          headers=admin_h)
        assert r.status_code == 200
        assert isinstance(r.get_json()['diagnoses'], list)

        r = pf_client.get('/ai/chat/admin/perf/slow-tasks?limit=5', headers=admin_h)
        assert r.status_code == 200
        assert any(t['attemptId'] == aid for t in r.get_json()['tasks'])

    def test_diagnosis_uses_cross_task_peer_p50(self, db_conn, user_id,
                                                pf_client, admin_h):
        # 同一定义两个 attempt：快 100s（P50）、慢 500s（>4×P50=OUTLIER_P50_FACTOR）
        # → 端点级首次断言概览→diagnose 的跨任务接缝（定向 P50 查询）
        _b, _s, slow_id = _seed_perf(db_conn, user_id)      # 墙钟 500s（600-100）
        _fb, _fs, fast_id = _seed_perf(db_conn, user_id)
        with db_conn.cursor() as cur:
            cur.execute("UPDATE ai_execution_attempts SET"
                        " started_at = NOW() - interval '200 seconds',"
                        " finished_at = NOW() - interval '100 seconds' WHERE id = %s",
                        (fast_id,))
        db_conn.commit()
        r = pf_client.get(f'/ai/chat/admin/perf/attempts/{slow_id}/diagnosis',
                          headers=admin_h)
        assert r.status_code == 200
        rules = [d['ruleId'] for d in r.get_json()['diagnoses']]
        assert 'outlier_vs_peers' in rules

    def test_attempt_404_and_limit_clamp(self, db_conn, user_id, pf_client, admin_h):
        assert pf_client.get('/ai/chat/admin/perf/attempts/nope',
                             headers=admin_h).status_code == 404
        r = pf_client.get('/ai/chat/admin/perf/defs/skill/x/tasks?limit=99999',
                          headers=admin_h)
        assert r.status_code == 200       # 收敛到 200，不 500

    def test_requires_admin_permission(self, pf_client):
        from auth import create_token
        tok = create_token({'id': 'u2', 'username': 'g', 'role': 'guest'})
        r = pf_client.get('/ai/chat/admin/perf/overview',
                          headers={'Authorization': f'Bearer {tok}'})
        assert r.status_code == 403


class TestToolDurationSchema:
    def test_agent_tool_calls_duration_columns_exist(self, db_conn):
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'agent_tool_calls' "
                "  AND column_name IN ('started_at', 'duration_ms')")
            cols = {r[0] for r in cur.fetchall()}
        assert cols == {'started_at', 'duration_ms'}

    def test_perf_indexes_exist(self, db_conn):
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT indexname FROM pg_indexes WHERE tablename IN "
                "('agent_tool_calls', 'ai_execution_manifests') "
                "  AND indexname IN ('idx_agent_tool_call_root', "
                "                    'idx_execution_manifest_kind_name')")
            names = {r[0] for r in cur.fetchall()}
        assert names == {'idx_agent_tool_call_root',
                         'idx_execution_manifest_kind_name'}
