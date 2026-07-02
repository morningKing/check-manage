# 客服主页现代化 — Stage ①（后端 panel_blocks + 管理端编辑器）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 后端支持自助区块配置（`kefu_instances.panel_blocks` JSONB）+ 复用 `guided_questions`，公开配置返回二者，管理端 `KefuManager` 补提示气泡编辑器 + 自助区块编辑器，端到端可 curl/pytest + 管理端 Playwright 验证。

**Architecture:** 沿用 kefu 架构：`kefu_instances` 加 JSONB 列 `panel_blocks`（有序区块数组，4 种 type）；`kefu_repo` 读写它；实例 PATCH 白名单加 `guided_questions`/`panel_blocks` 并做结构校验；公开配置端点返回它们。前端 `KefuManager.vue` 增气泡编辑 + 独立 `KefuBlocksEditor.vue`。访客页渲染属 Stage ②。

**Tech Stack:** Flask + psycopg2 + PostgreSQL（JSONB）；Vue 3 + Element Plus + md-editor-v3；pytest（Windows 需 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`）。

## Global Constraints

- `panel_blocks` 是 `kefu_instances` 的 JSONB 列（默认 `'[]'`），**非新表**。每块 `{id,type,title,enabled,config}`，type ∈ `links|faq|richtext|contact`。
- 提示气泡复用**现有** `kefu_instances.guided_questions`（JSONB 字符串数组，已在表/`_row_to_instance`/`update_instance` 白名单中）。
- 迁移随 `server/migrate_kefu.py` 的 `_SQL` 幂等追加 + `server/init_db.py` 平行加列。
- 后端对 `panel_blocks` 只做**基本结构校验**：必须是数组；每项是对象且 `type` ∈ 合法集合。非法 → PATCH 返回 400。细粒度内容校验（url 协议等）留前端。
- 无需新管理端点：复用实例 `PATCH /admin/kefu/instances/<iid>`（扩白名单 + 校验）。公开 `GET /kefu/i/<slug>` 返回值加 `panel_blocks`（`guided_questions` 已在返回）。
- 后端测试从 `server/` 运行，需 env `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`；前端 API 走 `@/utils/request`（已导出 `get/post/patch/del`）。
- 本 Stage 不做访客页渲染（Stage ②）。提交用中文 `feat:`/`fix:`/`test:`/`docs:` 前缀。

---

### Task 1: 迁移 `panel_blocks` 列

**Files:**
- Modify: `server/migrate_kefu.py`（`_SQL` 追加 ALTER）
- Modify: `server/init_db.py`（`kefu_instances` 建表加列）
- Test: `server/tests/test_kefu_panel_blocks_migration.py`（真库 `db_conn`）

**Interfaces:**
- Produces: `kefu_instances.panel_blocks JSONB NOT NULL DEFAULT '[]'`。`migrate_kefu(conn)` 仍幂等。

- [ ] **Step 1: 写失败测试**

```python
# server/tests/test_kefu_panel_blocks_migration.py
from migrate_kefu import migrate_kefu


def test_migrate_adds_panel_blocks(db_conn):
    migrate_kefu(db_conn)
    migrate_kefu(db_conn)  # idempotent
    cur = db_conn.cursor()
    cur.execute("SELECT 1 FROM information_schema.columns "
                "WHERE table_name='kefu_instances' AND column_name='panel_blocks'")
    assert cur.fetchone() is not None
    cur.execute("SELECT column_default FROM information_schema.columns "
                "WHERE table_name='kefu_instances' AND column_name='panel_blocks'")
    assert "'[]'" in (cur.fetchone()[0] or '')
    db_conn.rollback()
```

- [ ] **Step 2: 运行确认失败** — `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_kefu_panel_blocks_migration.py -v` → FAIL（列不存在）

- [ ] **Step 3: 在 `migrate_kefu.py` 的 `_SQL` 末尾追加**

```sql

ALTER TABLE kefu_instances ADD COLUMN IF NOT EXISTS panel_blocks JSONB NOT NULL DEFAULT '[]'::jsonb;
```

- [ ] **Step 4: 在 `server/init_db.py` 的 `kefu_instances` CREATE TABLE 中，`rate_limit` 列之后加一列**（grep `kefu_instances` 定位；保持与 migrate 一致）：

```sql
  panel_blocks     JSONB NOT NULL DEFAULT '[]'::jsonb,
