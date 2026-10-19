# AI 批任务管理页：产出文件能力设计

**日期**：2026-08-14
**状态**：设计已确认，待编写实施计划

## 1. 目标与范围

为 admin AI 批任务管理页（`/admin/ai-batches`，2026-08-12 落地）补齐**产出文件能力**：让管理员跨用户查看每个子任务的产出文件、预览、下载、导入到系统 `data_files`，并查看 git 变更与 diff。

原始需求：

> 后台有 AI 批任务管理功能，比较简陋，仅支持会话的查看、任务重执行，当业务人员想知道 AI 任务产出了哪些文件也做不到。

### 1.1 已确认的范围决策

| 维度 | 决策 |
|---|---|
| 文件来源 | **live scan 优先 + LEFT JOIN DB 记录补 `dataFileId`**。管理员视角只关心当下能下/能导入的文件；DB 历史记录里已被删的路径不列 |
| 入口 | 在子任务表「操作」列与「查看对话」「重跑」并列加「**产出文件**」链接，打开新的 `el-dialog`（独立，不挤进对话 Tab） |
| 鉴权 | 沿用 `admin.ai_chat_admin`（"AI 会话治理"），**不新建能力键**——已有批管理、跨用户会话查看走的就是它 |
| 导入归属 | `uploaded_by = 批任务的原归属用户`（不是管理员本人，不是 NULL）。`operation_logs` 记管理员用户 id 做审计 |
| 导入白名单 | **扩展不替换**：admin 端用 `DB 白名单 ∪ live-scan 文件名集合`；owner 端、对外 Open API 保持仅 DB 白名单 |
| PR 形态 | **单 PR**：util 抽取 + owner 端 refactor + admin 端点 + UI + 白名单扩展 + 完整验证 |
| 入口面板 | 子任务级 dialog；批任务级聚合视图留待将来 |

### 1.2 明确不做（YAGNI）

- **不改 `batch_engine._record_workspace_files`**：收尾仍只 git 变更落库；live-scan 不写回 DB。全局行为不动，"中"（live）与"历史"（DB）保持角色分工。
- **不向 `/api/v1/ai-batches/*` 对外 API 加这套文件端点**：现有对外 `/file-records` + `/import` 已满足集成方诉求，本设计面向内部 admin UI；对外契约多一个减一个容易、加一个难。
- **不做文件级删除/裁剪**：admin 不代用户清理子任务工作区文件；管理员对工作区的写语义留待将来单独设计（写路径泄漏的是数据完整性）。
- **不做游标翻页**：列文件 `_LISTFILES_MAX_FILES=1000` 上限 + `truncated:true`，与 owner 端同口径。
- **不重构 `BatchConversationView.vue`**：文件面板独立成组件，不挤进对话 Tab。理由同 2026-08-12 spec §3.3：两者关注点不同、生命周期不同。

### 1.3 探索中发现的既有事实

这五条不是本设计引入的，但直接影响方案，记录备查：

1. **`record_session_files` 的盲区**（`server/utils/batch_engine.py:602`）：收尾调 `git_changes` 落 `ai_chat_session_files`，而 workspace 根目录的 `.gitignore`（`server/utils/workspace.py:19-30`）屏蔽了 `outputs/` 与 `uploads/`——**agent 写到 `outputs/` 的文件永远不进 DB 记录**。纯 DB 列表会漏掉一整类产出。这是要做 live-scan 而非纯 DB 列表的根本原因。
2. **管理员读不到别人的子任务文件**（`server/routes/ai_chat.py:686` 的 `list_files`）：走 `_load_session_for_user(sid, user['userId'])`，跨用户直接 404。必须新增跨用户读取的 admin 端点，无法复用。
3. **导入白名单只认 DB**（`server/utils/session_file_import.py:34`）：`get_recorded_path(sid, p)` 为 None 即拒。live-scan 看到的 `outputs/` 文件会被 `NOT_RECORDED` 拒掉，与"列得到就导得到"的直觉不符。
4. **下载端点用 `login_required_sse`**（`server/routes/ai_chat.py:989`）：浏览器 `<a href download>` 不能带 `Authorization` 头，故该装饰器额外接受 `?access_token=` 透传 JWT。admin 端要做同样的下载链接，需要一个**带能力闸门**的同构装饰器（现成的只有裸 `login_required_sse` 和裸 `require_permission`，两者都没有组合）。
5. **`authParam` helper 私有在 `src/api/aiChat.ts:298`**：构造 `?access_token=` URL 的逻辑被私有化在 aiChat 模块里。admin 下载链接若抄一份必漂移，需抽到 `src/utils/request.ts` 公共入口。

