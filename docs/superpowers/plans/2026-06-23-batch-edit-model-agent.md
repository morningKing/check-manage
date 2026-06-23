# 批任务：组头编辑 agent/模型 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development 或 superpowers:executing-plans。Steps use checkbox (`- [ ]`)。顺序：后端 → 前端 → 文档,单一 PR。

**Goal:** 在批任务组头编辑该批次的 agent/模型并持久化,改后重试/重执/worker 自动用新值。

**Architecture:** 后端 `update_batch_config` repo + `PATCH /ai/chat/batches/<id>`;前端 `EditBatchConfigDialog`(agent+模型下拉)+ 组头「编辑」图标 + `updateBatchConfig` API/store。worker 读 `_fetch_batch_context` 每次取最新值,无需改。

**Tech Stack:** Flask、psycopg2、Vue3 + Element Plus + Pinia、pytest、vitest。

**Spec:** `docs/superpowers/specs/2026-06-23-batch-edit-model-agent-design.md`

---

## File Structure
- `server/utils/batch_repo.py` — `update_batch_config`
- `server/routes/ai_chat_batches.py` — `PATCH /batches/<id>`
- `server/tests/test_batch_routes.py` — 测试
- `src/api/aiChatBatches.ts` · `src/stores/aiChatBatches.ts` — `updateBatchConfig`
- `src/components/ai-chat/EditBatchConfigDialog.vue`(新)
- `src/components/ai-chat/BatchGroup.vue` — 组头「编辑」图标 + 挂对话框
- 文档：`docs/user-guide/ai/batch-tasks.md`、`CLAUDE.md`

---

## Task 1：后端 `update_batch_config` + PATCH 路由

**Files:** Modify `server/utils/batch_repo.py`, `server/routes/ai_chat_batches.py`; Test `server/tests/test_batch_routes.py`.

参考：`batch_repo` 有 `from db import get_db`、`get_batch_detail(user_id, batch_id)`。路由文件已 `from utils.batch_repo import (... get_batch_detail, ...)`、用 `@login_required`、`g.current_user['userId']`、`jsonify`、`request`、`ai_chat_batches_bp`。测试有 `_stage_one`、`setup_app`、`db_conn`。

- [ ] **Step 1: 追加失败测试** 到 `server/tests/test_batch_routes.py`:
```python
def test_patch_updates_agent_and_model(setup_app, tmp_path, monkeypatch, db_conn):
    client, admin_headers = setup_app
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    f = _stage_one(client, admin_headers, name='r.txt', upload_session_id='u-cfg-1')
    bid = client.post('/ai/chat/batches', json={'name': 'b', 'prompt': 'p', 'files': [f]},
                      headers=admin_headers).get_json()['batch']['id']
    r = client.patch(f'/ai/chat/batches/{bid}',
                     json={'agent': 'plan', 'model': 'mimo/mimo-v2.5'}, headers=admin_headers)
    assert r.status_code == 200
    assert r.get_json()['batch']['agent'] == 'plan'
    assert r.get_json()['batch']['model'] == 'mimo/mimo-v2.5'
    with db_conn.cursor() as cur:
        cur.execute("SELECT agent, model FROM ai_chat_batches WHERE id=%s", (bid,))
        assert cur.fetchone() == ('plan', 'mimo/mimo-v2.5')


def test_patch_empty_clears_to_null(setup_app, tmp_path, monkeypatch, db_conn):
    client, admin_headers = setup_app
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    f = _stage_one(client, admin_headers, name='r.txt', upload_session_id='u-cfg-2')
    bid = client.post('/ai/chat/batches', json={'name': 'b', 'prompt': 'p', 'agent': 'plan',
                                                'files': [f]}, headers=admin_headers).get_json()['batch']['id']
    r = client.patch(f'/ai/chat/batches/{bid}', json={'agent': '', 'model': ''}, headers=admin_headers)
    assert r.status_code == 200
    with db_conn.cursor() as cur:
        cur.execute("SELECT agent, model FROM ai_chat_batches WHERE id=%s", (bid,))
        assert cur.fetchone() == (None, None)


def test_patch_missing_batch_404(setup_app):
    client, admin_headers = setup_app
    r = client.patch('/ai/chat/batches/nope', json={'model': 'x'}, headers=admin_headers)
    assert r.status_code == 404
```

