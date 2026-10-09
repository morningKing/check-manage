# SkillOpt 任务性能分析（一期）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在后台 SkillOpt 页新增「性能分析」视图：按定义看任务耗时趋势，下钻单任务看覆盖切分（模型/子代理等待/引擎间隙）与瀑布图，规则引擎产出只读优化诊断。

**Architecture:** 纯读聚合层 `server/utils/perf_analysis.py`（get_db 注入的纯函数：覆盖切分/任务指标/定义聚合/诊断规则）+ `ai_execution_admin_bp` 下 5 个只读端点 + `AiSkillOpt.vue` 新 ElTabPane「性能分析」（PerfView 壳 + 慢任务 Top + 趋势图 + 任务下钻 + 诊断列表，ECharts 复用仓库已有依赖）。零迁移、零写路径改动。

**Tech Stack:** Flask + psycopg2（现库）；Vue3 + Element Plus + ECharts（已有依赖）；pytest 串行 + vitest + Playwright。

**Spec:** `docs/superpowers/specs/2026-10-09-skillopt-perf-analysis-design.md`（口径/契约/规则表以 spec 为准，本计划实现其中一期全部内容）

## Global Constraints

- 全链路排除 kefu：SQL 一律带 `source_type <> 'kefu'`（spec §2）。
- 覆盖切分三类互斥且之和 = wallMs；禁止模型/子代理时长简单加总（spec §3.2）。
- P50/P95/均值只统计已完结任务（started_at/finished_at 均非空）（spec §3.4）。
- 旧数据缺 meta.durationMs 不估算，进 `completeness.turnsWithoutDuration`（spec §3.1）。
- 端点权限一律 `@require_permission('admin.ai_chat_admin')`，camelCase 出参（spec §4）。
- 诊断 severity 只有 'info'|'warn'；阈值用 spec §6 的常量名与数值，逐字一致。
- ECharts 走懒加载封装；不新增任何 npm 依赖。
- 后端测试跑串行（`python -m pytest <file> -q -p no:cacheprovider`，在 server/ 下）；跑全量前先停 3002 后端。
- 工具调用参数规范化（重复判定）：`' '.join((args_text or '').split())[:200]`。
- 提交信息用中文 fix/feat/docs 前缀；docs/ 与 plans/ 目录需 `git add -f`。

---

### Task 1: 覆盖切分器 coverage_split（纯函数）

**Files:**
- Create: `server/utils/perf_analysis.py`
- Test: `server/tests/test_perf_analysis.py`

**Interfaces:**
- Produces: `coverage_split(wall_start_ms: int, wall_end_ms: int, model_intervals: list[tuple[int,int]], subagent_intervals: list[tuple[int,int]]) -> dict`，返回 `{'wallMs','modelMs','subagentWaitMs','idleMs'}`（int 毫秒）；后续所有任务依赖此签名与键名。

- [ ] **Step 1: 写失败测试**

```python
# server/tests/test_perf_analysis.py 顶部
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && python -m pytest tests/test_perf_analysis.py -q -p no:cacheprovider`
Expected: FAIL `ModuleNotFoundError: No module named 'utils.perf_analysis'`

- [ ] **Step 3: 最小实现**

```python
# server/utils/perf_analysis.py
"""SkillOpt 任务性能分析（2026-10-09 spec）：纯读聚合层。

覆盖切分（§3.2）：模型轮次与子代理区间在真实执行中重叠——把区间投到 attempt
时间轴切出互斥的三类时长（模型活跃 / 子代理等待 / 引擎间隙），和恒等于墙钟。
全部函数 get_db 参数注入（skill_fit 惯例），时间统一 epoch 毫秒。
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def _to_ms(dt) -> int | None:
    """psycopg2 timestamptz（tz-aware datetime）→ epoch 毫秒。"""
    if dt is None:
        return None
    return int(dt.timestamp() * 1000)


def _union(intervals: list[tuple[int, int]], lo: int, hi: int) -> list[tuple[int, int]]:
    """夹到 [lo,hi] 后合并重叠区间，返回升序不重叠列表。"""
    clamped = [(max(s, lo), min(e, hi)) for s, e in intervals if min(e, hi) > max(s, lo)]
    out: list[tuple[int, int]] = []
    for s, e in sorted(clamped):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _covered_ms(intervals: list[tuple[int, int]]) -> int:
    return sum(e - s for s, e in intervals)


def _subtract(base: list[tuple[int, int]], mask: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """base - mask（都为合并过的升序区间），返回剩余区间。"""
    out: list[tuple[int, int]] = []
    for s, e in base:
        cur = s
        for ms, me in mask:
            if me <= cur or ms >= e:
                continue
            if ms > cur:
                out.append((cur, ms))
            cur = max(cur, me)
            if cur >= e:
                break
        if cur < e:
            out.append((cur, e))
    return out


def coverage_split(wall_start_ms: int, wall_end_ms: int,
                   model_intervals: list[tuple[int, int]],
                   subagent_intervals: list[tuple[int, int]]) -> dict:
    """attempt 墙钟 → 三类互斥覆盖时长（spec §3.2）。"""
    wall_ms = max(0, wall_end_ms - wall_start_ms)
    model = _union(model_intervals, wall_start_ms, wall_end_ms)
    sub = _union(subagent_intervals, wall_start_ms, wall_end_ms)
    model_ms = _covered_ms(model)
    wait = _subtract(sub, model)
    wait_ms = _covered_ms(wait)
    idle_ms = max(0, wall_ms - model_ms - wait_ms)
    return {'wallMs': wall_ms, 'modelMs': model_ms,
            'subagentWaitMs': wait_ms, 'idleMs': idle_ms}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && python -m pytest tests/test_perf_analysis.py -q -p no:cacheprovider`
Expected: PASS（7 个用例）

- [ ] **Step 5: 提交**

```bash
git add server/utils/perf_analysis.py server/tests/test_perf_analysis.py
git commit -m "feat(skillopt): 性能分析覆盖切分器——模型/子代理等待/引擎间隙互斥切分"
```

---

### Task 2: 任务指标与聚合查询（attempt 指标 / 定义任务列表 / 慢任务 Top / 概览）

**Files:**
- Modify: `server/utils/perf_analysis.py`（追加查询与装配函数）
- Test: `server/tests/test_perf_analysis.py`（追加 DB 用例）

**Interfaces:**
- Consumes: Task 1 的 `coverage_split`、`_to_ms`。
- Produces（后续任务依赖的确切签名与返回键）:
  - `load_attempt_metrics(db_ctx, attempt_id: str) -> dict | None` —— 单任务全指标，含 `turns/subtasks/tools/batchReuseAgents`（诊断与详情端点的输入）；
  - `list_definition_tasks(db_ctx, kind: str, name: str, limit: int = 50) -> list[dict]`；
  - `list_slow_tasks(db_ctx, limit: int = 10) -> list[dict]`（带 `defKind/defName`）；
  - `definition_overview(db_ctx) -> list[dict]`（每定义 P50/P95 等，分母只计已完结）；
  - `_percentile(sorted_vals: list[int], q: float) -> int`。
- 任务条目（列表形）键：`attemptId,sessionId,sourceType,status,startedAt,finishedAt,wallMs,modelMs,modelRatio,subagentWaitMs,idleMs,turns,tokensIn,tokensOut,subtaskCount,completeness`。

- [ ] **Step 1: 写失败测试（DB 播种，风格照 test_subagent_reuse）**

```python
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
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_chat_sessions WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM ai_chat_batches WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
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
            "INSERT INTO ai_execution_manifests (attempt_id, kind, name, path,"
            " content_hash, injected) "
            "VALUES (%s,'skill','stock-analysis','C:\\tmp\\pf\\SKILL.md','h1',true)",
            (aid,))
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && python -m pytest tests/test_perf_analysis.py -q -p no:cacheprovider`
Expected: 新增用例 ImportError/AttributeError（函数不存在）

- [ ] **Step 3: 实现查询与装配（追加到 perf_analysis.py）**