```

- [ ] **Step 5: 运行确认通过** — 同 Step 2 命令 → PASS

- [ ] **Step 6: 开发库执行迁移** — `cd server && python migrate_kefu.py` → `kefu migration done`

- [ ] **Step 7: 提交**

```bash
git add server/migrate_kefu.py server/init_db.py server/tests/test_kefu_panel_blocks_migration.py
git commit -m "feat(kefu): kefu_instances 增列 panel_blocks"
```

---

### Task 2: `kefu_repo` 读写 `panel_blocks`

**Files:**
- Modify: `server/utils/kefu_repo.py`
- Test: `server/tests/test_kefu_panel_blocks_repo.py`（mocked `get_db`）

**Interfaces:**
- Produces: `_row_to_instance` 增 `panel_blocks`（索引 12）；`create_instance` 写入 `panel_blocks`；`update_instance` 白名单含 `panel_blocks`（JSON 序列化）。`_COLS` 末尾追加 `panel_blocks`。

- [ ] **Step 1: 写失败测试**

```python
# server/tests/test_kefu_panel_blocks_repo.py
from contextlib import contextmanager
from unittest.mock import patch
import utils.kefu_repo as repo


def _cm(conn):
    @contextmanager
    def cm():
        yield conn
    return cm()


def _row():  # 13 columns matching _COLS order (panel_blocks last)
    return ('kf_1', 'presale', '售前', None, None, None, None,
            [], {}, 'kefu-bot', True, {}, [{'id': 'b1', 'type': 'links'}])


def test_row_to_instance_includes_panel_blocks():
    inst = repo._row_to_instance(_row())
    assert inst['panel_blocks'] == [{'id': 'b1', 'type': 'links'}]


def test_update_instance_writes_panel_blocks(mock_conn, mock_cursor):
    mock_cursor.fetchone.return_value = _row()
    blocks = [{'id': 'b1', 'type': 'faq', 'title': '热点', 'enabled': True, 'config': {'limit': 5}}]
    with patch('utils.kefu_repo.get_db', lambda: _cm(mock_conn)):
        repo.update_instance('kf_1', {'panel_blocks': blocks})
    sql = ' '.join(c.args[0] for c in mock_cursor.execute.call_args_list)
    assert 'panel_blocks=%s' in sql
    # value serialized as JSON
    import json
    params = [p for c in mock_cursor.execute.call_args_list if c.args[1] for p in c.args[1]]
    assert json.dumps(blocks) in params
```

- [ ] **Step 2: 运行确认失败** — `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_kefu_panel_blocks_repo.py -v` → FAIL

- [ ] **Step 3: 改 `kefu_repo.py`**

(a) `_COLS` 末尾加 `panel_blocks`：
```python
_COLS = (
    "id, slug, name, agent, model, system_prompt, welcome_message, "
    "guided_questions, branding, bot_user_id, enabled, rate_limit, panel_blocks"
)
```
(b) `_row_to_instance` 增字段（在 `rate_limit` 之后）：
```python
        'rate_limit': r[11],
        'panel_blocks': r[12],
    }
```
(c) `create_instance` 的 INSERT：列清单末尾加 `panel_blocks`，VALUES 加一个 `%s`，params 末尾加 `json.dumps(payload.get('panel_blocks') or [])`：
```python
            "INSERT INTO kefu_instances "
            "(id, slug, name, agent, model, system_prompt, welcome_message, "
            " guided_questions, branding, bot_user_id, enabled, rate_limit, panel_blocks) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            f"RETURNING {_COLS}",
            (
                iid, payload['slug'], payload['name'],
                payload.get('agent') or None, payload.get('model') or None,
                payload.get('system_prompt') or None, payload.get('welcome_message') or None,
                json.dumps(payload.get('guided_questions') or []),
                json.dumps(payload.get('branding') or {}),
                bot_id, payload.get('enabled', True),
                json.dumps(payload.get('rate_limit') or {}),
                json.dumps(payload.get('panel_blocks') or []),
            ),
```
(d) `update_instance` 的 JSON 列循环加入 `panel_blocks`：
```python
    for col in ('guided_questions', 'branding', 'rate_limit', 'panel_blocks'):
        if col in payload:
            fields.append(f"{col}=%s")
            params.append(json.dumps(payload[col]))
