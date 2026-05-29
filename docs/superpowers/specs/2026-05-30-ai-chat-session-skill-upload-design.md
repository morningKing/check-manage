# AI 助手会话级技能上传 — 设计

- 日期: 2026-05-30
- 状态: 已批准（待实现）
- 范围: 后端（上传路由 + zip 解压 util）+ 前端（api/store/AiChatView 的 + 菜单）
- 依赖: 命令提示下拉已在 [[ai-chat-command-palette-design]] 落地（`loadPaletteItems` 已 fetch `/skill`）。

## 背景

会话已有 `+` 按钮上传任意附件到 `<workspace>/uploads/`。用户希望同一个按钮也能上传一个**技能 zip**，落进会话 workspace，让 OpenCode 在本会话内发现它，命令提示下拉里立刻出现。会话删除即自动清理。

### 关键发现（已验证）

- 在 `<workspace>/.opencode/skills/<name>/SKILL.md` 放一个文件，OpenCode `GET /skill?directory=<workspace>` **会返回**该技能（路径绝对、含 frontmatter description）—— 实测探针 `probe-.opencode-skills-probe` 命中。所以"会话级"技能 = 写进会话 workspace 的 `.opencode/skills/`。
- 现有 `+` 按钮：`src/views/ai-chat/AiChatView.vue` 第 403 行 `<ElButton class="composer-add" :icon="Plus" circle text :loading="store.uploading" @click="pickFiles" />`，回调走 `store.uploadAttachment(f)` → `POST /sessions/:id/files` → `<ws>/uploads/`。
- 现有上传路由：`server/routes/ai_chat.py:417` `POST /sessions/<sid>/files`（已 `secure_filename` + workspace 限定）；写入 `uploads/` 子目录。
- 已有 `loadPaletteItems` 在 `openSession`/`session.idle` 调用 → 上传成功后再调一次就能刷新调色板。

## 目标 / 非目标

**目标**：选 `+` 弹出菜单 → 「添加技能(zip)」→ 选 zip → 解压到 `<workspace>/.opencode/skills/<name>/` → 前端刷新调色板 → 输入 `/` 看见该技能、选中插脚手架。

**非目标**：
- 不做会话技能面板/单个删除（YAGNI；删会话即清）。
- 不做版本升级/重名自动改名（同名直接拒绝，让用户改 zip 里的 name 后重传）。
- 不改 OpenCode、不依赖 OpenCode 客户端 API（直接写文件）。
- 不解析/执行 zip 里的代码——只解压、不沙箱跑。

## 设计

### 1. 后端

**`server/utils/skill_upload.py`**（新）—— 纯函数模块：

```python
def extract_skill_zip(workspace_path: str, file_storage) -> dict:
    """Extract an OpenCode skill zip into <workspace>/.opencode/skills/<name>/.
    Returns {"name": <skill name>, "path": ".opencode/skills/<name>"}.
    Raises SkillUploadError(code, message) for validation failures.
    """
```

- 大小上限 5 MB（`werkzeug` `file.content_length` 或读取后 `len`），文件数上限 200，否则 `SkillUploadError('SKILL_ZIP_TOO_LARGE', ...)`/`SKILL_ZIP_TOO_MANY_FILES`。
- 用 `zipfile.ZipFile` 打开（坏 zip → `SKILL_ZIP_INVALID`）。
- 探测两种布局：
  1. 根存在 `SKILL.md` → zip 根即 skill 目录。
  2. 单一顶层目录 `top/`，其下有 `SKILL.md` → 剥掉 `top/`，把内部内容当作 skill 目录。
  - 其它布局（无 `SKILL.md`、或多个顶层兄弟）→ `INVALID_SKILL_ZIP`。
- 技能名解析：读 `SKILL.md` 的 YAML frontmatter `name`（`---` 块）；若无，回退到 zip 文件名去 `.zip` 并 `secure_filename`；最终非空、限制为 `[A-Za-z0-9_-]{1,64}`，否则 `INVALID_SKILL_NAME`。
- 目标目录：`<workspace>/.opencode/skills/<name>/`。若已存在 → `SKILL_EXISTS`（400）。
- 解压：先解到 `<workspace>/.opencode/skills/.tmp-<rand>/`，对每个条目用 `safe_resolve(tmp_root, member_name)` 校验 zip slip；目录条目 `mkdir`；其余 `open(...).write(member.read())`。完成后 `os.rename(tmp_root, final)`；异常时 `shutil.rmtree(tmp_root)`。
- 返回 `{"name": name, "path": f".opencode/skills/{name}"}`.

**`server/routes/ai_chat.py`** — 新路由 `POST /sessions/<sid>/skills`（`@write_required`）：
- 归属校验同 `upload_file`；无文件/非 `.zip` → 400。
- 调 `extract_skill_zip(sess[4], file)`；`SkillUploadError` → 400 `{error, code}`；成功 201 `{name, path}`。

### 2. 前端

**`src/api/aiChat.ts`** — `uploadSkill(id, file)`，模仿 `uploadFile`（multipart `file`），返回 `{name, path}`。

