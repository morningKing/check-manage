# 会话级技能上传 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 AI 助手 `+` 菜单里加「添加技能(zip)」,上传一个 OpenCode skill zip → 解压到当前会话 workspace 的 `.opencode/skills/<name>/` → 命令提示下拉里立刻出现该技能;会话删即清理。

**Architecture:** 后端纯文件操作:校验 zip + 防 zip slip + 探测「根含 SKILL.md」或「单一顶层目录含 SKILL.md」两种布局 + 解析 YAML frontmatter `name` + 原子写入。前端把现有 `+` 按钮换成 `ElDropdown`(两项:上传附件/添加技能),技能项触发隐藏 `<input type="file" accept=".zip">`,上传后刷新调色板。

**Tech Stack:** Flask + werkzeug;Vue 3 + TS + Element Plus + Pinia + Vitest/@vue/test-utils。

参考 spec:`docs/superpowers/specs/2026-05-30-ai-chat-session-skill-upload-design.md`

**约定**:后端测试 `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest <file> -v`;前端 `npx vitest run <file>`;构建 `npm run build`。不要用 ES2022 API。

---

## File Structure

- `server/utils/skill_upload.py` — 新,`extract_skill_zip(ws, file_storage) -> {name,path}` + `SkillUploadError(code,message)`。
- `server/routes/ai_chat.py` — 加 `POST /sessions/<sid>/skills` 路由 + import。
- `src/api/aiChat.ts` — `uploadSkill(id, file)`。
- `src/stores/aiChat.ts` — `uploadSkill(file)` action(成功后 `loadPaletteItems`)。
- `src/views/ai-chat/AiChatView.vue` — `+` 按钮包成 `ElDropdown` + 隐藏 `<input>` + handlers。
- 测试:`server/tests/test_skill_upload.py`(新)、`server/tests/test_routes_ai_chat.py`(追加)、`src/stores/__tests__/aiChat.skill.test.ts`(新)。

---

### Task 1: 后端 util `extract_skill_zip`

**Files:**
- Create: `server/utils/skill_upload.py`
- Test (create): `server/tests/test_skill_upload.py`

- [ ] **Step 1: 写失败测试** `server/tests/test_skill_upload.py`

