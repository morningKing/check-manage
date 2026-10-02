# AI Harness P1 优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐 P0/P1/P2 spec 交付后的 5 项能力空洞：对外状态/错误结构化、effect 补漏、备份表扩充、监控端点、SSE 前端消费者。

**Architecture:** 全部为增量改动——新增字段/端点/文件，不删除既有字段或行为。错误结构化走增量模式（保留 `error` 字符串 + 新增 `error_detail` 对象）。监控端点从既有表聚合输出 Prometheus 文本。SSE 前端复用后端已有事件流端点。

**Tech Stack:** Flask/Python, PostgreSQL/psycopg2, Vue 3/TypeScript/Pinia, Vitest, Pytest

**Spec:** `docs/superpowers/specs/2026-09-26-ai-harness-optimization-spec.md` §3

## Global Constraints

- `dynamic_bp` 仍是最后一个注册的 Flask 蓝图
- 外部 API 既有字段一律不删不重命名（增量兼容）
- 所有新增测试必须在 base（当前 HEAD）上失败（判别性）
- 共享 dev 库：测试不跑时后端必须停止；测试自带清理
- `docs/superpowers/` 在 .gitignore 中，git add 需 `-f`

---

### Task 1: 结构化错误响应 `error_detail`

**Files:**
- Modify: `server/utils/api_errors.py:19-22`
- Test: `server/tests/test_review_gap_tests.py`（追加用例）

**Interfaces:**
- Consumes: 无前置依赖
- Produces: `err(message, code, status, *, retryable=None, phase=None, attempt=None, evidence_refs=None)` — 既有调用方不传 kwargs 时行为不变（向后兼容）

- [ ] **Step 1: 写失败测试**

在 `server/tests/test_review_gap_tests.py` 末尾追加：

```python
def test_structured_error_detail():
    """P1-A1：err() 支持 error_detail 增量字段。"""
    from utils.api_errors import err
    import json
    resp, status = err('msg', 'CODE', 409,
                       retryable=True, phase='dispatch', attempt=2,
                       evidence_refs=['event:bevt_x'])
    body = resp.get_json()
    assert body['error'] == 'msg'
    assert body['code'] == 'CODE'
    assert body['error_detail']['retryable'] is True
    assert body['error_detail']['phase'] == 'dispatch'
    assert body['error_detail']['attempt'] == 2
    assert body['error_detail']['evidenceRefs'] == ['event:bevt_x']

def test_structured_error_backward_compat():
    """P1-A1：不传 kwargs 时行为与既有完全一致（无 error_detail 键）。"""
    from utils.api_errors import err
    resp, status = err('msg', 'CODE', 404)
    body = resp.get_json()
    assert body['error'] == 'msg'
    assert body['code'] == 'CODE'
    assert 'error_detail' not in body
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && python -m pytest tests/test_review_gap_tests.py::test_structured_error_detail tests/test_review_gap_tests.py::test_structured_error_backward_compat -v`
Expected: FAIL — `err() got an unexpected keyword argument 'retryable'`

- [ ] **Step 3: 实现**

修改 `server/utils/api_errors.py:19-22`：

```python
def err(message: str, code: str, status: int, *,
        retryable: bool | None = None,
        phase: str | None = None,
        attempt: int | None = None,
        evidence_refs: list | None = None):
    """返回可直接 `return` 的 (jsonify(...), status) 二元组。

    P1-A1：可选结构化增量字段（retryable/phase/attempt/evidence_refs）——
    传入时输出 `error_detail` 对象；不传时行为与既有完全一致。"""
    from flask import jsonify
    body: dict = {'error': message, 'code': code}
    detail: dict = {}
    if retryable is not None:
        detail['retryable'] = retryable
    if phase is not None:
        detail['phase'] = phase
    if attempt is not None:
        detail['attempt'] = attempt
    if evidence_refs is not None:
        detail['evidenceRefs'] = evidence_refs
    if detail:
        body['error_detail'] = detail
    return jsonify(body), status
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && python -m pytest tests/test_review_gap_tests.py::test_structured_error_detail tests/test_review_gap_tests.py::test_structured_error_backward_compat -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/utils/api_errors.py server/tests/test_review_gap_tests.py
git commit -m "feat(P1-A1): err() 支持 error_detail 增量结构化字段"
```

---

