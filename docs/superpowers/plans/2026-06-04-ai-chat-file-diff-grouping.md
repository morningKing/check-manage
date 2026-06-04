# AI Chat 变更文件 Diff 视图 + 分组折叠 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 AI 助手「变更文件」面板把修改文件用并排 diff（仅 git hunk）呈现、新增大文件用轻量截断查看器，并按 新增/修改/删除 分组折叠（删除默认折叠）。

**Architecture:** 后端新增 `file_diff()` + path→repo 解析，暴露 `GET /ai/chat/sessions/:id/diff?path=`（modified → unified diff，added → 截断内容，含 path-traversal 校验）。前端新增 `parseUnifiedDiff` 工具 + `FileDiffView.vue` 并排渲染，`AiChatView.vue` 抽屉按状态分流、面板按状态分组折叠。

**Tech Stack:** Python Flask + psycopg2 (后端)，Vue 3 + TypeScript + Element Plus (前端)，pytest + vitest (测试)。无新第三方库。

---

## File Structure

- `server/utils/workspace_changes.py` — 重构 repo 发现为可复用；新增 `resolve_repo_for_path()` 与 `file_diff()`。
- `server/routes/ai_chat.py` — 新增 `GET /sessions/<sid>/diff` 路由（紧邻现有 `list_changes`）。
- `server/tests/test_workspace_changes.py` — `resolve_repo_for_path` / `file_diff` 用例。
- `src/utils/unifiedDiff.ts` — `parseUnifiedDiff()` 纯函数 + 类型。
- `src/utils/__tests__/unifiedDiff.test.ts` — 解析/对齐单测。
- `src/api/aiChat.ts` — `getFileDiff()` + `FileDiff` 类型。
- `src/components/ai-chat/FileDiffView.vue` — 并排 diff + 截断查看器。
- `src/components/ai-chat/__tests__/FileDiffView.test.ts` — 渲染单测。
- `src/views/ai-chat/AiChatView.vue` — 分组折叠 + 抽屉接入 diff/查看器。

---

## Task 1: 后端 — repo 路径解析 `resolve_repo_for_path`

**Files:**
- Modify: `server/utils/workspace_changes.py`
- Test: `server/tests/test_workspace_changes.py`

- [ ] **Step 1: Write the failing test**

追加到 `server/tests/test_workspace_changes.py` 末尾：

```python
def test_resolve_repo_for_path_nested_clone(tmp_path):
    from utils.workspace_changes import resolve_repo_for_path
    ws = str(tmp_path)
    _init_repo(ws)                       # workspace itself a repo
    nested = os.path.join(ws, 'cloned-repo')
    _init_repo(nested)
    with open(os.path.join(nested, 'file.py'), 'w') as f:
        f.write('print(1)')
    repo, repo_rel = resolve_repo_for_path(ws, 'cloned-repo/file.py')
    assert os.path.realpath(repo) == os.path.realpath(nested)
    assert repo_rel == 'file.py'


def test_resolve_repo_for_path_workspace_root(tmp_path):
    from utils.workspace_changes import resolve_repo_for_path
    ws = str(tmp_path)
    _init_repo(ws)
    with open(os.path.join(ws, 'loose.txt'), 'w') as f:
        f.write('hi')
    repo, repo_rel = resolve_repo_for_path(ws, 'loose.txt')
    assert os.path.realpath(repo) == os.path.realpath(ws)
    assert repo_rel == 'loose.txt'


def test_resolve_repo_for_path_no_repo_returns_none(tmp_path):
    from utils.workspace_changes import resolve_repo_for_path
    ws = str(tmp_path)
    with open(os.path.join(ws, 'loose.txt'), 'w') as f:
        f.write('hi')
    assert resolve_repo_for_path(ws, 'loose.txt') == (None, None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_workspace_changes.py::test_resolve_repo_for_path_nested_clone -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_repo_for_path'`

- [ ] **Step 3: Write minimal implementation**

在 `server/utils/workspace_changes.py` 的 `_find_git_repos` 之后新增：