```python
# ---- 追加到 server/utils/perf_analysis.py ----
import time as _time


def _now_ms() -> int:
    return int(_time.time() * 1000)


def _iso(dt):
    return dt.isoformat() if dt is not None else None


def _percentile(sorted_vals: list, q: float) -> int:
    if not sorted_vals:
        return 0
    idx = min(len(sorted_vals) - 1, max(0, round(q * (len(sorted_vals) - 1))))
    return sorted_vals[idx]


_ATTEMPT_SQL = (
    "SELECT a.id, a.session_id, a.source_type, a.source_id, a.status,"
    " a.started_at, a.finished_at, a.requested_model, a.effective_model "
    "FROM ai_execution_attempts a WHERE a.id = %s")

_TASK_LIST_SQL = (
    "SELECT a.id, a.session_id, a.source_type, a.status, a.started_at, a.finished_at"
    " FROM ai_execution_attempts a"
    " WHERE a.source_type <> 'kefu' AND EXISTS ("
    "   SELECT 1 FROM ai_execution_manifests m WHERE m.attempt_id = a.id"
    "   AND m.kind = %s AND m.name = %s)"
    " ORDER BY a.started_at DESC NULLS LAST LIMIT %s")

_SLOW_SQL = (
    "SELECT a.id, a.session_id, a.source_type, a.status, a.started_at, a.finished_at,"
    " (SELECT m.kind FROM ai_execution_manifests m WHERE m.attempt_id = a.id"
    "   AND m.kind IN ('skill','agent') ORDER BY m.kind, m.name LIMIT 1) AS kind,"
    " (SELECT m.name FROM ai_execution_manifests m WHERE m.attempt_id = a.id"
    "   AND m.kind IN ('skill','agent') ORDER BY m.kind, m.name LIMIT 1) AS name "
    "FROM ai_execution_attempts a "
    "WHERE a.source_type <> 'kefu' AND a.started_at IS NOT NULL"
    "   AND a.finished_at IS NOT NULL "
    "ORDER BY EXTRACT(EPOCH FROM (a.finished_at - a.started_at)) DESC LIMIT %s")

_MSG_SQL = (
    "SELECT id, created_at, meta FROM ai_chat_messages "
    "WHERE session_id = %s AND role = 'assistant'"
    " AND created_at >= to_timestamp(%s/1000.0)"
    " AND created_at <= to_timestamp(%s/1000.0) ORDER BY created_at")


def _first_text_preview(cur, mid: str) -> str:
    cur.execute("SELECT content FROM ai_chat_messages WHERE id = %s", (mid,))
    row = cur.fetchone()
    for part in (row[0] if row else []) or []:
        if isinstance(part, dict) and part.get('type') == 'text' and part.get('text'):
            return part['text'].strip()[:80]
    return ''


def _norm_args(args_text) -> str:
    return ' '.join((args_text or '').split())[:200]


def _tool_aggregates(cur, sid: str, start_ms: int, end_ms: int) -> dict:
    cur.execute(
        "SELECT tool, args_text, state FROM agent_tool_calls "
        "WHERE root_session_id = %s AND occurred_at >= to_timestamp(%s/1000.0)"
        " AND occurred_at <= to_timestamp(%s/1000.0)",
        (sid, start_ms / 1000.0, end_ms / 1000.0))
    counts: dict = {}
    errors = 0
    for tool, args_text, state in cur.fetchall():
        if state == 'error':
            errors += 1
        key = f'{tool}|{_norm_args(args_text)}'
        counts[key] = counts.get(key, 0) + 1
    repeats = [{'tool': k.split('|', 1)[0], 'argsPreview': k.split('|', 1)[1],
                'count': n} for k, n in counts.items() if n >= 3]
    repeats.sort(key=lambda r: -r['count'])
    return {'errorCount': errors, 'repeats': repeats}


def _attempt_metrics(cur, row: dict, *, with_detail: bool) -> dict:
    sid = row['session_id']
    start_ms = _to_ms(row['started_at']) or 0
    end_ms = _to_ms(row['finished_at']) or _now_ms()
    model_ivs: list = []
    turns, turn_details = [], []
    missing = 0
    cur.execute(_MSG_SQL, (sid, start_ms / 1000.0, end_ms / 1000.0))
    for mid, created, meta in cur.fetchall():
        meta = meta or {}
        dur = meta.get('durationMs')
        tin = int(meta.get('tokensInput') or 0)
        tout = int(meta.get('tokensOutput') or 0)
        turns.append({'messageId': mid, 'durationMs': dur,
                      'tokensIn': tin, 'tokensOut': tout})
        if dur is None:
            missing += 1
        else:
            cms = _to_ms(created)
            model_ivs.append((cms, cms + int(dur)))
            turn_details.append({
                'messageId': mid, 'createdAt': _iso(created),
                'durationMs': int(dur), 'tokensIn': tin, 'tokensOut': tout,
                'preview': _first_text_preview(cur, mid)})
    cur.execute(
        "SELECT id, agent, description, status, created_at, completed_at "
        "FROM ai_chat_subtasks WHERE root_session_id = %s ORDER BY created_at", (sid,))
    sub_ivs, subtasks, running = [], [], 0
    for stid, agent, desc, status, created, finished in cur.fetchall():
        s_ms, e_ms = _to_ms(created), _to_ms(finished) or _now_ms()
        if status == 'running':
            running += 1
        sub_ivs.append((s_ms, e_ms))
        subtasks.append({'subtaskId': stid, 'agent': agent, 'description': desc,
                         'status': status, 'startedAt': _iso(created),
                         'finishedAt': _iso(finished),
                         'wallMs': max(0, e_ms - s_ms)})
    cov = coverage_split(start_ms, end_ms, model_ivs, sub_ivs)
    wall_ms = cov['wallMs']
    tools = {'errorCount': 0, 'repeats': []}
    if with_detail:
        tools = _tool_aggregates(cur, sid, start_ms, end_ms)
    out = {
        'attemptId': row['id'], 'sessionId': sid,
        'sourceType': row['source_type'], 'status': row['status'],
        'startedAt': _iso(row['started_at']), 'finishedAt': _iso(row['finished_at']),
        'wallMs': wall_ms, 'modelMs': cov['modelMs'],
        'modelRatio': round(cov['modelMs'] / wall_ms, 4) if wall_ms else 0,
        'subagentWaitMs': cov['subagentWaitMs'], 'idleMs': cov['idleMs'],
        'turns': len(turns),
        'tokensIn': sum(t['tokensIn'] for t in turns),
        'tokensOut': sum(t['tokensOut'] for t in turns),
        'subtaskCount': len(subtasks),
        'completeness': {'turnsWithoutDuration': missing, 'runningSubtasks': running},
    }
    if with_detail:
        cur.execute("SELECT subagent_reuse FROM ai_chat_batches WHERE id = %s",
                    (row.get('source_id'),))
        br = cur.fetchone()
        out['batchReuseAgents'] = (br[0] if br and br[0] else None)
        out['requestedModel'] = row.get('requested_model')
        out['effectiveModel'] = row.get('effective_model')
        out['turnDetails'] = turn_details
        out['subtasks'] = subtasks
        out['tools'] = tools
    return out


def _list_metrics(db_ctx, sql: str, params: tuple, *, with_detail: bool) -> list:
    out = []
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            for r in cur.fetchall():
                out.append(_attempt_metrics(cur, dict(zip(cols, r)),
                                            with_detail=with_detail))
    return out


def load_attempt_metrics(db_ctx, attempt_id: str):
    """单任务全指标（detail），不存在返回 None。db_ctx 可注入（测试）。"""
    ctx = db_ctx
    if ctx is None:
        from db import get_db as _default
        ctx = _default
    with ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(_ATTEMPT_SQL, (attempt_id,))
            row_ = cur.fetchone()
            if not row_:
                return None
            cols = [d[0] for d in cur.description]
            return _attempt_metrics(cur, dict(zip(cols, row_)), with_detail=True)


def list_definition_tasks(db_ctx, kind: str, name: str, limit: int = 50) -> list:
    ctx = db_ctx
    if ctx is None:
        from db import get_db as _default
        ctx = _default
    return _list_metrics(ctx, _TASK_LIST_SQL, (kind, name, limit), with_detail=False)


def list_slow_tasks(db_ctx, limit: int = 10) -> list:
    ctx = db_ctx
    if ctx is None:
        from db import get_db as _default
        ctx = _default
    out = []
    with ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(_SLOW_SQL, (limit,))
            cols = [d[0] for d in cur.description]
            for r in cur.fetchall():
                row = dict(zip(cols, r))
                m = _attempt_metrics(cur, row, with_detail=False)
                m['defKind'], m['defName'] = row.get('kind'), row.get('name')
                out.append(m)
    return out


def definition_overview(db_ctx) -> list:
    ctx = db_ctx
    if ctx is None:
        from db import get_db as _default
        ctx = _default
    defs_sql = (
        "SELECT DISTINCT m.kind, m.name FROM ai_execution_manifests m "
        "JOIN ai_execution_attempts a ON a.id = m.attempt_id "
        "WHERE m.kind IN ('skill','agent') AND a.source_type <> 'kefu' "
        "ORDER BY m.kind, m.name")
    out = []
    with ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(defs_sql)
            pairs = cur.fetchall()
    for kind, name in pairs:
        tasks = [t for t in _list_metrics(
            ctx, _TASK_LIST_SQL, (kind, name, 200), with_detail=False)
            if t['finishedAt'] is not None]
        walls = sorted(t['wallMs'] for t in tasks)
        out.append({
            'defKind': kind, 'defName': name, 'tasks': len(tasks),
            'p50Ms': _percentile(walls, 0.5),
            'p95Ms': _percentile(walls, 0.95),
            'avgModelRatio': (round(sum(t['modelRatio'] for t in tasks)
                                    / len(tasks), 4) if tasks else None),
            'lastActivity': (tasks[0]['startedAt'] if tasks else None)})
    return out
```

（注意：`_attempt_metrics` 内层查询复用同一 cursor，外层行集已 `fetchall` 物化故安全；`definition_overview` 先取定义对再逐定义聚合，同理。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && python -m pytest tests/test_perf_analysis.py -q -p no:cacheprovider`
Expected: PASS（Task 1 的 7 例 + 本任务 7 例）

- [ ] **Step 5: 提交**

```bash
git add server/utils/perf_analysis.py server/tests/test_perf_analysis.py
git commit -m "feat(skillopt): 性能分析任务指标与聚合——attempt 指标/定义任务/慢任务Top/概览"
```

---

### Task 3: 诊断规则引擎 diagnose()

**Files:**
- Modify: `server/utils/perf_analysis.py`（追加规则与编排器）
- Test: `server/tests/test_perf_analysis.py`（追加纯函数用例）

**Interfaces:**
- Consumes: Task 2 `load_attempt_metrics` 的 detail dict（`coverage 键 + turnDetails/subtasks/tools/batchReuseAgents/sourceType`）。
- Produces: `diagnose(breakdown: dict, peer_p50_ms: int | None = None) -> list[dict]`，条目 `{'ruleId','severity','text','anchor':{'type','ref'}}`；type ∈ turn|subtask|segment|def。规则 id 与 spec §6 表逐字一致。

- [ ] **Step 1: 写失败测试**

```python
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

    def test_healthy_task_no_warn(self):
        bd = _bd(modelMs=90_000, idleMs=10_000)   # 模型主导 info，无 warn
        ds = diagnose(bd)
        assert not [d for d in ds if d['severity'] == 'warn']
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && python -m pytest tests/test_perf_analysis.py -k Diagnose -q -p no:cacheprovider`
Expected: ImportError（diagnose 不存在）

- [ ] **Step 3: 实现（追加到 perf_analysis.py）**

```python
# ---- 诊断规则（spec §6，阈值常量逐字）----
SUBAGENT_WAIT_RATIO = 0.5
SUBAGENT_MIN_WALL_MS = 60_000
MODEL_RATIO = 0.7
SLOW_TURN_MS = 30_000
BIG_CONTEXT_TOKENS = 80_000
REPEAT_TOOL_COUNT = 3
TOOL_ERROR_COUNT = 3
IDLE_RATIO = 0.3
IDLE_MIN_WALL_MS = 120_000
OUTLIER_P50_FACTOR = 4
SEQUENTIAL_OVERLAP = 0.1


def _overlap_ratio(a: tuple[int, int], b: tuple[int, int]) -> float:
    inter = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    shorter = max(1, min(a[1] - a[0], b[1] - b[0]))
    return inter / shorter


