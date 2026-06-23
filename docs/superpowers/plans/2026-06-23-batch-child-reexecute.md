# 批任务：单个子任务重新执行（清空上下文）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development 或 superpowers:executing-plans。Steps use checkbox (`- [ ]`)。顺序：后端 → 前端 → 文档，单一 PR。

**Goal:** 对批任务中某个**终态**子任务「重新执行」：删其旧对话、清 OpenCode 会话，置回 pending 让 worker 用全新会话重跑。

**Architecture:** 后端 `reexecute_child` repo（删消息 + 重置子会话 + 计数回退 + 复算状态）+ 单子任务路由；worker 无需改（回 pending 即自然新建会话重跑）。前端 `reexecuteChild` API/store + `BatchGroup` 子任务行的「重新执行」图标。

**Tech Stack:** Flask、psycopg2、Vue3 + Element Plus + Pinia、pytest、vitest。

**Spec:** `docs/superpowers/specs/2026-06-23-batch-child-reexecute-design.md`

---

## File Structure
- `server/utils/batch_repo.py` — `reexecute_child`（复用 `_recompute_batch_status_for`）
- `server/routes/ai_chat_batches.py` — `POST /batches/<bid>/sessions/<sid>/reexecute`
- `server/tests/test_batch_routes.py` — 测试
- `src/api/aiChatBatches.ts` · `src/stores/aiChatBatches.ts` — `reexecuteChild`
- `src/components/ai-chat/BatchGroup.vue` — 子任务行「重新执行」图标
- `docs/user-guide/ai/batch-tasks.md`、`CLAUDE.md`

---

## Task 1：后端 `reexecute_child` + 路由

**Files:** Modify `server/utils/batch_repo.py`, `server/routes/ai_chat_batches.py`; Test `server/tests/test_batch_routes.py`.

参考：`batch_repo` 已有 `_recompute_batch_status_for(batch_id)`、`get_batch_detail(user_id, batch_id)`、`from db import get_db`、`RealDictCursor`。路由文件已有 `_stage_one`/`setup_app`/`db_conn` 测试设施、`@login_required`、`g.current_user['userId']`、`get_worker().notify()` 模式。

- [ ] **Step 1: 写失败测试** 追加到 `server/tests/test_batch_routes.py`:
> 用**2 个子任务**的批次（都置成终态），重执其中一个，另一个仍是终态，故复算得 `running`（单子任务批次重执唯一子项会得 `pending`，那是另一种正确情形，不在此断言）。
```python
def _make_terminal_batch(client, headers, db_conn, monkeypatch, tmp_path, *, usid, child_status):
    """Create a 2-child batch, mark BOTH children terminal (child_status) and the
    batch counters to match (done/failed = 2). Inserts an old message on the FIRST
    child. Returns (bid, first_sid)."""
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    f1 = _stage_one(client, headers, name='r1.txt', upload_session_id=usid)
    f2 = _stage_one(client, headers, name='r2.txt', upload_session_id=usid)
    detail = client.post('/ai/chat/batches', json={'name': 'b', 'prompt': 'p', 'files': [f1, f2]},
                         headers=headers).get_json()
    bid = detail['batch']['id']
    sids = [s['id'] for s in sorted(detail['sessions'], key=lambda x: x['batch_seq'])]
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET status=%s, opencode_session_id='oc-old', "
                    "last_message_preview='old', error_message='e' WHERE batch_id=%s",
                    (child_status, bid))
        cur.execute("INSERT INTO ai_chat_messages (id, session_id, role, content) "
                    "VALUES ('m-old-1', %s, 'user', '[]'::jsonb)", (sids[0],))
        done = 2 if child_status == 'completed' else 0
        failed = 2 if child_status == 'failed' else 0
        cur.execute("UPDATE ai_chat_batches SET status='completed', done=%s, failed=%s WHERE id=%s",
                    (done, failed, bid))
        db_conn.commit()
    return bid, sids[0]


def test_reexecute_completed_child_clears_context(setup_app, tmp_path, monkeypatch, db_conn):
    client, admin_headers = setup_app
    bid, sid = _make_terminal_batch(client, admin_headers, db_conn, monkeypatch, tmp_path,
                                    usid='u-rx-1', child_status='completed')
    r = client.post(f'/ai/chat/batches/{bid}/sessions/{sid}/reexecute', headers=admin_headers)
    assert r.status_code == 200
    with db_conn.cursor() as cur:
        cur.execute("SELECT status, opencode_session_id, last_message_preview, error_message "
                    "FROM ai_chat_sessions WHERE id=%s", (sid,))
        st, oc, prev, err = cur.fetchone()
        assert st == 'pending' and oc is None and prev is None and err is None
        cur.execute("SELECT count(*) FROM ai_chat_messages WHERE session_id=%s", (sid,))
        assert cur.fetchone()[0] == 0                      # context cleared
        cur.execute("SELECT done, status FROM ai_chat_batches WHERE id=%s", (bid,))
        done, bstatus = cur.fetchone()
        assert done == 1 and bstatus == 'running'          # done 2->1, other child still done


def test_reexecute_failed_child_decrements_failed(setup_app, tmp_path, monkeypatch, db_conn):
    client, admin_headers = setup_app
    bid, sid = _make_terminal_batch(client, admin_headers, db_conn, monkeypatch, tmp_path,
                                    usid='u-rx-2', child_status='failed')
    r = client.post(f'/ai/chat/batches/{bid}/sessions/{sid}/reexecute', headers=admin_headers)
    assert r.status_code == 200
    with db_conn.cursor() as cur:
        cur.execute("SELECT failed, status FROM ai_chat_batches WHERE id=%s", (bid,))
        failed, bstatus = cur.fetchone()
        assert failed == 1 and bstatus == 'running'        # failed 2->1, other child still failed


def test_reexecute_running_child_409(setup_app, tmp_path, monkeypatch, db_conn):
    client, admin_headers = setup_app
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    f = _stage_one(client, admin_headers, name='r.txt', upload_session_id='u-rx-3')
    detail = client.post('/ai/chat/batches', json={'name': 'b', 'prompt': 'p', 'files': [f]},
                         headers=admin_headers).get_json()
    bid = detail['batch']['id']; sid = detail['sessions'][0]['id']
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET status='running' WHERE id=%s", (sid,)); db_conn.commit()
    r = client.post(f'/ai/chat/batches/{bid}/sessions/{sid}/reexecute', headers=admin_headers)
    assert r.status_code == 409


def test_reexecute_missing_child_404(setup_app):
    client, admin_headers = setup_app
    r = client.post('/ai/chat/batches/nope/sessions/nope/reexecute', headers=admin_headers)
    assert r.status_code == 404
```