### Task 2: `_batch_out` 补 `paused` / `eventCursor`

**Files:**
- Modify: `server/utils/batch_repo.py`（`get_batch_progress_ext` 补 eventCursor）
- Modify: `server/routes/open_api_batches.py`（`_batch_out` 加 paused/eventCursor）
- Test: `server/tests/test_review_gap_tests.py`

**Interfaces:**
- Consumes: Task 1 的 `err()` 新签名
- Produces: `_batch_out(b)` 返回多两个键：`paused: bool`、`eventCursor: int | None`

- [ ] **Step 1: 写失败测试**

在 `server/tests/test_review_gap_tests.py` 追加：

```python
def test_batch_out_has_paused_and_event_cursor(client):
    """P1-A1：_batch_out 增量返回 paused 和 eventCursor。"""
    uid = 'p1-batch-out-test'
    bid, sids = _seed_batch_by_uid(client, uid, 1)
    r = client.get(f'/v1/ai-batches/{bid}', headers=api_key_headers())
    body = r.get_json()
    assert 'paused' in body
    assert body['paused'] is False
    assert 'eventCursor' in body
    # 有子任务 → 至少有 event（哪怕 0 条，cursor 也应是 0 而非缺失）
    assert isinstance(body['eventCursor'], int)
```

> 注意：此测试依赖已有的 `_seed_batch_by_uid` / `api_key_headers` helper（如无则参照 `test_open_api_batches_crud.py` 的模式创建）。如 helper 不存在，需要先用 `test_client` + JWT 创建批次并获取 API Key——参照 `test_open_api_batches_crud.py::test_create_passes_callback_fields` 的 fixture 模式。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && python -m pytest tests/test_review_gap_tests.py::test_batch_out_has_paused_and_event_cursor -v`
Expected: FAIL — `KeyError: 'paused'`

- [ ] **Step 3: 实现**

在 `batch_repo.py::get_batch_progress_ext` 的返回 dict 中追加：

```python
        cur.execute("SELECT COALESCE(MAX(event_seq), 0) "
                    "FROM ai_batch_events WHERE batch_id = %s", (batch_id,))
        out['eventCursor'] = cur.fetchone()[0]
```

在 `open_api_batches.py::_batch_out` 的 return dict 中追加：

```python
        'paused': bool(ext.get('paused', False)),
        'eventCursor': ext.get('eventCursor'),
```

其中 `paused` 的值来源：`get_batch_progress_ext` 需查 `(SELECT EXISTS(SELECT 1 FROM ai_chat_sessions WHERE batch_id=%s AND status='paused'))`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && python -m pytest tests/test_review_gap_tests.py::test_batch_out_has_paused_and_event_cursor -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/utils/batch_repo.py server/routes/open_api_batches.py server/tests/test_review_gap_tests.py
git commit -m "feat(P1-A1): _batch_out 增量返回 paused/eventCursor"
```

---

### Task 3: `_children_out` per-child 结构化数组

**Files:**
- Modify: `server/utils/batch_repo.py`（新增 `get_children_progress`）
- Modify: `server/routes/open_api_batches.py`（新增 `_children_out` + 挂到 `_batch_out`）
- Test: `server/tests/test_review_gap_tests.py`

**Interfaces:**
- Consumes: Task 2 的 `get_batch_progress_ext`
- Produces: `get_children_progress(batch_id) -> list[dict]`；`_batch_out(b)` 新增键 `children: list`

- [ ] **Step 1: 写失败测试**

在 `server/tests/test_review_gap_tests.py` 追加：

```python
def test_batch_out_children_array(client):
    """P1-A1：_batch_out 返回 children 结构化数组。"""
    bid, sids = _seed_batch_by_uid(client, 'p1-children-test', 2)
    # 设一个子任务状态以验证 attempt/phase
    from db import get_db
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE ai_chat_sessions SET status='running', "
                    "execution_generation=2, retry_count=1 WHERE id=%s", (sids[0],))
    conn.commit()
    r = client.get(f'/v1/ai-batches/{bid}', headers=api_key_headers())
    body = r.get_json()
    assert 'children' in body
    assert len(body['children']) == 2
    child = body['children'][0]
    assert 'childId' in child
    assert 'name' in child
    assert 'status' in child
    assert 'attempt' in child
    assert 'retryCount' in child
    assert 'retryable' in child
    assert 'error' in child
    assert isinstance(child['error'], dict)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && python -m pytest tests/test_review_gap_tests.py::test_batch_out_children_array -v`
