# 批任务：侧栏分组 + 批内追加 + 工具气泡入库 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development 或 superpowers:executing-plans。Steps use checkbox (`- [ ]`)。三块独立、按 **C→B→A** 顺序，每块自成一个可上线 PR。

**Goal:** 批子会话持久化完整对话（含工具气泡）；支持向已有批次追加文件；侧栏去 tab、批任务作可折叠分组。

**Architecture:** C 改 `batch_engine` 持久化（读 OpenCode 全量 parts 映射 typed content）；B 加 `append_to_batch` repo + `POST /batches/<id>/append` + 前端追加对话框；A 重构 `AiChatView` 侧栏为「会话列表 + 可折叠 BatchGroup」，复用现有 `aiChatBatches` store 轮询。

**Tech Stack:** Flask、psycopg2、Vue3 + Element Plus + Pinia、pytest、vitest。

**Spec:** `docs/superpowers/specs/2026-06-22-batch-sidebar-append-toolbubbles-design.md`

---

## File Structure
- `server/utils/batch_engine.py` — `_OpenCodeFacade.get_messages`（raw）、`_content_from_parts`、重写 `_persist_conversation`、`_run_one` 调用点（C）
- `server/utils/batch_repo.py` — `append_to_batch`（B）
- `server/routes/ai_chat_batches.py` — `POST /batches/<id>/append`（B）
- `server/tests/test_batch_persist.py`（新，C）、`server/tests/test_batch_routes.py`（B）
- `src/api/aiChatBatches.ts` · `src/stores/aiChatBatches.ts` — `appendBatch` + action（B）
- `src/components/ai-chat/AppendFilesDialog.vue`（新，B）
- `src/components/ai-chat/BatchGroup.vue`（新，A）
- `src/views/ai-chat/AiChatView.vue` — 去 tab、组合列表（A）

---

# Part C — 工具气泡入库（纯后端）

## Task 1：`_content_from_parts` + facade `get_messages`

**Files:** Modify `server/utils/batch_engine.py`; Create `server/tests/test_batch_persist.py`.

- [ ] **Step 1: 写失败测试** `server/tests/test_batch_persist.py`:
```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import utils.batch_engine as eng


def test_content_from_parts_maps_text_and_tool():
    parts = [
        {'type': 'step-start'},
        {'type': 'reasoning', 'text': 'thinking'},
        {'type': 'text', 'text': '总结：项目甲进行中。'},
        {'type': 'tool', 'tool': 'read',
         'state': {'status': 'completed', 'input': {'p': 1}, 'output': 'OUT', 'title': 'T'}},
        {'type': 'step-finish'},
    ]
    out = eng.BatchWorker._content_from_parts(parts)
    assert {'type': 'text', 'text': '总结：项目甲进行中。'} in out
    tool = [p for p in out if p['type'] == 'tool_use']
    assert len(tool) == 1
    assert tool[0] == {'type': 'tool_use', 'name': 'read', 'title': 'T',
                       'status': 'completed', 'input': {'p': 1}, 'result': 'OUT'}
    # reasoning / step markers dropped
    assert all(p['type'] in ('text', 'tool_use') for p in out)


def test_content_from_parts_drops_empty_text():
    out = eng.BatchWorker._content_from_parts([{'type': 'text', 'text': '   '}])
    assert out == []
```

- [ ] **Step 2: Run, confirm FAIL**: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_batch_persist.py -q` → `_content_from_parts` 不存在。

- [ ] **Step 3: 实现**。在 `server/utils/batch_engine.py` 的 `_OpenCodeFacade` 内（`list_messages` 之后）加 raw 读取：
```python
    def get_messages(self, oc_session_id: str, directory: str = '') -> list:
        """Raw OpenCode message list (each {'info':..., 'parts':[...]}). Used by
        _persist_conversation to store the FULL conversation incl. tool parts."""
        return self._client().get_messages(oc_session_id, directory=directory) or []