def diagnose(breakdown: dict, peer_p50_ms: int | None = None) -> list:
    wall = breakdown.get('wallMs') or 0
    out: list = []

    def add(rule_id, severity, text, anchor_type, ref):
        out.append({'ruleId': rule_id, 'severity': severity, 'text': text,
                    'anchor': {'type': anchor_type, 'ref': ref}})

    subtasks = breakdown.get('subtasks') or []
    wait_ms = breakdown.get('subagentWaitMs') or 0
    if wall > SUBAGENT_MIN_WALL_MS and wait_ms / wall > SUBAGENT_WAIT_RATIO:
        slowest = max(subtasks, key=lambda s: s.get('wallMs') or 0) if subtasks else None
        add('subagent_wait_dominant', 'warn',
            f'{round(wait_ms / wall * 100)}% 时间在等子代理（{len(subtasks)} 次委派）',
            'subtask', slowest['subtaskId'] if slowest else 'subtasks')
        if breakdown.get('sourceType') == 'batch' and not breakdown.get('batchReuseAgents'):
            agents = sorted({s.get('agent') for s in subtasks if s.get('agent')})
            add('subagent_reuse_hint', 'info',
                f'批任务未启用子代理会话复用（委派 agent：{",".join(agents) or "未识别"}）'
                '——多轮委派场景建议在批配置开启 subagent_reuse',
                'subtask', slowest['subtaskId'] if slowest else 'subtasks')

    model_ms = breakdown.get('modelMs') or 0
    if wall and model_ms / wall > MODEL_RATIO:
        turns = [t for t in (breakdown.get('turnDetails') or [])
                 if t.get('durationMs') is not None]
        slow = max(turns, key=lambda t: t['durationMs']) if turns else None
        add('model_dominant', 'info',
            f'时间主要花在模型推理（共 {model_ms // 1000}s / '
            f'{breakdown.get("turns") or 0} 轮）',
            'segment', slow['messageId'] if slow else 'turns')

    for i, t in enumerate(breakdown.get('turnDetails') or [], start=1):
        if (t.get('durationMs') or 0) > SLOW_TURN_MS \
                and (t.get('tokensIn') or 0) > BIG_CONTEXT_TOKENS:
            add('slow_turn_big_context', 'warn',
                f'第 {i} 轮 {t["durationMs"] // 1000}s、输入 '
                f'{round(t["tokensIn"] / 1000)}k token——建议拆分任务或压缩历史',
                'turn', t['messageId'])

    tools = breakdown.get('tools') or {}
    for r in tools.get('repeats') or []:
        add('repeated_tool_calls', 'warn',
            f'{r["tool"]} 同一参数重复 {r["count"]} 次（如 {r["argsPreview"][:40]}）'
            '——考虑在指令里要求一次读全/批量操作', 'segment', 'tools')
    if (tools.get('errorCount') or 0) >= TOOL_ERROR_COUNT:
        add('tool_error_storm', 'warn',
            f'{tools["errorCount"]} 次工具失败重试，检查工具参数与环境',
            'segment', 'tools')

    idle = breakdown.get('idleMs') or 0
    if wall > IDLE_MIN_WALL_MS and idle / wall > IDLE_RATIO:
        add('engine_overhead', 'warn',
            f'引擎开销占 {round(idle / wall * 100)}%——检查 OpenCode 服务状态/'
            '同时段任务是否普遍如此', 'segment', 'idle')

    if peer_p50_ms and wall > peer_p50_ms * OUTLIER_P50_FACTOR:
        add('outlier_vs_peers', 'warn',
            f'比该定义典型水平慢 {round(wall / max(1, peer_p50_ms))} 倍'
            f'（P50 {peer_p50_ms // 1000}s vs 本次 {wall // 1000}s）', 'def', 'self')

    if len(subtasks) >= 2:
        ivs = [(i, i + (s.get('wallMs') or 0)) for i, s in
               enumerate(subtasks)]  # 相对序即可判重叠形态
        pairs = [(ivs[i], ivs[j]) for i in range(len(ivs))
                 for j in range(i + 1, len(ivs))]
        if all(_overlap_ratio(a, b) < SEQUENTIAL_OVERLAP for a, b in pairs):
            add('sequential_subagents', 'info',
                f'{len(subtasks)} 个子代理串行执行，评估可否并行委托',
                'subtask', subtasks[0]['subtaskId'])
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && python -m pytest tests/test_perf_analysis.py -q -p no:cacheprovider`
Expected: PASS（全部）

- [ ] **Step 5: 提交**

```bash
git add server/utils/perf_analysis.py server/tests/test_perf_analysis.py
git commit -m "feat(skillopt): 性能诊断规则引擎——8 条只读规则+复用提示，阈值常量化"
```

---

### Task 4: 5 个管理端点

**Files:**
- Modify: `server/routes/ai_session_admin.py`（skill-fit 端点之后追加）
- Test: `server/tests/test_perf_analysis.py`（追加端点用例）

**Interfaces:**
- Consumes: Task 2/3 的 `definition_overview / list_definition_tasks / load_attempt_metrics / list_slow_tasks / diagnose`。
- Produces: `GET /ai/chat/admin/perf/overview`、`/perf/defs/<kind>/<name>/tasks`、`/perf/attempts/<id>`、`/perf/attempts/<id>/diagnosis`、`/perf/slow-tasks`（均 `admin.ai_chat_admin`）。attempt 端点出参把 detail 拆为 `attempt`（含 coverage）+ `turns`（=turnDetails）+ `subtasks` + `tools` + `completeness` + `diagnosis 兄弟端点`。

- [ ] **Step 1: 写失败测试**

```python
# 追加到 server/tests/test_perf_analysis.py
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && python -m pytest tests/test_perf_analysis.py -k PerfEndpoints -q -p no:cacheprovider`
Expected: 404（路由不存在）

- [ ] **Step 3: 实现路由（追加到 ai_session_admin.py，skill-fit 端点之后）**

```python
# ── 任务性能分析（spec 2026-10-09 §4）────────────────────────────

@ai_execution_admin_bp.get('/perf/overview')
@require_permission('admin.ai_chat_admin')
def perf_overview():
    from db import get_db
    from utils.perf_analysis import definition_overview
    return jsonify({'defs': definition_overview(get_db)})


@ai_execution_admin_bp.get('/perf/defs/<kind>/<name>/tasks')
@require_permission('admin.ai_chat_admin')
def perf_def_tasks(kind, name):
    from db import get_db
    from utils.perf_analysis import list_definition_tasks
    try:
        limit = min(max(int(request.args.get('limit') or 50), 1), 200)
    except (TypeError, ValueError):
        limit = 50
    return jsonify({'tasks': list_definition_tasks(get_db, kind, name, limit)})


@ai_execution_admin_bp.get('/perf/attempts/<aid>')
@require_permission('admin.ai_chat_admin')
def perf_attempt(aid):
    from db import get_db
    from utils.perf_analysis import load_attempt_metrics
    m = load_attempt_metrics(get_db, aid)
    if not m:
        return jsonify({'error': 'attempt not found'}), 404
    cov = {'wallMs': m['wallMs'], 'modelMs': m['modelMs'],
           'subagentWaitMs': m['subagentWaitMs'], 'idleMs': m['idleMs']}
    return jsonify({'attempt': {k: v for k, v in m.items()
                                if k not in ('turnDetails', 'subtasks', 'tools')},
                    'coverage': cov,
                    'turns': m.get('turnDetails') or [],
                    'subtasks': m.get('subtasks') or [],
                    'tools': m.get('tools') or {},
                    'completeness': m.get('completeness')})


@ai_execution_admin_bp.get('/perf/attempts/<aid>/diagnosis')
@require_permission('admin.ai_chat_admin')
def perf_attempt_diagnosis(aid):
    from db import get_db
    from utils.perf_analysis import load_attempt_metrics, diagnose, definition_overview
    m = load_attempt_metrics(get_db, aid)
    if not m:
        return jsonify({'error': 'attempt not found'}), 404
    # 同定义 P50 做对照（取该 attempt 的 manifest 定义）
    peer_p50 = None
    defs = [d for d in definition_overview(get_db)]
    # 用 attempt 的定义归属查 P50：manifests 里该 attempt 的第一个 skill/agent
    from utils.perf_analysis import _def_of_attempt
    dk, dn = _def_of_attempt(get_db, aid)
    if dk and dn:
        entry = next((d for d in defs
                      if d['defKind'] == dk and d['defName'] == dn), None)
        peer_p50 = entry['p50Ms'] if entry and entry['p50Ms'] else None
    return jsonify({'diagnoses': diagnose(m, peer_p50)})


@ai_execution_admin_bp.get('/perf/slow-tasks')
@require_permission('admin.ai_chat_admin')
def perf_slow_tasks():
    from db import get_db
    from utils.perf_analysis import list_slow_tasks
    try:
        limit = min(max(int(request.args.get('limit') or 10), 1), 50)
    except (TypeError, ValueError):
        limit = 10
    return jsonify({'tasks': list_slow_tasks(get_db, limit)})
```

并在 `perf_analysis.py` 追加诊断端点用到的小助手：

```python
def _def_of_attempt(db_ctx, attempt_id: str):
    from db import get_db as _default
    ctx = db_ctx or _default
    with ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT kind, name FROM ai_execution_manifests "
                "WHERE attempt_id = %s AND kind IN ('skill','agent') "
                "ORDER BY kind, name LIMIT 1", (attempt_id,))
            row = cur.fetchone()
    return (row[0], row[1]) if row else (None, None)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && python -m pytest tests/test_perf_analysis.py -q -p no:cacheprovider`
Expected: PASS（全部）

- [ ] **Step 5: 提交**

```bash
git add server/routes/ai_session_admin.py server/utils/perf_analysis.py server/tests/test_perf_analysis.py
git commit -m "feat(skillopt): 性能分析五端点——overview/定义任务/attempt分解/诊断/慢任务Top"
```

---

### Task 5: 前端 API 封装 + PerfView 壳（首屏 + 定义详情）

**Files:**
- Modify: `src/api/aiSkills.ts`（追加 perf 类型与函数）
- Create: `src/components/admin/skillopt/PerfView.vue`
- Create: `src/components/admin/skillopt/PerfSlowTop.vue`
- Test: `src/components/admin/skillopt/__tests__/PerfView.test.ts`

**Interfaces:**
- Consumes: Task 4 的 5 个端点。
- Produces（后续任务依赖）:
  - `src/api/aiSkills.ts` 导出：`PerfDefSummary`、`PerfTaskEntry`、`PerfAttemptDetail`、`Diagnosis` 类型；`perfOverview()`、`perfDefTasks(kind,name,limit?)`、`perfAttempt(id)`、`perfAttemptDiagnosis(id)`、`perfSlowTasks(limit?)`；
  - `PerfView.vue`：独立自足组件（自带数据加载），Task 8 只需 `<PerfView />` 挂入 ElTabPane。

- [ ] **Step 1: 写失败测试**

```typescript
// src/components/admin/skillopt/__tests__/PerfView.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { ElTabs, ElTabPane, ElTable, ElTableColumn } from 'element-plus'

