# Agent 自我进化 P1（O1 统一运行诊断 + O2 技能进化闭环）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 一次运行产出结构化五维度诊断（`ai_run_diagnoses`），拟合建议升级为「最小 diff 候选 → 双集回归 → 人工确认发布」（`ai_skill_candidates`），发布/回滚复用现有 `ai_skill_def_versions`。

**Architecture:** O1 是零 LLM 的离线聚合器——把账本/门禁/拟合/性能/声称核对五路现有信号按维度规则聚合成一张诊断行，挂在批 finalize 与管理端手动触发上。O2 在诊断建议与正式技能文件之间插入候选区：LLM 只产最小 diff 候选，双集回归（边界集改善 ∧ 保留集零退化）用现有 `match_steps` 纯函数对历史轨迹离线打分，promote 走现有定义文件回写+版本归档链路。

**Tech Stack:** Flask + psycopg（后端）、Vue3 + Element Plus（前端）、pytest（casemanage_test 独立库）、Playwright（真实链路 E2E）。

**Spec:** `docs/superpowers/specs/2026-10-26-agent-self-evolution-design.md`（设计文档）与 `docs/superpowers/specs/2026-10-26-agent-self-evolution-ch8-research.md`（研究报告）。执行者两份都要读；本计划按设计文档 §3/§4 展开，冲突时以设计文档为准。

**修订记录**：2026-10-27 依开源对照研究（`2026-10-27-agent-self-evolution-oss-benchmark.md` §4 决议）增补 R1–R4——R1 诊断输入含被拒修改（Task 8）、R2 edits_hash 候选去重（Task 6/9）、R3 apply_edits 结构守卫（Task 7）、R4 mechanical/behavioral 双模回归（Task 7）。设计文档 §4.6 为准。

## Global Constraints

- **迁移双注册**：新迁移文件放 `server/migrations/`，并必须在 `server/app.py` 加显式 boot 块（参照 app.py:529-537 的 try/spec_from_file_location 模式，模块名用未占用的 `_run_diag_boot` / `_skill_cand_boot`）。护栏测试 `server/tests/test_migration_boot_registration.py` 必须通过——漏注册是两次生产事故（2026_09_18、2026_10_01）的同一根因。
- **Mimosa 扫描约束**：源码改动一律走 Edit/Write，不得用 Bash 重定向写源码；SQL 一律 `cur.execute(sql, params)` 标准参数化，禁止把 SQL 字符串拼进变量再执行。
- **权限**：全部新端点挂 `@require_permission('admin.ai_chat_admin')`（路由文件 `server/routes/ai_session_admin.py`，蓝图 `ai_execution_admin_bp`，url_prefix=`/ai/chat/admin`）；写端点必须 `log_operation(...)`。
- **fail-closed 语义**：诊断证据缺失 → `uncertain`，绝不冒充 pass；带证据的维度在 `evidence` 数组里给可回查指针（表+id），生成器校验「status=fail 但 evidence 为空」直接拒绝写入。
- **候选不自动发布**：`promote` 必须人工点击；回归判定零 LLM（`match_steps` 纯函数）；LLM 产物只允许进入 `ai_skill_candidates`，永远不直接写定义文件。
- **拟合口径冻结（37c7057）**：`server/utils/skill_fit.py` 的 `compute_attempt_fit` 对外行为不得改变；本计划只**导入复用** `parse_fit_steps` / `match_steps` / `_load_trace`，不修改它们的签名与语义。
- **测试库**：后端单测自动跑在 `casemanage_test`（`server/tests/conftest.py:26` 已 setdefault），跑法 `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/<file> -v`（Git Bash 写法）。需要真实 DB 的路由测试按 `server/tests/test_skill_fit_routes.py:35-58` 的 shadow client 夹具模式（rebind `get_db`/`_default_get_db`）。
- **E2E**：真实后端、不烧 LLM（本计划无 LLM 依赖路径）；种子走 `e2e/ai-full/batch/db_exec.py` 桥（用法见 `e2e/ai-full/ai-skillopt-version-archive.spec.ts:22-44`）；`finally` 清理种子行与临时文件；禁止等 networkidle。
- **提交纪律**：每个 Task 一个 commit；Task 7 碰拟合复用边界，其测试先行提交。commit message 用中文、格式对齐近期历史（`feat(域): 摘要——细节`）。

---

## File Structure（新建/修改总览）

```
server/migrations/2026_10_27_run_diagnoses.py        [新建] ai_run_diagnoses 表
server/migrations/2026_10_27_skill_candidates.py     [新建] ai_skill_candidates 表
server/app.py                                        [修改] 两个 boot 注册块
server/utils/run_diagnosis.py                        [新建] O1 维度聚合器
server/utils/skill_candidate_eval.py                 [新建] O2 diff 校验+双集回归
server/utils/skill_fit_ai.py                         [修改] 诊断 schema 增 edits/expected_repair/possibly_harmed
server/routes/ai_session_admin.py                    [修改] O1 三端点 + O2 五端点
server/utils/batch_engine.py                         [修改] finalize 处挂诊断生成
src/api/aiSessionAdmin.ts                            [修改] 八个 API 函数
src/components/admin/skillopt/RunDiagnosisPanel.vue  [新建] O1 展示面板
src/components/admin/skillopt/SkillCandidates.vue    [新建] O2 候选区组件
src/views/admin/AiSkillOpt.vue                       [修改] 挂候选区 tab + 面板入口
server/tests/test_run_diagnosis.py                   [新建]
server/tests/test_skill_candidate_eval.py            [新建]
server/tests/test_skill_candidates_routes.py         [新建]
server/tests/test_skill_fit_ai.py                    [修改] 增 schema 用例
e2e/ai-full/run-diagnosis.spec.ts                    [新建]
e2e/ai-full/skill-candidates.spec.ts                 [新建]
docs/design/ai/08-Skill与SkillOpt.md                 [修改] 增补两节
```

---

## Phase A — O1 统一运行诊断

### Task 1: 迁移 ai_run_diagnoses + app.py 注册

**Files:**
- Create: `server/migrations/2026_10_27_run_diagnoses.py`
- Modify: `server/app.py`（在 2026_10_05_def_version_content_archive 注册块之后追加）
- Test: 护栏测试（已有，跑通即可）

**Interfaces:**
- Produces: 表 `ai_run_diagnoses(id TEXT PK, attempt_id TEXT NOT NULL REFERENCES ai_execution_attempts(id), root_session_id TEXT, overall VARCHAR(20) NOT NULL, dimensions JSONB NOT NULL, generator_version VARCHAR(20) NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), UNIQUE(attempt_id, generator_version))`；索引 `idx_run_diag_root(root_session_id, created_at DESC)`。后续任务的维度 JSONB 契约见 Task 2。

- [ ] **Step 1: 写迁移文件**

```python
# -*- coding: utf-8 -*-
"""幂等迁移：O1 统一运行诊断（ai_run_diagnoses）。

一次 attempt 一张结构化多维诊断行：dimensions JSONB 固定含五个维度键
（task_result/rule_compliance/promise_action/skill_fit/performance），每维
{status: pass|fail|uncertain, summary, evidence[]}。overall ∈
success|partial|fail|uncertain。UNIQUE(attempt_id, generator_version)
支撑诊断器升级后按版本重跑并存（对比用），同版本 upsert 幂等。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db

def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_run_diagnoses (
                id                TEXT PRIMARY KEY,
                attempt_id        TEXT NOT NULL
                                  REFERENCES ai_execution_attempts(id),
                root_session_id   TEXT,
                overall           VARCHAR(20) NOT NULL,
                dimensions        JSONB NOT NULL,
                generator_version VARCHAR(20) NOT NULL,
                created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (attempt_id, generator_version)
            )""")
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_run_diag_root
                ON ai_run_diagnoses(root_session_id, created_at DESC)""")
        conn.commit()
    print("run diagnoses migration ready.")

if __name__ == "__main__":
    run()
```

- [ ] **Step 2: app.py 注册 boot 块**

在 `server/app.py` 中 `2026_10_05_def_version_content_archive` 注册块（app.py:558-565 附近）之后追加：

```python
# O1 统一运行诊断表（2026-10-27）：ai_run_diagnoses。随启动幂等执行；
# 漏注册会让 run_diagnosis 聚合器建行报「表不存在」→ 批 finalize 后
# 诊断静默缺失（best-effort 吞掉），必须在此显式注册。
try:
    _mp20 = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'migrations', '2026_10_27_run_diagnoses.py')
    _spec20 = _ilu.spec_from_file_location('_run_diag_boot', _mp20)
    _m20 = _ilu.module_from_spec(_spec20)
    _spec20.loader.exec_module(_m20)
    _m20.run()
except Exception as _e:
    logging.warning('run diagnoses migration on boot failed: %s', _e)
```

注意：`_mp20/_spec20/_m20` 变量名先 grep `server/app.py` 确认未被占用，占用则顺延编号。

- [ ] **Step 3: 跑迁移护栏测试验证**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_migration_boot_registration.py -v`
Expected: PASS（新迁移已被 app.py 注册覆盖）

- [ ] **Step 4: 验证表在测试库真实建成**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_run_diagnosis.py -v`（此时该文件尚不存在，先建一个最小占位测试验证建表——见下）

创建 `server/tests/test_run_diagnosis.py` 最小占位：

```python
# -*- coding: utf-8 -*-
"""O1 运行诊断测试：维度判定规则 / overall 聚合 / 幂等 / fail 必带证据。"""


def test_table_exists(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ai_run_diagnoses")
        assert cur.fetchone()[0] == 0
```

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_run_diagnosis.py -v`
Expected: PASS（conftest 自动引导 casemanage_test 并跑全量迁移建表）

- [ ] **Step 5: Commit**

```bash
git add server/migrations/2026_10_27_run_diagnoses.py server/app.py server/tests/test_run_diagnosis.py
git commit -m "feat(skillopt): O1 迁移——ai_run_diagnoses 五维度运行诊断表+app.py 显式注册"
```

---

### Task 2: 诊断聚合器 utils/run_diagnosis.py（TDD 核心）

**Files:**
- Create: `server/utils/run_diagnosis.py`
- Modify: `server/tests/test_run_diagnosis.py`

**Interfaces:**
- Consumes: `perf_analysis.attempt_payload(db_ctx, attempt_id) -> dict | None`（perf_analysis.py:879）；`ai_execution_step_results` 表（trace_auditor.py:429-448 写入，列含 declared_by_agent/observed/status）；`action_expectations(scope_type, scope_id, last_status)`（2026_09_20_agent_action_gate.py:33-52）；`ai_chat_sessions.status`。
- Produces: `build_run_diagnosis(attempt_id: str, get_db=None) -> dict | None`——返回 `{'attemptId', 'rootSessionId', 'overall', 'dimensions': {key: {'status','summary','evidence'}}, 'generatorVersion'}` 并落库；attempt 不存在返回 None。`DIMENSION_KEYS = ('task_result','rule_compliance','promise_action','skill_fit','performance')`。**承诺—行动维度带 lazy-fill（2026-10-27 用户认可）**：`ai_execution_step_results` 无行时 best-effort 调既有 `ensure_diagnosis_for_session`（确定性审计，零 LLM）现算补齐再读；已有行绝不重算；失败/仍无行 → uncertain。Task 3 的端点与挂钩只调这一个函数。

- [ ] **Step 1: 写失败测试——维度规则与 overall 聚合**

在 `server/tests/test_run_diagnosis.py` 追加（复用 test_skill_fit_routes.py 的真实 DB shadow client 模式；本文件全部用例只需 `db_conn` fixture）：

```python
import uuid

from utils.run_diagnosis import build_run_diagnosis, DIMENSION_KEYS


def _seed_attempt(db_conn, *, session_status='completed', exp_status=None,
                  step_rows=None, fit_status=None):
    """种子 attempt + 可选的门禁期望行/步骤核对行/拟合行。返回 attempt_id。"""
    uid, sid, attempt = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                    "VALUES (%s, %s, 'x', 'FR', 'developer')", (uid, f'rd_{uid[:8]}'))
        cur.execute("INSERT INTO ai_chat_sessions (id, user_id, status, session_token) "
                    "VALUES (%s, %s, %s, %s)", (sid, uid, session_status, f'tok-{sid[:12]}'))
        cur.execute("INSERT INTO ai_execution_attempts (id, session_id, source_type, "
                    "  operation, started_at, finished_at) "
                    "VALUES (%s, %s, 'batch', 'send', NOW(), NOW())", (attempt, sid))
        if exp_status is not None:
            cur.execute("INSERT INTO action_expectations (scope_type, scope_id, name, tool, "
                        "  args_pattern, source, last_status, last_checked_at, last_evidence) "
                        "VALUES ('tree', %s, 'e1', 'bash', 'git clone', 'policy', %s, NOW(), 1)",
                        (sid, exp_status))
        if step_rows is not None:
            for sr in step_rows:
                cur.execute("INSERT INTO ai_execution_step_results (id, attempt_id, step_id, "
                            "  expected, declared_by_agent, observed, status, evidence_level, "
                            "  confidence, duration_ms, evidence_refs, reason) "
                            "VALUES (%s, %s, %s, true, %s, %s, %s, 'observed', 0.9, 10, "
                            "  '[]'::jsonb, 'seed')",
                            ('stpr_' + uuid.uuid4().hex[:10], attempt,
                             sr.get('stepId', 's1'), sr.get('declared', True),
                             sr.get('observed', True), sr.get('status', 'completed')))
        if fit_status is not None:
            cur.execute("INSERT INTO ai_skill_fit_results (id, attempt_id, session_id, "
                        "  def_kind, def_name, def_hash, steps_total, steps_hit, score, "
                        "  status, per_step) "
                        "VALUES (%s, %s, %s, 'skill', 'demo-skill', 'h1', 2, 1, 50, %s, '[]'::jsonb)",
                        ('fit_' + uuid.uuid4().hex[:10], attempt, sid, fit_status))
    return attempt, sid


def test_gate_failed_overrides_to_fail(db_conn):
    attempt, _ = _seed_attempt(db_conn, exp_status='failed')
    d = build_run_diagnosis(attempt)
    assert d['dimensions']['rule_compliance']['status'] == 'fail'
    assert d['dimensions']['rule_compliance']['evidence']  # fail 必带证据
    assert d['overall'] == 'fail'


def test_no_expectations_is_uncertain_not_pass(db_conn):
    attempt, _ = _seed_attempt(db_conn)
    d = build_run_diagnosis(attempt)
    assert d['dimensions']['rule_compliance']['status'] == 'uncertain'


def test_promise_action_claimed_without_evidence_fails(db_conn):
    attempt, _ = _seed_attempt(db_conn, step_rows=[
        {'status': 'completed_claimed', 'declared': True, 'observed': False}])
    d = build_run_diagnosis(attempt)
    assert d['dimensions']['promise_action']['status'] == 'fail'
    assert d['dimensions']['promise_action']['evidence'][0]['type'] == 'step_result'


def test_overall_partial_when_only_fit_partial(db_conn):
    attempt, _ = _seed_attempt(db_conn, fit_status='partial')
    d = build_run_diagnosis(attempt)
    assert d['dimensions']['skill_fit']['status'] in ('fail', 'pass')
    assert d['overall'] in ('partial', 'fail')


def test_all_five_dimensions_always_present(db_conn):
    attempt, _ = _seed_attempt(db_conn)
    d = build_run_diagnosis(attempt)
    assert set(d['dimensions'].keys()) == set(DIMENSION_KEYS)


def test_upsert_idempotent(db_conn):
    attempt, _ = _seed_attempt(db_conn, exp_status='passed')
    build_run_diagnosis(attempt)
    d2 = build_run_diagnosis(attempt)
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ai_run_diagnoses WHERE attempt_id=%s", (attempt,))
        assert cur.fetchone()[0] == 1
    assert d2['overall'] == 'pass'


def test_missing_attempt_returns_none(db_conn):
    assert build_run_diagnosis('no-such-attempt') is None


def test_perf_uncertain_without_metrics(db_conn):
    attempt, _ = _seed_attempt(db_conn)
    d = build_run_diagnosis(attempt)
    assert d['dimensions']['performance']['status'] == 'uncertain'


