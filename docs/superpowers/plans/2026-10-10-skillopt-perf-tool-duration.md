# SkillOpt 任务性能分析二期（工具级耗时采集）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 工具调用落账带时长（started_at/duration_ms），性能分析解锁工具耗时聚合表、四类覆盖切分（工具执行从模型活跃中切出）与 tool_hotspot 诊断规则。

**Architecture:** 一个幂等迁移（agent_tool_calls 两列 + manifests(kind,name) 与 agent_tool_calls(root_session_id,occurred_at) 两索引，app.py 双注册）；`map_part` tool_use 透传原始 time（交互路径 start 来源）；agent_ledger 两条提取路径升级为 6 元组并写入新列；perf_analysis 侧 `_tool_aggregates` 带时长聚合（byTool/总时长/coverage 标志）、`coverage_split` 可选第四类 toolMs（仅 detail 路径使用）、`diagnose` 升级 repeated 带真实时长 + 新增 tool_hotspot；前端下钻工具耗时表实体化 + 覆盖条四段。

**Tech Stack:** Flask + psycopg2（迁移双注册）；Vue3 + Element Plus；pytest 串行 + vitest + Playwright。

**Spec:** `docs/superpowers/specs/2026-10-09-skillopt-perf-analysis-design.md` §3.2/§6/§7（本计划实现 §7 全部；§3.2 的 toolMs 第四类按本计划 Task 3 的裁决落地）

## Global Constraints

- 迁移双注册：migrations/ + app.py 启动块；护栏 `test_migration_boot_registration.py` 自动把关。
- 旧数据不回填：duration_ms/started_at 为 NULL 的行合法；聚合与规则必须容忍 NULL。
- 四类覆盖互斥且和 = wallMs；toolMs 语义 = 工具覆盖 ∩ 模型覆盖（工具发生在模型回合内），modelMs 相应扣除；**仅 detail 路径产出**（列表路径不多查）。
- `coverage_split` 第四参数可选（缺省 [] 行为不变，一期测试零改动）。
- 规则阈值常量：`TOOL_HOTSPOT_RATIO = 0.4`（新增）；既有 11 个常量名值不动。
- 端点出参 camelCase；`tools` 形状扩展为 `{errorCount, repeats:[{tool,argsPreview,count,totalMs}], byTool:[{tool,count,totalMs}], durationAvailable: bool}`（repeats.totalMs 可为 null）。
- 前端不新增 npm 依赖；数据缺失时工具耗时区显示降级文案（不估算）。
- 后端测试串行（server/ 下 `python -m pytest <file> -q -p no:cacheprovider`）；全量前停 3002 栈。
- 一提交一任务；提交信息中文 fix/feat/test 前缀。

---

### Task 1: 迁移 + app.py 双注册

**Files:**
- Create: `server/migrations/2026_10_10_tool_call_duration.py`
- Modify: `server/app.py`（启动迁移块区，2026_10_05 块之后按时间序插入）
- Test: `server/tests/test_perf_analysis.py`（追加 schema 断言用例）

**Interfaces:**
- Produces: `agent_tool_calls.started_at TIMESTAMPTZ`、`agent_tool_calls.duration_ms INTEGER`、索引 `idx_agent_tool_call_root ON agent_tool_calls(root_session_id, occurred_at)`、`idx_execution_manifest_kind_name ON ai_execution_manifests(kind, name)`——Task 2/3 依赖列名。

- [ ] **Step 1: 写失败测试**

```python
# 追加到 server/tests/test_perf_analysis.py
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
        assert names == {'idx_agent_tool_call_root', 'idx_execution_manifest_kind_name'}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && python -m pytest tests/test_perf_analysis.py -k ToolDurationSchema -q -p no:cacheprovider`
Expected: FAIL（列/索引不存在）

- [ ] **Step 3: 写迁移与注册**

