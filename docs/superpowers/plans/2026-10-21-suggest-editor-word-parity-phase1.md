# 智能写作一期（表格/图片/排版/Word 导入导出）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给智能写作编辑器补齐表格、图片（含图注）、对齐/字号/颜色/高亮，以及结构化 docx 导入导出；同时保证现有本地规则与 AI 检查在表格/图注/导入场景下不退化。

**Architecture:** 前端用 Tiptap 扩展（TableKit / 自定义 figure / TextStyle 体系）实现编辑能力，浏览器端用 mammoth 解析 docx、用 docx 生成导出文件；Flask 只新增附件存储与导入种子 HTML 的一次性存取，严格文档属主。Node 侧扩展 `flattenFragment` 的表格/图注序列化，并在引擎中增加“导入基线 + 单轮分析上限”。

**Tech Stack:** Vue 3 + TypeScript + Element Plus、Tiptap 3.32、Yjs/Hocuspocus、Flask/psycopg2/PostgreSQL、mammoth、docx、Vitest、pytest、Playwright。

**Spec:** `docs/superpowers/specs/2026-10-21-suggest-editor-word-parity-phase1-design.md`

## Global Constraints

- 文档与附件严格属主私有；非属主/不存在统一 404，admin 不旁路。
- 附件 canonical URL 为 `/suggest-editor/attachments/<id>`；浏览器渲染/导出时才拼 `/api` 前缀与 token。
- 限制：docx ≤ 20 MB、≤ 200 张图、≤ 2000 块且文本 ≤ 2 MB；单图 ≤ 10 MB；seed HTML ≤ 5 MB。
- 图片允许 png/jpeg/gif/webp；扩展名 + magic bytes 双校验；不接受 SVG。
- AI：`MAX_ANALYZED_HASHES = 5000`、`MAX_DIRTY_BLOCKS_PER_RUN = 40`；导入后首轮只建基线不调模型。
- 源码改动必须走 Write/Edit（Mimosa 拦 Bash 直写）；Python 动态路径写文件用 pathlib。
- 后端单测使用独立库 `casemanage_test`；前端单测 vitest；E2E 用 dev mock 栈、worker=1。
- 每完成一个前端能力必须在任务内跑 `npx vue-tsc --noEmit` 对应单测；最终任务统一 `npm run build`。

## File Structure

**后端**

- Create `server/migrations/2026_10_21_suggest_editor_attachments.py`：附件表 + `se_documents.seed_content_html`。
- Create `server/utils/suggest_editor_attachments.py`：magic bytes、存储路径、DB CRUD、目录清理。
- Modify `server/app.py`：注册迁移启动钩子。
- Modify `server/utils/suggest_editor_repo.py`：文档读取返回 seed，seed 写入/清空。
- Modify `server/routes/suggest_editor.py`：附件上传/读取、seed 写入/消费、删除文档级联清理。
- Create `server/tests/test_suggest_editor_attachments.py`、`server/tests/test_suggest_editor_seed.py`。

**前端**

- Modify `package.json` / lock：Tiptap 扩展、mammoth、docx。
- Create `src/components/suggest-editor/core/formatting.ts` + `__tests__/formatting.test.ts`。
- Create `src/components/suggest-editor/core/table.ts`。
- Create `src/components/suggest-editor/core/attachment-url.ts`。
- Create `src/components/suggest-editor/core/figure.ts`。
- Create `src/components/suggest-editor/core/docx-import.ts` + `__tests__/docx-import.test.ts`。
- Create `src/components/suggest-editor/core/docx-export.ts` + `__tests__/docx-export.test.ts`。
- Create `src/components/suggest-editor/core/seed.ts` + `__tests__/seed.test.ts`。
- Create `src/components/suggest-editor/ImportDocxDialog.vue`。
- Modify `src/components/suggest-editor/SuggestEditorCore.vue`：扩展注册、粘贴/拖拽上传、文本统计（如需要）。
- Modify `src/components/suggest-editor/EditorToolbar.vue`：排版/表格/图片控件。
- Modify `src/components/suggest-editor/editor.scss`：figure/表格样式。
- Modify `src/views/suggest/SuggestEditorListView.vue`：导入入口与对话框。
- Modify `src/views/suggest/SuggestEditorDocView.vue`：seed 应用、导出按钮。
- Modify `src/api/suggestEditor.ts`、`src/types/suggestEditor.ts`：附件/seed API 与类型。
- Create `e2e/suggest-editor-word-parity.spec.ts`。

**Node**

- Modify `suggest-editor-server/src/flatten.ts`：table/figure 序列化。
- Modify `suggest-editor-server/src/engine.ts`：导入基线、dirty 上限、prompt 约束。
- Create `suggest-editor-server/tests/flatten.test.ts`、`suggest-editor-server/tests/engine-dirty.test.ts`。
- Modify `suggest-editor-server/package.json`：`test` script。

---

### Task 1: 排版扩展与工具栏（对齐/字号/颜色/高亮）

**Files:**
- Modify: `package.json`, `package-lock.json`
- Create: `src/components/suggest-editor/core/formatting.ts`
- Create: `src/components/suggest-editor/core/__tests__/formatting.test.ts`
- Modify: `src/components/suggest-editor/EditorToolbar.vue`
- Modify: `src/components/suggest-editor/SuggestEditorCore.vue`

**Interfaces:**
- Consumes: Tiptap `Editor` 实例。
- Produces: `FORMATTING_EXTENSIONS`（Extension 数组）、`FONT_SIZES: string[]`、`TEXT_COLORS: string[]`、`HIGHLIGHT_COLORS: string[]`、`clearFormatting(editor): void`。

- [ ] **Step 1: 安装依赖（一次装齐本期前端包）**

```bash
cd "E:/wsl/check/check-manage"
npm install --userconfig=.npmrc-local \
  @tiptap/extension-table @tiptap/extension-image \
  @tiptap/extension-text-style @tiptap/extension-highlight \
  @tiptap/extension-text-align mammoth docx
```

Expected: `package.json` 出现上述依赖，版本与 `@tiptap/core@^3.32.0` 兼容（text-style 使用 `./color`、`./font-size` 子路径）。

- [ ] **Step 2: 写失败测试**

```ts
// src/components/suggest-editor/core/__tests__/formatting.test.ts
import { describe, it, expect } from 'vitest'
import { Editor } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import {
  FORMATTING_EXTENSIONS, clearFormatting, FONT_SIZES,
} from '../formatting'

function makeEditor(content: string, withFormatting = true) {
  return new Editor({
    extensions: [StarterKit, ...(withFormatting ? FORMATTING_EXTENSIONS : [])],
    content,
  })
}

describe('formatting extensions', () => {
  it('字号/颜色/高亮/对齐可设置并持久化到 HTML', () => {
    const editor = makeEditor('<p>设备类型</p>')
    editor.chain().selectAll()
      .setFontSize('16pt')
      .setColor('#c0392b')
      .setHighlight({ color: '#fff3a3' })
      .setTextAlign('center')
      .run()
    const html = editor.getHTML()
    expect(html).toContain('font-size: 16pt')
    expect(html).toContain('color: #c0392b')
    expect(html).toContain('background-color: #fff3a3')
    expect(html).toContain('text-align: center')
    editor.destroy()
  })

  it('clearFormatting 清除 marks 与对齐', () => {
    const editor = makeEditor('<p>设备类型</p>')
    editor.chain().selectAll()
      .setFontSize('16pt').setColor('#c0392b')
      .setHighlight({ color: '#fff3a3' }).setTextAlign('center')
      .run()
    clearFormatting(editor)
    const html = editor.getHTML()
    expect(html).not.toContain('font-size')
    expect(html).not.toContain('color:')
    expect(html).not.toContain('background-color')
    expect(html).not.toContain('text-align')
    editor.destroy()
  })

  it('字号清单包含 12/14/16/18/20/24', () => {
    expect(FONT_SIZES).toEqual(['12pt', '14pt', '16pt', '18pt', '20pt', '24pt'])
  })
})
```

- [ ] **Step 3: 跑测试确认失败**

Run: `npx vitest run src/components/suggest-editor/core/__tests__/formatting.test.ts`
Expected: FAIL（找不到 `../formatting`）。

- [ ] **Step 4: 实现 formatting.ts**

```ts
// src/components/suggest-editor/core/formatting.ts
import { TextStyle } from '@tiptap/extension-text-style'
import { Color } from '@tiptap/extension-text-style/color'
import { FontSize } from '@tiptap/extension-text-style/font-size'
import { Highlight } from '@tiptap/extension-highlight'
import { TextAlign } from '@tiptap/extension-text-align'
import type { Editor } from '@tiptap/core'

export const FONT_SIZES = ['12pt', '14pt', '16pt', '18pt', '20pt', '24pt']
export const TEXT_COLORS = [
  '#1f2937', '#c0392b', '#d97706', '#047857',
  '#1d4ed8', '#7c3aed', '#be185d', '#6b7280',
]
export const HIGHLIGHT_COLORS = [
  '#fff3a3', '#d1fae5', '#dbeafe', '#fde68a', '#fce7f3', '#e5e7eb',
]

export const FORMATTING_EXTENSIONS = [
  TextStyle,
  Color,
  FontSize,
  Highlight.configure({ multicolor: true }),
  TextAlign.configure({ types: ['heading', 'paragraph'], alignments: ['left', 'center', 'right', 'justify'] }),
]

export function clearFormatting(editor: Editor): void {
  editor.chain().focus().unsetTextAlign().unsetAllMarks().clearNodes().run()
}
```

- [ ] **Step 5: 注册扩展并加工具栏控件**

在 `SuggestEditorCore.vue` 的 `extensions` 数组加 `...FORMATTING_EXTENSIONS`（放在 StarterKit 之后）。

在 `EditorToolbar.vue` 现有「清除格式」按钮之前加入：

```vue
<el-dropdown trigger="click" @command="(size: string) => applyFontSize(size)">
  <span class="se-toolbar-btn" title="字号">字号</span>
  <template #dropdown>
    <el-dropdown-menu>
      <el-dropdown-item command="">默认</el-dropdown-item>
      <el-dropdown-item v-for="size in FONT_SIZES" :key="size" :command="size">{{ size }}</el-dropdown-item>
    </el-dropdown-menu>
  </template>
</el-dropdown>
<el-dropdown trigger="click" @command="(color: string) => applyColor(color)">
  <span class="se-toolbar-btn" title="文字颜色">颜色</span>
  <template #dropdown>
    <el-dropdown-menu>
      <el-dropdown-item command="">默认</el-dropdown-item>
      <el-dropdown-item v-for="color in TEXT_COLORS" :key="color" :command="color">
        <span class="se-color-dot" :style="{ background: color }" />{{ color }}
      </el-dropdown-item>
    </el-dropdown-menu>
  </template>
</el-dropdown>
<el-dropdown trigger="click" @command="(color: string) => applyHighlight(color)">
  <span class="se-toolbar-btn" title="高亮">高亮</span>
  <template #dropdown>
    <el-dropdown-menu>
      <el-dropdown-item command="">清除</el-dropdown-item>
      <el-dropdown-item v-for="color in HIGHLIGHT_COLORS" :key="color" :command="color">
        <span class="se-color-dot" :style="{ background: color }" />{{ color }}
      </el-dropdown-item>
    </el-dropdown-menu>
  </template>
</el-dropdown>
<button v-for="align in ALIGNMENTS" :key="align.value" type="button"
  class="se-toolbar-btn" :class="{ 'is-active': editor.isActive({ textAlign: align.value }) }"
  :title="align.label" @click="applyAlign(align.value)">{{ align.icon }}</button>
```

脚本补充：

```ts
import { FONT_SIZES, TEXT_COLORS, HIGHLIGHT_COLORS, clearFormatting } from './core/formatting'
const ALIGNMENTS = [
  { value: 'left', label: '左对齐', icon: '⯇' },
  { value: 'center', label: '居中', icon: '≡' },
  { value: 'right', label: '右对齐', icon: '⯈' },
  { value: 'justify', label: '两端对齐', icon: '☰' },
]
function applyFontSize(size: string) {
  if (!size) editor.chain().focus().unsetFontSize().run()
  else editor.chain().focus().setFontSize(size).run()
}
function applyColor(color: string) {
  if (!color) editor.chain().focus().unsetColor().run()
  else editor.chain().focus().setColor(color).run()
}
function applyHighlight(color: string) {
  if (!color) editor.chain().focus().unsetHighlight().run()
  else editor.chain().focus().setHighlight({ color }).run()
}
function applyAlign(align: string) {
  editor.chain().focus().setTextAlign(align).run()
}
```

「清除格式」按钮改为调用 `clearFormatting(editor)`。

- [ ] **Step 6: 跑测试与类型检查**

Run:
```bash
npx vitest run src/components/suggest-editor/core/__tests__/formatting.test.ts
npx vue-tsc --noEmit
```
Expected: 3 passed；类型检查无输出。

- [ ] **Step 7: 提交**

```bash
git add package.json package-lock.json src/components/suggest-editor/core/formatting.ts \
  src/components/suggest-editor/core/__tests__/formatting.test.ts \
  src/components/suggest-editor/EditorToolbar.vue src/components/suggest-editor/SuggestEditorCore.vue
git commit -m "feat(suggest-editor): 对齐/字号/颜色/高亮排版能力"
```

---

### Task 2: 表格能力

**Files:**
- Create: `src/components/suggest-editor/core/table.ts`
- Modify: `src/components/suggest-editor/SuggestEditorCore.vue`
- Modify: `src/components/suggest-editor/core/slash-menu.ts`
- Modify: `src/components/suggest-editor/EditorToolbar.vue`
- Modify: `src/components/suggest-editor/editor.scss`
- Create: `src/components/suggest-editor/core/__tests__/table.test.ts`

**Interfaces:**
- Consumes: Task 1 的编辑器实例。
- Produces: `TABLE_EXTENSIONS`、`insertInitialTable(editor)`、`isInTable(editor)`、`TABLE_COMMANDS`（工具栏命令映射）。

- [ ] **Step 1: 写失败测试**