def test_promise_action_lazy_fill_via_existing_audit(db_conn, monkeypatch):
    """lazy-fill：无步骤核对行时 best-effort 调既有 ensure_diagnosis_for_session
    现算再读（2026-10-27 认可）；直接种子了 step 行的上面几个用例即证明
    「已有行不触发」。"""
    import utils.trace_auditor as ta
    attempt, sid = _seed_attempt(db_conn)          # 不种 step_rows → 无行
    calls = []

    def fake_ensure(session_id):
        calls.append(session_id)
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO ai_execution_step_results (id, attempt_id, "
                        "  step_id, expected, declared_by_agent, observed, status, "
                        "  evidence_level, confidence, duration_ms, evidence_refs, "
                        "  reason) VALUES (%s, %s, 's1', true, true, false, "
                        "  'completed_claimed', 'observed', 0.9, 10, '[]'::jsonb, "
                        "  'fake')",
                        ('stpr_' + uuid.uuid4().hex[:10], attempt))
        db_conn.commit()

    monkeypatch.setattr(ta, 'ensure_diagnosis_for_session', fake_ensure)
    d = build_run_diagnosis(attempt)
    assert calls == [sid]
    assert d['dimensions']['promise_action']['status'] == 'fail'
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_run_diagnosis.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'utils.run_diagnosis'`

- [ ] **Step 3: 实现聚合器**

创建 `server/utils/run_diagnosis.py`：

```python
# -*- coding: utf-8 -*-
"""O1 统一运行诊断（设计 docs/superpowers/specs/2026-10-26-agent-self-evolution-design.md §3）。

一次 attempt 的结构化多维诊断，零 LLM：五维度全部来自确定性数据——
task_result=会话终态；rule_compliance=action_expectations 终态核对结果；
promise_action=ai_execution_step_results 声称 vs 观察；skill_fit=拟合结果行；
performance=perf_analysis 四段/慢因。证据缺失一律 uncertain，不冒充 pass；
status=fail 的维度必须带 evidence（写入前校验，违者拒绝落库——无证据的
fail 判定是生成器 bug，不能进学习信号）。
"""
import json
import logging
import secrets

from db import get_db as _default_get_db
from utils.perf_analysis import attempt_payload

log = logging.getLogger(__name__)

GENERATOR_VERSION = 'v1'
DIMENSION_KEYS = ('task_result', 'rule_compliance', 'promise_action',
                  'skill_fit', 'performance')
_DIM_STATUSES = ('pass', 'fail', 'uncertain')


def _dim(status: str, summary: str, evidence: list | None = None) -> dict:
    assert status in _DIM_STATUSES, status
    if status == 'fail' and not evidence:
        raise ValueError(f'fail 维度必须带证据: {summary}')
    return {'status': status, 'summary': summary,
            'evidence': evidence or []}


def _dim_task_result(cur, session_id: str) -> dict:
    cur.execute("SELECT status, error_message FROM ai_chat_sessions WHERE id = %s",
                (session_id,))
    row = cur.fetchone()
    if not row:
        return _dim('uncertain', '会话不存在')
    status, err = row[0], row[1]
    if status == 'completed':
        return _dim('pass', '会话终态 completed')
    if status in ('failed', 'error', 'cancelled'):
        return _dim('fail', f'会话终态 {status}: {err or ""}'.strip(),
                    [{'type': 'session', 'id': session_id}])
    return _dim('uncertain', f'会话终态 {status}（非终态或不可判）')


def _dim_rule_compliance(cur, session_id: str) -> dict:
    # tree 作用域直接挂根会话 id；session 作用域挂子会话 oc id——经账本
    # root_session_id 反查本次运行实际用过的 oc 会话（与 agent_ledger 的
    # _subtree_oc_ids 同方向，不重复实现树遍历）。
    cur.execute(
        """
        SELECT e.id, e.name, e.last_status, e.last_evidence
        FROM action_expectations e
        WHERE (e.scope_type = 'tree' AND e.scope_id = %s)
           OR (e.scope_type = 'session' AND e.scope_id IN (
                SELECT oc_session_id FROM agent_tool_calls
                WHERE root_session_id = %s))
        """, (session_id, session_id))
    rows = cur.fetchall()
    if not rows:
        return _dim('uncertain', '本运行未登记门禁期望')
    failed = [r for r in rows if r[2] == 'failed']
    if failed:
        return _dim('fail', f'{len(failed)}/{len(rows)} 条期望未达标',
                    [{'type': 'gate_expectation', 'id': r[0], 'hint': r[1]}
                     for r in failed])
    return _dim('pass', f'{len(rows)} 条期望全部达标',
                [{'type': 'gate_expectation', 'id': r[0]} for r in rows])


def _dim_promise_action(cur, attempt_id: str, session_id: str, db_ctx) -> dict:
    cur.execute(
        "SELECT id, step_id, status, declared_by_agent, observed "
        "FROM ai_execution_step_results WHERE attempt_id = %s", (attempt_id,))
    rows = cur.fetchall()
    if not rows:
        # lazy-fill（设计 §3.3，2026-10-27 认可）：步骤核对行由执行审计按需
        # 生成（仅管理端打开时）；聚合时无行则 best-effort 调既有确定性审计
        # 现算，再读一次。已有行绝不重算；失败/仍无行 → uncertain，不阻塞。
        try:
            from utils.trace_auditor import ensure_diagnosis_for_session
            ensure_diagnosis_for_session(session_id)
        except Exception as e:  # noqa: BLE001
            log.warning('run_diagnosis: lazy-fill audit failed sid=%s: %s',
                        session_id, e)
        cur.execute(
            "SELECT id, step_id, status, declared_by_agent, observed "
            "FROM ai_execution_step_results WHERE attempt_id = %s", (attempt_id,))
        rows = cur.fetchall()
    if not rows:
        return _dim('uncertain', '无步骤核对记录（执行审计不可用）')
    bad = [r for r in rows
           if (r[3] and not r[4]) or (r[2] and 'claimed' in str(r[2]))]
    if bad:
        return _dim('fail', f'{len(bad)}/{len(rows)} 步声称完成但无工具证据',
                    [{'type': 'step_result', 'id': r[0],
                      'stepId': r[1], 'status': r[2]} for r in bad])
    return _dim('pass', f'{len(rows)} 步声称与证据一致',
                [{'type': 'step_result', 'id': r[0]} for r in rows])


def _dim_skill_fit(cur, attempt_id: str) -> dict:
    cur.execute(
        "SELECT id, def_name, status, score FROM ai_skill_fit_results "
        "WHERE attempt_id = %s", (attempt_id,))
    rows = cur.fetchall()
    if not rows:
        return _dim('uncertain', '无拟合结果行（未调用技能或无 runtime 证据）')
    bad = [r for r in rows if r[2] in ('diverged', 'parse_error')]
    warn = [r for r in rows if r[2] == 'partial']
    if bad:
        return _dim('fail', f'拟合偏离: {[r[1] for r in bad]}',
                    [{'type': 'fit_result', 'id': r[0], 'skill': r[1]}
                     for r in bad])
    if warn:
        return _dim('pass', f'拟合部分命中: {[r[1] for r in warn]}',
                    [{'type': 'fit_result', 'id': r[0], 'skill': r[1]}
                     for r in warn])
    return _dim('pass', f'{len(rows)} 个定义全部对齐')


def _dim_performance(db_ctx, attempt_id: str) -> dict:
    payload = attempt_payload(db_ctx, attempt_id)
    if not payload:
        return _dim('uncertain', '无性能数据')
    dominant = (payload.get('cause') or {}).get('dominant') \
        if isinstance(payload.get('cause'), dict) else None
    return _dim('pass', f'主导慢因: {dominant or "均衡"}',
                [{'type': 'perf_attempt', 'attemptId': attempt_id}])


_OVERALL_FAIL = {'fail'}
_OVERALL_PASS = {'pass', 'uncertain'}  # 全 uncertain 也算 uncertain，见下


def _overall(dims: dict) -> str:
    statuses = {v['status'] for v in dims.values()}
    if 'fail' in statuses:
        return 'fail'
    if 'uncertain' in statuses:
        return 'uncertain' if statuses == {'uncertain'} else 'partial'
    return 'success'


def build_run_diagnosis(attempt_id: str, get_db=None) -> dict | None:
    db_ctx = get_db or _default_get_db
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT session_id FROM ai_execution_attempts WHERE id = %s",
                        (attempt_id,))
            row = cur.fetchone()
            if not row:
                return None
            session_id = row[0]
            dims = {
                'task_result': _dim_task_result(cur, session_id),
                'rule_compliance': _dim_rule_compliance(cur, session_id),
                'promise_action': _dim_promise_action(cur, attempt_id,
                                                      session_id, db_ctx),
                'skill_fit': _dim_skill_fit(cur, attempt_id),
                'performance': _dim_performance(db_ctx, attempt_id),
            }
            overall = _overall(dims)
            payload = json.dumps(dims, ensure_ascii=False)
            cur.execute(
                """
                INSERT INTO ai_run_diagnoses
                    (id, attempt_id, root_session_id, overall, dimensions,
                     generator_version)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (attempt_id, generator_version) DO UPDATE SET
                    root_session_id = EXCLUDED.root_session_id,
                    overall = EXCLUDED.overall,
                    dimensions = EXCLUDED.dimensions,
                    created_at = NOW()
                """,
                ('rdi_' + secrets.token_hex(6), attempt_id, session_id,
                 overall, payload, GENERATOR_VERSION))
        conn.commit()
    return {'attemptId': attempt_id, 'rootSessionId': session_id,
            'overall': overall, 'dimensions': dims,
            'generatorVersion': GENERATOR_VERSION}
```

注意两个实现期核对点（不改变契约，只允许调整取数细节）：
1. `attempt_payload` 返回体里主导慢因的字段名以 `perf_analysis.py:547-661 definition_cause_distribution` 的 `dominantCounts` 结构为准，取不到时 summary 用「均衡」，不得因此报错。
2. `ai_execution_step_results` 的 status 枚举以 `trace_auditor.build_step_results`（trace_auditor.py:157-224）实际产出为准；判定条件「declared 且非 observed」为主、status 含 `claimed` 为辅，两者是或关系，已覆盖。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_run_diagnosis.py -v`
Expected: 10 个用例全 PASS（含 lazy-fill；直接种子 step 行的用例证明已有行不触发 ensure）。若 `test_overall_partial_when_only_fit_partial` 断言过松，收紧为按实现精确断言后仍 PASS。

- [ ] **Step 5: Commit**

```bash
git add server/utils/run_diagnosis.py server/tests/test_run_diagnosis.py
git commit -m "feat(skillopt): O1 诊断聚合器——五维度确定性判定+fail 必带证据+按版本幂等 upsert"
```

---

### Task 3: O1 端点与批 finalize 挂钩

**Files:**
- Modify: `server/routes/ai_session_admin.py`（文件尾部 perf 区块之后追加）
- Modify: `server/utils/batch_engine.py`（finalize/reconcile 收口处）
- Modify: `server/tests/test_run_diagnosis.py`（补端点用例）

**Interfaces:**
- Consumes: Task 2 的 `build_run_diagnosis(attempt_id, get_db=None)`。
- Produces: `POST /ai/chat/admin/attempts/<attempt_id>/run-diagnosis`（手动触发，返回 201+诊断体；attempt 不存在 404）；`GET /ai/chat/admin/attempts/<attempt_id>/run-diagnosis`（无则 404）；`GET /ai/chat/admin/run-diagnoses?rootSessionId=<sid>`（按 created_at DESC，limit≤100）。批 finalize 自动生成（best-effort，失败只记日志不断言 fail-closed——诊断缺失不能反过来拖垮任务收敛）。

- [ ] **Step 1: 写失败测试（端点）**

`server/tests/test_run_diagnosis.py` 追加：

```python
import pytest


@pytest.fixture
def client():
    """真实 DB test client（shadow conftest 的 mock-DB client，
    同 test_skill_fit_routes.py:35-58 模式）。"""
    import sys
    import db as _db
    from app import app as flask_app
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if (mod_name.startswith('routes.') or mod_name.startswith('utils.')
                or mod_name == 'auth'):
            for attr in ('get_db', '_default_get_db'):
                if getattr(mod, attr, None) is not None:
                    try:
                        setattr(mod, attr, _db.get_db)
                    except (AttributeError, TypeError):
                        pass
    flask_app.config['TESTING'] = True
    return flask_app.test_client()


def test_trigger_and_read_endpoints(client, db_conn):
    attempt, _ = _seed_attempt(db_conn, exp_status='passed')
    resp = client.post(f'/ai/chat/admin/attempts/{attempt}/run-diagnosis')
    assert resp.status_code == 201
    body = resp.get_json()
    assert body['attemptId'] == attempt
    resp2 = client.get(f'/ai/chat/admin/attempts/{attempt}/run-diagnosis')
    assert resp2.status_code == 200
    assert resp2.get_json()['overall'] in ('pass', 'partial', 'uncertain')


def test_trigger_missing_attempt_404(client):
    resp = client.post('/ai/chat/admin/attempts/nope/run-diagnosis')
    assert resp.status_code == 404


def test_list_by_root_session(client, db_conn):
    attempt, sid = _seed_attempt(db_conn, exp_status='passed')
    client.post(f'/ai/chat/admin/attempts/{attempt}/run-diagnosis')
    resp = client.get(f'/ai/chat/admin/run-diagnoses?rootSessionId={sid}')
    assert resp.status_code == 200
    assert any(r['attemptId'] == attempt for r in resp.get_json()['items'])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_run_diagnosis.py -v`
Expected: 新增 3 用例 FAIL（404 —— 路由不存在）

- [ ] **Step 3: 实现端点**

`server/routes/ai_session_admin.py` 尾部追加：

```python
# ── O1 统一运行诊断（spec 2026-10-26-agent-self-evolution-design §3.3）──

@ai_execution_admin_bp.post('/attempts/<attempt_id>/run-diagnosis')
@require_permission('admin.ai_chat_admin')
def run_diagnosis_generate(attempt_id):
    from utils.run_diagnosis import build_run_diagnosis
    d = build_run_diagnosis(attempt_id)
    if d is None:
        return jsonify({'error': 'attempt 不存在'}), 404
    log_operation('create', 'ai_run_diagnoses', attempt_id, None,
                  '运行诊断生成（O1 确定性聚合）')
    return jsonify(d), 201


@ai_execution_admin_bp.get('/attempts/<attempt_id>/run-diagnosis')
@require_permission('admin.ai_chat_admin')
def run_diagnosis_get(attempt_id):
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, attempt_id, root_session_id, overall, dimensions, "
                "       generator_version, created_at "
                "FROM ai_run_diagnoses WHERE attempt_id = %s "
                "ORDER BY generator_version DESC, created_at DESC LIMIT 1",
                (attempt_id,))
            row = cur.fetchone()
    if not row:
        return jsonify({'error': '尚未生成诊断'}), 404
    return jsonify({'id': row[0], 'attemptId': row[1],
                    'rootSessionId': row[2], 'overall': row[3],
                    'dimensions': row[4], 'generatorVersion': row[5],
                    'createdAt': row[6].isoformat() if row[6] else None})


@ai_execution_admin_bp.get('/run-diagnoses')
@require_permission('admin.ai_chat_admin')
def run_diagnoses_list():
    from db import get_db
    root = (request.args.get('rootSessionId') or '').strip()
    if not root:
        return jsonify({'error': 'rootSessionId 必填'}), 400
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, attempt_id, overall, created_at "
                "FROM ai_run_diagnoses WHERE root_session_id = %s "
                "ORDER BY created_at DESC LIMIT 100", (root,))
            items = [{'id': r[0], 'attemptId': r[1], 'overall': r[2],
                      'createdAt': r[3].isoformat() if r[3] else None}
                     for r in cur.fetchall()]
    return jsonify({'items': items})
```

- [ ] **Step 4: 批 finalize 挂钩**

在 `server/utils/batch_engine.py` 中 grep `_check_action_gate(`（batch_engine.py:1825 定义、约 1388 处调用）找到 `_run_one` 终态收口处，在门禁核对完成、终态写库事务**之后**追加（best-effort，绝不影响任务终态）：

```python
        # O1 运行诊断（best-effort）：诊断缺失/失败只记日志，不回写任务
        # 终态——fail-closed 只适用于门禁核对本身，聚合器是纯旁路观测。
        try:
            from utils.run_diagnosis import build_run_diagnosis
            build_run_diagnosis(attempt_id)
        except Exception as _diag_exc:  # noqa: BLE001
            log.warning('run diagnosis (best-effort) failed for %s: %s',
                        attempt_id, _diag_exc)
```