```python
def resolve_repo_for_path(workspace_path, rel_path):
    """Map a workspace-relative path to (repo_dir, repo_rel_path).

    Picks the deepest git repo (longest path) that contains the file, so a
    nested clone wins over the workspace-root repo. Returns (None, None) when
    no repo contains the path."""
    abs_target = os.path.realpath(os.path.join(workspace_path, rel_path))
    best = None
    for repo in _find_git_repos(workspace_path):
        repo_real = os.path.realpath(repo)
        prefix = repo_real + os.sep
        if abs_target == repo_real or abs_target.startswith(prefix):
            if best is None or len(repo_real) > len(os.path.realpath(best)):
                best = repo
    if best is None:
        return None, None
    repo_rel = os.path.relpath(abs_target, os.path.realpath(best)).replace(os.sep, '/')
    return best, repo_rel
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_workspace_changes.py -v`
Expected: PASS (all, including the 3 new cases)

- [ ] **Step 5: Commit**

```bash
git add server/utils/workspace_changes.py server/tests/test_workspace_changes.py
git commit -m "feat(ai-chat): resolve_repo_for_path maps workspace path to git repo"
```

---

## Task 2: 后端 — `file_diff()`

**Files:**
- Modify: `server/utils/workspace_changes.py`
- Test: `server/tests/test_workspace_changes.py`

- [ ] **Step 1: Write the failing test**

追加到 `server/tests/test_workspace_changes.py`：

```python
def test_file_diff_modified_returns_hunks(tmp_path):
    from utils.workspace_changes import file_diff
    ws = str(tmp_path)
    repo = os.path.join(ws, 'repo')
    _init_repo(repo)
    with open(os.path.join(repo, 'a.txt'), 'w') as f:
        f.write('line1\nline2\nline3\n')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-q', '-m', 'base')
    with open(os.path.join(repo, 'a.txt'), 'w') as f:
        f.write('line1\nCHANGED\nline3\n')
    res = file_diff(ws, 'repo/a.txt')
    assert res['status'] == 'modified'
    assert '@@' in res['diff']
    assert '-line2' in res['diff']
    assert '+CHANGED' in res['diff']
    assert res['truncated'] is False


def test_file_diff_added_returns_content(tmp_path):
    from utils.workspace_changes import file_diff
    ws = str(tmp_path)
    repo = os.path.join(ws, 'repo')
    _init_repo(repo)
    with open(os.path.join(repo, 'new.txt'), 'w') as f:
        f.write('fresh content\n')
    res = file_diff(ws, 'repo/new.txt')
    assert res['status'] == 'added'
    assert res['content'] == 'fresh content\n'
    assert res['truncated'] is False


def test_file_diff_added_truncates_large_file(tmp_path):
    from utils.workspace_changes import file_diff, MAX_DIFF_LINES
    ws = str(tmp_path)
    repo = os.path.join(ws, 'repo')
    _init_repo(repo)
    big = '\n'.join(f'line{i}' for i in range(MAX_DIFF_LINES + 50)) + '\n'
    with open(os.path.join(repo, 'big.txt'), 'w') as f:
        f.write(big)
    res = file_diff(ws, 'repo/big.txt')
    assert res['status'] == 'added'
    assert res['truncated'] is True
    assert res['content'].count('\n') <= MAX_DIFF_LINES


def test_file_diff_no_repo_returns_none_status(tmp_path):
    from utils.workspace_changes import file_diff
    ws = str(tmp_path)
    with open(os.path.join(ws, 'loose.txt'), 'w') as f:
        f.write('hi')
    res = file_diff(ws, 'loose.txt')
    assert res['status'] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_workspace_changes.py::test_file_diff_modified_returns_hunks -v`
Expected: FAIL with `ImportError: cannot import name 'file_diff'`

- [ ] **Step 3: Write minimal implementation**

在 `server/utils/workspace_changes.py` 顶部常量区追加（在 `MAX_CHANGES = 500` 旁）：

```python
MAX_DIFF_LINES = 2000      # cap added-file content & diff text by lines
MAX_DIFF_BYTES = 256 * 1024
```

在文件末尾追加：