```

- [ ] **Step 4: 运行确认通过** — 同 Step 2 → PASS

- [ ] **Step 5: 提交**

```bash
git add server/utils/kefu_repo.py server/tests/test_kefu_panel_blocks_repo.py
git commit -m "feat(kefu): kefu_repo 读写 panel_blocks"
```

---

### Task 3: PATCH 校验 + 公开配置返回

**Files:**
- Modify: `server/routes/kefu_admin.py`（update_instance 加 panel_blocks 结构校验）
- Modify: `server/routes/kefu_public.py`（`_public_config` 返回 panel_blocks）
- Test: `server/tests/test_kefu_panel_blocks_routes.py`

**Interfaces:**
- Consumes: `kefu_repo.update_instance`。
- Produces: 管理 PATCH 对非法 `panel_blocks` 返回 400；`GET /kefu/i/<slug>` 返回值含 `panel_blocks`。
- 校验规则（模块级 helper `_validate_panel_blocks(v) -> str | None`，返回错误消息或 None）：`v` 非 list → 报错；任一项非 dict 或 `type` ∉ `{'links','faq','richtext','contact'}` → 报错。

- [ ] **Step 1: 写失败测试**

```python
# server/tests/test_kefu_panel_blocks_routes.py
from unittest.mock import patch


def test_patch_rejects_non_list_panel_blocks(client, admin_headers):
    r = client.patch('/admin/kefu/instances/kf_1', json={'panel_blocks': {'not': 'a list'}},
                     headers=admin_headers)
    assert r.status_code == 400


def test_patch_rejects_bad_block_type(client, admin_headers):
    r = client.patch('/admin/kefu/instances/kf_1',
                     json={'panel_blocks': [{'id': 'b1', 'type': 'evil'}]}, headers=admin_headers)
    assert r.status_code == 400


def test_patch_accepts_valid_panel_blocks(client, admin_headers):
    blocks = [{'id': 'b1', 'type': 'faq', 'title': '热点', 'enabled': True, 'config': {'limit': 5}}]
    with patch('routes.kefu_admin.kefu_repo.update_instance',
               return_value={'id': 'kf_1', 'name': 'X', 'panel_blocks': blocks}) as m:
        r = client.patch('/admin/kefu/instances/kf_1', json={'panel_blocks': blocks},
                         headers=admin_headers)
    assert r.status_code == 200
    m.assert_called_once()


def test_public_config_returns_panel_blocks(client):
    inst = {'id': 'kf_1', 'slug': 'presale', 'name': '售前', 'enabled': True,
            'welcome_message': 'hi', 'guided_questions': ['Q?'], 'branding': {},
            'panel_blocks': [{'id': 'b1', 'type': 'contact', 'config': {'phone': '123'}}]}
    with patch('routes.kefu_public.kefu_repo.get_instance_by_slug', return_value=inst):
        r = client.get('/kefu/i/presale')
    body = r.get_json()
    assert body['panel_blocks'] == inst['panel_blocks']
    assert body['guided_questions'] == ['Q?']
```

- [ ] **Step 2: 运行确认失败** — `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_kefu_panel_blocks_routes.py -v` → FAIL

- [ ] **Step 3: 改 `kefu_admin.py`** — 在 `update_instance` 路由体开头（`_SLUG_RE` 校验附近）加校验；顶部加 helper：

```python
_BLOCK_TYPES = {'links', 'faq', 'richtext', 'contact'}


def _validate_panel_blocks(v):
    if not isinstance(v, list):
        return 'panel_blocks 必须是数组'
    for b in v:
        if not isinstance(b, dict) or b.get('type') not in _BLOCK_TYPES:
            return 'panel_blocks 每项需为对象且 type 合法'
    return None
```
在 `update_instance` 路由里，`body` 取到后、调 `kefu_repo.update_instance` 前：
```python
    if 'panel_blocks' in body:
        err = _validate_panel_blocks(body['panel_blocks'])
        if err:
            return jsonify({'error': err}), 400
```

- [ ] **Step 4: 改 `kefu_public.py` `_public_config`** — 返回 dict 加一行：
```python
        'panel_blocks': inst.get('panel_blocks') or [],
