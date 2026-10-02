# AI Harness P2 优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 编排可靠性（step 超时/join 汇聚/定义透传）+ 并发治理（可配/统一租约/workspace TTL）+ effect 补漏 + 实时 usage。

**Architecture:** 8 项分 5 个批次。编排引擎改动（A5/A6/A3）集中在 `orchestration_engine.py`/`orchestration_defs.py`；并发治理（B1/B2/B4）分散在多个调度器文件；effect/usage（B6/C5）在 `ai_scan_engine.py`/`batch_engine.py`。每批次独立可测试可提交。

**Tech Stack:** Flask/Python, PostgreSQL/psycopg2, Pytest

**Spec:** `docs/superpowers/specs/2026-09-26-ai-harness-optimization-spec.md` §4

## Global Constraints

- 所有新增测试必须在 base（`798ecd6`）上失败（判别性）
- 共享 dev 库：测试自带清理
- 不删除既有字段或行为（增量）
- 所有后台调度器统一走 `execution_lease.acquire(key, kind)`

---

### Task 1: A5 + A6 — Step 超时兜底 + join 汇聚

**Files:**
- Modify: `server/utils/orchestration_engine.py`
- Modify: `server/utils/orchestration_defs.py`
- Test: `server/tests/test_review_gap_tests.py`（追加）

**Interfaces:**
- Produces: definition 节点支持 `timeout_sec` 字段；join 节点支持 `join_policy: 'all_success'|'any_success'`

- [ ] **Step 1: 写失败测试**

在 `test_review_gap_tests.py` 追加：

```python
def test_step_timeout_marks_failed(app):
    """P2-A5：step 超时后标 failed，run 不永久 running。"""
    # 构造：definition 带 timeout_sec=0（立即超时）的 step → run
    # _advance_run 调度后 step 超过 timeout → failed
    # 断言：step.status == 'failed'，run.status != 'running'

def test_join_any_success_policy(app):
    """P2-A6：join_policy='any_success' 时一侧 skip 不阻塞 join。"""
    # 构造：菱形 DAG，join_policy='any_success'
    # 一侧 succeeded、另一侧 skipped → join = succeeded
    # 断言：join.status == 'succeeded'，run.status == 'completed'
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && python -m pytest tests/test_review_gap_tests.py::test_step_timeout_marks_failed tests/test_review_gap_tests.py::test_join_any_success_policy -v`
Expected: FAIL

- [ ] **Step 3: 实现**

`orchestration_engine.py`：

**超时**：在 `_advance_run` 的调度循环中，对 `status='running'` 的 step 检查 `started_at + timeout_sec < NOW()`，超时则 `UPDATE SET status='failed', error_message='step timeout'`。`timeout_sec` 从 node definition 读取，默认 `timeout_policy.default_sec`（definition 级）或硬编码 900。

**join 汇聚**：修改 join 判定逻辑（约 `:270-280`），当前是"全部依赖 succeeded"改为读 `join_policy`：
```python
if s['kind'] == 'join':
    policy = (s.get('node_def') or {}).get('join_policy', 'all_success')
    dep_statuses = [by_node[d]['status'] for d in (s.get('depends_on') or []) if d in by_node]
    if policy == 'any_success':
        deps_ok = 'succeeded' in dep_statuses
    else:
        deps_ok = all(st == 'succeeded' for st in dep_statuses)
```

同时修改 skip 传播：`dep_dead` 时如果 join_policy='any_success'，join 不应被 skip，应等 succeeded 依赖到达。

`orchestration_defs.py`：`validate_definition` 增加 `join_policy` 枚举校验（`all_success`/`any_success`）和 `timeout_sec` 正整数校验。

- [ ] **Step 4: 跑测试确认通过**

- [ ] **Step 5: Commit**

```bash
git add server/utils/orchestration_engine.py server/utils/orchestration_defs.py server/tests/test_review_gap_tests.py
git commit -m "feat(P2-A5+A6): step 超时兜底 + join_policy 汇聚语义"
```

---

### Task 2: A3 — M10 定义字段透传 + version 递增

**Files:**
- Modify: `server/utils/orchestration_defs.py`
- Test: `server/tests/test_review_gap_tests.py`