## 2. 后端设计

### 2.1 新 util：`server/utils/workspace_outputs.py`

把 `server/routes/ai_chat.py:680-736` 那段 `os.walk` 递归扫描逻辑**原样**抽出来：

```python
_LISTFILES_MAX_DEPTH = 8
_LISTFILES_MAX_FILES = 1000
_LISTFILES_SKIP_DIRS = {  # 与 routes/ai_chat.py 同步
    'node_modules', '.venv', '__pycache__', '.git', '.opencode', 'outputs'  # 见下面注 ⚠
}

def list_session_files(workspace_path: str) -> list[dict]:
    """递归扫描 workspace 下的真实文件，返回 [{name, path, dir, size}]。

    - `dir='uploads'`：uploads/ 下直子文件（用户输入）
    - `dir='outputs'`：outputs/ 下递归（agent 输出）
    - `dir='workspace'`：workspace 根或非 outputs 子目录
    排除：嵌套 git 仓库、噪声目录、dotfiles、opencode.json。
    超过 _LISTFILES_MAX_FILES 截断（truncated 由调用方处理）。
    """
```

⚠ **澄清 `_LISTFILES_SKIP_DIRS`**：现有 `routes/ai_chat.py:723` 的 `_LISTFILES_SKIP_DIRS` 集合**不含 `outputs/`**——它故意要列出 outputs 下的文件。抽 util 时照抄原集合，不要误加 `outputs`。`outputs/` 是 `git_changes` 的 `.gitignore` 屏蔽集（`server/utils/workspace_changes.py` 的另一个集合），与 `os.walk` 的跳过集是**两份完全独立的名单**。

**为什么抽 util**：owner 端、admin 端、未来再有 open API 的 file-list 都会走同一段逻辑；留在 routes 里三处必漂移（与 `open_api_batches.py::file_records` 注释「`_validate_files` 必漂移」同一理由）。owner 端改成调 util，行为**完全等价**（同一份 `os.walk` 逻辑搬家），并加一条 unit test 锁住字段集相同。

### 2.2 新 helper：`server/utils/batch_repo.py::admin_get_child_session`

```python
def admin_get_child_session(batch_id: str, session_id: str) -> dict | None:
    """跨用户取单个子任务行的关键列。校验 session 属于该 batch。
    返回 {id, status, workspace_path, ownerUserId} 或 None。

    workspace_path 可能为 NULL（子会话归档/清理后）；调用方据此区分
    files 端点返回空数组 vs preview/download/import 返 400 NO_WORKSPACE。
    """
```

SQL 一条 `JOIN ai_chat_batches ON b.id = s.batch_id`，取齐 `id / status / workspace_path / b.user_id AS "ownerUserId"`。

**为何单独这个函数**：
- 现有 `admin_get_child_messages`（`batch_repo.py:446`）已经做了 `WHERE id=? AND batch_id=?` 校验但只取对话；新 6 个端点都要校验归属 + 拿 `workspace_path` + 拿 owner（用于导入归属），需共享轻量入口。
- 不复用 `admin_get_batch_detail`（拉整批 sessions 的 N+1 重调用）；本场景是单 session 操作。这条与 2026-08-12 spec §2.2 的取舍一脉相承：宁可多一个函数名，也不用模糊的"可空 user_id"变体。

### 2.3 新装饰器：`server/auth.py::require_permission_sse`

与 `login_required_sse`（`auth.py:63`）同构 + 与 `require_permission`（`auth.py:103`）的能力闸门合并：

```python
def require_permission_sse(permission_key):
    """Like require_permission, but also accepts the JWT via ?access_token=
    (browser <a download> / EventSource can't set Authorization header)."""
    def wrapper(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            auth_header = request.headers.get('Authorization', '')
            if auth_header.startswith('Bearer '):
                token = auth_header.split(' ', 1)[1]
            else:
                token = request.args.get('access_token', '')
            if not token:
                return jsonify({'error': '未登录'}), 401
            payload = decode_token(token)
            if not payload:
                return jsonify({'error': '登录已过期'}), 401
            g.current_user = payload
            # 走与 require_permission 相同的能力闸门
            ...  # 见下
        return decorated
    return wrapper
```