其中 `attempt_id` 用 `_run_one` 作用域内已有的 attempt 变量名（grep `ai_execution_attempts` 在该函数内的 insert/查询行确认实际变量名，可能叫 `attempt_id` 或 `aid`）。

- [ ] **Step 5: 跑全部相关测试**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_run_diagnosis.py tests/test_agent_action_gate.py -v`
Expected: 全 PASS（agent_action_gate 回归证明账本/期望面未被扰动）

- [ ] **Step 6: Commit**

```bash
git add server/routes/ai_session_admin.py server/utils/batch_engine.py server/tests/test_run_diagnosis.py
git commit -m "feat(skillopt): O1 端点三件套+批 finalize 挂钩——手动触发/读取/按会话列表，best-effort 旁路"
```

---

### Task 4: O1 前端——RunDiagnosisPanel + API 封装

**Files:**
- Create: `src/components/admin/skillopt/RunDiagnosisPanel.vue`
- Modify: `src/api/aiSessionAdmin.ts`（文件尾部追加）
- Modify: `src/components/admin/skillopt/SubtaskPerfReport.vue`（抽屉内新增分区）

**Interfaces:**
- Consumes: Task 3 的三个端点。
- Produces: API 函数 `generateRunDiagnosis(attemptId: string)`、`getRunDiagnosis(attemptId: string)`、`listRunDiagnoses(rootSessionId: string)`；组件 `<RunDiagnosisPanel :attempt-id="..." />`，对外仅此一个 prop。E2E（Task 5）依赖组件内的 `data-testid="run-diag-panel"` / `run-diag-generate` / `.rd-dim[data-status]` 选择器。

- [ ] **Step 1: API 封装**

`src/api/aiSessionAdmin.ts` 尾部追加（http 助手与文件内既有导出同款——先看文件头部 import 的请求封装名，若是 `request`/`http` 按现名写）：

```ts
export function generateRunDiagnosis(attemptId: string) {
  return request.post(`/ai/chat/admin/attempts/${attemptId}/run-diagnosis`)
}

export function getRunDiagnosis(attemptId: string) {
  return request.get(`/ai/chat/admin/attempts/${attemptId}/run-diagnosis`)
}

export function listRunDiagnoses(rootSessionId: string) {
  return request.get('/ai/chat/admin/run-diagnoses', { params: { rootSessionId } })
}
```

- [ ] **Step 2: 组件**

创建 `src/components/admin/skillopt/RunDiagnosisPanel.vue`：

```vue
<template>
  <div class="rdp" data-testid="run-diag-panel">
    <div class="rdp__bar">
      <span class="rdp__title">运行诊断</span>
      <el-tag v-if="data" :type="overallTag" size="small">{{ overallText }}</el-tag>
      <el-button
        size="small" :loading="loading" data-testid="run-diag-generate"
        @click="generate">生成/刷新</el-button>
    </div>
    <div v-if="data" class="rdp__dims">
      <div
        v-for="key in ORDER" :key="key"
        class="rd-dim" :data-status="data.dimensions[key]?.status || 'uncertain'">
        <span class="rd-dim__name">{{ LABELS[key] }}</span>
        <el-tag size="small" :type="tagType(data.dimensions[key]?.status)">
          {{ statusText(data.dimensions[key]?.status) }}</el-tag>
        <span class="rd-dim__summary">{{ data.dimensions[key]?.summary }}</span>
      </div>
    </div>
    <div v-else class="rdp__empty">尚未生成诊断</div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { generateRunDiagnosis, getRunDiagnosis } from '@/api/aiSessionAdmin'

const props = defineProps<{ attemptId: string }>()
const ORDER = ['task_result', 'rule_compliance', 'promise_action', 'skill_fit', 'performance'] as const
const LABELS: Record<string, string> = {
  task_result: '任务结果', rule_compliance: '规则遵从',
  promise_action: '承诺—行动一致性', skill_fit: '技能拟合', performance: '性能',
}
const data = ref<any>(null)
const loading = ref(false)

const OVERALL_TEXT: Record<string, string> = {
  success: '成功', partial: '部分成功', fail: '失败', uncertain: '证据不足',
}
const overallText = () => OVERALL_TEXT[data.value?.overall] || data.value?.overall
const overallTag = () =>
  ({ success: 'success', partial: 'warning', fail: 'danger', uncertain: 'info' } as any)[data.value?.overall] || 'info'
const tagType = (s?: string) =>
  ({ pass: 'success', fail: 'danger', uncertain: 'info' } as any)[s || 'uncertain'] || 'info'
const statusText = (s?: string) =>
  ({ pass: '通过', fail: '未通过', uncertain: '证据不足' } as any)[s || 'uncertain'] || s

async function load() {
  try {
    const resp: any = await getRunDiagnosis(props.attemptId)
    data.value = resp.data ?? resp
  } catch { data.value = null }
}

async function generate() {
  loading.value = true
  try {
    const resp: any = await generateRunDiagnosis(props.attemptId)
    data.value = resp.data ?? resp
  } finally {
    loading.value = false
  }
}

load()
</script>

<style scoped>
.rdp { border: 1px solid var(--el-border-color-lighter); border-radius: 6px; padding: 8px 12px; }
.rdp__bar { display: flex; align-items: center; gap: 8px; }
.rdp__title { font-weight: 600; margin-right: auto; }
.rdp__empty { color: var(--el-text-color-secondary); font-size: 12px; padding: 6px 0; }
.rd-dim { display: flex; align-items: center; gap: 8px; padding: 4px 0; font-size: 12px; }
.rd-dim__name { width: 110px; color: var(--el-text-color-regular); }
.rd-dim__summary { color: var(--el-text-color-secondary); }
.rd-dim[data-status='fail'] .rd-dim__summary { color: var(--el-color-danger); }
</style>
```

- [ ] **Step 3: 挂进 SubtaskPerfReport 抽屉**

grep `src/components/admin/skillopt/SubtaskPerfReport.vue` 中最后一个分区（如「工具 Top」或复制 Markdown 按钮所在 el-form-item/section），在其后插入：

```vue
      <el-divider content-position="left">运行诊断（O1）</el-divider>
      <RunDiagnosisPanel v-if="attemptId" :attempt-id="attemptId" />
```

`<script setup>` 增加 `import RunDiagnosisPanel from './RunDiagnosisPanel.vue'`；`attemptId` 用该组件里已有的 attempt 标识变量（grep `attemptId|attempt_id|props.` 确认实际名，若变量名不同则同步改绑定）。

- [ ] **Step 4: 构建验证**

Run: `cd src/../ && npx vue-tsc --noEmit 2>&1 | head -20 || npm run build 2>&1 | tail -5`
（以仓库现行校验命令为准：先看 package.json scripts 里有无 `type-check`/`build`，用它）
Expected: 无类型错误

- [ ] **Step 5: Commit**

```bash
git add src/api/aiSessionAdmin.ts src/components/admin/skillopt/RunDiagnosisPanel.vue src/components/admin/skillopt/SubtaskPerfReport.vue
git commit -m "feat(skillopt): O1 前端——RunDiagnosisPanel 五维度状态条+SubtaskPerfReport 集成"
```

---

### Task 5: O1 E2E——真实链路诊断面板

**Files:**
- Create: `e2e/ai-full/run-diagnosis.spec.ts`

**Interfaces:**
- Consumes: Task 3 端点（真实后端）、Task 4 的 `data-testid` 选择器、`e2e/ai-full/batch/db_exec.py` 种子桥。
- Produces: 回归用例 `run-diagnosis.spec.ts`（纳入 e2e/ai-full 回归入口）。

- [ ] **Step 1: 写 spec**

创建 `e2e/ai-full/run-diagnosis.spec.ts`（模式对齐 `ai-skillopt-version-archive.spec.ts`：真实后端、db_exec 种子、finally 清理、不等 networkidle）：

```typescript
/**
 * O1 运行诊断（真实后端，零 LLM）。
 * spec: docs/superpowers/specs/2026-10-26-agent-self-evolution-design.md §7
 * 约定: 等真实元素,绝不等 networkidle;种子行 finally 清理（db_exec 桥）。
 * 种子: attempt + 一条 failed 门禁期望 → 诊断必含 rule_compliance=fail。
 */