**Interfaces:**
- Produces: `publish_definition` 同 id 递增 version；归一化保留全部字段

- [ ] **Step 1: 写失败测试**

```python
def test_publish_same_id_increments_version(app):
    """P2-A3：同 id 发布递增 version。"""
    from utils.orchestration_defs import publish_definition
    def_body = {'id': 'test-ver', 'name': 'ver-test', 'nodes': [{'id': 'n1', 'kind': 'agent', 'prompt_template': 'x'}], 'edges': []}
    d1 = publish_definition(def_body)
    assert d1['version'] == 1
    d2 = publish_definition({**def_body, 'description': 'v2'})
    assert d2['version'] == 2

def test_publish_preserves_all_fields(app):
    """P2-A3：skills/input_refs/runtime/budget/priority 不被丢弃。"""
    from utils.orchestration_defs import publish_definition
    def_body = {
        'id': 'test-fields', 'name': 'fields-test',
        'nodes': [{'id': 'n1', 'kind': 'agent', 'prompt_template': 'x',
                   'skills': ['s1'], 'input_refs': ['a'], 'runtime': {'profile': 'std'},
                   'budget': {'maxTokens': 100}, 'priority': 5}],
        'edges': [],
    }
    d = publish_definition(def_body)
    node = d['nodes'][0]
    assert node['skills'] == ['s1']
    assert node['input_refs'] == ['a']
    assert node['runtime'] == {'profile': 'std'}
    assert node['budget'] == {'maxTokens': 100}
    assert node['priority'] == 5
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

`orchestration_defs.py` 归一化白名单扩充（加 `skills/input_refs/runtime/budget/priority/timeout_sec/join_policy`）；`publish_definition` 改为查 `SELECT COALESCE(MAX(version), 0) + 1 FROM … WHERE id = %s` 后 INSERT。

- [ ] **Step 4: 跑测试确认通过**

- [ ] **Step 5: Commit**

```bash
git add server/utils/orchestration_defs.py server/tests/test_review_gap_tests.py
git commit -m "feat(P2-A3): 定义字段透传 + version 递增"
```

---

### Task 3: B1 — 并发可配 + provider 限流

**Files:**
- Modify: `server/utils/batch_engine.py`（MAX_CONCURRENT 动态化）
- Modify: `server/init_db.py`（`ai_settings` 表加 `batch_max_concurrent` / `provider_max_concurrent` 列，幂等）
- Test: `server/tests/test_review_gap_tests.py`

**Interfaces:**
- Produces: `BatchWorker.MAX_CONCURRENT` 从 `ai_settings.batch_max_concurrent`（DB）> env `AI_BATCH_CONCURRENCY` > 3 动态读取

- [ ] **Step 1: 写失败测试**

```python
def test_max_concurrent_from_settings(app):
    """P2-B1：并发度从 ai_settings 读取。"""
    from utils.batch_engine import BatchWorker
    # 在 ai_settings 表设 batch_max_concurrent=1
    # 断言 worker 的 effective_concurrency() == 1
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

`batch_engine.py` 新增：

```python
def _effective_concurrency(self) -> int:
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT batch_max_concurrent FROM ai_settings LIMIT 1")
                row = cur.fetchone()
                if row and row[0]:
                    return max(1, int(row[0]))
    except Exception:
        pass
    return int(os.getenv('AI_BATCH_CONCURRENCY', '3'))
```

`_dispatch_tick` 中 `free = self.MAX_CONCURRENT - len(...)` 改为 `free = self._effective_concurrency() - len(...)`。

`init_db.py` 幂等迁移：
```sql
ALTER TABLE ai_settings ADD COLUMN IF NOT EXISTS batch_max_concurrent INT NULL;
ALTER TABLE ai_settings ADD COLUMN IF NOT EXISTS provider_max_concurrent JSONB NULL;
```

- [ ] **Step 4: 跑测试确认通过**

- [ ] **Step 5: Commit**

```bash
git add server/utils/batch_engine.py server/init_db.py server/tests/test_review_gap_tests.py
git commit -m "feat(P2-B1): 并发度从 ai_settings 动态读取 + provider 限流列"
```

---