vi.mock('@/api/aiSkills', () => ({
  perfOverview: vi.fn(async () => ({
    defs: [{ defKind: 'skill', defName: 'stock-analysis', tasks: 3,
             p50Ms: 20000, p95Ms: 90000, avgModelRatio: 0.6,
             lastActivity: '2026-10-09T10:00:00' }],
  })),
  perfDefTasks: vi.fn(async () => ({
    tasks: [{ attemptId: 'a1', sessionId: 's1', sourceType: 'batch', status: 'completed',
              startedAt: '2026-10-09T10:00:00', finishedAt: '2026-10-09T10:01:40',
              wallMs: 100000, modelMs: 60000, modelRatio: 0.6,
              subagentWaitMs: 30000, idleMs: 10000, turns: 3,
              tokensIn: 50000, tokensOut: 4000, subtaskCount: 1,
              completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 } }],
  })),
  perfSlowTasks: vi.fn(async () => ({ tasks: [] })),
  perfAttempt: vi.fn(),
  perfAttemptDiagnosis: vi.fn(),
}))

import PerfView from '../PerfView.vue'

describe('PerfView', () => {
  beforeEach(() => vi.clearAllMocks())

  it('首屏渲染定义清单与慢任务 Top 卡片', async () => {
    const w = mount(PerfView, { global: { components: { ElTabs, ElTabPane, ElTable, ElTableColumn } } })
    await new Promise(r => setTimeout(r, 0))
    expect(w.text()).toContain('stock-analysis')
    expect(w.text()).toContain('最慢任务')
  })

  it('点击定义加载任务列表并渲染墙钟', async () => {
    const w = mount(PerfView, { global: { components: { ElTabs, ElTabPane, ElTable, ElTableColumn } } })
    await new Promise(r => setTimeout(r, 0))
    await w.find('.perf-def-item').trigger('click')
    await new Promise(r => setTimeout(r, 0))
    expect(w.text()).toContain('100000')      // wallMs 出现在任务表格
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run src/components/admin/skillopt/__tests__/PerfView.test.ts`
Expected: FAIL（PerfView.vue 不存在）

- [ ] **Step 3: 实现 API 封装**

```typescript
// 追加到 src/api/aiSkills.ts（该文件已有 const ADMIN = '/ai/chat/admin'）
export interface PerfDefSummary {
  defKind: string; defName: string; tasks: number
  p50Ms: number; p95Ms: number; avgModelRatio: number | null
  lastActivity: string | null
}

export interface PerfTaskEntry {
  attemptId: string; sessionId: string; sourceType: string; status: string
  startedAt: string | null; finishedAt: string | null
  wallMs: number; modelMs: number; modelRatio: number
  subagentWaitMs: number; idleMs: number
  turns: number; tokensIn: number; tokensOut: number; subtaskCount: number
  completeness: { turnsWithoutDuration: number; runningSubtasks: number }
  defKind?: string; defName?: string
}

export interface PerfTurn {
  messageId: string; createdAt: string | null
  durationMs: number; tokensIn: number; tokensOut: number; preview: string
}

export interface PerfSubtask {
  subtaskId: string; agent: string | null; description: string | null
  status: string; startedAt: string | null; finishedAt: string | null
  wallMs: number
}

export interface PerfAttemptDetail {
  attempt: PerfTaskEntry & {
    requestedModel?: string | null; effectiveModel?: string | null
    batchReuseAgents?: string[] | null
  }
  coverage: { wallMs: number; modelMs: number; subagentWaitMs: number; idleMs: number }
  turns: PerfTurn[]
  subtasks: PerfSubtask[]
  tools: { errorCount: number; repeats: { tool: string; argsPreview: string; count: number }[] }
  completeness: { turnsWithoutDuration: number; runningSubtasks: number }
}

export interface Diagnosis {
  ruleId: string; severity: 'info' | 'warn'; text: string
  anchor: { type: 'turn' | 'subtask' | 'segment' | 'def'; ref: string }
}

export function perfOverview() {
  return get<{ defs: PerfDefSummary[] }>(`${ADMIN}/perf/overview`)
}
export function perfDefTasks(kind: string, name: string, limit = 50) {
  return get<{ tasks: PerfTaskEntry[] }>(
    `${ADMIN}/perf/defs/${encodeURIComponent(kind)}/${encodeURIComponent(name)}/tasks?limit=${limit}`)
}
export function perfAttempt(id: string) {
  return get<PerfAttemptDetail>(`${ADMIN}/perf/attempts/${encodeURIComponent(id)}`)
}
export function perfAttemptDiagnosis(id: string) {
  return get<{ diagnoses: Diagnosis[] }>(
    `${ADMIN}/perf/attempts/${encodeURIComponent(id)}/diagnosis`)
}
export function perfSlowTasks(limit = 10) {
  return get<{ tasks: PerfTaskEntry[] }>(`${ADMIN}/perf/slow-tasks?limit=${limit}`)
}
```

- [ ] **Step 4: 实现 PerfView.vue 与 PerfSlowTop.vue**

```vue
<!-- src/components/admin/skillopt/PerfView.vue -->
<template>
  <div class="perf-view" v-loading="loading">
    <ElAlert v-if="error" type="error" :closable="false" :title="error" />
    <template v-else>
      <!-- 首屏：定义清单 + 慢任务 Top -->
      <div class="perf-landing">
        <aside class="perf-side">
          <div class="perf-side__title">定义</div>
          <div v-if="!defs.length" class="perf-empty">暂无任务数据——执行 agent/skill 任务后自动汇总。</div>
          <div v-for="d in defs" :key="d.defKind + '/' + d.defName"
               class="perf-def-item" :class="{ active: isSel(d) }"
               @click="selectDef(d)">
            <div class="perf-def-item__name">{{ d.defName }}</div>
            <div class="perf-def-item__meta">{{ d.tasks }} 任务 · P50 {{ fmtMs(d.p50Ms) }} · P95 {{ fmtMs(d.p95Ms) }}</div>
          </div>
        </aside>
        <section class="perf-main">
          <PerfSlowTop v-if="!selected" :tasks="slowTasks" @open="openTask" />
          <template v-if="selected">
            <div class="perf-def-head">
              <ElButton link @click="selected = null">← 返回</ElButton>
              <span class="perf-def-head__name">{{ selected.defName }}</span>
              <span class="muted">{{ selected.defKind }}</span>
            </div>
            <div class="perf-cards">
              <div class="perf-card"><div class="num">{{ defTasks.length }}</div><div class="lbl">任务</div></div>
              <div class="perf-card"><div class="num">{{ fmtMs(selected.p50Ms) }}</div><div class="lbl">P50</div></div>
              <div class="perf-card"><div class="num">{{ fmtMs(selected.p95Ms) }}</div><div class="lbl">P95</div></div>
              <div class="perf-card"><div class="num">{{ pct(selected.avgModelRatio) }}</div><div class="lbl">模型占比均值</div></div>
            </div>
            <PerfTrendChart v-if="defTasks.length" :tasks="defTasks" @open="openTask" />
            <ElTable :data="defTasks" size="small" @row-click="(r: any) => openTask(r.attemptId)">
              <ElTableColumn prop="startedAt" label="时间" width="170" />
              <ElTableColumn prop="sourceType" label="来源" width="90" />
              <ElTableColumn prop="status" label="状态" width="100" />
              <ElTableColumn label="墙钟" width="110">
                <template #default="{ row }">{{ fmtMs(row.wallMs) }}</template>
              </ElTableColumn>
              <ElTableColumn label="模型占比" width="100">
                <template #default="{ row }">{{ pct(row.modelRatio) }}</template>
              </ElTableColumn>
              <ElTableColumn prop="tokensIn" label="输入 token" width="110" />
              <ElTableColumn prop="subtaskCount" label="子代理" width="80" />
            </ElTable>
          </template>
        </section>
      </div>
      <!-- 任务下钻 -->
      <PerfTaskDetail v-if="openAttemptId" :attempt-id="openAttemptId"
                      :peer-p50-ms="selected?.p50Ms ?? null" @close="openAttemptId = null" />
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { perfOverview, perfDefTasks, perfSlowTasks, type PerfDefSummary, type PerfTaskEntry } from '@/api/aiSkills'
import PerfSlowTop from './PerfSlowTop.vue'
import PerfTrendChart from './PerfTrendChart.vue'
import PerfTaskDetail from './PerfTaskDetail.vue'

const loading = ref(false)
const error = ref('')
const defs = ref<PerfDefSummary[]>([])
const slowTasks = ref<PerfTaskEntry[]>([])
const selected = ref<PerfDefSummary | null>(null)
const defTasks = ref<PerfTaskEntry[]>([])
const openAttemptId = ref<string | null>(null)

const isSel = (d: PerfDefSummary) =>
  selected.value?.defKind === d.defKind && selected.value?.defName === d.defName

function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return '-'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60_000)}m${Math.round((ms % 60_000) / 1000)}s`
}
function pct(r: number | null | undefined): string {
  return r == null ? '-' : `${Math.round(r * 100)}%`
}
defineExpose({ fmtMs, pct })

async function selectDef(d: PerfDefSummary) {
  selected.value = d
  try {
    defTasks.value = (await perfDefTasks(d.defKind, d.defName)).tasks
  } catch { defTasks.value = [] }
}
function openTask(id: string) { openAttemptId.value = id }

onMounted(async () => {
  loading.value = true
  try {
    const [ov, slow] = await Promise.all([perfOverview(), perfSlowTasks()])
    defs.value = ov.defs
    slowTasks.value = slow.tasks
  } catch (e: any) {
    error.value = e?.message || '加载性能数据失败'
  } finally { loading.value = false }
})
</script>

<style scoped lang="scss">
.perf-landing { display: flex; gap: 16px; }
.perf-side { width: 240px; flex-shrink: 0; border-right: 1px solid var(--el-border-color-lighter); padding-right: 12px; }
.perf-side__title { font-weight: 600; margin-bottom: 8px; }
.perf-def-item { padding: 8px; border-radius: 6px; cursor: pointer; }
.perf-def-item:hover, .perf-def-item.active { background: var(--el-fill-color-light); }
.perf-def-item__meta { font-size: 12px; color: var(--el-text-color-secondary); }
.perf-main { flex: 1; min-width: 0; }
.perf-empty { color: var(--el-text-color-secondary); padding: 24px 0; }
.perf-def-head { display: flex; align-items: center; gap: 8px; margin: 8px 0; }
.perf-def-head__name { font-weight: 600; font-size: 15px; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }
.perf-cards { display: flex; gap: 12px; margin: 12px 0; }
.perf-card { border: 1px solid var(--el-border-color-lighter); border-radius: 8px; padding: 10px 16px; min-width: 110px; }
.perf-card .num { font-size: 18px; font-weight: 600; }
.perf-card .lbl { font-size: 12px; color: var(--el-text-color-secondary); }
</style>
```

```vue
<!-- src/components/admin/skillopt/PerfSlowTop.vue -->
<template>
  <ElCard shadow="never" class="perf-slow-top">
    <template #header>最慢任务 Top{{ tasks.length }}</template>
    <div v-if="!tasks.length" class="muted">暂无数据</div>
    <div v-for="t in tasks" :key="t.attemptId" class="row" @click="emit('open', t.attemptId)">
      <span class="def">{{ t.defName || t.sessionId.slice(0, 8) }}</span>
      <span class="wall">{{ fmtMs(t.wallMs) }}</span>
      <span class="muted">{{ t.sourceType }} · {{ t.startedAt || '' }}</span>
    </div>
  </ElCard>
</template>
<script setup lang="ts">
import type { PerfTaskEntry } from '@/api/aiSkills'
defineProps<{ tasks: PerfTaskEntry[] }>()
const emit = defineEmits<{ (e: 'open', attemptId: string): void }>()
function fmtMs(ms: number): string {
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60_000)}m${Math.round((ms % 60_000) / 1000)}s`
}
</script>
<style scoped>
.row { display: flex; gap: 10px; padding: 6px 0; cursor: pointer; align-items: baseline; }
.row:hover { background: var(--el-fill-color-light); }
.def { font-weight: 600; min-width: 120px; }
.wall { font-variant-numeric: tabular-nums; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }
</style>
```

同时创建 `PerfTrendChart.vue` 与 `PerfTaskDetail.vue` 的**临时占位**（Task 6/7 替换实体）：

```vue
<!-- src/components/admin/skillopt/PerfTrendChart.vue（占位，Task 6 实现） -->
<template><div class="perf-trend-placeholder" /></template>
<script setup lang="ts">
import type { PerfTaskEntry } from '@/api/aiSkills'
defineProps<{ tasks: PerfTaskEntry[] }>()
const emit = defineEmits<{ (e: 'open', attemptId: string): void }>()
</script>
```

```vue
<!-- src/components/admin/skillopt/PerfTaskDetail.vue（占位，Task 7 实现） -->
<template><div /></template>
<script setup lang="ts">
defineProps<{ attemptId: string; peerP50Ms: number | null }>()
const emit = defineEmits<{ (e: 'close'): void }>()
</script>
```

- [ ] **Step 5: 跑测试确认通过**

Run: `npx vitest run src/components/admin/skillopt/__tests__/PerfView.test.ts`
Expected: PASS（2 例）

- [ ] **Step 6: 提交**

```bash
git add src/api/aiSkills.ts src/components/admin/skillopt/PerfView.vue src/components/admin/skillopt/PerfSlowTop.vue src/components/admin/skillopt/PerfTrendChart.vue src/components/admin/skillopt/PerfTaskDetail.vue src/components/admin/skillopt/__tests__/PerfView.test.ts
git commit -m "feat(skillopt): 性能视图壳+API 封装——定义清单/慢任务Top/任务表"
```

---

### Task 6: ECharts 封装 + PerfTrendChart 实体

**Files:**
- Create: `src/components/admin/skillopt/useEcharts.ts`
- Create: `src/components/admin/skillopt/chartOptions.ts`（纯函数 option 构造，可测）
- Modify: `src/components/admin/skillopt/PerfTrendChart.vue`（替换占位）
- Test: `src/components/admin/skillopt/__tests__/chartOptions.test.ts`

**Interfaces:**
- Produces: `useEcharts(elRef)` 返回 `{ setOption, dispose }`（懒加载 echarts）；`buildTrendOption(tasks: PerfTaskEntry[]): EChartsOption` 纯函数；Task 7 复用 `useEcharts` 与 `buildWaterfallOption`（Task 7 自建）。

- [ ] **Step 1: 写失败测试（只测纯函数 option 构造，ECharts mock 掉）**

```typescript
// src/components/admin/skillopt/__tests__/chartOptions.test.ts
import { describe, it, expect } from 'vitest'
import { buildTrendOption } from '../chartOptions'
import type { PerfTaskEntry } from '@/api/aiSkills'

const t = (over: Partial<PerfTaskEntry>): PerfTaskEntry => ({
  attemptId: 'a', sessionId: 's', sourceType: 'batch', status: 'completed',
  startedAt: '2026-10-09T10:00:00', finishedAt: null, wallMs: 100000,
  modelMs: 60000, modelRatio: 0.6, subagentWaitMs: 30000, idleMs: 10000,
  turns: 2, tokensIn: 1000, tokensOut: 100, subtaskCount: 1,
  completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 }, ...over })