**能力闸门实现复用**：`require_permission` 现有实现里"判断用户角色是否持有 `permission_key`"那段（含超管旁路 + 缓存读取）应抽成内部 helper `_user_has_permission(payload, key) -> bool`，让 `require_permission` 和 `require_permission_sse` 都调它，避免两份判断逻辑漂移。这是这次 PR 顺带的小重构——`auth.py` 顶层不出现两处相似代码块。

**不沿用现成 `admin_required`**：CLAUDE.md 明确说 rbac 取代了硬角色，`admin_required` 已是弃用方向；新代码继续建该能力键即可。

### 2.4 端点（`server/routes/ai_batch_admin.py` 新增 6 条）

全部前缀 `/ai/chat/admin/batches/<batch_id>/sessions/<sid>/...`，挂在现成蓝图 `ai_batch_admin_bp`（`url_prefix='/ai/chat/admin/batches'`）上，**不动蓝图注册顺序**（已在 `dynamic_bp` 之前）。每个端点统一鉴权：

```python
from utils.workspace_outputs import list_session_files, LISTFILES_MAX_FILES
from utils.workspace_outputs import augment_with_data_file_id  # 见 §2.4 末尾

@ai_batch_admin_bp.route('/<batch_id>/sessions/<sid>/files')
@require_permission_sse('admin.ai_chat_admin')
def list_child_files(batch_id, sid):
    sess = admin_get_child_session(batch_id, sid)
    if sess is None:
        return jsonify({'error': '子任务不存在', 'code': 'NOT_FOUND'}), 404
    if not sess['workspace_path']:
        return jsonify({'files': [], 'truncated': False})
    files, truncated = list_session_files(sess['workspace_path'])
    augment_with_data_file_id(sid, files)
    return jsonify({'files': files, 'truncated': truncated})
```

`LISTFILES_MAX_FILES`、`LISTFILES_MAX_DEPTH`、`LISTFILES_SKIP_DIRS` 三个常量随之搬到 `workspace_outputs.py`，并 `export`；admin/owner 端点的"是否触发截断"判断一律来自 util 自己返回的 `truncated` 而非再读常量比对（避免二次漂移）。

| 方法 | 蓝图内路径 | 说明 | 复用资源 |
|---|---|---|---|
| GET | `/<batch_id>/sessions/<sid>/files` | 列文件（live scan + LEFT JOIN DB 记录补 `dataFileId`） | `list_session_files` + `_augment_with_data_file_id`（新 helper，见下） |
| GET | `/<batch_id>/sessions/<sid>/files/preview` | 文本预览（封顶，二进制提示下载） | `utils/workspace_changes.read_file_preview` |
| GET | `/<batch_id>/sessions/<sid>/files/download` | 下载（`send_file as_attachment`） | `utils/workspace.safe_resolve` |
| POST | `/<batch_id>/sessions/<sid>/files/import` | 导入 data_files（逐路径 status/error） | `utils/session_file_import.import_recorded_files`（传 `extra_whitelist`） |
| GET | `/<batch_id>/sessions/<sid>/changes` | git 变更文件列表 | `utils/workspace_changes.git_changes` |
| GET | `/<batch_id>/sessions/<sid>/files/diff` | 单文件 diff（modified）/ 封顶内容（added） | `utils/workspace_changes.file_diff` |

**path 参数安全**：preview/download/import/diff 都 `safe_resolve(sess['workspace_path'], rel)`，越界 `WorkspacePathError` → 400 `BAD_PATH`。与 owner 端同一闸门。

**`_augment_with_data_file_id` 新 helper**（放在 `workspace_outputs.py` 或 `batch_repo.py`，倾向前者，紧挨 `list_session_files`）：

```python
def augment_with_data_file_id(session_id: str, files: list[dict]) -> None:
    """原地给每个文件项加 dataFileId（来自 ai_chat_session_files.data_file_id）。
    没有记录的为 None；live scan 没出现的旧 DB 记录不补（调用方只看到 live）。
    一次 SELECT path, data_file_id WHERE session_id=? 拉回，构造 {path: dfid}
    字典后 in-place fill。"""
```

为何 LEFT JOIN 不直接写进 `list_session_files`：util 该与 DB 无关（未来若有非会话用途的扫描也能用），让 admin 端点做组装。

### 2.5 导入白名单扩展（关键技术变更）

`server/utils/session_file_import.import_recorded_files` 加可选参数：