```python
def _classify(repo, repo_rel):
    """Return 'added'|'modified'|'deleted'|None for a repo-relative path."""
    try:
        out = subprocess.run(
            ['git', '-C', repo, 'status', '--porcelain', '-z', '--', repo_rel],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:
        return None
    if out.returncode != 0 or not out.stdout:
        return None
    xy = out.stdout.split('\0')[0][:2]
    return _map_status(xy)


def _cap(text):
    """Truncate text to the line/byte caps; return (text, truncated)."""
    truncated = False
    if len(text.encode('utf-8', 'replace')) > MAX_DIFF_BYTES:
        text = text.encode('utf-8', 'replace')[:MAX_DIFF_BYTES].decode('utf-8', 'ignore')
        truncated = True
    lines = text.split('\n')
    if len(lines) > MAX_DIFF_LINES:
        text = '\n'.join(lines[:MAX_DIFF_LINES])
        truncated = True
    return text, truncated


def file_diff(workspace_path, rel_path):
    """Return {status, diff?|content?, truncated} for a single changed file.

    modified -> unified `git diff` (hunks only); added -> capped file content;
    deleted/unknown -> status only. No repo found -> status None."""
    repo, repo_rel = resolve_repo_for_path(workspace_path, rel_path)
    if repo is None:
        return {'status': None, 'truncated': False}
    status = _classify(repo, repo_rel)
    if status == 'modified':
        try:
            out = subprocess.run(
                ['git', '-C', repo, 'diff', '--', repo_rel],
                capture_output=True, text=True, timeout=20,
            )
            diff = out.stdout if out.returncode == 0 else ''
        except Exception:
            diff = ''
        diff, truncated = _cap(diff)
        return {'status': 'modified', 'diff': diff, 'truncated': truncated}
    if status == 'added':
        try:
            with open(os.path.join(repo, repo_rel), 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
        except Exception:
            content = ''
        content, truncated = _cap(content)
        return {'status': 'added', 'content': content, 'truncated': truncated}
    return {'status': status, 'truncated': False}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_workspace_changes.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add server/utils/workspace_changes.py server/tests/test_workspace_changes.py
git commit -m "feat(ai-chat): file_diff returns capped git diff / added-file content"
```

---

## Task 3: 后端 — `/diff` 路由

**Files:**
- Modify: `server/routes/ai_chat.py` (import + 新路由紧邻 `list_changes`，约 `:580` 之后)
- Test: `server/tests/test_routes_ai_chat.py` (沿用其现有 fixture 风格；若该文件已 mock session/workspace，复用同一辅助)

- [ ] **Step 1: Write the failing test**

先确认 `server/tests/test_routes_ai_chat.py` 现有的 client + 会话 fixture 命名，沿用之，新增一个使用真实 tmp workspace 的用例。追加：

```python
def test_diff_endpoint_modified(client, auth_headers, make_session_with_workspace):
    # make_session_with_workspace: 现有辅助，建一个 session 行并返回 (sid, ws_path)
    import os, subprocess
    sid, ws = make_session_with_workspace()
    repo = os.path.join(ws, 'repo')
    os.makedirs(repo, exist_ok=True)
    for a in (['init', '-q'], ['config', 'user.email', 't@t'], ['config', 'user.name', 't']):
        subprocess.run(['git', '-C', repo, *a], check=True, capture_output=True)
    with open(os.path.join(repo, 'a.txt'), 'w') as f:
        f.write('one\ntwo\n')
    subprocess.run(['git', '-C', repo, 'add', '.'], check=True, capture_output=True)
    subprocess.run(['git', '-C', repo, 'commit', '-q', '-m', 'b'], check=True, capture_output=True)
    with open(os.path.join(repo, 'a.txt'), 'w') as f:
        f.write('one\nTWO\n')
    r = client.get(f'/ai/chat/sessions/{sid}/diff?path=repo/a.txt', headers=auth_headers)
    assert r.status_code == 200
    body = r.get_json()
    assert body['status'] == 'modified'
    assert '@@' in body['diff']


def test_diff_endpoint_rejects_traversal(client, auth_headers, make_session_with_workspace):
    sid, ws = make_session_with_workspace()
    r = client.get(f'/ai/chat/sessions/{sid}/diff?path=../../etc/passwd', headers=auth_headers)
    assert r.status_code == 400
```

