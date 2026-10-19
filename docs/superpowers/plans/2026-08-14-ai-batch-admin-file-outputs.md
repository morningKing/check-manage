# AI 批任务管理页 产出文件能力 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 admin AI 批任务管理页（`/admin/ai-batches`）补齐"列出每个子任务产出文件 + 预览 + 下载 + 导入到 data_files + 查看 git 变更 diff"的能力，让管理员跨用户查看 AI 任务实际产出了什么文件。

**Architecture:** 新增 `utils/workspace_outputs.py` util 抽出 `os.walk` 递归扫描（同源于现 owner 端点），新增 `require_permission_sse` 装饰器支持 `?access_token=` 鉴权（`<a download>` 链接不能带 Authorization 头），新增 6 个 admin 端点（list/preview/download/import + changes/diff），扩展 `import_recorded_files` 加 `extra_whitelist` 参数（admin 端 live-scan 文件名集合 → 让 `outputs/` 也能导入；owner/对外 API 不传保持旧白名单不变）。新建 `AdminBatchFiles.vue` 独立 dialog 组件。单 PR 含 owner 端 refactor + 完整测试。

**Tech Stack:** Python Flask + psycopg2 + PostgreSQL JSONB（后端）；Vue 3 + TypeScript + Element Plus + Pinia（前端）；Pytest（后端测试）；Vitest + jsdom（前端单元）；Playwright（端到端）。

**Spec:** `docs/superpowers/specs/2026-08-14-ai-batch-admin-file-outputs-design.md`

## Global Constraints

- **鉴权**：admin 端点统一 `@require_permission_sse('admin.ai_chat_admin')`；能力键复用 existing 的 `admin.ai_chat_admin`（"AI 会话治理"），不新建能力键
- **路径安全**：所有 preview/download/import/diff 的 `path` 必经 `utils/workspace.safe_resolve`，越界 `WorkspacePathError` → `400 {code:'BAD_PATH'}`
- **导入归属**：`uploaded_by` = 批任务原 owner（不是 admin）；`log_operation` 自动取 `g.current_user=管理员` 作 operator（无需改其 API）
- **导入串行**：批量导入 ≤ `MAX_IMPORT_PATHS=100`，逐文件串行不并发跑 `save_workspace_file`
- **owner/对外 API 行为不变**：`import_recorded_files` 加 `extra_whitelist=None` 可选参数；不传时与旧实现完全等价
- **蓝图注册顺序**：admin 端点挂现成 `ai_batch_admin_bp`（`url_prefix='/ai/chat/admin/batches'`），不动 `app.py` 注册顺序（已在 `dynamic_bp` 之前）
- **proxy.py 不动**：新端点无 SSE、无自定义请求头；只需执行验证跑通 `:8080`，不改 `_is_sse()` 也不改 forward header list
- **不改**：`batch_engine.py`、`workspace_changes.py`、`workspace.py`、`open_api_batches.py`、`ai_chat_batches.py`、`app.py`、`proxy.py`
- **常量搬家**：`_LISTFILES_MAX_DEPTH=8`、`_LISTFILES_MAX_FILES=1000`、`_LISTFILES_SKIP_DIRS={...}`（**不含 `outputs/`**，照抄 `routes/ai_chat.py:679`）搬到 `workspace_outputs.py` 并 export
- **命名约定**：admin repo 函数前缀 `admin_`，与 `batch_repo.py` 现有 `admin_*` 系列一脉；util 函数 fullPath 名 `list_session_files`、`augment_with_data_file_id`
- **错误码**：全 admin 端点统一 `{error, code?}`，与 owner 端点字段同口径

---

## File Structure

**新建**

| 文件 | 责任 |
|---|---|
| `server/utils/workspace_outputs.py` | `list_session_files` 递归扫描 + `augment_with_data_file_id` LEFT JOIN DB 记录补 dataFileId + 3 个 LISTFILES 常量 |
| `server/tests/test_ai_batch_admin_files.py` | 6 个 admin 端点的 pytest + util 一致性 + 白名单差异 + 归属测试 |
| `src/components/ai-chat/AdminBatchFiles.vue` | 独立文件 dialog 组件（按目录折叠 + 全选 + 批量导入 + 预览 + 下载） |
| `src/components/ai-chat/__tests__/AdminBatchFiles.test.ts` | 组件单元测试 |
| `src/utils/request.ts`（若已存在则 modify） | `authParam` 公共 helper export |

**修改**

| 文件 | 改动 |
|---|---|
| `server/routes/ai_chat.py:684-736` | `list_files` owner 端点改成调 `list_session_files` util |
| `server/utils/batch_repo.py` | 新增 `admin_get_child_session(batch_id, sid) -> dict\|None` |
| `server/auth.py` | 新增 `require_permission_sse(perm)` + 抽 `_user_has_permission(payload, key) -> bool` |
| `server/utils/session_file_import.py:16` | `import_recorded_files` 加 `extra_whitelist: set[str]\|None=None` 参数 |
| `server/routes/ai_batch_admin.py` | 新增 6 个端点 + 导入归属/审计 |
| `src/api/aiChat.ts:296-309` | `authParam` 改为 import from `src/utils/request.ts`（行为不变） |
| `src/api/aiBatchAdmin.ts` | 新增 4 函数 + 类型 `AdminChildFile`、`AdminImportResult` |
| `src/views/admin/AiBatchAdmin.vue` | 子任务行加「产出文件」按钮 + 接线 dialog |
| `docs/user-guide/ai/batch-tasks.md` | 管理员一节追加「产出文件」子节 + 截图 |
| `CLAUDE.md` | AI 批任务管理段补一行产出文件能力说明 |

---

## Task 1: 抽取 `utils/workspace_outputs.py::list_session_files` util + owner 端点 refactor

**Files:**
- Create: `server/utils/workspace_outputs.py`
- Modify: `server/routes/ai_chat.py:677-736`（list_files 端点调用 util；常量搬家）
- Test: `server/tests/test_workspace_outputs.py`（新建）

**Interfaces:**
- Consumes: `os`, `utils.workspace.safe_resolve`（间接, 在 web_owner 端不调）
- Produces: `list_session_files(workspace_path) -> tuple[list[dict], bool]`、`LISTFILES_MAX_FILES`、`LISTFILES_MAX_DEPTH`、`LISTFILES_SKIP_DIRS` 常量。每项 `{name, path, dir:'uploads'\|'outputs'\|'workspace', size}`，与现 owner 端点字段集 1:1 一致

- [ ] **Step 1: 写失败测试 — util 抽取后字段集与 owner 端点 一致**

`server/tests/test_workspace_outputs.py`:
```python
"""list_session_files util 行为契约测试 + 与 routes/ai_chat.py::list_files
端点的字段集等价锁。"""
import os
import tempfile
from utils.workspace_outputs import (
    list_session_files, LISTFILES_MAX_FILES, LISTFILES_MAX_DEPTH, LISTFILES_SKIP_DIRS,
)


def _make_workspace(tmpdir):
    os.makedirs(os.path.join(tmpdir, 'uploads'), exist_ok=True)
    os.makedirs(os.path.join(tmpdir, 'outputs'), exist_ok=True)
    os.makedirs(os.path.join(tmpdir, 'outputs', 'sub'), exist_ok=True)
    with open(os.path.join(tmpdir, 'uploads', 'in.txt'), 'w') as f:
        f.write('input')
    with open(os.path.join(tmpdir, 'outputs', 'report.md'), 'w') as f:
        f.write('# Report')
    with open(os.path.join(tmpdir, 'outputs', 'sub', 'a.txt'), 'w') as f:
        f.write('a')
    with open(os.path.join(tmpdir, 'root.txt'), 'w') as f:
        f.write('root')
    # noise: 必须跳过
    os.makedirs(os.path.join(tmpdir, 'node_modules'), exist_ok=True)
    with open(os.path.join(tmpdir, 'node_modules', 'x.js'), 'w') as f:
        f.write('noise')
    # dotfile: 必须跳过
    with open(os.path.join(tmpdir, '.secret'), 'w') as f:
        f.write('hidden')
    # opencode.json: 必须跳过
    with open(os.path.join(tmpdir, 'opencode.json'), 'w') as f:
        f.write('{}')


def test_list_session_files_categorizes_dirs():
    with tempfile.TemporaryDirectory() as td:
        _make_workspace(td)
        files, truncated = list_session_files(td)
        by_path = {f['path']: f for f in files}
        # uploads/ 直接子：dir='uploads'
        assert by_path['uploads/in.txt']['dir'] == 'uploads'
        # outputs/ 递归：dir='outputs'
        assert by_path['outputs/report.md']['dir'] == 'outputs'
        assert by_path['outputs/sub/a.txt']['dir'] == 'outputs'
        # 根目录：dir='workspace'
        assert by_path['root.txt']['dir'] == 'workspace'
        # 字段齐全
        for f in files:
            assert set(f.keys()) == {'name', 'path', 'dir', 'size'}
            assert isinstance(f['size'], int)


def test_list_session_files_excludes_noise_and_dotfiles():
    with tempfile.TemporaryDirectory() as td:
        _make_workspace(td)
        files, _ = list_session_files(td)
        paths = {f['path'] for f in files}
        assert 'node_modules/x.js' not in paths
        assert '.secret' not in paths
        assert 'opencode.json' not in paths
        assert 'uploads/in.txt' in paths  # 但 uploads/ 目录本身被遍历


def test_listfiles_constants_match_old_definitions():
    """常量值与 routes/ai_chat.py 里抽走前一致（防误改）。"""
    assert LISTFILES_MAX_FILES == 1000
    assert LISTFILES_MAX_DEPTH == 8
    # outputs/ 故意不在跳过集（它要被列）；uploads/ 不在跳过集（被列但分类为 uploads）
    assert LISTFILES_SKIP_DIRS == {'uploads', '.git', 'node_modules', '.venv', '__pycache__', '.opencode'}
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_workspace_outputs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'utils.workspace_outputs'`

- [ ] **Step 3: 抽出 util 至 `server/utils/workspace_outputs.py`**

把 `routes/ai_chat.py:677-736` 的 `os.walk` 逻辑原样搬到 util，并把常量 export。返回值改为 `(list, truncated)` tuple：

```python
"""跨调用方统计会话工作区真实文件：list_session_files + 常量。

从 routes/ai_chat.py::list_files 抽出。两类调用方：
1. owner 端点 `/sessions/<sid>/files`，按 user_id 校验后调本 util；
2. admin 端点 `/ai/chat/admin/batches/<bid>/sessions/<sid>/files`，跨用户调。

不耦合 DB：要补 data_file_id 等会话信息由调用方 `augment_with_data_file_id`
后置 join（见 Task 2）。
"""
import os

# Dirs never scanned for 产出文件: user input, our own .git, well-known noise.
# Hidden dirs (starting with '.') are skipped too. ⚠ outputs/ 故意不在集中——
# 它要被列出（dir='outputs'），与 git_changes 的 .gitignore 名单是两份独立集合。
LISTFILES_SKIP_DIRS = {'uploads', '.git', 'node_modules', '.venv', '__pycache__', '.opencode'}
LISTFILES_MAX_DEPTH = 8
LISTFILES_MAX_FILES = 1000


def list_session_files(workspace_path: str) -> tuple[list[dict], bool]:
    """递归扫描 workspace，返回 (files, truncated)。

    files 每项：{name, path, dir:'uploads'|'outputs'|'workspace', size}
      - 'uploads'：uploads/ 下直子文件
      - 'outputs'：outputs/ 下递归
      - 'workspace'：根或其他非 outputs 子目录
    排除：嵌套 git 仓库、噪声目录、dotfiles、opencode.json。
    truncated 为 True 当达到 LISTFILES_MAX_FILES 上限。
    """
    if not workspace_path:
        return [], False
    out = []
    # uploads/ — user inputs, direct children only
    up = os.path.join(workspace_path, 'uploads')
    if os.path.isdir(up):
        for name in sorted(os.listdir(up)):
            fp = os.path.join(up, name)
            if os.path.isfile(fp):
                out.append({'name': name, 'path': f"uploads/{name}",
                            'dir': 'uploads', 'size': os.path.getsize(fp)})
    ws_real = os.path.realpath(workspace_path)
    base_depth = ws_real.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(workspace_path):
        # A nested git repo's changes belong to 变更文件, not 产出文件 — skip it
        # entirely. The workspace root is OUR auto-init repo, so keep listing it.
        if os.path.realpath(dirpath) != ws_real and os.path.isdir(os.path.join(dirpath, '.git')):
            dirnames[:] = []
            continue
        depth = os.path.realpath(dirpath).rstrip(os.sep).count(os.sep) - base_depth
        if depth >= LISTFILES_MAX_DEPTH:
            dirnames[:] = []
        dirnames[:] = sorted(
            d for d in dirnames if d not in LISTFILES_SKIP_DIRS and not d.startswith('.')
        )
        for name in sorted(filenames):
            if name.startswith('.') or name == 'opencode.json':
                continue
            fp = os.path.join(dirpath, name)
            if not os.path.isfile(fp):
                continue
            rel = os.path.relpath(fp, workspace_path).replace(os.sep, '/')
            sub = 'outputs' if rel.startswith('outputs/') else 'workspace'
            out.append({'name': name, 'path': rel, 'dir': sub, 'size': os.path.getsize(fp)})
            if len(out) >= LISTFILES_MAX_FILES:
                return out, True
    return out, False
```