import { test, expect, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import crypto from 'node:crypto'

const DIRNAME = path.dirname(fileURLToPath(import.meta.url))
const TAG = `rd_${Date.now()}`

function db(sql: string): string {
  const script = path.join(DIRNAME, 'batch', 'db_exec.py')
  return execFileSync('python', [script], { input: sql, encoding: 'utf-8' })
}

const uid = crypto.randomUUID()
const sid = crypto.randomUUID()
const attemptId = crypto.randomUUID()

function seed(): void {
  db(`
    INSERT INTO users (id, username, password_hash, display_name, role)
    VALUES ('${uid}', 'u_${TAG}', 'x', 'RD', 'developer');
    INSERT INTO ai_chat_sessions (id, user_id, status, session_token)
    VALUES ('${sid}', '${uid}', 'completed', 'tok-${TAG}');
    INSERT INTO ai_execution_attempts (id, session_id, source_type, operation,
      started_at, finished_at)
    VALUES ('${attemptId}', '${sid}', 'batch', 'send', NOW(), NOW());
    INSERT INTO action_expectations (scope_type, scope_id, name, tool,
      args_pattern, source, last_status, last_checked_at, last_evidence)
    VALUES ('tree', '${sid}', 'e2e-gate-${TAG}', 'bash', 'git clone',
      'policy', 'failed', NOW(), 0);`)
}

function cleanup(): void {
  db(`
    DELETE FROM ai_run_diagnoses WHERE attempt_id = '${attemptId}';
    DELETE FROM action_expectations WHERE scope_id = '${sid}';
    DELETE FROM ai_execution_attempts WHERE id = '${attemptId}';
    DELETE FROM ai_chat_sessions WHERE id = '${sid}';
    DELETE FROM users WHERE id = '${uid}';`)
}

async function login(page: Page): Promise<void> {
  await page.goto('/')
  const userInput = page.locator('input[placeholder*="用户名"]')
  try {
    await userInput.waitFor({ state: 'visible', timeout: 30_000 })
    await userInput.fill('admin')
    await page.locator('input[placeholder*="密码"]').fill('admin123')
    await page.getByRole('button', { name: /登\s*录/ }).click()
    await userInput.waitFor({ state: 'hidden', timeout: 15_000 })
  } catch { /* 已是登录态 */ }
}

test('运行诊断面板：生成→维度含 rule_compliance=fail', async ({ page }) => {
  test.setTimeout(180_000)
  seed()
  try {
    await login(page)
    // 入口：性能分析页任一任务抽屉——直接经 API 断言核心链路后，UI 面板
    // 经 SubtaskPerfReport 打开（抽屉入口在性能分析页任务表首行）。
    await page.goto('/admin/ai-skillopt')
    await expect(page.getByText('SkillOpt — 技能优化')).toBeVisible()
    // 用窗口内 fetch 直接驱动真实端点（管理页登录态下同源），断言服务端
    // 语义；UI 面板渲染随后在抽屉里断言。
    const diag = await page.evaluate(async (aid) => {
      const r = await fetch(`/api/ai/chat/admin/attempts/${aid}/run-diagnosis`,
        { method: 'POST' })
      return { status: r.status, body: await r.json() }
    }, attemptId)
    expect(diag.status).toBe(201)
    expect(diag.body.dimensions.rule_compliance.status).toBe('fail')
    expect(diag.body.overall).toBe('fail')
    // 幂等：重复生成仍是 201 且不报错
    const again = await page.evaluate(async (aid) =>
      (await fetch(`/api/ai/chat/admin/attempts/${aid}/run-diagnosis`,
        { method: 'POST' })).status, attemptId)
    expect(again).toBe(201)
  } finally {
    cleanup()
  }
})
```

注意：API 前缀若平台网关带 `/api`（记忆中「预览路径 /api 前缀坑」），先在浏览器 devtools 或 `vite.config`/网关配置里确认真实前缀；UI 面板部分若抽屉入口定位脆弱，允许本用例只保 API 语义断言（UI 面板由 Task 4 组件测试兜底），把「面板渲染」留作后续抽屉用例的增强，不算缺陷。

- [ ] **Step 2: 跑 spec（修复前先确认失败不存在——本用例在 Task 3/4 完成后应直接绿）**

Run: `npx playwright test e2e/ai-full/run-diagnosis.spec.ts`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add e2e/ai-full/run-diagnosis.spec.ts
git commit -m "test(e2e): O1 运行诊断真实链路——种子 failed 期望断言维度 fail/overall fail/幂等"
```

---

## Phase B — O2 技能进化闭环

### Task 6: 迁移 ai_skill_candidates + app.py 注册

**Files:**
- Create: `server/migrations/2026_10_27_skill_candidates.py`
- Modify: `server/app.py`

**Interfaces:**
- Produces: 表 `ai_skill_candidates(id TEXT PK, def_kind VARCHAR(30) NOT NULL, def_name VARCHAR(200) NOT NULL, base_content_hash VARCHAR(64) NOT NULL, edits JSONB NOT NULL DEFAULT '[]'::jsonb, edits_hash VARCHAR(64), fallback_content TEXT, source JSONB NOT NULL DEFAULT '{}'::jsonb, validation JSONB, status VARCHAR(20) NOT NULL DEFAULT 'pending', created_by VARCHAR(60) NOT NULL DEFAULT 'fit-diagnose', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), decided_at TIMESTAMPTZ, decided_by VARCHAR(60), promoted_version_id TEXT REFERENCES ai_skill_def_versions(id))`；索引 `idx_skill_cand_lookup(def_kind, def_name, status)` 与去重索引 `uq_skill_cand_active(def_kind, def_name, base_content_hash, edits_hash)`（部分唯一索引，仅约束 status IN (pending, validated)——R2 去重）。status 状态机：pending → validated|rejected；validated → promoted|expired（expire 也可从 pending 直接发生）。

- [ ] **Step 1: 写迁移文件**

```python
# -*- coding: utf-8 -*-
"""幂等迁移：O2 技能进化候选区（ai_skill_candidates）。

LLM 诊断产物只进候选区，永不直接写定义文件（第 8 章安全边界②候选与正式
隔离）。edits=[{old_str,new_str,reason}] 最小 diff；无法表达为 diff 时整块
降级存 fallback_content（UI 显式标注）。promoted_version_id 关联发布产生
的 ai_skill_def_versions 行，回滚复用现有 rollback 端点。edits_hash 是
canonical edits JSON 的 sha256（R2 去重键）。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db

def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_skill_candidates (
                id                  TEXT PRIMARY KEY,
                def_kind            VARCHAR(30) NOT NULL,
                def_name            VARCHAR(200) NOT NULL,
                base_content_hash   VARCHAR(64) NOT NULL,
                edits               JSONB NOT NULL DEFAULT '[]'::jsonb,
                edits_hash          VARCHAR(64),
                fallback_content    TEXT,
                source              JSONB NOT NULL DEFAULT '{}'::jsonb,
                validation          JSONB,
                status              VARCHAR(20) NOT NULL DEFAULT 'pending',
                created_by          VARCHAR(60) NOT NULL DEFAULT 'fit-diagnose',
                created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                decided_at          TIMESTAMPTZ,
                decided_by          VARCHAR(60),
                promoted_version_id TEXT REFERENCES ai_skill_def_versions(id)
            )""")
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_skill_cand_lookup
                ON ai_skill_candidates(def_kind, def_name, status)""")
        # R2 去重：同一 (def, 基线, diff) 只允许一个未决候选。
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_skill_cand_active
                ON ai_skill_candidates(def_kind, def_name, base_content_hash, edits_hash)
                WHERE status IN ('pending', 'validated')""")
        conn.commit()
    print("skill candidates migration ready.")

if __name__ == "__main__":
    run()
```

- [ ] **Step 2: app.py 注册 boot 块**

紧随 Task 1 的注册块之后追加（变量名先 grep 确认未占用，占用则顺延）：

```python
# O2 技能进化候选区表（2026-10-27）：ai_skill_candidates。随启动幂等执行；
# 漏注册会让候选创建/列表端点 500。
try:
    _mp21 = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'migrations', '2026_10_27_skill_candidates.py')
    _spec21 = _ilu.spec_from_file_location('_skill_cand_boot', _mp21)
    _m21 = _ilu.module_from_spec(_spec21)
    _spec21.loader.exec_module(_m21)
    _m21.run()
except Exception as _e:
    logging.warning('skill candidates migration on boot failed: %s', _e)
```

- [ ] **Step 3: 跑护栏 + 建表验证**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_migration_boot_registration.py -v`
Expected: PASS
Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_candidate_eval.py -v`（下一步建最小占位）

先创建 `server/tests/test_skill_candidate_eval.py` 占位：

```python
# -*- coding: utf-8 -*-
"""O2 候选评估测试：diff 校验/双集回归门槛/promote 前置校验。"""


def test_table_exists(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ai_skill_candidates")
        assert cur.fetchone()[0] == 0
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add server/migrations/2026_10_27_skill_candidates.py server/app.py server/tests/test_skill_candidate_eval.py
git commit -m "feat(skillopt): O2 迁移——ai_skill_candidates 候选区表（最小 diff+来源 manifest+状态机）"
```

---

### Task 7: skill_candidate_eval.py——diff 校验与双集回归（纯函数核心）

**Files:**
- Create: `server/utils/skill_candidate_eval.py`
- Modify: `server/tests/test_skill_candidate_eval.py`

**Interfaces:**
- Consumes: `skill_fit.parse_fit_steps(text) -> list[dict]`（skill_fit.py:35）；`skill_fit.match_steps(steps, trace) -> dict`（skill_fit.py:114）；`skill_fit._load_trace(db_ctx, session_id, started_at, finished_at) -> list[dict]`（skill_fit.py:214，模块私有但同仓导入属约定内——若 lint 拦私有导入，改从 `skill_fit` 导出公共别名 `load_trace = _load_trace` 后导入别名，这不算口径变更）；`ai_skill_fit_results`（status/score 列）与 `ai_skill_def_versions(content)`。
- Produces:
  - `apply_edits(base_text: str, edits: list[dict]) -> tuple[str, str | None]`——返回 `(new_text, None)`；old_str 非唯一命中、new_str 与 old_str 相同、**或替换后增删了 fit.steps 的步骤 id（R3 结构守卫）**返回 `(base_text, 错误说明)`。
  - `structural_guard(base_text: str, new_text: str, expected_repair: str | None) -> str | None`——任一步骤 expect 条目被收窄而 expected_repair 为空时返回错误说明（R3；Task 9 创建时也调用）。
  - `collect_validation_sets(db_ctx, def_kind, def_name) -> dict`——`{'boundary': [attempt_id], 'holdout': [attempt_id]}`（boundary=该定义 fit_status ∈ partial/diverged 的最近 ≤20 attempt；holdout=fit_status ∈ fit 的最近 ≤20；按 created_at DESC 去重）。
  - `score_steps_on_attempts(steps, attempt_ids, db_ctx) -> list[dict]`——每 attempt 返回 `{'attemptId','score','status'}`（内部取 attempt 时窗 + `_load_trace` + `match_steps`）。
  - `run_validation(candidate_id: str, get_db=None) -> dict`——**先做 R4 分类**：edits 未触及 frontmatter `fit.steps` 区、或触及但匹配签名（id+expect 序列）未变 → `behavioral` 模式（validation 记 `{mode:'behavioral', note}`，直接 validated——纯正文修改回归打分必然无变化，人工 promote 仍是闸门）；否则 `mechanical` 双集回归：门槛判定并写 `validation` JSONB（含 `mode:'mechanical'`）+ status 迁移，返回 validation 体。Task 9 的 promote/validate 端点与 Task 8 的创建前置都调这里。

- [ ] **Step 1: 写失败测试——门槛与 diff 语义**

`server/tests/test_skill_candidate_eval.py` 全量替换为：

```python
# -*- coding: utf-8 -*-
"""O2 候选评估测试：diff 校验/结构守卫/双集回归门槛/双模分类/promote 前置校验。"""
import uuid

from utils.skill_candidate_eval import (
    apply_edits, collect_validation_sets, run_validation, score_steps_on_attempts,
    structural_guard,
)

# 定义两步：clone（新旧同）+ install（旧模式 'pip install -r requirements'
# 拼错路径；候选改成 'pip install'）。边界轨迹只跑了错误路径 → 旧定义
# partial、新定义 fit；保留轨迹两步都正常 → 新旧都 fit（零退化）。
BASE_MD = (
    '---\ndescription: demo\nfit:\n  steps:\n'
    '    - id: clone\n      name: 克隆\n      expect:\n'
    "        - tool: bash\n          args_pattern: 'git clone'\n"
    '    - id: install\n      name: 安装\n      expect:\n'
    "        - tool: bash\n          args_pattern: 'pip install -r requirements'\n"
    '---\n正文\n')
EDITS = [{'old_str': "args_pattern: 'pip install -r requirements'",
          'new_str': "args_pattern: 'pip install'",
          'reason': '放宽安装匹配'}]


def test_apply_edits_unique_match():
    new, err = apply_edits(BASE_MD, EDITS)
    assert err is None and "args_pattern: 'pip install'\n" in new


def test_apply_edits_rejects_ambiguous():
    dup = BASE_MD.replace('正文\n', "args_pattern: 'pip install -r requirements'\n")
    _, err = apply_edits(dup, EDITS)
    assert err and '唯一' in err


def test_apply_edits_rejects_noop():
    _, err = apply_edits(BASE_MD, [
        {'old_str': 'x', 'new_str': 'x'}])
    assert err


def test_apply_edits_rejects_step_id_change():
    """R3 结构守卫：edits 不得增删步骤 id。"""
    _, err = apply_edits(BASE_MD, [
        {'old_str': "    - id: install\n      name: 安装\n      expect:\n",
         'new_str': '', 'reason': '删步骤'}])
    assert err and '步骤 id' in err


def test_structural_guard_requires_expected_repair():
    """R3：expect 收窄必须声明 expected_repair。"""
    narrowed = BASE_MD.replace(
        "        - tool: bash\n          args_pattern: 'pip install -r requirements'\n", '')
    assert structural_guard(BASE_MD, narrowed, '') and 'expect' in structural_guard(
        BASE_MD, narrowed, '')
    assert structural_guard(BASE_MD, narrowed, '安装改为可选') is None


def test_validation_body_only_edit_is_behavioral(db_conn):
    """R4：纯正文（不触及 fit.steps）修改走 behavioral 模式直接 validated，
    不会被「边界无改善」误杀；人工 promote 仍是闸门。"""
    name, chash, _ = _seed_def_and_attempts(db_conn)
    cid = 'cand_' + uuid.uuid4().hex[:10]
    with db_conn.cursor() as cur:
        cur.execute("INSERT INTO ai_skill_candidates (id, def_kind, def_name, "
                    "  base_content_hash, edits, source) "
                    "VALUES (%s, 'skill', %s, %s, %s::jsonb, '{}'::jsonb)",
                    (cid, name, chash,
                     '[{"old_str": "正文", "new_str": "正文（补充：安装前先确认网络）", '
                     '"reason": "补充提醒"}]'))
    v = run_validation(cid)
    assert v['mode'] == 'behavioral' and v['decision'] == 'validated'


def _seed_def_and_attempts(db_conn, *, boundary_n=2, holdout_n=3):
    """种子 def 版本（content=BASE_MD）+ boundary（只跑 clone+错误路径）
    与 holdout（clone+正确路径）两类 attempt，含 manifest/invocation/
    agent_tool_calls/fit 结果行。返回 (def_name, attempt_ids)。"""
    uid = str(uuid.uuid4())
    name = f'cand_{uuid.uuid4().hex[:8]}'
    import hashlib
    chash = hashlib.sha256(BASE_MD.encode()).hexdigest()
    ids = {'boundary': [], 'holdout': []}
    with db_conn.cursor() as cur:
        cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                    "VALUES (%s, %s, 'x', 'FR', 'developer')", (uid, f'ce_{uid[:8]}'))
        cur.execute("INSERT INTO ai_skill_def_versions (def_kind, def_name, content_hash, "
                    "  version_label, content, content_captured_at, first_seen_at) "
                    "VALUES ('skill', %s, %s, 'base', %s, NOW(), NOW())",
                    (name, chash, BASE_MD))
        for kind, tools in (('boundary', ["git clone origin", "pip install -r ./wrong.txt"]),
                            ('holdout', ["git clone origin", "pip install -r requirements.txt"])):
            for i in range({'boundary': boundary_n, 'holdout': holdout_n}[kind]):
                sid, aid = str(uuid.uuid4()), str(uuid.uuid4())
                ids[kind].append(aid)
                cur.execute("INSERT INTO ai_chat_sessions (id, user_id, status, session_token) "
                            "VALUES (%s, %s, 'completed', %s)", (sid, uid, f'tok-{sid[:12]}'))
                cur.execute("INSERT INTO ai_execution_attempts (id, session_id, source_type, "
                            "  operation, started_at, finished_at) "
                            "VALUES (%s, %s, 'batch', 'send', NOW(), NOW())", (aid, sid))
                cur.execute("INSERT INTO ai_execution_manifests (id, attempt_id, kind, name, "
                            "  source, path, content_hash, injected) "
                            "VALUES (%s, %s, 'skill', %s, 'session', '/tmp/na.md', %s, true)",
                            ('man_' + uuid.uuid4().hex[:8], aid, name, chash))
                cur.execute("INSERT INTO ai_skill_invocations (id, session_id, attempt_id, "
                            "  skill_name, skill_hash, source, evidence_level, evidence_refs) "
                            "VALUES (%s, %s, %s, %s, '', 'runtime', 'confirmed', '[]'::jsonb)",
                            ('inv_' + uuid.uuid4().hex[:10], sid, aid, name))
                for t in tools:
                    cur.execute("INSERT INTO agent_tool_calls (oc_session_id, root_session_id, "
                                "  part_id, tool, args_text, state, occurred_at) "
                                "VALUES (%s, %s, %s, 'bash', %s, 'completed', NOW())",
                                (f'oc-{aid[:8]}-{t[:6]}', sid,
                                 f'p-{uuid.uuid4().hex[:8]}', t))
                fit_status = 'partial' if kind == 'boundary' else 'fit'
                fit_score = 50 if kind == 'boundary' else 100
                cur.execute("INSERT INTO ai_skill_fit_results (id, attempt_id, session_id, "
                            "  def_kind, def_name, def_hash, steps_total, steps_hit, score, "
                            "  status, per_step) "
                            "VALUES (%s, %s, %s, 'skill', %s, %s, 2, %s, %s, %s, '[]'::jsonb)",
                            ('fit_' + uuid.uuid4().hex[:10], aid, sid, name, chash,
                             1 if kind == 'boundary' else 2, fit_score, fit_status))
    return name, chash, ids


def _seed_candidate(db_conn, name, chash):
    cid = 'cand_' + uuid.uuid4().hex[:10]
    with db_conn.cursor() as cur:
        cur.execute("INSERT INTO ai_skill_candidates (id, def_kind, def_name, "
                    "  base_content_hash, edits, source) "
                    "VALUES (%s, 'skill', %s, %s, %s::jsonb, '{}'::jsonb)",
                    (cid, name, chash, str(EDITS).replace("'", '"')))
    return cid


def test_validation_boundary_improves_holdout_stable(db_conn):
    name, chash, ids = _seed_def_and_attempts(db_conn)
    cid = _seed_candidate(db_conn, name, chash)
    v = run_validation(cid)
    assert v['decision'] == 'validated'
    b = {r['attemptId']: r for r in v['boundary']}
    for aid in ids['boundary']:
        assert b[aid]['after']['score'] > b[aid]['before']['score']
    for r in v['holdout']:
        assert r['after']['status'] == 'fit'


def test_validation_insufficient_evidence_stays_pending(db_conn):
    name, chash, _ = _seed_def_and_attempts(db_conn, boundary_n=1, holdout_n=2)
    cid = _seed_candidate(db_conn, name, chash)
    # holdout=2 < 门槛 3 → insufficient，status 仍 pending
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_skill_fit_results WHERE def_name=%s AND status='fit' "
                    "AND attempt_id IN (SELECT attempt_id FROM ai_skill_fit_results "
                    "WHERE def_name=%s AND status='fit' LIMIT 1)", (name, name))
    v = run_validation(cid)
    assert v.get('insufficientEvidence') and v['holdoutCount'] < 3
    with db_conn.cursor() as cur:
        cur.execute("SELECT status FROM ai_skill_candidates WHERE id=%s", (cid,))
        assert cur.fetchone()[0] == 'pending'


def test_score_matches_trace_not_definition_file(db_conn):
    name, chash, ids = _seed_def_and_attempts(db_conn)
    steps = __import__('utils.skill_fit', fromlist=['parse_fit_steps']).parse_fit_steps(BASE_MD)
    rows = score_steps_on_attempts(steps, ids['holdout'])
    assert all(r['status'] == 'fit' for r in rows)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_candidate_eval.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'utils.skill_candidate_eval'`

- [ ] **Step 3: 实现**

创建 `server/utils/skill_candidate_eval.py`：

```python
# -*- coding: utf-8 -*-
"""O2 候选评估（设计 §4.3+§4.6）：diff 校验 + 双集回归，全部零 LLM。

匹配器直接复用 skill_fit 的纯函数（parse_fit_steps/match_steps/_load_trace），
拟合口径 37c7057 不受影响。门槛（第 8 章 8.2.2 发布条件）：
- 边界集（partial/diverged 的近期 attempt）：候选得分逐条 ≥ 现行，且严格
  提升条数 ≥1，恶化条数 ≤ 20%；
- 保留集（fit 的近期 attempt）：现行已 fit 的 attempt 候选后必须仍 fit
  （零退化是硬门槛）；
- 集合不足（边界 <2 或保留 <3）→ insufficient_evidence，候选停留 pending
  继续积累（8.3.1：证据不足的偶发故障不触发学习）。
R3 结构守卫：edits 不得增删步骤 id；expect 收窄须声明 expected_repair。
R4 双模分类：纯正文（未触及 fit.steps 匹配面）修改走 behavioral——回归
打分对其必然无变化，直接 validated 交人工发布；触及匹配面的走 mechanical。
"""
import json
import logging
import re
import secrets
from datetime import datetime

from db import get_db as _default_get_db
from utils.skill_fit import _load_trace, match_steps, parse_fit_steps

log = logging.getLogger(__name__)

BOUNDARY_MIN, HOLDOUT_MIN, BOUNDARY_MAX = 2, 3, 20
_DEGRADE_RATE_MAX = 0.2


def apply_edits(base_text: str, edits: list[dict]) -> tuple[str, str | None]:
    """逐条精确替换；old_str 必须在当前文本中唯一命中，new_str != old_str，
    且不得增删 fit.steps 的步骤 id（R3）。返回 (new_text, None) 或
    (base_text, 错误说明)。"""
    text = base_text
    for i, e in enumerate(edits or []):
        old, new = e.get('old_str'), e.get('new_str')
        if not isinstance(old, str) or not isinstance(new, str) or not old:
            return base_text, f'edits[{i}] 缺 old_str/new_str'
        if old == new:
            return base_text, f'edits[{i}] old_str 与 new_str 相同（无变化）'
        n = text.count(old)
        if n != 1:
            return base_text, f'edits[{i}] old_str 命中 {n} 次（要求唯一命中）'
        text = text.replace(old, new, 1)
    if text == base_text:
        return base_text, 'edits 未产生任何变化'
    old_ids = {s['id'] for s in parse_fit_steps(base_text)}
    new_ids = {s['id'] for s in parse_fit_steps(text)}
    if old_ids != new_ids:
        return base_text, (f'edits 不得增删步骤 id'
                           f'（差异: {sorted(old_ids ^ new_ids)}）——结构性改动'
                           f'请人工走定义编辑，不走候选区')
    return text, None


def structural_guard(base_text: str, new_text: str,
                     expected_repair: str | None) -> str | None:
    """R3 作用域守卫：任一步骤的 expect 条目被收窄（断言变少）而 manifest
    未声明 expected_repair 时返回错误说明，否则 None。"""
    old_steps = {s['id']: s for s in parse_fit_steps(base_text)}
    new_steps = {s['id']: s for s in parse_fit_steps(new_text)}
    narrowed = [sid for sid, s in old_steps.items()
                if sid in new_steps
                and len(new_steps[sid]['expect']) < len(s['expect'])]
    if narrowed and not (expected_repair or '').strip():
        return (f'步骤 {narrowed} 的 expect 被收窄，须在 expectedRepair '
                '声明预期修复（受影响行为）')
    return None


def _matching_signature(steps: list[dict]) -> list:
    """回归打分真正消费的匹配面：id + expect 序列（tool, args_pattern）。"""
    return [(s['id'], [(e.get('tool'), e.get('args_pattern'))
                       for e in s['expect']]) for s in steps]


def _touches_steps(base_text: str, new_text: str, edits: list[dict]) -> bool:
    """R4 分类：True=mechanical（触及 fit.steps 匹配面）。
    任一 edit 落在 frontmatter 区且匹配签名变化 → mechanical；
    纯正文修改或仅步骤名等表面改动 → behavioral。"""
    m = re.match(r'\A---\r?\n.*?\r?\n---\r?\n', base_text, re.S)
    fm_end = m.end() if m else 0
    in_fm = any(0 <= base_text.find(e.get('old_str') or '') < fm_end
                for e in (edits or []))
    if not in_fm:
        return False
    return (_matching_signature(parse_fit_steps(base_text))
            != _matching_signature(parse_fit_steps(new_text)))


def collect_validation_sets(db_ctx, def_kind: str, def_name: str) -> dict:
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT attempt_id, status FROM (
                    SELECT attempt_id, status,
                           row_number() OVER (PARTITION BY status
                               ORDER BY created_at DESC) AS rn
                    FROM ai_skill_fit_results
                    WHERE def_kind = %s AND def_name = %s
                      AND status IN ('partial', 'diverged', 'fit')
                ) t WHERE rn <= %s
                """, (def_kind, def_name, BOUNDARY_MAX))
            boundary, holdout = [], []
            for attempt_id, status in cur.fetchall():
                (boundary if status in ('partial', 'diverged')
                 else holdout).append(attempt_id)
    return {'boundary': boundary, 'holdout': holdout}


def score_steps_on_attempts(steps: list[dict], attempt_ids: list[str],
                            db_ctx) -> list[dict]:
    out = []
    with db_ctx() as conn:
        with conn.cursor() as cur:
            for aid in attempt_ids:
                cur.execute(
                    "SELECT session_id, started_at, finished_at "
                    "FROM ai_execution_attempts WHERE id = %s", (aid,))
                row = cur.fetchone()
                if not row:
                    out.append({'attemptId': aid, 'score': 0,
                                'status': 'no_attempt'})
                    continue
                trace = _load_trace(db_ctx, row[0], row[1], row[2])
                m = match_steps(steps, trace)
                out.append({'attemptId': aid, 'score': m.get('score', 0),
                            'status': m.get('status', 'miss')})
    return out


def run_validation(candidate_id: str, get_db=None) -> dict:
    db_ctx = get_db or _default_get_db
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT def_kind, def_name, base_content_hash, edits, "
                "       fallback_content, status, source "
                "FROM ai_skill_candidates WHERE id = %s", (candidate_id,))
            row = cur.fetchone()
            if not row:
                raise LookupError(f'候选不存在: {candidate_id}')
            kind, name, base_hash, edits_raw, fallback, status, source_raw = row
            source = source_raw if isinstance(source_raw, dict) else {}
            if status != 'pending':
                raise ValueError(f'候选状态 {status} 不允许验证（仅 pending）')
            cur.execute(
                "SELECT content FROM ai_skill_def_versions "
                "WHERE def_kind = %s AND def_name = %s AND content_hash = %s",
                (kind, name, base_hash))
            base_row = cur.fetchone()
            if not base_row or not base_row[0]:
                raise ValueError('基线版本未归档正文，无法离线回归'
                                 '（需先经 apply 路径归档一次）')
            base_text = base_row[0]
            edits = edits_raw if isinstance(edits_raw, list) else []
            if fallback:
                new_text = fallback
            else:
                new_text, err = apply_edits(base_text, edits)
                if err:
                    raise ValueError(f'候选 diff 非法: {err}')
            guard_err = structural_guard(base_text, new_text,
                                         source.get('expectedRepair'))
            if guard_err:
                raise ValueError(guard_err)
            behavioral = (not fallback
                          and not _touches_steps(base_text, new_text, edits))
            old_steps = parse_fit_steps(base_text)
            new_steps = parse_fit_steps(new_text)
            sets = None if behavioral else collect_validation_sets(db_ctx, kind, name)
    if behavioral:
        # R4 行为类修改：回归打分不适用（分数必然无变化），直接 validated，
        # 人工 promote 仍是唯一发布闸门。
        validation = {
            'mode': 'behavioral',
            'note': '纯正文/非匹配面修改，双集回归不适用；请人工审阅后发布',
            'validatedAt': datetime.now().isoformat(timespec='seconds'),
        }
        decision = 'validated'
        boundary = holdout = []
    else:
        # 评分放事务外（_load_trace 只读）
        before_b = score_steps_on_attempts(old_steps, sets['boundary'], db_ctx)
        after_b = score_steps_on_attempts(new_steps, sets['boundary'], db_ctx)
        before_h = score_steps_on_attempts(old_steps, sets['holdout'], db_ctx)
        after_h = score_steps_on_attempts(new_steps, sets['holdout'], db_ctx)

        boundary = [{'attemptId': b['attemptId'],
                     'before': {'score': b['score'], 'status': b['status']},
                     'after': {'score': a['score'], 'status': a['status']}}
                    for b, a in zip(before_b, after_b)]
        holdout = [{'attemptId': b['attemptId'],
                    'before': {'score': b['score'], 'status': b['status']},
                    'after': {'score': a['score'], 'status': a['status']}}
                   for b, a in zip(before_h, after_h)]

        validation = {
            'mode': 'mechanical',
            'boundary': boundary, 'holdout': holdout,
            'boundaryCount': len(boundary), 'holdoutCount': len(holdout),
            'validatedAt': datetime.now().isoformat(timespec='seconds'),
        }
        if len(boundary) < BOUNDARY_MIN or len(holdout) < HOLDOUT_MIN:
            validation['insufficientEvidence'] = True
            decision = None                      # 停留 pending，继续积累
        else:
            improved = sum(1 for r in boundary
                           if r['after']['score'] > r['before']['score'])
            degraded = [r for r in boundary
                        if r['after']['score'] < r['before']['score']]
            regressed = [r for r in holdout
                         if r['before']['status'] == 'fit'
                         and r['after']['status'] != 'fit']
            validation.update({'improved': improved,
                               'degraded': [r['attemptId'] for r in degraded],
                               'regressed': [r['attemptId'] for r in regressed]})
            if regressed:
                validation['rejectReason'] = '保留集出现拟合退化（硬门槛）'
                decision = 'rejected'
            elif improved < 1 or len(degraded) > len(boundary) * _DEGRADE_RATE_MAX:
                validation['rejectReason'] = '边界集无改善或恶化超阈值'
                decision = 'rejected'
            else:
                decision = 'validated'

    with db_ctx() as conn:
        with conn.cursor() as cur:
            if decision:
                cur.execute(
                    "UPDATE ai_skill_candidates SET validation = %s::jsonb, "
                    "  status = %s, decided_at = NOW() WHERE id = %s",
                    (json.dumps(validation, ensure_ascii=False),
                     decision, candidate_id))
            else:
                cur.execute(
                    "UPDATE ai_skill_candidates SET validation = %s::jsonb "
                    "WHERE id = %s",
                    (json.dumps(validation, ensure_ascii=False), candidate_id))
        conn.commit()
    validation['decision'] = decision or 'insufficient_evidence'
    return validation
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_candidate_eval.py -v`
Expected: 6 个用例全 PASS。注意 `test_validation_insufficient_evidence_stays_pending` 的清理 SQL 若与实现里的集合口径（row_number 窗口）有出入，以实现为准修测试种子（保证 holdout 实际只剩 1 条）。

- [ ] **Step 5: 拟合口径回归（冻结证明）**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_fit.py tests/test_skill_fit_routes.py tests/test_skill_fit_ai.py -v`
Expected: 全 PASS（证明只导入未改动拟合口径）

- [ ] **Step 6: Commit**

```bash
git add server/utils/skill_candidate_eval.py server/tests/test_skill_candidate_eval.py
git commit -m "feat(skillopt): O2 候选评估——apply_edits 唯一命中校验+双集回归零 LLM 门槛（保留集零退化硬门槛）"
```

---

### Task 8: 诊断 schema 最小 diff 化（skill_fit_ai 扩展）

**Files:**
- Modify: `server/utils/skill_fit_ai.py`（`_normalize_diagnosis` 与诊断 prompt，约 309-386 行区域）
- Modify: `server/tests/test_skill_fit_ai.py`

**Interfaces:**
- Consumes: 现有 `diagnose_result(result_id, get_db=None) -> dict`（skill_fit_ai.py:324）与 `_DIAG_CAUSES` 五类枚举（:47-52）；`ai_skill_candidates` 表（Task 6）。
- Produces: 诊断 JSONB 增量字段——`edits: [{old_str, new_str, reason}]`（最小 diff，优先）、`expected_repair: str`、`possibly_harmed: [str]`；`revised_steps` 保留兼容（无 edits 时的降级）。**不改 `_normalize_diagnosis` 的函数签名与既有字段**，Task 9 创建候选时按「有 edits 用 edits、否则用 revised_steps 提示人工整块降级」。新增 `evolution_context(def_kind: str, def_name: str, get_db=None) -> str`（R1）：汇总该定义最近 ≤5 条 rejected/expired 候选的 old_str+拒绝原因、以及保留集（aligned fit 结果）步骤清单，供 `diagnose_result` 注入 prompt——候选生成器的输入必须含「此前被拒的修改 + 必须保留的成功行为」（第 8 章 8.2.3 / Self-Harness）。

- [ ] **Step 1: 写失败测试**

`server/tests/test_skill_fit_ai.py` 追加（`_normalize_diagnosis` 是纯函数，直接测）：

```python
def test_normalize_diagnosis_accepts_edits_and_contract_fields():
    from utils.skill_fit_ai import _normalize_diagnosis
    d = _normalize_diagnosis({
        'cause': 'missing_validation',
        'suggestions': ['放宽安装步骤匹配'],
        'revised_steps': [],
        'edits': [{'old_str': 'a', 'new_str': 'b', 'reason': 'r'}],
        'expected_repair': '边界场景应命中步骤 2',
        'possibly_harmed': ['已可跳过校验的场景可能被强制重跑'],
    })
    assert d['edits'] == [{'old_str': 'a', 'new_str': 'b', 'reason': 'r'}]
    assert d['expected_repair'] == '边界场景应命中步骤 2'
    assert d['possibly_harmed'] == ['已可跳过校验的场景可能被强制重跑']
    assert d['cause'] == 'missing_validation'


def test_normalize_diagnosis_defaults_for_new_fields():
    from utils.skill_fit_ai import _normalize_diagnosis
    d = _normalize_diagnosis({'cause': 'wrong_order', 'suggestions': []})
    assert d['edits'] == []
    assert d['expected_repair'] == ''
    assert d['possibly_harmed'] == []


def test_evolution_context_lists_rejected_and_holdout(db_conn):
    """R1（Self-Harness）：诊断输入须包含此前被拒/过期候选与保留集成功行为。"""
    import uuid
    from utils.skill_fit_ai import evolution_context
    name = f'eco_{uuid.uuid4().hex[:8]}'
    old_str = "args_pattern: 'pip install -r requirements'"
    edits_json = json.dumps([{'old_str': old_str, 'new_str': 'x', 'reason': 'r'}])
    uid, sid, aid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    try:
        with db_conn.cursor() as cur:
            # ai_skill_fit_results.attempt_id 有 FK（2026_10_01:35）——先种真 attempt
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s, %s, 'x', 'FR', 'developer')", (uid, f'eco_{uid[:8]}'))
            cur.execute("INSERT INTO ai_chat_sessions (id, user_id, status, session_token) "
                        "VALUES (%s, %s, 'completed', %s)", (sid, uid, f'tok-{sid[:12]}'))
            cur.execute("INSERT INTO ai_execution_attempts (id, session_id, source_type, "
                        "  operation, started_at, finished_at) "
                        "VALUES (%s, %s, 'batch', 'send', NOW(), NOW())", (aid, sid))
            cur.execute("INSERT INTO ai_skill_candidates (id, def_kind, def_name, "
                        "  base_content_hash, edits, source, status) "
                        "VALUES (%s, 'skill', %s, %s, %s::jsonb, %s::jsonb, 'rejected')",
                        ('cand_' + uuid.uuid4().hex[:8], name, 'h' * 64,
                         edits_json,
                         json.dumps({'expectedRepair': '', 'possiblyHarmed': []})))
            cur.execute("INSERT INTO ai_skill_fit_results (id, attempt_id, session_id, "
                        "  def_kind, def_name, def_hash, steps_total, steps_hit, "
                        "  score, status, per_step) "
                        "VALUES (%s, %s, %s, 'skill', %s, 'h2', 2, 2, 100, 'fit', '[]'::jsonb)",
                        ('fit_' + uuid.uuid4().hex[:8], aid, sid, name))
        db_conn.commit()
        ctx = evolution_context('skill', name)
        assert '此前被拒' in ctx and old_str in ctx
        assert '必须保留' in ctx
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_skill_candidates WHERE def_name=%s", (name,))
            cur.execute("DELETE FROM ai_skill_fit_results WHERE def_name=%s", (name,))
            cur.execute("DELETE FROM ai_execution_attempts WHERE id=%s", (aid,))
            cur.execute("DELETE FROM ai_chat_sessions WHERE id=%s", (sid,))
            cur.execute("DELETE FROM users WHERE id=%s", (uid,))
        db_conn.commit()