```python
def import_recorded_files(session_id, workspace_path, paths, uploaded_by=None,
                          extra_whitelist: set[str] | None = None):
    ...
    for p in paths:
        rec = get_recorded_path(session_id, p)
        if rec is None and (not extra_whitelist or p not in extra_whitelist):
            results.append({'path': p, 'error': '该文件不在会话的文件记录中',
                            'code': 'NOT_RECORDED'})
            continue
        if rec and rec['dataFileId']:  # 幂等：已导入过且 data_file 还在
            meta = data_file_meta(rec['dataFileId'])
            if meta:
                results.append({'path': p, 'status': 'existing', 'file': meta})
                continue
        # 在 live 集合里但 DB 没记录 → 仍要 safe_resolve + 文件存在检查 + save_workspace_file
        # 导入成功后回填 set_data_file_id（让下回也走 DB 白名单的快路径）
        ...
        set_data_file_id(session_id, p, meta['id'])
```

**语义边界**：
- owner 端 `routes/ai_chat.py:769` 调时不传 `extra_whitelist` → 行为完全不变，旧回归测试不动。
- 对外 Open API `routes/open_api_batches.py:343` 调时不传 → 行为不变，对外契约稳定。
- admin 端调时传 `set(f['path'] for f in list_session_files(ws))` → `outputs/` 文件被放行。

**回填效果**：admin 导入一次 `outputs/foo.txt` 后，`set_data_file_id` 落库；下次 DB 白名单也命中该路径，不再依赖 live 集合（live 文件可能后来被 agent 删了，但 DB 已绑定 data_file_id → 不必再走 live）。

### 2.6 导入归属

```python
# admin import endpoint
sess = admin_get_child_session(batch_id, sid)
owner = sess['ownerUserId']  # 来自 §2.2 的 helper
results = import_recorded_files(
    sess['id'], sess['workspace_path'], paths,
    uploaded_by=owner,                              # 归属原 owner，不是管理员
    extra_whitelist={f['path'] for f in list_session_files(sess['workspace_path'])},
)
# 审计：记管理员（发起人），不是 owner
imported_ids = [r['file']['id'] for r in results
                if r.get('status') in ('imported', 'existing') and r.get('file')]
log_operation(
    'create', 'data_file',
    target_id=';'.join(imported_ids) if imported_ids else None,
    target_name=f'批任务 {batch_id} 子任务 {sid}',
    detail=f'管理员代导入 {len(imported_ids)} 个产出文件（归属 {owner}）',
    operator=flask_g.current_user['userId'],
)
```

`uploaded_by` 与 `operator` 是两个独立字段：前者是数据归属（谁名下的文件），后者是动作发起人（谁点的按钮）。这与 2026-08-12 spec §2.5「管理员代改他人数据必须留痕」同一原则。

`log_operation` 若现有签名不支持 `operator=` 参数，**先在本次 PR 内扩展签名**（向后兼容默认 None = 取 `g.current_user`），不要绕开它手写一条 SQL——审计路径必须统一。

### 2.7 owner 端 refactor

`server/routes/ai_chat.py:686` 的 `list_files` 改成：

```python
from utils.workspace_outputs import list_session_files
if not workspace_path:   # batch child sessions have no workspace dir
    return jsonify({'files': [], 'truncated': False})
out, truncated = list_session_files(workspace_path)
return jsonify({'files': out, 'truncated': truncated})
```

`list_session_files` 同时改成 **返回 `(list, truncated)` tuple**（更直白，免去调用方再判一次），并补一条 unit test 锁住"两边端点字段集相同 + 行为等价"。检查 workspace 必须在调 util 之前，否则 `os.walk(None)` 崩。

owner 端的 `route/ai_chat.py::import_session_files`（769-789）**不改**：仍走纯 DB 白名单；admin 才有 `extra_whitelist`。owner 用户工作区里 `outputs/` 的文件本就可在普通聊天侧 UI 看到下载，导入是否也开放给 owner 自助是另一个产品决策，不在本次范围。

## 3. 前端设计

### 3.1 入口

`src/views/admin/AiBatchAdmin.vue` 子任务表的「操作」列（现含「查看对话」「重跑」）追加：

```vue
<el-button link type="primary" @click="openChildFiles(row)">产出文件</el-button>
<el-button link type="primary" @click="openConversation(row)">查看对话</el-button>
<el-button link type="warning" :disabled="!isTerminal(row.status)" @click="onReexecute(row)">重跑</el-button>
```

点击 `openChildFiles` 打开新 `el-dialog`（与 `convOpen` 平级，不嵌套；命名 `filesOpen`）。