```python
# server/migrations/2026_10_10_tool_call_duration.py
"""工具级耗时采集（SkillOpt 性能分析二期，spec §7）：

  agent_tool_calls.started_at / duration_ms   工具调用绝对开始与时长
                                              （来源 OC part state.time）
  idx_agent_tool_call_root                    性能聚合按 root+时窗取数
  idx_execution_manifest_kind_name            定义→任务关联按 (kind,name) 命中

全部幂等，可重复执行；旧数据不回填（duration_ms/started_at 为 NULL 合法）。

用法：python migrations/2026_10_10_tool_call_duration.py
或由 init_db 的 _run_dated_migrations / app.py 启动块调用 run()。
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db

DDL = [
    "ALTER TABLE agent_tool_calls "
    "ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ",
    "ALTER TABLE agent_tool_calls "
    "ADD COLUMN IF NOT EXISTS duration_ms INTEGER",
    "CREATE INDEX IF NOT EXISTS idx_agent_tool_call_root "
    "ON agent_tool_calls(root_session_id, occurred_at)",
    "CREATE INDEX IF NOT EXISTS idx_execution_manifest_kind_name "
    "ON ai_execution_manifests(kind, name)",
]


def run():
    with get_db() as conn:
        cur = conn.cursor()
        for stmt in DDL:
            cur.execute(stmt)
        conn.commit()
    print("tool call duration columns ready "
          "(started_at/duration_ms + root/manifest indexes).")


if __name__ == "__main__":
    run()
```

app.py 在 `2026_10_05_def_version_content_archive` 块之后插入（仿该块模式）：

```python
# 工具级耗时采集列（2026-10-10）：agent_tool_calls 加 started_at/duration_ms
# + root/manifest 聚合索引（SkillOpt 性能分析二期，spec §7）。随启动幂等执行。
try:
    _mp20 = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'migrations', '2026_10_10_tool_call_duration.py')
    _spec20 = _ilu.spec_from_file_location('_tool_duration_boot', _mp20)
    _m20 = _ilu.module_from_spec(_spec20)
    _spec20.loader.exec_module(_m20)
    _m20.run()
except Exception as _e:
    logging.warning('tool call duration migration on boot failed: %s', _e)
```

注意：测试库表由 conftest bootstrap（init_db 自动扫描）建列——护栏 `test_migration_boot_registration.py` 会验证 app.py 注册；本任务测试通过即证明两条路径都生效。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && python -m pytest tests/test_perf_analysis.py -q -p no:cacheprovider`
Expected: PASS（30 + 2 = 32；若 conftest 引导未扫到新迁移，手动 `python migrations/2026_10_10_tool_call_duration.py` 后重跑并如实记录）

- [ ] **Step 5: 提交**

```bash
git add server/migrations/2026_10_10_tool_call_duration.py server/app.py server/tests/test_perf_analysis.py
git commit -m "feat(skillopt): 工具耗时采集列——agent_tool_calls started_at/duration_ms+聚合索引"
```

---

### Task 2: 账本写路径提取时长

**Files:**
- Modify: `server/utils/opencode_parts.py`（map_part tool_use 透传 time）
- Modify: `server/utils/agent_ledger.py`（两条 extract 返回 6 元组；两条 INSERT 带新列）
- Test: `server/tests/test_agent_ledger.py`（既有文件，追加）

**Interfaces:**
- Consumes: `tool_duration_ms(state)`（ai_message_meta，已有）。
- Produces: `extract_from_parts(parts)` / `extract_from_part_map(part_map)` 返回 `(part_id, tool, args_text, state, started_at_dt|None, duration_ms|None)` 六元组（started_at_dt = tz-aware UTC datetime，由 `state.time.start` epoch ms 换算）；record_messages/record_state 落列 `started_at/duration_ms`。

- [ ] **Step 1: 写失败测试**

```python
# server/tests/test_agent_ledger.py（文件已存在则追加；导入区照既有风格）
import uuid
from datetime import datetime, timezone

from utils.agent_ledger import extract_from_parts, extract_from_part_map


def _raw_tool_part(part_id='p1', tool='bash', start=1_700_000_000_000, end=1_700_000_005_000):
    return {'id': part_id, 'type': 'tool', 'tool': tool,
            'state': {'status': 'completed', 'input': {'cmd': 'ls'},
                      'time': {'start': start, 'end': end}}}