```

- [ ] **Step 5: 运行确认通过 + 全量回归** — 
`cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_kefu_panel_blocks_routes.py -v` → PASS；
再 `python -m pytest tests/ -q`（仅既有预存失败 `test_ai_scan_engine::test_run_one_invokes_scan_hook_on_success`，无新失败）。

- [ ] **Step 6: 提交**

```bash
git add server/routes/kefu_admin.py server/routes/kefu_public.py server/tests/test_kefu_panel_blocks_routes.py
git commit -m "feat(kefu): panel_blocks 校验 + 公开配置返回"
```

---

### Task 4: 管理端编辑器（气泡 + 自助区块）

**Files:**
- Create: `src/components/kefu/KefuBlocksEditor.vue`
- Modify: `src/views/admin/KefuManager.vue`（挂入气泡编辑器 + 区块编辑器）
- Modify: `src/api/kefu.ts`（`getInstance` + `updateInstance`）
- Test: `src/components/kefu/__tests__/KefuBlocksEditor.test.ts`

**Interfaces:**
- Consumes: 实例 PATCH（Task 3）。
- Produces: `src/api/kefu.ts` 增 `getInstance(iid)`、`updateInstance(iid, data)`；`KefuBlocksEditor.vue`（v-model `blocks`，add/remove/reorder/启停 + 按 type 子表单）。

- [ ] **Step 1: 写 `src/api/kefu.ts` 增量**（在现有导出后追加）

```ts
export interface KefuInstanceFull extends KefuInstance {
  guided_questions?: string[]
  panel_blocks?: any[]
}
export function getInstance(iid: string) { return get<KefuInstanceFull>(`/admin/kefu/instances/${iid}`) }
export function updateInstance(iid: string, data: Partial<KefuInstanceFull>) { return patch<KefuInstanceFull>(`/admin/kefu/instances/${iid}`, data) }
```

- [ ] **Step 2: 写失败测试**（区块编辑器逻辑）

```ts
// src/components/kefu/__tests__/KefuBlocksEditor.test.ts
import { describe, it, expect, beforeAll } from 'vitest'
import { mount } from '@vue/test-utils'
import KefuBlocksEditor from '../KefuBlocksEditor.vue'

beforeAll(() => { globalThis.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} } as any })
const stubs = {
  'el-select': { template: `<select :value="modelValue" @change="$emit('update:modelValue',$event.target.value)"><slot/></select>`, props: ['modelValue'] },
  'el-option': { template: `<option :value="value"><slot/></option>`, props: ['value','label'] },
  'el-input': { template: `<input :value="modelValue" @input="$emit('update:modelValue',$event.target.value)" />`, props: ['modelValue'] },
  'el-button': { template: `<button @click="$emit('click')"><slot/></button>` },
  'el-switch': { template: `<input type="checkbox" :checked="modelValue" @change="$emit('update:modelValue',$event.target.checked)" />`, props: ['modelValue'] },
  MdEditor: { template: `<textarea :value="modelValue" @input="$emit('update:modelValue',$event.target.value)" />`, props: ['modelValue'] },
}