```python
"""Tests for extract_skill_zip: zip layout detection, name parsing, zip-slip,
size/file caps, conflict, and atomic install."""

import io
import os
import zipfile
import pytest
from werkzeug.datastructures import FileStorage


def _make_zip(entries: dict) -> FileStorage:
    """Build a zip from {relpath: bytes} and wrap in a FileStorage('foo.zip')."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for path, data in entries.items():
            zf.writestr(path, data)
    buf.seek(0)
    return FileStorage(stream=buf, filename='foo.zip', content_type='application/zip')


def test_root_skill_md_uses_frontmatter_name(tmp_path):
    from utils.skill_upload import extract_skill_zip
    z = _make_zip({'SKILL.md': b'---\nname: alpha\ndescription: d\n---\n# alpha', 'helper.py': b'x=1'})
    res = extract_skill_zip(str(tmp_path), z)
    assert res == {'name': 'alpha', 'path': '.opencode/skills/alpha'}
    assert (tmp_path / '.opencode' / 'skills' / 'alpha' / 'SKILL.md').exists()
    assert (tmp_path / '.opencode' / 'skills' / 'alpha' / 'helper.py').read_bytes() == b'x=1'


def test_single_top_dir_layout_strips_prefix(tmp_path):
    from utils.skill_upload import extract_skill_zip
    z = _make_zip({'beta/SKILL.md': b'---\nname: beta\n---\n', 'beta/notes.txt': b'hi'})
    res = extract_skill_zip(str(tmp_path), z)
    assert res['name'] == 'beta'
    assert (tmp_path / '.opencode' / 'skills' / 'beta' / 'SKILL.md').exists()
    assert (tmp_path / '.opencode' / 'skills' / 'beta' / 'notes.txt').read_bytes() == b'hi'


def test_missing_frontmatter_name_falls_back_to_zip_filename(tmp_path):
    from utils.skill_upload import extract_skill_zip, SkillUploadError
    z = _make_zip({'SKILL.md': b'no frontmatter here'})
    z.filename = 'my-skill.zip'
    res = extract_skill_zip(str(tmp_path), z)
    assert res['name'] == 'my-skill'


def test_zip_slip_rejected(tmp_path):
    from utils.skill_upload import extract_skill_zip, SkillUploadError
    z = _make_zip({'SKILL.md': b'---\nname: ok\n---\n', '../evil.txt': b'pwn'})
    with pytest.raises(SkillUploadError) as ei:
        extract_skill_zip(str(tmp_path), z)
    assert ei.value.code == 'SKILL_ZIP_UNSAFE'


def test_missing_skill_md_rejected(tmp_path):
    from utils.skill_upload import extract_skill_zip, SkillUploadError
    z = _make_zip({'README.md': b'no skill md'})
    with pytest.raises(SkillUploadError) as ei:
        extract_skill_zip(str(tmp_path), z)
    assert ei.value.code == 'INVALID_SKILL_ZIP'


def test_name_conflict_rejected(tmp_path):
    from utils.skill_upload import extract_skill_zip, SkillUploadError
    # pre-create the target dir
    (tmp_path / '.opencode' / 'skills' / 'dup').mkdir(parents=True)
    z = _make_zip({'SKILL.md': b'---\nname: dup\n---\n'})
    with pytest.raises(SkillUploadError) as ei:
        extract_skill_zip(str(tmp_path), z)
    assert ei.value.code == 'SKILL_EXISTS'


def test_non_zip_filename_rejected(tmp_path):
    from utils.skill_upload import extract_skill_zip, SkillUploadError
    z = _make_zip({'SKILL.md': b'---\nname: ok\n---\n'})
    z.filename = 'not-a-zip.txt'
    with pytest.raises(SkillUploadError) as ei:
        extract_skill_zip(str(tmp_path), z)
    assert ei.value.code == 'BAD_FILE'


def test_invalid_name_rejected(tmp_path):
    from utils.skill_upload import extract_skill_zip, SkillUploadError
    z = _make_zip({'SKILL.md': b'---\nname: bad name with spaces\n---\n'})
    with pytest.raises(SkillUploadError) as ei:
        extract_skill_zip(str(tmp_path), z)
    assert ei.value.code == 'INVALID_SKILL_NAME'
```

- [ ] **Step 2: 运行确认失败**

`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_upload.py -v` → FAIL(`No module named 'utils.skill_upload'`)。

- [ ] **Step 3: 实现** `server/utils/skill_upload.py`