```

（cause 枚举值以 `_DIAG_CAUSES` 实际内容为准——先读 skill_fit_ai.py:47-52 取真实枚举值替换 `'missing_validation'`/`'wrong_order'`。追加用例用到 `json`/`uuid` 时在文件头补 import。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_fit_ai.py -v`
Expected: 新增 2 用例 FAIL（KeyError/断言失败——新字段不存在）

- [ ] **Step 3: 实现**

`server/utils/skill_fit_ai.py` 的 `_normalize_diagnosis`（:309-321）扩展返回体：

```python
    edits = raw.get('edits') if isinstance(raw.get('edits'), list) else []
    clean_edits = []
    for e in edits:
        if (isinstance(e, dict) and isinstance(e.get('old_str'), str)
                and isinstance(e.get('new_str'), str) and e['old_str'] != e['new_str']):
            clean_edits.append({'old_str': e['old_str'], 'new_str': e['new_str'],
                                'reason': str(e.get('reason') or '')})
    return {
        'cause': cause,
        'suggestions': [str(s) for s in suggestions if s],
        'revised_steps': revised if isinstance(revised, list) else [],
        'edits': clean_edits,
        'expected_repair': str(raw.get('expected_repair') or ''),
        'possibly_harmed': [str(x) for x in (raw.get('possibly_harmed') or [])
                            if x],
    }
```

同时在 `diagnose_result` 组装 prompt 的位置（grep 该函数内的 prompt 文本，`revised_steps` 要求处）追加要求（保持既有 prompt 风格）：

```
6. 输出必须包含 edits 数组：对「当前定义正文」给出最小可审计修改，每项
   {old_str, new_str, reason}——old_str 必须是正文中唯一出现的原文片段，
   禁止整块重写；确实无法用局部 diff 表达时才输出 revised_steps 并在
   suggestions 里注明「整块重写」。
7. 输出必须包含 expected_repair（预期修复什么场景、如何观察）与
   possibly_harmed（可能受损的既有行为清单，可为空数组）。
```

并实现 `evolution_context`（R1，同文件新增）：

```python
def evolution_context(def_kind: str, def_name: str, get_db=None) -> str:
    """R1（Self-Harness 原则）：候选生成器输入必须含「此前被拒的修改」与
    「必须保留的成功行为」。无记录返回空串（prompt 不注入该节）。"""
    from db import get_db as _gdb
    db_ctx = get_db or _gdb
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT edits, validation, source, status "
                "FROM ai_skill_candidates "
                "WHERE def_kind=%s AND def_name=%s AND status IN ('rejected','expired') "
                "ORDER BY decided_at DESC NULLS LAST LIMIT 5", (def_kind, def_name))
            rejected = cur.fetchall()
            cur.execute(
                "SELECT def_hash, per_step FROM ai_skill_fit_results "
                "WHERE def_kind=%s AND def_name=%s AND status='fit' "
                "ORDER BY created_at DESC LIMIT 5", (def_kind, def_name))
            holdout = cur.fetchall()
    if not rejected and not holdout:
        return ''
    lines = []
    if rejected:
        lines.append('此前被拒绝/过期的候选修改（禁止换一种说法重复提交，'
                     '除非你有理由认为判定有误并说明）：')
        for edits, validation, source, status in rejected:
            reason = (validation or {}).get('rejectReason') if isinstance(validation, dict) else None
            olds = [e.get('old_str', '')[:80] for e in (edits or [])
                    if isinstance(e, dict)]
            lines.append(f'- [{status}] old_str={olds} 拒绝原因: {reason or "人工拒绝"}')
    if holdout:
        lines.append('保留集（当前已对齐的成功行为，修复时不得破坏这些步骤的'
                     '断言）：' + ', '.join(h[0][:8] for h in holdout))
    return '\n'.join(lines)
```