Expected: FAIL — `KeyError: 'children'`

- [ ] **Step 3: 实现**

`batch_repo.py` 新增：

```python
def get_children_progress(batch_id: str) -> list[dict]:
    """P1-A1：per-child 结构化状态（attempt/retryable/error/lastProgressAt）。
    best-effort，失败返回空列表。"""
    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, batch_seq, batch_input_file, status, "
                    "execution_generation, retry_count, error_message, "
                    "gate_status, last_active_at "
                    "FROM ai_chat_sessions WHERE batch_id = %s "
                    "AND deleted_at IS NULL ORDER BY batch_seq", (batch_id,))
                rows = [dict(r) for r in cur.fetchall()]
        out = []
        for r in rows:
            terminal = r['status'] in ('completed', 'failed', 'cancelled', 'needs_review')
            out.append({
                'childId': r['id'],
                'seq': r['batch_seq'],
                'name': (r['batch_input_file'] or '').rsplit('/', 1)[-1] if r['batch_input_file'] else None,
                'status': r['status'],
                'attempt': (r['execution_generation'] or 0) + 1,
                'retryCount': r['retry_count'] or 0,
                'retryable': not terminal,
                'lastProgressAt': r['last_active_at'].isoformat() if r.get('last_active_at') else None,
                'error': {
                    'code': 'GATE_INCONCLUSIVE' if r.get('gate_status') == 'inconclusive' else None,
                    'message': r.get('error_message'),
                    'retryable': r['status'] in ('failed', 'needs_review'),
                },
            })
        return out
    except Exception:
        return []
```

`open_api_batches.py::_batch_out` 追加：

```python
        'children': get_children_progress(b['id']),
```

（顶部 import `from utils.batch_repo import get_children_progress`）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && python -m pytest tests/test_review_gap_tests.py::test_batch_out_children_array -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/utils/batch_repo.py server/routes/open_api_batches.py server/tests/test_review_gap_tests.py
git commit -m "feat(P1-A1): _batch_out 返回 children 结构化数组"
```

---

### Task 4: A2b — `_import_child_outputs_to_record` effect 补漏

**Files:**
- Modify: `server/utils/ai_scan_engine.py:195`（`_import_child_outputs_to_record` 函数）
- Test: `server/tests/test_review_gap_tests.py`

**Interfaces:**
- Consumes: `execution_effect.record_effect(session_id, effect_type, key, batch_id=…)` + `settle_effect(id, status, external_ref=…)`
- Produces: 扫描产物文件导入后 effect 行出现

- [ ] **Step 1: 写失败测试**

在 `server/tests/test_review_gap_tests.py` 追加：

```python
def test_scan_import_registers_effect(db_conn):
    """P1-A2b：扫描产物导入 data_files 时登记 file_import effect。"""
    # 构造：子会话 + 已记录的变更文件 + 触发 _import_child_outputs_to_record
    # 具体构造参照 test_ai_scan_engine.py 的现有 fixture 模式
    # 断言：ai_execution_effects 中出现 effect_type='file_import' 的行
    # 且 status='committed'
    pass  # 实现时参照 test_ai_scan_engine 的既有 fixture 补全
```

> 注意：这个测试需要构造 `ai_scan_tasks` 行 + 子会话 + 变更记录，比较重。参照 `test_ai_scan_engine.py` 的 `scan_fixture` 模式。如果构造太重可以拆为集成测试。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && python -m pytest tests/test_review_gap_tests.py::test_scan_import_registers_effect -v`
Expected: FAIL — effect 行不存在

- [ ] **Step 3: 实现**

在 `ai_scan_engine.py` 的 `_import_child_outputs_to_record` 函数中，找到 `INSERT INTO data_files` 的执行位置（`:228-233`），在其后追加：

```python
from utils.execution_effect import record_effect, settle_effect
eff = record_effect(session_row['id'], 'file_import',
                    f"{session_id}:{rel_path}:{file_hash[:16]}",
                    batch_id=session_row.get('batch_id'))
if eff:
    settle_effect(eff['id'], 'committed', external_ref=str(file_id))
```