```
在 `BatchWorker` 内加（放在 `_preview_from` 附近）:
```python
    @staticmethod
    def _content_from_parts(parts) -> list:
        """Map one OpenCode message's parts to persisted typed content: text +
        tool_use (matches interactive build_content + the AiContentPart schema).
        Drops reasoning/step markers."""
        out = []
        for p in (parts or []):
            t = p.get('type')
            if t == 'text':
                if (p.get('text') or '').strip():
                    out.append({'type': 'text', 'text': p['text']})
            elif t == 'tool':
                st = p.get('state') or {}
                out.append({
                    'type': 'tool_use',
                    'name': p.get('tool') or 'tool',
                    'title': st.get('title') or '',
                    'status': st.get('status'),
                    'input': st.get('input'),
                    'result': st.get('output'),
                })
        return out
```

- [ ] **Step 4: Run, confirm PASS** (2 passed).

- [ ] **Step 5: Commit**:
```
cd E:/wsl/check/check-manage
git add server/utils/batch_engine.py server/tests/test_batch_persist.py
git commit -m "feat(batch): map OpenCode parts to typed content (text + tool_use)"
```

## Task 2：重写 `_persist_conversation` 落库完整对话

**Files:** Modify `server/utils/batch_engine.py`; Test `server/tests/test_batch_persist.py`.

参考：当前 `_run_one` 调用 `self._persist_conversation(sid, prompt, final_msg)`；`_await_finished` 返回 `(preview, final_msg)`。

- [ ] **Step 1: 追加失败测试**:
```python
from unittest.mock import patch, MagicMock
from contextlib import contextmanager


def _capture_db():
    inserts = []
    conn = MagicMock(); cur = MagicMock()
    def _exec(sql, params=None):
        if 'INSERT INTO ai_chat_messages' in sql:
            inserts.append(params)
    cur.execute.side_effect = _exec
    conn.cursor.return_value.__enter__ = lambda s: cur
    conn.cursor.return_value.__exit__ = lambda *a: False
    @contextmanager
    def fake_db():
        yield conn
    return fake_db, inserts


def test_persist_conversation_stores_user_and_each_assistant():
    fake_db, inserts = _capture_db()
    raw = [
        {'info': {'role': 'user'}, 'parts': [{'type': 'text', 'text': 'ignored'}]},
        {'info': {'role': 'assistant'},
         'parts': [{'type': 'tool', 'tool': 'read',
                    'state': {'status': 'completed', 'input': {}, 'output': 'O', 'title': ''}}]},
        {'info': {'role': 'assistant'},
         'parts': [{'type': 'text', 'text': '最终答案'}]},
    ]
    oc = MagicMock(); oc.get_messages.return_value = raw
    w = eng.BatchWorker()
    with patch.object(eng, 'opencode_client', oc), patch.object(eng, 'get_db', fake_db):
        w._persist_conversation('sess-1', '我的问题', 'oc-1', {'content': [{'type': 'text', 'text': '最终答案'}]})
    roles = [p[2] for p in inserts]            # params = (id, session_id, role, content_json)
    assert roles == ['user', 'assistant', 'assistant']   # user + 2 assistant
    import json
    a1 = json.loads(inserts[1][3])
    assert a1[0]['type'] == 'tool_use' and a1[0]['name'] == 'read'
