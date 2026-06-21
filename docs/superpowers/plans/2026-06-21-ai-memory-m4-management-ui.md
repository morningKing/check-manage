# AI 长期记忆 — M4 实现计划（记忆管理 UI）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** 用户能在 AI 助手里查看并删除自己的长期记忆（透明、可纠错）。

**Architecture:** 后端两个 per-user 端点（复用 M1 的 `list_memories`/`delete_memory`，删除前校验归属）。前端一个 `MemoryManager` 抽屉（仿 `PromptTemplateManager`），从 `AiChatView` 入口打开。

**Tech Stack:** Flask、Vue3 + Element Plus、pytest/vitest。

**Spec:** `docs/superpowers/specs/2026-06-20-ai-session-longterm-memory-design.md` §9。依赖 M1（`list_memories`/`delete_memory`）。

---

## File Structure（M4）
- `server/routes/ai.py` — `GET /ai/memories` + `DELETE /ai/memories/<id>`
- `server/tests/test_routes_ai_memories.py`（新）
- `src/api/aiChat.ts` — `listMemories` / `deleteMemory`
- `src/components/ai-chat/MemoryManager.vue`（新）
- `src/views/ai-chat/AiChatView.vue` — 入口按钮 + 挂载
- `docs/user-guide/ai/long-term-memory.md` + `CLAUDE.md`

---

## Task 1：后端 per-user 记忆端点

**Files:** Modify `server/routes/ai.py`; Test `server/tests/test_routes_ai_memories.py`.

- [ ] **Step 1: 写失败测试** `server/tests/test_routes_ai_memories.py`:
```python
import sys, os
from unittest.mock import patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from auth import create_token

def _h():
    from auth import create_token
    return {'Authorization': 'Bearer ' + create_token({'id': 'u1', 'username': 'bob', 'role': 'developer'})}

def _client():
    from app import app
    app.config['TESTING'] = True
    return app.test_client()

def test_list_memories_returns_user_memories():
    with patch('routes.ai.list_memories', return_value=[{'id': '1', 'memory': '喜欢 Python'}]) as lm:
        r = _client().get('/ai/memories', headers=_h())
    assert r.status_code == 200
    assert r.get_json()['memories'][0]['memory'] == '喜欢 Python'
    lm.assert_called_once_with('u1')

def test_delete_memory_checks_ownership():
    with patch('routes.ai.list_memories', return_value=[{'id': 'mine', 'memory': 'x'}]), \
         patch('routes.ai.delete_memory') as dm:
        ok = _client().delete('/ai/memories/mine', headers=_h())
        nope = _client().delete('/ai/memories/someone-else', headers=_h())
    assert ok.status_code == 200
    dm.assert_called_once_with('mine')
    assert nope.status_code == 404
```

- [ ] **Step 2: Run, confirm FAIL:**
`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_routes_ai_memories.py -v`