> 注：若 `test_routes_ai_chat.py` 没有 `make_session_with_workspace`/`auth_headers` 这类 fixture，按该文件已有的 session-mock 模式改写这两个用例（保持断言不变：modified 返回 `@@`、穿越返回 400）。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_ai_chat.py -k diff -v`
Expected: FAIL (404 — 路由不存在)

- [ ] **Step 3: Write minimal implementation**

`server/routes/ai_chat.py`：把 import 行 `from utils.workspace_changes import git_changes` 改为：

```python
from utils.workspace_changes import git_changes, file_diff
```

在 `list_changes`（`:580` 后）紧接着新增：

```python
@ai_chat_bp.route('/sessions/<sid>/diff', methods=['GET'])
@login_required
def file_diff_endpoint(sid):
    """Return a single changed file's diff (modified) or capped content (added).
    Path is validated against the workspace root to block traversal."""
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    rel = request.args.get('path', '')
    try:
        safe_resolve(sess[4], rel)  # raises on traversal; result unused
    except Exception:
        return jsonify({'error': 'bad path', 'code': 'BAD_PATH'}), 400
    return jsonify(file_diff(sess[4], rel))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_ai_chat.py -k diff -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/routes/ai_chat.py server/tests/test_routes_ai_chat.py
git commit -m "feat(ai-chat): GET /sessions/:id/diff endpoint with path guard"
```

---

## Task 4: 前端 — `parseUnifiedDiff` 工具

**Files:**
- Create: `src/utils/unifiedDiff.ts`
- Test: `src/utils/__tests__/unifiedDiff.test.ts`

- [ ] **Step 1: Write the failing test**

Create `src/utils/__tests__/unifiedDiff.test.ts`:

```typescript
import { describe, it, expect } from 'vitest'
import { parseUnifiedDiff } from '../unifiedDiff'

const DIFF = `diff --git a/a.txt b/a.txt
index 111..222 100644
--- a/a.txt
+++ b/a.txt
@@ -1,3 +1,3 @@
 line1
-line2
+CHANGED
 line3
`