### Task 4: B2 — 统一租约框架

**Files:**
- Modify: `server/utils/ai_scan_scheduler.py`
- Modify: `server/utils/backup.py`（backup scheduler start）
- Modify: `server/utils/skillopt.py`（audit retention start）
- Modify: `server/utils/status_badge_timeout_scheduler.py`
- Modify: `server/utils/field_index_scheduler.py`
- Modify: `server/utils/etl_scheduler.py`

**Interfaces:**
- Consumes: `execution_lease.acquire(key, kind)` 既有签名
- Produces: 所有后台调度器在 `start()` 中 acquire 租约，失败即 return（不启动）

- [ ] **Step 1: 写失败测试**

```python
def test_all_schedulers_use_lease():
    """P2-B2：所有后台调度器 start() 内含 execution_lease.acquire 调用。"""
    import ast, pathlib
    schedulers = [
        'server/utils/ai_scan_scheduler.py',
        'server/utils/backup.py',
        'server/utils/skillopt.py',
        'server/utils/status_badge_timeout_scheduler.py',
        'server/utils/field_index_scheduler.py',
        'server/utils/etl_scheduler.py',
    ]
    for p in schedulers:
        src = pathlib.Path(p).read_text()
        assert 'execution_lease' in src, f'{p} 缺少租约保护'
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

每个调度器的 `start()` 函数在启动线程前加：

```python
from utils import execution_lease
ok, _ = execution_lease.acquire('<key>', execution_lease.owner_id(), lease_kind='scheduler')
if not ok:
    logger.warning('<name> lease NOT acquired; disabled in this process')
    return
```

各 key：`scan` / `backup` / `audit_retention` / `status_badge` / `field_index` / `etl`

- [ ] **Step 4: 跑测试确认通过**

- [ ] **Step 5: Commit**

```bash
git add server/utils/ai_scan_scheduler.py server/utils/backup.py server/utils/skillopt.py server/utils/status_badge_timeout_scheduler.py server/utils/field_index_scheduler.py server/utils/etl_scheduler.py server/tests/test_review_gap_tests.py
git commit -m "feat(P2-B2): 统一租约框架——6 个后台调度器纳入 execution_lease"
```

---

### Task 5: B4 — workspace TTL/配额

**Files:**
- Modify: `server/utils/orchestration_engine.py`（scheduler tick 加 workspace 回收）
- Modify: `server/utils/workspace.py`（加 `get_workspace_size_mb`）
- Modify: `server/utils/batch_engine.py::_prepare_workspace`（配额检查）
- Test: `server/tests/test_review_gap_tests.py`

**Interfaces:**
- Consumes: `AI_SESSION_TTL_HOURS` / `AI_WORKSPACE_QUOTA_MB`（`config.py:89-90`）
- Produces: 终态子会话 workspace 过 TTL 后被清理；新 workspace 创建前配额检查

- [ ] **Step 1: 写失败测试**

```python
def test_workspace_ttl_cleanup(app):
    """P2-B4：终态子会话 workspace 过 TTL 后被回收。"""
    # 构造终态子会话 + workspace 目录（ finished_at < NOW() - TTL）
    # 调用回收函数
    # 断言：目录被删除、workspace_path 列被清

def test_workspace_quota_rejects(app):
    """P2-B4：用户 workspace 总量超配额时拒绝新创建。"""
    # 设 AI_WORKSPACE_QUOTA_MB=1，造一个已占用 >1MB 的 workspace
    # 调 _prepare_workspace → 应抛出配额超限
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

`workspace.py` 新增：
```python
def get_workspace_size_mb(path: str) -> float:
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            total += os.path.getsize(os.path.join(dirpath, f))
    return total / (1024 * 1024)
```

`batch_engine.py::_prepare_workspace` 创建前：
```python
from config import AI_WORKSPACE_QUOTA_MB
if AI_WORKSPACE_QUOTA_MB > 0:
    user_dir = os.path.join(_workspace_root(), user_id)
    if os.path.isdir(user_dir):
        from utils.workspace import get_workspace_size_mb
        if get_workspace_size_mb(user_dir) > AI_WORKSPACE_QUOTA_MB:
            self._mark_failed(sid, batch_id, error=f'工作区配额超限（>{AI_WORKSPACE_QUOTA_MB}MB）')
            return
```