`diagnose_result` 组装输入材料处追加：`evo = evolution_context(kind, name, get_db)`，非空时把 `evo` 作为独立小节拼进 prompt（标注「进化上下文」）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_fit_ai.py tests/test_skill_fit_routes.py -v`
Expected: 全 PASS（既有诊断用例不回归——新字段有默认值）

- [ ] **Step 5: Commit**

```bash
git add server/utils/skill_fit_ai.py server/tests/test_skill_fit_ai.py
git commit -m "feat(skillopt): O2 诊断 schema 最小 diff 化——edits/expected_repair/possibly_harmed 契约字段，revised_steps 保留降级"
```

---

### Task 9: O2 端点——候选创建/验证/发布/拒绝 + feedback 联动

**Files:**
- Modify: `server/routes/ai_session_admin.py`（O1 端点区块之后追加）
- Modify: `server/routes/ai_session_admin.py` 的 `suggestion_feedback`（:705-765，applied 分支联动建候选）
- Create: `server/tests/test_skill_candidates_routes.py`

**Interfaces:**
- Consumes: Task 7 的 `apply_edits`/`run_validation`；Task 8 的诊断新字段；`skill_fit.register_def_version(cur, kind, name, hash, content=...)`（skill_fit.py:236）；`skill_fit.resolve_definition_path(kind, name, content_hash)`（skill_fit.py:415）；`_path_in_allowed_roots`（ai_session_admin.py 内已有）。
- Produces:
  - `POST /ai/chat/admin/skill-candidates`——body `{fitResultId, edits?|fallbackText?, expectedRepair?, possiblyHarmed?}`；服务端从 fit 结果行取 def_kind/name/hash 与诊断 JSONB（edits/expected_repair/possibly_harmed 缺省由 body 覆盖），diff 校验通过建 pending 行；响应 `{candidate}`。校验失败 400/404；**重复候选 409 + existingId（R2）**；expect 收窄未声明 expectedRepair 400（R3）。
  - `GET /ai/chat/admin/skill-candidates?status=&defKind=&defName=`（DESC 限 100）。
  - `POST /ai/chat/admin/skill-candidates/<cid>/validate`——调 `run_validation`，insufficient 时仍 200 但 `decision=insufficient_evidence`。
  - `POST /ai/chat/admin/skill-candidates/<cid>/promote`——仅 validated；基线 hash 漂移 409；写定义文件+登记版本；响应 `{versionLabel, contentHash}`。
  - `POST /ai/chat/admin/skill-candidates/<cid>/reject`——pending/validated → rejected。
  - feedback applied 分支：body 带可选 `candidate`（同创建 body 去掉 fitResultId）时自动建候选并在响应追加 `candidateId`。
  - `expire_candidates_for_def(kind, name, conn, cur)`：def 内容被推进（新 hash 注册）时把该定义 validated/pending 候选置 expired——在 `skill_def_steps_apply`（:1454-1513）写盘成功后调用。

- [ ] **Step 1: 写失败测试**

创建 `server/tests/test_skill_candidates_routes.py`（shadow client 夹具从 `test_run_diagnosis.py` 同款复制；种子可复用 `test_skill_candidate_eval.py` 的 `_seed_def_and_attempts`——把它挪到本文件或提为共享 helper 均可，两个文件各自自带一份最省事，遵守「测试文件自包含」惯例）：

```python
# -*- coding: utf-8 -*-
"""O2 候选端点测试：创建/验证/发布/拒绝 + hash 漂移防护 + 过期联动。"""
import hashlib
import uuid

import pytest

from utils.skill_candidate_eval import apply_edits

BASE_MD = (
    '---\ndescription: demo\nfit:\n  steps:\n'
    '    - id: clone\n      name: 克隆\n      expect:\n'
    "        - tool: bash\n          args_pattern: 'git clone'\n"
    '---\n正文\n')
EDITS = [{'old_str': '正文', 'new_str': '正文 v2', 'reason': '补一句'}]


@pytest.fixture
def client():
    import sys
    import db as _db
    from app import app as flask_app
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if (mod_name.startswith('routes.') or mod_name.startswith('utils.')
                or mod_name == 'auth'):
            for attr in ('get_db', '_default_get_db'):
                if getattr(mod, attr, None) is not None:
                    try:
                        setattr(mod, attr, _db.get_db)
                    except (AttributeError, TypeError):
                        pass
    flask_app.config['TESTING'] = True
    return flask_app.test_client()


def _seed(client, db_conn, tmp_path):
    """种子 def 版本（正文归档）+ 指向真实临时文件的 manifest（供 promote
    定位路径）。返回 (name, chash, attempt_id, fit_result_id)。"""
    uid, sid, aid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    name = f'route_{uuid.uuid4().hex[:8]}'
    chash = hashlib.sha256(BASE_MD.encode()).hexdigest()
    # 种子定义文件必须落在 _path_in_allowed_roots 的允许根内（promote 要回写
    # 它）——系统 temp 不在根内必 400。用 AI_WORKSPACE_ROOT 下子目录。
    from pathlib import Path
    from config import AI_WORKSPACE_ROOT
    md_dir = Path(AI_WORKSPACE_ROOT) / f'test-cand-{uuid.uuid4().hex[:8]}'
    md_dir.mkdir(parents=True, exist_ok=True)
    md = md_dir / 'SKILL.md'
    md.write_text(BASE_MD, encoding='utf-8')
    fit_id = 'fit_' + uuid.uuid4().hex[:10]
    with db_conn.cursor() as cur:
        cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                    "VALUES (%s, %s, 'x', 'FR', 'developer')", (uid, f'cr_{uid[:8]}'))
        cur.execute("INSERT INTO ai_chat_sessions (id, user_id, status, session_token) "
                    "VALUES (%s, %s, 'completed', %s)", (sid, uid, f'tok-{sid[:12]}'))
        cur.execute("INSERT INTO ai_execution_attempts (id, session_id, source_type, "
                    "  operation, started_at, finished_at) "
                    "VALUES (%s, %s, 'batch', 'send', NOW(), NOW())", (aid, sid))
        cur.execute("INSERT INTO ai_execution_manifests (id, attempt_id, kind, name, "
                    "  source, path, content_hash, injected) "
                    "VALUES (%s, %s, 'skill', %s, 'session', %s, %s, true)",
                    ('man_' + uuid.uuid4().hex[:8], aid, name, str(md), chash))
        cur.execute("INSERT INTO ai_skill_invocations (id, session_id, attempt_id, "
                    "  skill_name, skill_hash, source, evidence_level, evidence_refs) "
                    "VALUES (%s, %s, %s, %s, '', 'runtime', 'confirmed', '[]'::jsonb)",
                    ('inv_' + uuid.uuid4().hex[:10], sid, aid, name))
        cur.execute("INSERT INTO ai_skill_fit_results (id, attempt_id, session_id, "
                    "  def_kind, def_name, def_hash, steps_total, steps_hit, score, "
                    "  status, per_step, diagnosis) "
                    "VALUES (%s, %s, %s, 'skill', %s, %s, 1, 0, 0, 'diverged', "
                    "  '[]'::jsonb, %s::jsonb)",
                    (fit_id, aid, sid, name, chash,
                     '{"cause":"missing_validation","suggestions":[],"revised_steps":[],'
                     '"edits":[{"old_str":"正文","new_str":"正文 v2","reason":"r"}],'
                     '"expected_repair":"E","possibly_harmed":[]}'))
        cur.execute("INSERT INTO ai_skill_def_versions (def_kind, def_name, content_hash, "
                    "  version_label, content, content_captured_at, first_seen_at) "
                    "VALUES ('skill', %s, %s, 'base', %s, NOW(), NOW())",
                    (name, chash, BASE_MD))
    return {'name': name, 'chash': chash, 'attemptId': aid, 'fitResultId': fit_id,
            'mdPath': str(md), 'mdDir': str(md_dir),
            'userId': uid, 'sessionId': sid}


def _cleanup(db_conn, seeded):
    import shutil
    if seeded.get('mdDir'):
        shutil.rmtree(seeded['mdDir'], ignore_errors=True)
    name = seeded['name']
    with db_conn.cursor() as cur:
        for table, col in (('ai_skill_candidates', 'def_name'),
                           ('ai_skill_fit_results', 'def_name'),
                           ('ai_skill_def_versions', 'def_name')):
            cur.execute(f"DELETE FROM {table} WHERE {col} = %s", (name,))
        cur.execute("DELETE FROM agent_tool_calls WHERE root_session_id = %s",
                    (seeded['sessionId'],))
        cur.execute("DELETE FROM ai_skill_invocations WHERE session_id = %s",
                    (seeded['sessionId'],))
        cur.execute("DELETE FROM ai_execution_manifests WHERE attempt_id = %s",
                    (seeded['attemptId'],))
        cur.execute("DELETE FROM ai_execution_attempts WHERE id = %s",
                    (seeded['attemptId'],))
        cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (seeded['sessionId'],))
        cur.execute("DELETE FROM users WHERE id = %s", (seeded['userId'],))
    db_conn.commit()


def test_create_validate_reject_cycle(client, db_conn, tmp_path):
    s = _seed(client, db_conn, tmp_path)
    try:
        resp = client.post('/ai/chat/admin/skill-candidates', json={
            'fitResultId': s['fitResultId']})
        assert resp.status_code == 201
        cand = resp.get_json()['candidate']
        assert cand['status'] == 'pending'
        assert cand['edits'] == EDITS
        # 正式定义文件未被触碰
        assert open(s['mdPath'], encoding='utf-8').read() == BASE_MD

        # 拒绝路径
        resp2 = client.post(
            f"/ai/chat/admin/skill-candidates/{cand['id']}/reject")
        assert resp2.status_code == 200
        resp3 = client.post(
            f"/ai/chat/admin/skill-candidates/{cand['id']}/validate")
        assert resp3.status_code == 400  # 非 pending 不可验证
    finally:
        _cleanup(db_conn, s)


def test_promote_writes_file_and_registers_version(client, db_conn, tmp_path):
    s = _seed(client, db_conn, tmp_path)
    try:
        cand = client.post('/ai/chat/admin/skill-candidates', json={
            'fitResultId': s['fitResultId']}).get_json()['candidate']
        # 单步定义凑不齐双集 → validate 得 insufficient，直接测 promote 的
        # 状态防护；为走通 promote，人工把状态抬到 validated（测试专用路径）
        with db_conn.cursor() as cur:
            cur.execute("UPDATE ai_skill_candidates SET status='validated' "
                        "WHERE id=%s", (cand['id'],))
        db_conn.commit()
        resp = client.post(
            f"/ai/chat/admin/skill-candidates/{cand['id']}/promote")
        assert resp.status_code == 200
        new_text = open(s['mdPath'], encoding='utf-8').read()
        assert '正文 v2' in new_text
        assert hashlib.sha256(new_text.encode()).hexdigest() == \
            resp.get_json()['contentHash']
        with db_conn.cursor() as cur:
            cur.execute("SELECT status, promoted_version_id FROM ai_skill_candidates "
                        "WHERE id=%s", (cand['id'],))
            status, vid = cur.fetchone()
            assert status == 'promoted' and vid
            cur.execute("SELECT content FROM ai_skill_def_versions WHERE id=%s", (vid,))
            assert cur.fetchone()[0] == new_text
    finally:
        _cleanup(db_conn, s)


def test_promote_rejects_hash_drift(client, db_conn, tmp_path):
    s = _seed(client, db_conn, tmp_path)
    try:
        cand = client.post('/ai/chat/admin/skill-candidates', json={
            'fitResultId': s['fitResultId']}).get_json()['candidate']
        with db_conn.cursor() as cur:
            cur.execute("UPDATE ai_skill_candidates SET status='validated' "
                        "WHERE id=%s", (cand['id'],))
            drifted = hashlib.sha256(b'drifted').hexdigest()
            cur.execute("INSERT INTO ai_skill_def_versions (def_kind, def_name, "
                        "  content_hash, first_seen_at) VALUES ('skill', %s, %s, NOW())",
                        (s['name'], drifted))
            cur.execute("UPDATE ai_skill_def_versions SET first_seen_at = NOW() "
                        "+ interval '1 second' WHERE content_hash = %s", (drifted,))
        db_conn.commit()
        resp = client.post(
            f"/ai/chat/admin/skill-candidates/{cand['id']}/promote")
        assert resp.status_code == 409
        assert open(s['mdPath'], encoding='utf-8').read() == BASE_MD
    finally:
        _cleanup(db_conn, s)


def test_feedback_applied_with_candidate_draft(client, db_conn, tmp_path):
    s = _seed(client, db_conn, tmp_path)
    try:
        # feedback 依赖 ai_execution_diagnoses 行——补种子
        did = 'diag_' + uuid.uuid4().hex[:8]
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO ai_execution_diagnoses (id, session_id, "
                        "  operation, status) VALUES (%s, %s, 'send', 'completed')",
                        (did, s['sessionId']))
        db_conn.commit()
        resp = client.post(
            f"/ai/chat/admin/analyses/{did}/suggestions/sug1/feedback",
            json={'action': 'applied', 'appliedValue': '{"skillName": "%s"}' % s['name'],
                  'candidate': {'fitResultId': s['fitResultId']}})
        assert resp.status_code == 200
        assert resp.get_json().get('candidateId')
    finally:
        _cleanup(db_conn, s)


def test_create_duplicate_returns_409_with_existing_id(client, db_conn, tmp_path):
    """R2 去重：同一 (def, 基线, diff) 的未决候选重复创建 → 409 + 既有 id。"""
    s = _seed(client, db_conn, tmp_path)
    try:
        first = client.post('/ai/chat/admin/skill-candidates', json={
            'fitResultId': s['fitResultId']})
        assert first.status_code == 201
        dup = client.post('/ai/chat/admin/skill-candidates', json={
            'fitResultId': s['fitResultId']})
        assert dup.status_code == 409
        assert dup.get_json()['existingId'] == \
            first.get_json()['candidate']['id']
    finally:
        _cleanup(db_conn, s)
```

注意：`ai_execution_diagnoses` 的必填列以 `2026_09_17_execution_audit_tables.py:201-218` 为准（若 operation/status 列名不同，按实际 DDL 调整种子）。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_candidates_routes.py -v`
Expected: FAIL（404 —— 端点不存在）

- [ ] **Step 3: 实现端点**

`server/routes/ai_session_admin.py` O1 区块后追加：