```
> `_persist_conversation` 签名将变为 `(session_id, prompt, oc_session_id, final_msg, directory='')`。

- [ ] **Step 2: Run, confirm FAIL**（签名不符/仍只存 2 条）。

- [ ] **Step 3: 重写** `_persist_conversation`:
```python
    def _persist_conversation(self, session_id: str, prompt: str,
                              oc_session_id: str, assistant_msg: dict | None,
                              directory: str = ''):
        """Persist the FULL conversation: the user prompt + every assistant
        message (mapped to text + tool_use parts) read from OpenCode's REST
        message list, so the batch child's thread shows tool bubbles like an
        interactive session. Falls back to `assistant_msg` if REST yields none.
        Best-effort; never raises."""
        try:
            import uuid as _uuid
            import json as _json
            raw = []
            try:
                raw = opencode_client.get_messages(oc_session_id, directory=directory) or []
            except Exception:
                raw = []
            rows = [('user', [{'type': 'text', 'text': prompt}])]
            for m in raw:
                if (m.get('info') or {}).get('role') != 'assistant':
                    continue
                content = self._content_from_parts(m.get('parts'))
                if content:
                    rows.append(('assistant', content))
            if len(rows) == 1:   # REST gave nothing usable — fall back to final msg
                parts = (assistant_msg or {}).get('content') or []
                rows.append(('assistant', parts if parts else [{'type': 'text', 'text': ''}]))
            with get_db() as conn:
                with conn.cursor() as cur:
                    for role, content in rows:
                        cur.execute(
                            "INSERT INTO ai_chat_messages (id, session_id, role, content) "
                            "VALUES (%s, %s, %s, %s::jsonb)",
                            (str(_uuid.uuid4()), session_id, role, _json.dumps(content)),
                        )
                conn.commit()
        except Exception:
            traceback.print_exc()
```
并改 `_run_one` 调用点（把 `oc_session_id` 与 `ws` 传入）:
```python
            preview, final_msg = self._await_finished(oc_session_id, directory=ws)
            self._persist_conversation(sid, prompt, oc_session_id, final_msg, directory=ws)
            self._mark_done(sid, batch_id, last_preview=preview)
```

- [ ] **Step 4: Run, confirm PASS**；并跑批回归：
`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_batch_persist.py tests/test_batch_engine.py tests/test_batch_completion.py tests/test_ai_scan_engine.py -q`

- [ ] **Step 5: Commit**:
```
git add server/utils/batch_engine.py server/tests/test_batch_persist.py
git commit -m "feat(batch): persist full conversation incl. tool bubbles for batch children"
```

> **Part C PR**: push 分支、建 PR、合并（C 独立可上线）。可在此处停下走 finishing-a-development-branch，或继续 B。

---

# Part B — 批内追加

## Task 3：`append_to_batch` repo + 路由

**Files:** Modify `server/utils/batch_repo.py`, `server/routes/ai_chat_batches.py`; Test `server/tests/test_batch_routes.py`.

参考：`create_batch` 用 `RealDictCursor`；`list_batches`/`get_batch_detail` 用 `SELECT *`；路由有 `_stage_one` 测试辅助、`MAX_FILES_PER_BATCH` 来自 `batch_repo`；`get_worker().notify()` 已在 create 用。

- [ ] **Step 1: 追加失败测试** 到 `server/tests/test_batch_routes.py`:
```python
def test_append_adds_children_and_resets_running(setup_app, tmp_path, monkeypatch, db_conn):
    client, admin_headers = setup_app
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    f1 = _stage_one(client, admin_headers, name='a.txt', upload_session_id='u-app-1')
    bid = client.post('/ai/chat/batches', json={'name': 'b', 'prompt': 'p', 'files': [f1]},
                      headers=admin_headers).get_json()['batch']['id']
    # mark the batch + child terminal to prove append flips it back to running
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_batches SET status='completed', done=1 WHERE id=%s", (bid,))
        cur.execute("UPDATE ai_chat_sessions SET status='completed' WHERE batch_id=%s", (bid,))
        db_conn.commit()
    f2 = _stage_one(client, admin_headers, name='c.txt', upload_session_id='u-app-2')
    r = client.post(f'/ai/chat/batches/{bid}/append', json={'files': [f2]}, headers=admin_headers)
    assert r.status_code == 200
    with db_conn.cursor() as cur:
        cur.execute("SELECT total, status FROM ai_chat_batches WHERE id=%s", (bid,))
        total, status = cur.fetchone()
        assert total == 2 and status == 'running'
        cur.execute("SELECT max(batch_seq) FROM ai_chat_sessions WHERE batch_id=%s", (bid,))
        assert cur.fetchone()[0] == 1   # seq continued 0 -> 1