```python
"""Validate and extract an OpenCode skill zip into a session workspace.

A skill is a folder containing SKILL.md (YAML frontmatter with `name`) plus
helper files. OpenCode auto-discovers skills at
`<workspace>/.opencode/skills/<name>/` (verified via GET /skill?directory=ws).
"""

import io
import os
import re
import secrets
import shutil
import zipfile

from werkzeug.datastructures import FileStorage

MAX_ZIP_BYTES = 5 * 1024 * 1024
MAX_ZIP_ENTRIES = 200
SKILLS_SUBDIR = ".opencode/skills"
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class SkillUploadError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _safe_join(base: str, rel: str) -> str:
    """Join base+rel and refuse if the result escapes base (zip-slip guard)."""
    final = os.path.abspath(os.path.join(base, rel))
    base_abs = os.path.abspath(base)
    if final != base_abs and not final.startswith(base_abs + os.sep):
        raise SkillUploadError("SKILL_ZIP_UNSAFE", "zip contains path traversal")
    return final


def _parse_name_from_md(md_bytes: bytes) -> str | None:
    """Parse `name` from SKILL.md YAML frontmatter (returns None if absent)."""
    text = md_bytes.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"^name\s*:\s*(.+?)\s*$", line)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return None


def extract_skill_zip(workspace_path: str, file_storage: FileStorage) -> dict:
    """Extract `file_storage` (a .zip) into <workspace>/.opencode/skills/<name>/.

    Returns {"name": <name>, "path": ".opencode/skills/<name>"}.
    Raises SkillUploadError on any validation failure.
    """
    fname = (file_storage.filename or "").lower()
    if not fname.endswith(".zip"):
        raise SkillUploadError("BAD_FILE", "file must be a .zip")

    data = file_storage.read()
    if len(data) > MAX_ZIP_BYTES:
        raise SkillUploadError("SKILL_ZIP_TOO_LARGE", "zip exceeds 5 MiB")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise SkillUploadError("SKILL_ZIP_INVALID", "zip is corrupted")

    members = [m for m in zf.infolist() if not m.is_dir()]
    if len(members) > MAX_ZIP_ENTRIES:
        raise SkillUploadError("SKILL_ZIP_TOO_MANY_FILES", "too many entries")

    # Normalize paths and detect layout (root or single-top-dir).
    norm_names = [m.filename.replace("\\", "/") for m in members]
    skill_at_root = any(n.lower() == "skill.md" for n in norm_names)
    strip_prefix = ""
    if not skill_at_root:
        top_dirs = {n.split("/", 1)[0] for n in norm_names if "/" in n}
        root_files = [n for n in norm_names if "/" not in n]
        if len(top_dirs) == 1 and not root_files:
            top = next(iter(top_dirs))
            if any(n.lower() == f"{top.lower()}/skill.md" for n in norm_names):
                strip_prefix = top + "/"
            else:
                raise SkillUploadError("INVALID_SKILL_ZIP", "missing SKILL.md")
        else:
            raise SkillUploadError("INVALID_SKILL_ZIP", "missing SKILL.md")

    # Read SKILL.md to parse name (case-insensitive match in zip).
    target_md = (strip_prefix + "SKILL.md").lower()
    md_member = next(
        (m for m in members if m.filename.replace("\\", "/").lower() == target_md),
        None,
    )
    if md_member is None:  # pragma: no cover - guarded above
        raise SkillUploadError("INVALID_SKILL_ZIP", "missing SKILL.md")
    name = _parse_name_from_md(zf.read(md_member))
    if not name:
        base = os.path.splitext(os.path.basename(file_storage.filename or ""))[0]
        name = base
    if not _NAME_RE.match(name or ""):
        raise SkillUploadError("INVALID_SKILL_NAME", "name must match [A-Za-z0-9_-]{1,64}")

    skills_root = os.path.join(workspace_path, *SKILLS_SUBDIR.split("/"))
    final_dir = os.path.join(skills_root, name)
    if os.path.exists(final_dir):
        raise SkillUploadError("SKILL_EXISTS", f"skill {name!r} already exists in this session")

    os.makedirs(skills_root, exist_ok=True)
    tmp_dir = os.path.join(skills_root, ".tmp-" + secrets.token_hex(6))
    os.makedirs(tmp_dir, exist_ok=True)
    try:
        for m in members:
            rel = m.filename.replace("\\", "/")
            if strip_prefix and rel.startswith(strip_prefix):
                rel = rel[len(strip_prefix):]
            if not rel:
                continue
            dest = _safe_join(tmp_dir, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(m) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
        os.rename(tmp_dir, final_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    return {"name": name, "path": f"{SKILLS_SUBDIR}/{name}"}
```

- [ ] **Step 4: 运行确认通过**

`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_upload.py -v` → 8 passed.

- [ ] **Step 5: Commit**

```bash
git add server/utils/skill_upload.py server/tests/test_skill_upload.py
git commit -m "feat(ai-chat): extract_skill_zip util (zip layout + slip + atomic install)"
```

---

### Task 2: 后端路由 `POST /sessions/:id/skills`

**Files:**
- Modify: `server/routes/ai_chat.py`
- Test (append): `server/tests/test_routes_ai_chat.py`