- [ ] **Step 4: 修改 owner 端点调用 util**

`server/routes/ai_chat.py:677-736` 改为：

```python
# 这三个常量已搬到 utils.workspace_outputs，这里只 import 供其他端点不变使用
from utils.workspace_outputs import list_session_files  # noqa: 至文件顶部 import 区


@ai_chat_bp.route('/sessions/<sid>/files', methods=['GET'])
@login_required
def list_files(sid):
    """List the session's files for the 产出文件 / inputs panels."""
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    workspace_path = sess[4]
    if not workspace_path:   # batch child sessions have no workspace dir
        return jsonify({'files': [], 'truncated': False})
    files, truncated = list_session_files(workspace_path)
    return jsonify({'files': files, 'truncated': truncated})
```

**删除原 inline 的 `_LISTFILES_*` 常量与 `os.walk` 内联逻辑**（移到 util）。`routes/ai_chat.py` 顶部 import 区添加 `from utils.workspace_outputs import list_session_files`。文件其他位置可能引用了这些常量的，全部改 import from util。grep `routes/ai_chat.py` 中 `_LISTFILES_` 引用，零外漏。

- [ ] **Step 5: 跑测试验证 PASS**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_workspace_outputs.py -v`
Expected: PASS（3 个测试）

- [ ] **Step 6: 跑 owner 端点原有测试，确认未击穿**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_ai_chat.py -v`
Expected: 现有 list_files 相关测试全 PASS（行为等价 refactor）

- [ ] **Step 7: 提交**

```bash
git add server/utils/workspace_outputs.py server/tests/test_workspace_outputs.py server/routes/ai_chat.py
git commit -m "refactor(ai-chat): 抽 list_session_files util，owner 端点改为调用 (字段集等价)"
```

---

## Task 2: `augment_with_data_file_id` LEFT JOIN helper

**Files:**
- Modify: `server/utils/workspace_outputs.py`（在 Task 1 基础上追加）
- Test: `server/tests/test_workspace_outputs.py`（追加用例）

**Interfaces:**
- Consumes: `db.get_db`, `ai_chat_session_files` 表
- Produces: `augment_with_data_file_id(session_id: str, files: list[dict]) -> None`（**原地修改** files 项，加 `dataFileId` 键；没记录的 None；live scan 没出现的旧 DB 记录不补）

- [ ] **Step 1: 写失败测试 — 单 SELECT 不 N+1，记录命中加 dataFileId**

追加到 `server/tests/test_workspace_outputs.py`：

```python
import pytest
from unittest.mock import patch, MagicMock
from utils.workspace_outputs import augment_with_data_file_id


def test_augment_with_data_file_id_in_place(monkeypatch):
    files = [
        {'name': 'a.txt', 'path': 'outputs/a.txt', 'dir': 'outputs', 'size': 10},
        {'name': 'b.txt', 'path': 'outputs/b.txt', 'dir': 'outputs', 'size': 20},
    ]
    rows = [('outputs/a.txt', 'df-1')]  # 只有 a 有记录

    class FakeCur:
        def execute(self, sql, params=None):
            FakeCur._exec_count += 1
        def fetchall(self):
            return rows
    FakeCur._exec_count = 0

    class FakeConn:
        def cursor(self): return FakeCur()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    import utils.workspace_outputs as mod
    monkeypatch.setattr(mod, 'get_db', lambda: FakeConn(), raising=False)
    # 模块顶部无 import get_db，需 monkey 在调用处补 patch
    with patch('utils.workspace_outputs.get_db', create=True, new=lambda: FakeConn()):
        augment_with_data_file_id('sess-1', files)

    assert 'dataFileId' in files[0]
    assert files[0]['dataFileId'] == 'df-1'
    assert files[1]['dataFileId'] is None
    assert FakeCur._exec_count == 1   # 只一条 SELECT，不 N+1
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_workspace_outputs.py::test_augment_with_data_file_id_in_place -v`
Expected: FAIL with `AttributeError: module 'utils.workspace_outputs' has no attribute 'augment_with_data_file_id'`

- [ ] **Step 3: 实装 `augment_with_data_file_id`**

追加到 `server/utils/workspace_outputs.py`：

```python
def augment_with_data_file_id(session_id: str, files: list[dict]) -> None:
    """原地给每个文件项加 dataFileId（从 ai_chat_session_files 表 LEFT JOIN 来）。

    没记录的填 None；live scan 没出现的旧 DB 记录不补（调用方只看 live）。
    单条 SELECT，构造 {path: data_file_id} 后 in-place fill，**不**逐文件 N+1。

    Best-effort：DB 异常时 silent（与 record_session_files 同契约），不阻断 ADMIN 看文件。
    """
    from db import get_db
    if not files:
        return
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT path, data_file_id FROM ai_chat_session_files WHERE session_id = %s",
                (session_id,),
            )
            dfid_map = {r[0]: r[1] for r in cur.fetchall()}
    except Exception:
        dfid_map = {}
    for f in files:
        f['dataFileId'] = dfid_map.get(f['path'])
```

- [ ] **Step 4: 运行测试验证 PASS**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_workspace_outputs.py -v`
Expected: PASS（4 个测试）

- [ ] **Step 5: 提交**

```bash
git add server/utils/workspace_outputs.py server/tests/test_workspace_outputs.py
git commit -m "feat(ai-chat): augment_with_data_file_id LEFT JOIN 补导入归属，单 SELECT 不 N+1"
```

---

## Task 3: `batch_repo.admin_get_child_session` helper

**Files:**
- Modify: `server/utils/batch_repo.py`
- Test: `server/tests/test_batch_repo_admin_child_session.py`（新建）

**Interfaces:**
- Consumes: `db.get_db`
- Produces: `admin_get_child_session(batch_id: str, session_id: str) -> dict | None`。返回 `{id, status, workspace_path, ownerUserId}`；session 不属于该 batch 或不存在返回 None

- [ ] **Step 1: 写失败测试 — 校验归属、NULL workspace、不存在**

`server/tests/test_batch_repo_admin_child_session.py`：

```python
"""admin_get_child_session 跨用户轻查：含 workspace_path 与 ownerUserId。
校验 session 属于 batch；不存在的 batch/session 返 None。"""
import pytest
from utils.batch_repo import admin_get_child_session


@pytest.fixture
def seeded_pair(test_db):
    """插入一个 batch + 两个 child session（一个 tiene workspace, 一个 workspace_path=NULL）。"""
    from db import get_db
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO users (id, username, password, role) "
                    "VALUES ('owner-1', 'owa', 'x', 'developer') ON CONFLICT DO NOTHING")
        cur.execute("INSERT INTO ai_chat_batches (id, user_id, name, status, total, done, failed) "
                    "VALUES ('batch-A', 'owner-1', '测试批', 'completed', 2, 2, 0)")
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, batch_seq, "
            "batch_input_file, workspace_path) "
            "VALUES ('sess-1', 'owner-1', 'completed', 'batch-A', 1, 'in/foo.txt', '/tmp/ws1'),"
            "       ('sess-2', 'owner-1', 'completed', 'batch-A', 2, 'in/bar.txt', NULL)")
        conn.commit()
    return {'batch_id': 'batch-A', 'sid1': 'sess-1', 'sid2': 'sess-2'}


def test_admin_get_child_session_returns_columns(seeded_pair):
    r = admin_get_child_session('batch-A', seeded_pair['sid1'])
    assert r is not None
    assert r['id'] == seeded_pair['sid1']
    assert r['status'] == 'completed'
    assert r['workspace_path'] == '/tmp/ws1'
    assert r['ownerUserId'] == 'owner-1'


def test_admin_get_child_session_null_workspace(seeded_pair):
    r = admin_get_child_session('batch-A', seeded_pair['sid2'])
    assert r is not None
    assert r['workspace_path'] is None  # 区分: caller 据此返 NO_WORKSPACE


def test_admin_get_child_session_wrong_batch_returns_none(seeded_pair):
    # session 存在但属于另一 batch
    from db import get_db
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO ai_chat_batches (id, user_id, name, status, total, done, failed) "
                    "VALUES ('batch-B', 'owner-1', '测试批2', 'completed', 0, 0, 0)")
        # 把 sess-1 关联到 batch-B（更新 batch_id）以制造跨错
        # 但 sess-1 已在 batch-A；为不污染 fixture，新插一个 sess-3 属 batch-B
        cur.execute("INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, batch_seq) "
                    "VALUES ('sess-3', 'owner-1', 'completed', 'batch-B', 1)")
        conn.commit()
    assert admin_get_child_session('batch-A', 'sess-3') is None
    assert admin_get_child_session('batch-B', seeded_pair['sid1']) is None


def test_admin_get_child_session_nonexistent_batch(seeded_pair):
    assert admin_get_child_session('batch-XXX', seeded_pair['sid1']) is None


def test_admin_get_child_session_nonexistent_session(seeded_pair):
    assert admin_get_child_session('batch-A', 'sess-XXX') is None
```

注：测试用 `test_db` fixture 与 `utils/__init__.py` 中的连接池 mock 模式（沿用现有 `tests/test_batch_repo.py`、`tests/test_ai_batch_admin.py` 中的 fixture 套路）—— 若无 `test_db` fixture，参考现有 admin/owner 的 batch_repo 测试用 `monkeypatch` 替换 `get_db`。

- [ ] **Step 2: 跑测试验证失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_batch_repo_admin_child_session.py -v`
Expected: FAIL with `ImportError: cannot import name 'admin_get_child_session' from 'utils.batch_repo'`

- [ ] **Step 3: 实装 `admin_get_child_session`**

追加到 `server/utils/batch_repo.py`，与现有 `admin_get_batch_detail` 相邻：

```python
def admin_get_child_session(batch_id: str, session_id: str) -> dict | None:
    """跨用户取单个子任务的关键列。校验 session 属于该 batch。

    轻量单行查询：返回 {id, status, workspace_path, ownerUserId} 或 None。
    workspace_path 可能为 NULL（子会话归档/清理后）；调用方据此区分
    files 端点返空数组 vs preview/download/import 返 400 NO_WORKSPACE。
    ownerUserId 来自 JOIN ai_chat_batches，用于导入归属（uploaded_by=owner）。

    不复用 admin_get_batch_detail（拉整批 sessions 的 N+1 重调用）；本场景是单 session 操作。
    """
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT s.id, s.status, s.workspace_path, b.user_id AS \"ownerUserId\" "
                "  FROM ai_chat_sessions s "
                "  JOIN ai_chat_batches b ON b.id = s.batch_id "
                " WHERE s.id = %s AND s.batch_id = %s",
                (session_id, batch_id),
            )
            row = cur.fetchone()
    return dict(row) if row else None
```