### 3.2 新组件 `src/components/ai-chat/AdminBatchFiles.vue`

不挤进 `BatchConversationView` 的 Tab。理由已在 §1.2：文件是独立诉求，不必先钻对话才看到。

**结构**：

- 顶部工具条：
  - 左 = 子任务标识（输入文件名 + seq）
  - 右 = `全选` checkbox + `导入到 data_files` 按钮（批量，disabled 当无选中）
- 主体：按目录分组的折叠列表，复刻 `AiChatView.vue:779-797` 的产出文件布局。每行：
  - 文件名 + size（`大小.toFixed(1) KB`）
  - 「预览」按钮 → 弹出嵌套 `el-dialog`（文本 cap 展示，二进制提示下载）
  - 「下载」链接（`<a href="...?access_token=..." target="_blank" rel="noopener">`，下载走 `?access_token=` 透传 JWT）
  - checkbox（不可重复点：`dataFileId` 非空的行尾加灰色「已导入」标签 + checkbox disabled）
  - 图片缩略图（isImageFile → `<img>` 行内，沿用 AiChatView 同判断）
- 加载中 / 空列表 / 错误状态文案与现有对话查看器统一风格（`el-empty`、`el-alert`）

**Props**：`{batchId, sessionId, loading, files, truncated}`，事件 `preview(path)` / `refresh()` / `import(paths)`。父页面 `AiBatchAdmin.vue` 拿数据、传 props，组件纯渲染——与 `BatchConversationView.vue` 的解耦方式一致。

### 3.3 API 层：`src/api/aiBatchAdmin.ts` 新增 4 个函数

```ts
export function listAdminChildFiles(batchId: string, sessionId: string) {
  return get<{ files: AdminChildFile[]; truncated: boolean }>(
    `${BASE}/${batchId}/sessions/${sessionId}/files`)
}

export function getAdminChildFilePreview(batchId: string, sessionId: string, path: string) {
  return get<{ content: string; truncated: boolean }>(
    `${BASE}/${batchId}/sessions/${sessionId}/files/preview`, { path })
}

export function importAdminChildFiles(batchId: string, sessionId: string, paths: string[]) {
  return post<{ results: AdminImportResult[] }>(
    `${BASE}/${batchId}/sessions/${sessionId}/files/import`, { paths })
}

export function adminChildFileDownloadUrl(batchId: string, sessionId: string, path: string): string {
  // 返回字符串 URL，不是 axios 请求 —— 浏览器 <a href> 直下载
  return `/api${BASE}/${encodeURIComponent(batchId)}/sessions/${encodeURIComponent(sessionId)}`
       + `/files/download?path=${encodeURIComponent(path)}${authParam('&')}`
}
```

`AdminChildFile` / `AdminImportResult` 类型同字段集见 §6 文件清单。

### 3.4 公共 helper：`src/utils/request.ts` 抽 `authParam`

`src/api/aiChat.ts:298` 的私有 `authParam` 抽到 `src/utils/request.ts`，**两处共用**（aiChat.ts 改为 import 它，行为不变）。否则 admin 下载链接抄一份 `authParam` 必漂移——用户改 token 存储格式（已经走过一次 rapping）改一处即可。

### 3.5 Store 层

`src/stores/aiBatchAdmin.ts` 不需要扩展：文件相关 state 是打开 dialog 时一次拉取的临时数据，放在 `AiBatchAdmin.vue` 局部 ref 即可（与 `convMessages`/`convName`/`convOpen` 等并行），不进 store。轮询不涉及文件状态，沿用现有 `store.startPolling()` 不改。

### 3.6 交互行为

- 打开 dialog：先清空 `files.value=[]`、`filesLoading.value=true`，调用 `listAdminChildFiles` 落盘 → `filesLoading=false`
- 「预览」按钮：调 `getAdminChildFilePreview`，结果在嵌套 `el-dialog` 展示（cap 文本带 `truncated` 提示）。二进制文件后端会返回特定提示文案，前端按字面展示
- 「下载」链接：直接构造 `adminChildFileDownloadUrl` 作 `href`，浏览器原生处理下载
- 「导入到 data_files」：收集选中 checkbox 的 `path` 数组 → 调 `importAdminChildFiles` → 返回逐路径 `results`：
  - `status==='imported'` / `existing`：对应行尾换「已导入」灰标签 + checkbox disabled
  - 单条 `error`：toast 红字（如 `FILE_MISSING`、`TOO_LARGE`），不整体回滚成功的；`NOT_RECORDED` 期望不出现（白名单已并集挡掉）但保留容错
