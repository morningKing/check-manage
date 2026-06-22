# 手动补写记忆 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development 或 superpowers:executing-plans。Steps use checkbox (`- [ ]`)。

**Goal:** 让用户在「我的记忆」抽屉手动补写一条长期记忆，默认 mem0 提炼（`infer=True`），勾「原样保存」则 `infer=False`。

**Architecture:** `memory.py` 加 `add_memory_text(user_id, text, infer)`（钉单线程 executor 调 `m.add(infer=)`）；`routes/ai.py` 加 `POST /ai/memories`（校验 + 409 未配置 + 写当前用户 + 回传最新列表）；前端 `aiChat.ts` 加 `addMemory`，`MemoryManager.vue` 加补写区。

**Tech Stack:** Flask、mem0 2.0.7（`Memory.add(..., infer=True)`）、Vue3 + Element Plus、pytest。

**Spec:** `docs/superpowers/specs/2026-06-22-manual-memory-write-design.md`

---

## File Structure
- `server/utils/memory.py` — 新增 `add_memory_text`
- `server/routes/ai.py` — 新增 `POST /ai/memories`（import 补 `add_memory_text`、`get_memory`）
- `server/tests/test_memory_manual_add.py`（新）
- `src/api/aiChat.ts` — 新增 `addMemory`
- `src/components/ai-chat/MemoryManager.vue` — 补写区
- 文档：`docs/user-guide/ai/long-term-memory.md`、`CLAUDE.md`

---

## Task 1：后端 `add_memory_text` + `POST /ai/memories`

**Files:** Modify `server/utils/memory.py`, `server/routes/ai.py`; Create `server/tests/test_memory_manual_add.py`.

参考事实：
- `memory.py` 已有 `_on_mem_thread(fn)`、`get_memory()`、`list_memories(user_id)`、`logger`。
- `routes/ai.py` 顶部已 `from utils.memory import reset_memory_singleton, list_memories, delete_memory`，已 import `request`、`g`、`login_required`；蓝图为 `ai_bp`，现有 `GET /memories`、`DELETE /memories/<id>`。

- [ ] **Step 1: 写失败测试** `server/tests/test_memory_manual_add.py`:
```python
import sys, os
from unittest.mock import patch, MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from auth import create_token

def _client():
    from app import app
    app.config['TESTING'] = True
    return app.test_client()

def _h(uid='u1', role='developer'):
    return {'Authorization': 'Bearer ' + create_token({'id': uid, 'username': uid, 'role': role})}

# ---- add_memory_text unit ----
def test_add_memory_text_default_infer_true():
    import utils.memory as mem
    m = MagicMock()
    with patch.object(mem, 'get_memory', return_value=m), \
         patch.object(mem, '_on_mem_thread', side_effect=lambda fn: fn()):
        ok = mem.add_memory_text('u1', '负责 PostgreSQL 运维')
    assert ok is True
    assert m.add.call_args.kwargs['infer'] is True
    assert m.add.call_args.kwargs['user_id'] == 'u1'

def test_add_memory_text_verbatim_infer_false():
    import utils.memory as mem
    m = MagicMock()
    with patch.object(mem, 'get_memory', return_value=m), \
         patch.object(mem, '_on_mem_thread', side_effect=lambda fn: fn()):
        ok = mem.add_memory_text('u1', '原样一句话', infer=False)
    assert ok is True
    assert m.add.call_args.kwargs['infer'] is False

def test_add_memory_text_no_memory_returns_false():
    import utils.memory as mem
    with patch.object(mem, 'get_memory', return_value=None):
        assert mem.add_memory_text('u1', 'x') is False

# ---- POST /ai/memories ----
def test_post_memory_empty_400():
    import routes.ai as ai
    with patch.object(ai, 'get_memory', return_value=MagicMock()):
        r = _client().post('/ai/memories', json={'text': '   '}, headers=_h())
    assert r.status_code == 400

def test_post_memory_too_long_400():
    import routes.ai as ai
    with patch.object(ai, 'get_memory', return_value=MagicMock()):
        r = _client().post('/ai/memories', json={'text': 'a' * 2001}, headers=_h())
    assert r.status_code == 400

def test_post_memory_unavailable_409():
    import routes.ai as ai
    with patch.object(ai, 'get_memory', return_value=None):
        r = _client().post('/ai/memories', json={'text': 'hi'}, headers=_h())
    assert r.status_code == 409

def test_post_memory_ok_writes_current_user_infer_true():
    import routes.ai as ai
    with patch.object(ai, 'get_memory', return_value=MagicMock()), \
         patch.object(ai, 'add_memory_text', return_value=True) as add, \
         patch.object(ai, 'list_memories', return_value=[{'id': '1', 'memory': 'hi'}]):
        r = _client().post('/ai/memories', json={'text': 'hi'}, headers=_h(uid='u9'))
    assert r.status_code == 200
    assert r.get_json()['memories'] == [{'id': '1', 'memory': 'hi'}]
    assert add.call_args.args[0] == 'u9'           # current user id
    assert add.call_args.kwargs['infer'] is True

def test_post_memory_verbatim_infer_false():
    import routes.ai as ai
    with patch.object(ai, 'get_memory', return_value=MagicMock()), \
         patch.object(ai, 'add_memory_text', return_value=True) as add, \
         patch.object(ai, 'list_memories', return_value=[]):
        r = _client().post('/ai/memories', json={'text': 'hi', 'verbatim': True}, headers=_h())
    assert r.status_code == 200
    assert add.call_args.kwargs['infer'] is False
```
> 测试断言 `routes.ai` 模块里能 patch 到 `get_memory`、`add_memory_text`、`list_memories` —— 故它们必须是 `routes/ai.py` 顶层 import 的名字。