**`src/stores/aiChat.ts`** — 新 action：
```ts
async uploadSkill(file: File): Promise<{ name: string; path: string }> {
  const sid = this.activeSessionId
  if (!sid) throw new Error('no active session')
  this.uploading = true
  try {
    const res = await uploadSkill(sid, file)
    await this.loadPaletteItems(sid)   // refresh palette so the new skill shows
    return res
  } finally { this.uploading = false }
}
```

**`src/views/ai-chat/AiChatView.vue`** — 把 `<ElButton class="composer-add" ...>` 包成 `<ElDropdown trigger="click">`：
- 按钮触发器仍是当前 `+`，`text circle :icon="Plus"`，`:loading="store.uploading"`。
- 菜单两项：
  - 「上传附件」→ 走现有 `pickFiles()`。
  - 「添加技能 (zip)」→ 触发一个**隐藏的** `<input ref="skillInput" type="file" accept=".zip">`；其 `@change` 处理：取首文件 → `await store.uploadSkill(f)` → 成功 `ElMessage.success(\`已添加技能：\${res.name}\`)`，失败 `ElMessage.error(err?.response?.data?.error || '技能添加失败')`；清空 input 值。
- 不动 `pickFiles` 既有逻辑。

### 数据流

1. 点 `+` → ElDropdown → 选「添加技能(zip)」。
2. 隐藏 `<input>` 弹文件选择器（限 `.zip`）。
3. 选中 → `store.uploadSkill(file)` → `POST /sessions/:id/skills`（multipart）。
4. 后端 `extract_skill_zip` → 写 `<ws>/.opencode/skills/<name>/`。
5. store 调 `loadPaletteItems(sid)` → 后端 `GET /commands` → OpenCode `GET /skill?directory=ws` 返回包含新技能。
6. 输入 `/` → 调色板「技能」分组里看到 `<name>`；选中插脚手架「使用 \`<name>\` 技能:」。

### 错误处理

| 情况 | 行为 |
|------|------|
| 文件缺失 / 非 `.zip` | 400 `{code:'BAD_FILE'}` |
| zip 损坏 | 400 `{code:'SKILL_ZIP_INVALID'}` |
| zip 大于 5 MB / 文件数超 200 | 400 `{code:'SKILL_ZIP_TOO_LARGE'}` / `..._TOO_MANY_FILES` |
| zip 内含路径穿越 | 400 `{code:'SKILL_ZIP_UNSAFE'}` |
| 缺 `SKILL.md` 或布局非两种之一 | 400 `{code:'INVALID_SKILL_ZIP'}` |
| 技能名非法 | 400 `{code:'INVALID_SKILL_NAME'}` |
| 技能已存在 | 400 `{code:'SKILL_EXISTS'}` |
| 会话不归属 | 404 `{code:'SESSION_NOT_FOUND'}` |
| guest | 403（`@write_required`） |

前端把 `err.response.data.error`（如有）作为 ElMessage 文案，否则用兜底「技能添加失败」。

### 测试

**后端**
- `server/tests/test_skill_upload.py`（新）：在 `tmp_path` 用 `zipfile.ZipFile` 构造若干 zip → 调 `extract_skill_zip`：
  - 根含 `SKILL.md`（含 `---\nname: foo\n---`）→ 返 `{name:'foo', path:'.opencode/skills/foo'}`，文件落在 `<ws>/.opencode/skills/foo/SKILL.md`。
  - 单顶层目录 `bar/` 含 `SKILL.md`（`name: bar`）→ 剥掉 top；落在 `bar/`。
  - 无 frontmatter name → 回退 zip 文件名。
  - zip slip（条目 `../evil`）→ 抛 `SKILL_ZIP_UNSAFE`。
  - 缺 `SKILL.md` → `INVALID_SKILL_ZIP`。
  - 重名 → `SKILL_EXISTS`。
- `server/tests/test_routes_ai_chat.py`：`POST /sessions/:id/skills`：
  - 归属 404、guest 403。
  - 成功 201 + 调 `extract_skill_zip`（mock util）。
  - util 抛 `SkillUploadError` → 400 含 `code`。

**前端（Vitest + stub Element Plus）**
- `aiChat` store `uploadSkill`：调 `uploadSkill` api + 之后调 `loadPaletteItems`。
- AiChatView：mount → 点 `+` → 菜单显示两项；触发「添加技能」→ 隐藏 input 收到 change → 调 `store.uploadSkill`。

**真机**：备一个最小 zip（含 `SKILL.md` 带 `name: hello-skill`）→ 点 `+` 选「添加技能」→ 上传 → 输入 `/hello` 调色板出现该项；选中输入框出现脚手架；发送后 agent 调技能。

## 影响文件清单

- 新增：`server/utils/skill_upload.py`、`server/tests/test_skill_upload.py`
- 改：`server/routes/ai_chat.py`（新路由 + 顶部 imports）
- 改：`src/api/aiChat.ts`（`uploadSkill`）
- 改：`src/stores/aiChat.ts`（`uploadSkill` action + 测试）
- 改：`src/views/ai-chat/AiChatView.vue`（`+` → ElDropdown + 隐藏 zip input + handler）
- 测试：`server/tests/test_routes_ai_chat.py` 追加 + 前端 `__tests__/`
