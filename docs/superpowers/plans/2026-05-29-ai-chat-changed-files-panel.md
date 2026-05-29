# AI 助手会话变更文件面板 (SP2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 AI 助手对话框自动展示会话 workspace 内 git 仓库的变更文件(新增/修改/删除),可预览、可下载。

**Architecture:** 后端 `git_changes()` 用 `git status --porcelain -z` 扫描 workspace 的 git 仓库,经 `GET /sessions/:id/changes` 暴露;前端 store 在 `openSession`/`session.idle` 拉取,`AiChatView` 渲染"变更文件"面板,预览/下载复用现有 `/files/download`。

**Tech Stack:** Python Flask + subprocess(git);Vue 3 + TS + Element Plus + Pinia;pytest / Vitest。

设计依据:`docs/superpowers/specs/2026-05-29-ai-chat-changed-files-panel-design.md`

> 后端测试:`server/` 下 `set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/<file> -v`(Windows cmd;PowerShell 用 `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`)。前端:`npx vitest run <path>`。

---

### Task 1: 后端 `git_changes` 采集

**Files:**
- Create: `server/utils/workspace_changes.py`
- Test: `server/tests/test_workspace_changes.py`

- [ ] **Step 1: 写失败测试** `server/tests/test_workspace_changes.py`

```python
"""Tests for utils.workspace_changes.git_changes (real temp git repo)."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _git(cwd, *args):
    subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True)


def _init_repo(path):
    os.makedirs(path, exist_ok=True)
    _git(path, 'init', '-q')
    _git(path, 'config', 'user.email', 't@t')
    _git(path, 'config', 'user.name', 't')


def test_git_changes_detects_added_modified_deleted(tmp_path):
    from utils.workspace_changes import git_changes
    ws = str(tmp_path)
    repo = os.path.join(ws, 'repo')
    _init_repo(repo)
    for name in ('mod.txt', 'del.txt'):
        with open(os.path.join(repo, name), 'w') as f:
            f.write('base')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-q', '-m', 'base')
    with open(os.path.join(repo, 'new.txt'), 'w') as f:
        f.write('hi')                                   # added (untracked)
    with open(os.path.join(repo, 'mod.txt'), 'w') as f:
        f.write('changed')                              # modified
    os.remove(os.path.join(repo, 'del.txt'))            # deleted

    changes, truncated = git_changes(ws)
    by = {c['path']: c['status'] for c in changes}
    assert by.get('repo/new.txt') == 'added'
    assert by.get('repo/mod.txt') == 'modified'
    assert by.get('repo/del.txt') == 'deleted'
    assert truncated is False


def test_git_changes_skips_uploads_outputs(tmp_path):
    from utils.workspace_changes import git_changes
    ws = str(tmp_path)
    repo = os.path.join(ws, 'outputs', 'r')
    _init_repo(repo)
    with open(os.path.join(repo, 'x.txt'), 'w') as f:
        f.write('hi')
    changes, truncated = git_changes(ws)
    assert changes == []
    assert truncated is False


def test_git_changes_no_repo_returns_empty(tmp_path):
    from utils.workspace_changes import git_changes
    with open(os.path.join(str(tmp_path), 'loose.txt'), 'w') as f:
        f.write('not in a repo')
    assert git_changes(str(tmp_path)) == ([], False)
```

- [ ] **Step 2: 运行确认失败**

Run (server 目录): `set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_workspace_changes.py -v`
Expected: `ModuleNotFoundError: No module named 'utils.workspace_changes'`.

- [ ] **Step 3: 实现** `server/utils/workspace_changes.py`

```python
"""Compute the changed files in a session workspace via `git status`.

OpenCode's native /session/{id}/diff returns nothing for the clone+edit flow,
so we read git status of the workspace's git repos directly. Read-only.
"""
import os
import subprocess

MAX_CHANGES = 500
_SKIP_DIRS = {'uploads', 'outputs', 'node_modules', '.venv', '__pycache__'}


def _find_git_repos(workspace_path, max_depth=3):
    """Return dirs under workspace_path that are git repos (contain .git),
    bounded depth, skipping well-known noise dirs. Does not descend into a repo."""
    repos = []
    base_depth = workspace_path.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, _files in os.walk(workspace_path):
        depth = dirpath.rstrip(os.sep).count(os.sep) - base_depth
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        if depth >= max_depth:
            dirnames[:] = []
        if '.git' in os.listdir(dirpath):
            repos.append(dirpath)
            dirnames[:] = []  # don't descend into the repo
    return repos


def _map_status(xy):
    """Map a 2-char porcelain code to added|modified|deleted."""
    if xy == '??':
        return 'added'
    if 'D' in xy:
        return 'deleted'
    if 'A' in xy:
        return 'added'
    return 'modified'  # M / R / C / etc.


def git_changes(workspace_path):
    """Return (changes, truncated) where changes is
    [{'path': <rel-to-workspace POSIX>, 'status': 'added'|'modified'|'deleted'}]."""
    changes = []
    for repo in _find_git_repos(workspace_path):
        try:
            out = subprocess.run(
                ['git', '-C', repo, 'status', '--porcelain', '-z'],
                capture_output=True, text=True, timeout=20,
            )
        except Exception:
            continue
        if out.returncode != 0:
            continue
        entries = out.stdout.split('\0')
        i = 0
        while i < len(entries):
            e = entries[i]
            if not e:
                i += 1
                continue
            xy, path = e[:2], e[3:]
            if 'R' in xy or 'C' in xy:
                i += 1  # rename/copy: the next NUL field is the original path
            rel = os.path.relpath(os.path.join(repo, path), workspace_path).replace(os.sep, '/')
            changes.append({'path': rel, 'status': _map_status(xy)})
            i += 1
    changes.sort(key=lambda c: c['path'])
    truncated = len(changes) > MAX_CHANGES
    return changes[:MAX_CHANGES], truncated
```