describe('buildTrendOption', () => {
  it('墙钟柱系列 + 模型占比折线系列，x 轴为时间序', () => {
    const opt = buildTrendOption([t({}), t({ startedAt: '2026-10-09T10:05:00' })])
    expect(opt.xAxis.data).toHaveLength(2)
    const bar = opt.series.find((s: any) => s.name === '墙钟')
    const line = opt.series.find((s: any) => s.name === '模型占比')
    expect(bar.data).toEqual([100000, 100000])
    expect(line.data).toEqual([0.6, 0.6])
  })

  it('柱色按 sourceType 映射（__sources 供组件层 color 回调使用）', () => {
    const opt = buildTrendOption([t({ sourceType: 'interactive' })])
    expect((opt.series as any[])[0].__sources).toEqual(['interactive'])
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run src/components/admin/skillopt/__tests__/chartOptions.test.ts`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 useEcharts 与 chartOptions**

```typescript
// src/components/admin/skillopt/useEcharts.ts
import { ref, onMounted, onUnmounted, type Ref } from 'vue'

/** ECharts 懒加载封装：echarts 已在依赖里（md-editor 同源），动态 import
 *  避免进主包。init 失败（jsdom/无 canvas 测试环境）静默降级为 no-op，
 *  组件层不因此报错。setOption 前必须等 ready。 */
export function useEcharts(el: Ref<HTMLElement | null>) {
  let chart: any = null
  const ready = ref(false)

  onMounted(async () => {
    try {
      const echarts = await import('echarts')
      if (el.value) {
        chart = echarts.init(el.value)
        ready.value = true
      }
    } catch (e) {
      console.warn('[useEcharts] init skipped:', e)
    }
  })
  onUnmounted(() => { chart?.dispose(); chart = null })

  return {
    ready,
    setOption(opt: any) { chart?.setOption(opt) },
  }
}
```

```typescript
// src/components/admin/skillopt/chartOptions.ts
import type { PerfTaskEntry } from '@/api/aiSkills'

export function buildTrendOption(tasks: PerfTaskEntry[]) {
  const sorted = [...tasks].sort((a, b) =>
    (a.startedAt || '').localeCompare(b.startedAt || ''))
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['墙钟', '模型占比'] },
    grid: { left: 60, right: 50, top: 40, bottom: 60 },
    xAxis: { type: 'category', data: sorted.map(t => t.startedAt || t.attemptId) },
    yAxis: [
      { type: 'value', name: '墙钟(ms)' },
      { type: 'value', name: '模型占比', max: 1 },
    ],
    series: [
      // __sources 供 PerfTrendChart 组件层按 sourceType 上色（bar series 的
      // color 回调经 dataIndex 查它），ECharts 忽略未知键、纯函数可断言
      { name: '墙钟', type: 'bar', data: sorted.map(t => t.wallMs),
        __sources: sorted.map(t => t.sourceType) },
      { name: '模型占比', type: 'line', yAxisIndex: 1,
        data: sorted.map(t => t.modelRatio), smooth: true },
    ],
  }
}
```

```vue
<!-- src/components/admin/skillopt/PerfTrendChart.vue（实体，替换占位） -->
<template>
  <div ref="el" class="perf-trend" data-test="perf-trend" />
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import { useEcharts } from './useEcharts'
import { buildTrendOption } from './chartOptions'
import type { PerfTaskEntry } from '@/api/aiSkills'

const props = defineProps<{ tasks: PerfTaskEntry[] }>()
const emit = defineEmits<{ (e: 'open', attemptId: string): void }>()
const el = ref<HTMLElement | null>(null)
const { ready, setOption } = useEcharts(el)

watch([ready, () => props.tasks], () => {
  if (ready.value) setOption(buildTrendOption(props.tasks))
}, { immediate: true, deep: false })
</script>
<style scoped>
.perf-trend { height: 280px; }
</style>
```

- [ ] **Step 4: PerfView.test 顶部补 useEcharts mock**（PerfTrendChart 实体化后，PerfView 渲染会触发 echarts 动态 import——jsdom 下虽已 try/catch 降级，mock 掉更快更稳）：

```typescript
// 追加到 src/components/admin/skillopt/__tests__/PerfView.test.ts 的 vi.mock 区
vi.mock('../useEcharts', () => ({
  useEcharts: () => ({ ready: { value: false }, setOption: vi.fn() }),
}))
```

- [ ] **Step 5: 跑测试确认通过**

Run: `npx vitest run src/components/admin/skillopt/__tests__/chartOptions.test.ts src/components/admin/skillopt/__tests__/PerfView.test.ts`
Expected: PASS（chartOptions 2 例 + PerfView 2 例）

- [ ] **Step 6: 提交**

```bash
git add src/components/admin/skillopt/useEcharts.ts src/components/admin/skillopt/chartOptions.ts src/components/admin/skillopt/PerfTrendChart.vue src/components/admin/skillopt/__tests__/chartOptions.test.ts src/components/admin/skillopt/__tests__/PerfView.test.ts
git commit -m "feat(skillopt): 性能趋势图——ECharts 懒加载封装+纯函数 option 构造"
```

---

### Task 7: PerfTaskDetail 任务下钻（覆盖条 + 瀑布 + 两表）

**Files:**
- Modify: `src/components/admin/skillopt/PerfTaskDetail.vue`（替换占位）
- Modify: `src/components/admin/skillopt/chartOptions.ts`（追加 `buildWaterfallOption`）
- Test: `src/components/admin/skillopt/__tests__/chartOptions.test.ts`（追加）、`__tests__/PerfTaskDetail.test.ts`

**Interfaces:**
- Consumes: `perfAttempt(id)` / `perfAttemptDiagnosis(id)`、`useEcharts`、`Diagnosis` 类型。
- Produces: `buildWaterfallOption(detail: PerfAttemptDetail): any` 纯函数；emit `close`。Task 8 的 `PerfDiagnosisList` 从本组件接收 `diagnoses` 与 `anchorResolver`。

- [ ] **Step 1: 写失败测试**

```typescript
// 追加到 src/components/admin/skillopt/__tests__/chartOptions.test.ts
import { buildWaterfallOption } from '../chartOptions'
import type { PerfAttemptDetail } from '@/api/aiSkills'

const detail = (): PerfAttemptDetail => ({
  attempt: t({}) as any,
  coverage: { wallMs: 100000, modelMs: 60000, subagentWaitMs: 30000, idleMs: 10000 },
  turns: [{ messageId: 'm1', createdAt: '2026-10-09T10:00:00',
            durationMs: 60000, tokensIn: 5000, tokensOut: 500, preview: 'p' }],
  subtasks: [{ subtaskId: 'ses_1', agent: 'general', description: 'd',
               status: 'completed', startedAt: '2026-10-09T10:00:10',
               finishedAt: '2026-10-09T10:00:40', wallMs: 30000 }],
  tools: { errorCount: 0, repeats: [] },
  completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 },
})

describe('buildWaterfallOption', () => {
  it('时间轴系列含模型段与子代理段，y 为段类型、x 为绝对时间', () => {
    const opt = buildWaterfallOption(detail())
    const data = (opt.series as any[])[0].data
    expect(data.length).toBe(2)                       // 1 个模型段 + 1 个子代理段
    expect(data[0].name).toContain('模型')
    expect(data[1].name).toContain('子代理')
    expect(opt.xAxis.type).toBe('time')
  })
})
```

```typescript
// src/components/admin/skillopt/__tests__/PerfTaskDetail.test.ts
import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'

vi.mock('@/api/aiSkills', async (orig) => ({
  ...(await (orig as any)()),
  perfAttempt: vi.fn(async () => ({
    attempt: { attemptId: 'a1', wallMs: 100000, modelMs: 60000, modelRatio: 0.6,
               subagentWaitMs: 30000, idleMs: 10000, sourceType: 'batch',
               status: 'completed', turns: 1, tokensIn: 5000, tokensOut: 500,
               subtaskCount: 1, sessionId: 's1', startedAt: null, finishedAt: null,
               completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 } },
    coverage: { wallMs: 100000, modelMs: 60000, subagentWaitMs: 30000, idleMs: 10000 },
    turns: [{ messageId: 'm1', createdAt: null, durationMs: 60000,
              tokensIn: 5000, tokensOut: 500, preview: 'p' }],
    subtasks: [{ subtaskId: 'ses_1', agent: 'general', description: 'd',
                 status: 'completed', startedAt: null, finishedAt: null, wallMs: 30000 }],
    tools: { errorCount: 0, repeats: [] },
    completeness: { turnsWithoutDuration: 0, runningSubtasks: 0 },
  })),
  perfAttemptDiagnosis: vi.fn(async () => ({
    diagnoses: [{ ruleId: 'subagent_wait_dominant', severity: 'warn',
                  text: '68% 时间在等子代理',
                  anchor: { type: 'subtask', ref: 'ses_1' } }],
  })),
}))