def test_append_other_users_batch_404(setup_app, tmp_path, monkeypatch):
    client, admin_headers = setup_app
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    f = _stage_one(client, admin_headers, name='a.txt', upload_session_id='u-app-3')
    r = client.post('/ai/chat/batches/does-not-exist/append', json={'files': [f]}, headers=admin_headers)
    assert r.status_code == 404
```

- [ ] **Step 2: Run, confirm FAIL** (404 route missing → 404 actually; first test fails because endpoint absent → 404 not 200).

- [ ] **Step 3: 实现 repo**。在 `server/utils/batch_repo.py` 加:
```python
def append_to_batch(user_id: str, batch_id: str, files: list[dict]) -> dict | None:
    """Append N child sessions to an existing batch (any status). seq continues
    from max+1, total += N, status recomputed (-> running). Returns
    {batch, sessions} or None if the batch isn't found / not owned."""
    if not files:
        raise ValueError("at least one file required")
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT total FROM ai_chat_batches WHERE id=%s AND user_id=%s",
                        (batch_id, user_id))
            row = cur.fetchone()
            if not row:
                return None
            if row['total'] + len(files) > MAX_FILES_PER_BATCH:
                raise ValueError(f"max {MAX_FILES_PER_BATCH} files per batch")
            cur.execute("SELECT COALESCE(MAX(batch_seq), -1) AS m "
                        "FROM ai_chat_sessions WHERE batch_id=%s", (batch_id,))
            start = cur.fetchone()['m'] + 1
            sessions = []
            for i, f in enumerate(files):
                sid = str(uuid.uuid4())
                cur.execute(
                    "INSERT INTO ai_chat_sessions "
                    "  (id, user_id, status, batch_id, batch_seq, batch_input_file) "
                    "VALUES (%s, %s, 'pending', %s, %s, %s) RETURNING *",
                    (sid, user_id, batch_id, start + i, f['path']),
                )
                sessions.append(dict(cur.fetchone()))
            cur.execute("UPDATE ai_chat_batches SET total = total + %s WHERE id=%s",
                        (len(files), batch_id))
        conn.commit()
    _recompute_batch_status_for(batch_id)   # see below
    return get_batch_detail(user_id, batch_id)
```
`_recompute_batch_status` 现在在 `batch_engine`；为避免循环依赖，在 `batch_repo` 加一个本地等价（直接 SQL）:
```python
def _recompute_batch_status_for(batch_id: str) -> None:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT done, failed, total FROM ai_chat_batches WHERE id=%s", (batch_id,))
            row = cur.fetchone()
            if not row:
                return
            done, failed, total = row
            terminal = done + failed
            status = ('pending' if terminal == 0 else
                      'running' if terminal < total else
                      'failed' if failed == total else
                      'completed' if done == total else 'partial')
            cur.execute("UPDATE ai_chat_batches SET status=%s, "
                        "completed_at = CASE WHEN %s = total THEN now() ELSE NULL END "
                        "WHERE id=%s", (status, terminal, batch_id))
        conn.commit()
```

- [ ] **Step 4: 实现路由**。在 `server/routes/ai_chat_batches.py` 加（import 处补 `append_to_batch`）:
```python
@ai_chat_batches_bp.post('/<batch_id>/append')
@login_required
def append(batch_id):
    body = request.get_json(silent=True) or {}
    files = body.get('files') or []
    if not isinstance(files, list) or not files:
        return jsonify({'error': 'at least one file required'}), 400
    for f in files:
        if not isinstance(f, dict) or not f.get('path') or not f.get('name'):
            return jsonify({'error': 'each file must have {name, path}'}), 400
    try:
        result = append_to_batch(g.current_user['userId'], batch_id, files)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if result is None:
        return jsonify({'error': 'not found'}), 404
    from utils.batch_engine import get_worker
    get_worker().notify()
    return jsonify(result)