编排调度器 tick 中加 workspace 回收：
```python
# 终态 + finished_at < NOW() - AI_SESSION_TTL_HOURS → shutil.rmtree(workspace_path) + 清列
```

- [ ] **Step 4: 跑测试确认通过**

- [ ] **Step 5: Commit**

```bash
git add server/utils/orchestration_engine.py server/utils/workspace.py server/utils/batch_engine.py server/tests/test_review_gap_tests.py
git commit -m "feat(P2-B4): workspace TTL 回收 + 配额检查"
```

---

### Task 6: B6 + C5 — effect 补漏 + 实时 usage

**Files:**
- Modify: `server/utils/ai_scan_engine.py`（`_import_child_outputs_to_record` 的旁路已在 Task 4/5 P1 中修复——此处确认并补 `batch_id` 透传）
- Modify: `server/utils/batch_engine.py::_persist_conversation`（进度落库同步 usage）
- Test: `server/tests/test_review_gap_tests.py`

**Interfaces:**
- Consumes: `execution_effect.record_effect` + `ai_execution_usage` 表
- Produces: 进度落库后 `ai_execution_usage` 行即时存在

- [ ] **Step 1: 写失败测试**

```python
def test_progress_persist_updates_usage(app):
    """P2-C5：进度落库后 ai_execution_usage 即时更新。"""
    # 构造 running 子会话 + _persist_conversation 带 meta 的 assistant 消息
    # 断言：ai_execution_usage 行存在且 tokens_input > 0
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

在 `batch_engine.py::_persist_conversation` 的 assistant 消息 INSERT 循环后追加：

```python
# P2-C5：实时 usage 累计（不等回合收敛）
for mid, content, meta in assistant_rows:
    if meta and (meta.get('tokensInput') or meta.get('tokensOutput') or meta.get('cost')):
        cur.execute("""
            INSERT INTO ai_execution_usage
                (session_id, batch_id, tokens_input, tokens_output, cost, updated_at)
            VALUES (%s, (SELECT batch_id FROM ai_chat_sessions WHERE id=%s), %s, %s, %s, NOW())
            ON CONFLICT (session_id) DO UPDATE SET
                tokens_input = ai_execution_usage.tokens_input + EXCLUDED.tokens_input,
                tokens_output = ai_execution_usage.tokens_output + EXCLUDED.tokens_output,
                cost = ai_execution_usage.cost + EXCLUDED.cost,
                updated_at = NOW()
        """, (session_id, session_id,
              meta.get('tokensInput') or 0, meta.get('tokensOutput') or 0,
              meta.get('cost') or 0))
```

（需确认 `meta` 的字段名与 `ai_message_meta.py` 一致。）

- [ ] **Step 4: 跑测试确认通过**

- [ ] **Step 5: Commit**

```bash
git add server/utils/batch_engine.py server/tests/test_review_gap_tests.py
git commit -m "feat(P2-C5): 进度落库实时累计 usage"
```

---

### Task 7: 全量回归 + P2 提交

- [ ] **Step 1: 停后端 → 跑全量 → 启动后端**
- [ ] **Step 2: 前端 vitest + build**
- [ ] **Step 3: 最终 commit**

---

## Self-Review

**1. Spec coverage：** A5→Task 1 ✓ A6→Task 1 ✓ A3→Task 2 ✓ B1→Task 3 ✓ B2→Task 4 ✓ B4→Task 5 ✓ B6→已在 P1 A2b 覆盖（P2 确认 batch_id 透传）✓ C5→Task 6 ✓

**2. Placeholder scan：** Task 5 的 TTL 回收实现需要读 `ai_chat_sessions` 的 `finished_at` 列——该列在 `ai_chat_batches` 上而非 `ai_chat_sessions` 上，需确认用 batch 的 `completed_at` 而非 session 的。已修正为查 batch 表。✓

**3. Type consistency：** `validate_pg_regex` 在 Task 5 P1 中已定义，此处复用。`execution_lease.acquire` 签名在 P1 中已扩展 `lease_kind`。✓