vi.mock('../useEcharts', () => ({
  useEcharts: () => ({ ready: { value: false }, setOption: vi.fn() }),
}))

import PerfTaskDetail from '../PerfTaskDetail.vue'

describe('PerfTaskDetail', () => {
  it('渲染覆盖条三段、子代理表与诊断列表', async () => {
    const w = mount(PerfTaskDetail, {
      props: { attemptId: 'a1', peerP50Ms: 20000 },
    })
    await new Promise(r => setTimeout(r, 0))
    expect(w.find('[data-test="cov-model"]').attributes('style')).toContain('60%')
    expect(w.find('[data-test="cov-wait"]').attributes('style')).toContain('30%')
    expect(w.text()).toContain('general')
    expect(w.text()).toContain('68% 时间在等子代理')
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run src/components/admin/skillopt/__tests__/chartOptions.test.ts src/components/admin/skillopt/__tests__/PerfTaskDetail.test.ts`
Expected: FAIL（buildWaterfallOption 不存在 / 覆盖条无数据）

- [ ] **Step 3: 实现 buildWaterfallOption（追加到 chartOptions.ts）**

```typescript
export function buildWaterfallOption(detail: PerfAttemptDetail) {
  const t0ms = detail.attempt.startedAt ? Date.parse(detail.attempt.startedAt) : null
  const segs: { name: string; type: string; ref: string;
                start: number; end: number }[] = []
  for (const turn of detail.turns) {
    if (turn.durationMs == null || !turn.createdAt) continue
    const s = Date.parse(turn.createdAt)
    segs.push({ name: `模型轮次 ${(turn.durationMs / 1000).toFixed(1)}s`,
                type: 'model', ref: turn.messageId, start: s, end: s + turn.durationMs })
  }
  for (const st of detail.subtasks) {
    if (!st.startedAt) continue
    const s = Date.parse(st.startedAt)
    const e = st.finishedAt ? Date.parse(st.finishedAt) : s + st.wallMs
    segs.push({ name: `子代理 ${st.agent || ''} ${(st.wallMs / 1000).toFixed(1)}s`,
                type: 'subagent', ref: st.subtaskId, start: s, end: e })
  }
  return {
    tooltip: { formatter: (p: any) => p.name },
    grid: { left: 90, right: 30, top: 20, bottom: 40 },
    xAxis: { type: 'time' },
    yAxis: { type: 'category', data: ['子代理', '模型'], inverse: false },
    series: [{
      type: 'custom',
      renderItem: (params: any, api: any) => {
        const catIdx = api.value(0)
        const left = api.coord([api.value(1), catIdx])[0]
        const right = api.coord([api.value(2), catIdx])[0]
        const top = api.coord([api.value(1), catIdx])[1]
        return {
          type: 'rect',
          shape: { x: left, y: top - 12, width: Math.max(1, right - left), height: 24 },
          style: { fill: catIdx === 1 ? '#409eff' : '#e6a23c' },
        }
      },
      encode: { x: [1, 2], y: 0 },
      data: segs.map(s => ({
        name: s.name, value: [s.type === 'model' ? 1 : 0, s.start, s.end],
        __ref: s.ref,
      })),
    }],
  }
}
```

- [ ] **Step 4: 实现 PerfTaskDetail.vue（替换占位）**

```vue
<!-- src/components/admin/skillopt/PerfTaskDetail.vue -->
<template>
  <ElDrawer :model-value="true" title="任务耗时分解" size="62%" @close="emit('close')">
    <div v-loading="loading">
      <ElAlert v-if="error" type="error" :closable="false" :title="error" />
      <template v-else-if="detail">
        <!-- 覆盖占比条（spec §5.1）；同时挂诊断「定位」的 segment 锚点 -->
        <div class="cov" data-test="cov-bar" data-diag-anchor="segment:idle">
          <div class="cov__seg" data-test="cov-model" :style="segStyle('model')" />
          <div class="cov__seg" data-test="cov-wait" :style="segStyle('wait')" />
          <div class="cov__seg" data-test="cov-idle" :style="segStyle('idle')" />
        </div>
        <div class="cov__legend">
          <span><i class="dot dot--model" />模型 {{ pct(cov.modelRatio) }}</span>
          <span><i class="dot dot--wait" />子代理等待 {{ pct(ratio(cov.subagentWaitMs)) }}</span>
          <span><i class="dot dot--idle" />引擎间隙 {{ pct(ratio(cov.idleMs)) }}</span>
          <span class="muted">墙钟 {{ fmtMs(cov.wallMs) }}</span>
        </div>

        <div ref="waterfallEl" class="waterfall" data-test="waterfall" />

        <h4 data-diag-anchor="segment:tools">子代理与工具</h4>
        <ElTable :data="detail.subtasks" size="small" data-test="subtask-table">
          <ElTableColumn prop="agent" label="Agent" width="140" />
          <ElTableColumn prop="description" label="委派" min-width="200" show-overflow-tooltip />
          <ElTableColumn prop="status" label="状态" width="100" />
          <ElTableColumn label="墙钟" width="100">
            <template #default="{ row }">{{ fmtMs(row.wallMs) }}</template>
          </ElTableColumn>
        </ElTable>

        <h4 data-diag-anchor="segment:turns">模型轮次</h4>
        <ElTable :data="detail.turns" size="small" data-test="turn-table"
                 :row-class-name="turnRowClass">
          <ElTableColumn prop="createdAt" label="时间" width="170" />
          <ElTableColumn label="耗时" width="100">
            <template #default="{ row }">
              <span :class="{ 'slow-turn': (row.durationMs || 0) >= 30000 }">
                {{ row.durationMs == null ? '缺数据' : fmtMs(row.durationMs) }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn prop="tokensIn" label="输入 token" width="110" />
          <ElTableColumn prop="tokensOut" label="输出 token" width="110" />
          <ElTableColumn prop="preview" label="预览" min-width="220" show-overflow-tooltip />
        </ElTable>

        <!-- 二期占位（spec §5.1/§7）：工具级耗时区块，数据缺失即显示此文案 -->
        <h4>工具耗时</h4>
        <div class="muted" data-test="tool-perf-placeholder">
          该任务早于工具耗时采集上线（或无工具级数据），仅新跑任务可见每工具耗时分解。
        </div>

        <PerfDiagnosisList :diagnoses="diagnoses" />
      </template>
    </div>
  </ElDrawer>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import {
  perfAttempt, perfAttemptDiagnosis, type PerfAttemptDetail, type Diagnosis,
} from '@/api/aiSkills'
import { useEcharts } from './useEcharts'
import { buildWaterfallOption } from './chartOptions'
import PerfDiagnosisList from './PerfDiagnosisList.vue'

const props = defineProps<{ attemptId: string; peerP50Ms: number | null }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const loading = ref(false)
const error = ref('')
const detail = ref<PerfAttemptDetail | null>(null)
const diagnoses = ref<Diagnosis[]>([])
const waterfallEl = ref<HTMLElement | null>(null)
const { ready: wfReady, setOption: wfSet } = useEcharts(waterfallEl)

const cov = computed(() => detail.value?.coverage ?? { wallMs: 0, modelMs: 0, subagentWaitMs: 0, idleMs: 0 })
const ratio = (ms: number) => (cov.value.wallMs ? ms / cov.value.wallMs : 0)
const pct = (r: number) => `${Math.round(r * 100)}%`
function segStyle(kind: 'model' | 'wait' | 'idle') {
  const map = { model: ratio(cov.value.modelMs), wait: ratio(cov.value.subagentWaitMs), idle: ratio(cov.value.idleMs) }
  const color = { model: '#409eff', wait: '#e6a23c', idle: '#909399' }
  return { width: pct(map[kind]), background: color[kind] }
}
function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return '-'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60_000)}m${Math.round((ms % 60_000) / 1000)}s`
}
const turnRowClass = ({ row }: any) =>
  [(row.durationMs || 0) >= 30_000 ? 'slow-turn-row' : '',
   `diag-anchor-turn-${row.messageId}`].filter(Boolean).join(' ')

function renderWaterfall() {
  if (wfReady.value && detail.value) wfSet(buildWaterfallOption(detail.value))
}
watch([wfReady, detail], () => nextTick(renderWaterfall), { immediate: true })

onMounted(async () => {
  loading.value = true
  try {
    const [d, dg] = await Promise.all([
      perfAttempt(props.attemptId), perfAttemptDiagnosis(props.attemptId)])
    detail.value = d
    diagnoses.value = dg.diagnoses
  } catch (e: any) {
    error.value = e?.message || '加载任务分解失败'
  } finally { loading.value = false }
})
</script>

<style scoped lang="scss">
.cov { display: flex; height: 18px; border-radius: 9px; overflow: hidden; background: var(--el-fill-color); }
.cov__seg { height: 100%; }
.cov__legend { display: flex; gap: 16px; margin: 8px 0 16px; font-size: 13px; align-items: center; }
.dot { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; }
.dot--model { background: #409eff; } .dot--wait { background: #e6a23c; } .dot--idle { background: #909399; }
.waterfall { height: 180px; margin-bottom: 16px; }
h4 { margin: 18px 0 8px; }
.slow-turn { color: var(--el-color-danger); font-weight: 600; }
.muted { color: var(--el-text-color-secondary); }
</style>
```

> 注意：`PerfDiagnosisList.vue` 此时仍是**本任务内创建的最小占位**（props 接收 `diagnoses` 渲染文本列表），Task 8 实体化——否则本任务无法独立编译：

```vue
<!-- src/components/admin/skillopt/PerfDiagnosisList.vue（占位，Task 8 替换） -->
<template>
  <div class="diag-list">
    <div v-for="(d, i) in diagnoses" :key="i" class="diag" :class="d.severity">{{ d.text }}</div>
  </div>
</template>
<script setup lang="ts">
import type { Diagnosis } from '@/api/aiSkills'
defineProps<{ diagnoses: Diagnosis[] }>()
</script>
```

- [ ] **Step 5: 跑测试确认通过**

Run: `npx vitest run src/components/admin/skillopt/__tests__/chartOptions.test.ts src/components/admin/skillopt/__tests__/PerfTaskDetail.test.ts`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/components/admin/skillopt/PerfTaskDetail.vue src/components/admin/skillopt/PerfDiagnosisList.vue src/components/admin/skillopt/chartOptions.ts src/components/admin/skillopt/__tests__/chartOptions.test.ts src/components/admin/skillopt/__tests__/PerfTaskDetail.test.ts
git commit -m "feat(skillopt): 任务下钻——覆盖占比条/时间轴瀑布/轮次与子代理表"
```

---

### Task 8: PerfDiagnosisList 实体 + AiSkillOpt 挂载「性能分析」tab

**Files:**
- Modify: `src/components/admin/skillopt/PerfDiagnosisList.vue`（替换占位）
- Modify: `src/views/admin/AiSkillOpt.vue`（ElTabs 内加 `<ElTabPane label="性能分析" name="perf">`，import PerfView）
- Test: `src/components/admin/skillopt/__tests__/PerfDiagnosisList.test.ts`

**Interfaces:**
- Consumes: `Diagnosis` 类型；PerfTaskDetail（Task 7）以 `<PerfDiagnosisList :diagnoses="diagnoses" />` 使用。
- Produces: `PerfDiagnosisList` props `{ diagnoses: Diagnosis[] }`；「定位」点击按 `anchor.type` 滚动/高亮：turn→行 class `slow-turn-row` 同款脉冲（`document.querySelector('[data-diag-anchor="..."]')` 约定），subtask→子代理表行，segment→对应区块（tools/idle/turns 表头）。

- [ ] **Step 1: 写失败测试**

```typescript
// src/components/admin/skillopt/__tests__/PerfDiagnosisList.test.ts
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import PerfDiagnosisList from '../PerfDiagnosisList.vue'
import type { Diagnosis } from '@/api/aiSkills'

const ds: Diagnosis[] = [
  { ruleId: 'subagent_wait_dominant', severity: 'warn', text: '68% 时间在等子代理',
    anchor: { type: 'subtask', ref: 'ses_1' } },
  { ruleId: 'model_dominant', severity: 'info', text: '时间主要花在模型推理',
    anchor: { type: 'segment', ref: 'turns' } },
]

describe('PerfDiagnosisList', () => {
  it('按 severity 渲染 warn/info 样式与定位按钮', () => {
    const w = mount(PerfDiagnosisList, { props: { diagnoses: ds } })
    expect(w.findAll('.diag--warn')).toHaveLength(1)
    expect(w.findAll('.diag--info')).toHaveLength(1)
    expect(w.findAll('button[data-test="diag-locate"]')).toHaveLength(2)
  })

  it('定位点击触发锚点高亮（subtask ref）', async () => {
    document.body.innerHTML = '<div data-diag-anchor="subtask:ses_1">行</div>'
    const w = mount(PerfDiagnosisList, { props: { diagnoses: ds }, attachTo: document.body })
    await w.findAll('button[data-test="diag-locate"]')[0].trigger('click')
    const el = document.querySelector('[data-diag-anchor="subtask:ses_1"]')!
    expect(el.className).toContain('diag-flash')
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run src/components/admin/skillopt/__tests__/PerfDiagnosisList.test.ts`
Expected: FAIL（占位无按钮/样式类）

- [ ] **Step 3: 实现 PerfDiagnosisList（替换占位）**

```vue
<!-- src/components/admin/skillopt/PerfDiagnosisList.vue -->
<template>
  <div class="diag-list" data-test="diag-list">
    <h4>优化诊断</h4>
    <ElAlert v-if="!diagnoses.length" type="success" :closable="false"
             title="未发现明显耗时问题" />
    <div v-for="(d, i) in diagnoses" :key="i"
         class="diag" :class="`diag--${d.severity}`">
      <ElTag size="small" :type="d.severity === 'warn' ? 'warning' : 'info'">
        {{ d.severity === 'warn' ? '建议优化' : '说明' }}
      </ElTag>
      <span class="diag__text">{{ d.text }}</span>
      <ElButton v-if="anchorTarget(d)" link size="small" data-test="diag-locate"
                @click="locate(d)">定位</ElButton>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { Diagnosis } from '@/api/aiSkills'

const props = defineProps<{ diagnoses: Diagnosis[] }>()

const SEGMENT_LABEL: Record<string, string> = {
  tools: '工具调用', idle: '引擎间隙', turns: '模型轮次',
}

function anchorTarget(d: Diagnosis): string | null {
  if (d.anchor.type === 'subtask') return `subtask:${d.anchor.ref}`
  if (d.anchor.type === 'turn') return `turn:${d.anchor.ref}`
  if (d.anchor.type === 'segment') return `segment:${d.anchor.ref}`
  return null
}

function locate(d: Diagnosis) {
  // 回退链（对接约定见上）：turn 行锚 → segment 锚 → 逐类型兜底
  const candidates: string[] = []
  if (d.anchor.type === 'turn') candidates.push(`.diag-anchor-turn-${d.anchor.ref}`)
  for (const t of ['segment', 'subtask', 'turn']) {
    candidates.push(`[data-diag-anchor="${t}:${d.anchor.ref}"]`)
  }
  for (const sel of candidates) {
    const el = document.querySelector(sel)
    if (!el) continue
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    el.classList.remove('diag-flash')
    void (el as HTMLElement).offsetWidth
    el.classList.add('diag-flash')
    setTimeout(() => el.classList.remove('diag-flash'), 2000)
    return
  }
}
</script>

<style scoped lang="scss">
.diag { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-radius: 6px; margin-bottom: 6px; }
.diag--warn { background: var(--el-color-warning-light-9); }
.diag--info { background: var(--el-fill-color-light); }
.diag__text { flex: 1; font-size: 13px; }
:global(.diag-flash) { outline: 2px solid var(--el-color-primary); animation: diagPulse 1s ease 2; }
@keyframes diagPulse { 50% { opacity: 0.55; } }
</style>
```

锚点与 PerfTaskDetail（Task 7）的对接约定（Task 7 模板已带好属性）：
- `segment:*` → `PerfTaskDetail` 上的 `data-diag-anchor="segment:idle|tools|turns"`（覆盖条/子代理与工具表头/轮次表头）；
- `turn:<messageId>` → 轮次表行 class `diag-anchor-turn-<messageId>`（Task 7 的 `turnRowClass` 已拼）；
- `subtask:*` → 定位到子代理表头（`segment:tools` 同位），`locate` 回退链：先查 `.diag-anchor-turn-${ref}`（turn 锚），再查 `[data-diag-anchor="segment:${ref}"]`（segment 锚），均未命中则静默返回。
高亮样式统一用 `:global(.diag-flash)`（本组件 style 已定义）；`diag-anchor-turn-*` 行样式在 PerfTaskDetail 的 style 里补 `:global(.diag-anchor-turn)`（无独立样式，仅作 querySelector 锚点）。

- [ ] **Step 4: AiSkillOpt.vue 挂载 tab**

在 `src/views/admin/AiSkillOpt.vue`：
1. import 区加 `import PerfView from '@/components/admin/skillopt/PerfView.vue'`；
2. `<ElTabs v-model="activeTab">` 内、任务拟合 pane 之后加：

```html
      <!-- ── 性能分析（2026-10-09 spec）────────────────────────────── -->
      <ElTabPane label="性能分析" name="perf">
        <PerfView />
      </ElTabPane>
```

- [ ] **Step 5: 跑测试确认通过 + 全量前端回归**

Run: `npx vitest run src/components/admin/skillopt/ && npx vitest run 2>&1 | tail -4`
Expected: 新测试 PASS；全量绿（当前基线 1339+）

- [ ] **Step 6: 提交**

```bash
git add src/components/admin/skillopt/PerfDiagnosisList.vue src/views/admin/AiSkillOpt.vue src/components/admin/skillopt/__tests__/PerfDiagnosisList.test.ts
git commit -m "feat(skillopt): 诊断列表实体+性能分析 tab 挂载——定位高亮交互"
```

---

### Task 9: E2E（确定性 DB 播种 + 真 LLM 批链路）

**Files:**
- Create: `e2e/ai-full/skillopt-perf.spec.ts`

**Interfaces:**
- Consumes: `gotoWithAuth / tag`（`e2e/ai-full/helpers.ts`）、`adminToken/createBatch/uploadStaging/waitBatchTerminal/cleanupBatch`（`batch/batch-helpers.ts`）、`db_exec`（`batch/db_exec.py` 经 `dbSeed`/同款 python 调用，确定性用例播种 attempt/manifests/消息/子代理行）。
- 页面路径：`/admin/ai-execution?tab=skillopt`（SettingsTabShell normalize 落 SkillOpt tab，内存：admin 菜单合并）。

- [ ] **Step 1: 确定性用例（零 LLM）**

```typescript
/**
 * SkillOpt 任务性能分析（spec 2026-10-09）。
 * 用例 1 纯 DB 播种（零 LLM）：播种 attempt+manifests+消息+子代理 →
 * 性能 tab 渲染定义/趋势/下钻覆盖条与诊断列表。
 * 用例 2 真 LLM 批链路：建批跑终态后同一页面断言真实任务出现（@llm）。
 */
import { test, expect } from '@playwright/test'
import { gotoWithAuth, tag } from './helpers'
import {
  adminToken, createBatch, uploadStaging, waitBatchTerminal, cleanupBatch,
} from './batch/batch-helpers'
import { execFileSync } from 'node:child_process'

const PAGE = '/admin/ai-execution?tab=skillopt'

function dbExec(sql: string) {
  execFileSync('python', ['-c', `
import sys; sys.path.insert(0, 'server')
from db import get_db
with get_db() as conn:
    with conn.cursor() as cur:
        cur.execute("""${sql}""")
    conn.commit()
`], { cwd: 'E:/wsl/check/check-manage', encoding: 'utf-8' })
}

test('性能分析：DB 播种任务在视图中渲染（趋势+下钻+诊断）', async ({ page }) => {
  const mark = tag('perf')
  dbExec(`
    INSERT INTO users (id, username, password_hash, display_name, role)
    VALUES ('pf-e2e-user', 'pf_e2e', 'x', 'PF E2E', 'developer')
    ON CONFLICT (id) DO NOTHING;
  `)
  // 会话/批/attempt/manifests/消息/子代理（一次 SQL 搞定，id 用固定前缀便于清理）
  dbExec(`
    INSERT INTO ai_chat_batches (id, user_id, name, prompt, total)
    VALUES ('pf-e2e-batch', 'pf-e2e-user', '${mark}', 'p', 1);
    INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, batch_seq, opencode_session_id, workspace_path)
    VALUES ('pf-e2e-sess', 'pf-e2e-user', 'completed', 'pf-e2e-batch', 0, 'oc-pf-e2e', 'C:/tmp/pf-e2e');
    INSERT INTO ai_execution_attempts (id, session_id, source_type, source_id, status, started_at, finished_at)
    VALUES ('pf-e2e-att', 'pf-e2e-sess', 'batch', 'pf-e2e-batch', 'completed',
            NOW() - interval '500 seconds', NOW() - interval '100 seconds');
    INSERT INTO ai_execution_manifests (attempt_id, kind, name, path, content_hash, injected)
    VALUES ('pf-e2e-att', 'skill', 'perf-e2e-skill', 'C:/tmp/pf-e2e/SKILL.md', 'h', true);
    INSERT INTO ai_chat_messages (id, session_id, role, content, meta, created_at)
    VALUES ('pf-e2e-msg', 'pf-e2e-sess', 'assistant', '[{"type":"text","text":"done"}]'::jsonb,
            '{"durationMs":200000,"tokensInput":50000,"tokensOutput":3000}'::jsonb,
            NOW() - interval '500 seconds');
    INSERT INTO ai_chat_subtasks (id, root_session_id, agent, description, status, created_at, completed_at)
    VALUES ('ses-pf-e2e', 'pf-e2e-sess', 'general', 'e2e 委派', 'completed',
            NOW() - interval '400 seconds', NOW() - interval '200 seconds');
  `)
  try {
    await gotoWithAuth(page, PAGE)
    await page.getByText('性能分析').click()
    await page.getByText('perf-e2e-skill').click()
    await expect(page.locator('[data-test="perf-trend"]')).toBeVisible({ timeout: 10_000 })
    // 任务表首行（ElTable row-click 打开下钻；表列无 sessionId，按行点）
    await page.locator('.perf-view .el-table__row').first().click()
    await expect(page.locator('[data-test="cov-model"]')).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('[data-test="diag-list"]')).toBeVisible()
  } finally {
    dbExec(`
      DELETE FROM ai_chat_sessions WHERE id = 'pf-e2e-sess';
      DELETE FROM ai_execution_attempts WHERE id = 'pf-e2e-att';
      DELETE FROM ai_chat_batches WHERE id = 'pf-e2e-batch';
      DELETE FROM users WHERE id = 'pf-e2e-user';
    `)
  }
})
```

- [ ] **Step 2: 真 LLM 用例（同文件追加）**

```typescript
test('性能分析：真实批任务收敛后出现在性能视图（@llm）', async ({ page }) => {
  test.info().annotations.push({ type: 'llm' })
  test.setTimeout(420_000)
  const tk = await adminToken()
  const name = tag('perf-llm')
  const staged = await uploadStaging(tk, 'in.txt', 'hello\n', `e2e-perf-${Date.now()}`)
  const created = await createBatch(tk, {
    name,
    prompt: '读取 uploads/in.txt 并复述其内容，一句话即可。',
    files: [staged],
  })
  const bid = created.batchId || created.batch?.id
  try {
    await waitBatchTerminal(tk, bid, 360_000)
    await gotoWithAuth(page, PAGE)
    await page.getByText('性能分析').click()
    // 该批默认 agent 在 workspace 无 skill/agent 定义文件 → manifest 只有
    // guidance，慢任务 Top 行的归属显示回退为 sessionId（不含批名）——
    // 断言 Top 非空并点首行打开下钻（真链路的视图可用性）
    await expect(page.locator('.perf-slow-top .row').first())
      .toBeVisible({ timeout: 10_000 })
    await page.locator('.perf-slow-top .row').first().click()
    await expect(page.locator('[data-test="cov-bar"]')).toBeVisible({ timeout: 10_000 })
  } finally {
    await cleanupBatch(tk, bid)
  }
})
```

- [ ] **Step 3: 本地起栈跑 E2E**

Run: `npm run dev:all`（后台）→ `npx playwright test e2e/ai-full/skillopt-perf.spec.ts`
Expected: 2 passed（确定性例必须过；@llm 例受模型波动容忍：非 flaky 断言已收敛在「视图可用」层面）
跑完全量后停 dev 栈（注意孤儿进程按端口清，见仓库运维记忆）。

- [ ] **Step 4: 提交**

```bash
git add e2e/ai-full/skillopt-perf.spec.ts
git commit -m "test(skillopt): 性能分析 E2E——DB 播种确定性例+真 LLM 批链路例"
```

---

### Task 10: 全量回归与收尾

**Files:** 无新文件（回归 + 可能的小修）

- [ ] **Step 1: 停 3002 后端与 dev 栈（全量套件前置，见运维记忆）**

```bash
netstat -ano | grep LISTENING | grep -E ":3002|:5173"   # 按 PID taskkill //PID <pid> //T //F
```

- [ ] **Step 2: 后端全量串行**

Run: `cd server && python -m pytest -q -p no:cacheprovider`
Expected: 全绿 0 failed（基线 2496+ 新增）

- [ ] **Step 3: 前端全量 + 构建**

Run: `npx vitest run 2>&1 | tail -3 && npm run build 2>&1 | tail -3`
Expected: vitest 全绿；vue-tsc + vite build 通过

- [ ] **Step 4: 推送**

```bash
git push origin main
```

- [ ] **Step 5: 更新项目记忆**：SkillOpt 性能分析一期落地（覆盖切分口径、端点清单、二期工具采集待做——spec §7）。