（需从函数上下文获取 `session_row` / `session_id` / `file_hash` / `file_id` / `rel_path` 变量，读实现时确认变量名。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && python -m pytest tests/test_review_gap_tests.py::test_scan_import_registers_effect -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/utils/ai_scan_engine.py server/tests/test_review_gap_tests.py
git commit -m "feat(P1-A2b): 扫描产物导入 effect 补漏"
```

---

### Task 5: A9 — 备份表清单扩充

**Files:**
- Modify: `server/utils/backup.py`（`BACKUP_TABLES` 列表）
- Test: `server/tests/test_review_gap_tests.py`

**Interfaces:**
- Consumes: `backup.py` 既有 `BACKUP_TABLES` 格式 `(table_name, [columns], {jsonb_indices}, label)`
- Produces: 备份/还原覆盖 19 张新表

- [ ] **Step 1: 写失败测试**

在 `server/tests/test_review_gap_tests.py` 追加：

```python
def test_backup_covers_ai_harness_tables():
    """P1-A9：BACKUP_TABLES 覆盖 AI Harness 新表。"""
    from utils.backup import BACKUP_TABLES
    table_names = {t[0] for t in BACKUP_TABLES}
    required = {
        'ai_orchestration_definitions', 'ai_orchestration_runs',
        'ai_orchestration_steps', 'artifacts', 'artifact_refs',
        'ai_runtime_manifests', 'ai_execution_attempts',
        'ai_execution_events', 'ai_execution_checkpoints',
        'ai_execution_effects', 'ai_execution_commands',
        'ai_batch_events', 'ai_delivery_outbox',
        'ai_execution_budgets', 'ai_execution_usage',
        'ai_chat_turns', 'agent_tool_calls', 'action_expectations',
    }
    missing = required - table_names
    assert not missing, f"BACKUP_TABLES 缺少: {missing}"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && python -m pytest tests/test_review_gap_tests.py::test_backup_covers_ai_harness_tables -v`
Expected: FAIL — 缺少全部 18 张表

- [ ] **Step 3: 实现**

在 `server/utils/backup.py` 的 `BACKUP_TABLES` 列表末尾追加 18 个元组。每行的列清单需对照 `server/init_db.py` 的 DDL 逐表填写，JSONB 列用集合标记索引。例如：

```python
    ('ai_orchestration_definitions',
     ['id', 'version', 'name', 'description', 'enabled', 'owner_user_id',
      'nodes', 'edges', 'retry_policy', 'timeout_policy', 'budget_policy',
      'approval_policy', 'compensation_policy', 'created_at', 'published_at'],
     {5, 6, 7, 8, 9, 10, 11}, 'AI 编排定义'),
    # …其余 17 张表同格式
```

实现时需逐表读 `init_db.py` 中对应 DDL 确认列名和 JSONB 列位置。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && python -m pytest tests/test_review_gap_tests.py::test_backup_covers_ai_harness_tables -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/utils/backup.py server/tests/test_review_gap_tests.py
git commit -m "feat(P1-A9): BACKUP_TABLES 覆盖 AI Harness 19 张新表"
```

---

### Task 6: B3 — `/metrics` 监控端点

**Files:**
- Create: `server/routes/metrics.py`
- Modify: `server/app.py`（注册蓝图）
- Test: `server/tests/test_review_gap_tests.py`

**Interfaces:**
- Consumes: `db.get_db()`；Flask Blueprint
- Produces: `GET /metrics` → 200 + `text/plain`（Prometheus 文本格式）

- [ ] **Step 1: 写失败测试**

在 `server/tests/test_review_gap_tests.py` 追加：

```python
def test_metrics_endpoint(client):
    """P1-B3：/metrics 端点返回 Prometheus 文本格式指标。"""
    r = client.get('/metrics')
    assert r.status_code == 200
    assert 'text/plain' in r.content_type
    body = r.get_data(as_text=True)
    assert 'ai_batch_queue_depth' in body
    assert 'ai_batch_running' in body
    assert 'ai_outbox_pending' in body
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && python -m pytest tests/test_review_gap_tests.py::test_metrics_endpoint -v`
Expected: FAIL — 404（蓝图未注册）

- [ ] **Step 3: 实现**

新建 `server/routes/metrics.py`：