- [ ] **Step 2: Run, confirm FAIL**: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_batch_routes.py -k patch -q` (route missing → 404/405).

- [ ] **Step 3: 实现 repo** in `server/utils/batch_repo.py` (add):
```python
def update_batch_config(user_id: str, batch_id: str, *,
                        agent: str | None, model: str | None) -> dict | None:
    """Update a batch's agent/model (owner-only). NULL clears to the default.
    Returns updated detail, or None if not found / not owned. Takes effect on the
    next run the worker claims (retry / reexecute / pending), since the worker
    reads agent+model fresh per run via _fetch_batch_context."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ai_chat_batches SET agent = %s, model = %s "
                "WHERE id = %s AND user_id = %s",
                (agent, model, batch_id, user_id),
            )
            updated = cur.rowcount > 0
        conn.commit()
    if not updated:
        return None
    return get_batch_detail(user_id, batch_id)
```

- [ ] **Step 4: 实现路由** in `server/routes/ai_chat_batches.py` (在 `from utils.batch_repo import (...)` 加 `update_batch_config`;加路由):
```python
@ai_chat_batches_bp.patch('/<batch_id>')
@login_required
def update_config(batch_id):
    body = request.get_json(silent=True) or {}
    agent = (body.get('agent') or '').strip() or None
    model = (body.get('model') or '').strip() or None
    result = update_batch_config(g.current_user['userId'], batch_id, agent=agent, model=model)
    if result is None:
        return jsonify({'error': 'not found'}), 404
    return jsonify(result)
```

- [ ] **Step 5: Run, confirm PASS**; 批路由回归：
`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_batch_routes.py -q`

- [ ] **Step 6: Commit**:
```
cd E:/wsl/check/check-manage
git add server/utils/batch_repo.py server/routes/ai_chat_batches.py server/tests/test_batch_routes.py
git commit -m "feat(batch): PATCH endpoint to edit a batch's agent/model"
```

---

## Task 2：前端 API + store + EditBatchConfigDialog + 组头入口

**Files:** Modify `src/api/aiChatBatches.ts`, `src/stores/aiChatBatches.ts`, `src/components/ai-chat/BatchGroup.vue`; Create `src/components/ai-chat/EditBatchConfigDialog.vue`.

参考：`aiChatBatches.ts` 有 `post`、`patch`? — 用 `import { get, post, del, patch } from '@/utils/request'`（确认 `patch` 已从 request 导出；`aiChat.ts` 里用过 `patch`）。store 的 `retryFailed`/`reexecuteChild` 展示刷新写法。`CreateBatchDialog.vue` 有 agent/model 选择器写法（`listAgents` → `[...agents, ...subagents]`；`listModels().models`；`AgentInfo`/`ModelInfo` 类型来自 `@/api/aiChat`）。`BatchGroup.vue` 组头 `bg-actions` 区有 追加/重试/删除 图标。

- [ ] **Step 1: api** — 在 `src/api/aiChatBatches.ts`：确保顶部 import 含 `patch`（`import { get, post, del, patch } from '@/utils/request'`，缺则补 `patch`），加:
```typescript
export function updateBatchConfig(id: string, body: { agent: string | null; model: string | null }) {
  return patch<AiChatBatchDetail>(`/ai/chat/batches/${id}`, body)
}
```

- [ ] **Step 2: store** — 在 `src/stores/aiChatBatches.ts` 的 return 前加，并加入返回对象 `updateBatchConfig,`:
```typescript
  async function updateBatchConfig(id: string, body: { agent: string | null; model: string | null }) {
    const detail = await api.updateBatchConfig(id, body)
    const idx = items.value.findIndex(b => b.id === id)
    if (idx >= 0) items.value[idx] = detail.batch
    if (activeBatch.value?.id === id) applyDetail(detail)
    return detail
  }
```

- [ ] **Step 3: 对话框** `src/components/ai-chat/EditBatchConfigDialog.vue`:
```vue
<template>
  <ElDialog :model-value="modelValue" title="编辑 Agent / 模型" width="460px"
            @update:model-value="$emit('update:modelValue', $event)" @open="prefill">
    <div class="row">
      <label>Agent <span class="hint">（留空=默认）</span></label>
      <ElSelect v-model="agent" placeholder="使用 OpenCode 默认 Agent" clearable>
        <ElOption v-for="a in agents" :key="a.name" :label="a.name" :value="a.name" />
      </ElSelect>
    </div>
    <div class="row">
      <label>模型 <span class="hint">（留空=默认）</span></label>
      <ElSelect v-model="model" placeholder="使用默认模型" clearable filterable>
        <ElOption v-for="m in models" :key="m.id" :label="m.label" :value="m.id" />
      </ElSelect>
    </div>
    <template #footer>
      <ElButton @click="$emit('update:modelValue', false)">取消</ElButton>
      <ElButton type="primary" :loading="saving" @click="save">保存</ElButton>
    </template>
  </ElDialog>
</template>
<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElDialog, ElSelect, ElOption, ElButton, ElMessage } from 'element-plus'
import { listAgents, listModels } from '@/api/aiChat'
import type { AgentInfo, ModelInfo } from '@/api/aiChat'
import { useAiChatBatchesStore } from '@/stores/aiChatBatches'
import type { AiChatBatch } from '@/types/aiChatBatch'

const props = defineProps<{ modelValue: boolean; batch: AiChatBatch }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'saved'): void }>()
const store = useAiChatBatchesStore()
const agents = ref<AgentInfo[]>([])
const models = ref<ModelInfo[]>([])
const agent = ref<string>('')
const model = ref<string>('')
const saving = ref(false)

function prefill() { agent.value = props.batch.agent || ''; model.value = props.batch.model || '' }

onMounted(async () => {
  try { const r = await listAgents(); agents.value = [...r.agents, ...r.subagents] } catch { /* non-fatal */ }
  try { models.value = (await listModels()).models } catch { /* non-fatal */ }
  prefill()
})

async function save() {
  saving.value = true
  try {
    await store.updateBatchConfig(props.batch.id, { agent: agent.value || null, model: model.value || null })
    ElMessage.success('已保存')
    emit('saved'); emit('update:modelValue', false)
  } catch (e: unknown) {
    const err = e as { response?: { data?: { error?: string } } }
    ElMessage.error(err.response?.data?.error || '保存失败')
  } finally { saving.value = false }
}
</script>
<style scoped>
.row { margin-bottom: 12px; }
.row label { display: block; margin-bottom: 4px; font-size: 13px; }
.hint { color: var(--el-text-color-placeholder); font-size: 11px; }
</style>
```

- [ ] **Step 4: 组头入口** — 在 `src/components/ai-chat/BatchGroup.vue`：
  - import 处加 `Setting`：`import { ArrowRight, ArrowDown, Plus, RefreshRight, RefreshLeft, Delete, Setting } from '@element-plus/icons-vue'`，并 `import EditBatchConfigDialog from './EditBatchConfigDialog.vue'`。
  - 组头 `bg-actions` 区加「编辑」图标（放在「追加文件」前或后）:
```html
        <ElIcon title="编辑 Agent/模型" @click="editOpen = true"><Setting /></ElIcon>
```
  - 在模板里（与 `AppendFilesDialog` 同级，`<AppendFilesDialog ... />` 旁）加:
```html
    <EditBatchConfigDialog v-model="editOpen" :batch="batch" @saved="onConfigSaved" />
```
  - 脚本加 `const editOpen = ref(false)` 和:
```typescript
async function onConfigSaved() { if (expanded.value) await store.selectBatch(props.batch.id) }
```

- [ ] **Step 5: 类型检查**: `cd E:/wsl/check/check-manage && npx vue-tsc --noEmit -p tsconfig.json` → clean。（若 `patch` 未从 `@/utils/request` 导出，确认其名；`aiChat.ts` 已 `import { ..., patch } from '@/utils/request'`，故存在。）

- [ ] **Step 6: Commit**:
```
git add src/api/aiChatBatches.ts src/stores/aiChatBatches.ts src/components/ai-chat/EditBatchConfigDialog.vue src/components/ai-chat/BatchGroup.vue
git commit -m "feat(batch): edit a batch's agent/model from the group header"
```

---

## Task 3：文档

**Files:** `docs/user-guide/ai/batch-tasks.md`、`CLAUDE.md`。

- [ ] **Step 1:** `batch-tasks.md` 补：在批次组头点「编辑」可改该批次的 **Agent / 模型**（留空=默认）；改后「重试失败」「重新执行」即用新模型——**模型卡住/不可用时可切到可用模型重跑,无需删了重建**。
- [ ] **Step 2:** `CLAUDE.md` 批段补一句：`PATCH /ai/chat/batches/<id>`（`update_batch_config`：改 `agent`/`model`,空=NULL 默认;owner-only；worker 下次运行即用新值）。
- [ ] **Step 3: Commit**:
```
git add docs/user-guide CLAUDE.md
git commit -m "docs(batch): edit agent/model from group header"
```

---

## 验收
- [ ] 后端：3 个 PATCH 测试 + 批路由回归全绿。
- [ ] 前端：`vue-tsc` clean；EditBatchConfigDialog 预填当前值、保存触发 `updateBatchConfig`。
- [ ] 实测：把卡在 glm-5 的批次改成默认/mimo → 组头 `agent · model` 立即更新 → 重试失败 → 子任务正常出内容并完成。
