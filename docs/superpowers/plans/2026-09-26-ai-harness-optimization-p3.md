# AI Harness P3 优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 平台成熟度：PG LISTEN/NOTIFY 替代轮询、Runtime Adapter 接线、编排管理面前端骨架、实时仪表数据层、PreToolUse 门禁拦截。

**Architecture:** 7 项分 4 个批次。C1（LISTEN/NOTIFY）是基础设施改动；A8（Adapter 接线）是执行路径重构；A7+C2 是前端/可观测层；C3 是插件+门禁联动。B5/C4 需 OpenCode 插件层配合，本批登记不实施。

**Tech Stack:** Flask/Python, PostgreSQL (LISTEN/NOTIFY), Vue 3/TypeScript, Pytest

**Spec:** `docs/superpowers/specs/2026-09-26-ai-harness-optimization-spec.md` §5

## Global Constraints

- 不替换 OpenCode runtime（A8 只做接线，行为不变可回退）
- PG LISTEN/NOTIFY 不引入新依赖（psycopg2 原生支持）
- 前端改动不破坏既有轮询降级
- 插件改动与 baize-trace 同机制（OC serve 需重启生效）
- B5/C4 登记后续（需 OC 插件层配合，超出 Flask 范围）

---

### Task 1: C1 — PG LISTEN/NOTIFY 替代 worker 轮询

**Files:**
- Modify: `server/utils/batch_engine.py`（dispatcher 加 LISTEN）
- Create: `server/migrations/2026_09_26_batch_notify_trigger.py`（幂等 trigger）
- Test: `server/tests/test_review_gap_tests.py`

**Interfaces:**
- Produces: INSERT on `ai_chat_sessions`（status='pending'）触发 `pg_notify('batch_claim_ready')` → worker 即时唤醒 claim

- [ ] **Step 1: 写失败测试**

