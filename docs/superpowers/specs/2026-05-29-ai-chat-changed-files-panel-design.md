# AI 助手会话变更文件面板 (SP2) — 设计

- 日期: 2026-05-29
- 状态: 已批准（待实现）
- 范围: 后端(git 变更采集 + 路由) + 前端(store + 面板)
- 依赖: SP1(会话工作目录隔离)已合并——agent/skill 的文件操作现已落在会话 workspace

## 背景

用户的 skill 会从远程库 `git clone` 到会话 workspace 并新增/修改文件(巡检用例脚本开发)。
SP1 之后这些文件落在该会话 workspace。现在要把"新增/修改/删除的文件"**自动展示在对话框**,可预览、可下载。

### 关键发现(已验证)

- OpenCode 原生 `GET /session/{id}/diff` 对本场景返回 **0**(即便指向克隆出的 git 仓库;它也不含未跟踪新文件)。
  ⇒ **不可用**。
- `git status --porcelain` 在克隆仓库里正确给出变更(实测:`?? note.txt`)。⇒ 采用 git status。
- 现有下载接口 `GET /ai/chat/sessions/:id/files/download?path=<rel>` 用 `safe_resolve(workspace, rel)`
  限定在 workspace 内(防穿越),**允许 workspace 内任意文件**(不仅 uploads/outputs)。⇒ 预览/下载直接复用。

## 关键决策(已确认)

1. **呈现方式**:后端计算 + 前端**自动面板**(像"产出文件"),在会话 `session.idle` 与 `openSession` 时拉取。
   不依赖 agent 主动调工具(对弱模型 MiMo 可靠)。
2. **采集机制**:`git status`(skill 本就 clone git 仓库)。非 git 区域的散落文件本轮不计入(YAGNI)。
3. **视图深度**:文件清单 + 状态徽标(新增/修改/删除)+ 点击预览当前内容 + 下载。**不做行级 diff**(后续可加)。

## 目标 / 非目标

**目标**:把会话 workspace 内 git 仓库的变更文件(added/modified/deleted)自动列在对话框面板,可预览/下载。

**非目标**:
- 不做行级 before/after diff(本轮)。
- 不纳入 workspace 内非 git 区域的散落文件(后续可加 fs-snapshot)。
- 不改 OpenCode、不依赖其 `/diff`。
- 不依赖 agent 调用工具(纯后端计算 + 自动拉取)。

## 设计

### 1. 后端

**`server/utils/workspace_changes.py`** — 新增 `git_changes(workspace_path) -> list[dict]`:
- 在 workspace 下**有界深度**(≤3 层)扫描 `.git` 目录,**跳过** `uploads/`、`outputs/`。
- 对每个仓库 `subprocess.run(['git','-C',repo,'status','--porcelain','-z'], ...)`(`-z` 避免文件名转义/换行歧义)。
- porcelain XY 码映射为 `status`:`??`→`added`(未跟踪);含 `D`→`deleted`;含 `A`→`added`;否则有 `M`/`R`→`modified`。
- 返回 `[{ 'path': <相对 workspace 的 POSIX 路径>, 'status': 'added'|'modified'|'deleted' }]`,按 path 排序,**数量封顶 500**(超出截断并标记)。
- 只读;`git` 不可用或仓库异常时该仓库跳过(不抛断整个请求)。

**`server/routes/ai_chat.py`** — 新路由 `GET /sessions/:id/changes`(`@login_required`):
- 校验会话归属;取 `workspace_path`;`return jsonify({'changes': git_changes(workspace_path), 'truncated': <bool>})`。

**预览/下载**:复用现有 `GET /sessions/:id/files/download?path=<rel>`(已 workspace 限定;顺手把其 docstring 从
"uploads/ or outputs/" 改为"workspace 内任意文件")。

### 2. 前端

- **`src/api/aiChat.ts`**:`getChanges(sid)` → `GET /ai/chat/sessions/:id/changes`;类型 `ChangedFile = {path,status}`。
- **`src/stores/aiChat.ts`**:`changes: Record<string, ChangedFile[]>` 状态;`loadChanges(sid)`(失败静默,模仿 `loadFiles`);
  在 `openSession` 与 `session.idle` 调用(与 `loadFiles` 同处);`activeChanges` getter。
- **`src/components/ai-chat/AiChatView.vue`**:在"产出文件"面板旁新增**"变更文件"面板**:
  - 仅当 `activeChanges.length` 时显示;每行:状态徽标(新增=success / 修改=warning / 删除=info)+ 文件路径
    + 「预览」+「下载」。
  - 「下载」= `<a :href="fileUrl(path)">`(复用 `downloadFileUrl`)。
  - 「预览」= 取该文件文本(经 download URL fetch)后用现有 `ArtifactPreview` 抽屉展示(传 `{lang, code}`);
    deleted 状态无预览。

## 数据流

1. 用户发消息 → agent/skill 在会话 workspace 里 clone/改文件(SP1 已保证落在 workspace)。
2. `session.idle` → 前端 `loadChanges(sid)` → `GET /changes` → 后端 `git_changes(workspace)` 跑 git status。
3. 前端"变更文件"面板渲染清单 + 状态;点击预览/下载经现有 download 接口取该文件。

## 测试

- **后端单测 `server/tests/test_workspace_changes.py`**:在 `tmp_path` 建一个 git 仓库,提交基线,然后
  新增/修改/删除文件,断言 `git_changes` 返回对应 `{path,status}`;`uploads/`/`outputs/` 下的 `.git` 被跳过;
  数量封顶。
- **后端路由测试 `server/tests/test_routes_ai_chat.py`**:`GET /changes` 归属校验 + 返回 `git_changes` 结果(mock util)。
- **前端**:`stores` 的 `loadChanges` 设置 `changes[sid]`;`AiChatView`/小组件渲染状态徽标 + 预览/下载按钮(Vitest，stub Element Plus）。
- **真机**:复用 SP1 克隆场景,确认"变更文件"面板出现 `Hello-World/note.txt(新增)`,可预览/下载。

## 风险与缓解

- **大仓库**:`git status` 快;数量封顶 500、有界扫描深度防 payload 膨胀/卡顿。
- **git 不可用/非仓库**:逐仓库 try/except,跳过不影响整体;无仓库则返回空清单。
- **路径安全**:路径相对 workspace;下载经 `safe_resolve` 防穿越;只读。
- **删除文件预览**:无内容可取 → 不提供预览,仅显示"已删除"。

## 影响文件清单

- 新增:`server/utils/workspace_changes.py`、`server/tests/test_workspace_changes.py`
- 改:`server/routes/ai_chat.py`(新增 `/changes` 路由 + download docstring)
- 改:`src/api/aiChat.ts`、`src/stores/aiChat.ts`、`src/views/ai-chat/AiChatView.vue`
- 测试:`server/tests/test_routes_ai_chat.py` + 前端相应 `__tests__`