- [ ] **Step 3: Add to `server/routes/ai.py`** (import `flask.g as flask_g` if not present; `login_required` from auth; reference `list_memories`/`delete_memory` by name at module level so tests' `patch('routes.ai.list_memories')` works — add `from utils.memory import list_memories, delete_memory` at the top):
```python
@ai_bp.route('/memories', methods=['GET'])
@login_required
def list_my_memories():
    user = flask_g.current_user
    return jsonify({'memories': list_memories(user['userId'])})


@ai_bp.route('/memories/<memory_id>', methods=['DELETE'])
@login_required
def delete_my_memory(memory_id):
    user = flask_g.current_user
    owned = {m.get('id') for m in list_memories(user['userId'])}
    if memory_id not in owned:
        return jsonify({'error': 'not found'}), 404
    delete_memory(memory_id)
    return jsonify({'ok': True})
```

- [ ] **Step 4: Run, confirm PASS** (2 passed).

- [ ] **Step 5: Commit:**
```
cd E:/wsl/check/check-manage
git add server/routes/ai.py server/tests/test_routes_ai_memories.py
git commit -m "feat(memory): per-user GET/DELETE memory endpoints (M4)"
```

## Context
- ALREADY on branch `feat/ai-session-longterm-memory`. `routes/ai.py` already uses `@login_required` (from `auth`) and `flask_g.current_user` elsewhere — READ the file to match imports. `current_user['userId']` is the user id.
- `utils.memory.list_memories(user_id)` returns `[{id, memory, ...}]`; `delete_memory(memory_id)` deletes. Both degrade to empty/no-op when memory disabled (so endpoints return `{memories: []}` when disabled — fine).
- Import the two functions at module top (`from utils.memory import list_memories, delete_memory`) so the tests can patch `routes.ai.list_memories`/`routes.ai.delete_memory`.

---

## Task 2：前端记忆管理抽屉

**Files:** Modify `src/api/aiChat.ts`; Create `src/components/ai-chat/MemoryManager.vue`; Modify `src/views/ai-chat/AiChatView.vue`.

- [ ] **Step 1: api** — append to `src/api/aiChat.ts`:
```typescript
export interface AiMemory { id: string; memory: string }

export function listMemories() {
  return get<{ memories: AiMemory[] }>('/ai/memories')
}
export function deleteMemory(id: string) {
  return del(`/ai/memories/${id}`)
}
```
(`get`/`del` are already imported at the top of the file.)

- [ ] **Step 2: Component** — create `src/components/ai-chat/MemoryManager.vue`. READ `src/components/ai-chat/PromptTemplateManager.vue` first and MIRROR its `ElDrawer` structure/props/styling. Functional spec:
```vue
<template>
  <ElDrawer :model-value="modelValue" @update:model-value="$emit('update:modelValue', $event)"
            title="我的长期记忆" size="480px" @open="load">
    <div v-loading="loading">
      <p v-if="!items.length" class="empty">暂无长期记忆。</p>
      <ul v-else class="mem-list">
        <li v-for="m in items" :key="m.id">
          <span class="mem-text">{{ m.memory }}</span>
          <ElButton link type="danger" size="small" @click="remove(m.id)">删除</ElButton>
        </li>
      </ul>
    </div>
  </ElDrawer>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElDrawer, ElButton, ElMessage, ElMessageBox } from 'element-plus'
import { listMemories, deleteMemory, type AiMemory } from '@/api/aiChat'

defineProps<{ modelValue: boolean }>()
defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

const items = ref<AiMemory[]>([])
const loading = ref(false)

async function load() {
  loading.value = true
  try { items.value = (await listMemories()).memories || [] }
  catch { items.value = [] }
  finally { loading.value = false }
}

async function remove(id: string) {
  try {
    await ElMessageBox.confirm('确定删除这条记忆？', '提示', { type: 'warning' })
  } catch { return }
  await deleteMemory(id)
  items.value = items.value.filter((m) => m.id !== id)
  ElMessage.success('已删除')
}
</script>

<style scoped>
.mem-list { list-style: none; padding: 0; margin: 0; }
.mem-list li { display: flex; align-items: center; justify-content: space-between;
  gap: 12px; padding: 10px 4px; border-bottom: 1px solid var(--el-border-color-lighter); }
.mem-text { flex: 1; word-break: break-word; }
.empty { color: var(--el-text-color-secondary); padding: 16px 4px; }
</style>
```
> Adapt imports/style tokens to match `PromptTemplateManager.vue` if the project has conventions (e.g. how it imports Element Plus, drawer size). Keep behavior: open → load list; delete → confirm → call API → remove from list.

- [ ] **Step 3: Entry in `AiChatView.vue`** — READ how `PromptTemplateManager` is wired (a `showTemplateManager` ref + `<PromptTemplateManager v-model="showTemplateManager" />` near line 829, opened by some button). Mirror it:
  - add `import MemoryManager from '@/components/ai-chat/MemoryManager.vue'`
  - add `const showMemoryManager = ref(false)`
  - add `<MemoryManager v-model="showMemoryManager" />` next to the PromptTemplateManager mount
  - add a small entry to open it — place a "我的记忆" button in the SAME place/menu where "管理模板"/template manager is reachable from the assistant UI (follow the existing entry's pattern; if templates are opened from a header/menu in AiChatView, add the memory button there).

- [ ] **Step 4: Type check:**
`cd E:/wsl/check/check-manage && npx vue-tsc --noEmit -p tsconfig.json` → no NEW errors.

- [ ] **Step 5: Commit:**
```
git add src/api/aiChat.ts src/components/ai-chat/MemoryManager.vue src/views/ai-chat/AiChatView.vue
git commit -m "feat(memory): memory management drawer in AI assistant (M4)"
```

## Context
- `src/views/ai-chat/AiChatView.vue` is the main assistant view; it already mounts `PromptTemplateManager` via `v-model="showTemplateManager"` (~line 829). Mirror that exact pattern for `MemoryManager`.
- `src/api/aiChat.ts` imports `get, post, del, patch` from `@/utils/request` already.
- Keep the component small and consistent with `PromptTemplateManager.vue` (same Element Plus import style, drawer sizing).

---

## Task 3：文档

**Files:** `docs/user-guide/ai/long-term-memory.md`, `CLAUDE.md`.

- [ ] **Step 1:** Append to the user guide:
```markdown
## 查看与删除记忆（M4）

在 AI 助手里打开「我的记忆」，可查看助手记住的全部长期记忆，并删除不准确或过时的条目（删除只影响你自己的记忆）。
```

- [ ] **Step 2:** Append to the CLAUDE.md AI memory sentence:
```
M4：`GET/DELETE /ai/memories`（`routes/ai.py`，删除前校验归属）+ 前端 `MemoryManager.vue`（AI 助手「我的记忆」抽屉）供用户查看/删除自己的记忆。
```

- [ ] **Step 3: Commit:**
```
git add docs/user-guide/ai/long-term-memory.md CLAUDE.md
git commit -m "docs(memory): memory management UI guide (M4)"
```

---

## 验收（M4）
- [ ] `pytest tests/test_routes_ai_memories.py` 全绿（list + 归属校验删除）；既有测试无回归。
- [ ] `vue-tsc` 通过。
- [ ] 手动（可选）：开启 mem0 → 对话产生记忆 → AI 助手「我的记忆」看到 → 删除一条 → 列表更新。