- 导入完成后**调用 `listAdminChildFiles` 重新拉列表**，确认 `dataFileId` 已回填到 UI

## 4. 边界情况与错误处理

### 4.1 错误码全表

admin 端点统一返回 `{error, code?: string}`：

| 场景 | HTTP | code |
|---|---|---|
| session 不属于该 batch / batch 不存在 | 404 | `NOT_FOUND` |
| `workspace_path` 为空（清理/归档后） | files: `{files:[], truncated:false}`；preview/download/import: 400 | `NO_WORKSPACE` |
| `path` 越界 / 绝对路径 | 400 | `BAD_PATH` |
| 文件已删（live scan 没看到） | download: 404；import 单条 error | download `FILE_NOT_FOUND` / import `FILE_MISSING` |
| 导入单文件失败 | 不整体回滚，单条 `/IMPORT_FAILED` `/TOO_LARGE` | 见 `import_recorded_files` 既有协议 |
| `paths` 空 / 超 100 | 400 | `PATHS_REQUIRED` / `TOO_MANY` |
| 管理员被撤销 `admin.ai_chat_admin` 能力 | 403 | 与现有一致 |

### 4.2 性能约束

- `list_session_files` 复用 `_LISTFILES_MAX_FILES=1000` + `_LISTFILES_MAX_DEPTH=8`，触发截断 `truncated:true`。
- 一次批量导入 ≤ `MAX_IMPORT_PATHS=100`（沿用现有常量），单文件**串行**导入（不并发跑 `save_workspace_file`）避免 IO 抢占 + DB 事务冲突；与现有 `import_recorded_files` 同步合同一致。
- `augment_with_data_file_id` 单条 `SELECT path, data_file_id WHERE session_id=?`，构造 `{path: dfid}` 字典后原地 fill，**不**逐文件 N+1 查。

### 4.3 子任务工作区被异步清理

场景：管理员刚打开 dialog 拿到 `files=[...]`，期间子任务会话被 owner `clear` / `delete`（CLAUDE.md AI Agent Chat 段）触发 `rmtree(workspace_path)`。

- 此刻点下载链接后端 `safe_resolve` OK 但 `os.path.isfile` False → 404 `FILE_NOT_FOUND`，前端 toast 红字「文件已不存在」。
- 此刻点导入 → `import_recorded_files` 里 `safe_resolve` 仍 OK（路径在 workspace 根下），但 `os.path.isfile` False → 单条 `FILE_MISSING`，`NOT_RECORDED` 不出现（extra_whitelist 是打开 dialog 时拉的快照，含曾存在的路径）。
- 列表刷新（手动 `reload`）会重新拉 live scan，已消失的文件就不再出现。

**不补"被清空的 toast 提示"**：列表变空本身就是信号；过度提示是噪音。

### 4.4 并发导入

管理员与 owner 同时导入同一路径：两边都走 `get_recorded_path` → 都见无 `data_file_id`（或一边见到，一边没见到，依发生顺序）。可能发生：

- 两边都过白名单 → 都调 `save_workspace_file` → 两份 `data_file` 实体 + 两个 storage 文件，但 `set_data_file_id` 后者覆盖前者 → DB 里只见一份，磁盘上多一份垃圾。
- **不设防**：与现 owner 端 + 对外 API 端同样不设防。导入属低频管理员动作，发生冲突时磁盘多一份垃圾可接受，比加行级锁的代价低。文档不入歧义项，留作已知限制。

### 4.5 鉴权中途失效

管理员被撤销 `admin.ai_chat_admin` 后接口返 403。dialog 已打开的旧数据仍可见但点击下载/导入会失败。toast 提示，**不强制关 dialog**；用户可自行关闭。与 2026-08-12 spec §4.4 一致。

### 4.6 `proxy.py` 验证（CLAUDE.md 强约束）

新端点没有 SSE、没有自定义请求头（`Authorization` 头或 `?access_token=` 都在 forward allowlist 内）。所以：
- **不需要修 `proxy.py::_is_sse()`**（响应都是 JSON / send_file / 文本预览，非 `text/event-stream`）。
- **不需要扩 `_proxy_to_backend` 的 forward header list**（无新自定义请求头）。
- 但仍必须执行验证：`npm run build` → `cd server && python proxy.py` → 经 `:8080` 跑一遍列文件 + 下载 + 导入，确认经 proxy 可达且未遭 `dynamic_bp` catch-all 抢占（虽蓝图注册顺序已保证，proxy 层手动复核一次）。