class TestExtractDuration:
    def test_raw_shape_extracts_start_and_duration(self):
        rows = extract_from_parts([_raw_tool_part()])
        assert len(rows) == 1
        pid, tool, args, state, started, dur = rows[0]
        assert tool == 'bash' and state == 'completed'
        assert dur == 5_000
        assert started is not None
        assert started == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)

    def test_raw_shape_without_time_yields_nones(self):
        p = {'id': 'p2', 'type': 'tool', 'tool': 'read',
             'state': {'status': 'completed', 'input': {}}}
        pid, tool, args, state, started, dur = extract_from_parts([p])[0]
        assert started is None and dur is None

    def test_mapped_shape_extracts_start_and_duration(self):
        # map_part 透传后：tool_use 带 durationMs + time（Task 2 的 map_part 改动）
        mapped = {'type': 'tool_use', 'name': 'read', 'status': 'completed',
                  'input': {'filePath': 'a.py'}, 'durationMs': 2_500,
                  'time': {'start': 1_700_000_000_000, 'end': 1_700_000_002_500}}
        rows = extract_from_part_map({'p9': mapped})
        pid, tool, args, state, started, dur = rows[0]
        assert tool == 'read' and dur == 2_500
        assert started == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && python -m pytest tests/test_agent_ledger.py -k TestExtractDuration -q -p no:cacheprovider`
Expected: FAIL（解包数量/键不符）

- [ ] **Step 3: 实现**

opencode_parts.py 的 tool_use 返回 dict（非 task 分支）加一项：

```python
        return {
            'type': 'tool_use',
            'name': part.get('tool') or 'tool',
            'title': st.get('title') or '',
            'status': st.get('status'),
            'input': st.get('input'),
            'result': st.get('output') if st.get('output') is not None else st.get('result'),
            'durationMs': tool_duration_ms(st),
            # 工具级耗时采集（spec §7）：绝对 start/end 透传给账本交互路径
            'time': st.get('time'),
        }
```

agent_ledger.py：

```python
# 顶部 import 区
from datetime import datetime, timezone
from utils.ai_message_meta import tool_duration_ms  # 与该模块既有导入合并


def _start_dt(time_obj):
    """state.time.start（epoch ms）→ tz-aware UTC datetime；缺省 None。"""
    start = (time_obj or {}).get('start')
    if not start:
        return None
    return datetime.fromtimestamp(int(start) / 1000, tz=timezone.utc)
```

（两条 INSERT 的列清单已含 `agent`——核实过现文件即如此；只追加 `started_at, duration_ms`。）

`extract_from_parts` 循环改为：

```python
        state_obj = p.get('state') or {}
        state = (state_obj.get('status') or 'pending')
        args_text = args_to_text(state_obj.get('input'))[:MAX_ARGS_LEN]
        started = _start_dt(state_obj.get('time'))
        out[pid] = (str(pid), str(tool), args_text, str(state)[:20],
                    started, tool_duration_ms(state_obj))
```

`extract_from_part_map` 两个分支：

```python
        if t == 'tool':                      # 原始 OpenCode part 形状
            key = str(p.get('id') or pid)
            tool = p.get('tool')
            state_obj = p.get('state') or {}
            args_text = args_to_text(state_obj.get('input'))
            state = (state_obj.get('status') or 'pending')
            started = _start_dt(state_obj.get('time'))
            dur = tool_duration_ms(state_obj)
        elif t == 'tool_use':                # map_part 映射形状（已透传 time）
            key = str(pid)
            tool = p.get('name')
            args_text = args_to_text(p.get('input'))
            state = (p.get('status') or 'pending')
            started = _start_dt(p.get('time'))
            dur = p.get('durationMs')
            dur = int(dur) if isinstance(dur, (int, float)) else None
        else:
            continue
        if not tool:
            continue
        out[key] = (key, str(tool), args_text[:MAX_ARGS_LEN], str(state)[:20],
                    started, dur)