```python
def test_notify_trigger_exists(db_conn):
    """P3-C1：INSERT pending 子会话触发 pg_notify。"""
    with db_conn.cursor() as cur:
        cur.execute("""
            SELECT count(*) FROM pg_trigger
            WHERE tgname = 'notify_batch_claim_ready'
        """)
        assert cur.fetchone()[0] >= 1
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

迁移 `2026_09_26_batch_notify_trigger.py`：

```python
def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE OR REPLACE FUNCTION notify_batch_claim() RETURNS trigger AS $$
            BEGIN
                IF NEW.status = 'pending' AND OLD.status IS DISTINCT FROM 'pending' THEN
                    PERFORM pg_notify('batch_claim_ready', '');
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """)
        cur.execute("""
            DROP TRIGGER IF EXISTS notify_batch_claim_ready ON ai_chat_sessions;
            CREATE TRIGGER notify_batch_claim_ready
                AFTER UPDATE OR INSERT ON ai_chat_sessions
                FOR EACH ROW EXECUTE FUNCTION notify_batch_claim()
        """)
        conn.commit()
```

`batch_engine.py::_dispatcher_loop` 加 LISTEN 连接：

```python
# dispatcher 线程内：维护一条专用 LISTEN 连接
listen_conn = psycopg2.connect(**DB_CONFIG, async_=False)
listen_conn.autocommit = True
lc = listen_conn.cursor()
lc.execute('LISTEN batch_claim_ready')
# 在 select loop 中：select.select([listen_conn], [], [], timeout) → conn.poll() → if notify: self._wake.set()
```

- [ ] **Step 4: 跑测试确认通过 + 全量**

- [ ] **Step 5: Commit**

---

### Task 2: A8 — Runtime Adapter 生产接线

**Files:**
- Modify: `server/utils/batch_engine.py`（`_OpenCodeFacade._client()` 经 `get_runtime()`）
- Test: `server/tests/test_review_gap_tests.py`

**Interfaces:**
- Consumes: `server/utils/runtime/__init__.py::get_runtime()` + `runtime/base.py::AgentRuntime` + `runtime/opencode_local.py::OpenCodeLocalRuntime`
- Produces: batch_engine 的 OpenCode 调用经 adapter（行为不变可切换）

- [ ] **Step 1: 写失败测试**

```python
def test_batch_engine_uses_runtime_adapter():
    """P3-A8：batch_engine 的 OpenCode 调用经 get_runtime()。"""
    # monkeypatch get_runtime → 断言被调用（而非直接调 OpenCodeClient）
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

`batch_engine.py` 的 `_OpenCodeFacade._client()` 方法改为：

```python
def _client(self):
    from utils.runtime import get_runtime
    rt = get_runtime()
    if rt:
        return rt.get_client()
    from utils.opencode_client import OpenCodeClient
    from config import OPENCODE_BASE_URL
    return OpenCodeClient(OPENCODE_BASE_URL)
```

`runtime/opencode_local.py` 加 `get_client()` 方法返回 `OpenCodeClient` 实例。

保持向后兼容：`get_runtime()` 返回 None 或 opencode_local 时行为与直接调 OpenCodeClient 相同。

- [ ] **Step 4: 跑测试确认通过 + 全量**

- [ ] **Step 5: Commit**

---

### Task 3: A7 — 编排管理面前端骨架

**Files:**
- Create: `src/views/admin/AiOrchestrationManager.vue`
- Create: `src/api/orchestration.ts`
- Modify: `src/router/index.ts`（注册路由）
- Test: `src/views/admin/__tests__/AiOrchestrationManager.test.ts`

**Interfaces:**
- Consumes: `GET/POST /ai/orchestrations/definitions`、`GET /ai/orchestrations/runs` 等（已存在）
- Produces: 管理页面展示 definition 列表 + run 列表 + step 状态

- [ ] **Step 1: 写失败测试**

vitest：组件渲染 + API mock + 状态展示断言

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

`src/api/orchestration.ts`：类型定义 + API 封装（definitions CRUD / runs 列表 / steps 查询 / 审批）

`AiOrchestrationManager.vue`：
- Definition 列表（id/name/version/enabled）
- Run 列表（status/definition/started/finished）
- Run 详情展开：step 表格（node_id/status/error/耗时）

注册到 admin 路由 `admin/ai-orchestrations`。

- [ ] **Step 4: 跑测试确认通过 + `npm run build` 确认无 TS 错误**

- [ ] **Step 5: Commit**

---

### Task 4: C2 — 实时仪表数据层

**Files:**
- Modify: `server/routes/metrics.py`（增 attempt 生命周期指标）
- Modify: `server/routes/ai_batch_admin.py`（增 attempt timeline API）
- Test: `server/tests/test_review_gap_tests.py`

**Interfaces:**
- Produces: `GET /ai/chat/admin/sessions/<sid>/attempt-timeline` → attempt 生命周期 JSON

- [ ] **Step 1: 写失败测试**

```python
def test_attempt_timeline_endpoint(app):
    """P3-C2：attempt timeline API 返回结构化时间线。"""
    # 构造 session + attempts
    # GET → 断言 attempts 数组含 status/started_at/finished_at/duration_ms
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

`ai_batch_admin.py` 新增端点：查 `ai_execution_attempts` WHERE session_id=… ORDER BY attempt_no，返回 JSON 数组。

`metrics.py` 增加 `ai_attempts_running` gauge。

- [ ] **Step 4: 跑测试确认通过**

- [ ] **Step 5: Commit**

---

### Task 5: C3 — action gate PreToolUse 拦截

**Files:**
- Modify: `server/utils/skillopt.py`（插件模板扩展 pre-check 逻辑）
- Create: `server/routes/ai_gate_internal.py`（内部门禁校验端点）
- Modify: `server/app.py`（注册蓝图）

**Interfaces:**
- Consumes: `action_expectations` 表（`mode='pre'` 的行——需迁移加列）
- Produces: OpenCode 工具调用前 POST Flask 校验 → deny 则阻断

- [ ] **Step 1: 设计决策**

当前 action gate 是终态核对（"该做的做了没"）。PreToolUse 拦截是"不允许做的别做"：
- `action_expectations` 加 `mode` 列（默认 `'post'`，新增 `'pre'` 支持）
- `pre` 模式的期望含义反转："不允许调用 args_pattern 匹配的工具"（deny list）
- OC 插件在工具调用前 POST `/ai/gate/pre-check` → Flask 查 deny list → 返回 `{"allow": false}` → 插件阻断

**涉及文件**：迁移（加列）+ `agent_ledger.py`（pre-check 函数）+ `ai_gate_internal.py`（新路由）+ `skillopt.py` 插件模板（加 pre-check 逻辑）

**涉及量**：~80 行 Python + ~30 行 JS

**测试**：pytest 断言 pre-check 端点返回 allow/deny；插件 JS 不在本批测试范围

- [ ] **Step 2-5: TDD 实现 + Commit**

---

### Task 6: B5 + C4 — 登记（不实施）

B5（子代理独立取消）与 C4（effect 自动补偿）需要 OpenCode 插件层深度配合。在 `docs/superpowers/specs/` 本 spec 的 §5 已有概要设计。本批**不实施**，登记后续。

---

## Self-Review

**1. Spec coverage：** A7→Task 3 ✓ A8→Task 2 ✓ C1→Task 1 ✓ C2→Task 4 ✓ C3→Task 5 ✓ B5/C4→Task 6 登记 ✓

**2. Placeholder scan：** Task 1 的 LISTEN 实现需要 psycopg2 异步连接管理——已给出伪代码，实现时需确认 select/poll 用法。✓

**3. Type consistency：** N/A（各任务独立文件）✓