- [ ] **Step 2: Run, confirm FAIL**:
`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_memory_manual_add.py -v`
Expected: 失败（`add_memory_text` 不存在 / `POST /ai/memories` 404 或 405）。

- [ ] **Step 3: 实现 `add_memory_text`** in `server/utils/memory.py`（放在 `add_memory` 附近）:
```python
def add_memory_text(user_id, text, infer=True):
    """手动补写一条记忆。infer=False 为 verbatim（原样、不提炼，仍嵌入）。
    返回是否写入（mem0 不可用/降级时 False）。"""
    m = get_memory()
    if m is None or not user_id or not text:
        return False
    try:
        _on_mem_thread(lambda: m.add([{'role': 'user', 'content': text}],
                                     user_id=user_id, infer=infer))
        return True
    except Exception as e:
        logger.warning('mem0 manual add failed: %s', e)
        return False
```

- [ ] **Step 4: 实现 `POST /ai/memories`** in `server/routes/ai.py`:
  - 顶部 import 改为：`from utils.memory import reset_memory_singleton, list_memories, delete_memory, add_memory_text, get_memory`。
  - 在 `GET /memories` 之后、`DELETE` 之前加：
```python
@ai_bp.route('/memories', methods=['POST'])
@login_required
def add_my_memory():
    user = g.current_user
    body = request.get_json(silent=True) or {}
    text = (body.get('text') or '').strip()
    verbatim = bool(body.get('verbatim'))
    if not text:
        return jsonify({'error': '内容不能为空'}), 400
    if len(text) > 2000:
        return jsonify({'error': '内容过长（上限 2000 字符）'}), 400
    if get_memory() is None:
        return jsonify({'error': '记忆功能未配置（缺少 API Key 或未启用底层）',
                        'code': 'MEMORY_UNAVAILABLE'}), 409
    if not add_memory_text(user['userId'], text, infer=not verbatim):
        return jsonify({'error': '写入失败'}), 500
    return jsonify({'ok': True, 'memories': list_memories(user['userId'])})
```
> 确认 `request` 已在 `routes/ai.py` import（现有 GET/PUT settings 用到）。若未 import，补 `from flask import ..., request`。

- [ ] **Step 5: Run, confirm PASS**（8 passed）。再跑回归：
`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_memory_manual_add.py tests/test_memory.py tests/test_ai_settings_mem0.py -q`

- [ ] **Step 6: Commit**:
```
cd E:/wsl/check/check-manage
git add server/utils/memory.py server/routes/ai.py server/tests/test_memory_manual_add.py
git commit -m "feat(memory): manual memory write endpoint (infer default + verbatim)"
```

---

## Task 2：前端补写 UI

**Files:** Modify `src/api/aiChat.ts`, `src/components/ai-chat/MemoryManager.vue`.