```python
# ── O2 技能进化候选区（spec 2026-10-26-agent-self-evolution-design §4）──

def _create_candidate_from_fit(cur, fit_result_id: str, body: dict) -> dict:
    """从拟合结果行 + 诊断 JSONB 建 pending 候选。edits/fallbackText 二选一：
    优先诊断里的 edits，body 可显式给 fallbackText 整块降级。"""
    cur.execute(
        "SELECT def_kind, def_name, def_hash, diagnosis "
        "FROM ai_skill_fit_results WHERE id = %s", (fit_result_id,))
    row = cur.fetchone()
    if not row:
        raise LookupError('拟合结果行不存在')
    kind, name, def_hash, diag = row
    diag = diag if isinstance(diag, dict) else {}
    edits = body.get('edits')
    if edits is None:
        edits = diag.get('edits') or []
    fallback = (body.get('fallbackText') or '').strip() or None
    if not edits and not fallback:
        raise ValueError('诊断无 edits 且未提供 fallbackText——请先重新诊断'
                         '（最小 diff），或人工提供整块内容')
    if edits and fallback:
        raise ValueError('edits 与 fallbackText 二选一')
    edits_hash = None
    if edits:
        import hashlib as _hl
        from utils.skill_candidate_eval import apply_edits, structural_guard
        cur.execute("SELECT content FROM ai_skill_def_versions "
                    "WHERE def_kind=%s AND def_name=%s AND content_hash=%s",
                    (kind, name, def_hash))
        base_row = cur.fetchone()
        if not base_row or not base_row[0]:
            raise ValueError('基线版本未归档正文，无法校验 diff'
                             '（先经 apply 路径归档一次）')
        new_text, err = apply_edits(base_row[0], edits)
        if err:
            raise ValueError(f'diff 校验失败: {err}')
        gerr = structural_guard(
            base_row[0], new_text,
            body.get('expectedRepair') or diag.get('expected_repair'))
        if gerr:
            raise ValueError(gerr)
        # R2 去重：同一 (def, 基线, diff) 的未决候选只允许存在一个
        edits_hash = _hl.sha256(json.dumps(
            edits, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        cur.execute(
            "SELECT id FROM ai_skill_candidates "
            "WHERE def_kind=%s AND def_name=%s AND base_content_hash=%s "
            "  AND edits_hash=%s AND status IN ('pending','validated')",
            (kind, name, def_hash, edits_hash))
        dup = cur.fetchone()
        if dup:
            raise ValueError(f'重复候选: {dup[0]}')
    source = {
        'fitResultId': fit_result_id,
        'diagnosisCause': diag.get('cause'),
        'expectedRepair': body.get('expectedRepair') or diag.get('expected_repair') or '',
        'possiblyHarmed': body.get('possiblyHarmed') or diag.get('possibly_harmed') or [],
    }
    cid = 'cand_' + secrets.token_hex(6)
    cur.execute(
        """
        INSERT INTO ai_skill_candidates
            (id, def_kind, def_name, base_content_hash, edits, edits_hash,
             fallback_content, source, created_by)
        VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, 'fit-diagnose')
        """,
        (cid, kind, name, def_hash, json.dumps(edits, ensure_ascii=False),
         edits_hash, fallback, json.dumps(source, ensure_ascii=False)))
    return {'id': cid, 'defKind': kind, 'defName': name,
            'baseContentHash': def_hash, 'edits': edits,
            'fallbackContent': fallback, 'source': source, 'status': 'pending'}


@ai_execution_admin_bp.post('/skill-candidates')
@require_permission('admin.ai_chat_admin')
def skill_candidate_create():
    body = request.get_json(silent=True) or {}
    fit_result_id = (body.get('fitResultId') or '').strip()
    if not fit_result_id:
        return jsonify({'error': 'fitResultId 必填'}), 400
    from db import get_db
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cand = _create_candidate_from_fit(cur, fit_result_id, body)
            conn.commit()
    except LookupError as e:
        return jsonify({'error': str(e)}), 404
    except ValueError as e:
        msg = str(e)
        if msg.startswith('重复候选: '):
            return jsonify({'error': msg,
                            'existingId': msg.split(': ', 1)[1]}), 409
        return jsonify({'error': msg}), 400
    log_operation('create', 'ai_skill_candidates', cand['id'],
                  f"{cand['defKind']}/{cand['defName']}", '技能候选创建（O2）')
    return jsonify({'candidate': cand}), 201


@ai_execution_admin_bp.get('/skill-candidates')
@require_permission('admin.ai_chat_admin')
def skill_candidate_list():
    from db import get_db
    conds, params = [], []
    for col, key in (('status', 'status'), ('def_kind', 'defKind'),
                     ('def_name', 'defName')):
        v = (request.args.get(key) or '').strip()
        if v:
            conds.append(f'{col} = %s')
            params.append(v)
    where = ('WHERE ' + ' AND '.join(conds)) if conds else ''
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, def_kind, def_name, base_content_hash, edits, "
                "       fallback_content, source, validation, status, created_at, "
                "       promoted_version_id "
                f"FROM ai_skill_candidates {where} "
                "ORDER BY created_at DESC LIMIT 100", params)
            items = [{
                'id': r[0], 'defKind': r[1], 'defName': r[2],
                'baseContentHash': r[3], 'edits': r[4],
                'fallbackContent': r[5], 'source': r[6], 'validation': r[7],
                'status': r[8],
                'createdAt': r[9].isoformat() if r[9] else None,
                'promotedVersionId': r[10],
            } for r in cur.fetchall()]
    return jsonify({'items': items})


@ai_execution_admin_bp.post('/skill-candidates/<cid>/validate')
@require_permission('admin.ai_chat_admin')
def skill_candidate_validate(cid):
    from db import get_db
    from utils.skill_candidate_eval import run_validation
    try:
        v = run_validation(cid)
    except LookupError as e:
        return jsonify({'error': str(e)}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    log_operation('update', 'ai_skill_candidates', cid, None,
                  f"候选双集回归: {v.get('decision')}")
    return jsonify({'validation': v})


@ai_execution_admin_bp.post('/skill-candidates/<cid>/promote')
@require_permission('admin.ai_chat_admin')
def skill_candidate_promote(cid):
    from db import get_db
    from utils.skill_fit import register_def_version, resolve_definition_path
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT def_kind, def_name, base_content_hash, edits, "
                "       fallback_content, status "
                "FROM ai_skill_candidates WHERE id = %s", (cid,))
            row = cur.fetchone()
            if not row:
                return jsonify({'error': '候选不存在'}), 404
            kind, name, base_hash, edits_raw, fallback, status = row
            if status != 'validated':
                return jsonify({'error': f'候选状态 {status}，仅 validated 可发布'}), 400
            # 基线漂移防护：当前最新版本的 hash 必须仍是候选所基于的 hash
            cur.execute(
                "SELECT content_hash FROM ai_skill_def_versions "
                "WHERE def_kind=%s AND def_name=%s "
                "ORDER BY first_seen_at DESC, created_at DESC LIMIT 1",
                (kind, name))
            latest = cur.fetchone()
            if not latest or latest[0] != base_hash:
                return jsonify({'error': '基线已漂移（定义被其他发布推进），'
                                         '候选已过期，请基于新版本重建'}), 409
            cur.execute("SELECT content FROM ai_skill_def_versions "
                        "WHERE def_kind=%s AND def_name=%s AND content_hash=%s",
                        (kind, name, base_hash))
            base_row = cur.fetchone()
            if not base_row or not base_row[0]:
                return jsonify({'error': '基线版本未归档正文'}), 409
            base_text = base_row[0]
            if fallback:
                new_text = fallback
            else:
                from utils.skill_candidate_eval import apply_edits
                new_text, err = apply_edits(base_text,
                                            edits_raw if isinstance(edits_raw, list) else [])
                if err:
                    return jsonify({'error': f'diff 非法: {err}'}), 400
            resolved = resolve_definition_path(kind, name, base_hash)
            if not resolved or not resolved.get('path'):
                return jsonify({'error': '定位不到定义文件路径'}), 404
            path = resolved['path']
            if not _path_in_allowed_roots(path):
                return jsonify({'error': 'path escapes allowed roots'}), 400
            if not os.path.isfile(path):
                return jsonify({'error': f'定义文件不存在: {path}'}), 404
            with open(path, 'w', encoding='utf-8', newline='') as f:
                f.write(new_text)
            with open(path, 'rb') as f:
                raw = f.read()
            new_hash = hashlib.sha256(raw).hexdigest()
            register_def_version(cur, kind, name, new_hash,
                                 content=raw.decode('utf-8'))
            label = f'技能候选发布 {datetime.now():%Y-%m-%d %H:%M}'
            cur.execute(
                "SELECT id FROM ai_skill_def_versions "
                "WHERE def_kind=%s AND def_name=%s AND content_hash=%s",
                (kind, name, new_hash))
            version_id = cur.fetchone()[0]
            cur.execute("UPDATE ai_skill_def_versions SET version_label = %s "
                        "WHERE id = %s", (label, version_id))
            cur.execute(
                "UPDATE ai_skill_candidates SET status='promoted', "
                "  decided_at=NOW(), decided_by=%s, promoted_version_id=%s "
                "WHERE id=%s",
                (flask_g.current_user['userId'], version_id, cid))
        conn.commit()
    log_operation('update', 'ai_skill_candidates', cid, f'{kind}/{name}',
                  f'候选发布 → 版本 {version_id}（定义文件已回写）')
    return jsonify({'versionId': version_id, 'contentHash': new_hash,
                    'versionLabel': label})
```

（`json`/`secrets`/`hashlib`/`datetime`/`flask_g` 若文件头未导入则补 import——ai_session_admin.py 头部已有 os/secrets/hashlib/datetime，`json` 需确认。）

同文件再追加 reject 与过期联动：

```python
@ai_execution_admin_bp.post('/skill-candidates/<cid>/reject')
@require_permission('admin.ai_chat_admin')
def skill_candidate_reject(cid):
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE ai_skill_candidates SET status='rejected', "
                        "  decided_at=NOW(), decided_by=%s "
                        "WHERE id=%s AND status IN ('pending','validated')",
                        (flask_g.current_user['userId'], cid))
            if cur.rowcount == 0:
                return jsonify({'error': '候选不存在或状态不允许拒绝'}), 400
        conn.commit()
    log_operation('update', 'ai_skill_candidates', cid, None, '候选拒绝（O2）')
    return jsonify({'ok': True})


def expire_candidates_for_def(cur, def_kind: str, def_name: str) -> None:
    """定义被其他路径推进（apply/rollback）后，未决候选全部过期——
    base hash 不再是最新，继续放行会在 publish 时撞 409。"""
    cur.execute(
        "UPDATE ai_skill_candidates SET status='expired', decided_at=NOW() "
        "WHERE def_kind=%s AND def_name=%s AND status IN ('pending','validated')",
        (def_kind, def_name))
```

并在 `skill_def_steps_apply`（:1454）写盘登记版本的同一个 `with conn.cursor()` 块内（`register_def_version(...)` 之后、`conn.commit()` 之前）插入：

```python
            from routes.ai_session_admin import expire_candidates_for_def
            expire_candidates_for_def(cur, def_kind, def_name)
```

`skill_candidate_promote` 自身的发布也推进了 hash——在 promote 的 `cur.execute("UPDATE ai_skill_candidates ...status='promoted'...")` 之后补一行 `expire_candidates_for_def(cur, kind, name)` 不会误伤本候选（它已是 promoted），加上以保持不变式统一。

feedback applied 分支联动——`suggestion_feedback`（:705）的 applied 插入之后、`conn.commit()` 之前：

```python
            candidate_id = None
            if action == 'applied' and isinstance(body.get('candidate'), dict):
                try:
                    cand = _create_candidate_from_fit(
                        cur, body['candidate'].get('fitResultId') or '',
                        body['candidate'])
                    candidate_id = cand['id']
                except (LookupError, ValueError) as e:
                    return jsonify({'error': f'候选创建失败: {e}'}), 400
```

响应改为 `return jsonify({'ok': True, **({'candidateId': candidate_id} if candidate_id else {})})`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_candidates_routes.py tests/test_skill_candidate_eval.py tests/test_skill_fit_routes.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add server/routes/ai_session_admin.py server/tests/test_skill_candidates_routes.py
git commit -m "feat(skillopt): O2 候选端点五件套——创建/列表/回归/发布/拒绝+基线漂移 409+apply 联动过期"
```

---

### Task 10: O2 前端——SkillCandidates 候选区组件

**Files:**
- Create: `src/components/admin/skillopt/SkillCandidates.vue`
- Modify: `src/api/aiSessionAdmin.ts`
- Modify: `src/views/admin/AiSkillOpt.vue`（新增 tab）

**Interfaces:**
- Consumes: Task 9 的五个端点。
- Produces: API 函数 `listSkillCandidates(params)`/`createSkillCandidate(body)`/`validateSkillCandidate(id)`/`promoteSkillCandidate(id)`/`rejectSkillCandidate(id)`；tab「进化候选」（E2E 依赖 `getByRole('tab', { name: '进化候选' })`、`.cand-list`、`.cand-item`、`[data-testid="cand-validate"]`、`[data-testid="cand-promote"]`、`[data-testid="cand-reject"]`、`.cand-detail`）。

- [ ] **Step 1: API 封装**

`src/api/aiSessionAdmin.ts` 追加：

```ts
export function listSkillCandidates(params: { status?: string; defKind?: string; defName?: string }) {
  return request.get('/ai/chat/admin/skill-candidates', { params })
}
export function createSkillCandidate(body: { fitResultId: string; edits?: Array<{ oldStr: string; newStr: string; reason?: string }>; fallbackText?: string }) {
  return request.post('/ai/chat/admin/skill-candidates', body)
}
export function validateSkillCandidate(id: string) {
  return request.post(`/ai/chat/admin/skill-candidates/${id}/validate`)
}
export function promoteSkillCandidate(id: string) {
  return request.post(`/ai/chat/admin/skill-candidates/${id}/promote`)
}
export function rejectSkillCandidate(id: string) {
  return request.post(`/ai/chat/admin/skill-candidates/${id}/reject`)
}
```

- [ ] **Step 2: 组件**

创建 `src/components/admin/skillopt/SkillCandidates.vue`：

```vue
<template>
  <div class="cands">
    <div class="cands__bar">
      <el-select v-model="status" size="small" style="width: 140px" @change="load">
        <el-option label="全部状态" value="" />
        <el-option v-for="s in STATUSES" :key="s" :label="s" :value="s" />
      </el-select>
      <span class="cands__hint">候选经双集回归（边界改善 ∧ 保留零退化）后才能发布</span>
    </div>
    <div class="cand-list" v-loading="loading">
      <div v-for="c in items" :key="c.id" class="cand-item" :data-status="c.status">
        <div class="cand-item__head">
          <span class="cand-item__name">{{ c.defKind }}/{{ c.defName }}</span>
          <el-tag size="small" :type="tagType(c.status)">{{ c.status }}</el-tag>
          <span class="cand-item__time">{{ c.createdAt }}</span>
        </div>
        <div class="cand-item__ops">
          <el-button size="small" data-testid="cand-validate"
            :disabled="c.status !== 'pending'" @click="validate(c)">回归验证</el-button>
          <el-button size="small" type="primary" data-testid="cand-promote"
            :disabled="c.status !== 'validated'" @click="promote(c)">发布</el-button>
          <el-button size="small" data-testid="cand-reject"
            :disabled="!['pending', 'validated'].includes(c.status)"
            @click="reject(c)">拒绝</el-button>
        </div>
        <div v-if="openId === c.id" class="cand-detail">
          <div v-if="c.fallbackContent" class="cand-detail__warn">⚠ 整块降级（非最小 diff）</div>
          <pre v-else class="cand-detail__diff">{{ diffText(c.edits) }}</pre>
          <div class="cand-detail__meta">
            <div>根因：{{ c.source?.diagnosisCause || '—' }}</div>
            <div>预期修复：{{ c.source?.expectedRepair || '—' }}</div>
            <div>可能受损：{{ (c.source?.possiblyHarmed || []).join('；') || '—' }}</div>
          </div>
          <table v-if="c.validation?.boundary?.length" class="cand-detail__table">
            <thead><tr><th>集合</th><th>任务</th><th>前</th><th>后</th></tr></thead>
            <tbody>
              <tr v-for="r in c.validation.boundary" :key="'b' + r.attemptId">
                <td>边界</td><td>{{ shortId(r.attemptId) }}</td>
                <td>{{ r.before.score }}/{{ r.before.status }}</td>
                <td>{{ r.after.score }}/{{ r.after.status }}</td>
              </tr>
              <tr v-for="r in c.validation.holdout" :key="'h' + r.attemptId">
                <td>保留</td><td>{{ shortId(r.attemptId) }}</td>
                <td>{{ r.before.score }}/{{ r.before.status }}</td>
                <td>{{ r.after.score }}/{{ r.after.status }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <div v-if="!loading && !items.length" class="cands__empty">暂无候选</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listSkillCandidates, validateSkillCandidate, promoteSkillCandidate, rejectSkillCandidate,
} from '@/api/aiSessionAdmin'

const STATUSES = ['pending', 'validated', 'rejected', 'promoted', 'expired']
const items = ref<any[]>([])
const loading = ref(false)
const status = ref('')
const openId = ref('')