```

两条 INSERT（record_messages / record_state）同步：列清单加 `agent, started_at, duration_ms`（record_messages 的 INSERT 现缺 agent 列则保持其原状，只加 started_at/duration_ms——以其现文件实际列为准）；VALUES 元组追加对应值；`ON CONFLICT DO UPDATE` 加 `started_at = EXCLUDED.started_at, duration_ms = EXCLUDED.duration_ms`（WHERE 现有 DISTINCT 条件扩展或去掉——以「状态或时长变化即更新」为准：`WHERE agent_tool_calls.state IS DISTINCT FROM EXCLUDED.state OR agent_tool_calls.args_text IS DISTINCT FROM EXCLUDED.args_text OR agent_tool_calls.duration_ms IS DISTINCT FROM EXCLUDED.duration_ms`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && python -m pytest tests/test_agent_ledger.py tests/test_perf_analysis.py -q -p no:cacheprovider`
Expected: PASS（ledger 全部 + perf 32——extract 元组扩位如有既有调用方断言 4 元组，逐一更新为 6 元组解包）

- [ ] **Step 5: 提交**

```bash
git add server/utils/opencode_parts.py server/utils/agent_ledger.py server/tests/test_agent_ledger.py
git commit -m "feat(skillopt): 账本写路径提取工具时长——map_part 透传 time+两路径落列"
```

---

### Task 3: 覆盖切分第四类 toolMs（仅 detail 路径）

**Files:**
- Modify: `server/utils/perf_analysis.py`（coverage_split 可选参数；_attempt_metrics detail 分支取工具区间）
- Test: `server/tests/test_perf_analysis.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 duration_ms/started_at 列。
- Produces: `coverage_split(wall_start_ms, wall_end_ms, model_intervals, subagent_intervals, tool_intervals=[])` 返回加 `'toolMs'`（缺省参数时为 0，一期调用方零改动）；detail 指标 `coverage` 含 toolMs，`tools` 含 `byTool/durationAvailable`（Task 4 产出，此处先布线区间获取）。

- [ ] **Step 1: 写失败测试**

```python
# 追加 TestCoverageSplit
    def test_tool_intervals_split_from_model(self):
        # 工具 [200,600] 完全落在模型 [0,1000] 内 → toolMs=400，modelMs=600
        r = coverage_split(0, 1000, [(0, 1000)], [], [(200, 600)])
        assert r == {'wallMs': 1000, 'modelMs': 600, 'toolMs': 400,
                     'subagentWaitMs': 0, 'idleMs': 400}

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
```

以及 TestAttemptMetrics 追加（_seed_perf 后手工插带时长的工具调用）：

```python
    def test_detail_coverage_includes_tool_ms(self, db_conn, user_id):
        _bid, _sid, aid = _seed_perf(
            db_conn, user_id, model_turns=[(400_000, 10_000, 100, 0)])
        # 模型轮 [0,400]s；工具 [100,300]s 在其中 → toolMs 200s，纯模型 200s
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_tool_calls (oc_session_id, root_session_id,"
                " part_id, tool, args_text, state, occurred_at,"
                " started_at, duration_ms) VALUES (%s, %s, 'pd1', 'bash', '{}',"
                " 'completed', NOW() - interval '300 seconds',"
                " NOW() - interval '300 seconds', 200000)")
        db_conn.commit()
        m = load_attempt_metrics(db_conn, aid)
        assert m['coverage']['toolMs'] == 200_000
        assert m['modelMs'] == 200_000          # 400s 轮次 - 200s 工具
        assert m['subagentWaitMs'] == 0 and m['idleMs'] == 200_000
```

（注意：`_attempt_metrics` 返回的 `coverage` 键在 detail 下需新增——现返回把四类拆平在顶层；本任务统一为：顶层保留 modelMs/subagentWaitMs/idleMs/wallMs，detail 额外带 `coverage` dict（含 toolMs）供端点与前端——与既有端点拆包兼容：端点 `coverage` 从 detail 的 `coverage` 或顶层组装均可，实施时以「端点测试不改断言值」为准。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && python -m pytest tests/test_perf_analysis.py -k "tool_ms or tool_param" -q -p no:cacheprovider`
Expected: FAIL

- [ ] **Step 3: 实现**

coverage_split 加可选参数（互斥语义：tool 从 model 中切出）：