- [ ] **Step 2: Run, confirm FAIL**: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_batch_routes.py -k reexecute -q` (endpoint missing → 404 for the 200-expecting tests).

- [ ] **Step 3: 实现 repo** in `server/utils/batch_repo.py` (add):
```python
def reexecute_child(user_id: str, batch_id: str, session_id: str) -> dict | None:
    """Re-run a single TERMINAL (completed/failed) batch child from scratch:
    delete its old messages, reset it to pending with a cleared OpenCode session,
    roll back the batch counter, recompute status (-> running). Returns updated
    detail, or None if the child isn't found / not owned. Raises ValueError if the
    child is not in a terminal state."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT s.status FROM ai_chat_sessions s "
                "JOIN ai_chat_batches b ON s.batch_id = b.id "
                "WHERE s.id = %s AND s.batch_id = %s AND b.user_id = %s",
                (session_id, batch_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            status = row[0]
            if status not in ('completed', 'failed'):
                raise ValueError('only completed/failed children can be re-executed')
            cur.execute("DELETE FROM ai_chat_messages WHERE session_id = %s", (session_id,))
            cur.execute(
                "UPDATE ai_chat_sessions SET status='pending', opencode_session_id=NULL, "
                "  last_message_preview=NULL, error_message=NULL WHERE id = %s",
                (session_id,),
            )
            if status == 'completed':
                cur.execute("UPDATE ai_chat_batches SET done = done - 1 WHERE id = %s", (batch_id,))
            else:
                cur.execute("UPDATE ai_chat_batches SET failed = failed - 1 WHERE id = %s", (batch_id,))
        conn.commit()
    _recompute_batch_status_for(batch_id)
    return get_batch_detail(user_id, batch_id)
```

- [ ] **Step 4: 实现路由** in `server/routes/ai_chat_batches.py` (import 处补 `reexecute_child`；加路由):
```python
@ai_chat_batches_bp.post('/<batch_id>/sessions/<session_id>/reexecute')
@login_required
def reexecute(batch_id, session_id):
    try:
        result = reexecute_child(g.current_user['userId'], batch_id, session_id)
    except ValueError as e:
        return jsonify({'error': str(e)}), 409
    if result is None:
        return jsonify({'error': 'not found'}), 404
    from utils.batch_engine import get_worker
    get_worker().notify()
    return jsonify(result)
```

- [ ] **Step 5: Run, confirm PASS**; 批路由回归：
`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_batch_routes.py -q`
（若 `_stage_one`/`setup_app` 等辅助名与真实文件不符，READ 文件顶部并适配，保持断言意图。）

- [ ] **Step 6: Commit**:
```
cd E:/wsl/check/check-manage
git add server/utils/batch_repo.py server/routes/ai_chat_batches.py server/tests/test_batch_routes.py
git commit -m "feat(batch): re-execute a single terminal child with cleared context"
```

---

## Task 2：前端 API + store + BatchGroup 动作

**Files:** Modify `src/api/aiChatBatches.ts`, `src/stores/aiChatBatches.ts`, `src/components/ai-chat/BatchGroup.vue`.

参考：store `retryFailed(batchId?)` 的刷新写法（`api.getBatch` → 更新 `items` 行 → 若是 active 则 `applyDetail`+轮询）。`BatchGroup` 子行：`<div class="bg-child" @click="$emit('selectChild', s.id)">` 内有 dot/file/preview。`AiChatBatchSession.status` 可为 `'pending'|'running'|'completed'|'failed'`。

- [ ] **Step 1: api** — 在 `src/api/aiChatBatches.ts` 加:
```typescript
export function reexecuteChild(batchId: string, sessionId: string) {
  return post<AiChatBatchDetail>(`/ai/chat/batches/${batchId}/sessions/${sessionId}/reexecute`, {})
}
```

- [ ] **Step 2: store** — 在 `src/stores/aiChatBatches.ts` 的 return 前加，并加入返回对象 `reexecuteChild,`:
```typescript
  async function reexecuteChild(batchId: string, sessionId: string) {
    const detail = await api.reexecuteChild(batchId, sessionId)
    const idx = items.value.findIndex(b => b.id === batchId)
    if (idx >= 0) items.value[idx] = detail.batch
    if (activeBatch.value?.id === batchId) {
      applyDetail(detail)
      if (!TERMINAL_STATUSES.has(detail.batch.status)) startDetailPolling(batchId)
    }
    return detail
  }
```

- [ ] **Step 3: BatchGroup 子行动作** — 在 `src/components/ai-chat/BatchGroup.vue`：
  - import 处把图标加上 `RefreshLeft`：`import { ArrowRight, ArrowDown, Plus, RefreshRight, RefreshLeft, Delete } from '@element-plus/icons-vue'`。
  - 子行模板加「重新执行」图标（终态才显，`@click.stop` 避免触发打开线程）。把子行改为:
```html
      <div v-for="s in store.activeSessions" :key="s.id"
           class="bg-child" :class="{ active: s.id === activeSessionId }"
           @click="$emit('selectChild', s.id)">
        <span :class="`dot dot--${s.status}`" />
        <span class="bg-child__file">{{ fileName(s.batch_input_file) }}</span>
        <span class="bg-child__preview">{{ s.last_message_preview || '' }}</span>
        <ElIcon v-if="s.status === 'completed' || s.status === 'failed'"
                class="bg-child__reexec" title="重新执行（清空上下文）"
                @click.stop="onReexec(s.id)"><RefreshLeft /></ElIcon>
      </div>
```
  - 脚本加方法:
```typescript
async function onReexec(sessionId: string) {
  try { await store.reexecuteChild(props.batch.id, sessionId) }
  catch (e: unknown) {
    const err = e as { response?: { data?: { error?: string } } }
    ElMessage.error(err.response?.data?.error || '重新执行失败')
  }
}
```
  - 样式加（在 `<style scoped>`）:
```css
.bg-child__reexec { cursor: pointer; flex: 0 0 auto; color: var(--el-text-color-secondary); }
.bg-child__reexec:hover { color: var(--el-color-primary); }
```

- [ ] **Step 4: 类型检查**: `cd E:/wsl/check/check-manage && npx vue-tsc --noEmit -p tsconfig.json` → clean。

- [ ] **Step 5: Commit**:
```
git add src/api/aiChatBatches.ts src/stores/aiChatBatches.ts src/components/ai-chat/BatchGroup.vue
git commit -m "feat(batch): UI to re-execute a single batch child"
```

---

## Task 3：文档

**Files:** `docs/user-guide/ai/batch-tasks.md`、`CLAUDE.md`。

- [ ] **Step 1:** `batch-tasks.md` 补：展开批次后，**已完成/失败**的子任务行可点「重新执行」——会**清空该子任务的旧对话**并用全新会话从头重跑（区别于「重试失败」批量重置所有失败项）。
- [ ] **Step 2:** `CLAUDE.md` 批段补一句：`POST /ai/chat/batches/<id>/sessions/<sid>/reexecute`（`reexecute_child`：仅终态、删旧消息 + 清 `opencode_session_id` + 计数回退 + 复算→running；worker 回 pending 后用新会话重跑）。
- [ ] **Step 3: Commit**:
```
git add docs/user-guide CLAUDE.md
git commit -m "docs(batch): re-execute single child"
```

---

## 验收
- [ ] 后端：4 个 reexecute 测试 + 批路由回归全绿。
- [ ] 前端：`vue-tsc` clean；BatchGroup 对终态子项渲染「重新执行」、非终态不渲染。
- [ ] 实测：completed 子任务点「重新执行」→ 回到运行→重新完成；点开线程只剩新一轮对话（旧已清）。