```ts
// src/components/suggest-editor/core/__tests__/table.test.ts
import { describe, it, expect } from 'vitest'
import { Editor } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import { TABLE_EXTENSIONS, insertInitialTable } from '../table'

function editor() {
  return new Editor({ extensions: [StarterKit, ...TABLE_EXTENSIONS], content: '<p>x</p>' })
}

describe('table support', () => {
  it('插入 3x3 带表头的表格', () => {
    const e = editor()
    insertInitialTable(e)
    const json = e.getJSON()
    const table = json.content?.find((n) => n.type === 'table')
    expect(table).toBeTruthy()
    expect(table?.content).toHaveLength(3)
    const firstRow = table?.content?.[0]
    expect(firstRow?.content?.[0]?.type).toBe('tableHeader')
    e.destroy()
  })

  it('增删行列命令生效', () => {
    const e = editor()
    insertInitialTable(e)
    e.chain().focus().addRowAfter().addColumnAfter().run()
    expect(e.getJSON().content?.find((n) => n.type === 'table')?.content).toHaveLength(4)
    e.chain().focus().deleteRow().deleteColumn().run()
    expect(e.getJSON().content?.find((n) => n.type === 'table')?.content).toHaveLength(3)
    e.destroy()
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run src/components/suggest-editor/core/__tests__/table.test.ts`
Expected: FAIL（找不到 `../table`）。

- [ ] **Step 3: 实现 table.ts**

```ts
// src/components/suggest-editor/core/table.ts
import { TableKit } from '@tiptap/extension-table'
import type { Editor } from '@tiptap/core'

export const TABLE_EXTENSIONS = [
  TableKit.configure({
    table: { resizable: true, lastColumnResizable: true },
  }),
]

export function insertInitialTable(editor: Editor): void {
  editor.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()
}

export const isInTable = (editor: Editor): boolean => editor.isActive('table')

export const TABLE_COMMANDS = {
  addRowBefore: (e: Editor) => e.chain().focus().addRowBefore().run(),
  addRowAfter: (e: Editor) => e.chain().focus().addRowAfter().run(),
  deleteRow: (e: Editor) => e.chain().focus().deleteRow().run(),
  addColumnBefore: (e: Editor) => e.chain().focus().addColumnBefore().run(),
  addColumnAfter: (e: Editor) => e.chain().focus().addColumnAfter().run(),
  deleteColumn: (e: Editor) => e.chain().focus().deleteColumn().run(),
  mergeCells: (e: Editor) => e.chain().focus().mergeCells().run(),
  splitCell: (e: Editor) => e.chain().focus().splitCell().run(),
  toggleHeaderRow: (e: Editor) => e.chain().focus().toggleHeaderRow().run(),
  deleteTable: (e: Editor) => e.chain().focus().deleteTable().run(),
}
```

- [ ] **Step 4: 注册扩展、斜杠菜单与表格工具栏**

1. `SuggestEditorCore.vue` extensions 加 `...TABLE_EXTENSIONS`。
2. `slash-menu.ts` 的 `BLOCK_ITEMS` 在「分割线」前加：

```ts
{
  title: '表格',
  hint: '3×3，带表头',
  keywords: 'table biaoge',
  badge: '▦',
  command: ({ editor, range }) =>
    editor.chain().focus().deleteRange(range).insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run(),
},
```

3. `EditorToolbar.vue` 在工具栏末尾加（仅表格内显示）：

```vue
<template v-if="editor.isActive('table')">
  <button class="se-toolbar-btn" title="上方插入行" @click="TABLE_COMMANDS.addRowBefore(editor)">↑行</button>
  <button class="se-toolbar-btn" title="下方插入行" @click="TABLE_COMMANDS.addRowAfter(editor)">↓行</button>
  <button class="se-toolbar-btn" title="删除行" @click="TABLE_COMMANDS.deleteRow(editor)">删行</button>
  <button class="se-toolbar-btn" title="左侧插入列" @click="TABLE_COMMANDS.addColumnBefore(editor)">←列</button>
  <button class="se-toolbar-btn" title="右侧插入列" @click="TABLE_COMMANDS.addColumnAfter(editor)">→列</button>
  <button class="se-toolbar-btn" title="删除列" @click="TABLE_COMMANDS.deleteColumn(editor)">删列</button>
  <button class="se-toolbar-btn" title="合并单元格" @click="TABLE_COMMANDS.mergeCells(editor)">合并</button>
  <button class="se-toolbar-btn" title="拆分单元格" @click="TABLE_COMMANDS.splitCell(editor)">拆分</button>
  <button class="se-toolbar-btn" title="切换表头行" @click="TABLE_COMMANDS.toggleHeaderRow(editor)">表头</button>
  <button class="se-toolbar-btn" title="删除表格" @click="TABLE_COMMANDS.deleteTable(editor)">删表</button>
</template>
```

4. `editor.scss` 加：

```scss
.doc-surface table { border-collapse: collapse; table-layout: fixed; width: 100%; margin: 12px 0; }
.doc-surface th, .doc-surface td { border: 1px solid var(--el-border-color); padding: 6px 8px; vertical-align: top; position: relative; }
.doc-surface th { background: var(--el-fill-color-light); font-weight: 600; text-align: left; }
.doc-surface .column-resize-handle { background: var(--el-color-primary); width: 2px; }
.doc-surface .selectedCell::after { background: rgba(64, 158, 255, .12); content: ''; inset: 0; position: absolute; pointer-events: none; }
```

- [ ] **Step 5: 跑测试与类型检查**

Run:
```bash
npx vitest run src/components/suggest-editor/core/__tests__/table.test.ts
npx vue-tsc --noEmit
```
Expected: 2 passed；类型检查无输出。

- [ ] **Step 6: 提交**

```bash
git add src/components/suggest-editor/core/table.ts \
  src/components/suggest-editor/core/__tests__/table.test.ts \
  src/components/suggest-editor/core/slash-menu.ts \
  src/components/suggest-editor/EditorToolbar.vue \
  src/components/suggest-editor/SuggestEditorCore.vue \
  src/components/suggest-editor/editor.scss
git commit -m "feat(suggest-editor): 表格插入与行列/合并操作"
```

---

### Task 3: 附件后端（迁移 + 存储 + 上传/读取 + 删除级联）

**Files:**
- Create: `server/migrations/2026_10_21_suggest_editor_attachments.py`
- Create: `server/utils/suggest_editor_attachments.py`
- Modify: `server/app.py`
- Modify: `server/routes/suggest_editor.py`
- Create: `server/tests/test_suggest_editor_attachments.py`

**Interfaces:**
- Produces:
  - `save_attachment(doc_id, owner_id, filename, data) -> dict`（含 `id/url/filename/mime/size/stored_path`）
  - `get_attachment(attachment_id) -> dict | None`
  - `delete_document_attachments(doc_id) -> None`
  - `attachment_dir(doc_id) -> Path`
  - HTTP：`POST /suggest-editor/documents/<doc_id>/attachments`、`GET /suggest-editor/attachments/<attachment_id>`。

- [ ] **Step 1: 写迁移**

```python
# server/migrations/2026_10_21_suggest_editor_attachments.py
# -*- coding: utf-8 -*-
"""幂等迁移：智能写作附件表 + 导入种子正文列。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db


def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS se_attachments ("
            " id TEXT PRIMARY KEY,"
            " document_id TEXT NOT NULL,"
            " owner_user_id TEXT NOT NULL,"
            " filename TEXT NOT NULL,"
            " mime VARCHAR(100) NOT NULL,"
            " size BIGINT NOT NULL,"
            " stored_path TEXT NOT NULL,"
            " created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_se_attachments_document "
                    "ON se_attachments (document_id)")
        cur.execute("ALTER TABLE se_documents ADD COLUMN IF NOT EXISTS seed_content_html TEXT")
        conn.commit()
    print("suggest editor attachments/seed migration ready.")


if __name__ == "__main__":
    run()
```

在 `server/app.py` 的 `2026_10_18_suggest_editor_tables.py` 注册块之后，按相同 try/except 形态注册新文件：

```python
try:
    _mp_se_att = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'migrations', '2026_10_21_suggest_editor_attachments.py')
    _spec_se_att = _ilu.spec_from_file_location('_suggest_editor_attachments_boot', _mp_se_att)
    _m_se_att = _ilu.module_from_spec(_spec_se_att)
    _spec_se_att.loader.exec_module(_m_se_att)
    _m_se_att.run()
except Exception as _e:
    logging.warning('suggest editor attachments migration on boot failed: %s', _e)
```

- [ ] **Step 2: 写失败测试**

```python
# server/tests/test_suggest_editor_attachments.py
# -*- coding: utf-8 -*-
import io, os, sys
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pytest
from app import app
from auth import create_token
from db import get_db

PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
       b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01'
       b'\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')


@pytest.fixture()
def att_env(tmp_path, monkeypatch):
    import config
    import routes.suggest_editor as se
    monkeypatch.setattr(config, 'SE_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(se, 'SE_DATA_DIR', str(tmp_path))
    suffix = os.urandom(4).hex()
    owner, other = f'att-owner-{suffix}', f'att-other-{suffix}'
    with get_db() as conn:
        with conn.cursor() as cur:
            for uid, role, name in ((owner, 'developer', 'atto-'), (other, 'developer', 'attx-')):
                cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                            "VALUES (%s,%s,'x',%s,%s)", (uid, name + suffix, uid, role))
            cur.execute("INSERT INTO se_documents (id, title, owner_user_id) VALUES (%s,%s,%s)",
                        (f'doc-{suffix}', '附件测试', owner))
        conn.commit()
    client = app.test_client()
    yield {
        'client': client,
        'owner': {'Authorization': f"Bearer {create_token({'id': owner, 'username': 'o', 'role': 'developer'})}"},
        'other': {'Authorization': f"Bearer {create_token({'id': other, 'username': 'x', 'role': 'developer'})}"},
        'doc_id': f'doc-{suffix}', 'tmp': str(tmp_path),
    }
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM se_attachments WHERE document_id = %s", (f'doc-{suffix}',))
            cur.execute("DELETE FROM se_documents WHERE id = %s", (f'doc-{suffix}',))
            cur.execute("DELETE FROM users WHERE id IN (%s,%s)", (owner, other))
        conn.commit()


def test_upload_and_read(att_env):
    c = att_env['client']
    r = c.post(f"/suggest-editor/documents/{att_env['doc_id']}/attachments",
               headers=att_env['owner'],
               data={'file': (io.BytesIO(PNG), '图.png')},
               content_type='multipart/form-data')
    assert r.status_code == 201
    body = r.get_json()['attachment']
    assert body['url'] == f"/suggest-editor/attachments/{body['id']}"
    got = c.get(body['url'], headers=att_env['owner'])
    assert got.status_code == 200 and got.data == PNG
    assert got.headers['Content-Type'].startswith('image/png')
    # 非属主 404
    assert c.get(body['url'], headers=att_env['other']).status_code == 404
    # 未登录 401
    assert c.get(body['url']).status_code == 401


def test_upload_rejects_non_image_and_oversize(att_env):
    c = att_env['client']
    url = f"/suggest-editor/documents/{att_env['doc_id']}/attachments"
    bad = c.post(url, headers=att_env['owner'],
                 data={'file': (io.BytesIO(b'not an image'), 'x.png')},
                 content_type='multipart/form-data')
    assert bad.status_code == 400
    big = c.post(url, headers=att_env['owner'],
                 data={'file': (io.BytesIO(b'\x89PNG\r\n\x1a\n' + b'x' * (10 * 1024 * 1024)), 'big.png')},
                 content_type='multipart/form-data')
    assert big.status_code == 400


def test_delete_document_cleans_attachment_dir(att_env):
    c = att_env['client']
    r = c.post(f"/suggest-editor/documents/{att_env['doc_id']}/attachments",
               headers=att_env['owner'],
               data={'file': (io.BytesIO(PNG), 'a.png')},
               content_type='multipart/form-data')
    att_id = r.get_json()['attachment']['id']
    assert Path(att_env['tmp'], 'attachments', att_env['doc_id']).is_dir()
    assert c.delete(f"/suggest-editor/documents/{att_env['doc_id']}",
                    headers=att_env['owner']).status_code == 200
    assert not Path(att_env['tmp'], 'attachments', att_env['doc_id']).exists()
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM se_attachments WHERE id = %s", (att_id,))
            assert cur.fetchone()[0] == 0
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd server && python -m pytest tests/test_suggest_editor_attachments.py -q`
Expected: FAIL（404，路由不存在）。

- [ ] **Step 4: 实现存储模块**

```python
# server/utils/suggest_editor_attachments.py
# -*- coding: utf-8 -*-
"""智能写作附件：图片 magic bytes、磁盘存储、DB 元数据。"""
import os
import uuid
from pathlib import Path
import config
from db import get_db

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
ALLOWED_EXT = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif', 'image/webp': '.webp'}


def detect_image_mime(data: bytes) -> str | None:
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    return None


def attachment_dir(doc_id: str) -> Path:
    root = Path(os.path.realpath(os.path.join(config.SE_DATA_DIR, 'attachments')))
    target = Path(os.path.realpath(root / doc_id))
    if target != root / doc_id or root not in target.parents:
        raise ValueError('invalid document id')
    return target


def save_attachment(doc_id: str, owner_id: str, filename: str, data: bytes) -> dict:
    if not data or len(data) > MAX_ATTACHMENT_BYTES:
        raise ValueError('图片大小不合法（上限 10MB）')
    mime = detect_image_mime(data)
    if mime not in ALLOWED_EXT:
        raise ValueError('仅支持 png/jpeg/gif/webp 图片')
    att_id = f'att_{uuid.uuid4().hex}'
    target_dir = attachment_dir(doc_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f'{att_id}{ALLOWED_EXT[mime]}'
    path.write_bytes(data)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO se_attachments (id, document_id, owner_user_id, filename, mime, size, stored_path) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (att_id, doc_id, owner_id, filename[:255], mime, len(data), str(path)))
        conn.commit()
    return {'id': att_id, 'url': f'/suggest-editor/attachments/{att_id}',
            'filename': filename, 'mime': mime, 'size': len(data), 'stored_path': str(path)}


def get_attachment(attachment_id: str) -> dict | None:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, document_id, owner_user_id, filename, mime, size, stored_path "
                        "FROM se_attachments WHERE id = %s", (attachment_id,))
            row = cur.fetchone()
    if not row:
        return None
    return dict(zip(('id', 'document_id', 'owner_user_id', 'filename', 'mime', 'size', 'stored_path'), row))


def delete_document_attachments(doc_id: str) -> None:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM se_attachments WHERE document_id = %s", (doc_id,))
        conn.commit()
    target = attachment_dir(doc_id)
    if target.exists():
        import shutil
        shutil.rmtree(target, ignore_errors=True)
```