参考事实：
- `aiChat.ts` 已 `import { get, post, del, patch } from '@/utils/request'`，已有 `listMemories`/`deleteMemory` + `export interface AiMemory { id: string; memory: string }`。
- `MemoryManager.vue` 用 `<script setup>`，已 import `ElDrawer, ElButton, ElMessage, ElMessageBox` 与 `listMemories, deleteMemory, type AiMemory`；`items`/`loading` 为 `ref`，`load()` 拉列表。

- [ ] **Step 1: api** — 在 `src/api/aiChat.ts` 的 `deleteMemory` 之后加：
```ts
export function addMemory(text: string, verbatim = false) {
  return post<{ ok: boolean; memories: AiMemory[] }>('/ai/memories', { text, verbatim })
}
```

- [ ] **Step 2: 组件脚本** — 在 `MemoryManager.vue` `<script setup>`：
  - import 改为：`import { ElDrawer, ElButton, ElInput, ElSwitch, ElMessage, ElMessageBox } from 'element-plus'`，并把 `addMemory` 加入 `@/api/aiChat` 的 import。
  - 加状态与方法：
```ts
const draft = ref('')
const verbatim = ref(false)
const adding = ref(false)

async function add() {
  const text = draft.value.trim()
  if (!text) return
  adding.value = true
  try {
    const res = await addMemory(text, verbatim.value)
    items.value = res.memories || []
    draft.value = ''
    ElMessage.success('已添加')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || '添加失败')
  } finally {
    adding.value = false
  }
}
```

- [ ] **Step 3: 组件模板** — 在 `<div v-loading="loading">` 内、记忆列表之前插入补写区：
```html
      <div class="mem-add">
        <ElInput v-model="draft" type="textarea" :rows="2" :maxlength="2000" show-word-limit
          placeholder="写一句话关键事实，如：负责 PostgreSQL 运维" />
        <div class="mem-add__bar">
          <ElSwitch v-model="verbatim" />
          <span class="mem-add__hint">原样保存（不提炼）— 默认会被 AI 提炼成简洁事实，原样适合一句话关键事实</span>
          <ElButton type="primary" size="small" :loading="adding" :disabled="!draft.trim()" @click="add">添加</ElButton>
        </div>
      </div>
```
  - 在 `<style scoped>` 末尾加：
```css
.mem-add { margin-bottom: 12px; }
.mem-add__bar { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.mem-add__hint { flex: 1; font-size: 12px; color: var(--el-text-color-secondary); }
```

- [ ] **Step 4: 类型检查**: `cd E:/wsl/check/check-manage && npx vue-tsc --noEmit -p tsconfig.json` → 你改的 3 文件无新增错误。

- [ ] **Step 5: Commit**:
```
git add src/api/aiChat.ts src/components/ai-chat/MemoryManager.vue
git commit -m "feat(memory): UI to manually add a memory (verbatim toggle)"
```

---

## Task 3：文档

**Files:** `docs/user-guide/ai/long-term-memory.md`、`CLAUDE.md`。

- [ ] **Step 1:** `long-term-memory.md` 补一节「手动补写记忆」：在「我的记忆」抽屉添加；默认 AI 提炼成简洁事实，勾「原样保存」则不提炼；建议短而原子（利于向量检索）；与自动逐轮抽取互补；已存记忆仍可查看/删除。
- [ ] **Step 2:** `CLAUDE.md` AI 记忆段加一句：`POST /ai/memories`（手动补写，`add_memory_text(user_id, text, infer)`；`verbatim=True`→`infer=False` 原样、仍嵌入；不受启用开关影响，apiKey 缺失返回 409）。
- [ ] **Step 3: Commit**:
```
git add docs/user-guide CLAUDE.md
git commit -m "docs(memory): manual memory write"
```

---

## 验收
- [ ] `pytest tests/test_memory_manual_add.py` 全绿（8）；mem0 既有测试无回归。
- [ ] `POST /ai/memories`：空→400、超长→400、未配置→409、正常→200 回传最新列表、写当前用户、`verbatim`→`infer=False`。
- [ ] 前端 `vue-tsc` clean；抽屉可添加、刷新、清空、错误提示。
- [ ] 手动：抽屉输入→添加→出现在列表；勾「原样保存」→ 原文入库。