describe('KefuBlocksEditor', () => {
  it('addBlock appends a typed block and emits update', async () => {
    const w = mount(KefuBlocksEditor, { props: { modelValue: [] }, global: { stubs } })
    ;(w.vm as any).addBlock('links')
    const emitted = w.emitted('update:modelValue')!.at(-1)![0] as any[]
    expect(emitted).toHaveLength(1)
    expect(emitted[0].type).toBe('links')
    expect(emitted[0].id).toMatch(/^blk_/)
    expect(emitted[0].enabled).toBe(true)
  })

  it('removeBlock drops by index', async () => {
    const blocks = [{ id: 'blk_a', type: 'faq', title: '', enabled: true, config: {} },
                    { id: 'blk_b', type: 'contact', title: '', enabled: true, config: {} }]
    const w = mount(KefuBlocksEditor, { props: { modelValue: blocks }, global: { stubs } })
    ;(w.vm as any).removeBlock(0)
    const emitted = w.emitted('update:modelValue')!.at(-1)![0] as any[]
    expect(emitted.map((b: any) => b.id)).toEqual(['blk_b'])
  })

  it('move reorders blocks', async () => {
    const blocks = [{ id: 'blk_a', type: 'faq', title: '', enabled: true, config: {} },
                    { id: 'blk_b', type: 'contact', title: '', enabled: true, config: {} }]
    const w = mount(KefuBlocksEditor, { props: { modelValue: blocks }, global: { stubs } })
    ;(w.vm as any).move(0, 1)
    const emitted = w.emitted('update:modelValue')!.at(-1)![0] as any[]
    expect(emitted.map((b: any) => b.id)).toEqual(['blk_b', 'blk_a'])
  })
})
```

- [ ] **Step 3: 运行确认失败** — `npx vitest run src/components/kefu/__tests__/KefuBlocksEditor.test.ts` → FAIL

- [ ] **Step 4: 写 `KefuBlocksEditor.vue`**

```vue
<template>
  <div class="kefu-blocks-editor">
    <div class="toolbar">
      <span>自助区块</span>
      <el-select v-model="newType" placeholder="选择类型" style="width:140px">
        <el-option v-for="t in TYPES" :key="t.value" :value="t.value" :label="t.label" />
      </el-select>
      <el-button @click="addBlock(newType)">新增区块</el-button>
    </div>
    <div v-for="(b, idx) in list" :key="b.id" class="block-row">
      <div class="block-head">
        <el-button link :disabled="idx===0" @click="move(idx,-1)">↑</el-button>
        <el-button link :disabled="idx===list.length-1" @click="move(idx,1)">↓</el-button>
        <span class="btype">{{ labelOf(b.type) }}</span>
        <el-input v-model="b.title" placeholder="区块标题（可空）" style="width:200px" @input="emit" />
        <el-switch v-model="b.enabled" @update:modelValue="emit" />
        <el-button link type="danger" @click="removeBlock(idx)">删除</el-button>
      </div>
      <div class="block-body">
        <!-- links -->
        <template v-if="b.type==='links'">
          <div v-for="(it, j) in (b.config.items || [])" :key="j" class="link-item">
            <el-input v-model="it.icon" placeholder="图标(可空)" style="width:100px" @input="emit" />
            <el-input v-model="it.label" placeholder="名称" style="width:160px" @input="emit" />
            <el-input v-model="it.url" placeholder="https://…" style="width:240px" @input="emit" />
            <el-button link type="danger" @click="delItem(b, j)">×</el-button>
          </div>
          <el-button size="small" @click="addItem(b)">+ 添加入口</el-button>
        </template>
        <!-- faq -->
        <template v-else-if="b.type==='faq'">
          <el-input v-model.number="b.config.limit" type="number" placeholder="展示条数(默认5)" style="width:180px" @input="emit" />
        </template>
        <!-- richtext -->
        <template v-else-if="b.type==='richtext'">
          <MdEditor v-model="b.config.markdown" style="height:200px" @onChange="emit" />
        </template>
        <!-- contact -->
        <template v-else-if="b.type==='contact'">
          <el-input v-model="b.config.phone" placeholder="电话" @input="emit" />
          <el-input v-model="b.config.email" placeholder="邮箱" @input="emit" />
          <el-input v-model="b.config.hours" placeholder="工作时间" @input="emit" />
          <el-input v-model="b.config.wechat" placeholder="微信" @input="emit" />
        </template>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { MdEditor } from 'md-editor-v3'
import 'md-editor-v3/lib/style.css'

interface Block { id: string; type: string; title: string; enabled: boolean; config: any }
const props = defineProps<{ modelValue: Block[] }>()
const emits = defineEmits<{ (e: 'update:modelValue', v: Block[]): void }>()

const TYPES = [
  { value: 'links', label: '快捷入口' }, { value: 'faq', label: '热点问题' },
  { value: 'richtext', label: '公告/富文本' }, { value: 'contact', label: '联系方式' },
]
const newType = ref('links')
const list = ref<Block[]>(clone(props.modelValue))
watch(() => props.modelValue, v => { list.value = clone(v) })

function clone(v: Block[]) { return JSON.parse(JSON.stringify(v || [])) }
function labelOf(t: string) { return TYPES.find(x => x.value === t)?.label || t }
function emit() { emits('update:modelValue', clone(list.value)) }

function addBlock(type: string) {
  const id = 'blk_' + Math.random().toString(36).slice(2, 8)
  const config = type === 'links' ? { items: [] } : type === 'faq' ? { limit: 5 } : type === 'richtext' ? { markdown: '' } : {}
  list.value.push({ id, type, title: '', enabled: true, config })
  emit()
}
function removeBlock(idx: number) { list.value.splice(idx, 1); emit() }
function move(idx: number, dir: number) {
  const j = idx + dir; const a = list.value
  ;[a[idx], a[j]] = [a[j], a[idx]]; emit()
}
function addItem(b: Block) { (b.config.items ||= []).push({ icon: '', label: '', url: '' }); emit() }
function delItem(b: Block, j: number) { b.config.items.splice(j, 1); emit() }