- [ ] **Step 4: 运行确认通过**

Run (server 目录): `set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_workspace_changes.py -v`
Expected: 3 passed.

- [ ] **Step 5: 提交**

```bash
git add server/utils/workspace_changes.py server/tests/test_workspace_changes.py
git commit -m "feat(ai-chat): git_changes — collect session workspace changes"
```

---

### Task 2: 后端路由 `GET /sessions/:id/changes`

**Files:**
- Modify: `server/routes/ai_chat.py`
- Test: `server/tests/test_routes_ai_chat.py`

- [ ] **Step 1: 写失败测试** — 在 `server/tests/test_routes_ai_chat.py` 末尾追加:

```python
def test_list_changes_returns_git_changes(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active', '/tmp/ws')
    with patch('routes.ai_chat.git_changes',
               return_value=([{'path': 'repo/new.txt', 'status': 'added'}], False)) as gc:
        resp = client.get('/ai/chat/sessions/sess_x/changes', headers=dev_h)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['changes'] == [{'path': 'repo/new.txt', 'status': 'added'}]
    assert body['truncated'] is False
    assert gc.call_args[0][0] == '/tmp/ws'  # called with the session workspace


def test_list_changes_other_users_session_404(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = None
    resp = client.get('/ai/chat/sessions/sess_other/changes', headers=dev_h)
    assert resp.status_code == 404
```
(`from unittest.mock import patch` is already imported at the top of this file.)

- [ ] **Step 2: 运行确认失败**

Run (server 目录): `set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_ai_chat.py::test_list_changes_returns_git_changes -v`
Expected: FAIL — 404 (route not defined) or AttributeError patching `git_changes`.

- [ ] **Step 3a: 实现 import** — 在 `server/routes/ai_chat.py` 顶部 import 区(靠近 `from utils.workspace import (...)`)新增:

```python
from utils.workspace_changes import git_changes
```

- [ ] **Step 3b: 新增路由** — 在 `server/routes/ai_chat.py` 的 `list_files` 路由之后追加:

```python
@ai_chat_bp.route('/sessions/<sid>/changes', methods=['GET'])
@login_required
def list_changes(sid):
    """List files the session changed (added/modified/deleted) in its workspace
    git repos, via git status. Used by the chat's 变更文件 panel."""
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    changes, truncated = git_changes(sess[4])
    return jsonify({'changes': changes, 'truncated': truncated})
```

- [ ] **Step 3c: 修正 download docstring** — 把 `download_file` 的 docstring
`"""Download a file from the session workspace (uploads/ or outputs/)."""`
改为
`"""Download any file under the session workspace (path is safe_resolve'd)."""`

- [ ] **Step 4: 运行确认通过(含路由全量不回归)**

Run (server 目录): `set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_ai_chat.py -v`
Expected: 全部通过(新增 2 项 + 既有)。

- [ ] **Step 5: 提交**

```bash
git add server/routes/ai_chat.py server/tests/test_routes_ai_chat.py
git commit -m "feat(ai-chat): GET /sessions/:id/changes route"
```

---

### Task 3: 前端 API + store

**Files:**
- Modify: `src/api/aiChat.ts`
- Modify: `src/stores/aiChat.ts`
- Test: `src/stores/__tests__/aiChat.test.ts`

- [ ] **Step 1: 写失败测试** — 在 `src/stores/__tests__/aiChat.test.ts` 中:
  - 顶部的 `vi.mock('@/api/aiChat', () => ({ ... }))` 工厂里增加一个 mock 函数 `getChanges: vi.fn()`(与现有 `listFiles` 等并列)。
  - 追加测试:

```typescript
it('loadChanges populates changes for the session', async () => {
  const store = useAiChatStore()
  ;(api.getChanges as any).mockResolvedValue({
    changes: [{ path: 'repo/new.txt', status: 'added' }], truncated: false,
  })
  store.activeSessionId = 's1'
  await store.loadChanges('s1')
  expect(store.changes['s1']).toEqual([{ path: 'repo/new.txt', status: 'added' }])
  expect(store.activeChanges).toEqual([{ path: 'repo/new.txt', status: 'added' }])
})
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/stores/__tests__/aiChat.test.ts`
Expected: FAIL — `api.getChanges` undefined / `store.loadChanges` not a function.

- [ ] **Step 3a: API** — 在 `src/api/aiChat.ts` 增加类型与函数(放在 `AiFile` 定义之后):

```typescript
export interface ChangedFile {
  path: string
  status: 'added' | 'modified' | 'deleted'
}
```
并在 `listFiles` 函数之后增加:
```typescript
export function getChanges(id: string) {
  return get<{ changes: ChangedFile[]; truncated: boolean }>(
    `/ai/chat/sessions/${encodeURIComponent(id)}/changes`,
  )
}
```

- [ ] **Step 3b: store** — 编辑 `src/stores/aiChat.ts`:
  1. import 行加入 `getChanges` 与 `type ChangedFile`(与 `listFiles`/`AiFile` 并列)。
  2. state 增加 `changes: {} as Record<string, ChangedFile[]>,`(紧挨 `outputs: {}` 后)。
  3. getter 增加(紧挨 `activeOutputs` 后):
```typescript
    activeChanges(state): ChangedFile[] {
      return state.activeSessionId ? state.changes[state.activeSessionId] ?? [] : []
    },
```
  4. action 增加(紧挨 `loadFiles` 后):
```typescript
    async loadChanges(id: string) {
      try {
        const { changes } = await getChanges(id)
        this.changes[id] = changes
      } catch { /* non-fatal */ }
    },
```
  5. 在 `openSession` 里 `this.loadFiles(id)` 之后加 `this.loadChanges(id)`;在 `_handleEvent` 的 `session.idle` 分支里 `this.loadFiles(sid)` 之后加 `this.loadChanges(sid)`。
  （注意 state 接口/类型定义处也补 `changes` 字段,保持 TS 通过。）

- [ ] **Step 4: 运行确认通过**

Run: `npx vitest run src/stores/__tests__/aiChat.test.ts`
Expected: PASS（含新测试）。

- [ ] **Step 5: 提交**

```bash
git add src/api/aiChat.ts src/stores/aiChat.ts src/stores/__tests__/aiChat.test.ts
git commit -m "feat(ai-chat): store loadChanges + getChanges API"
```

---

### Task 4: 前端"变更文件"面板

**Files:**
- Modify: `src/views/ai-chat/AiChatView.vue`

> AiChatView 是大文件,沿用既有模式(`activeOutputs` 面板 + `ArtifactPreview` 抽屉)。无单测(与既有视图一致),靠 Task 5 真机验证 + `npm run build` 类型检查。

- [ ] **Step 1: `<script setup>` 增加状态与函数**
  - import 行 `import { downloadFileUrl, runScript, type AiMessage } from '@/api/aiChat'` 改为加上 `type ChangedFile`:
    `import { downloadFileUrl, runScript, type AiMessage, type ChangedFile } from '@/api/aiChat'`
  - 在 `const outputs = computed(() => store.activeOutputs)` 附近加:
```typescript
const changes = computed<ChangedFile[]>(() => store.activeChanges)
function changeBadge(status: string): { label: string; type: string } {
  if (status === 'added') return { label: '新增', type: 'success' }
  if (status === 'deleted') return { label: '删除', type: 'info' }
  return { label: '修改', type: 'warning' }
}
async function previewChange(c: ChangedFile) {
  if (c.status === 'deleted') return
  try {
    const text = await fetch(fileUrl(c.path)).then(r => r.text())
    const lang = (c.path.split('.').pop() || 'txt').toLowerCase()
    preview.value = { filename: c.path, versions: [{ lang, code: text }] }
    previewOpen.value = true
  } catch { ElMessage.error('预览失败') }
}
```
  (`preview`/`previewOpen`/`fileUrl`/`ElMessage` 均已存在于本文件。)

- [ ] **Step 2: 模板增加面板** — 在"产出文件"面板(`<div v-if="outputs.length" class="ai-outputs">...</div>`)之后追加:

```vue
            <!-- 变更文件（会话 workspace 内 git 仓库的新增/修改/删除） -->
            <div v-if="changes.length" class="ai-changes">
              <div class="ai-changes__title">变更文件</div>
              <div v-for="c in changes" :key="c.path" class="change-file">
                <el-tag size="small" :type="changeBadge(c.status).type">{{ changeBadge(c.status).label }}</el-tag>
                <span class="change-file__name">{{ c.path }}</span>
                <ElButton v-if="c.status !== 'deleted'" size="small" text @click="previewChange(c)">预览</ElButton>
                <a
                  v-if="c.status !== 'deleted'"
                  class="change-file__dl" :href="fileUrl(c.path)" target="_blank" rel="noopener"
                >下载</a>
              </div>
            </div>
```

- [ ] **Step 3: 样式** — 在 `<style scoped>` 末尾追加:

```scss
.ai-changes {
  margin: 4px 0 24px;
  padding: 12px 14px;
  border: 1px dashed var(--el-border-color);
  border-radius: 10px;
  background: var(--el-fill-color-lighter);
  &__title { font-size: 13px; font-weight: 600; color: var(--el-text-color-secondary); margin-bottom: 8px; }
}
.change-file {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 8px; border-radius: 6px; font-size: 14px;
  &:hover { background: var(--el-fill-color); }
  &__name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: var(--el-font-family-mono, monospace); }
  &__dl { color: var(--el-color-primary); text-decoration: none; font-size: 13px; }
}
```

- [ ] **Step 4: 类型检查 + 构建**

Run: `npm run build`
Expected: vue-tsc 无类型错误,构建成功。

- [ ] **Step 5: 提交**

```bash
git add src/views/ai-chat/AiChatView.vue
git commit -m "feat(ai-chat): 变更文件 panel with status + preview/download"
```

---

### Task 5: 真机验证

**Files:** 无。前置:`:8080` 生产栈运行(`cd server && python proxy.py`),OpenCode 4096;前端已 `npm run build`(Task 4 已构建)。

- [ ] **Step 1: 重启栈以加载后端新路由 + 新前端**

停 8080/3001/3003 进程树,`cd server && python proxy.py`,确认 8080/3001/3003 监听、3003 `/health` 200。

- [ ] **Step 2: 真机:克隆+改文件 → 看变更文件接口**

Run (server 目录,`python -`):

```python
import json, time, threading, urllib.request
BASE='http://127.0.0.1:8080'
def req(m,p,t=None,b=None):
    d=json.dumps(b).encode() if b is not None else None
    r=urllib.request.Request(BASE+p,data=d,method=m); r.add_header('Content-Type','application/json')
    if t: r.add_header('Authorization','Bearer '+t)
    return json.loads(urllib.request.urlopen(r,timeout=30).read())
tok=req('POST','/api/auth/login',b={'username':'admin','password':'admin123'})['token']
sid=req('POST','/api/ai/chat/sessions',t=tok,b={})['id']
idle={'v':False}
def sse():
    resp=urllib.request.urlopen(f'{BASE}/api/ai/chat/sessions/{sid}/events?access_token={tok}',timeout=180)
    for line in resp:
        if b'session.idle' in line: idle['v']=True; break
threading.Thread(target=sse,daemon=True).start(); time.sleep(1)
req('POST',f'/api/ai/chat/sessions/{sid}/messages',t=tok,b={'content':'用 bash 执行 git clone https://github.com/octocat/Hello-World.git ，然后在 Hello-World 目录新建 note.txt 内容 hi，并在 README 末尾追加一行 // edited'})
dl=time.time()+150
while time.time()<dl and not idle['v']: time.sleep(2)
time.sleep(2)
ch=req('GET',f'/api/ai/chat/sessions/{sid}/changes',t=tok)
print('changes:', json.dumps(ch, ensure_ascii=False))
```

Expected: `changes` 含 `Hello-World/note.txt`(added),若 agent 改了 README 则含 `Hello-World/README`(modified)。

- [ ] **Step 3: 真机 UI(Playwright)**

注入 token → 打开该会话 → 等待 → 断言"变更文件"面板存在(`document.querySelectorAll('.ai-changes .change-file').length >= 1`),点击「预览」弹出抽屉显示文件内容,「下载」链接 href 指向 `/files/download?path=...`。截图留证。

- [ ] **Step 4: 清理**

删除验证会话(或留作演示);确认仓库根目录无 `Hello-World` 残留(SP1 已保证落在 workspace)。

---

## 备注 / 风险

- 仅 git 仓库内变更可见(skill 本就 clone git 仓库);非 git 散落文件本轮不计入(后续可加 fs-snapshot)。
- 大仓库:`git status` 快;`MAX_CHANGES=500` 封顶 + 有界扫描深度。
- git 不可用/非仓库:逐仓库 try/except,跳过;无仓库返回空。
- 预览:删除文件不预览;二进制文件按文本取可能乱码——可接受(主要场景是脚本/文本)。
- 安全:路径相对 workspace,下载经 `safe_resolve` 防穿越,只读。
- 不做行级 diff(本轮);后续可在面板里加 before/after。