- [ ] **Step 5: 加路由与删除级联**

在 `server/routes/suggest_editor.py` 顶部 import：

```python
from utils.suggest_editor_attachments import (
    save_attachment, get_attachment, delete_document_attachments,
)
```

在文档 CRUD 段落后加：

```python
@suggest_editor_bp.route("/documents/<doc_id>/attachments", methods=["POST"])
@write_required
def upload_document_attachment(doc_id: str):
    doc = _get_own_document(doc_id)
    if not doc:
        return jsonify({"error": "文档不存在"}), 404
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "缺少 file"}), 400
    data = file.read()
    try:
        attachment = save_attachment(doc_id, _user_id(), file.filename or "image", data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"attachment": {k: attachment[k] for k in ('id', 'url', 'filename', 'mime', 'size')}}), 201


@suggest_editor_bp.route("/attachments/<attachment_id>", methods=["GET"])
@login_required_sse
def serve_attachment(attachment_id: str):
    att = get_attachment(attachment_id)
    if not att or att["owner_user_id"] != _user_id():
        return jsonify({"error": "附件不存在"}), 404
    path = Path(att["stored_path"])
    if not path.is_file():
        return jsonify({"error": "附件文件缺失"}), 404
    resp = Response(path.read_bytes(), mimetype=att["mime"])
    resp.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    resp.headers["Content-Disposition"] = "inline"
    return resp
```

修改 `delete_my_document`：在 `delete_document(doc_id)` 之后加 `delete_document_attachments(doc_id)`。

- [ ] **Step 6: 跑测试**

Run:
```bash
cd server && python -m pytest tests/test_suggest_editor_attachments.py -q
python -m pytest tests/test_migration_boot_registration.py -q
```
Expected: 附件 3 例通过；迁移注册护栏通过。

- [ ] **Step 7: 提交**

```bash
git add server/migrations/2026_10_21_suggest_editor_attachments.py \
  server/utils/suggest_editor_attachments.py server/routes/suggest_editor.py \
  server/app.py server/tests/test_suggest_editor_attachments.py
git commit -m "feat(suggest-editor): 图片附件存储与属主鉴权端点"
```

---

### Task 4: 导入种子后端（seed 写入/消费 + 文档读取字段）

**Files:**
- Modify: `server/utils/suggest_editor_repo.py`
- Modify: `server/routes/suggest_editor.py`
- Create: `server/tests/test_suggest_editor_seed.py`

**Interfaces:**
- Produces:
  - `get_document` 返回 `seedContentHtml`；
  - `set_document_seed(doc_id, html) -> bool`（false = 已存在 state 或已有 seed）；
  - `clear_document_seed(doc_id) -> None`；
  - HTTP：`PUT /suggest-editor/documents/<id>/seed`、`POST /suggest-editor/documents/<id>/seed/applied`。

- [ ] **Step 1: 写失败测试**

```python
# server/tests/test_suggest_editor_seed.py
# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pytest
from app import app
from auth import create_token
from db import get_db


@pytest.fixture()
def seed_env():
    suffix = os.urandom(4).hex()
    owner, other = f'seed-owner-{suffix}', f'seed-other-{suffix}'
    doc_id = f'seed-doc-{suffix}'
    with get_db() as conn:
        with conn.cursor() as cur:
            for uid, name in ((owner, 'seedo-'), (other, 'seedx-')):
                cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                            "VALUES (%s,%s,'x',%s,'developer')", (uid, name + suffix, uid))
            cur.execute("INSERT INTO se_documents (id, title, owner_user_id) VALUES (%s,%s,%s)",
                        (doc_id, 'seed 测试', owner))
        conn.commit()
    yield {
        'client': app.test_client(),
        'owner': {'Authorization': f"Bearer {create_token({'id': owner, 'username': 'o', 'role': 'developer'})}"},
        'other': {'Authorization': f"Bearer {create_token({'id': other, 'username': 'x', 'role': 'developer'})}"},
        'doc_id': doc_id,
    }
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM se_document_states WHERE document_id = %s", (doc_id,))
            cur.execute("DELETE FROM se_documents WHERE id = %s", (doc_id,))
            cur.execute("DELETE FROM users WHERE id IN (%s,%s)", (owner, other))
        conn.commit()


def test_seed_write_read_and_apply(seed_env):
    c, h = seed_env['client'], seed_env['owner']
    doc_id = seed_env['doc_id']
    html = '<h1>导入标题</h1><p>正文</p>'
    assert c.put(f'/suggest-editor/documents/{doc_id}/seed', headers=h,
                 json={'contentHtml': html}).status_code == 200
    got = c.get(f'/suggest-editor/documents/{doc_id}', headers=h).get_json()['document']
    assert got['seedContentHtml'] == html
    # 列表不返回大字段
    listed = c.get('/suggest-editor/documents', headers=h).get_json()['documents']
    assert all('seedContentHtml' not in d for d in listed)
    assert c.post(f'/suggest-editor/documents/{doc_id}/seed/applied', headers=h).status_code == 200
    assert c.get(f'/suggest-editor/documents/{doc_id}', headers=h).get_json()['document']['seedContentHtml'] is None


def test_seed_rejects_non_owner_conflict_and_oversize(seed_env):
    c, h = seed_env['client'], seed_env['owner']
    doc_id = seed_env['doc_id']
    assert c.put(f'/suggest-editor/documents/{doc_id}/seed',
                 headers=seed_env['other'], json={'contentHtml': '<p>x</p>'}).status_code == 404
    assert c.put(f'/suggest-editor/documents/{doc_id}/seed', headers=h,
                 json={'contentHtml': 'x' * (5 * 1024 * 1024 + 1)}).status_code == 400
    assert c.put(f'/suggest-editor/documents/{doc_id}/seed', headers=h,
                 json={'contentHtml': '<p>x</p>'}).status_code == 200
    # 已有 seed：再次写入 409
    assert c.put(f'/suggest-editor/documents/{doc_id}/seed', headers=h,
                 json={'contentHtml': '<p>y</p>'}).status_code == 409
    # 已有 Yjs state：409
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO se_document_states (document_id, state) VALUES (%s,%s)",
                        (doc_id, b''))
        conn.commit()
    c.post(f'/suggest-editor/documents/{doc_id}/seed/applied', headers=h)
    assert c.put(f'/suggest-editor/documents/{doc_id}/seed', headers=h,
                 json={'contentHtml': '<p>z</p>'}).status_code == 409
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && python -m pytest tests/test_suggest_editor_seed.py -q`
Expected: FAIL（404/字段缺失）。

- [ ] **Step 3: 实现 repo 与路由**

`server/utils/suggest_editor_repo.py`：

```python
# get_document 的 SELECT 增加 seed_content_html
"SELECT id, title, owner_user_id, template_id, status, created_at, updated_at, "
"last_opened_at, seed_content_html FROM se_documents WHERE id = %s"

# _doc_row 增加（用 .get 兼容列表查询不选该列）
"seedContentHtml": row.get("seed_content_html"),


def set_document_seed(doc_id: str, content_html: str) -> bool:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE se_documents SET seed_content_html = %s "
                "WHERE id = %s AND seed_content_html IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM se_document_states WHERE document_id = %s)",
                (content_html, doc_id, doc_id))
            ok = cur.rowcount > 0
        conn.commit()
    return ok


def clear_document_seed(doc_id: str) -> None:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE se_documents SET seed_content_html = NULL WHERE id = %s", (doc_id,))
        conn.commit()
```

路由（放在附件路由附近）：

```python
MAX_SEED_HTML_BYTES = 5 * 1024 * 1024


@suggest_editor_bp.route("/documents/<doc_id>/seed", methods=["PUT"])
@write_required
def put_document_seed(doc_id: str):
    if not _get_own_document(doc_id):
        return jsonify({"error": "文档不存在"}), 404
    body = request.get_json(silent=True) or {}
    html = body.get("contentHtml")
    if not isinstance(html, str) or not html.strip():
        return jsonify({"error": "contentHtml 不能为空"}), 400
    if len(html.encode("utf-8")) > MAX_SEED_HTML_BYTES:
        return jsonify({"error": "contentHtml 超过 5MB"}), 400
    from utils.suggest_editor_repo import set_document_seed
    if not set_document_seed(doc_id, html):
        return jsonify({"error": "文档已开始编辑或已有种子内容"}), 409
    return jsonify({"ok": True})


@suggest_editor_bp.route("/documents/<doc_id>/seed/applied", methods=["POST"])
@write_required
def mark_document_seed_applied(doc_id: str):
    if not _get_own_document(doc_id):
        return jsonify({"error": "文档不存在"}), 404
    from utils.suggest_editor_repo import clear_document_seed
    clear_document_seed(doc_id)
    return jsonify({"ok": True})
```

- [ ] **Step 4: 跑测试**

Run:
```bash
cd server && python -m pytest tests/test_suggest_editor_seed.py -q
python -m pytest tests/test_suggest_editor_routes.py -q
```
Expected: seed 用例全过；既有文档路由回归通过（`_doc_row` 改动不破坏列表）。

- [ ] **Step 5: 提交**

```bash
git add server/utils/suggest_editor_repo.py server/routes/suggest_editor.py \
  server/tests/test_suggest_editor_seed.py
git commit -m "feat(suggest-editor): 导入种子正文的一次性存取"
```

---

### Task 5: 图片 figure 与附件前端（上传、粘贴/拖拽、鉴权渲染）

**Files:**
- Create: `src/components/suggest-editor/core/attachment-url.ts`
- Create: `src/components/suggest-editor/core/figure.ts`
- Create: `src/components/suggest-editor/core/__tests__/figure.test.ts`
- Modify: `src/api/suggestEditor.ts`
- Modify: `src/components/suggest-editor/SuggestEditorCore.vue`
- Modify: `src/components/suggest-editor/EditorToolbar.vue`
- Modify: `src/components/suggest-editor/core/slash-menu.ts`
- Modify: `src/components/suggest-editor/editor.scss`

**Interfaces:**
- Produces:
  - `attachmentSrc(canonical: string): string`（`/api` + 当前 token）
  - `attachmentFetchPath(canonical: string): string`（导出用，不带 token）
  - `FIGURE_EXTENSIONS`、`insertFigure(editor, {src, alt})`
  - `uploadAttachment(docId, file): Promise<SeAttachment>`
  - `SeAttachment` 类型。

- [ ] **Step 1: 写失败测试**

```ts
// src/components/suggest-editor/core/__tests__/figure.test.ts
import { describe, it, expect, beforeEach } from 'vitest'
import { Editor } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import { FIGURE_EXTENSIONS, insertFigure, attachmentSrc } from '../figure'

beforeEach(() => {
  localStorage.setItem('check-manage:token', JSON.stringify('tok-1'))
})

function editor() {
  return new Editor({ extensions: [StarterKit, ...FIGURE_EXTENSIONS], content: '<p>x</p>' })
}

describe('figure', () => {
  it('插入 figure 后 JSON 含 image + figureCaption', () => {
    const e = editor()
    insertFigure(e, { src: '/suggest-editor/attachments/att_1', alt: '拓扑' })
    const fig = e.getJSON().content?.find((n) => n.type === 'figure')
    expect(fig?.content?.[0]?.attrs?.src).toBe('/suggest-editor/attachments/att_1')
    expect(fig?.content?.[1]?.type).toBe('figureCaption')
    e.destroy()
  })

  it('attachmentSrc 拼 /api 与 access_token，非附件 URL 原样返回', () => {
    expect(attachmentSrc('/suggest-editor/attachments/att_1'))
      .toBe('/api/suggest-editor/attachments/att_1?access_token=tok-1')
    expect(attachmentSrc('https://example.com/a.png')).toBe('https://example.com/a.png')
  })

  it('图注可编辑并进入 HTML', () => {
    const e = editor()
    insertFigure(e, { src: '/suggest-editor/attachments/att_1', alt: '' })
    e.chain().focus('end').insertContent('图 1 拓扑').run()
    expect(e.getHTML()).toContain('<figcaption>图 1 拓扑</figcaption>')
    e.destroy()
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run src/components/suggest-editor/core/__tests__/figure.test.ts`
Expected: FAIL（找不到 `../figure`）。

- [ ] **Step 3: 实现 attachment-url.ts 与 figure.ts**

```ts
// src/components/suggest-editor/core/attachment-url.ts
import { authParam } from '@/utils/request'

const CANONICAL = '/suggest-editor/attachments/'

export function attachmentSrc(canonical: string): string {
  if (!canonical.startsWith(CANONICAL)) return canonical
  return `/api${canonical}${authParam()}`
}

export function attachmentFetchPath(canonical: string): string {
  return canonical.startsWith(CANONICAL) ? `/api${canonical}` : canonical
}
```