- [ ] **Step 1: 写失败测试**(追加到 `test_routes_ai_chat.py`)

```python
def test_upload_skill_success(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc', 'active', '/tmp/ws')
    with patch('routes.ai_chat.extract_skill_zip',
               return_value={'name': 'hello', 'path': '.opencode/skills/hello'}) as ex:
        from io import BytesIO
        resp = client.post('/ai/chat/sessions/sess_x/skills',
                           data={'file': (BytesIO(b'fake-zip'), 'hello.zip')},
                           headers=dev_h, content_type='multipart/form-data')
    assert resp.status_code == 201
    assert resp.get_json() == {'name': 'hello', 'path': '.opencode/skills/hello'}
    assert ex.call_args[0][0] == '/tmp/ws'   # workspace path passed first


def test_upload_skill_guest_403(setup):
    from io import BytesIO
    client, *_, guest_h, _ = setup
    resp = client.post('/ai/chat/sessions/sess_x/skills',
                       data={'file': (BytesIO(b'z'), 'h.zip')},
                       headers=guest_h, content_type='multipart/form-data')
    assert resp.status_code == 403


def test_upload_skill_other_users_session_404(setup):
    from io import BytesIO
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = None
    resp = client.post('/ai/chat/sessions/sess_other/skills',
                       data={'file': (BytesIO(b'z'), 'h.zip')},
                       headers=dev_h, content_type='multipart/form-data')
    assert resp.status_code == 404


def test_upload_skill_no_file_400(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc', 'active', '/tmp/ws')
    resp = client.post('/ai/chat/sessions/sess_x/skills',
                       data={}, headers=dev_h, content_type='multipart/form-data')
    assert resp.status_code == 400
    assert resp.get_json()['code'] == 'BAD_FILE'


def test_upload_skill_util_error_400(setup):
    from io import BytesIO
    from utils.skill_upload import SkillUploadError
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc', 'active', '/tmp/ws')
    with patch('routes.ai_chat.extract_skill_zip',
               side_effect=SkillUploadError('INVALID_SKILL_ZIP', 'missing SKILL.md')):
        resp = client.post('/ai/chat/sessions/sess_x/skills',
                           data={'file': (BytesIO(b'z'), 'h.zip')},
                           headers=dev_h, content_type='multipart/form-data')
    assert resp.status_code == 400
    body = resp.get_json()
    assert body['code'] == 'INVALID_SKILL_ZIP'
    assert body['error'] == 'missing SKILL.md'
```

- [ ] **Step 2: 运行确认失败**

`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_routes_ai_chat.py -k upload_skill -v` → FAIL(路由不存在 / 404)。

- [ ] **Step 3: 实现路由**

In `server/routes/ai_chat.py`, add this import to the top imports area (after the other `from utils.*` imports, e.g. next to `from utils.session_token import generate_token, revoke_token`):

```python
from utils.skill_upload import extract_skill_zip, SkillUploadError
```

In `server/routes/ai_chat.py`, add this route (e.g. right after `upload_file`):

```python
@ai_chat_bp.route('/sessions/<sid>/skills', methods=['POST'])
@write_required
def upload_skill(sid):
    """Install an OpenCode skill zip into <workspace>/.opencode/skills/<name>/.
    OpenCode then discovers it via GET /skill?directory=<workspace>; the chat's
    command palette picks it up after loadPaletteItems refresh."""
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'file required', 'code': 'BAD_FILE'}), 400
    try:
        res = extract_skill_zip(sess[4], f)
    except SkillUploadError as e:
        return jsonify({'error': e.message, 'code': e.code}), 400
    return jsonify(res), 201
```

- [ ] **Step 4: 运行确认通过**

Run `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_routes_ai_chat.py -k upload_skill -v` → 5 passed. Then run the whole file `... -q` — no regressions.

- [ ] **Step 5: Commit**

```bash
git add server/routes/ai_chat.py server/tests/test_routes_ai_chat.py
git commit -m "feat(ai-chat): POST /sessions/:id/skills installs a skill zip"
```