describe('parseUnifiedDiff', () => {
  it('aligns context, removal and addition into side-by-side rows', () => {
    const hunks = parseUnifiedDiff(DIFF)
    expect(hunks).toHaveLength(1)
    const rows = hunks[0].rows
    // context line1
    expect(rows[0]).toEqual({ type: 'context', left: 'line1', right: 'line1', leftNo: 1, rightNo: 1 })
    // removal paired with addition on the same row
    expect(rows[1]).toEqual({ type: 'change', left: 'line2', right: 'CHANGED', leftNo: 2, rightNo: 2 })
    // context line3
    expect(rows[2]).toEqual({ type: 'context', left: 'line3', right: 'line3', leftNo: 3, rightNo: 3 })
  })

  it('pads unbalanced add/remove runs with blank fillers', () => {
    const diff = `--- a/x
+++ b/x
@@ -1,1 +1,2 @@
-old
+new1
+new2
`
    const rows = parseUnifiedDiff(diff)[0].rows
    expect(rows[0]).toEqual({ type: 'change', left: 'old', right: 'new1', leftNo: 1, rightNo: 1 })
    expect(rows[1]).toEqual({ type: 'add', left: null, right: 'new2', leftNo: null, rightNo: 2 })
  })

  it('returns empty array for empty diff', () => {
    expect(parseUnifiedDiff('')).toEqual([])
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/utils/__tests__/unifiedDiff.test.ts`
Expected: FAIL (cannot find module `../unifiedDiff`)

- [ ] **Step 3: Write minimal implementation**

Create `src/utils/unifiedDiff.ts`:

```typescript
export type DiffRowType = 'context' | 'add' | 'del' | 'change'

export interface DiffRow {
  type: DiffRowType
  left: string | null
  right: string | null
  leftNo: number | null
  rightNo: number | null
}

export interface DiffHunk {
  header: string
  rows: DiffRow[]
}

const HUNK_RE = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/

/**
 * Parse git unified diff text into side-by-side hunks. Removal (`-`) and
 * addition (`+`) runs are paired row-by-row; the shorter side gets blank
 * fillers. Context lines appear on both sides.
 */
export function parseUnifiedDiff(diff: string): DiffHunk[] {
  if (!diff.trim()) return []
  const lines = diff.split('\n')
  const hunks: DiffHunk[] = []
  let cur: DiffHunk | null = null
  let leftNo = 0
  let rightNo = 0
  let dels: string[] = []
  let adds: string[] = []

  const flush = () => {
    if (!cur) return
    const n = Math.max(dels.length, adds.length)
    for (let i = 0; i < n; i++) {
      const l = i < dels.length ? dels[i] : null
      const r = i < adds.length ? adds[i] : null
      if (l !== null && r !== null) {
        cur.rows.push({ type: 'change', left: l, right: r, leftNo: ++leftNo, rightNo: ++rightNo })
      } else if (l !== null) {
        cur.rows.push({ type: 'del', left: l, right: null, leftNo: ++leftNo, rightNo: null })
      } else if (r !== null) {
        cur.rows.push({ type: 'add', left: null, right: r, leftNo: null, rightNo: ++rightNo })
      }
    }
    dels = []
    adds = []
  }

  for (const line of lines) {
    const m = HUNK_RE.exec(line)
    if (m) {
      flush()
      cur = { header: line, rows: [] }
      hunks.push(cur)
      leftNo = parseInt(m[1], 10) - 1
      rightNo = parseInt(m[2], 10) - 1
      continue
    }
    if (!cur) continue // skip the diff/index/--- /+++ preamble
    if (line.startsWith('\\')) continue // "\ No newline at end of file"
    if (line.startsWith('-')) { dels.push(line.slice(1)); continue }
    if (line.startsWith('+')) { adds.push(line.slice(1)); continue }
    // context (leading space) or blank line inside a hunk
    flush()
    const text = line.startsWith(' ') ? line.slice(1) : line
    cur.rows.push({ type: 'context', left: text, right: text, leftNo: ++leftNo, rightNo: ++rightNo })
  }
  flush()
  return hunks
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run src/utils/__tests__/unifiedDiff.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/utils/unifiedDiff.ts src/utils/__tests__/unifiedDiff.test.ts
git commit -m "feat(ai-chat): parseUnifiedDiff aligns git diff into side-by-side rows"
```

---

## Task 5: 前端 — `getFileDiff` API

**Files:**
- Modify: `src/api/aiChat.ts` (在 `getChanges` 之后，约 `:133`)

- [ ] **Step 1: Add the type + function**

在 `ChangedFile` 接口附近新增类型，并在 `getChanges` 之后新增函数：

```typescript
export interface FileDiff {
  status: 'added' | 'modified' | 'deleted' | null
  diff?: string
  content?: string
  truncated: boolean
}

export function getFileDiff(id: string, path: string) {
  return get<FileDiff>(
    `/ai/chat/sessions/${encodeURIComponent(id)}/diff`,
    { path },
    { silent: true },
  )
}
```

> 确认 `get` 的第二参是 query 对象（与本文件其他调用一致）；若签名不同，按本文件既有的 query 传参方式改写。

- [ ] **Step 2: Type-check**

Run: `npx vue-tsc --noEmit`
Expected: 无新增错误（仅检查本文件能编译；项目既有错误不在本任务范围）。

- [ ] **Step 3: Commit**

```bash
git add src/api/aiChat.ts
git commit -m "feat(ai-chat): getFileDiff API client + FileDiff type"
```

---

## Task 6: 前端 — `FileDiffView.vue`

**Files:**
- Create: `src/components/ai-chat/FileDiffView.vue`
- Test: `src/components/ai-chat/__tests__/FileDiffView.test.ts`

- [ ] **Step 1: Write the failing test**

Create `src/components/ai-chat/__tests__/FileDiffView.test.ts`:

```typescript
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import FileDiffView from '../FileDiffView.vue'

const DIFF = `--- a/a.txt
+++ b/a.txt
@@ -1,3 +1,3 @@
 line1
-line2
+CHANGED
 line3
`

describe('FileDiffView', () => {
  it('renders side-by-side change rows for a modified diff', () => {
    const w = mount(FileDiffView, { props: { status: 'modified', diff: DIFF, truncated: false } })
    expect(w.findAll('.diff-row').length).toBe(3)
    expect(w.find('.diff-cell--del').text()).toContain('line2')
    expect(w.find('.diff-cell--add').text()).toContain('CHANGED')
  })

  it('renders added-file content in the lightweight viewer', () => {
    const w = mount(FileDiffView, { props: { status: 'added', content: 'hello\nworld\n', truncated: false } })
    expect(w.find('.diff-added-viewer').text()).toContain('hello')
    expect(w.find('.diff-row').exists()).toBe(false)
  })

  it('shows a truncation note when truncated', () => {
    const w = mount(FileDiffView, { props: { status: 'added', content: 'x', truncated: true } })
    expect(w.find('.diff-truncated').exists()).toBe(true)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/ai-chat/__tests__/FileDiffView.test.ts`
Expected: FAIL (cannot find `../FileDiffView.vue`)

- [ ] **Step 3: Write minimal implementation**

Create `src/components/ai-chat/FileDiffView.vue`:

```vue
<script setup lang="ts">
import { computed } from 'vue'
import { parseUnifiedDiff } from '@/utils/unifiedDiff'

const props = defineProps<{
  status: 'added' | 'modified' | 'deleted' | null
  diff?: string
  content?: string
  truncated?: boolean
}>()

const hunks = computed(() => (props.status === 'modified' ? parseUnifiedDiff(props.diff || '') : []))
const addedLines = computed(() =>
  props.status === 'added' ? (props.content || '').replace(/\n$/, '').split('\n') : [],
)
</script>

<template>
  <div class="file-diff">
    <!-- modified: side-by-side hunks -->
    <template v-if="status === 'modified'">
      <div v-if="!hunks.length" class="diff-empty">无文本差异</div>
      <div v-for="(h, hi) in hunks" :key="hi" class="diff-hunk">
        <div class="diff-hunk__header">{{ h.header }}</div>
        <div v-for="(row, ri) in h.rows" :key="ri" class="diff-row" :class="`diff-row--${row.type}`">
          <span class="diff-no">{{ row.leftNo ?? '' }}</span>
          <span class="diff-cell" :class="{ 'diff-cell--del': row.type === 'del' || row.type === 'change' }">{{ row.left ?? '' }}</span>
          <span class="diff-no">{{ row.rightNo ?? '' }}</span>
          <span class="diff-cell" :class="{ 'diff-cell--add': row.type === 'add' || row.type === 'change' }">{{ row.right ?? '' }}</span>
        </div>
      </div>
    </template>

    <!-- added: lightweight capped viewer -->
    <template v-else-if="status === 'added'">
      <pre class="diff-added-viewer"><span v-for="(ln, i) in addedLines" :key="i" class="diff-added-line"><span class="diff-no">{{ i + 1 }}</span>{{ ln }}
</span></pre>
    </template>

    <div v-else class="diff-empty">该文件已删除，无可预览内容</div>

    <div v-if="truncated" class="diff-truncated">内容过大，已截断；请下载查看完整内容。</div>
  </div>
</template>

<style scoped lang="scss">
.file-diff { font-family: var(--el-font-family-mono, monospace); font-size: 12px; }
.diff-hunk { margin-bottom: 14px; border: 1px solid var(--el-border-color-light); border-radius: 6px; overflow: hidden; }
.diff-hunk__header { padding: 4px 10px; background: var(--el-fill-color); color: var(--el-text-color-secondary); }
.diff-row { display: grid; grid-template-columns: 44px 1fr 44px 1fr; align-items: stretch; }
.diff-no { color: var(--el-text-color-secondary); text-align: right; padding: 0 6px; user-select: none; background: var(--el-fill-color-lighter); }
.diff-cell { padding: 0 8px; white-space: pre-wrap; word-break: break-word; }
.diff-cell--del { background: var(--el-color-danger-light-9); }
.diff-cell--add { background: var(--el-color-success-light-9); }
.diff-added-viewer { margin: 0; white-space: pre-wrap; word-break: break-word; }
.diff-added-line { display: block; }
.diff-added-line .diff-no { display: inline-block; width: 38px; margin-right: 10px; }
.diff-empty { color: var(--el-text-color-secondary); padding: 8px 0; }
.diff-truncated { margin-top: 10px; padding: 6px 10px; font-size: 12px; color: var(--el-color-warning-dark-2); background: var(--el-color-warning-light-9); border-radius: 6px; }
</style>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run src/components/ai-chat/__tests__/FileDiffView.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/components/ai-chat/FileDiffView.vue src/components/ai-chat/__tests__/FileDiffView.test.ts
git commit -m "feat(ai-chat): FileDiffView side-by-side diff + added-file viewer"
```

---

## Task 7: 前端 — 抽屉接入 diff/查看器

**Files:**
- Modify: `src/views/ai-chat/AiChatView.vue`

- [ ] **Step 1: Import + state**

在 `<script setup>` 顶部 import 区加入：

```typescript
import FileDiffView from '@/components/ai-chat/FileDiffView.vue'
import { getFileDiff, type FileDiff } from '@/api/aiChat'
```

> `getFileDiff`/`FileDiff` 可合并进现有的 `from '@/api/aiChat'` import 行。

在现有 `previewOpen` / `preview` 声明附近新增 diff 抽屉状态：

```typescript
const diffOpen = ref(false)
const diffData = ref<FileDiff | null>(null)
const diffFile = ref('')
const diffLoading = ref(false)
```

- [ ] **Step 2: 改写 `previewChange`**

把现有 `previewChange`（`:107-117`）替换为：modified/added 走新 diff 抽屉，其余维持。

```typescript
async function previewChange(c: ChangedFile) {
  if (c.status === 'deleted' || !activeId.value) return
  diffFile.value = c.path
  diffData.value = null
  diffOpen.value = true
  diffLoading.value = true
  try {
    diffData.value = await getFileDiff(activeId.value, c.path)
  } catch {
    ElMessage.error('预览失败')
    diffOpen.value = false
  } finally {
    diffLoading.value = false
  }
}
```

- [ ] **Step 3: 新增 diff 抽屉模板**

在现有「制品预览面板」`ElDrawer`（`:673`）之后新增：

```vue
<ElDrawer v-model="diffOpen" :title="diffFile || '差异'" direction="rtl" size="60%">
  <div class="preview-body">
    <div v-if="diffLoading" class="ai-chat__pending"><ElIcon class="spin"><Loading /></ElIcon> 加载中…</div>
    <FileDiffView
      v-else-if="diffData"
      :status="diffData.status"
      :diff="diffData.diff"
      :content="diffData.content"
      :truncated="diffData.truncated"
    />
    <a
      v-if="diffData && diffData.status !== 'deleted'"
      class="change-file__dl" :href="fileUrl(diffFile)" target="_blank" rel="noopener"
      style="display:inline-block;margin-top:12px"
    >下载完整文件</a>
  </div>
</ElDrawer>
```

- [ ] **Step 4: Type-check + run existing frontend tests**

Run: `npx vue-tsc --noEmit`
Expected: 无新增错误。

Run: `npx vitest run src/components/ai-chat`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/views/ai-chat/AiChatView.vue
git commit -m "feat(ai-chat): preview modified/added files via diff drawer"
```

---

## Task 8: 前端 — 变更面板分组折叠

**Files:**
- Modify: `src/views/ai-chat/AiChatView.vue`

- [ ] **Step 1: 分组 computed + 折叠状态**

在 `<script setup>` 内（`changes` computed 之后）新增：

```typescript
const groupedChanges = computed(() => ({
  added: changes.value.filter((c) => c.status === 'added'),
  modified: changes.value.filter((c) => c.status === 'modified'),
  deleted: changes.value.filter((c) => c.status === 'deleted'),
}))

// 折叠状态：删除组默认折叠，其余展开
const collapsed = reactive<Record<'added' | 'modified' | 'deleted', boolean>>({
  added: false,
  modified: false,
  deleted: true,
})
function toggleGroup(k: 'added' | 'modified' | 'deleted') { collapsed[k] = !collapsed[k] }

const GROUP_META: { key: 'added' | 'modified' | 'deleted'; label: string; type: any }[] = [
  { key: 'added', label: '新增', type: 'success' },
  { key: 'modified', label: '修改', type: 'warning' },
  { key: 'deleted', label: '删除', type: 'info' },
]
```

把 `reactive` 加入顶部 `vue` import：`import { ref, reactive, computed, onMounted, nextTick, watch } from 'vue'`，并加入 `ArrowRight` 图标到 `@element-plus/icons-vue` import。

- [ ] **Step 2: 替换变更列表模板**

把现有「变更文件」区块里 `<div v-for="c in changes" ...>`（`:576-592`）整段替换为分组渲染（保留外层 `.ai-changes` 容器、标题与 🔄 刷新按钮）：

```vue
<div v-if="!changes.length" class="ai-changes__empty">暂无变更（点击 🔄 重新扫描）</div>
<template v-else>
  <div v-for="g in GROUP_META" :key="g.key" class="change-group">
    <template v-if="groupedChanges[g.key].length">
      <button class="change-group__head" type="button" @click="toggleGroup(g.key)">
        <ElIcon class="change-group__chev" :class="{ open: !collapsed[g.key] }"><ArrowRight /></ElIcon>
        <ElTag size="small" :type="g.type">{{ g.label }}</ElTag>
        <span class="change-group__count">{{ groupedChanges[g.key].length }}</span>
      </button>
      <div v-show="!collapsed[g.key]" class="change-group__body">
        <div v-for="c in groupedChanges[g.key]" :key="c.path" class="change-file">
          <div class="change-file__row">
            <span class="change-file__name">{{ c.path }}</span>
            <template v-if="c.status !== 'deleted'">
              <ElButton size="small" text @click="previewChange(c)">预览</ElButton>
              <a class="change-file__dl" :href="fileUrl(c.path)" target="_blank" rel="noopener">下载</a>
            </template>
          </div>
          <a
            v-if="c.status !== 'deleted' && isImageFile(c.path)"
            class="change-file__img" :href="fileUrl(c.path)" target="_blank" rel="noopener noreferrer"
          >
            <img :src="fileUrl(c.path)" :alt="c.path" />
          </a>
        </div>
      </div>
    </template>
  </div>
</template>
```

- [ ] **Step 3: 新增分组样式**

在 `<style scoped>` 的 `.ai-changes` 规则之后新增：

```scss
.change-group { margin-bottom: 4px; }
.change-group__head {
  display: flex; align-items: center; gap: 6px;
  width: 100%; padding: 4px 0; background: none; border: none; cursor: pointer;
  color: var(--el-text-color-regular);
}
.change-group__chev { transition: transform 0.15s; color: var(--el-text-color-secondary); &.open { transform: rotate(90deg); } }
.change-group__count { font-size: 12px; color: var(--el-text-color-secondary); }
.change-group__body { padding-left: 4px; }
.change-file__img { margin-left: 0; } /* tag removed from row; image lines up to the left */
```

- [ ] **Step 4: Type-check + run tests**

Run: `npx vue-tsc --noEmit`
Expected: 无新增错误。

Run: `npx vitest run src/components/ai-chat`
Expected: PASS

- [ ] **Step 5: Manual smoke (可选，需后端 + OpenCode 运行)**

启动 `npm run dev:all`，在一个有 git 仓库改动的会话里：确认新增/修改分组展开、删除分组默认折叠、点击分组标题可折叠/展开、修改文件「预览」打开并排 diff、新增大文件显示截断提示。

- [ ] **Step 6: Commit**

```bash
git add src/views/ai-chat/AiChatView.vue
git commit -m "feat(ai-chat): group changed files by status with collapsible sections"
```

---

## Self-Review 结果

- **Spec 覆盖**：后端 diff 端点（Task 1-3）、并排 diff 渲染（Task 4,6）、新增文件轻量查看器（Task 6）、分组折叠+删除默认折叠（Task 8）、抽屉接入（Task 7）、path 安全（Task 3）、测试（Task 1,2,3,4,6）均有对应任务。
- **Placeholder 扫描**：无 TBD/TODO；所有代码步骤含完整代码。Task 3/5 含「确认现有 fixture/签名」的说明性注释，但断言与目标明确，非占位。
- **类型一致性**：`FileDiff`/`DiffRow`/`DiffHunk`/`parseUnifiedDiff`/`getFileDiff`/`file_diff`/`resolve_repo_for_path`/`MAX_DIFF_LINES` 在定义与引用处命名一致；`FileDiffView` props 与 `getFileDiff` 返回结构对齐。