```ts
// src/components/suggest-editor/core/figure.ts
import { Node, mergeAttributes, type Editor } from '@tiptap/core'
import { Image } from '@tiptap/extension-image'
import { attachmentSrc } from './attachment-url'

export const FigureCaption = Node.create({
  name: 'figureCaption',
  content: 'inline*',
  group: 'block',
  parseHTML: () => [{ tag: 'figcaption' }],
  renderHTML: () => ['figcaption', 0],
})

const SeImage = Image.extend({
  addNodeView() {
    return ({ node }) => {
      const img = document.createElement('img')
      const apply = (attrs: Record<string, unknown>) => {
        img.src = attachmentSrc(String(attrs.src ?? ''))
        img.alt = String(attrs.alt ?? '')
        img.className = 'se-figure-img'
      }
      apply(node.attrs)
      return {
        dom: img,
        update: (updated) => {
          if (updated.type.name !== 'image') return false
          apply(updated.attrs)
          return true
        },
      }
    }
  },
})

export const Figure = Node.create({
  name: 'figure',
  group: 'block',
  content: 'image figureCaption',
  parseHTML: () => [{ tag: 'figure' }],
  renderHTML: ({ HTMLAttributes }) => ['figure', mergeAttributes(HTMLAttributes, { class: 'se-figure' }), 0],
})

export const FIGURE_EXTENSIONS = [
  SeImage.configure({ inline: false, allowBase64: false }),
  FigureCaption,
  Figure,
]

export function insertFigure(editor: Editor, attrs: { src: string; alt?: string }): void {
  editor.chain().focus().insertContent({
    type: 'figure',
    content: [
      { type: 'image', attrs: { src: attrs.src, alt: attrs.alt ?? '' } },
      { type: 'figureCaption' },
    ],
  }).run()
}
```

- [ ] **Step 4: 加 API、扩展与上传入口**

`src/api/suggestEditor.ts`：

```ts
export interface SeAttachment {
  id: string
  url: string
  filename: string
  mime: string
  size: number
}

export function uploadAttachment(docId: string, file: File) {
  const form = new FormData()
  form.append('file', file)
  return post<{ attachment: SeAttachment }>(
    `/suggest-editor/documents/${docId}/attachments`, form,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  )
}
```

`SuggestEditorCore.vue`：

```ts
import { FIGURE_EXTENSIONS, insertFigure } from './core/figure'
import { uploadAttachment } from '@/api/suggestEditor'

const extensions = [..., ...FIGURE_EXTENSIONS]

async function insertUploadedImages(files: File[]): Promise<void> {
  for (const file of files) {
    const { attachment } = await uploadAttachment(props.docId, file)
    insertFigure(editorRef.value!, { src: attachment.url, alt: file.name })
  }
}

editorProps: {
  attributes: { class: 'doc-surface' },
  handlePaste(_view, event) {
    const files = Array.from(event.clipboardData?.files ?? []).filter((f) => f.type.startsWith('image/'))
    if (!files.length) return false
    event.preventDefault()
    void insertUploadedImages(files)
    return true
  },
  handleDrop(_view, event, _slice, moved) {
    if (moved) return false
    const files = Array.from(event.dataTransfer?.files ?? []).filter((f) => f.type.startsWith('image/'))
    if (!files.length) return false
    event.preventDefault()
    void insertUploadedImages(files)
    return true
  },
}
```

`EditorToolbar.vue` 加按钮 + 隐藏 file input；在现有 `defineProps` 中增加 `docId: string`，并 `defineExpose({ openImagePicker })`：

```ts
const props = defineProps<{ editor: Editor; withSidebarPad?: boolean; docId: string }>()
const imageInput = ref<HTMLInputElement | null>(null)
function openImagePicker(): void { imageInput.value?.click() }
defineExpose({ openImagePicker })
```

```vue
<input ref="imageInput" type="file" accept="image/png,image/jpeg,image/gif,image/webp" style="display:none"
  @change="onImageChosen" />
<button type="button" class="se-toolbar-btn" title="插入图片" @click="openImagePicker">图片</button>
```

```ts
async function onImageChosen(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  const { uploadAttachment } = await import('@/api/suggestEditor')
  const { insertFigure } = await import('./core/figure')
  const { attachment } = await uploadAttachment(props.docId, file)
  insertFigure(props.editor, { src: attachment.url, alt: file.name })
}
```

`SuggestEditorCore.vue` 模板给工具栏传 `ref="toolbarRef"` 与 `:doc-id="docId"`：

```vue
<EditorToolbar v-if="editor" ref="toolbarRef" :editor="editor"
  :with-sidebar-pad="sidebarOpen" :doc-id="docId" />
```

Core 在编辑器就绪后监听斜杠菜单发出的插入事件：

```ts
const toolbarRef = ref<InstanceType<typeof EditorToolbar> | null>(null)
watch(editor, (value) => {
  if (!value) return
  value.view.dom.addEventListener('se:insert-image', () => toolbarRef.value?.openImagePicker())
}, { immediate: true })
```

斜杠菜单加「图片」项，command 只派发事件（文件选择与上传由工具栏/Core 统一处理）：

```ts
{
  title: '图片', hint: '上传本地图片', keywords: 'image tupian', badge: '🖼',
  command: ({ editor }) => editor.view.dom.dispatchEvent(new CustomEvent('se:insert-image', { bubbles: true })),
},
```

`editor.scss`：

```scss
.doc-surface figure.se-figure { margin: 14px 0; text-align: center; }
.doc-surface figure.se-figure img { max-width: 100%; height: auto; border-radius: 4px; }
.doc-surface figure.se-figure figcaption { margin-top: 6px; color: var(--el-text-color-secondary); font-size: 12px; }
.doc-surface figure.se-figure figcaption:empty::before { content: '图注（可选）'; color: var(--el-text-color-placeholder); }
```

- [ ] **Step 5: 跑测试与类型检查**

Run:
```bash
npx vitest run src/components/suggest-editor/core/__tests__/figure.test.ts
npx vue-tsc --noEmit
```
Expected: 3 passed；类型检查无输出。

- [ ] **Step 6: 提交**

```bash
git add src/components/suggest-editor/core/attachment-url.ts \
  src/components/suggest-editor/core/figure.ts \
  src/components/suggest-editor/core/__tests__/figure.test.ts \
  src/api/suggestEditor.ts src/components/suggest-editor/SuggestEditorCore.vue \
  src/components/suggest-editor/EditorToolbar.vue \
  src/components/suggest-editor/core/slash-menu.ts src/components/suggest-editor/editor.scss
git commit -m "feat(suggest-editor): 图片/图注节点与附件上传渲染"
```

---

### Task 6: docx 解析与 HTML 清洗（纯模块）

**Files:**
- Create: `src/components/suggest-editor/core/docx-import.ts`
- Create: `src/components/suggest-editor/core/__tests__/docx-import.test.ts`

**Interfaces:**
- Produces:
  - `parseDocx(file: File): Promise<ImportedDocx>`
  - `sanitizeImportedHtml(html: string): { html: string; warnings: string[] }`
  - `replaceImagePlaceholders(html: string, urlById: Map<string, string>): string`
  - `DocxImportError`（message 可直接展示）
  - `ImportedDocx { html, images: { id, blob, filename }[], warnings: string[], textLength, blockCount }`

- [ ] **Step 1: 写失败测试**

```ts
// src/components/suggest-editor/core/__tests__/docx-import.test.ts
import { describe, it, expect } from 'vitest'
import {
  sanitizeImportedHtml, replaceImagePlaceholders, DocxImportError, assertImportLimits,
} from '../docx-import'

describe('sanitizeImportedHtml', () => {
  it('移除 script/style/on*，保留允许标签', () => {
    const { html, warnings } = sanitizeImportedHtml(
      '<h1 onclick="x()">标题</h1><script>alert(1)</script><p style="color:red">正文</p>')
    expect(html).toContain('<h1>标题</h1>')
    expect(html).toContain('<p>正文</p>')
    expect(html).not.toContain('script')
    expect(html).not.toContain('onclick')
    expect(warnings.length).toBeGreaterThan(0)
  })

  it('img 转 figure + figcaption 占位并保留 src', () => {
    const { html } = sanitizeImportedHtml('<p><img src="se-img://img-1" alt="拓扑"></p>')
    expect(html).toContain('<figure')
    expect(html).toContain('src="se-img://img-1"')
    expect(html).toContain('<figcaption></figcaption>')
  })

  it('嵌套表格降级并告警', () => {
    const { html, warnings } = sanitizeImportedHtml(
      '<table><tr><td><table><tr><td>x</td></tr></table></td></tr></table>')
    expect(html.match(/<table/g)?.length).toBe(1)
    expect(warnings.join('')).toContain('嵌套表格')
  })

  it('替换图片占位符', () => {
    const html = '<figure><img src="se-img://img-1"><figcaption></figcaption></figure>'
    expect(replaceImagePlaceholders(html, new Map([['img-1', '/suggest-editor/attachments/att_1']])))
      .toContain('src="/suggest-editor/attachments/att_1"')
  })
})

describe('assertImportLimits', () => {
  it('文本/块数超限抛 DocxImportError', () => {
    expect(() => assertImportLimits(2 * 1024 * 1024 + 1, 10)).toThrow(DocxImportError)
    expect(() => assertImportLimits(100, 2001)).toThrow(DocxImportError)
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run src/components/suggest-editor/core/__tests__/docx-import.test.ts`
Expected: FAIL（找不到 `../docx-import`）。

- [ ] **Step 3: 实现 docx-import.ts**

```ts
// src/components/suggest-editor/core/docx-import.ts
export class DocxImportError extends Error {}

export interface ImportedImage { id: string; blob: Blob; filename: string }
export interface ImportedDocx {
  html: string
  images: ImportedImage[]
  warnings: string[]
  textLength: number
  blockCount: number
}

const ALLOWED_TAGS = new Set([
  'H1','H2','H3','H4','H5','H6','P','STRONG','B','EM','I','U','S','DEL','A','UL','OL','LI',
  'BLOCKQUOTE','PRE','CODE','BR','HR','TABLE','THEAD','TBODY','TR','TH','TD','FIGURE','FIGCAPTION','IMG',
])
const DROP_TAGS = new Set(['SCRIPT','STYLE','IFRAME','OBJECT','EMBED','LINK','META'])
const ALLOWED_ATTR = new Set(['href','src','alt','colspan','rowspan','title'])
export const MAX_TEXT = 2 * 1024 * 1024
export const MAX_BLOCKS = 2000
export const MAX_DOCX_BYTES = 20 * 1024 * 1024

export function assertImportLimits(textLength: number, blockCount: number): void {
  if (textLength > MAX_TEXT) throw new DocxImportError('文档文本超过 2MB 上限')
  if (blockCount > MAX_BLOCKS) throw new DocxImportError('文档块数超过 2000 上限')
}

function extFromMime(mime: string): string {
  return mime === 'image/jpeg' ? 'jpg' : mime.split('/')[1] || 'png'
}

export async function parseDocx(file: File): Promise<ImportedDocx> {
  if (!file.name.toLowerCase().endsWith('.docx')) throw new DocxImportError('仅支持 .docx 文件')
  if (file.size > MAX_DOCX_BYTES) throw new DocxImportError('docx 超过 20MB 上限')
  const mammoth = await import('mammoth')
  const images: ImportedImage[] = []
  let index = 0
  const result = await mammoth.convertToHtml(
    { arrayBuffer: await file.arrayBuffer() },
    {
      convertImage: mammoth.images.imgElement(async (image) => {
        const base64 = await image.read('base64')
        const id = `img-${++index}`
        const binary = atob(base64)
        const bytes = new Uint8Array(binary.length)
        for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
        images.push({
          id,
          blob: new Blob([bytes], { type: image.contentType }),
          filename: `${id}.${extFromMime(image.contentType)}`,
        })
        return { src: `se-img://${id}`, alt: (image as { altText?: string }).altText ?? '' }
      }),
    },
  )
  const sanitized = sanitizeImportedHtml(result.value)
  const warnings = [...result.messages.map((m) => m.message), ...sanitized.warnings]
  const doc = new DOMParser().parseFromString(sanitized.html, 'text/html')
  const textLength = (doc.body.textContent ?? '').length
  const blockCount = doc.body.querySelectorAll('h1,h2,h3,h4,h5,h6,p,li,tr,pre,blockquote').length
  assertImportLimits(textLength, blockCount)
  return { html: sanitized.html, images, warnings, textLength, blockCount }
}

export function sanitizeImportedHtml(html: string): { html: string; warnings: string[] } {
  const warnings: string[] = []
  const doc = new DOMParser().parseFromString(html, 'text/html')

  doc.querySelectorAll('table table').forEach((nested) => {
    const table = nested.closest('table')
    if (table === nested) return
    nested.remove()
    warnings.push('嵌套表格已降级为普通内容')
  })

  doc.body.querySelectorAll('img').forEach((img) => {
    const figure = doc.createElement('figure')
    figure.appendChild(img)
    const caption = doc.createElement('figcaption')
    figure.appendChild(caption)
    if (img.parentElement?.tagName === 'P' && img.parentElement.childElementCount === 1) {
      img.parentElement.replaceWith(figure)
    } else {
      img.replaceWith(figure)
    }
  })

  doc.body.querySelectorAll('*').forEach((el) => {
    if (DROP_TAGS.has(el.tagName)) { el.remove(); return }
    if (!ALLOWED_TAGS.has(el.tagName)) {
      const text = doc.createTextNode(el.textContent ?? '')
      el.replaceWith(text)
      warnings.push(`不支持的标签 ${el.tagName} 已降级为文本`)
      return
    }
    for (const attr of Array.from(el.attributes)) {
      if (!ALLOWED_ATTR.has(attr.name) || attr.name.startsWith('on')) el.removeAttribute(attr.name)
    }
  })
  return { html: doc.body.innerHTML, warnings: Array.from(new Set(warnings)) }
}

export function replaceImagePlaceholders(html: string, urlById: Map<string, string>): string {
  const doc = new DOMParser().parseFromString(html, 'text/html')
  doc.body.querySelectorAll('img').forEach((img) => {
    const src = img.getAttribute('src') ?? ''
    const id = src.startsWith('se-img://') ? src.slice('se-img://'.length) : ''
    const url = urlById.get(id)
    if (url) img.setAttribute('src', url)
  })
  return doc.body.innerHTML
}
```

- [ ] **Step 4: 跑测试**

Run: `npx vitest run src/components/suggest-editor/core/__tests__/docx-import.test.ts`
Expected: 5 passed。

- [ ] **Step 5: 提交**

```bash
git add src/components/suggest-editor/core/docx-import.ts \
  src/components/suggest-editor/core/__tests__/docx-import.test.ts