```

- [ ] **Step 5: Run, confirm PASS**；批路由回归：
`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_batch_routes.py -q`

- [ ] **Step 6: Commit**:
```
git add server/utils/batch_repo.py server/routes/ai_chat_batches.py server/tests/test_batch_routes.py
git commit -m "feat(batch): append files to an existing batch (any status)"
```

## Task 4：前端 `appendBatch` API + store + 追加对话框

**Files:** Modify `src/api/aiChatBatches.ts`, `src/stores/aiChatBatches.ts`; Create `src/components/ai-chat/AppendFilesDialog.vue`.

参考：`stagingUpload(file, uploadSessionId)` 已存在；store 有 `selectBatch`/`activeBatch`。

- [ ] **Step 1: api** — 在 `src/api/aiChatBatches.ts` 加:
```typescript
export function appendBatch(id: string, files: StagedFile[]) {
  return post<AiChatBatchDetail>(`/ai/chat/batches/${id}/append`, { files })
}
```
（确认 `post`、`StagedFile`、`AiChatBatchDetail` 已 import；`StagedFile` 来自 `@/types/aiChatBatch`。）

- [ ] **Step 2: store action** — 在 `src/stores/aiChatBatches.ts` 的 return 前加，并加入 return：
```typescript
  async function appendToBatch(id: string, files: AiChatBatchSession[] | { name: string; path: string }[]) {
    const detail = await api.appendBatch(id, files as any)
    // refresh list + the active detail/polling
    await fetchList()
    if (activeBatch.value?.id === id) {
      applyDetail(detail)
      if (!TERMINAL_STATUSES.has(detail.batch.status)) startDetailPolling(id)
    }
    return detail
  }
```
return 里补 `appendToBatch,`。

- [ ] **Step 3: 追加对话框** `src/components/ai-chat/AppendFilesDialog.vue`:
```vue
<template>
  <ElDialog :model-value="modelValue" title="追加文件到批次" width="460px"
            @update:model-value="$emit('update:modelValue', $event)">
    <input ref="fileEl" type="file" multiple style="display:none" @change="onPick" />
    <ElButton :icon="Plus" @click="fileEl?.click()">选择文件</ElButton>
    <ul class="staged">
      <li v-for="f in staged" :key="f.path">{{ f.name }}</li>
    </ul>
    <p v-if="!staged.length" class="hint">每个文件会作为该批次的一个新子任务。</p>
    <template #footer>
      <ElButton @click="$emit('update:modelValue', false)">取消</ElButton>
      <ElButton type="primary" :loading="submitting" :disabled="!staged.length" @click="submit">追加</ElButton>
    </template>
  </ElDialog>
</template>
<script setup lang="ts">
import { ref } from 'vue'
import { ElDialog, ElButton, ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { stagingUpload } from '@/api/aiChatBatches'
import { useAiChatBatchesStore } from '@/stores/aiChatBatches'
import type { StagedFile } from '@/types/aiChatBatch'

const props = defineProps<{ modelValue: boolean; batchId: string }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'appended'): void }>()
const store = useAiChatBatchesStore()
const fileEl = ref<HTMLInputElement | null>(null)
const staged = ref<StagedFile[]>([])
const submitting = ref(false)
const usid = (crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2))