## 5. 测试与验证

### 5.1 后端（pytest）

新增 `server/tests/test_ai_batch_admin_files.py`。需要特别构造的：

1. **权限门参数化遍历路由表**：遍历 `app.url_map` 中 `/ai/chat/admin/batches/.../files*` / `/changes` / `/diff` 全部规则，逐个断言**非持有 `admin.ai_chat_admin` 角色**得 403。沿用 2026-08-12 spec §5.1 的形式 —— 上个分支的审查发现过 `require_bound_key` 只有一个端点有测试、其余漏挂装饰器不会被发现。6 个新端点忘挂 `require_permission_sse` 时本测试立刻变红。
2. **`require_permission_sse` 同时支持 `Authorization` 头与 `?access_token=`**：分别两条断言；显式测一个被撤销能力的 token 在两种携带方式下都得 403。
3. **跨用户可见性**：用户 A 登录调 admin 端点取用户 B 的子任务文件 → 200（管理员视角语义相反路径，必须正向钉住）。
4. **`admin_get_child_session` 的归属校验**：构造不属于该 batch 的 `sid` → 404 `NOT_FOUND`；`workspace_path=NULL` 的子任务 → `files` 端点返空、`preview/download/import` 返 400 `NO_WORKSPACE`。
5. **路径安全**：preview/download/import/diff 的 `path=path-traversal-spec../../../etc/passwd` / `path=/etc/passwd`（绝对）→ 400 `BAD_PATH`。
6. **白名单扩展正向**：构造一个 `outputs/foo.txt`（DB 无记录）→ owner 端的 `import_session_files` 拒绝 `NOT_RECORDED`（回归行为不变），admin 端的 `import_files` 接受并返回 `imported`。**两边行为差异必须各有一条测试钉住**，否则日后有人"统一一下"会悄改语义。
7. **import 幂等**：导入同一路径两次 → 第二次返 `existing`，不重复写 `data_files`。
8. **import 串行 + 不回滚**：构造 3 个 paths，其中一个文件已被删 → 返回 3 条 results，被删那条 `FILE_MISSING`，另两条 `imported` 已落库（断言 DB row count 与 toast 一致）。
9. **`uploaded_by` 是 owner 不是管理员**：导入后查 `data_files.uploaded_by` 等于批任务的 `user_id`；同时 `operation_logs` 里有管理员发起的 row，`operator` 是管理员 userId。
10. **`list_session_files` 抽取后的回归**：owner 端 `/sessions/<sid>/files` 的字段集与 admin 端 `/ai/chat/admin/batches/<bid>/sessions/<sid>/files` 字段集一致（仅 admin 多 `dataFileId`），用一组构造 fixtures 跑两端点，比较同名键集合。
11. **`augment_with_data_file_id` 不 N+1**：单测对含 50 个 file 项、其中 10 个有 DB 记录的构造，断言只发了一条 `SELECT ... WHERE session_id=?`（用 psycopg2 的 mock 计数 cursor.execute 调用）。这条是显式性能契约，非可选。

其余：files 截断 `truncated=true`、preview 二进制提示、download `Content-Disposition: attachment`、changes/diff 返回与 owner 端字段等价。

### 5.2 前端（vitest）

`src/components/ai-chat/__tests__/AdminBatchFiles.test.ts`：

- `authParam` helper 抽出后，aiChat 与 aiBatchAdmin 的 `downloadFileUrl / adminChildFileDownloadUrl` 各自的回归测试断言 URL 含 `access_token=...`
- 选中 checkbox 数量正确驱动「导入到 data_files」按钮 disabled / enabled
- `dataFileId` 非空的行 checkbox disabled + 显示「已导入」
- 导入结果映射：成功行 → 灰标签 + checkbox disabled；失败行 → 行尾红字 toast（mock API 返多种 status，断言渲染分支）
- 列表加载中态 + 截断提示文案

`AiBatchAdmin.vue` 的 store/筛选测试不动；新增"打开文件弹窗的按钮总算在子任务行"这条断言钉住入口可见。

### 5.3 Playwright（CLAUDE.md 强制）