---

### Task 3: 前端 api + store action

**Files:**
- Modify: `src/api/aiChat.ts`
- Modify: `src/stores/aiChat.ts`
- Test (create): `src/stores/__tests__/aiChat.skill.test.ts`

- [ ] **Step 1: 写失败测试** `src/stores/__tests__/aiChat.skill.test.ts`

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/aiChat', () => ({
  uploadSkill: vi.fn(),
  getCommands: vi.fn(), postCommand: vi.fn(),
  getMcpServices: vi.fn(),
  createSession: vi.fn(), listSessions: vi.fn(), renameSession: vi.fn(), deleteSession: vi.fn(),
  getMessages: vi.fn(), sendMessage: vi.fn(), uploadFile: vi.fn(), listFiles: vi.fn(),
  getChanges: vi.fn(), createEventStream: vi.fn(() => ({ close() {} })),
}))
import { uploadSkill, getCommands } from '@/api/aiChat'
import { useAiChatStore } from '@/stores/aiChat'

beforeEach(() => setActivePinia(createPinia()))

describe('uploadSkill store action', () => {
  it('throws if no active session', async () => {
    const store = useAiChatStore()
    await expect(store.uploadSkill(new File(['x'], 'a.zip'))).rejects.toThrow()
  })

  it('uploads and then reloads palette items', async () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    ;(uploadSkill as any).mockResolvedValue({ name: 'hello', path: '.opencode/skills/hello' })
    ;(getCommands as any).mockResolvedValue({ commands: [], skills: [{ name: 'hello', description: '' }] })
    const res = await store.uploadSkill(new File(['z'], 'h.zip'))
    expect(res.name).toBe('hello')
    expect(uploadSkill).toHaveBeenCalledWith('s1', expect.any(File))
    expect(getCommands).toHaveBeenCalledWith('s1')
    expect(store.paletteItems['s1'].skills[0].name).toBe('hello')
    expect(store.uploading).toBe(false)
  })

  it('clears uploading on failure and rethrows', async () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    ;(uploadSkill as any).mockRejectedValue(new Error('boom'))
    await expect(store.uploadSkill(new File(['z'], 'h.zip'))).rejects.toThrow('boom')
    expect(store.uploading).toBe(false)
  })
})
```

- [ ] **Step 2: 运行确认失败**

`npx vitest run src/stores/__tests__/aiChat.skill.test.ts` → FAIL(`uploadSkill is not a function` 或导入失败)。

- [ ] **Step 3: 实现 api**(在 `src/api/aiChat.ts`,挨着 `uploadFile`):

```ts
export function uploadSkill(id: string, file: File) {
  const form = new FormData()
  form.append('file', file)
  return post<{ name: string; path: string }>(
    `/ai/chat/sessions/${encodeURIComponent(id)}/skills`, form,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  )
}
```

- [ ] **Step 4: 实现 store action**

In `src/stores/aiChat.ts`, extend the `@/api/aiChat` import to also include `uploadSkill`. Then add this action (e.g. after `uploadAttachment`):

```ts
async uploadSkill(file: File): Promise<{ name: string; path: string }> {
  const sid = this.activeSessionId
  if (!sid) throw new Error('no active session')
  this.uploading = true
  try {
    const res = await uploadSkill(sid, file)
    await this.loadPaletteItems(sid)
    return res
  } finally { this.uploading = false }
},
```

- [ ] **Step 5: 运行确认通过**

`npx vitest run src/stores/__tests__/aiChat.skill.test.ts` → 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/api/aiChat.ts src/stores/aiChat.ts src/stores/__tests__/aiChat.skill.test.ts
git commit -m "feat(ai-chat): uploadSkill api + store action (refresh palette on success)"
```

---

### Task 4: AiChatView `+` → ElDropdown + 隐藏 zip input

**Files:**
- Modify: `src/views/ai-chat/AiChatView.vue`

(组件层级测试 `ElDropdown` + 隐藏 `<input>` 触发链路较脏,本任务靠构建 + 真机验证,不写新的组件测试。)