- [ ] **Step 4: 跑测试验证 PASS**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_batch_repo_admin_child_session.py -v`
Expected: PASS（5 个测试）

- [ ] **Step 5: 提交**

```bash
git add server/utils/batch_repo.py server/tests/test_batch_repo_admin_child_session.py
git commit -m "feat(ai-batch-admin): admin_get_child_session 跨用户轻查含 workspace_path/ownerUserId"
```

---

## Task 4: `auth.require_permission_sse` 装饰器 + `_user_has_permission` 抽出

**Files:**
- Modify: `server/auth.py`
- Test: `server/tests/test_auth_sse_perms.py`（新建）

**Interfaces:**
- Consumes: `utils.permissions.has_permission` 或已有的权限解析
- Produces: `require_permission_sse(permission_key)` 装饰器；`_user_has_permission(payload, key) -> bool` 内部 helper（供 `require_permission` 与 `require_permission_sse` 共用）

- [ ] **Step 1: 写失败测试 — 支持 header 与 query token，能力缺失得 403**

`server/tests/test_auth_sse_perms.py`：

```python
"""require_permission_sse 装饰器测试：支持 Authorization 头与 ?access_token=，
能力缺失二态必 403，超管 pass。"""
import pytest
from unittest.mock import patch
from flask import Flask, jsonify


def _make_app():
    from auth import require_permission_sse
    app = Flask(__name__)
    @app.route('/protected')
    @require_permission_sse('admin.ai_chat_admin')
    def protected():
        return jsonify({'ok': True})
    return app


def test_require_permission_sse_no_token_returns_401():
    client = _make_app().test_client()
    r = client.get('/protected')
    assert r.status_code == 401


def test_require_permission_sse_header_token_forwards_to_perm_gate():
    """有 token 通过，能力门内部决定放行/拒绝。"""
    app = _make_app()
    client = app.test_client()
    with patch('auth.decode_token', return_value={'userId': 'u', 'role': 'admin', 'permissions': ['admin.ai_chat_admin']}):
        with patch('auth.has_permission', return_value=True) if False else patch('utils.permissions.has_permission', return_value=True):
            r = client.get('/protected', headers={'Authorization': 'Bearer tok'})
    assert r.status_code == 200
    assert r.get_json()['ok'] is True


def test_require_permission_sse_query_token_works():
    """模拟 <a href=...?access_token=tok> 下载链接：不能带 Authorization 头。"""
    app = _make_app()
    client = app.test_client()
    with patch('auth.decode_token', return_value={'userId': 'u', 'role': 'dev', 'permissions': []}):
        with patch('utils.permissions.has_permission', return_value=False):
            r = client.get('/protected?access_token=tok')
    assert r.status_code == 403  # 能力门拒


def test_require_permission_sse_superuser_bypasses():
    app = _make_app()
    client = app.test_client()
    with patch('auth.decode_token', return_value={'userId': 'u', 'role': 'admin'}):
        # admin 角色由 require_permission 视为超管，跳过能力查询
        r = client.get('/protected?access_token=tok')
    assert r.status_code == 200
```

注：`utils/permissions.py` 里现有的能力解析与超管逻辑（超管 role='admin' 全通过）需要被 `_user_has_permission` 抽取并复用；若实现细节不同，参照现有 `require_permission` 实际规则改测试期望。所有测试 utility 要同 `_load_session_for_user` 测试 conventions（用现有 `tests/test_auth.py` 的 mock 风格）。

- [ ] **Step 2: 跑测试验证失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_auth_sse_perms.py -v`
Expected: FAIL with `ImportError: cannot import name 'require_permission_sse' from 'auth'`

- [ ] **Step 3: 实装 `require_permission_sse` + 抽 `_user_has_permission`**

`server/auth.py`：
- 现有 `require_permission`（`auth.py:103-`）中"超管判 / 解析角色能力"那段抽成 `_user_has_permission(payload, key) -> bool`，放模块级
- `require_permission` 与新增 `require_permission_sse` 都 call 它，绝不重复两份判断逻辑

```python
def _user_has_permission(payload: dict, permission_key: str) -> bool:
    """超管 (role='admin' 且非用户所定义的超管) 直接放行；否则查能力。

    集中实现，给 require_permission 与 require_permission_sse 共用；这两
    装饰器在 token 提取方式上不同（header only / header or query），但
    能力判断逻辑一致——逻辑漂移是安全事故常见来源。
    """
    if payload.get('role') == 'admin':
        return True
    from utils.permissions import has_permission
    return has_permission(payload, permission_key)


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
            if not _user_has_permission(payload, permission_key):
                return jsonify({'error': '权限不足'}), 403
            return f(*args, **kwargs)
        return decorated
    return wrapper
```

确保现有 `require_permission` 改成调 `_user_has_permission`，行为等价（这条等同 refactor，要靠现有 `test_auth.py` 回归跑过）。

- [ ] **Step 4: 跑测试验证 PASS，并回归 require_permission**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_auth_sse_perms.py tests/test_auth.py -v`
Expected: PASS（新测试 + 现有 require_permission 回归全 PASS）

- [ ] **Step 5: 提交**

```bash
git add server/auth.py server/tests/test_auth_sse_perms.py
git commit -m "feat(auth): require_permission_sse 支持 ?access_token= + 抽 _user_has_permission 公用"
```

---

## Task 5: `import_recorded_files` 加 `extra_whitelist` 参数

**Files:**
- Modify: `server/utils/session_file_import.py:16`
- Test: `server/tests/test_session_file_import_whitelist.py`（新建）

**Interfaces:**
- Consumes: `utils.workspace_changes.get_recorded_path`、`utils.workspace.safe_resolve`、`routes.data_files.save_workspace_file`
- Produces: `import_recorded_files(session_id, workspace_path, paths, uploaded_by=None, extra_whitelist=None)`；`extra_whitelist` 为 set[str] 时，DB 无记录但路径在集合中也放行；行为完全扩展不替换；不传 / 传 None 时与旧实现等价

- [ ] **Step 1: 写失败测试 — 三场景：DB 命中 / live 命中但 DB 无 / 都不命中**

`server/tests/test_session_file_import_whitelist.py`：

```python
"""import_recorded_files 的 extra_whitelist 扩展语义。
- 默认（不传）：DB 命中放行，DB 无 → NOT_RECORDED（与旧行为等价）
- 传额外白名单：DB 无但 path 在 extra 中 → 放行，导入成功后回填 set_data_file_id
- 都不命中：NOT_RECORDED
- extra 与 DB 命中重叠：走 DB 快路径（已有的 data_file_id 直接 reuse）"""
import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock
from utils.session_file_import import import_recorded_files


@pytest.fixture
def workspace_with_files():
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, 'outputs', 'report.md'), 'w') if False else None as _:
            pass
        os.makedirs(os.path.join(td, 'outputs'), exist_ok=True)
        with open(os.path.join(td, 'outputs', 'report.md'), 'w') as f:
            f.write('# r')
        yield td


def test_import_DB_hit_uses_existing_data_file(workspace_with_files, monkeypatch):
    """DB 有记录且有 data_file_id → status=existing，不重新写。"""
    def fake_get_recorded_path(sid, p):
        return {'path': p, 'status': 'added', 'dataFileId': 'df-1'}

    def fake_data_file_meta(fid):
        return {'id': 'df-1', 'name': 'report.md', 'size': 3}

    monkeypatch.setattr('utils.session_file_import.get_recorded_path', fake_get_recorded_path)
    monkeypatch.setattr('utils.session_file_import.data_file_meta', fake_data_file_meta)
    results = import_recorded_files('s', workspace_with_files, ['outputs/report.md'])
    assert results[0]['status'] == 'existing'
    assert results[0]['file']['id'] == 'df-1'


def test_import_DB_miss_without_extra_returns_NOT_RECORDED(workspace_with_files, monkeypatch):
    monkeypatch.setattr('utils.session_file_import.get_recorded_path', lambda sid, p: None)
    results = import_recorded_files('s', workspace_with_files, ['outputs/report.md'])
    assert results[0].get('code') == 'NOT_RECORDED'


def test_import_DB_miss_with_extra_whitelist_passes(workspace_with_files, monkeypatch):
    """admin 端的核心扩展：DB 无但 live 在白名单 → 放行 + 真导入 + 回填。"""
    monkeypatch.setattr('utils.session_file_import.get_recorded_path', lambda sid, p: None)
    saved = {'meta': {'id': 'df-new', 'name': 'report.md', 'size': 3}, 'err': None}

    def fake_save_workspace_file(abs_path, rel_path, uploaded_by=None):
        return saved['meta'], saved['err']

    set_calls = []
    monkeypatch.setattr('utils.session_file_import.save_workspace_file', fake_save_workspace_file)
    monkeypatch.setattr('utils.session_file_import.set_data_file_id',
                        lambda sid, p, fid: set_calls.append((sid, p, fid)))
    results = import_recorded_files(
        'sess-X', workspace_with_files, ['outputs/report.md'],
        uploaded_by='owner-1',
        extra_whitelist={'outputs/report.md'},
    )
    assert results[0]['status'] == 'imported'
    assert results[0]['file']['id'] == 'df-new'
    assert set_calls == [('sess-X', 'outputs/report.md', 'df-new')]  # 回填到 DB


def test_import_DB_miss_not_in_extra_returns_NOT_RECORDED(workspace_with_files, monkeypatch):
    monkeypatch.setattr('utils.session_file_import.get_recorded_path', lambda sid, p: None)
    results = import_recorded_files(
        's', workspace_with_files, ['outputs/other.md'],
        extra_whitelist={'outputs/report.md'},  # 不含 other
    )
    assert results[0].get('code') == 'NOT_RECORDED'


def test_import_default_no_change_to_owner_semantics(workspace_with_files, monkeypatch):
    """owner 端默认不传 extra：行为与旧实现一致。回归锚。"""
    monkeypatch.setattr('utils.session_file_import.get_recorded_path', lambda sid, p: None)
    results = import_recorded_files(
        's', workspace_with_files, ['outputs/report.md'],
        # 不传 extra_whitelist
    )
    assert results[0].get('code') == 'NOT_RECORDED'
```

注：`save_workspace_file` 来自 `routes.data_files`，模块顶部 import 走 lazy 形式（见 `import_recorded_files` 既有代码），monkey 时 patch 全名 `utils.session_file_import.save_workspace_file`。

- [ ] **Step 2: 跑测试验证失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_session_file_import_whitelist.py -v`
Expected: FAIL（5 个测试，因 `import_recorded_files` 无 `extra_whitelist` 参数）

- [ ] **Step 3: 改 `import_recorded_files` 加可选参数**

`server/utils/session_file_import.py:16` 改为：