```python
def coverage_split(wall_start_ms: int, wall_end_ms: int,
                   model_intervals: list, subagent_intervals: list,
                   tool_intervals: list | None = None) -> dict:
    """attempt 墙钟 → 互斥覆盖时长。二期（spec §3.2/§7）：tool_intervals
    提供时产出第四类 toolMs = 工具覆盖 ∩ 模型覆盖（工具发生在模型回合内），
    modelMs 相应扣除——四类之和仍 = wallMs。缺省 tool 0，一期调用零改动。"""
    wall_ms = max(0, wall_end_ms - wall_start_ms)
    model = _union(model_intervals, wall_start_ms, wall_end_ms)
    sub = _union(subagent_intervals, wall_start_ms, wall_end_ms)
    tool_ivs = _union(tool_intervals or [], wall_start_ms, wall_end_ms)
    tool_ms = _covered_ms(_intersect(model, tool_ivs))
    model_ms = _covered_ms(_subtract(model, tool_ivs))
    wait = _subtract(sub, model)               # 子代理等待仍相对模型总覆盖
    wait_ms = _covered_ms(wait)
    idle_ms = max(0, wall_ms - model_ms - tool_ms - wait_ms)
    return {'wallMs': wall_ms, 'modelMs': model_ms, 'toolMs': tool_ms,
            'subagentWaitMs': wait_ms, 'idleMs': idle_ms}
```

新增 `_intersect(base, mask)`（与 _subtract 同骨架，取交）：

```python
def _intersect(base: list, mask: list) -> list:
    out: list = []
    for s, e in base:
        for ms, me in mask:
            if me <= s or ms >= e:
                continue
            out.append((max(s, ms), min(e, me)))
    return out
```

`_attempt_metrics` 的 with_detail 分支：detail 时查工具区间并传入（顶层与 coverage 同步带 toolMs；非 detail 路径 `tool_intervals=None` 零额外查询）：

```python
    tool_ivs: list | None = None
    if with_detail:
        cur.execute(
            "SELECT started_at, duration_ms FROM agent_tool_calls "
            "WHERE root_session_id = %s AND duration_ms IS NOT NULL"
            " AND started_at >= to_timestamp(%s/1000.0)"
            " AND started_at <= to_timestamp(%s/1000.0)",
            (sid, start_ms / 1000.0, end_ms / 1000.0))
        tool_ivs = [(int(_to_ms(s)), int(_to_ms(s)) + int(d))
                    for s, d in cur.fetchall() if s is not None]
    cov = coverage_split(start_ms, end_ms, model_ivs, sub_ivs, tool_ivs)
```

detail 返回 dict 加 `'coverage': {'wallMs': wall_ms, 'modelMs': cov['modelMs'], 'toolMs': cov['toolMs'], 'subagentWaitMs': cov['subagentWaitMs'], 'idleMs': cov['idleMs']}`（顶层 modelMs 等保留现值——非 detail 路径语义不变）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && python -m pytest tests/test_perf_analysis.py -q -p no:cacheprovider`
Expected: PASS（32 + 5 = 37）

- [ ] **Step 5: 提交**

```bash
git add server/utils/perf_analysis.py server/tests/test_perf_analysis.py
git commit -m "feat(skillopt): 覆盖切分第四类 toolMs——工具执行从模型活跃中切出（detail 路径）"
```

---

### Task 4: 工具时长聚合 + 规则升级（tool_hotspot / repeated 带时长）

**Files:**
- Modify: `server/utils/perf_analysis.py`
- Test: `server/tests/test_perf_analysis.py`

**Interfaces:**
- Consumes: Task 1 列；Task 3 detail 布线。
- Produces: detail `tools = {'errorCount', 'repeats':[{tool,argsPreview,count,totalMs|None}], 'byTool':[{tool,count,totalMs|None}], 'durationAvailable': bool}`；`diagnose` 规则：repeated_tool_calls 文本带真实总时长（可 得时）、新增 `tool_hotspot`（TOOL_HOTSPOT_RATIO=0.4，byTool 某 totalMs/wall > 阈值 → warn，anchor segment:tools）。

- [ ] **Step 1: 写失败测试**

```python
# TestAttemptMetrics 追加
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
                " NOW() - interval '300 seconds')")
        db_conn.commit()
        m = load_attempt_metrics(db_conn, aid)
        assert m['tools']['durationAvailable'] is False