- [ ] **Step 1: imports**

In `src/views/ai-chat/AiChatView.vue` `<script setup>` 的 element-plus import 处(已有 `ElButton, ElInput, ElScrollbar, ElIcon, ElTooltip, ElEmpty, ElMessageBox, ElMessage`),加上 `ElDropdown, ElDropdownMenu, ElDropdownItem`:

```ts
import {
  ElButton, ElInput, ElScrollbar, ElIcon, ElTooltip, ElEmpty, ElMessageBox, ElMessage,
  ElDropdown, ElDropdownMenu, ElDropdownItem,
} from 'element-plus'
```

- [ ] **Step 2: state + handlers**

In `<script setup>`(near the existing `pickFiles`/`onUploadPicked` handlers), add:

```ts
const skillInput = ref<HTMLInputElement | null>(null)

function handleAddMenu(cmd: string) {
  if (cmd === 'file') pickFiles()
  else if (cmd === 'skill') skillInput.value?.click()
}

async function onSkillPicked(e: Event) {
  const input = e.target as HTMLInputElement
  const f = input.files?.[0]
  input.value = ''
  if (!f) return
  if (!activeId.value) await newSession()
  try {
    const res = await store.uploadSkill(f)
    ElMessage.success(`已添加技能：${res.name}`)
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.error || '技能添加失败')
  }
}
```

- [ ] **Step 3: 模板替换**

In the template, find the existing `+` button line — currently:
```html
<ElButton class="composer-add" :icon="Plus" circle text :loading="store.uploading" @click="pickFiles" />
```
Replace it with:
```html
<ElDropdown trigger="click" @command="handleAddMenu">
  <ElButton class="composer-add" :icon="Plus" circle text :loading="store.uploading" />
  <template #dropdown>
    <ElDropdownMenu>
      <ElDropdownItem command="file">上传附件</ElDropdownItem>
      <ElDropdownItem command="skill">添加技能 (zip)</ElDropdownItem>
    </ElDropdownMenu>
  </template>
</ElDropdown>
<input ref="skillInput" type="file" accept=".zip" hidden @change="onSkillPicked" />
```

- [ ] **Step 4: 构建 + 前端全量测试**

`npm run build` → 必须通过(vue-tsc + vite)。
`npm test` → 之前绿的依然绿,加上 Task 3 的 3 个新 store 测试。

- [ ] **Step 5: 真机验证**

1. 备一个最小 skill zip(本地造):
   - `mkdir hello-skill && cd hello-skill && printf '%s\n' '---' 'name: hello-skill' 'description: hi' '---' '# hello' > SKILL.md && cd .. && (cd hello-skill && zip -r ../hello-skill.zip .)`
2. 重启后端 + 刷新前端。点 `+` → 选「添加技能 (zip)」→ 选 `hello-skill.zip` → 成功提示「已添加技能:hello-skill」。
3. 输入 `/` → 调色板「技能」分组里应见到 `hello-skill`。选中 → 输入框出现「使用 \`hello-skill\` 技能:」。

- [ ] **Step 6: Commit**

```bash
git add src/views/ai-chat/AiChatView.vue
git commit -m "feat(ai-chat): + button dropdown adds session-level skills"
```

---

## Self-Review 备注

- **Spec 覆盖**:T1=util(两种布局、name 解析、zip slip、size/file 上限、冲突);T2=路由(归属/guest/坏文件/util 错误转 400/成功 201);T3=api+store(成功+失败+无会话);T4=UI(`+` ElDropdown + 隐藏 zip input + handlers + 真机)。
- **类型一致**:`uploadSkill(id, file) -> {name,path}` 在 api/store/真机一致;store action 调 `loadPaletteItems` 与命令调色板同源刷新;后端 `SkillUploadError(code,message)` 路由层映射为 `{error: message, code}` 与前端 `err.response.data.error` 一致。
- **YAGNI**:无技能面板/删除/版本化/自动改名。