async function onPick(e: Event) {
  const files = (e.target as HTMLInputElement).files
  if (!files) return
  for (const f of Array.from(files)) {
    try { staged.value.push(await stagingUpload(f, usid)) }
    catch { ElMessage.error(`上传失败：${f.name}`) }
  }
  ;(e.target as HTMLInputElement).value = ''
}
async function submit() {
  submitting.value = true
  try {
    await store.appendToBatch(props.batchId, staged.value)
    ElMessage.success('已追加')
    staged.value = []
    emit('appended'); emit('update:modelValue', false)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || '追加失败')
  } finally { submitting.value = false }
}
</script>
<style scoped>
.staged { list-style: none; padding: 0; margin: 8px 0; }
.staged li { padding: 4px 0; font-size: 13px; }
.hint { color: var(--el-text-color-secondary); font-size: 12px; }
</style>
```

- [ ] **Step 4: 类型检查**: `cd E:/wsl/check/check-manage && npx vue-tsc --noEmit -p tsconfig.json` → clean。

- [ ] **Step 5: Commit**:
```
git add src/api/aiChatBatches.ts src/stores/aiChatBatches.ts src/components/ai-chat/AppendFilesDialog.vue
git commit -m "feat(batch): frontend append-files API/store/dialog"
```

> 追加入口的挂载点在 Part A 的组头（Task 6）。若先单独发 B，可临时把 `AppendFilesDialog` 挂在现有 `BatchDetailView` 头部；A 落地后迁到组头。

---

# Part A — 侧栏分组（去 tab）

## Task 5：`BatchGroup` 组件

**Files:** Create `src/components/ai-chat/BatchGroup.vue`.

参考：store `selectBatch(id)` 把该批 children 载入 `activeSessions` 并轮询；`activeBatch`/`activeSessions`；`retryFailed`/`removeBatch`/`clearSelection`。组件采用**手风琴**：同一时刻展开一个组（即 store 当前 `activeBatch`）。

- [ ] **Step 1: 组件**:
```vue
<template>
  <div class="batch-group">
    <div class="batch-group__head" :class="{ open: expanded }" @click="toggle">
      <ElIcon class="caret"><ArrowRight v-if="!expanded" /><ArrowDown v-else /></ElIcon>
      <span class="bg-name">{{ batch.name }}</span>
      <span :class="`badge badge--${batch.status}`">{{ statusLabel(batch.status) }}</span>
      <span class="bg-meta">{{ batch.done }}/{{ batch.total }}</span>
      <span class="bg-am">{{ batch.agent || '默认' }} · {{ batch.model || '默认' }}</span>
      <span class="bg-actions" @click.stop>
        <ElIcon title="追加文件" @click="appendOpen = true"><Plus /></ElIcon>
        <ElIcon v-if="batch.failed" title="重试失败" @click="onRetry"><RefreshRight /></ElIcon>
        <ElIcon title="删除批次" @click="onDelete"><Delete /></ElIcon>
      </span>
    </div>
    <div v-if="expanded" class="batch-group__body">
      <div v-for="s in store.activeSessions" :key="s.id"
           class="bg-child" :class="{ active: s.id === activeSessionId }"
           @click="$emit('selectChild', s.id)">
        <span :class="`dot dot--${s.status}`" />
        <span class="bg-child__file">{{ fileName(s.batch_input_file) }}</span>
        <span class="bg-child__preview">{{ s.last_message_preview || '' }}</span>
      </div>
      <div v-if="!store.activeSessions.length" class="bg-empty">加载中…</div>
    </div>
    <AppendFilesDialog v-model="appendOpen" :batch-id="batch.id" @appended="onAppended" />
  </div>
</template>
<script setup lang="ts">
import { ref, computed } from 'vue'
import { ElIcon, ElMessageBox, ElMessage } from 'element-plus'
import { ArrowRight, ArrowDown, Plus, RefreshRight, Delete } from '@element-plus/icons-vue'
import { useAiChatBatchesStore } from '@/stores/aiChatBatches'
import AppendFilesDialog from './AppendFilesDialog.vue'
import type { AiChatBatch } from '@/types/aiChatBatch'

const props = defineProps<{ batch: AiChatBatch; activeSessionId: string | null }>()
defineEmits<{ (e: 'selectChild', id: string): void }>()
const store = useAiChatBatchesStore()
const appendOpen = ref(false)
const expanded = computed(() => store.activeBatch?.id === props.batch.id)