```python
"""Prometheus 文本格式监控端点（P1-B3）。全部指标从既有表聚合，零新依赖。"""
from flask import Blueprint, Response
from db import get_db

metrics_bp = Blueprint('metrics', __name__, url_prefix='/metrics')

def _query(cur, sql):
    cur.execute(sql)
    return cur.fetchone()[0]

@metrics_bp.route('', methods=['GET'])
def metrics():
    lines = []
    with get_db() as conn:
        cur = conn.cursor()
        pairs = [
            ('ai_batch_queue_depth', 'Pending batch children',
             "SELECT count(*) FROM ai_chat_sessions WHERE status='pending' AND (batch_id IS NOT NULL OR api_key_id IS NOT NULL) AND deleted_at IS NULL"),
            ('ai_batch_running', 'Currently running batch children',
             "SELECT count(*) FROM ai_chat_sessions WHERE status='running' AND (batch_id IS NOT NULL OR api_key_id IS NOT NULL)"),
            ('ai_batch_failed_last_hour', 'Failed in last hour',
             "SELECT count(*) FROM ai_chat_sessions WHERE status='failed' AND created_at > NOW() - interval '1 hour'"),
            ('ai_orchestration_runs_active', 'Active orchestration runs',
             "SELECT count(*) FROM ai_orchestration_runs WHERE status IN ('running','waiting_approval')"),
            ('ai_outbox_pending', 'Undelivered outbox rows',
             "SELECT count(*) FROM ai_delivery_outbox WHERE status IN ('pending','failed')"),
        ]
        for name, help_text, sql in pairs:
            val = _query(cur, sql)
            lines.append(f'# HELP {name} {help_text}')
            lines.append(f'# TYPE {name} gauge')
            lines.append(f'{name} {val}')
        for row in cur.execute(
            "SELECT lease_key, "
            "EXTRACT(EPOCH FROM (NOW()-heartbeat_at))::int "
            "FROM ai_batch_worker_leases"
        ):
            lines.append(f'ai_worker_lease_heartbeat_seconds{{kind="{row[0]}"}} {row[1]}')
    return Response('\n'.join(lines) + '\n', mimetype='text/plain')
```

在 `server/app.py` 蓝图注册区追加：

```python
from routes.metrics import metrics_bp
app.register_blueprint(metrics_bp)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && python -m pytest tests/test_review_gap_tests.py::test_metrics_endpoint -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/routes/metrics.py server/app.py server/tests/test_review_gap_tests.py
git commit -m "feat(P1-B3): /metrics Prometheus 监控端点"
```

---

### Task 7: A4 — SSE 前端消费者

**Files:**
- Create: `src/api/batchEvents.ts`
- Modify: `src/stores/aiChatBatches.ts`
- Test: `src/stores/__tests__/aiChatBatches.test.ts`

**Interfaces:**
- Consumes: 后端 `GET /api/ai/chat/batches/events?ids=…&access_token=…`（`login_required_sse`）
- Produces: `createBatchEventStream(batchIds, handlers) → EventSource`

- [ ] **Step 1: 写失败测试**

在 `src/stores/__tests__/aiChatBatches.test.ts` 追加：

```typescript
import { createBatchEventStream } from '@/api/batchEvents'

// mock EventSource
class MockEventSource {
  static last: MockEventSource | null = null
  url = ''
  onerror: (() => void) | null = null
  private listeners: Record<string, ((e: MessageEvent) => void)[]> = {}
  constructor(url: string) { this.url = url; MockEventSource.last = this }
  addEventListener(type: string, cb: (e: MessageEvent) => void) {
    (this.listeners[type] ??= []).push(cb)
  }
  close() { /* noop */ }
  emit(type: string, data: any) {
    const e = new MessageEvent(type, { data: JSON.stringify(data) })
    for (const cb of this.listeners[type] ?? []) cb(e)
  }
}

vi.stubGlobal('EventSource', MockEventSource)

it('SSE batch_event triggers applyDetail', () => {
  const store = useAiChatBatchesStore()
  const es = createBatchEventStream(['b1'], {
    onEvent: (bid, detail) => store.applyDetail({ batch: detail, sessions: [] }),
    onDone: () => {},
    onError: () => {},
  })
  MockEventSource.last!.emit('batch_event', { id: 'b1', status: 'running', done: 1 })
  expect(store.activeBatch?.status).toBe('running')
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npm run test -- src/stores/__tests__/aiChatBatches.test.ts`
Expected: FAIL — module `@/api/batchEvents` not found