# TestDiagnose 追加
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && python -m pytest tests/test_perf_analysis.py -k "duration or hotspot" -q -p no:cacheprovider`
Expected: FAIL

- [ ] **Step 3: 实现**

`_tool_aggregates` 重写（SQL 加 duration_ms；一次 fetchall 物化后单遍聚合出 byTool/重复参数键两套计数）：

```python
def _tool_aggregates(cur, sid: str, start_ms: int, end_ms: int) -> dict:
    cur.execute(
        "SELECT tool, args_text, state, duration_ms FROM agent_tool_calls "
        "WHERE root_session_id = %s AND occurred_at >= to_timestamp(%s/1000.0)"
        " AND occurred_at <= to_timestamp(%s/1000.0)",
        (sid, start_ms / 1000.0, end_ms / 1000.0))
    rows = cur.fetchall()
    by_tool: dict = {}
    key_counts: dict = {}
    errors = 0
    any_duration = False
    for tool, args_text, state, dur in rows:
        if state == 'error':
            errors += 1
        entry = by_tool.setdefault(tool, {'count': 0, 'totalMs': 0,
                                          'hasDuration': False})
        entry['count'] += 1
        if dur is not None:
            any_duration = True
            entry['hasDuration'] = True
            entry['totalMs'] += int(dur)
        key = f'{tool}|{_norm_args(args_text)}'
        kc = key_counts.setdefault(key, {'count': 0, 'totalMs': 0,
                                         'hasDuration': False, 'tool': tool,
                                         'argsPreview': _norm_args(args_text)})
        kc['count'] += 1
        if dur is not None:
            kc['hasDuration'] = True
            kc['totalMs'] += int(dur)
    by = [{'tool': t, 'count': v['count'],
           'totalMs': v['totalMs'] if v['hasDuration'] else None}
          for t, v in sorted(by_tool.items(), key=lambda kv: -kv[1]['totalMs'])]
    repeats = [{'tool': v['tool'], 'argsPreview': v['argsPreview'],
                'count': v['count'],
                'totalMs': v['totalMs'] if v['hasDuration'] else None}
               for v in key_counts.values() if v['count'] >= REPEAT_TOOL_COUNT]
    repeats.sort(key=lambda r: -(r['totalMs'] or 0))
    return {'errorCount': errors, 'repeats': repeats, 'byTool': by,
            'durationAvailable': any_duration}
```

规则引擎（diagnose 内）：阈值区加 `TOOL_HOTSPOT_RATIO = 0.4`；repeated 分支与新增 hotspot 分支：

```python
    tools = breakdown.get('tools') or {}
    tool_total = {t['tool']: t.get('totalMs') for t in tools.get('byTool') or []}
    for r in tools.get('repeats') or []:
        total = r.get('totalMs')
        suffix = f'（共约 {total / 1000:.1f}s）' if total else ''
        add('repeated_tool_calls', 'warn',
            f'{r["tool"]} 同一参数重复 {r["count"]} 次{suffix}'
            '——考虑在指令里要求一次读全/批量操作', 'segment', 'tools')
    if wall and tools.get('durationAvailable'):
        for t in tools.get('byTool') or []:
            total = t.get('totalMs')
            if total and total / wall > TOOL_HOTSPOT_RATIO:
                add('tool_hotspot', 'warn',
                    f'{round(total / wall * 100)}% 时间在 {t["tool"]}'
                    f'（共 {total / 1000:.1f}s / {t["count"]} 次）',
                    'segment', 'tools')
```

（repeated 原分支里对 tools.get('repeats') 的现有实现替换为上式；tool_error_storm 与 engine_overhead 分支不动。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && python -m pytest tests/test_perf_analysis.py -q -p no:cacheprovider`
Expected: PASS（37 + 4 = 41）

- [ ] **Step 5: 提交**

```bash
git add server/utils/perf_analysis.py server/tests/test_perf_analysis.py
git commit -m "feat(skillopt): 工具时长聚合与 tool_hotspot 规则——byTool/repeats 带真实时长"
```

---

### Task 5: 端点出参扩展 + 契约测试

**Files:**
- Modify: `server/routes/ai_session_admin.py`（perf_attempt 的 tools/coverage 组装）
- Test: `server/tests/test_perf_analysis.py`（既有 TestPerfEndpoints 断言微调）