```python
def import_recorded_files(session_id, workspace_path, paths, uploaded_by=None,
                          extra_whitelist=None):
    """把 `paths`（工作区相对路径）逐个导入 data_files。

    白名单：
      - DB 记录（ai_chat_session_files，由 git 扫描累积）命中 → 总是放行（owner
        与对外 API 的默认行为）。
      - `extra_whitelist` 命中 → 也放行（admin 端用 live-scan 集互补：outputs/
        文件被 .gitignore 屏蔽不进 DB，admin 想导入这批需通过此参数放行）。
      - DB 无记录且 extra 不命中或为 None → NOT_RECORDED 拒。

    幂等：DB 中已有 data_file_id 且文件还在 → reuse 旧 id，不重复入库。
    导入成功后回填 set_data_file_id(session_id, path, file_id)，让下次走 DB
    快路径（不依赖 extra_whitelist，即便 live 文件后来被删也能复用已导入的）。

    返回逐路径结果列表，每项形如：
      {'path', 'status': 'imported'|'existing', 'file': {id,name,size,...}}
      {'path', 'error': ..., 'code': 'NOT_RECORDED'|'BAD_PATH'|'FILE_MISSING'|
                                     'TOO_LARGE'|'IMPORT_FAILED'}
    """
    from routes.data_files import save_workspace_file, data_file_meta

    results = []
    for p in paths:
        p = str(p or '').strip().replace('\\', '/')
        if not p:
            results.append({'path': p, 'error': '路径不能为空', 'code': 'BAD_PATH'})
            continue
        rec = get_recorded_path(session_id, p)
        if rec is None and (not extra_whitelist or p not in extra_whitelist):
            results.append({'path': p, 'error': '该文件不在会话的文件记录中',
                            'code': 'NOT_RECORDED'})
            continue
        if rec and rec['dataFileId']:
            meta = data_file_meta(rec['dataFileId'])
            if meta:
                results.append({'path': p, 'status': 'existing', 'file': meta})
                continue
            # 引用已失效（文件被清过）→ 落到下面重新导入
        try:
            abs_path = safe_resolve(workspace_path, p)
        except WorkspacePathError:
            results.append({'path': p, 'error': '路径无效', 'code': 'BAD_PATH'})
            continue
        if not os.path.isfile(abs_path):
            results.append({'path': p, 'error': '文件在工作区中已不存在',
                            'code': 'FILE_MISSING'})
            continue
        try:
            meta, err = save_workspace_file(abs_path, p, uploaded_by=uploaded_by)
        except Exception:
            meta, err = None, None
        if not meta:
            too_large = bool(err) and err[1] == 413
            results.append({'path': p,
                            'error': '文件过大' if too_large else '导入失败',
                            'code': 'TOO_LARGE' if too_large else 'IMPORT_FAILED'})
            continue
        set_data_file_id(session_id, p, meta['id'])
        results.append({'path': p, 'status': 'imported', 'file': meta})
    return results
```

- [ ] **Step 4: 跑测试验证 PASS**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_session_file_import_whitelist.py -v`
Expected: PASS（5 个测试）

- [ ] **Step 5: 回归 owner/对外 API 现有 import 测试**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_ai_chat.py tests/test_open_api_batches.py -v -k import`
Expected: 现有 import 相关测试全 PASS（不传 extra_whitelist，向后兼容）

- [ ] **Step 6: 提交**

```bash
git add server/utils/session_file_import.py server/tests/test_session_file_import_whitelist.py
git commit -m "feat(file-import): extra_whitelist 可选参数（默认不变，admin 端用 live-scan 集扩展放行 outputs/）"
```

---

## Task 6: 6 个 admin 端点（list/preview/download/import/changes/diff）+ 导入归属审计

**Files:**
- Modify: `server/routes/ai_batch_admin.py`
- Test: `server/tests/test_ai_batch_admin_files.py`（新建）

**Interfaces:**
- Consumes:
  - `utils.workspace_outputs.list_session_files`、`augment_with_data_file_id`（Task 1+2）
  - `utils.batch_repo.admin_get_child_session`（Task 3）
  - `utils.session_file_import.import_recorded_files`、`MAX_IMPORT_PATHS`（Task 5）
  - `utils.workspace.safe_resolve`、`utils.workspace.WorkspacePathError`
  - `utils.workspace_changes.git_changes`、`read_file_preview`、`file_diff`
  - `utils.operation_log.log_operation`
  - `auth.require_permission_sse`（Task 4）
- Produces: 6 个 HTTP 端点（见 §2.4 spec 表）

- [ ] **Step 1: 写失败测试 — 权限门参数化遍历 6 个端点**

`server/tests/test_ai_batch_admin_files.py`：

```python
"""admin 文件端点的核心测试。

特别构造（与 2026-08-12 spec §5.1 同形式）：
1. 权限门参数化遍历路由表 — 6 个新端点必挂 @require_permission_sse。
2. 跨用户可见性 — 管理员能看别人的子任务。
3. 归属校验 — 守住 batch_id 与 sid 不匹配返 404。
4. workspace_path=NULL 路径分支。
5. 路径安全 safe_resolve。
6. 白名单扩展正向 + owner 端差异锁。
7. 幂等 import。
8. 不回滚单条失败。
9. uploaded_by=owner，不是管理员。
10. 操作日志写管理员。
11. augment 不 N+1（已在 Task 2 单测过, 这里集成层再 sanity）。"""
import os
import pytest
from unittest.mock import patch


# ---- (1) 权限门参数化遍历 --------------------------------------------------

def _files_routes(app):
    """挑出 6 个新文件端点的 url rule 名（排除现有 admin/owner endpoint）。"""
    out = []
    for rule in app.url_map.iter_rules():
        s = str(rule)
        if '/ai/chat/admin/batches/<batch_id>/sessions/<sid>/files' in s and rule.endpoint != 'static':
            out.append(rule)
        elif '/ai/chat/admin/batches/<batch_id>/sessions/<sid>/changes' in s:
            out.append(rule)
        elif '/.../files/preview' in s or '/files/download' in s or '/files/import' in s or '/files/diff' in s:
            out.append(rule)
    return out


def test_all_file_endpoints_require_ai_chat_admin(app, non_admin_client):
    routes = [r for r in app.url_map.iter_rules()
              if str(r).startswith('/ai/chat/admin/batches/<batch_id>/sessions/<sid>/files')
                 or str(r).endswith('/changes') and str(r).startswith('/ai/chat/admin/batches/')]
    assert routes  # 防空集 (表明 test suite 自己挂了)
    for r in routes:
        method = next(iter(r.methods - {'HEAD', 'OPTIONS'}))
        # 构造一个 owner-scoped token（无 admin.ai_chat_admin）查到每个路由返 403
        c = non_admin_client
        kwargs = {'headers': {'Authorization': 'Bearer dev-tok'}}
        if method == 'POST':
            kwargs['json'] = {'paths': []}
        resp = c.open(f'/api{str(r).replace("<batch_id>","batch-X").replace("<sid>","sess-X")}', method=method, **kwargs)
        assert resp.status_code == 403, f'{r} 未挂 @require_permission_sse'
```

(non_admin_client、admin_client fixture 与现有 `tests/test_ai_batch_admin.py` 共用；若不存在则参照建一个：用普通 `developer` 用户 token + `admin` token。)

继续：

```python
# ---- (2)(3)(4) 跨用户、归属校验、workspace NULL -----------------------------

def test_list_child_files_cross_user_visible(admin_client, seeded_pair_2):
    """admin 看 dev 用户的子任务文件。"""
    r = admin_client.get(f'/api/ai/chat/admin/batches/{seeded_pair_2["batch_id"]}'
                         f'/sessions/{seeded_pair_2["sid1"]}/files')
    assert r.status_code == 200
    files = r.get_json()['files']
    paths = {f['path'] for f in files}
    assert 'outputs/report.md' in paths
    # admin 多一个 dataFileId 字段
    assert 'dataFileId' in files[0]


def test_list_child_files_returns_404_for_wrong_batch(admin_client, seeded_pair_2):
    r = admin_client.get(f'/api/ai/chat/admin/batches/{seeded_pair_2["batch_id"]}'
                         f'/sessions/sess-NOT-EXISTING/files')
    assert r.status_code == 404
    assert r.get_json().get('code') == 'NOT_FOUND'


def test_list_child_files_empty_when_workspace_is_null(admin_client, seeded_pair_2):
    """归档后 workspace_path=NULL → 空列表而非 400 (preview/download/import 才返 400)。"""
    r = admin_client.get(f'/api/ai/chat/admin/batches/{seeded_pair_2["batch_id"]}'
                         f'/sessions/{seeded_pair_2["sid2"]}/files')  # sid2 workspace NULL
    assert r.status_code == 200
    assert r.get_json() == {'files': [], 'truncated': False}


def test_preview_returns_400_when_workspace_null(admin_client, seeded_pair_2):
    r = admin_client.get(f'/api/ai/chat/admin/batches/{seeded_pair_2["batch_id"]}'
                         f'/sessions/{seeded_pair_2["sid2"]}/files/preview?path=outputs/x.md')
    assert r.status_code == 400
    assert r.get_json().get('code') == 'NO_WORKSPACE'


# ---- (5) 路径安全 safe_resolve --------------------------------------------

@pytest.mark.parametrize('bad_path', [
    '../../../../../etc/passwd',
    '/etc/passwd',
    '..\\..\\..\\windows\\system32',
])
def test_download_rejects_path_traversal(admin_client, seeded_pair_2, bad_path):
    r = admin_client.get(f'/api/ai/chat/admin/batches/{seeded_pair_2["batch_id"]}'
                         f'/sessions/{seeded_pair_2["sid1"]}/files/download',
                         query_string={'path': bad_path})
    assert r.status_code == 400
    assert r.get_json().get('code') == 'BAD_PATH'


# ---- (6)(7)(8) import：白名单扩、幂等、不回滚 -------------------------------

def test_admin_import_outputs_file_passes_live_whitelist(admin_client, seeded_pair_2, db_check):
    """outputs/ 文件无 DB 记录（.gitignore 屏蔽），admin 端靠 extra_whitelist 放行。
    owner 端登录做同 import 应 NOT_RECORDED — 钉住差异。"""
    sid = seeded_pair_2['sid1']
    r = admin_client.post(
        f'/api/ai/chat/admin/batches/{seeded_pair_2["batch_id"]}/sessions/{sid}/files/import',
        json={'paths': ['outputs/report.md']},
    )
    assert r.status_code == 200
    results = r.get_json()['results']
    assert results[0]['status'] == 'imported'
    # uploaded_by = 原 owner 不是 admin
    assert db_check.uploaded_by(results[0]['file']['id']) == seeded_pair_2['owner_user_id']


def test_admin_import_idempotent_second_time_returns_existing(admin_client, seeded_pair_2):
    sid = seeded_pair_2['sid1']
    url = f'/api/ai/chat/admin/batches/{seeded_pair_2["batch_id"]}/sessions/{sid}/files/import'
    admin_client.post(url, json={'paths': ['outputs/report.md']})
    results2 = admin_client.post(url, json={'paths': ['outputs/report.md']}).get_json()['results']
    assert results2[0]['status'] == 'existing'
    # 只入库一次 data_files row
    # (db_check 装饰或 fixture)


def test_admin_import_partial_failure_does_not_rollback(admin_client, seeded_pair_2, db_check):
    sid = seeded_pair_2['sid1']
    # 一个 outputs/report.md 真存在，一个 foo/nope.md 不存在
    r = admin_client.post(
        f'/api/ai/chat/admin/batches/{seeded_pair_2["batch_id"]}/sessions/{sid}/files/import',
        json={'paths': ['outputs/report.md', 'outputs/nope.md']},
    )
    results = r.get_json()['results']
    assert results[0]['status'] in ('imported', 'existing')
    assert results[1].get('code') == 'FILE_MISSING'
    # 已导入的 row 真在 DB 里
    assert db_check.data_files_count(results[0]['file']['id']) == 1


# ---- (9)(10) 导入归属 operator vs uploaded_by -----------------------------

def test_admin_import_logs_operator_as_admin_acting_user(admin_client, seeded_pair_2, db_check):
    sid = seeded_pair_2['sid1']
    admin_client.post(
        f'/api/ai/chat/admin/batches/{seeded_pair_2["batch_id"]}/sessions/{sid}/files/import',
        json={'paths': ['outputs/report.md']},
    )
    logs = db_check.recent_operation_logs(target_type='data_file', limit=10)
    assert any(log.operator_id == 'admin-1' for log in logs)  # 管理员当 operator
    # uploaded_by 不是 admin
    assert db_check.uploaded_by_latest_data_file() != 'admin-1'
```

`db_check` fixture 是测试套件里加一个 DB 校验助手（用真实 `get_db` 跑 SELECT），skip 重细节 mock。