defineExpose({ addBlock, removeBlock, move })
</script>
```

- [ ] **Step 5: 挂入 `KefuManager.vue`** — 在实例选择后、FAQ 表附近加两块（读现有文件结构，追加）：一个 `guided_questions` 编辑（简单字符串列表，加/删/上下移）与 `<KefuBlocksEditor v-model="blocks" />`；加载实例时 `getInstance(iid)` 填充 `bubbles`/`blocks`，一个「保存主页配置」按钮调 `updateInstance(iid, { guided_questions: bubbles, panel_blocks: blocks })`。示例脚本片段：

```ts
import * as kapi from '@/api/kefu'
import KefuBlocksEditor from '@/components/kefu/KefuBlocksEditor.vue'
const bubbles = ref<string[]>([])
const blocks = ref<any[]>([])
async function loadHome() {
  if (!activeIid.value) return
  const inst = await kapi.getInstance(activeIid.value)
  bubbles.value = inst.guided_questions || []
  blocks.value = inst.panel_blocks || []
}
async function saveHome() {
  await kapi.updateInstance(activeIid.value, { guided_questions: bubbles.value, panel_blocks: blocks.value })
  ElMessage.success('主页配置已保存')
}
```
（气泡编辑用一组 `el-input` + 加/删/上下移按钮，绑定 `bubbles`；`loadHome()` 在实例切换时调用，与现有 `loadFaq()` 并列。）

- [ ] **Step 6: 运行前端测试 + 类型检查** — `npx vitest run src/components/kefu/__tests__/KefuBlocksEditor.test.ts` → PASS；`npm run build` → vue-tsc clean。

- [ ] **Step 7: Playwright 手测（必做）** — 后端重启（加载 panel_blocks 路由）+ vite 起；登录 admin，进 `/admin/kefu` 选实例 → 加提示气泡若干 → 加各类型区块（links 填 2 条、faq limit=3、richtext 写 Markdown、contact 填电话）→ 保存 → 刷新后配置保留（DB 交叉核对 `kefu_instances.panel_blocks`/`guided_questions`）。截图存 `.playwright-mcp/kefu-home-admin.png`。

- [ ] **Step 8: 提交**

```bash
git add src/api/kefu.ts src/components/kefu/KefuBlocksEditor.vue src/views/admin/KefuManager.vue src/components/kefu/__tests__/KefuBlocksEditor.test.ts
git commit -m "feat(kefu): 管理端提示气泡 + 自助区块编辑器"
```

---

### Task 5: 文档

**Files:**
- Modify: `docs/user-guide/ai/smart-customer-service.md`

- [ ] **Step 1:** 新增/扩写「访客主页配置（提示气泡 + 自助区块）」小节：在 `/admin/kefu` 如何设提示气泡（`guided_questions`）、如何加 4 种自助区块及各自字段、保存生效；注明访客页新版布局渲染属 Stage ②。

- [ ] **Step 2: 提交**

```bash
git add docs/user-guide/ai/smart-customer-service.md
git commit -m "docs(kefu): 访客主页配置（气泡+区块）Stage① 文档"
```

---

## Self-Review

**Spec coverage（对照 design §3–§6, §9, §10 阶段①）：**
- §3.1 迁移 → Task 1 ✓；§3.3/§4 panel_blocks 结构 → Task 2（存储）+ Task 3（校验）✓；§5.2 repo → Task 2 ✓；§5.3 公开配置 → Task 3 ✓；§5.4 PATCH 校验 → Task 3 ✓；§6 管理端编辑器（气泡 + 区块） → Task 4 ✓；§9 测试（pytest + Vitest + Playwright + 文档） → Task 1-5 ✓；§10 阶段① 范围 → 本计划 ✓。
- 访客页渲染（§7、§10 阶段②）**不在本计划**。

**Placeholder scan:** 无 TBD/TODO。Task 5 Step 5 的 KefuManager 集成给了脚本片段 + 明确挂载点（气泡编辑用 el-input 列表），实现者按现有 KefuManager 结构补 UI；非占位（行为/接口明确）。

**Type consistency:** `panel_blocks` 块结构 `{id,type,title,enabled,config}` 在 Task 2（存储）、Task 3（校验 type 集合）、Task 4（编辑器 addBlock 生成同形）一致；`_COLS` 顺序（panel_blocks 末位，索引 12）与 `_row_to_instance` 一致；`updateInstance(iid,{guided_questions,panel_blocks})` 与后端 PATCH 白名单一致；block type 集合 `{links,faq,richtext,contact}` 前后端一致。

---

## 后续

- **Stage ②（访客页重构）**：`KefuChatPage` 两栏布局 + 提示气泡渲染（点击发送）+ `KefuServiceColumn` + 4 个 Block 子组件（links/faq/richtext/contact，faq 复用展开/埋点/转 AI）+ 移动抽屉 + 主题色。访客端 Playwright 全流程。落地后另出计划。