- [ ] **Step 3: 实现**

新建 `src/api/batchEvents.ts`：

```typescript
/**
 * P1-A4：批任务事件 SSE 消费者。
 * 后端端点：GET /ai/chat/batches/events?ids=…&access_token=…
 * EventSource 原生带 Last-Event-ID 重连（后端已输出 id: 行）。
 */
export interface BatchEvent {
  batchId: string
  eventSeq: number
  type: string
  data: Record<string, unknown>
}

export function createBatchEventStream(
  batchIds: string[],
  handlers: {
    onEvent?: (batchId: string, event: BatchEvent) => void
    onDone?: (batchId: string) => void
    onError?: () => void
  },
): EventSource {
  const token = localStorage.getItem('check-manage:token') ?? ''
  const ids = batchIds.map(id => `ids=${encodeURIComponent(id)}`).join('&')
  const url = `/api/ai/chat/batches/events?${ids}&access_token=${encodeURIComponent(token)}`
  const es = new EventSource(url)

  es.addEventListener('batch_event', e => {
    try {
      const d = JSON.parse((e as MessageEvent).data)
      handlers.onEvent?.(d.batchId, d)
    } catch { /* ignore malformed */ }
  })
  es.addEventListener('batch_done', e => {
    try {
      const d = JSON.parse((e as MessageEvent).data)
      handlers.onDone?.(d.batchId)
    } catch { /* ignore */ }
  })
  es.onerror = () => { handlers.onError?.() }
  return es
}
```

`src/stores/aiChatBatches.ts` 改造：在 `selectBatch` 中加入 SSE 订阅逻辑：

```typescript
import { createBatchEventStream } from '@/api/batchEvents'

// store 内部
let batchES: EventSource | null = null

function subscribeBatchEvents(id: string) {
  batchES?.close()
  batchES = createBatchEventStream([id], {
    onEvent: (bid, event) => {
      // 增量更新，不等 5s 轮询
      if (event.data?.detail) applyDetail(event.data.detail)
    },
    onDone: (bid) => {
      stopDetailPolling()
    },
    onError: () => { /* EventSource 自动重连；轮询仍在作为兜底 */ },
  })
}
```

在 `selectBatch` 的非终态分支调用 `subscribeBatchEvents(id)`；在 `clearSelection` 中 `batchES?.close()`。

- [ ] **Step 4: 跑测试确认通过**

Run: `npm run test -- src/stores/__tests__/aiChatBatches.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/api/batchEvents.ts src/stores/aiChatBatches.ts src/stores/__tests__/aiChatBatches.test.ts
git commit -m "feat(P1-A4): SSE 批任务事件前端消费者"
```

---

### Task 8: 全量回归 + 提交

- [ ] **Step 1: 停后端（如运行中）**

```bash
# 按 PID 杀（不用 taskkill /IM）
netstat -ano | grep :3002 | grep LISTENING
# 确认 PID 后 kill
```

- [ ] **Step 2: 跑后端全量**

```bash
cd server && python -m pytest tests/ -q
```
Expected: 全绿（2282+ 新增测试）

- [ ] **Step 3: 启动后端 + 跑前端全量**

```bash
cd server && python app.py &    # 或手动重启
npm run test
```
Expected: 全绿

- [ ] **Step 4: 最终 commit（如有个别未 commit 的改动）**

```bash
git add -A
git commit -m "feat(P1): 能力空洞首批落地——结构化错误/children/paused/eventCursor/metrics/SSE/effect补漏/备份表"
```

---

## Self-Review

**1. Spec coverage：**
- A1（结构化）→ Task 1/2/3 ✓
- A2b（effect 补漏）→ Task 4 ✓
- A9（备份表）→ Task 5 ✓
- B3（/metrics）→ Task 6 ✓
- A4（SSE 前端）→ Task 7 ✓
- P2/P3 项不在本计划（等 P1 合入后另出计划）✓

**2. Placeholder scan：** Task 4 Step 1 有 `pass` placeholder——需要实现时参照 `test_ai_scan_engine.py` 的 fixture 模式补全。已标注。✓

**3. Type consistency：** `get_children_progress` 返回 `list[dict]` → `_children_out` 直接透传 JSON → 测试断言 `'children' in body`。`err()` 新签名向后兼容。✓