**Interfaces:**
- Produces: `GET /perf/attempts/<id>` 的 `coverage` 变四键+toolMs、`tools` 变新形状（Task 4）；`attempt` 顶层新增 `toolMs`。

- [ ] **Step 1: 更新/补契约断言（先红）**

在 TestPerfEndpoints 的 attempt 用例追加：

```python
        assert 'toolMs' in body['coverage']
        assert set(body['tools']) >= {'errorCount', 'repeats', 'byTool',
                                      'durationAvailable'}
```

Run: `cd server && python -m pytest tests/test_perf_analysis.py -k PerfEndpoints -q -p no:cacheprovider`
Expected: FAIL（端点 coverage 组装还缺 toolMs / tools 旧形状）

- [ ] **Step 2: 端点实现**

`perf_attempt` 组装修正：

```python
    cov = m.get('coverage') or {
        'wallMs': m['wallMs'], 'modelMs': m['modelMs'],
        'subagentWaitMs': m['subagentWaitMs'], 'idleMs': m['idleMs'],
        'toolMs': 0}
    return jsonify({'attempt': {**{k: v for k, v in m.items()
                                   if k not in ('turnDetails', 'subtasks', 'tools', 'coverage')},
                                 'toolMs': cov.get('toolMs', 0)},
                    'coverage': cov,
                    'turns': m.get('turnDetails') or [],
                    'subtasks': m.get('subtasks') or [],
                    'tools': m.get('tools') or {},
                    'completeness': m.get('completeness')})
```

- [ ] **Step 3: 跑测试确认通过**

Run: `cd server && python -m pytest tests/test_perf_analysis.py -q -p no:cacheprovider`
Expected: PASS（41+1 断言全绿）

- [ ] **Step 4: 提交**

```bash
git add server/routes/ai_session_admin.py server/tests/test_perf_analysis.py
git commit -m "feat(skillopt): attempt 端点出参扩展——coverage.toolMs+tools 新形状"
```

---

### Task 6: 前端下钻升级（覆盖条四段 + 工具耗时表实体化）

**Files:**
- Modify: `src/api/aiSkills.ts`（tools/coverage 类型扩展）
- Modify: `src/components/admin/skillopt/PerfTaskDetail.vue`（覆盖条四段；工具耗时表替换占位块）
- Test: `src/components/admin/skillopt/__tests__/PerfTaskDetail.test.ts`（追加）

**Interfaces:**
- Consumes: Task 5 端点形状；`fmtMs/pct`（./format）。
- Produces: 工具耗时区块 data-test="tool-perf-table"；无时长数据时保留降级文案（data-test="tool-perf-placeholder" 文案改「本任务无工具级耗时数据」）。

- [ ] **Step 1: 更新类型与失败测试**

aiSkills.ts：

```typescript
export interface PerfToolAgg {
  errorCount: number
  repeats: { tool: string; argsPreview: string; count: number; totalMs: number | null }[]
  byTool: { tool: string; count: number; totalMs: number | null }[]
  durationAvailable: boolean
}
```

（PerfAttemptDetail.tools 类型改为 `PerfToolAgg`；coverage 加 `toolMs: number`。）

PerfTaskDetail.test：mock tools 换新形状 + byTool 两行，追加用例：

```typescript
  it('工具耗时表渲染 byTool 聚合（时长/占比），覆盖条含工具段', async () => {
    // mock tools: { errorCount: 1, durationAvailable: true,
    //   byTool: [{tool:'bash',count:2,totalMs:40000},{tool:'read',count:3,totalMs:10000}],
    //   repeats: [...] }，coverage 含 toolMs: 20000（wall 100000）
    // 断言：[data-test="tool-perf-table"] 可见、含 'bash' 与 '40.0s'、
    //       cov-tool 段 style 含 '20%'
  })
```

Run: `npx vitest run src/components/admin/skillopt/__tests__/PerfTaskDetail.test.ts`
Expected: FAIL（无 cov-tool 段/占位块还在）

- [ ] **Step 2: 实现 PerfTaskDetail**

- 覆盖条加第四段 `<div class="cov__seg" data-test="cov-tool" :style="segStyle('tool')" />`；segStyle map 加 `tool: ratio(cov.value.toolMs ?? 0)`、color `#67c23a`；图例加「工具 X%」（有 toolMs>0 才显示）。
- 占位块替换为：