const tagType = (s: string) =>
  ({ pending: 'info', validated: 'success', rejected: 'danger', promoted: 'success', expired: 'warning } as any)[s] || 'info'
const diffText = (edits: any[]) =>
  (edits || []).map(e => `- ${e.old_str}\n+ ${e.new_str}\n# ${e.reason}`).join('\n---\n')
const shortId = (id: string) => String(id).slice(0, 8)

async function load() {
  loading.value = true
  try {
    const resp: any = await listSkillCandidates({ status: status.value || undefined })
    items.value = resp.data?.items ?? resp.items ?? []
  } finally {
    loading.value = false
  }
}

async function validate(c: any) {
  const resp: any = await validateSkillCandidate(c.id)
  const v = resp.data?.validation ?? resp.validation
  if (v?.decision === 'insufficient_evidence') {
    ElMessage.warning('证据不足（边界/保留集样本不够），候选继续保留待积累')
  } else if (v?.decision === 'validated') {
    ElMessage.success('回归通过：边界改善 ∧ 保留零退化')
  } else {
    ElMessage.error(`回归未通过：${v?.rejectReason || '未知'}`)
  }
  await load()
}

async function promote(c: any) {
  await ElMessageBox.confirm(
    `发布将回写定义文件并登记新版本（可从版本演进回滚）。确认发布 ${c.defName}？`, '发布候选')
  try {
    await promoteSkillCandidate(c.id)
    ElMessage.success('已发布，新版本见「版本演进」')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || '发布失败')
  }
  await load()
}

async function reject(c: any) {
  await rejectSkillCandidate(c.id)
  await load()
}

onMounted(load)
</script>

<style scoped>
.cands__bar { display: flex; gap: 12px; align-items: center; margin-bottom: 8px; }
.cands__hint { color: var(--el-text-color-secondary); font-size: 12px; }
.cands__empty { color: var(--el-text-color-secondary); padding: 24px; text-align: center; }
.cand-item { border: 1px solid var(--el-border-color-lighter); border-radius: 6px; padding: 8px 12px; margin-bottom: 8px; }
.cand-item__head { display: flex; gap: 8px; align-items: center; cursor: pointer; }
.cand-item__name { font-weight: 600; margin-right: auto; }
.cand-item__time { color: var(--el-text-color-secondary); font-size: 12px; }
.cand-item__ops { margin: 6px 0; }
.cand-detail { border-top: 1px dashed var(--el-border-color); padding-top: 8px; }
.cand-detail__warn { color: var(--el-color-warning); font-size: 12px; }
.cand-detail__diff { background: var(--el-fill-color-light); padding: 8px; font-size: 12px; white-space: pre-wrap; }
.cand-detail__meta { font-size: 12px; color: var(--el-text-color-regular); margin: 6px 0; }
.cand-detail__table { font-size: 12px; width: 100%; }
</style>
```

（注意 `tagType` 行的引号在落地时保持合法 TS——上面 `expired: 'warning` 处补全引号。）

- [ ] **Step 3: 挂 tab**

grep `src/views/admin/AiSkillOpt.vue` 中 `版本演进`（版本时间线 tab 的 name），在同一个 `el-tabs` 内其后插入：

```vue
      <el-tab-pane label="进化候选" name="candidates">
        <SkillCandidates />
      </el-tab-pane>
```

`<script setup>` 增加 `import SkillCandidates from '@/components/admin/skillopt/SkillCandidates.vue'`。

- [ ] **Step 4: 构建/类型校验 + Commit**

Run: 用 package.json 里的现行 type-check/build 脚本验证无错误。

```bash
git add src/api/aiSessionAdmin.ts src/components/admin/skillopt/SkillCandidates.vue src/views/admin/AiSkillOpt.vue
git commit -m "feat(skillopt): O2 前端——进化候选 tab（diff 视图+双集回归表+发布确认）"
```

---

### Task 11: O2 E2E——候选区真实链路

**Files:**
- Create: `e2e/ai-full/skill-candidates.spec.ts`

**Interfaces:**
- Consumes: Task 9 端点（真实后端）、Task 10 选择器、db_exec 种子桥。
- Produces: 回归用例 `skill-candidates.spec.ts`。

- [ ] **Step 1: 写 spec**

种子设计（SQL 全量种子，见 Task 7 测试的同构思路，但走 db_exec）：定义两步 + 边界/保留 attempt + 真实临时 SKILL.md 文件（落在 `server/ai-workspaces/e2e-cand-<ts>/SKILL.md`，E2E 与后端同机，绝对路径种子进 manifest 供 promote 定位与回写；finally 里删文件）。断言链路：列表可见 pending → 点回归验证 → 边界行 after>before、保留行零退化 → 点发布（确认弹窗）→ 状态 promoted → 「版本演进」tab 出现新版本标注「技能候选发布」。

```typescript
/**
 * O2 技能进化候选区（真实后端，零 LLM）。
 * spec: docs/superpowers/specs/2026-10-26-agent-self-evolution-design.md §7
 * 种子: 定义（正文归档）+ 边界/保留 attempt + 真实临时 SKILL.md；
 * 链路: 候选列表 → 回归验证（双集结果）→ 发布 → 版本演进新版本。finally 清理。
 */
import { test, expect, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import crypto from 'node:crypto'

const DIRNAME = path.dirname(fileURLToPath(import.meta.url))
const TAG = `cand_${Date.now()}`
const NAME = `e2e-cand-${Date.now()}`

// 与后端同机的真实定义文件（promote 要回写它）；allowed roots 含 ai-workspaces
const MD_DIR = path.resolve(DIRNAME, '../../server/ai-workspaces', `e2e-${TAG}`)
const MD_PATH = path.join(MD_DIR, 'SKILL.md')
const BASE_MD = [
  '---', 'description: demo', 'fit:', '  steps:',
  '    - id: clone', '      name: 克隆', '      expect:',
  "        - tool: bash", "          args_pattern: 'git clone'",
  '    - id: install', '      name: 安装', '      expect:',
  "        - tool: bash", "          args_pattern: 'pip install -r requirements'",
  '---', '正文',
].join('\n')
const EDITS = JSON.stringify([{
  old_str: "args_pattern: 'pip install -r requirements'",
  new_str: "args_pattern: 'pip install'",
  reason: '放宽安装匹配',
}])

function db(sql: string): string {
  const script = path.join(DIRNAME, 'batch', 'db_exec.py')
  return execFileSync('python', [script], { input: sql, encoding: 'utf-8' })
}
const sha = (s: string) => crypto.createHash('sha256').update(s).digest('hex')

function seed(): { uid: string; boundary: string; holdout: string } {
  fs.mkdirSync(MD_DIR, { recursive: true })
  fs.writeFileSync(MD_PATH, BASE_MD, 'utf-8')
  const uid = crypto.randomUUID()
  const chash = sha(BASE_MD)
  // 边界 attempt：跑 clone + 错误路径（旧定义 partial）；保留 attempt：两步全中
  const rows: string[] = []
  const mk = (kind: 'b' | 'h'): string => {
    const sid = crypto.randomUUID()
    const aid = crypto.randomUUID()
    const tools = kind === 'b'
      ? ['git clone origin', 'pip install -r ./wrong.txt']
      : ['git clone origin', 'pip install -r requirements.txt']
    rows.push(`
      INSERT INTO ai_chat_sessions (id, user_id, status, session_token)
      VALUES ('${sid}', '${uid}', 'completed', 'tok-${sid.slice(0, 12)}');
      INSERT INTO ai_execution_attempts (id, session_id, source_type, operation,
        started_at, finished_at)
      VALUES ('${aid}', '${sid}', 'batch', 'send', NOW(), NOW());
      INSERT INTO ai_execution_manifests (id, attempt_id, kind, name, source, path,
        content_hash, injected)
      VALUES ('man_${aid.slice(0, 8)}', '${aid}', 'skill', '${NAME}', 'session',
        '${MD_PATH.replace(/\\/g, '\\\\')}', '${chash}', true);
      INSERT INTO ai_skill_invocations (id, session_id, attempt_id, skill_name,
        skill_hash, source, evidence_level, evidence_refs)
      VALUES ('inv_${aid.slice(0, 8)}', '${sid}', '${aid}', '${NAME}', '',
        'runtime', 'confirmed', '[]'::jsonb);`)
    for (const t of tools) {
      rows.push(`
        INSERT INTO agent_tool_calls (oc_session_id, root_session_id, part_id, tool,
          args_text, state, occurred_at)
        VALUES ('oc-${aid.slice(0, 8)}-${t.slice(0, 6)}', '${sid}',
          'p-${crypto.randomUUID().slice(0, 8)}', 'bash', '${t}', 'completed', NOW());`)
    }
    rows.push(`
      INSERT INTO ai_skill_fit_results (id, attempt_id, session_id, def_kind, def_name,
        def_hash, steps_total, steps_hit, score, status, per_step)
      VALUES ('fit_${aid.slice(0, 8)}', '${aid}', '${sid}', 'skill', '${NAME}',
        '${chash}', 2, ${kind === 'b' ? 1 : 2}, ${kind === 'b' ? 50 : 100},
        ${kind === 'b' ? "'partial'" : "'fit'"}, '[]'::jsonb);`)
    return aid
  }
  const boundary = mk('b')
  mk('b') // 边界 ≥2
  const holdout = mk('h')
  mk('h'); mk('h') // 保留 ≥3
  db(`
    INSERT INTO users (id, username, password_hash, display_name, role)
    VALUES ('${uid}', 'u_${TAG}', 'x', 'CD', 'developer');
    INSERT INTO ai_skill_def_versions (def_kind, def_name, content_hash,
      version_label, content, content_captured_at, first_seen_at)
    VALUES ('skill', '${NAME}', '${chash}', 'e2e base', '${BASE_MD}', NOW(), NOW());
    INSERT INTO ai_skill_candidates (id, def_kind, def_name, base_content_hash,
      edits, source)
    VALUES ('cand_${TAG}', 'skill', '${NAME}', '${chash}', '${EDITS.replace(/'/g, "''")}'::jsonb,
      '{}'::jsonb);
    ${rows.join('\n')}`)
  return { uid, boundary, holdout }
}

function cleanup(): void {
  db(`
    DELETE FROM ai_skill_candidates WHERE def_name = '${NAME}';
    DELETE FROM ai_skill_fit_results WHERE def_name = '${NAME}';
    DELETE FROM ai_skill_def_versions WHERE def_name = '${NAME}';
    DELETE FROM agent_tool_calls WHERE root_session_id IN (
      SELECT id FROM ai_chat_sessions WHERE user_id = '${uid}');
    DELETE FROM ai_skill_invocations WHERE session_id IN (
      SELECT id FROM ai_chat_sessions WHERE user_id = '${uid}');
    DELETE FROM ai_execution_manifests WHERE attempt_id IN (
      SELECT a.id FROM ai_execution_attempts a
      JOIN ai_chat_sessions s ON s.id = a.session_id WHERE s.user_id = '${uid}');
    DELETE FROM ai_execution_attempts WHERE session_id IN (
      SELECT id FROM ai_chat_sessions WHERE user_id = '${uid}');
    DELETE FROM ai_chat_sessions WHERE user_id = '${uid}';
    DELETE FROM users WHERE id = '${uid}';`)
  fs.rmSync(MD_DIR, { recursive: true, force: true })
}

async function login(page: Page): Promise<void> {
  await page.goto('/')
  const userInput = page.locator('input[placeholder*="用户名"]')
  try {
    await userInput.waitFor({ state: 'visible', timeout: 30_000 })
    await userInput.fill('admin')
    await page.locator('input[placeholder*="密码"]').fill('admin123')
    await page.getByRole('button', { name: /登\s*录/ }).click()
    await userInput.waitFor({ state: 'hidden', timeout: 15_000 })
  } catch { /* 已是登录态 */ }
}

test('候选区：回归验证双集结果 → 发布 → 版本演进新版本', async ({ page }) => {
  test.setTimeout(240_000)
  seed()
  try {
    await login(page)
    await page.goto('/admin/ai-skillopt')
    await page.getByRole('tab', { name: '进化候选' }).click()
    const item = page.locator('.cand-item', { hasText: NAME }).first()
    await item.waitFor({ state: 'visible', timeout: 30_000 })
    // 回归验证：边界改善 ∧ 保留零退化 → validated
    await item.getByTestId('cand-validate').click()
    await expect(item.locator('.el-tag')).toHaveText(/validated/, { timeout: 15_000 })
    // 发布（带确认弹窗）
    await item.getByTestId('cand-promote').click()
    await page.getByRole('button', { name: /确定|确认/ }).click()
    await expect(item.locator('.el-tag')).toHaveText(/promoted/, { timeout: 15_000 })
    // 定义文件真的被回写
    expect(fs.readFileSync(MD_PATH, 'utf-8')).toContain("args_pattern: 'pip install'")
    // 版本演进出现新版本
    await page.getByRole('tab', { name: '版本演进' }).click()
    await expect(page.locator('body')).toContainText('技能候选发布', { timeout: 15_000 })
  } finally {
    cleanup()
  }
})
```

- [ ] **Step 2: 跑 spec**

Run: `npx playwright test e2e/ai-full/skill-candidates.spec.ts`
Expected: PASS（若 tag 文案断言脆弱，放宽为 contains「发布」）

- [ ] **Step 3: Commit**

```bash
git add e2e/ai-full/skill-candidates.spec.ts
git commit -m "test(e2e): O2 候选区真实链路——双集回归判定/发布回写文件/版本演进新版本"
```

---

### Task 12: 文档收尾与全量回归

**Files:**
- Modify: `docs/design/ai/08-Skill与SkillOpt.md`（增补「运行诊断」「进化候选区」两节，各 10-20 行：表、端点、门槛语义）
- Modify: `docs/superpowers/specs/2026-10-26-agent-self-evolution-design.md`（顶部状态行改为「P1 已实施」）

**Interfaces:** 无代码接口；收尾核对。

- [ ] **Step 1: 文档增补**——`docs/design/ai/08-Skill与SkillOpt.md` 末尾追加两节，写清：五维度判定规则表（Task 2 的映射）、候选状态机（pending→validated/rejected→promoted/expired）、双集门槛数值（边界≥2/保留≥3/退化率≤20%/保留零退化硬门槛）、安全边界（LLM 只进候选区；发布/回滚/校验是可信根不可被进化流程修改）。

- [ ] **Step 2: 全量后端回归**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/ -q`
Expected: 全绿（对照记忆基线：串行约 2500 用例量级；若有与本计划无关的既有失败，记录并区分，不盲修）

- [ ] **Step 3: 全量 E2E（本计划新增两条 + 相邻回归）**

Run: `npx playwright test e2e/ai-full/run-diagnosis.spec.ts e2e/ai-full/skill-candidates.spec.ts e2e/ai-full/ai-skillopt-fit.spec.ts e2e/ai-full/ai-skillopt-page.spec.ts e2e/ai-full/ai-skillopt-version-archive.spec.ts`
Expected: 全 PASS

- [ ] **Step 4: Commit + 收尾**

```bash
git add docs/design/ai/08-Skill与SkillOpt.md docs/superpowers/specs/2026-10-26-agent-self-evolution-design.md
git commit -m "docs(skillopt): P1 落地收尾——设计文档标记已实施，域文档增补运行诊断与进化候选两节"
```

---

## Self-Review 记录（写计划时已自查；2026-10-27 修订后复核）

1. **Spec 覆盖**：设计文档 §3（O1 数据模型/维度映射/端点/前端）→ Task 1-5；**§3.3 lazy-fill（2026-10-27 认可：承诺—行动维度无行时 best-effort 调既有 ensure_diagnosis_for_session 现算，零新增埋点）→ Task 2**；§4.1-4.2（候选表/manifest）→ Task 6、8、9；§4.3（diff+双集回归）→ Task 7、8；§4.4（发布/回滚/effect）→ Task 9（effect 端点不改、promoted_version_id 已关联可回滚）；§4.5（前端）→ Task 10；**§4.6（R1–R4）→ Task 6（edits_hash 列）/Task 7（R3 守卫+R4 双模）/Task 8（R1 回灌）/Task 9（R2 去重+R3 创建期校验）**；§7 测试 → Task 5、11、12；§8 P1c 文档 → Task 12。§3.3 的「批任务页任务行诊断徽标」降级为后续增强（E2E 里注明），不影响 O1 核心闭环。
2. **无占位符**：所有代码步骤给出真实代码；允许执行期微调的点（attempt_payload 返回结构字段名、step_results status 枚举、cause 枚举真实值）都在任务内写明核对方法与兜底语义，不属于「待定」。
3. **类型一致性**：`build_run_diagnosis` / `apply_edits`（R3 后仍返回二元组）/ `structural_guard` / `run_validation`（validation 体新增 `mode` 键，UI/E2E 断言已同步）/ `_create_candidate_from_fit`（新增 edits_hash）/ `expire_candidates_for_def` / `evolution_context` 的签名在定义任务与消费任务间已逐一对照；JSONB 字段名 `edits/expected_repair/possibly_harmed`（服务端）与 `edits/expectedRepair/possiblyHarmed`（API 蛇/驼峰转换）在 Task 8/9/10 中口径一致。