git commit -m "feat(suggest-editor): docx 解析与导入 HTML 清洗"
```

---

### Task 7: 导入 UI 与编排（创建 → 上传 → seed → 跳转/回滚）

**Files:**
- Create: `src/components/suggest-editor/ImportDocxDialog.vue`
- Modify: `src/views/suggest/SuggestEditorListView.vue`
- Modify: `src/api/suggestEditor.ts`
- Modify: `src/api/__tests__/suggestEditor.test.ts`

**Interfaces:**
- Consumes: Task 3 上传端点、Task 4 seed 端点、Task 6 `parseDocx`。
- Produces: `setDocumentSeed(docId, html)`、`markDocumentSeedApplied(docId)` 前端 API；`ImportDocxDialog` 的 `imported(docId)` 事件。

- [ ] **Step 1: 写失败测试（API 形状）**

在 `src/api/__tests__/suggestEditor.test.ts` 增加（同时把 `uploadAttachment`、`setDocumentSeed`、`markDocumentSeedApplied` 加入文件顶部的 import 列表）：

```ts
it('attachment/seed api 形状', async () => {
  const file = new File(['x'], 'a.png', { type: 'image/png' })
  await uploadAttachment('doc-1', file)
  const [url, form, cfg] = vi.mocked(post).mock.calls[0]
  expect(url).toBe('/suggest-editor/documents/doc-1/attachments')
  expect((form as FormData).get('file')).toBe(file)
  expect(cfg).toMatchObject({ headers: { 'Content-Type': 'multipart/form-data' } })

  await setDocumentSeed('doc-1', '<p>x</p>')
  expect(put).toHaveBeenCalledWith('/suggest-editor/documents/doc-1/seed', { contentHtml: '<p>x</p>' })
  await markDocumentSeedApplied('doc-1')
  expect(post).toHaveBeenCalledWith('/suggest-editor/documents/doc-1/seed/applied')
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run src/api/__tests__/suggestEditor.test.ts`
Expected: FAIL（函数未导出）。

- [ ] **Step 3: 加 API 与对话框**

`src/api/suggestEditor.ts`：

```ts
export function setDocumentSeed(docId: string, contentHtml: string) {
  return put<{ ok: boolean }>(`/suggest-editor/documents/${docId}/seed`, { contentHtml })
}
export function markDocumentSeedApplied(docId: string) {
  return post<{ ok: boolean }>(`/suggest-editor/documents/${docId}/seed/applied`)
}
```

`ImportDocxDialog.vue`（要点）：

```vue
<template>
  <el-dialog :model-value="visible" title="导入 Word" width="520px" :close-on-click-modal="false"
    @update:model-value="(v: boolean) => emit('update:visible', v)">
    <div class="import-body">
      <input ref="fileInput" type="file" accept=".docx" @change="onFile" />
      <p v-if="stage" class="import-stage">{{ stage }}</p>
      <p v-if="error" class="import-error">{{ error }}</p>
    </div>
    <template #footer>
      <el-button @click="emit('update:visible', false)">取消</el-button>
      <el-button type="primary" :loading="busy" :disabled="!file" @click="start">开始导入</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { parseDocx, replaceImagePlaceholders, DocxImportError } from './core/docx-import'
import { createDocument, deleteDocument, uploadAttachment, setDocumentSeed } from '@/api/suggestEditor'

defineProps<{ visible: boolean }>()
const emit = defineEmits<{ (e: 'update:visible', v: boolean): void; (e: 'imported', docId: string): void }>()

const fileInput = ref<HTMLInputElement | null>(null)
const file = ref<File | null>(null)
const busy = ref(false)
const stage = ref('')
const error = ref('')

function onFile(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0] ?? null
  error.value = ''
}

async function start(): Promise<void> {
  if (!file.value) return
  busy.value = true
  error.value = ''
  let createdId = ''
  try {
    stage.value = '解析文档…'
    const parsed = await parseDocx(file.value)
    stage.value = '创建文档…'
    const title = file.value.name.replace(/\.docx$/i, '').slice(0, 200) || '导入文档'
    const { document } = await createDocument({ title })
    createdId = document.id
    const urlById = new Map<string, string>()
    for (let i = 0; i < parsed.images.length; i += 1) {
      stage.value = `上传图片 ${i + 1}/${parsed.images.length}…`
      const image = parsed.images[i]
      const { attachment } = await uploadAttachment(document.id, new File([image.blob], image.filename, { type: image.blob.type }))
      urlById.set(image.id, attachment.url)
    }
    stage.value = '保存内容…'
    await setDocumentSeed(document.id, replaceImagePlaceholders(parsed.html, urlById))
    if (parsed.warnings.length) {
      ElMessage.warning(`导入完成，${parsed.warnings.length} 条提示（不支持的样式已降级）`)
    }
    emit('imported', document.id)
    emit('update:visible', false)
  } catch (e) {
    const msg = e instanceof DocxImportError
      ? e.message
      : (e as { response?: { data?: { error?: string } } }).response?.data?.error || '导入失败'
    error.value = msg
    if (createdId) await deleteDocument(createdId).catch(() => {})
  } finally {
    busy.value = false
    stage.value = ''
  }
}
</script>
```

列表页加按钮与对话框：

```vue
<el-button @click="importVisible = true">导入 Word</el-button>
<ImportDocxDialog v-model:visible="importVisible" @imported="(id: string) => router.push(`/suggest-editor/${id}`)" />
```

导入只创建当前登录用户自己的文档，后端仍校验属主与写权限，因此按钮对所有登录用户显示，不加 admin 条件。

- [ ] **Step 4: 跑测试与类型检查**

Run:
```bash
npx vitest run src/api/__tests__/suggestEditor.test.ts
npx vue-tsc --noEmit
```
Expected: API 测试全过；类型检查无输出。

- [ ] **Step 5: 提交**

```bash
git add src/components/suggest-editor/ImportDocxDialog.vue \
  src/views/suggest/SuggestEditorListView.vue src/api/suggestEditor.ts \
  src/api/__tests__/suggestEditor.test.ts
git commit -m "feat(suggest-editor): Word 导入 UI 与创建/上传/seed 编排"
```

---

### Task 8: DocView 应用种子 + skipInitialAnalysis 标记

**Files:**
- Create: `src/components/suggest-editor/core/seed.ts`
- Create: `src/components/suggest-editor/core/__tests__/seed.test.ts`
- Modify: `src/views/suggest/SuggestEditorDocView.vue`
- Modify: `src/types/suggestEditor.ts`（`SuggestDocument` 增加 `seedContentHtml`）

**Interfaces:**
- Produces: `applySeedIfEmpty(editor, ydoc, seedHtml): boolean`；`DOC_META_MAP = 'meta'`、`SKIP_INITIAL_ANALYSIS_KEY = 'skipInitialAnalysis'`。
- `SuggestDocument` 增加 `seedContentHtml?: string | null`。

- [ ] **Step 1: 写失败测试**

```ts
// src/components/suggest-editor/core/__tests__/seed.test.ts
import { describe, it, expect } from 'vitest'
import { Editor } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import * as Y from 'yjs'
import { applySeedIfEmpty, DOC_META_MAP, SKIP_INITIAL_ANALYSIS_KEY } from '../seed'

function make() {
  const ydoc = new Y.Doc()
  const editor = new Editor({ extensions: [StarterKit], content: '' })
  return { ydoc, editor }
}

describe('applySeedIfEmpty', () => {
  it('空正文应用 seed 并设置跳过首轮分析标记', () => {
    const { ydoc, editor } = make()
    expect(applySeedIfEmpty(editor, ydoc, '<h1>标题</h1><p>正文</p>')).toBe(true)
    expect(editor.getText()).toContain('标题')
    expect(ydoc.getMap(DOC_META_MAP).get(SKIP_INITIAL_ANALYSIS_KEY)).toBe(true)
    editor.destroy()
  })

  it('非空正文不覆盖，也不设置标记', () => {
    const { ydoc, editor } = make()
    editor.commands.setContent('<p>已有内容</p>')
    expect(applySeedIfEmpty(editor, ydoc, '<p>导入内容</p>')).toBe(false)
    expect(editor.getText()).toContain('已有内容')
    expect(ydoc.getMap(DOC_META_MAP).get(SKIP_INITIAL_ANALYSIS_KEY)).toBeUndefined()
    editor.destroy()
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run src/components/suggest-editor/core/__tests__/seed.test.ts`
Expected: FAIL（找不到 `../seed`）。

- [ ] **Step 3: 实现 seed.ts 与 DocView 接线**

```ts
// src/components/suggest-editor/core/seed.ts
import type { Editor } from '@tiptap/core'
import type * as Y from 'yjs'

export const DOC_META_MAP = 'meta'
export const SKIP_INITIAL_ANALYSIS_KEY = 'skipInitialAnalysis'

export function applySeedIfEmpty(editor: Editor, ydoc: Y.Doc, seedHtml: string): boolean {
  if (!seedHtml.trim()) return false
  if (ydoc.getXmlFragment('default').length > 0 || editor.getText().trim().length > 0) return false
  editor.commands.setContent(seedHtml)
  ydoc.getMap(DOC_META_MAP).set(SKIP_INITIAL_ANALYSIS_KEY, true)
  return true
}
```

`SuggestEditorDocView.vue`：

1. `doc` 类型增加 `seedContentHtml`；`getDocument` 响应直接使用。
2. `onSynced` 改为先处理 seed，再处理模板：

```ts
import { applySeedIfEmpty } from '@/components/suggest-editor/core/seed'
import { markDocumentSeedApplied } from '@/api/suggestEditor'

async function onSynced(): Promise<void> {
  const core = coreRef.value
  const ydoc = core?.ydoc
  const editor = core?.editor
  if (!ydoc || !editor) return
  const seed = doc.value?.seedContentHtml
  if (seed) {
    const applied = applySeedIfEmpty(editor, ydoc, seed)
    await markDocumentSeedApplied(docId).catch(() => {})
    if (applied) {
      templateApplied = true
      return
    }
  }
  if (templateApplied || !doc.value?.templateId) return
  // ...既有模板逻辑不变
}
```

- [ ] **Step 4: 跑测试与类型检查**

Run:
```bash
npx vitest run src/components/suggest-editor/core/__tests__/seed.test.ts
npx vue-tsc --noEmit
```
Expected: 2 passed；类型检查无输出。

- [ ] **Step 5: 提交**

```bash
git add src/components/suggest-editor/core/seed.ts \
  src/components/suggest-editor/core/__tests__/seed.test.ts \
  src/views/suggest/SuggestEditorDocView.vue src/types/suggestEditor.ts
git commit -m "feat(suggest-editor): 导入种子正文应用与首轮分析跳过标记"
```

---

### Task 9: flatten 表格与图注序列化

**Files:**
- Modify: `suggest-editor-server/src/flatten.ts`
- Create: `suggest-editor-server/tests/flatten.test.ts`
- Modify: `suggest-editor-server/package.json`

**Interfaces:**
- Consumes: Y.XmlFragment（table/tableRow/tableCell/tableHeader/figure/figcaption）。
- Produces: `flattenFragment` 输出 Markdown 表格文本与图注文本；segment 仍指向单元格/图注的 Y.XmlText。

- [ ] **Step 1: 加测试脚本与失败测试**

`package.json` scripts 增加：

```json
"test": "node --import tsx --test tests/flatten.test.ts tests/engine-dirty.test.ts"
```

```ts
// suggest-editor-server/tests/flatten.test.ts
import test from 'node:test'
import assert from 'node:assert/strict'
import * as Y from 'yjs'
import { flattenFragment } from '../src/flatten'

function tableFragment(cells: string[][]): Y.XmlFragment {
  const doc = new Y.Doc()
  const frag = doc.getXmlFragment('default')
  const table = new Y.XmlElement('table')
  cells.forEach((rowCells, rowIndex) => {
    const row = new Y.XmlElement('tableRow')
    rowCells.forEach((value) => {
      const cell = new Y.XmlElement(rowIndex === 0 ? 'tableHeader' : 'tableCell')
      const p = new Y.XmlElement('paragraph')
      const text = new Y.XmlText()
      text.insert(0, value)
      p.insert(0, [text])
      cell.insert(0, [p])
      row.insert(row.insert.length, [cell])
    })
    table.insert(table.length, [row])
  })
  frag.insert(0, [table])
  return frag
}

test('table serializes to markdown rows and keeps segments', () => {
  const flat = flattenFragment(tableFragment([['设备类型', '数量'], ['9800M12', '3']]))
  assert.match(flat.text, /\| 设备类型 \| 数量 \|/)
  assert.match(flat.text, /\| --- \| --- \|/)
  assert.match(flat.text, /\| 9800M12 \| 3 \|/)
  assert.equal(flat.blocks.length, 1)
  assert.ok(flat.segments.some((s) => s.yText.toString() === '9800M12'))
})

test('editing a cell changes the table block hash', () => {
  const a = flattenFragment(tableFragment([['设备类型'], ['9800M12']])).blocks[0].hash
  const b = flattenFragment(tableFragment([['设备类型'], ['5000M12']])).blocks[0].hash
  assert.notEqual(a, b)
})

test('figure serializes caption and ignores image-only figure text', () => {
  const doc = new Y.Doc()
  const frag = doc.getXmlFragment('default')
  const figure = new Y.XmlElement('figure')
  const image = new Y.XmlElement('image')
  const caption = new Y.XmlElement('figureCaption')
  const text = new Y.XmlText()
  text.insert(0, '图 1 拓扑')
  caption.insert(0, [text])
  figure.insert(0, [image, caption])
  frag.insert(0, [figure])
  const flat = flattenFragment(frag)
  assert.match(flat.text, /图 1 拓扑/)
  assert.equal(flat.blocks.length, 1)
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd suggest-editor-server && npm test`
Expected: FAIL（表格被粘连、无 `|` 分隔）。

- [ ] **Step 3: 实现 table/figure 分支**

在 `appendInline` 中处理 figure（只在块级调用时使用）；在 `appendBlock` 的 `switch` 增加：

```ts
case "table": {
  const rows = element.toArray().filter(
    (child): child is Y.XmlElement => child instanceof Y.XmlElement && child.nodeName === "tableRow",
  )
  rows.forEach((row, rowIndex) => {
    const cells = row.toArray().filter(
      (child): child is Y.XmlElement => child instanceof Y.XmlElement &&
        (child.nodeName === "tableCell" || child.nodeName === "tableHeader"),
    )
    appendRaw("| ")
    cells.forEach((cell, cellIndex) => {
      appendInline(cell)
      appendRaw(cellIndex === cells.length - 1 ? " |" : " | ")
    })
    appendRaw("\n")
    if (rowIndex === 0 && cells[0]?.nodeName === "tableHeader") {
      appendRaw("| " + cells.map(() => "---").join(" | ") + " |\n")
    }
  })
  endBlock()
  break
}
case "figure": {
  for (const child of element.toArray()) {
    if (!(child instanceof Y.XmlElement)) continue
    if (child.nodeName === "image") {
      const alt = child.getAttribute("alt")
      if (alt) appendRaw(`[图片] ${alt} `)
    } else if (child.nodeName === "figureCaption") {
      appendInline(child)
    }
  }
  endBlock()
  break
}
```

注意：`appendInline` 默认会递归进 `image`（无文本，无副作用）；单元格内多段落会直接拼接，需在每个 `paragraph` 之间补一个空格：

```ts
// appendInline 内处理 paragraph/tableCell 的递归时，在子段落结束时补空格
else if (child instanceof Y.XmlElement) {
  if (child.nodeName === "hardBreak") appendRaw("\n")
  else {
    appendInline(child)
    if (child.nodeName === "paragraph") appendRaw(" ")
  }
}
```

跑测试后若 Markdown 行尾出现多余空格，断言使用 `\|\s*设备类型\s*\|` 容错。

- [ ] **Step 4: 跑测试**

Run: `cd suggest-editor-server && npm test`
Expected: flatten 3 例通过。

- [ ] **Step 5: 提交**

```bash
git add suggest-editor-server/src/flatten.ts suggest-editor-server/tests/flatten.test.ts \
  suggest-editor-server/package.json
git commit -m "feat(suggest-editor): 分析快照支持表格与图注序列化"
```

---

### Task 10: 引擎导入基线 + 单轮分析上限 + prompt 约束

**Files:**
- Modify: `suggest-editor-server/src/engine.ts`
- Create: `suggest-editor-server/tests/engine-dirty.test.ts`
- Modify: `suggest-editor-server/src/flatten.ts`（如 Task 9 未覆盖 alt 处理，可在此补）

**Interfaces:**
- Produces:
  - `MAX_ANALYZED_HASHES = 5000`
  - `MAX_DIRTY_BLOCKS_PER_RUN = 40`
  - `takeDirtyBlocks(blocks, analyzedHashes, limit): BlockInfo[]`（导出供测试）
  - `meta` map 的 `skipInitialAnalysis` 消费逻辑。

- [ ] **Step 1: 写失败测试**

```ts
// suggest-editor-server/tests/engine-dirty.test.ts
import test from 'node:test'
import assert from 'node:assert/strict'
import { takeDirtyBlocks, MAX_DIRTY_BLOCKS_PER_RUN } from '../src/engine'

function block(i: number, analyzed = false) {
  return { start: i, end: i + 1, text: `p${i}`, hash: `h${i}`, analyzed }
}

test('dirty blocks are capped per run', () => {
  const blocks = Array.from({ length: 100 }, (_, i) => block(i))
  const dirty = takeDirtyBlocks(blocks, new Set(), MAX_DIRTY_BLOCKS_PER_RUN)
  assert.equal(dirty.length, MAX_DIRTY_BLOCKS_PER_RUN)
  assert.deepEqual(dirty.map((b) => b.text), blocks.slice(0, 40).map((b) => b.text))
})

test('already analyzed blocks are skipped before capping', () => {
  const blocks = Array.from({ length: 50 }, (_, i) => block(i))
  const analyzed = new Set(blocks.slice(0, 45).map((b) => b.hash))
  const dirty = takeDirtyBlocks(blocks, analyzed, 40)
  assert.deepEqual(dirty.map((b) => b.text), ['p45', 'p46', 'p47', 'p48', 'p49'])
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd suggest-editor-server && npm test`
Expected: FAIL（`takeDirtyBlocks` 未导出）。

- [ ] **Step 3: 实现基线、上限与 prompt**

`engine.ts`：

```ts
export const MAX_ANALYZED_HASHES = 5000
export const MAX_DIRTY_BLOCKS_PER_RUN = 40

export function takeDirtyBlocks(
  blocks: BlockInfo[],
  analyzedHashes: Set<string>,
  limit: number,
): BlockInfo[] {
  return blocks
    .filter((block) => block.text.trim().length > 0 && !analyzedHashes.has(block.hash))
    .slice(0, limit)
}
```

在 `run(state)` 计算 `flattened` 之后、`pruneSuggestions` 之前加：

```ts
const meta = state.ydoc.getMap("meta")
if (meta.get("skipInitialAnalysis") === true) {
  for (const block of flattened.blocks) {
    if (block.text.trim().length > 0) rememberHash(state, block.hash)
  }
  meta.delete("skipInitialAnalysis")
  setAssistantState(state.ydoc, {
    state: "idle",
    lastRunAt: Date.now(),
    lastAnalyzedBlocks: 0,
    lastSkippedBlocks: flattened.blocks.length,
    hint: "已建立导入基线，后续只分析改动段落",
    error: null,
  })
  return
}
```

将 dirty 计算替换为：

```ts
const allDirty = flattened.blocks.filter(
  (block) => block.text.trim().length > 0 && !state.analyzedHashes.has(block.hash),
)
const dirty = takeDirtyBlocks(flattened.blocks, state.analyzedHashes, MAX_DIRTY_BLOCKS_PER_RUN)
const skipped = flattened.blocks.length - dirty.length
const remaining = Math.max(allDirty.length - dirty.length, 0)
```

提示词 hint：

```ts
hint: remaining > 0
  ? `分析 ${dirty.length} 个未分析块（剩余 ${remaining} 个，稍后继续）`
  : `分析 ${dirty.length} 个变动段落（跳过 ${skipped} 个未变段落）`,
```

在成功收尾（`setAssistantState` 之后）加：

```ts
if (remaining > 0) scheduleAnalysis(state.name, 2000)
```

`buildPrompt` 第一段改为使用“块”，并追加：

```ts
"文档内容可能包含 Markdown 表格；请按行/单元格理解。引用只取单元格内或图注内的连续短句，"
"不要引用整行、跨单元格或跨图注；不要对图片内容本身提建议，只检查图注文字。"
```

把 `MAX_ANALYZED_HASHES` 的现有常量声明从 500 改为 5000。

- [ ] **Step 4: 跑测试**

Run:
```bash
cd suggest-editor-server && npm test
npm run typecheck
```
Expected: flatten 3 + engine 2 通过；tsc 无输出。

- [ ] **Step 5: 提交**

```bash
git add suggest-editor-server/src/engine.ts suggest-editor-server/tests/engine-dirty.test.ts
git commit -m "feat(suggest-editor): 导入分析基线、单轮块上限与表格引用约束"
```

---

### Task 11: docx 导出核心（段落/标题/列表/marks）

**Files:**
- Create: `src/components/suggest-editor/core/docx-export.ts`
- Create: `src/components/suggest-editor/core/__tests__/docx-export.test.ts`

**Interfaces:**
- Produces:
  - `exportDocx(json: JSONContent, options: { fetchImage: (canonical: string) => Promise<ArrayBuffer>; title: string }): Promise<Blob>`
  - `downloadDocx(blob, title): void`
- 后续 Task 12 在同一文件扩展表格/图片分支。

- [ ] **Step 1: 写失败测试**

```ts
// src/components/suggest-editor/core/__tests__/docx-export.test.ts
import { describe, it, expect } from 'vitest'
import { exportDocx } from '../docx-export'

const json = {
  type: 'doc',
  content: [
    { type: 'heading', attrs: { level: 1 }, content: [{ type: 'text', text: '方案标题' }] },
    { type: 'paragraph', content: [
      { type: 'text', text: '加粗', marks: [{ type: 'bold' }] },
      { type: 'text', text: '与普通' },
    ] },
    { type: 'bulletList', content: [
      { type: 'listItem', content: [{ type: 'paragraph', content: [{ type: 'text', text: '事项一' }] }] },
    ] },
  ],
}

describe('exportDocx core', () => {
  it('生成可被 mammoth 回读的 docx', async () => {
    const blob = await exportDocx(json, { title: '测试', fetchImage: async () => ({ data: new ArrayBuffer(0), type: 'image/png' }) })
    const mammoth = await import('mammoth')
    const { value } = await mammoth.extractRawText({ arrayBuffer: await blob.arrayBuffer() })
    expect(value).toContain('方案标题')
    expect(value).toContain('加粗')
    expect(value).toContain('事项一')
  })

  it('非法 URL 链接降级为文本，不抛错', async () => {
    const bad = { type: 'doc', content: [{ type: 'paragraph', content: [
      { type: 'text', text: '坏链接', marks: [{ type: 'link', attrs: { href: 'javascript:alert(1)' } }] },
    ] }] }
    const blob = await exportDocx(bad, { title: 'x', fetchImage: async () => ({ data: new ArrayBuffer(0), type: 'image/png' }) })
    expect(blob.size).toBeGreaterThan(0)
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run src/components/suggest-editor/core/__tests__/docx-export.test.ts`
Expected: FAIL（找不到 `../docx-export`）。

- [ ] **Step 3: 实现核心导出**

```ts
// src/components/suggest-editor/core/docx-export.ts
import {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType, ExternalHyperlink,
  type IRunOptions,
} from 'docx'
import type { JSONContent } from '@tiptap/core'

export interface ExportOptions {
  title: string
  fetchImage: (canonical: string) => Promise<{ data: ArrayBuffer; type: string }>
}

const HEADING = {
  1: HeadingLevel.HEADING_1, 2: HeadingLevel.HEADING_2, 3: HeadingLevel.HEADING_3,
  4: HeadingLevel.HEADING_4, 5: HeadingLevel.HEADING_5, 6: HeadingLevel.HEADING_6,
} as const

const ALIGN = { left: AlignmentType.LEFT, center: AlignmentType.CENTER,
  right: AlignmentType.RIGHT, justify: AlignmentType.JUSTIFIED } as const

function markRun(text: string, marks: JSONContent['marks'] = []): TextRun {
  const opts: IRunOptions = { text }
  for (const mark of marks) {
    switch (mark.type) {
      case 'bold': opts.bold = true; break
      case 'italic': opts.italics = true; break
      case 'underline': opts.underline = {}; break
      case 'strike': opts.strike = true; break
      case 'code': opts.font = 'Consolas'; opts.shading = { fill: 'F3F4F6' }; break
      case 'color': opts.color = String(mark.attrs?.color ?? '000000').replace('#', ''); break
      case 'highlight': opts.shading = { fill: String(mark.attrs?.color ?? '#fff3a3').replace('#', '') }; break
      case 'textStyle': {
        const size = String(mark.attrs?.fontSize ?? '')
        if (size) opts.size = Math.round(parseFloat(size) * 2)
        break
      }
      default: break
    }
  }
  return new TextRun(opts)
}

function inlineToRuns(nodes: JSONContent[] = []): Array<TextRun | ExternalHyperlink> {
  return nodes.flatMap((node) => {
    if (node.type === 'hardBreak') return [new TextRun({ break: 1 })]
    if (node.type !== 'text') return []
    const link = node.marks?.find((m) => m.type === 'link')
    const href = String(link?.attrs?.href ?? '')
    const safe = /^(https?:\/\/|mailto:)/i.test(href)
    const runs = [markRun(node.text ?? '', node.marks ?? [])]
    return safe && link ? [new ExternalHyperlink({ children: runs, link: href })] : runs
  })
}

function paragraphFrom(node: JSONContent): Paragraph {
  const align = node.attrs?.textAlign
  return new Paragraph({
    children: inlineToRuns(node.content),
    alignment: align && ALIGN[align as keyof typeof ALIGN] ? ALIGN[align as keyof typeof ALIGN] : undefined,
  })
}

function listParagraphs(item: JSONContent, bullet: boolean, level = 0): Paragraph[] {
  return (item.content ?? []).flatMap((child) => {
    if (child.type === 'paragraph') {
      return [new Paragraph({
        children: inlineToRuns(child.content),
        bullet: bullet ? { level } : undefined,
        numbering: bullet ? undefined : { reference: 'ordered', level },
      })]
    }
    return blockToParagraphs(child)
  })
}

function blockToParagraphs(node: JSONContent): Paragraph[] {
  switch (node.type) {
    case 'heading': return [new Paragraph({
      children: inlineToRuns(node.content),
      heading: HEADING[(node.attrs?.level ?? 1) as keyof typeof HEADING] ?? HeadingLevel.HEADING_1,
      alignment: ALIGN[(node.attrs?.textAlign ?? 'left') as keyof typeof ALIGN],
    })]
    case 'paragraph': return [paragraphFrom(node)]
    case 'bulletList': return (node.content ?? []).flatMap((item) => listParagraphs(item, true))
    case 'orderedList': return (node.content ?? []).flatMap((item) => listParagraphs(item, false))
    case 'taskList': return (node.content ?? []).flatMap((item) => {
      const checked = item.attrs?.checked === true
      return (item.content ?? []).flatMap((child) => {
        if (child.type === 'paragraph') {
          return [new Paragraph({
            children: [new TextRun(checked ? '☑ ' : '☐ '), ...inlineToRuns(child.content)],
            bullet: { level: 0 },
          })]
        }
        return blockToParagraphs(child)
      })
    })
    case 'blockquote': return (node.content ?? []).flatMap((child) => {
      if (child.type === 'paragraph') {
        return [new Paragraph({ children: inlineToRuns(child.content), indent: { left: 480 } })]
      }
      return blockToParagraphs(child)
    })
    case 'codeBlock': return [new Paragraph({
      children: [new TextRun({ text: (node.content ?? []).map((n) => n.text ?? '').join(''), font: 'Consolas' })],
      shading: { fill: 'F3F4F6' },
    })]
    case 'horizontalRule': return [new Paragraph({ border: { bottom: { style: 'single', size: 6, color: '999999' } } })]
    default: return []
  }
}

export async function exportDocx(json: JSONContent, options: ExportOptions): Promise<Blob> {
  const paragraphs = (json.content ?? []).flatMap(blockToParagraphs)
  const doc = new Document({
    numbering: { config: [{ reference: 'ordered', levels: [
      { level: 0, format: 'decimal', text: '%1.', alignment: AlignmentType.LEFT },
    ] }] },
    sections: [{ properties: { page: { size: { width: 11906, height: 16838 } } }, children: paragraphs }],
  })
  return Packer.toBlob(doc)
}

export function downloadDocx(blob: Blob, title: string): void {
  const safe = (title || '未命名文档').replace(/[\\/:*?"<>|]/g, '_')
  const date = new Date().toISOString().slice(0, 10).replace(/-/g, '')
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${safe}-${date}.docx`
  a.click()
  URL.revokeObjectURL(url)
}
```

- [ ] **Step 4: 跑测试**

Run: `npx vitest run src/components/suggest-editor/core/__tests__/docx-export.test.ts`
Expected: 2 passed。

- [ ] **Step 5: 提交**

```bash
git add src/components/suggest-editor/core/docx-export.ts \
  src/components/suggest-editor/core/__tests__/docx-export.test.ts
git commit -m "feat(suggest-editor): docx 导出核心（标题/段落/列表/marks）"
```

---

### Task 12: 导出表格/图片/图注 + 页面按钮

**Files:**
- Modify: `src/components/suggest-editor/core/docx-export.ts`
- Modify: `src/components/suggest-editor/core/__tests__/docx-export.test.ts`
- Modify: `src/views/suggest/SuggestEditorDocView.vue`

**Interfaces:**
- Consumes: Task 11 `exportDocx`；`attachmentFetchPath`（Task 5）。
- Produces: 完整导出路径 + `导出 Word` 按钮。

- [ ] **Step 1: 写失败测试**

在 `docx-export.test.ts` 增加：

```ts
it('表格/图片/图注进入导出文件', async () => {
  const png = Uint8Array.from(atob(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/lwJ2XwAAAABJRU5ErkJggg=='
  ), (c) => c.charCodeAt(0))
  const json = {
    type: 'doc',
    content: [
      { type: 'table', content: [
        { type: 'tableRow', content: [
          { type: 'tableHeader', content: [{ type: 'paragraph', content: [{ type: 'text', text: '设备' }] }] },
          { type: 'tableHeader', content: [{ type: 'paragraph', content: [{ type: 'text', text: '数量' }] }] },
        ] },
        { type: 'tableRow', content: [
          { type: 'tableCell', content: [{ type: 'paragraph', content: [{ type: 'text', text: '9800M12' }] }] },
          { type: 'tableCell', content: [{ type: 'paragraph', content: [{ type: 'text', text: '3' }] }] },
        ] },
      ] },
      { type: 'figure', content: [
        { type: 'image', attrs: { src: '/suggest-editor/attachments/att_1', alt: '拓扑' } },
        { type: 'figureCaption', content: [{ type: 'text', text: '图 1 拓扑' }] },
      ] },
    ],
  }
  const blob = await exportDocx(json as never, {
    title: 'x',
    fetchImage: async () => ({ data: png.buffer, type: 'image/png' }),
  })
  const mammoth = await import('mammoth')
  const html = (await mammoth.convertToHtml({ arrayBuffer: await blob.arrayBuffer() })).value
  expect(html).toContain('<table>')
  expect(html).toContain('9800M12')
  expect(html).toContain('图 1 拓扑')
  expect(html).toContain('<img')
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run src/components/suggest-editor/core/__tests__/docx-export.test.ts`
Expected: 表格/图片用例失败（table/figure 未映射）。

- [ ] **Step 3: 实现表格/图片映射**

在 `blockToParagraphs` 之前增加异步 block 映射（异步因为要拉图片）：

```ts
import { Table, TableRow, TableCell, WidthType, ImageRun, BorderStyle } from 'docx'
import { attachmentFetchPath } from './attachment-url'

async function blockToDocx(node: JSONContent, options: ExportOptions): Promise<Array<Paragraph | Table>> {
  if (node.type === 'table') {
    const rows = (node.content ?? []).map((row) => new TableRow({
      tableHeader: row.content?.[0]?.type === 'tableHeader',
      children: (row.content ?? []).map((cell) => new TableCell({
        columnSpan: cell.attrs?.colspan ? Number(cell.attrs.colspan) : undefined,
        rowSpan: cell.attrs?.rowspan ? Number(cell.attrs.rowspan) : undefined,
        children: (cell.content ?? []).flatMap(blockToParagraphs),
      })),
    }))
    return [new Table({
      rows,
      width: { size: 100, type: WidthType.PERCENTAGE },
      borders: {
        top: { style: BorderStyle.SINGLE, size: 4, color: 'BFBFBF' },
        bottom: { style: BorderStyle.SINGLE, size: 4, color: 'BFBFBF' },
        left: { style: BorderStyle.SINGLE, size: 4, color: 'BFBFBF' },
        right: { style: BorderStyle.SINGLE, size: 4, color: 'BFBFBF' },
        insideHorizontal: { style: BorderStyle.SINGLE, size: 4, color: 'BFBFBF' },
        insideVertical: { style: BorderStyle.SINGLE, size: 4, color: 'BFBFBF' },
      },
    })]
  }
  if (node.type === 'figure') {
    const imageNode = (node.content ?? []).find((n) => n.type === 'image')
    const captionNode = (node.content ?? []).find((n) => n.type === 'figureCaption')
    const src = String(imageNode?.attrs?.src ?? '')
    const parts: Array<Paragraph | Table> = []
    try {
      const { data, type } = await options.fetchImage(src)
      const imageType = type.includes('jpeg') ? 'jpg'
        : type.includes('gif') ? 'gif'
        : type.includes('bmp') ? 'bmp' : 'png'
      parts.push(new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new ImageRun({
          type: imageType as 'png' | 'jpg' | 'gif' | 'bmp',
          data,
          transformation: { width: 480, height: 270 },
        })],
      }))
    } catch {
      parts.push(new Paragraph({ children: [new TextRun(`[图片缺失: ${imageNode?.attrs?.alt ?? ''}]`)] }))
    }
    parts.push(new Paragraph({
      alignment: AlignmentType.CENTER,
      children: [new TextRun({ text: (captionNode?.content ?? []).map((n) => n.text ?? '').join(''), italics: true, size: 18 })],
    }))
    return parts
  }
  return blockToParagraphs(node)
}

export async function exportDocx(json: JSONContent, options: ExportOptions): Promise<Blob> {
  const children: Array<Paragraph | Table> = []
  for (const node of json.content ?? []) children.push(...await blockToDocx(node, options))
  const doc = new Document({
    numbering: { config: [{ reference: 'ordered', levels: [
      { level: 0, format: 'decimal', text: '%1.', alignment: AlignmentType.LEFT },
    ] }] },
    sections: [{ properties: { page: { size: { width: 11906, height: 16838 } } }, children }],
  })
  return Packer.toBlob(doc)
}
```

`SuggestEditorDocView.vue`：

```ts
import { exportDocx, downloadDocx } from '@/components/suggest-editor/core/docx-export'
import { attachmentFetchPath } from '@/components/suggest-editor/core/attachment-url'

const exporting = ref(false)
async function exportWord(): Promise<void> {
  const editor = coreRef.value?.editor
  if (!editor) return
  exporting.value = true
  try {
    const token = JSON.parse(localStorage.getItem('check-manage:token') || '""')
    const blob = await exportDocx(editor.getJSON(), {
      title: doc.value?.title ?? '未命名文档',
      fetchImage: async (canonical) => {
        const res = await fetch(attachmentFetchPath(canonical), {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (!res.ok) throw new Error('图片加载失败')
        return { data: await res.arrayBuffer(), type: res.headers.get('Content-Type') ?? 'image/png' }
      },
    })
    downloadDocx(blob, doc.value?.title ?? '未命名文档')
  } catch (e) {
    ElMessage.error((e as Error).message || '导出失败')
  } finally {
    exporting.value = false
  }
}
```

模板 `#actions` 加按钮：

```vue
<el-button size="small" :loading="exporting" :disabled="!canExport" @click="exportWord">导出 Word</el-button>
```

```ts
import { computed } from 'vue'
const canExport = computed(() => {
  const editor = coreRef.value?.editor
  return !!editor && !editor.isEmpty
})
```

- [ ] **Step 4: 跑测试与类型检查**

Run:
```bash
npx vitest run src/components/suggest-editor/core/__tests__/docx-export.test.ts
npx vue-tsc --noEmit
```
Expected: 3 passed；类型检查无输出。

- [ ] **Step 5: 提交**

```bash
git add src/components/suggest-editor/core/docx-export.ts \
  src/components/suggest-editor/core/__tests__/docx-export.test.ts \
  src/views/suggest/SuggestEditorDocView.vue
git commit -m "feat(suggest-editor): 导出表格/图片/图注并接入页面按钮"
```

---

### Task 13: E2E（导入/导出/表格规则/图片图注/排版）

**Files:**
- Create: `e2e/suggest-editor-word-parity.spec.ts`

**Interfaces:**
- Consumes: 前述全部能力；dev mock 栈。
- 复用 `suggest-editor-rules.spec.ts` 的登录/列表/建文档 helper 形态。

- [ ] **Step 1: 写用例骨架与 fixture 生成**

```ts
/**
 * 智能写作一期 E2E：表格规则、图片图注、排版、Word 导入/导出。
 * 前置：npm run dev:all（Flask 3002 + vite 5173 + se mock 1234）。
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { test, expect, type APIRequestContext, type Page } from '@playwright/test'
import {
  Document, Packer, Paragraph, HeadingLevel, Table, TableRow, TableCell, TextRun, ImageRun,
} from 'docx'

const AUTH_DIR = path.join(process.cwd(), 'e2e', '.auth')
const AUTH_FILE = path.join(AUTH_DIR, 'admin.json')
const AUTH_KEYS = ['check-manage:token', 'check-manage:userInfo']
const API_BASE = 'http://127.0.0.1:3002'
const PNG_B64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/lwJ2XwAAAABJRU5ErkJggg=='
const PNG_BUFFER = Buffer.from(PNG_B64, 'base64')

test.beforeAll(async ({ browser }) => {
  fs.mkdirSync(AUTH_DIR, { recursive: true })
  let auth: Record<string, string> | null = null
  for (let attempt = 0; attempt < 3 && !auth; attempt++) {
    const ctx = await browser.newContext()
    const page = await ctx.newPage()
    try {
      await page.goto('/')
      await page.fill('input[placeholder*="用户名"]', 'admin')
      await page.fill('input[placeholder*="密码"]', 'admin123')
      await page.getByRole('button', { name: /登\s*录/ }).click()
      await page.waitForFunction(
        () => !!localStorage.getItem('check-manage:token')
           && !!localStorage.getItem('check-manage:userInfo'),
        undefined, { timeout: 30_000 })
      await page.waitForTimeout(1500)
      auth = await page.evaluate((keys) => {
        const out: Record<string, string> = {}
        for (const k of keys) {
          const v = localStorage.getItem(k)
          if (v) out[k] = v
        }
        return out
      }, AUTH_KEYS)
    } catch { /* retry */ }
    await ctx.close()
  }
  if (!auth) throw new Error('shared login failed')
  fs.writeFileSync(AUTH_FILE, JSON.stringify(auth))
})

test.beforeEach(async ({ context }) => {
  const auth = JSON.parse(fs.readFileSync(AUTH_FILE, 'utf-8'))
  await context.addInitScript((entries) => {
    for (const [k, v] of Object.entries(entries)) localStorage.setItem(k, v)
  }, auth)
})

async function tokenOf(page: Page): Promise<string> {
  return page.evaluate(() =>
    JSON.parse(localStorage.getItem('check-manage:token') || '""')) as Promise<string>
}

function authHeaders(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
}

async function gotoSuggestList(page: Page): Promise<void> {
  await page.goto('/home')
  const menuItem = page.locator('.el-menu-item', { hasText: '智能写作' }).first()
  await menuItem.waitFor({ state: 'visible', timeout: 30_000 })
  await menuItem.click()
  await expect(page.getByRole('button', { name: '新建文档' })).toBeVisible({ timeout: 15_000 })
}

async function createBlankDoc(page: Page, title: string): Promise<void> {
  await page.getByRole('button', { name: '新建文档' }).click()
  const dialog = page.locator('.el-dialog', { hasText: '新建文档' })
  await dialog.waitFor({ state: 'visible' })
  await dialog.getByPlaceholder('未命名文档').fill(title)
  await dialog.getByRole('button', { name: '创建' }).click()
  await expect(page.locator('.doc-surface')).toBeVisible({ timeout: 20_000 })
  await expect(page.locator('.se-badge').first()).toHaveText('已同步', { timeout: 20_000 })
  await page.waitForTimeout(1200)
}

async function fetchGlobalRules(api: APIRequestContext, token: string): Promise<unknown[]> {
  const res = await api.get(`${API_BASE}/suggest-editor/rules/global`, {
    headers: authHeaders(token),
  })
  if (!res.ok()) return []
  return ((await res.json()) as { rules?: unknown[] }).rules ?? []
}

async function putGlobalRules(
  api: APIRequestContext, token: string, rules: unknown[],
): Promise<void> {
  const res = await api.put(`${API_BASE}/suggest-editor/rules/global`, {
    headers: authHeaders(token), data: { rules },
  })
  expect(res.ok(), `PUT global rules failed: ${res.status()} ${await res.text()}`).toBeTruthy()
}

async function deleteDocsByTitle(
  api: APIRequestContext, token: string, title: string,
): Promise<void> {
  const res = await api.get(`${API_BASE}/suggest-editor/documents`, {
    headers: authHeaders(token),
  })
  const body = await res.json() as { documents?: { id: string; title: string }[] }
  for (const doc of (body.documents ?? []).filter((d) => d.title === title)) {
    const deleted = await api.delete(`${API_BASE}/suggest-editor/documents/${doc.id}`, {
      headers: authHeaders(token),
    })
    expect(deleted.ok(), `DELETE document ${doc.id} failed: ${deleted.status()}`).toBeTruthy()
  }
}

async function makeFixtureDocx(filePath: string): Promise<void> {
  const doc = new Document({
    sections: [{ children: [
      new Paragraph({ text: '导入标题', heading: HeadingLevel.HEADING_1 }),
      new Paragraph({ children: [new TextRun('正文第一段')] }),
      new Paragraph({ text: '事项一', bullet: { level: 0 } }),
      new Table({ rows: [
        new TableRow({ children: [
          new TableCell({ children: [new Paragraph('设备')] }),
          new TableCell({ children: [new Paragraph('数量')] }),
        ] }),
        new TableRow({ children: [
          new TableCell({ children: [new Paragraph('9800M12')] }),
          new TableCell({ children: [new Paragraph('3')] }),
        ] }),
      ] }),
      new Paragraph({ children: [new ImageRun({
        type: 'png', data: PNG_BUFFER, transformation: { width: 60, height: 60 },
      })] }),
    ] }],
  })
  fs.writeFileSync(filePath, await Packer.toBuffer(doc))
}
```

- [ ] **Step 2: 表格 + 本地规则**

```ts
test('表格单元格内的本地规则实时命中', async ({ page, playwright }) => {
  test.setTimeout(180_000)
  const title = `E2E-表格规则-${Date.now()}`
  const api = await playwright.request.newContext()
  let token = ''
  let original: unknown[] = []
  let testFailure: unknown = null

  try {
    await page.goto('/home')
    token = await tokenOf(page)
    expect(token).toBeTruthy()
    original = await fetchGlobalRules(api, token)
    await putGlobalRules(api, token, [{
      id: 'e2e-wl-table',
      name: 'E2E设备类型白名单',
      enabled: true,
      severity: 'warn',
      whitelists: [{
        anchors: ['设备类型：', '设备类型:'],
        values: ['9800M12', '9600M12'],
        message: '设备类型只能是 9800M12 或 9600M12',
      }],
    }])

    await gotoSuggestList(page)
    await createBlankDoc(page, title)
    await page.locator('.doc-surface').click()
    await page.keyboard.type('/')
    await page.keyboard.type('biaoge')
    const menu = page.locator('.se-slash-menu')
    await expect(menu).toBeVisible({ timeout: 10_000 })
    await menu.locator('.slash-item', { hasText: '表格' }).click()
    await expect(page.locator('.doc-surface table')).toBeVisible({ timeout: 10_000 })

    await page.locator('.doc-surface th').first().click()
    await page.keyboard.type('设备类型：9800M12；5000M12', { delay: 20 })
    const card = page.locator('.se-suggest-note', { hasText: '设备类型只能是 9800M12 或 9600M12' })
    await expect(card).toBeVisible({ timeout: 10_000 })
    await expect(card.locator('.se-suggest-note-quote')).toContainText('5000M12')

    await page.reload()
    await expect(page.locator('.doc-surface table')).toBeVisible({ timeout: 20_000 })
    await expect(page.locator('.doc-surface th').first()).toContainText('9800M12')
  } catch (error) {
    testFailure = error
  } finally {
    const cleanupErrors: string[] = []
    try {
      await putGlobalRules(api, token, original)
    } catch (error) {
      cleanupErrors.push(`恢复全局规则失败：${String(error)}`)
    }
    try {
      await deleteDocsByTitle(api, token, title)
    } catch (error) {
      cleanupErrors.push(`删除文档「${title}」失败：${String(error)}`)
    }
    await api.dispose()
    if (cleanupErrors.length) throw new Error(`清理未完成：\n- ${cleanupErrors.join('\n- ')}`)
    if (testFailure) throw testFailure
  }
})
```

- [ ] **Step 3: 图片 + 图注 + 排版**

```ts
test('图片图注与排版持久化', async ({ page, playwright }) => {
  test.setTimeout(180_000)
  const title = `E2E-图片排版-${Date.now()}`
  const api = await playwright.request.newContext()
  let token = ''
  let testFailure: unknown = null

  try {
    await page.goto('/home')
    token = await tokenOf(page)
    await gotoSuggestList(page)
    await createBlankDoc(page, title)

    // 先排版一段文字（避免图片插入后的光标位置不确定）
    await page.locator('.doc-surface').click()
    await page.keyboard.type('排版测试')
    await page.keyboard.press('Home')
    await page.keyboard.press('Shift+End')
    await page.locator('[title="字号"]').click()
    await page.locator('.el-dropdown-menu__item:visible', { hasText: '16pt' }).click()
    await page.locator('[title="文字颜色"]').click()
    await page.locator('.el-dropdown-menu__item:visible', { hasText: '#c0392b' }).click()
    await page.locator('[title="高亮"]').click()
    await page.locator('.el-dropdown-menu__item:visible', { hasText: '#fff3a3' }).click()
    await page.locator('[title="居中"]').click()

    // 上传图片
    const chooserPromise = page.waitForEvent('filechooser')
    await page.locator('[title="插入图片"]').click()
    const chooser = await chooserPromise
    await chooser.setFiles({ name: 'dot.png', mimeType: 'image/png', buffer: PNG_BUFFER })
    const img = page.locator('.doc-surface figure img').first()
    await expect(img).toBeVisible({ timeout: 15_000 })
    await expect.poll(
      () => img.evaluate((el) => (el as HTMLImageElement).naturalWidth),
    ).toBeGreaterThan(0)

    const caption = page.locator('.doc-surface figcaption').first()
    await caption.click()
    await page.keyboard.type('图注：9800M12')
    await expect(caption).toContainText('图注：9800M12')

    const html = await page.locator('.doc-surface').innerHTML()
    expect(html).toContain('font-size: 16pt')
    expect(html).toContain('color: #c0392b')
    expect(html).toContain('#fff3a3')
    expect(html).toContain('text-align: center')

    await page.reload()
    await expect(page.locator('.doc-surface figure img').first()).toBeVisible({ timeout: 20_000 })
    await expect(page.locator('.doc-surface figcaption').first()).toContainText('图注：9800M12')
    const restored = await page.locator('.doc-surface').innerHTML()
    expect(restored).toContain('font-size: 16pt')
    expect(restored).toContain('text-align: center')
  } catch (error) {
    testFailure = error
  } finally {
    const cleanupErrors: string[] = []
    try {
      await deleteDocsByTitle(api, token, title)
    } catch (error) {
      cleanupErrors.push(`删除文档「${title}」失败：${String(error)}`)
    }
    await api.dispose()
    if (cleanupErrors.length) throw new Error(`清理未完成：\n- ${cleanupErrors.join('\n- ')}`)
    if (testFailure) throw testFailure
  }
})
```

- [ ] **Step 4: 导入（含基线断言）与导出**

```ts
test('Word 导入、首轮基线与导出', async ({ page, playwright }) => {
  test.setTimeout(240_000)
  const title = `E2E-Word导入-${Date.now()}`
  const fixture = path.join(os.tmpdir(), `${title}.docx`)
  const exported = path.join(os.tmpdir(), `${title}-out.docx`)
  const api = await playwright.request.newContext()
  let token = ''
  let testFailure: unknown = null

  try {
    await makeFixtureDocx(fixture)
    await page.goto('/home')
    token = await tokenOf(page)
    await gotoSuggestList(page)

    await page.getByRole('button', { name: '导入 Word' }).click()
    const dialog = page.locator('.el-dialog').filter({
      has: page.locator('.el-dialog__title', { hasText: '导入 Word' }),
    })
    await dialog.waitFor({ state: 'visible' })
    await dialog.locator('input[type="file"]').setInputFiles(fixture)
    await dialog.getByRole('button', { name: '开始导入' }).click()

    await page.waitForURL(/\/suggest-editor\/[0-9a-f-]+/, { timeout: 120_000 })
    await expect(page.locator('.doc-surface h1')).toHaveText('导入标题', { timeout: 60_000 })
    await expect(page.locator('.doc-surface table')).toContainText('9800M12')
    const img = page.locator('.doc-surface figure img').first()
    await expect(img).toBeVisible({ timeout: 20_000 })
    await expect.poll(
      () => img.evaluate((el) => (el as HTMLImageElement).naturalWidth),
    ).toBeGreaterThan(0)

    // 导入后首轮只建基线（不调模型）；之后再编辑 1 个块，只分析这 1 个块
    await expect(page.locator('.se-status')).toContainText('已建立导入基线', { timeout: 60_000 })
    await page.locator('.doc-surface').click()
    await page.keyboard.press('Control+End')
    await page.keyboard.type('X')
    await expect(page.locator('.se-status')).toContainText(/分析 1 个变动段落/, { timeout: 60_000 })

    const downloadPromise = page.waitForEvent('download')
    await page.getByRole('button', { name: '导出 Word' }).click()
    const download = await downloadPromise
    await download.saveAs(exported)

    const mammothModule = await import('mammoth') as unknown as {
      convertToHtml?: (input: { path: string }) => Promise<{ value: string }>
      default?: { convertToHtml: (input: { path: string }) => Promise<{ value: string }> }
    }
    const convert = mammothModule.convertToHtml ?? mammothModule.default?.convertToHtml
    if (!convert) throw new Error('mammoth convertToHtml unavailable')
    const { value } = await convert({ path: exported })
    expect(value).toContain('导入标题')
    expect(value).toContain('9800M12')
    expect(value).toContain('正文第一段')
  } catch (error) {
    testFailure = error
  } finally {
    const cleanupErrors: string[] = []
    try {
      await deleteDocsByTitle(api, token, title)
    } catch (error) {
      cleanupErrors.push(`删除文档「${title}」失败：${String(error)}`)
    }
    fs.rmSync(fixture, { force: true })
    fs.rmSync(exported, { force: true })
    await api.dispose()
    if (cleanupErrors.length) throw new Error(`清理未完成：\n- ${cleanupErrors.join('\n- ')}`)
    if (testFailure) throw testFailure
  }
})
```

- [ ] **Step 5: 运行 E2E**

Run:
```bash
npm run dev:all   # 另开终端（已运行则跳过）
npx playwright test e2e/suggest-editor-word-parity.spec.ts --reporter=line
```
Expected: 3 passed。首次失败时按截图/追踪修选择器或时序，不放宽断言。

- [ ] **Step 6: 提交**

```bash
git add e2e/suggest-editor-word-parity.spec.ts
git commit -m "test(suggest-editor): 表格/图片/排版/Word 导入导出 E2E"
```

---

### Task 14: 文档、全量回归、构建与发布说明

**Files:**
- Modify: `docs/user-guide/ai/suggest-editor.md`
- Modify: `CLAUDE.md`
- Modify: `docs/superpowers/specs/2026-10-21-suggest-editor-word-parity-phase1-design.md`（如有实现偏差，回写）
- Build: `dist/`

**Interfaces:**
- 无新代码接口；交付可部署状态。

- [ ] **Step 1: 更新用户指南**

补充分节：
- 表格、图片/图注、排版能力与限制；
- 「导入 Word」流程、支持范围、限制（20MB/200 图/2000 块）、失败回滚；
- 「导出 Word」映射范围与非目标（无页眉页脚/目录/分页）；
- AI 边界：表格/图注可查，图片内容与格式类要求不可查；导入后只检查改动块；
- 附件属主私有与删除文档清理；
- 发布：迁移随启动、Node 重启、前端 build、旧缓存必须 Ctrl+F5（旧 schema 会丢新节点）。

- [ ] **Step 2: 更新 CLAUDE.md**

在智能写作段落补充：TableKit/figure 节点、附件表与 canonical URL、seed 机制、flatten 表格、`MAX_DIRTY_BLOCKS_PER_RUN`、导入导出边界与测试入口。

- [ ] **Step 3: 全量回归**

Run:
```bash
cd server && python -m pytest tests/ -q -k "suggest_editor or migration_boot"
cd .. && npx vitest run
npx vue-tsc --noEmit
cd suggest-editor-server && npm test && npm run typecheck
cd .. && npm run build
```

Expected: 后端 SE 相关全绿；vitest 全绿；类型检查无输出；Node 测试全绿；构建成功。

- [ ] **Step 4: 既有 E2E 回归**

Run（dev mock 栈）:
```bash
npx playwright test e2e/suggest-editor.spec.ts e2e/suggest-editor-layout.spec.ts \
  e2e/suggest-editor-rules.spec.ts e2e/suggest-editor-snippets.spec.ts \
  e2e/suggest-editor-word-parity.spec.ts --reporter=line
```
Expected: 全部通过（worker=1）。

- [ ] **Step 5: 手工验证**

- 用 Word/LibreOffice 打开导出的 docx，确认标题/表格/图片/图注可读。
- 导入一份真实技术方案，记录 mammoth warning，确认无崩溃、无半成品文档。
- 打开旧文档确认原有内容/规则/建议不受影响。

- [ ] **Step 6: 提交**

```bash
git add docs/user-guide/ai/suggest-editor.md CLAUDE.md \
  docs/superpowers/specs/2026-10-21-suggest-editor-word-parity-phase1-design.md
git commit -m "docs(suggest-editor): 一期表格/图片/Word 能力与发布说明"
```

---

## 计划自检

- **Spec 覆盖**：附件/seed API（Task 3/4）、图片 figure（Task 5）、导入（Task 6/7/8）、表格与排版（Task 1/2）、AI flatten/预算（Task 9/10）、导出（Task 11/12）、测试（各任务 + Task 13）、文档与发布（Task 14）。
- **无占位符**：每个实现步骤给出文件、签名与关键代码；重复性分支（列表/表格单元格）在任务内已列出。
- **类型一致性**：`SeAttachment`、`seedContentHtml`、`DOC_META_MAP/SKIP_INITIAL_ANALYSIS_KEY`、`attachmentSrc/attachmentFetchPath`、`takeDirtyBlocks` 在任务间名称一致。
- **已知实现注意**：Tiptap 3.32 的 `TableKit`/`Highlight`/`TextAlign` 使用命名导出；若安装版本导出形态不同，以 `node_modules/*/dist/index.d.ts` 为准并保持签名不变。