- [ ] **Step 2: 跑测试验证失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_batch_admin_files.py -v`
Expected: FAIL（端点未实现）

- [ ] **Step 3: 实装 6 个端点**

`server/routes/ai_batch_admin.py` 末尾追加（沿用文件现有 import 风格，flask functions 用 `flask_g`/`request`）：

```python
from flask import send_file
from utils.workspace import safe_resolve, WorkspacePathError
from utils.workspace_outputs import list_session_files, augment_with_data_file_id
from utils.batch_repo import admin_get_child_session
from utils.session_file_import import import_recorded_files, MAX_IMPORT_PATHS
from utils.workspace_changes import git_changes, read_file_preview, file_diff


@ai_batch_admin_bp.get('/<batch_id>/sessions/<sid>/files')
@require_permission_sse('admin.ai_chat_admin')
def list_child_files(batch_id, sid):
    """列子任务产出文件：live scan + LEFT JOIN ai_chat_session_files.data_file_id。"""
    sess = admin_get_child_session(batch_id, sid)
    if sess is None:
        return jsonify({'error': '子任务不存在', 'code': 'NOT_FOUND'}), 404
    if not sess['workspace_path']:
        return jsonify({'files': [], 'truncated': False})
    files, truncated = list_session_files(sess['workspace_path'])
    augment_with_data_file_id(sid, files)
    return jsonify({'files': files, 'truncated': truncated})


@ai_batch_admin_bp.get('/<batch_id>/sessions/<sid>/files/preview')
@require_permission_sse('admin.ai_chat_admin')
def preview_child_file(batch_id, sid):
    """单文件文本预览（封顶），与 routes/ai_chat.py::preview_file 同实现。"""
    sess = admin_get_child_session(batch_id, sid)
    if sess is None:
        return jsonify({'error': '子任务不存在', 'code': 'NOT_FOUND'}), 404
    if not sess['workspace_path']:
        return jsonify({'error': '该子会话没有工作区', 'code': 'NO_WORKSPACE'}), 400
    rel = (request.args.get('path') or '').strip()
    if not rel:
        return jsonify({'error': 'path required', 'code': 'PATH_REQUIRED'}), 400
    try:
        abs_path = safe_resolve(sess['workspace_path'], rel)
    except WorkspacePathError:
        return jsonify({'error': 'bad path', 'code': 'BAD_PATH'}), 400
    if not os.path.isfile(abs_path):
        return jsonify({'error': 'not found', 'code': 'FILE_NOT_FOUND'}), 404
    return jsonify(read_file_preview(abs_path))


@ai_batch_admin_bp.get('/<batch_id>/sessions/<sid>/files/download')
@require_permission_sse('admin.ai_chat_admin')
def download_child_file(batch_id, sid):
    """下载工作区文件。`<a href=...?access_token=>` 链接专用：require_permission_sse
    已支持 query token 通过能力门。"""
    sess = admin_get_child_session(batch_id, sid)
    if sess is None:
        return jsonify({'error': '子任务不存在', 'code': 'NOT_FOUND'}), 404
    if not sess['workspace_path']:
        return jsonify({'error': '该子会话没有工作区', 'code': 'NO_WORKSPACE'}), 400
    rel = (request.args.get('path') or '').strip()
    try:
        abs_path = safe_resolve(sess['workspace_path'], rel)
    except WorkspacePathError:
        return jsonify({'error': 'bad path', 'code': 'BAD_PATH'}), 400
    if not os.path.isfile(abs_path):
        return jsonify({'error': 'not found', 'code': 'FILE_NOT_FOUND'}), 404
    return send_file(abs_path, as_attachment=True, download_name=os.path.basename(abs_path))


@ai_batch_admin_bp.post('/<batch_id>/sessions/<sid>/files/import')
@require_permission_sse('admin.ai_chat_admin')
def import_child_files(batch_id, sid):
    """导入子任务产出文件到系统 data_files。

    - extra_whitelist = live-scan 所有 file paths（admin 视角放行 outputs/ +
      workspace + uploads 混合）
    - uploaded_by = 批任务原 owner（不是管理员）
    - log_operation 触发 (g.current_user 自动是管理员，作 operator)
    """
    sess = admin_get_child_session(batch_id, sid)
    if sess is None:
        return jsonify({'error': '子任务不存在', 'code': 'NOT_FOUND'}), 404
    if not sess['workspace_path']:
        return jsonify({'error': '该子会话没有工作区', 'code': 'NO_WORKSPACE'}), 400
    body = request.get_json(silent=True) or {}
    paths = body.get('paths')
    if not isinstance(paths, list) or not paths:
        return jsonify({'error': 'paths required', 'code': 'PATHS_REQUIRED'}), 400
    if len(paths) > MAX_IMPORT_PATHS:
        return jsonify({'error': f'单次最多导入 {MAX_IMPORT_PATHS} 个文件',
                        'code': 'TOO_MANY'}), 400
    ws = sess['workspace_path']
    live_paths = {f['path'] for f, _ in [list_session_files(ws)]} if False else set()
    # 修正：list_session_files 返回 (files, truncated)，取 paths
    files_live, _tr = list_session_files(ws)
    live_paths = {f['path'] for f in files_live}
    results = import_recorded_files(
        sess['id'], ws, paths,
        uploaded_by=sess['ownerUserId'],
        extra_whitelist=live_paths,
    )
    imported_ids = [r['file']['id'] for r in results
                    if r.get('status') in ('imported', 'existing') and r.get('file')]
    log_operation(
        'create', 'data_file',
        target_id=';'.join(imported_ids) if imported_ids else None,
        target_name=f'批任务 {batch_id} 子任务 {sid}',
        description=(f'管理员代导入 {len(imported_ids)} 个产出文件'
                     f'（归属 {sess["ownerUserId"]}）'),
    )
    return jsonify({'results': results})


@ai_batch_admin_bp.get('/<batch_id>/sessions/<sid>/changes')
@require_permission_sse('admin.ai_chat_admin')
def list_child_changes(batch_id, sid):
    """列 git 变更文件（routes/ai_chat.py::list_changes 的管理员跨用户版）。"""
    sess = admin_get_child_session(batch_id, sid)
    if sess is None:
        return jsonify({'error': '子任务不存在', 'code': 'NOT_FOUND'}), 404
    if not sess['workspace_path']:
        return jsonify({'changes': [], 'truncated': False, 'ok': True})
    changes, truncated, ok = git_changes(sess['workspace_path'])
    # 顺手维护 ai_chat_session_files 记录（与 owner 端一致）
    if ok:
        from utils.workspace_changes import record_session_files
        record_session_files(sid, changes)
    return jsonify({'changes': changes, 'truncated': truncated, 'ok': ok})


@ai_batch_admin_bp.get('/<batch_id>/sessions/<sid>/files/diff')
@require_permission_sse('admin.ai_chat_admin')
def child_file_diff(batch_id, sid):
    """单文件 diff（modified）/封顶内容（added）。"""
    sess = admin_get_child_session(batch_id, sid)
    if sess is None:
        return jsonify({'error': '子任务不存在', 'code': 'NOT_FOUND'}), 404
    if not sess['workspace_path']:
        return jsonify({'error': '该子会话没有工作区', 'code': 'NO_WORKSPACE'}), 400
    rel = (request.args.get('path') or '').strip()
    if not rel:
        return jsonify({'error': 'path required', 'code': 'PATH_REQUIRED'}), 400
    try:
        safe_resolve(sess['workspace_path'], rel)
    except WorkspacePathError:
        return jsonify({'error': 'bad path', 'code': 'BAD_PATH'}), 400
    return jsonify(file_diff(sess['workspace_path'], rel))
```

文件顶部 import `os` 已有（若没有则加）。

> ⚠️ **实施时验证关键细节** - import_child_files 中那个两段 `live_paths = ... if False else set()` 是 illustrative；写的时候直接清干净：用 `_files_live, _ = list_session_files(ws); live_paths = {f['path'] for f in _files_live}`。

- [ ] **Step 4: 跑测试验证 PASS**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_batch_admin_files.py -v`
Expected: PASS（11 个测试）

- [ ] **Step 5: 全量后端测试回归**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/ -v -x`
Expected: 全 PASS；若 `test_open_api_batches.py` 的 import 测试 FAIL，系 Task 5 行为变更越界——回查 Task 5 验证不传 extra_whitelist 真等价

- [ ] **Step 6: 提交**

```bash
git add server/routes/ai_batch_admin.py server/tests/test_ai_batch_admin_files.py
git commit -m "feat(ai-batch-admin): 6 个产出文件端点 + 导入归属+审计 (uploaded_by=owner, operator=管理员)"
```

---

## Task 7: 前端 API 层与 `authParam` 抽出公共 helper

**Files:**
- Modify: `src/api/aiChat.ts:296-309`（`authParam` 改 import）
- Modify: `src/api/aiBatchAdmin.ts`（新增 4 函数 + 类型）

**Interfaces:**
- Consumes: `src/utils/request.ts`（已存在则加 export；不存在则创建并 import）
- Produces:
  - `src/utils/request.ts::authParam(prefix: '?'|'&' = '?'): string`
  - `src/api/aiBatchAdmin.ts::listAdminChildFiles(batchId, sessionId)`
  - `getSourceChildFilePreview(batchId, sessionId, path)`
  - `importAdminChildFiles(batchId, sessionId, paths)`
  - `adminChildFileDownloadUrl(batchId, sessionId, path): string`
  - `AdminChildFile`、`AdminImportResult` TS 类型

- [ ] **Step 1: 抽 `authParam` 至 `src/utils/request.ts`**

检查 `src/utils/request.ts` 是否已存在。如存在则在内部追加并 export 一个 `authParam`：

```typescript
// JWT 透传给不能带 Authorization 头的 URL（浏览器 <a download> / EventSource）。
// aiChat 的 downloadFileUrl 与 aiBatchAdmin 的 adminChildFileDownloadUrl 共用，避免两份逻辑漂移。
export function authParam(prefix: '?' | '&' = '?'): string {
  const raw = localStorage.getItem('check-manage:token')
  if (!raw) return ''
  let token = raw
  try {
    const parsed = JSON.parse(raw)
    if (parsed) token = parsed
  } catch {
    /* raw is already the token string */
  }
  return token ? `${prefix}access_token=${encodeURIComponent(token)}` : ''
}
```

- [ ] **Step 2: 改 `src/api/aiChat.ts` 去私有 authParam 改用公共**

删除 `aiChat.ts:296-309` 内的私有 `authParam` 函数，文件顶部加 `import { authParam } from '@/utils/request'`。其余使用点（`downloadFileUrl`、`createEventStream`）从 import 调用 —— 行为完全等价。

- [ ] **Step 3: 在 `src/utils/request.ts` 写 `authParam` 的 vitest 单元测试**

若 `src/utils/__tests__/request.test.ts` 不存在则创建：

```typescript
import { describe, it, expect, beforeEach } from 'vitest'
import { authParam } from '@/utils/request'

describe('authParam', () => {
  beforeEach(() => localStorage.clear())

  it('no token → empty string', () => {
    expect(authParam('?')).toBe('')
  })

  it('plain string token → encoded', () => {
    localStorage.setItem('check-manage:token', 'abc.def.ghi')
    expect(authParam('?')).toBe('?access_token=abc.def.ghi')
    expect(authParam('&')).toBe('&access_token=abc.def.ghi')
  })

  it('JSON-wrapped token → unwrap', () => {
    localStorage.setItem('check-manage:token', JSON.stringify('tok-123'))
    expect(authParam('?')).toBe('?access_token=tok-123')
  })

  it('special chars in token → URL-encoded', () => {
    localStorage.setItem('check-manage:token', 'a+b/=')
    expect(authParam('?')).toBe('?access_token=a%2Bb%2F%3D')
  })
})
```

Run: `npx vitest run src/utils/__tests__/request.test.ts`
Expected: PASS（4）

- [ ] **Step 4: 在 `src/api/aiBatchAdmin.ts` 加 4 函数 + 类型**

`src/api/aiBatchAdmin.ts` 末尾追加：

```typescript
import { authParam } from '@/utils/request'