admin 登录（admin/admin123）→ `/admin/ai-batches` → 选一个**已完成**的批任务（若无则先在普通助手侧建一个跑完）→ 子任务行点「产出文件」→ 弹窗出现文件列表 → 下载一个文件确认保存 → 勾选若干 + 点「导入到 data_files」→ toast 成功 → 去 `/admin/data-files`（或 data_files 列表所在位置）确认文件已入库且 `uploaded_by` 字段是批 owner（不是 admin）→ 回 admin 批管理 dialog 确认行尾变成「已导入」灰标签。截图存入 `.playwright-mcp/`。

**回查 DB**：导入后用一段 psql 断言 `data_files.uploaded_by = <owner_user_id>` 且 `ai_chat_session_files.data_file_id IS NOT NULL` —— 不只看 UI 的绿色 toast。

### 5.4 proxy.py 验证（CLAUDE.md 强制）

虽 §4.6 已声明不需要改 `_is_sse()` / `forward header list`，仍强制跑：

`npm run build` → `cd server && python proxy.py` → 在 `:8080` 重跑列文件 + 下载（带 `?access_token=`）+ 导入三步骤。下载链接必须经 proxy 走通（特别测一次不带 `Authorization` 头纯靠 query token 的下载，对应 `<a href>` 的实际行为）。

### 5.5 文档同步（CLAUDE.md 强制）

`docs/user-guide/ai/batch-tasks.md` 在「管理员：批任务管理页」一节（由 2026-08-12 spec §5.5 引入）内追加「产出文件」子节，含真实 dialog 截图（存 `docs/user-guide/_images/`）。介绍：

- 入口位置（子任务行的「产出文件」按钮）
- 文件来源（live 扫描工作区 + 已导入标记）
- 预览 / 下载 / 导入到 data_files 三种动作
- 导入归属语义（`uploaded_by = 原 owner`，不是管理员 — 这是审计与所有权相关的重要说明）

## 6. 文件清单

**新建**

| 文件 | 职责 |
|---|---|
| `server/utils/workspace_outputs.py` | `list_session_files` + `augment_with_data_file_id`（§2.1） |
| `server/tests/test_ai_batch_admin_files.py` | 后端测试（§5.1） |
| `src/components/ai-chat/AdminBatchFiles.vue` | 文件 dialog 组件（§3.2） |
| `src/components/ai-chat/__tests__/AdminBatchFiles.test.ts` | 前端组件测试（§5.2） |

**修改**

| 文件 | 改动 |
|---|---|
| `server/routes/ai_batch_admin.py` | 新增 6 个 file/changes/diff 端点（§2.4）+ 导入归属与审计（§2.6） |
| `server/utils/batch_repo.py` | 新增 `admin_get_child_session`（§2.2） |
| `server/auth.py` | 新增 `require_permission_sse` + 抽 `_user_has_permission` 内部 helper（§2.3） |
| `server/utils/session_file_import.py` | `import_recorded_files` 加 `extra_whitelist` 参数（§2.5） |
| `server/routes/ai_chat.py` | `list_files` 改成调 `list_session_files` util（§2.7）；owner 端 `import_session_files` 不动 |
| `src/api/aiBatchAdmin.ts` | 新增 `listAdminChildFiles` / `getAdminChildFilePreview` / `importAdminChildFiles` / `adminChildFileDownloadUrl` + 新类型（§3.3） |
| `src/api/aiChat.ts` | `authParam` 改 import from `src/utils/request.ts`（§3.4，行为不变） |
| `src/utils/request.ts` | 抽出 `authParam` + export（§3.4） |
| `src/views/admin/AiBatchAdmin.vue` | 子任务行加「产出文件」按钮 + 文件 dialog 状态 + 接线（§3.1, §3.6） |
| `docs/user-guide/ai/batch-tasks.md` | 管理员一节追加「产出文件」子节 + 截图（§5.5） |
| `CLAUDE.md` | 在「AI 批任务管理」段落补"产出文件能力（管理员跨用户预览/下载/导入）"一行 |

**不动**

- `server/utils/batch_engine.py`（`_record_workspace_files` 行为不变，§1.3-1）
- `server/utils/workspace_changes.py`（`record_session_files` / `git_changes` / `read_file_preview` / `file_diff` 全部不动；admin 只复用不修改）
- `server/utils/workspace.py`（`safe_resolve` 不动）
- `server/routes/open_api_batches.py`（对外 API 行为不动，§1.2 + §2.5）
- `server/routes/ai_chat_batches.py`（内部 owner 端批任务不变）
- `server/app.py`（新端点都挂在已注册的 `ai_batch_admin_bp` 上，不需新增蓝图注册）
- `server/proxy.py`（无需改 `_is_sse()` / forward header list，§4.6）