function toggle() {
  if (expanded.value) store.clearSelection()
  else store.selectBatch(props.batch.id)
}
function statusLabel(s: string) {
  return ({ pending: '待运行', running: '运行中', completed: '已完成', partial: '部分失败', failed: '失败' } as any)[s] || s
}
function fileName(p?: string | null) { return (p || '').split('/').pop() || '' }
async function onRetry() { try { await store.retryFailed() } catch { ElMessage.error('重试失败') } }
async function onDelete() {
  try {
    await ElMessageBox.confirm('删除该批次及其所有子任务？', '删除', { type: 'warning' })
    await store.removeBatch(props.batch.id)
  } catch { /* cancelled */ }
}
async function onAppended() { if (expanded.value) await store.selectBatch(props.batch.id) }
</script>
<style scoped>
.batch-group__head { display: flex; align-items: center; gap: 6px; padding: 6px 8px;
  cursor: pointer; border-radius: 6px; font-size: 13px; }
.batch-group__head:hover { background: var(--el-fill-color-light); }
.bg-name { font-weight: 600; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bg-meta, .bg-am { color: var(--el-text-color-secondary); font-size: 11px; }
.bg-am { flex-basis: 100%; padding-left: 22px; }
.bg-actions { display: flex; gap: 6px; }
.batch-group__body { padding-left: 18px; }
.bg-child { display: flex; align-items: center; gap: 6px; padding: 5px 8px; cursor: pointer;
  border-radius: 6px; font-size: 12px; }
.bg-child:hover, .bg-child.active { background: var(--el-fill-color); }
.bg-child__file { flex: 0 0 auto; max-width: 40%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bg-child__preview { color: var(--el-text-color-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--el-color-info); }
.dot--completed { background: var(--el-color-success); }
.dot--failed { background: var(--el-color-danger); }
.dot--running { background: var(--el-color-warning); }
.bg-empty { padding: 6px 8px; color: var(--el-text-color-secondary); font-size: 12px; }
</style>
```

- [ ] **Step 2: 类型检查**: `npx vue-tsc --noEmit -p tsconfig.json` → clean。

- [ ] **Step 3: Commit**:
```
git add src/components/ai-chat/BatchGroup.vue
git commit -m "feat(batch): collapsible BatchGroup sidebar component"
```

## Task 6：AiChatView 去 tab、组合列表

**Files:** Modify `src/views/ai-chat/AiChatView.vue`.

参考当前侧栏（`<aside class="ai-chat__sidebar">`）：tab 按钮（`sidebarTab`）+ sessions-wrap + `<BatchListView v-if=sidebarTab==='batches'>`；主区有 `<BatchDetailView v-if="sidebarTab === 'batches' && batches.activeBatch">`。

- [ ] **Step 1: 模板** — 把 `<aside>` 内替换为（去掉 tab，sessions 列表后接批次分组）:
```html
    <aside class="ai-chat__sidebar">
      <div class="ai-sidebar__sessions-wrap">
        <ElButton class="ai-chat__new" type="primary" :icon="Plus" @click="newSession">新建会话</ElButton>
        <ElScrollbar class="ai-chat__sessions">
          <div
            v-for="s in sessions" :key="s.id"
            class="session-item" :class="{ active: s.id === activeId, 'is-closed': s.status === 'closed' }"
            @click="selectSession(s.id)"
          >
            <span class="session-item__title">{{ s.title || '新会话' }}</span>
            <span class="session-item__actions" @click.stop>
              <ElIcon @click="renameSession(s.id, s.title)"><EditPen /></ElIcon>
              <ElIcon v-if="s.status === 'closed'" title="重开会话" @click="reopenSessionItem(s.id)"><RefreshRight /></ElIcon>
              <ElIcon v-else title="关闭会话" @click="closeSessionItem(s.id)"><Close /></ElIcon>
            </span>
          </div>

          <div v-if="batches.items.length" class="ai-sidebar__batches-head">
            批任务
            <ElButton link size="small" :icon="Plus" @click="showCreateBatch = true">新建</ElButton>
          </div>
          <BatchGroup
            v-for="b in batches.items" :key="b.id"
            :batch="b" :active-session-id="activeId"
            @select-child="selectSession"
          />

          <ElEmpty v-if="!sessions.length && !batches.items.length" description="暂无会话" :image-size="60" />
        </ElScrollbar>
      </div>
    </aside>
```

- [ ] **Step 2: 主区** — 删除 `<BatchDetailView v-if="sidebarTab === 'batches' && batches.activeBatch" ... />` 整行及其 `<template v-else>`/`</template>` 包裹（让主区**始终**渲染会话线程）。即把第 545–546 行的 `<BatchDetailView .../>` 删除，并把其后的 `<template v-else>` 改为直接内容（去掉这一层 v-else 包裹与对应 `</template>`）。

- [ ] **Step 3: 脚本** — 在 `<script setup>`：
  - 删除 `const sidebarTab = ref<...>('sessions')` 及所有 `sidebarTab.value = ...` 赋值；删除对 `BatchListView`/`BatchDetailView` 的 import 与 `selectBatch`/`openSession` 中仅服务于它们的逻辑（若 `openSession` 仍被其它处用则保留）。
  - import `BatchGroup`：`import BatchGroup from '@/components/ai-chat/BatchGroup.vue'`。
  - 确保 `batches` store 已初始化并启动列表轮询：在 `onMounted` 里 `await batches.fetchList(); batches.startListPolling()`（若已存在则不重复）。
  - 保留 `showCreateBatch` + `CreateBatchDialog`（新建批任务入口移到批区头部）；`CreateBatchDialog` 的 `@created` 后 `batches.fetchList()`。

- [ ] **Step 4: 样式** — 加一条分区标题样式（在该文件 `<style>`）:
```scss
.ai-sidebar__batches-head { display:flex; align-items:center; justify-content:space-between;
  margin: 10px 8px 4px; font-size: 12px; color: var(--el-text-color-secondary); font-weight: 600; }
```

- [ ] **Step 5: 类型检查 + 既有测试**: `npx vue-tsc --noEmit -p tsconfig.json` → clean。若 `src/components/ai-chat/__tests__/CreateBatchDialog.test.ts` 或其它引用了被删除的 `BatchListView`/`BatchDetailView`，相应调整或删除其断言。

- [ ] **Step 6: Commit**:
```
git add src/views/ai-chat/AiChatView.vue
git commit -m "feat(batch): unify sidebar — collapsible batch groups, remove tab"
```

## Task 7：文档

**Files:** `docs/user-guide/ai/batch-tasks.md`、`CLAUDE.md`。

- [ ] **Step 1:** `batch-tasks.md` 补：批任务以**可折叠分组**出现在会话列表（点组名展开看子任务）；可向已有批次**追加文件**（任意状态，追加后接着跑）；点子任务可见**完整对话含工具调用气泡**。
- [ ] **Step 2:** `CLAUDE.md` 批段补：`POST /ai/chat/batches/<id>/append`（`append_to_batch`，seq 接续/total+N/状态回 running）；批 worker 现持久化完整对话（`_content_from_parts` 映射 text+tool_use，`_persist_conversation` 落库 user+每条 assistant）；侧栏去 tab、`BatchGroup` 可折叠分组。
- [ ] **Step 3: Commit**:
```
git add docs/user-guide CLAUDE.md
git commit -m "docs(batch): sidebar grouping + append + tool-bubble persistence"
```

---

## 验收
- [ ] 后端：`test_batch_persist.py` + `test_batch_routes.py`（含 append）+ 批/扫描回归全绿。
- [ ] 前端：`vue-tsc` clean；分组渲染/折叠、追加对话框、tab 移除无回归。
- [ ] 实测：① 带工具调用的批子会话→点开见工具气泡且刷新仍在、`ai_chat_messages` 含 `tool_use`；② 已完成批次追加文件→新子任务接着跑、入库、组内出现；③ 侧栏无 tab、批次可折叠、点组名展开看全部子任务。