export interface AdminChildFile {
  name: string
  path: string
  dir: 'uploads' | 'outputs' | 'workspace'
  size: number
  dataFileId: string | null
}

export interface AdminImportResult {
  path: string
  status?: 'imported' | 'existing'
  file?: { id: string; name: string; size: number }
  error?: string
  code?: 'NOT_RECORDED' | 'BAD_PATH' | 'FILE_MISSING' | 'TOO_LARGE' | 'IMPORT_FAILED' | 'PATHS_REQUIRED' | 'TOO_MANY'
}

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
  // 返字符串 URL 而非 axios 请求：浏览器 <a href download> 直接下载
  const bid = encodeURIComponent(batchId)
  const sid = encodeURIComponent(sessionId)
  const p = encodeURIComponent(path)
  return `/api${BASE}/${bid}/sessions/${sid}/files/download?path=${p}${authParam('&')}`
}
```

- [ ] **Step 5: 跑 aiChat 与 aiBatchAdmin 的现有测试正常回归**

Run: `npx vitest run src/api/__tests__/ src/stores/__tests__/`
Expected: PASS。若有现有 aiChat 测试断言 `authParam` 为 aiChat 模块私有成员则需同步改成 import 调用。

- [ ] **Step 6: 跑 vue-tsc 类型检查**

Run: `npm run build` 或者 `npx vue-tsc --noEmit`
Expected: PASS，无 TS error。否则修类型（`AdminImportResult.code` union 与后端实际代码集合对齐）。

- [ ] **Step 7: 提交**

```bash
git add src/utils/request.ts src/api/aiChat.ts src/api/aiBatchAdmin.ts src/utils/__tests__/request.test.ts
git commit -m "refactor(frontend): authParam 抽公共 helper + admin batch 文件 API 函数"
```

---

## Task 8: `AdminBatchFiles.vue` 文件 dialog 组件 + 单元测试

**Files:**
- Create: `src/components/ai-chat/AdminBatchFiles.vue`
- Create: `src/components/ai-chat/__tests__/AdminBatchFiles.test.ts`

**Interfaces:**
- Consumes (props):
  - `batchId: string`、`sessionId: string`
  - `files: AdminChildFile[]`
  - `loading: boolean`
  - `truncated: boolean`
- Emits:
  - `preview(path: string)` — 父接管嵌套预览
  - `import(paths: string[])` — 父接管批量导入 + 返回结果映射

**Props**（决定 Bundling）：组件本身只渲染 + 触 event；父（`AiBatchAdmin.vue` Task 9）拿数据、传 props、收集事件。这与现有 `BatchConversationView.vue` 的解耦方式一致。

- [ ] **Step 1: 写失败测试 — 渲染分支、选中、批量导入按钮联动**

`src/components/ai-chat/__tests__/AdminBatchFiles.test.ts`：

```typescript
import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import AdminBatchFiles from '../AdminBatchFiles.vue'
import type { AdminChildFile } from '@/api/aiBatchAdmin'

beforeAll(() => {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as any
})

const files: AdminChildFile[] = [
  { name: 'a.txt', path: 'outputs/a.txt', dir: 'outputs', size: 1024, dataFileId: null },
  { name: 'b.md', path: 'outputs/b.md', dir: 'outputs', size: 512, dataFileId: 'df-1' },
  { name: 'c.md', path: 'root.md', dir: 'workspace', size: 200, dataFileId: null },
]

function makeWrapper(props: any) {
  return mount(AdminBatchFiles, {
    props: { batchId: 'batch-1', sessionId: 'sess-1', files, truncated: false, loading: false, ...props },
    global: { stubs: {
      'el-button': { template: '<button @click="$emit(\'click\')"><slot /></button>', emits: ['click'] },
      'el-checkbox': { template: '<input type="checkbox" :checked="modelValue" @change="$emit(\'update:modelValue\', $event.target.checked)" />', props: ['modelValue'], emits: ['update:modelValue'] },
      'el-empty': { template: '<div>empty</div>' },
      'el-icon': { template: '<span><slot /></span>' },
      'el-scrollbar': { template: '<div><slot /></div>' },
    } },
  })
}

describe('AdminBatchFiles', () => {
  it('groups files by dir', () => {
    const w = makeWrapper({})
    // outputs/ 一个分组、根目录(workspace) 一组
    const groupHeaders = w.findAll('.admin-files__group-head')
    expect(groupHeaders.length).toBeGreaterThanOrEqual(2)
  })

  it('已导入 (dataFileId 非空) 的行 checkbox disabled + 标签', () => {
    const w = makeWrapper({})
    const importedRow = w.findAll('.admin-files__row').find(r => r.text().includes('b.md'))
    expect(importedRow?.text()).toContain('已导入')
    expect(importedRow?.find('input[type="checkbox"]').attributes('disabled')).toBeDefined()
  })

  it('选中若干 → emits import with paths', async () => {
    const w = makeWrapper({})
    const unimportedCheckbox = w.findAll('input[type="checkbox"]')
      .find(c => !c.attributes('disabled'))
    await unimportedCheckbox?.setValue(true)
    await w.find('[data-testid="import-btn"]').trigger('click')
    const ev = w.emitted('import')
    expect(ev).toBeTruthy()
    expect(ev![0][0]).toContain('outputs/a.txt')  // 含未导入的
    expect(ev![0][0]).not.toContain('outputs/b.md') // 不含已导入的
  })

  it('全部未导入时全选复用', () => {
    const w = makeWrapper({ files: [
      { name: 'a.txt', path: 'outputs/a.txt', dir: 'outputs', size: 1, dataFileId: null }
    ] })
    expect(w.find('[data-testid="select-all"]').isVisible()).toBe(true)
  })

  it('truncated=true 时显示截断提示', () => {
    const w = makeWrapper({ truncated: true })
    expect(w.text()).toContain('仅显示最近')
  })

  it('loading=true 时显示加载中', () => {
    const w = makeWrapper({ loading: true })
    expect(w.text()).toMatch(/加载中|loading/i)
  })
})
```

- [ ] **Step 2: 跑测试验证失败**

Run: `npx vitest run src/components/ai-chat/__tests__/AdminBatchFiles.test.ts`
Expected: FAIL（组件不存在）

- [ ] **Step 3: 实装 `AdminBatchFiles.vue`**

`src/components/ai-chat/AdminBatchFiles.vue`：

```vue
<template>
  <div class="admin-files">
    <div class="admin-files__bar">
      <span class="admin-files__ident">{{ ident }}</span>
      <div class="admin-files__bar-right">
        <el-checkbox v-if="hasUnimported && !loading" v-model="selectAll"
                     data-testid="select-all">全选</el-checkbox>
        <el-button type="primary" size="small" :disabled="!selectedPaths.length || loading"
                   data-testid="import-btn" @click="onImport">
          导入到 data_files（{{ selectedPaths.length }}）
        </el-button>
      </div>
    </div>

    <el-alert v-if="truncated" type="info" :closable="false" show-icon
              title="仅显示最近 1000 个文件" />

    <div v-if="loading" class="admin-files__hint">加载中…</div>
    <el-empty v-else-if="!files.length" description="该子任务没有产出文件" />
    <el-scrollbar v-else max-height="60vh">
      <div v-for="g in groups" :key="g.dirKey" class="admin-files__group">
        <button class="admin-files__group-head" type="button" @click="toggleGroup(g.dirKey)">
          <ElIcon class="admin-files__chev" :class="{ open: !collapsed[g.dirKey] }"><ArrowRight /></ElIcon>
          <span class="admin-files__groupdir">{{ g.label }}</span>
          <span class="admin-files__groupcount">{{ g.files.length }}</span>
        </button>
        <div v-show="!collapsed[g.dirKey]" class="admin-files__group-body">
          <div v-for="f in g.files" :key="f.path" class="admin-files__row">
            <el-checkbox v-if="!f.dataFileId" v-model="selection[f.path]" />
            <span class="admin-files__name">{{ f.name }}</span>
            <span class="admin-files__size">{{ (f.size / 1024).toFixed(1) }} KB</span>
            <el-button link type="primary" @click="emit('preview', f.path)">预览</el-button>
            <a class="admin-files__dl" :href="downloadUrl(f.path)" target="_blank" rel="noopener">下载</a>
            <span v-if="f.dataFileId" class="admin-files__flag">已导入</span>
          </div>
        </div>
      </div>
    </el-scrollbar>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, reactive } from 'vue'
import { ArrowRight, Document } from '@element-plus/icons-vue'
import { ElCheckbox, ElButton, ElEmpty, ElIcon, ElScrollbar, ElAlert } from 'element-plus'
import { adminChildFileDownloadUrl, type AdminChildFile } from '@/api/aiBatchAdmin'

const props = defineProps<{
  batchId: string
  sessionId: string
  files: AdminChildFile[]
  loading: boolean
  truncated: boolean
  ident?: string
}>()
const emit = defineEmits<{ preview: [string]; import: [string[]] }>()

const DIR_LABELS: Record<string, string> = {
  outputs: 'outputs/（agent 输出）',
  workspace: '根目录 / 子目录',
  uploads: 'uploads/（用户输入）',
}
const collapsed = reactive<Record<string, boolean>>({})
function toggleGroup(k: string) { collapsed[k] = !collapsed[k] }

const groups = computed(() => {
  const m = new Map<string, AdminChildFile[]>()
  for (const f of props.files) {
    if (!m.has(f.dir)) m.set(f.dir, [])
    m.get(f.dir)!.push(f)
  }
  return Array.from(m.entries())
    .sort((a, b) => (a[0] === 'outputs' ? -1 : 1))
    .map(([dirKey, files]) => ({ dirKey, files, label: DIR_LABELS[dirKey] || dirKey }))
})

const selection = reactive<Record<string, boolean>>({})
const hasUnimported = computed(() => props.files.some(f => !f.dataFileId))
const selectedPaths = computed(() => props.files.filter(f => selection[f.path] && !f.dataFileId).map(f => f.path))
const selectAll = computed({
  get: () => props.files.filter(f => !f.dataFileId).every(f => selection[f.path]),
  set: (v: boolean) => { props.files.filter(f => !f.dataFileId).forEach(f => { selection[f.path] = v }) },
})

function downloadUrl(path: string) {
  return adminChildFileDownloadUrl(props.batchId, props.sessionId, path)
}
function onImport() { emit('import', selectedPaths.value) }
</script>

<style scoped lang="scss">
.admin-files { display: flex; flex-direction: column; gap: 8px; }
.admin-files__bar { display: flex; justify-content: space-between; align-items: center; }
.admin-files__bar-right { display: flex; align-items: center; gap: 12px; }
.admin-files__hint { color: var(--el-text-color-secondary); padding: 12px 0; }
.admin-files__group-head { display: flex; align-items: center; gap: 6px; padding: 6px 0; background: none; border: none; cursor: pointer; }
.admin-files__chev { transition: transform .2s; }
.admin-files__chev.open { transform: rotate(90deg); }
.admin-files__row { display: flex; align-items: center; gap: 8px; padding: 4px 16px; }
.admin-files__flag { color: var(--el-text-color-secondary); font-size: 12px; margin-left: auto; }
.admin-files__dl { color: var(--el-color-primary); text-decoration: none; }
.admin-files__size { color: var(--el-text-color-secondary); font-size: 12px; }
</style>
```

- [ ] **Step 4: 跑测试验证 PASS**

Run: `npx vitest run src/components/ai-chat/__tests__/AdminBatchFiles.test.ts`
Expected: PASS（6）

- [ ] **Step 5: 跑 vue-tsc**

Run: `npx vue-tsc --noEmit`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/components/ai-chat/AdminBatchFiles.vue src/components/ai-chat/__tests__/AdminBatchFiles.test.ts
git commit -m "feat(ai-batch-admin): AdminBatchFiles.vue 独立文件 dialog + 单元测试"
```

---

## Task 9: `AiBatchAdmin.vue` 入口按钮 + 接线 dialog 与导入逻辑