```vue
        <h4>工具耗时</h4>
        <ElTable v-if="tools?.durationAvailable && tools.byTool?.length"
                 :data="tools.byTool" size="small" data-test="tool-perf-table">
          <ElTableColumn prop="tool" label="工具" width="160" />
          <ElTableColumn prop="count" label="调用" width="80" />
          <ElTableColumn label="总时长" width="110">
            <template #default="{ row }">{{ row.totalMs == null ? '-' : fmtMs(row.totalMs) }}</template>
          </ElTableColumn>
          <ElTableColumn label="占墙钟" width="100">
            <template #default="{ row }">
              {{ row.totalMs == null ? '-' : pct(cov.wallMs ? row.totalMs / cov.wallMs : 0) }}
            </template>
          </ElTableColumn>
        </ElTable>
        <div v-else class="muted" data-test="tool-perf-placeholder">
          本任务无工具级耗时数据（早于采集上线或无工具调用）。
        </div>
```

（`const tools = computed(() => detail.value?.tools)`；script 里保留现有结构。）

- [ ] **Step 3: 跑测试确认通过**

Run: `npx vitest run src/components/admin/skillopt/ && npx vue-tsc --noEmit`
Expected: 全绿、0 类型错误

- [ ] **Step 4: 提交**

```bash
git add src/api/aiSkills.ts src/components/admin/skillopt/PerfTaskDetail.vue src/components/admin/skillopt/__tests__/PerfTaskDetail.test.ts
git commit -m "feat(skillopt): 工具耗时表实体化+覆盖条四段——byTool 聚合与降级文案"
```

---

### Task 7: E2E 升级 + 回归收尾

**Files:**
- Modify: `e2e/ai-full/skillopt-perf.spec.ts`
- 无新文件（回归）

- [ ] **Step 1: 确定性用例播种工具时长并断言**

种子 SQL 追加（attempt 时窗内；**不显式插 id**——BIGSERIAL 自增；`(oc_session_id, part_id)` 唯一索引使复跑必撞，故 PF_CLEANUP 必须先清工具调用行）：

```sql
    DELETE FROM agent_tool_calls WHERE oc_session_id = 'oc-pf-e2e';
    INSERT INTO agent_tool_calls (oc_session_id, root_session_id, part_id,
      tool, args_text, state, occurred_at, started_at, duration_ms) VALUES
    ('oc-pf-e2e', 'pf-e2e-sess', 'pt1', 'bash', '{}', 'completed',
     NOW() - interval '350 seconds', NOW() - interval '350 seconds', 150000),
    ('oc-pf-e2e', 'pf-e2e-sess', 'pt2', 'bash', '{}', 'completed',
     NOW() - interval '300 seconds', NOW() - interval '300 seconds', 50000);
```

（agent_tool_calls 对 sessions **无 FK**——会话级联清不掉它，DELETE 必须显式；一期 T2 的 UniqueViolation 教训同源。）

下钻断言追加：

```typescript
    await expect(page.locator('[data-test="tool-perf-table"]')).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('[data-test="tool-perf-table"]')).toContainText('bash')
```

- [ ] **Step 2: 起栈跑两条用例（确定性必过；@llm 按一期惯例）**

Run: `npm run dev:all`（后台）→ `npx playwright test e2e/ai-full/skillopt-perf.spec.ts`
Expected: 2 passed；跑完按端口清栈（netstat + taskkill //PID //T //F）

- [ ] **Step 3: 双端全量回归**

Run: 停栈后 `cd server && python -m pytest -q -p no:cacheprovider`；`npx vitest run`；`npm run build`
Expected: 全绿（后端 ~2533；前端 ~1361）

- [ ] **Step 4: 提交 + 推送**

```bash
git add e2e/ai-full/skillopt-perf.spec.ts
git commit -m "test(skillopt): 工具耗时 E2E——种子带时长工具调用断言聚合表"
git push origin main
```

- [ ] **Step 5: 记忆更新**

check-manage-skillopt-perf-analysis.md：二期完成段（列/接线/四类覆盖/规则/页面）+ 保留项。