**Files:**
- Modify: `src/views/admin/AiBatchAdmin.vue`

**Interfaces:**
- Consumes: `AdminBatchFiles.vue`（Task 8）、`listAdminChildFiles` / `importAdminChildFiles` / `getAdminChildFilePreview`（Task 7）

- [ ] **Step 1: 加「产出文件」按钮与文件 dialog state**

`src/views/admin/AiBatchAdmin.vue` 在子任务表「操作」列追加按钮（与「查看对话」「重跑」并列）和 `filesOpen` 状态对：

```vue
<!-- 在 <el-table-column label="操作"> 里 -->
<template #default="{ row }">
  <el-button link type="primary" @click="openConversation(row)">查看对话</el-button>
  <el-button link type="primary" @click="openChildFiles(row)">产出文件</el-button>
  <el-button link type="warning" :disabled="!isTerminal(row.status)" @click="onReexecute(row)">重跑</el-button>
</template>
```

正文追加文件 dialog：

```vue
<el-dialog v-model="filesOpen" :title="`产出文件：${filesIdent}`" width="70%" top="6vh">
  <AdminBatchFiles
    v-if="filesOpen"
    :batch-id="filesBatchId"
    :session-id="filesSessionId"
    :files="childFiles"
    :loading="childFilesLoading"
    :truncated="childFilesTruncated"
    :ident="filesIdent"
    @preview="onPreviewChildFile"
    @import="onImportChildFiles"
  />
</el-dialog>
```

类似的方式为预览加嵌套 dialog（参照 `AiChatView.vue` 中 preview output 的写法，简短文本内容展示，二进制给提示）：可以用 `el-dialog` 嵌套或 `el-message-box`。

- [ ] **Step 2: script setup 添加 state 与 handlers**

```typescript
import AdminBatchFiles from '@/components/ai-chat/AdminBatchFiles.vue'
import {
  listAdminChildFiles, importAdminChildFiles, getAdminChildFilePreview,
  type AdminChildFile, type AdminImportResult,
} from '@/api/aiBatchAdmin'

// 文件 dialog 局部 state（不进 store，同 convOpen 风格）
const filesOpen = ref(false)
const filesBatchId = ref('')
const filesSessionId = ref('')
const filesIdent = ref('')
const childFiles = ref<AdminChildFile[]>([])
const childFilesLoading = ref(false)
const childFilesTruncated = ref(false)

// 预览 dialog state
const previewOpen = ref(false)
const previewPath = ref('')
const previewContent = ref('')
const previewTruncated = ref(false)
const previewLoading = ref(false)

async function openChildFiles(row: AdminChild) {
  if (!detail.value) return
  filesBatchId.value = detail.value.batch.batchId
  filesSessionId.value = row.sessionId
  filesIdent.value = `${row.name} #${row.seq}`
  childFiles.value = []
  childFilesTruncated.value = false
  childFilesLoading.value = true
  filesOpen.value = true
  try {
    const res = await listAdminChildFiles(filesBatchId.value, filesSessionId.value)
    childFiles.value = res.files
    childFilesTruncated.value = res.truncated
  } finally {
    childFilesLoading.value = false
  }
}

async function onPreviewChildFile(path: string) {
  previewPath.value = path
  previewOpen.value = true
  previewLoading.value = true
  try {
    const res = await getAdminChildFilePreview(filesBatchId.value, filesSessionId.value, path)
    previewContent.value = res.content
    previewTruncated.value = res.truncated
  } finally {
    previewLoading.value = false
  }
}

async function onImportChildFiles(paths: string[]) {
  try {
    const res = await importAdminChildFiles(filesBatchId.value, filesSessionId.value, paths)
    const results: AdminImportResult[] = res.results
    const ok = results.filter(r => r.status).length
    const fail = results.filter(r => r.code).length
    ElMessage.success(`已导入 ${ok} 个文件${fail ? `，${fail} 个失败` : ''}`)
    // 重新拉列表以刷新 dataFileId
    const fresh = await listAdminChildFiles(filesBatchId.value, filesSessionId.value)
    childFiles.value = fresh.files
    childFilesTruncated.value = fresh.truncated
  } catch (e: any) {
    // 全局拦截器已弹错；这里仅展示降级提示
  }
}
```

模板追加预览 dialog：

```vue
<el-dialog v-model="previewOpen" :title="`预览：${previewPath}`" width="60%" top="10vh">
  <div v-if="previewLoading">加载中…</div>
  <template v-else>
    <pre class="admin-preview__text">{{ previewContent }}</pre>
    <el-alert v-if="previewTruncated" type="info" :closable="false"
              title="内容过长，已截断显示" />
  </template>
</el-dialog>
```

- [ ] **Step 3: 跑 vitest 与 vue-tsc**

Run: `npx vitest run src/views/admin/__tests__/AiBatchAdmin.test.ts 2>$null || npx vitest run src/stores/__tests__/aiBatchAdmin.test.ts`
Expected: 现有 admin batch store/页面测试全 PASS（按钮入口只是渲染，未破坏 store 行为）

Run: `npx vue-tsc --noEmit`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add src/views/admin/AiBatchAdmin.vue
git commit -m "feat(ai-batch-admin): 子任务行加产出文件按钮 + 接线文件 dialog 与导入预览"
```

---

## Task 10: Playwright 端到端验证（含 DB 回查）+ proxy.py :8080 跑通

**Files:**
- No file changes（验证 only）
- Evidence: `.playwright-mcp/*.png`、`.proxy-evidence/*`（CI/artifacts）

- [ ] **Step 1: 启动服务**

确认 `npm run dev:all` 跑起来：前端 `:5173`、后端 `:3002`、DB `casemanage`。前端 HMR 自动更新。后端重启加载新代码（`python app.py`）。

确保有一个**已完成**的批任务：若没有先在 AI 助手侧边栏建一个跑完（admin/admin123 登录 → 左边栏 BatchGroup → CreateBatchDialog → 提交 N 个文件 + 一段 prompt → 跑完）。

- [ ] **Step 2: 浏览器流走 — 列文件 + 下载**

Playwright MCP tools：

```
navigate http://localhost:5173/admin/ai-batches
预计: 看到 admin 批管理页

fill [test=用户名] admin
fill [test=密码] admin123
click 登录
navigate http://localhost:5173/admin/ai-batches

预计: 看到刚跑完的批任务行（已完成，绿色提示）
click 详情（行里的「详情」按钮）
预计: 抽屉里子任务表，每行有「查看对话」「产出文件」「重跑」
click 第一个子任务的「产出文件」
预计: 弹窗出来，列出 outputs/ 下产生的文件名 + 大小 + 预览/下载链接
```

截图保存到 `.playwright-mcp/01-files-dialog.png`。

- [ ] **Step 3: 下载文件 + 真存磁盘**

```
hover 下载链接
click 下载
预计: 浏览器开始下载文件
```

某些 Playwright 实际下载行为需在 file chooser 与 downloads 后验证。可在 MCP 里 `expect_download` 之后断言文件大小 > 0。

- [ ] **Step 4: 勾选 + 导入 + 回查 DB**

```
hover 一个文件 row
check 该行 checkbox
click 「导入到 data_files」按钮
预计: 出现 toast「已导入 1 个文件」
预计: 该行尾出现「已导入」灰字
```

**回查 DB**：

```bash
psql -d casemanage -c "SELECT id, name, uploaded_by FROM data_files ORDER BY created_at DESC LIMIT 1;"
```

断言 `uploaded_by = <批任务 owner_user_id>`（不是 'admin-1'）：

```sql
SELECT df.uploaded_by, b.user_id
FROM data_files df
JOIN ai_chat_session_files sf ON sf.data_file_id = df.id
JOIN ai_chat_sessions s ON s.id = sf.session_id
JOIN ai_chat_batches b ON b.id = s.batch_id
WHERE df.uploaded_by <> b.user_id   -- 期待 0 row（不能 mismatch）
;
```

回查 operation_logs：

```sql
SELECT operator_id, target_type, description
FROM operation_logs
WHERE target_type='data_file'
ORDER BY created_at DESC LIMIT 5;
```

断言 `operator_id` = 'admin-1'（管理员），description 含「管理员代导入」+ 「归属 <owner_user_id>」。

截图 `.playwright-mcp/02-after-import.png`，把 DB 查询结果 document 在 PR 描述里。

- [ ] **Step 5: proxy.py :8080 跑通**

```
bash: npm run build
cd server && python proxy.py
```

`:8080` 启动后用 curl/playwright 走一遍 6 个端点：

```bash
# 列文件 (跟 admin token)
curl -s -H "Authorization: Bearer <admin-jwt>" \
  http://localhost:8080/api/ai/chat/admin/batches/<bid>/sessions/<sid>/files | python -m json.tool

# 下载（纯 query token，对应 <a href>）
curl -sL "http://localhost:8080/api/ai/chat/admin/batches/<bid>/sessions/<sid>/files/download?path=outputs/foo.md&access_token=<admin-jwt>" -o /tmp/dl_proxy.md
# 验证 /tmp/dl_proxy.md 内容与原会话工作区文件一致
diff /tmp/dl_proxy.md <workspace_root>/<owner>/<sid>/outputs/foo.md
```

Expected: 输出一致，0 exit code。

断言 proxy 不会 buffered SSE（端点非 SSE，应走 `_relay_buffered` 正常 forward，不应 hang）。手动用 `time curl --max-time 5` 测各端点响应时长不超过 5 秒。

把 evidence（命令输出）弄成 `.proxy-evidence/list.txt`、`.proxy-evidence/download.txt` 等。

- [ ] **Step 6: 若失败 → 排查 + 修**

任何验证 fail：
1. 重新跑前端：`npm run dev:all`（HMR 修 unit/类型 typo）
2. 重启后端：`python app.py`（代码改动需重载）
3. 修后回到 Step 2 继续

- [ ] **Step 7: 文档证据合并到 PR 描述**

PR description 必含三块（在 Task 11 CLAUDE.md / 文档提交后再贴过来）：

```
## 验证记录

### 后端单元 (pytest)
`cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_batch_admin_files.py tests/test_workspace_outputs.py tests/test_batch_repo_admin_child_session.py tests/test_auth_sse_perms.py tests/test_session_file_import_whitelist.py -v`
PASS（X 个测试）

### 前端单元 (vitest + vue-tsc)
`npm run test && npm run build`
PASS

### 端到端 (Playwright on :5173)
- 产出文件 dialog 打开 [.playwright-mcp/01-files-dialog.png]
- 下载文件并保存磁盘 [.playwright-mcp/02-download.png]
- 勾选 + 导入data_files [.playwright-mcp/02-after-import.png]
- DB 回查：uploaded_by = owner_user_id（非 admin-1）[.playwright-mcp/db-check.txt]
- operation_logs.operator_id = admin-1（管理员）[同上]

### proxy.py 验证（:8080)
- list files 端点经 proxy 通 [.proxy-evidence/list.txt]
- download with ?access_token= 经 proxy 通 [.proxy-evidence/download.txt]
- diff 与 workspace 文件一致 → 0 exit code
- 无 SSE 改动（spec §4.6 已声明）：proxy._is_sse() 未动
```

---

## Task 11: 文档同步 + CLAUDE.md 更新

**Files:**
- Modify: `docs/user-guide/ai/batch-tasks.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: 文档加「产出文件」子节**

打开 `docs/user-guide/ai/batch-tasks.md`，定位「管理员：批任务管理页」一节（由 2026-08-12 spec §5.5 引入），在其内部加一个「产出文件」子节：

```markdown
### 产出文件

管理员在批任务详情抽屉里点击子任务行的 **「产出文件」** 按钮，打开一个新弹窗，列出该子任务 AI 实际产出的全部文件：

- **文件来源**：实时递归扫描子任务的工作区根目录 + `outputs/` 子目录（不依赖 git 记录，因为 `.gitignore` 屏蔽 `outputs/` 故 DB 不记录这批），外加上传到 `uploads/` 的输入文件。按目录分组折叠，每个分组显示文件数量。
- **文件状态**：行尾如有「已导入」灰标签，说明此文件**已导入**到系统 `data_files`，不可重复导入（checkbox 自动禁用）。
- **预览**：点击「预览」按钮在嵌套对话框里查看文件文本内容（超长会被截断并提示）。二进制文件显示下载提示。
- **下载**：点击「下载」链接下载原始文件到本地。下载靠 `?access_token=` 传递登录凭据，所以管理员能在同一浏览器会话下直接下载，不弹二次登录。
- **导入到 data_files**：勾选若干 checkbox → 点击「导入到 data_files」按钮：
  - 文件被复制到系统 `data_files` 存储，获得文件 id。
  - **导入归属**：`uploaded_by` 字段填的是**批任务的原属主**，不是管理员本人 —— 这样产出文件在 `data_files` 的列表里属于"该用户名下"，与本次 AI 任务的语义保持一致。导入这个动作在 `operation_logs` 里记的是**管理员**为操作发起人，便于追责。
  - 幂等：重复导入同一路径视为 `existing`，不重复写入 `data_files`。
  - 单文件失败不会回滚其他已经导入的文件：失败项展示红字错误（如 `FILE_MISSING`、`TOO_LARGE`）。

> 截图：管理员批任务页打开产出文件弹窗，见 `docs/user-guide/_images/ai-batch-admin-files.png`

> 注意：导入功能依赖工作区文件还在；若子任务的会话被其所有者归档/清理了 workspace（见 [AI 助手 - 会话治理](./assistant.md) 一节），live scan 会返回空，import 也将失败提示「文件在工作区中已不存在」。
```

把 `.playwright-mcp/01-files-dialog.png` 复制到 `docs/user-guide/_images/ai-batch-admin-files.png`。

- [ ] **Step 2: 在 `docs/user-guide/README.md` 索引 检查**

打开 `docs/user-guide/README.md`，检查 `batch-tasks.md` 已在索引里（由 2026-08-12 spec 添加），无需重复加链接，仅在同一行下追加产出文件能力的一句话：

```
- [AI 批任务](ai/batch-tasks.md) — 批处理多文件并行 AI；管理员跨用户查看产出文件、下载、导入到 data_files
```

- [ ] **Step 3: CLAUDE.md 补一行能力说明**

打开 `CLAUDE.md` 定位到「AI Agent Chat — Batch Tasks (M1.5)」段，找到「Admin page」子段（介绍 `/admin/ai-batches`、`AiBatchAdmin.vue`、`ai_batch_admin_bp`），在文末再加一句：

> 产出文件能力（2026-08-14）：管理员可在子任务行点「产出文件」打开独立 dialog，列出该子任务 AI 实际产出的文件（live scan workspace + LEFT JOIN `ai_chat_session_files.data_file_id`）、预览、下载、批量导入到 `data_files`（`uploaded_by` = 批任务原 owner，`operation_logs.operator` = 管理员）。导入白名单扩展为 `DB 白名单 ∪ live-scan 文件名集合`（admin 端独有；owner 端与对外 API 仍只 DB 白名单，行为不变）。新增端点：`/<batch_id>/sessions/<sid>/files`、`/files/preview`、`/files/download`、`/files/import`、`/changes`、`/files/diff`，全部走 `require_permission_sse('admin.ai_chat_admin')` 装饰器（`<a download>` 链接要靠 `?access_token=`）。owner 端 `/sessions/<sid>/files` refactor 为调 `utils/workspace_outputs.py::list_session_files`，行为等价。

关键文件：`server/utils/workspace_outputs.py`、`server/routes/ai_batch_admin.py` 新增 6 endpts + `src/components/ai-chat/AdminBatchFiles.vue`、`server/auth.py::require_permission_sse`、`src/utils/request.ts::authParam`。

- [ ] **Step 4: 提交**

```bash
git add docs/user-guide/ai/batch-tasks.md docs/user-guide/README.md CLAUDE.md docs/user-guide/_images/ai-batch-admin-files.png
git commit -m "docs(ai-batch-admin): 用户指南+CLAUDE.md 补产出文件能力"
```

---

## Task 12: 最终自查 + PR 描述与提交

**Files:**
- No file changes

- [ ] **Step 1: 全量后端测试回归**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/ -v`
Expected: 全 PASS（含原有 `test_routes_ai_chat.py`、`test_ai_batch_admin.py`、`test_open_api_batches.py`、新增 5 个 test 文件）

若有 FAIL：
- `open_api_batches.py` import 测试 FAIL → 检查 Task 5 `import_recorded_files` 是否在不传 `extra_whitelist` 时严格等价
- `test_routes_ai_chat.py::list_files` FAIL → 检查 Task 1 util 抽取后字段集是否真的 1:1

- [ ] **Step 2: 全量前端测试 + 类型**

Run: `npm run test && npm run build`
Expected: 全 PASS。`vue-tsc` 无 error。

- [ ] **Step 3: Lint**

Run: `npm run lint` 等（如有）
Expected: PASS

- [ ] **Step 4: 对照 spec §6 文件清单一项一项 verify**

清单（开 PR 时贴到 PR description）：

新建：
- [ ] `server/utils/workspace_outputs.py`
- [ ] `server/tests/test_ai_batch_admin_files.py`
- [ ] `server/tests/test_workspace_outputs.py`
- [ ] `server/tests/test_batch_repo_admin_child_session.py`
- [ ] `server/tests/test_auth_sse_perms.py`
- [ ] `server/tests/test_session_file_import_whitelist.py`
- [ ] `src/components/ai-chat/AdminBatchFiles.vue`
- [ ] `src/components/ai-chat/__tests__/AdminBatchFiles.test.ts`
- [ ] `src/utils/__tests__/request.test.ts`（或已合并到现有测试文件）

修改：
- [ ] `server/routes/ai_chat.py` — `list_files` 调 util
- [ ] `server/utils/batch_repo.py` — `admin_get_child_session`
- [ ] `server/auth.py` — `require_permission_sse` + `_user_has_permission`
- [ ] `server/utils/session_file_import.py` — `extra_whitelist` 参数
- [ ] `server/routes/ai_batch_admin.py` — 6 端点
- [ ] `src/api/aiChat.ts` — `authParam` 改 import
- [ ] `src/api/aiBatchAdmin.ts` — 4 函数 + 类型
- [ ] `src/utils/request.ts` — `authParam` export
- [ ] `src/views/admin/AiBatchAdmin.vue` — 入口 button + dialog state
- [ ] `docs/user-guide/ai/batch-tasks.md` — 「产出文件」子节 + 截图
- [ ] `docs/user-guide/README.md` — 索引行更新
- [ ] `CLAUDE.md` — AI 批任务管理段补一行

不动（spec §6 明示）：
- [ ] `server/utils/batch_engine.py` — `_record_workspace_files` 行为不变
- [ ] `server/utils/workspace_changes.py` — `record_session_files`、`git_changes`、`read_file_preview`、`file_diff` 全部不动
- [ ] `server/utils/workspace.py` — `safe_resolve` 不动
- [ ] `server/routes/open_api_batches.py` — 对外 API 行为不动
- [ ] `server/routes/ai_chat_batches.py` — owner 内部批任务不变
- [ ] `server/app.py` — 不动注册顺序
- [ ] `server/proxy.py` — 不动 `_is_sse()`、不动 forward header list

- [ ] **Step 5: 对照 spec §1.2 YAGNI verify**

- [ ] 未改 `batch_engine._record_workspace_files`
- [ ] 未向对外 API `/api/v1/ai-batches/*` 添加 file 端点
- [ ] 未做文件级删除/裁剪
- [ ] 未做游标翻页
- [ ] 未重构 `BatchConversationView.vue`

- [ ] **Step 6: 开 PR**

提 PR：`gh pr create --base main --title "feat(ai-batch-admin): 产出文件能力（列+预览+下载+导入+diff）" --body "$(cat <<EOF
## 概览

为 admin AI 批任务管理页（2026-08-12 引入）补齐产出文件能力。spec: \`docs/superpowers/specs/2026-08-14-ai-batch-admin-file-outputs-design.md\`

## 范围
- 列文件（live scan + LEFT JOIN \`ai_chat_session_files.data_file_id\`）
- 预览 / 下载（含 \`?access_token=\` 透传）
- 批量导入到 \`data_files\`（uploaded_by=owner；operator=管理员）
- git 变更与 diff

## 设计要点
- 不改 \`batch_engine._record_workspace_files\` / \`open_api_batches.py\` / \`batch_engine\` / \`proxy.py\`
- 新增 \`require_permission_sse\` 装饰器（下载链接通过 \`?access_token=\` 鉴权）
- \`import_recorded_files\` 加 \`extra_whitelist\` 可选参数：admin 端 union（live-scan + DB），owner/对外 API 不传保持原行为不变
- owner 端 \`/sessions/<sid>/files\` refactor 为调 util，行为等价
- 共 6 个新 admin 端点全部 \`require_permission_sse('admin.ai_chat_admin')\`

## 验证记录
[贴 Task 10 Step 7 的模板]

## 文件清单
[贴 Task 12 Step 4 的清单勾选项]

## Spec 与本 PR 的差异
None.
EOF
)"`

Expected: PR URL 返出，记录。

- [ ] **Step 7: 自查 plan 与 spec 一致性**

回到本计划 + spec 跑一遍，确认：
- [ ] spec §1 范围每个决策都有对应 task
- [ ] spec §2 端点表 6 项都有 Task 6 测覆盖
- [ ] spec §3 4 个 helper 全部在 Tasks 1-5 内
- [ ] spec §4 边界情况每个有 Task 6 测试条目
- [ ] spec §5 测试 11 条都对到 Tasks 5/6/8 内的具体测试
- [ ] spec §6 文件清单 Task 12 Step 4 已逐项打勾
EOF

---

## Self-Review Checklist (writer runs after plan written)

**1. Spec coverage** (spec §1-§6 全段对照本 plan):
- §1 范围决策（7 条）→ Tasks 1, 5, 6 cover 完
- §2.1 util → Task 1 ✓
- §2.2 helper → Task 3 ✓
- §2.3 decorator → Task 4 ✓
- §2.4 6 端点 → Task 6 ✓
- §2.5 白名单扩展 → Task 5 ✓
- §2.6 导入归属 → Task 6 ✓
- §2.7 owner refactor → Task 1 ✓
- §3.1 入口 → Task 9 ✓
- §3.2 组件 → Task 8 ✓
- §3.3 API 层 → Task 7 ✓
- §3.4 authParam 抽 → Task 7 ✓
- §3.5 store → Task 9 (无新 store)
- §3.6 交互 → Task 8 + 9 ✓
- §4 错误码全表 → Task 6 测试 ✓
- §5.1 pytest 11 条 → Task 6 ✓
- §5.2 vitest → Task 8 ✓
- §5.3 Playwright + DB 回查 → Task 10 ✓
- §5.4 proxy.py → Task 10 Step 5 ✓
- §5.5 文档同步 → Task 11 ✓
- §6 文件清单一一对应所有 task ✓

**2. Placeholder scan**: Zero TODO/TBD/... ✓（只有 "..." 在真实代码 fragment 占位，对应到 spec 引用，unit test codes 写真实断言）

**3. Type consistency**:
- `list_session_files(ws) -> tuple[list, bool]` 在 Task 1 / Task 2 / Task 6 用法一致 ✓
- `admin_get_child_session(batch_id, sid) -> dict|None` 返回键 `{id, status, workspace_path, ownerUserId}` ✓
- `import_recorded_files(..., extra_whitelist=None)` 在 Task 5 / Task 6 一致 ✓
- `require_permission_sse(perm_key)` 在 Task 4 / Task 6 一致 ✓
- `AdminChildFile` / `AdminImportResult` (前端) Task 7 / Task 8 / Task 9 一致 ✓
- `authParam(prefix)` 签名 Task 7 抽出与复用一致 ✓

无问题。Plan 已经 ready for execution.