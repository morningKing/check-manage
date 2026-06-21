# AI 会话治理与审计 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** 个人只能 close（软关闭、可 reopen、永久保留）会话、不能物理删；会话生命周期（create/close/reopen/archive）写入 operation_logs 做审计/溯源；admin 可查看所有会话并归档。

**Architecture:** 在 `routes/ai_chat.py` 把个人的物理 DELETE 换成 `close`（仅改 status、**保留** token/workspace/OpenCode session，故可 reopen），新增 `reopen`、admin `archive` 与 admin 列表端点；每个生命周期操作调现成的 `log_operation()`。前端会话列表把"删除"换成"关闭"+ closed 会话"重开"。

**Tech Stack:** Flask、psycopg2、operation_log helper、RBAC permissions、Vue3 + Element Plus、pytest。

**Spec:** `docs/superpowers/specs/2026-06-21-ai-chat-session-governance-design.md`。

---

## File Structure
- `server/routes/ai_chat.py` — close/reopen/archive/admin-list 端点；list 改造；create 审计；移除个人 DELETE
- `server/utils/permissions.py` — 新增 `admin.ai_chat_admin`
- `server/routes/operation_logs.py` — `TARGET_LABELS` 加 `ai_chat_session`（审计 UI 友好显示）
- `server/tests/test_ai_chat_governance.py`（新）
- `src/api/aiChat.ts` — `closeSession`/`reopenSession`（替换 deleteSession）
- `src/stores/aiChat.ts` — close/reopen action
- `src/views/ai-chat/AiChatView.vue` — 关闭/重开按钮 + status 渲染
- 文档：`docs/user-guide/ai/`、`CLAUDE.md`

---

## Task 1：后端 close + reopen 端点 + 审计

**Files:** Modify `server/routes/ai_chat.py`; Test `server/tests/test_ai_chat_governance.py`.

- [ ] **Step 1: 写失败测试** `server/tests/test_ai_chat_governance.py`:
```python
import sys, os
from unittest.mock import patch, MagicMock
from contextlib import contextmanager
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from auth import create_token

def _db(rows):
    conn = MagicMock(); cur = MagicMock()
    cur.fetchone.side_effect = list(rows)
    conn.cursor.return_value = cur
    @contextmanager
    def fake():
        yield conn
    return fake, cur

def _client():
    from app import app
    app.config['TESTING'] = True
    return app.test_client()

def _h(uid='u1', role='developer'):
    return {'Authorization': 'Bearer ' + create_token({'id': uid, 'username': uid, 'role': role})}

def test_close_sets_status_and_audits():
    import routes.ai_chat as ac
    fake, cur = _db([('s1', 'u1', 'oc1', 'active', '/ws')])  # _load_session_for_user row
    with patch.object(ac, 'get_db', fake), \
         patch.object(ac, 'stop_listener') as stop, \
         patch.object(ac, 'log_operation') as logop:
        r = _client().post('/ai/chat/sessions/s1/close', headers=_h())
    assert r.status_code == 200
    # status updated to closed
    assert any("status = 'closed'" in str(c.args[0]) or "status='closed'" in str(c.args[0])
               for c in cur.execute.call_args_list)
    stop.assert_called_once_with('s1')
    logop.assert_called_once()
    assert logop.call_args.args[1] == 'ai_chat_session'  # target_type

def test_close_others_session_404():
    import routes.ai_chat as ac
    fake, cur = _db([None])  # _load_session_for_user returns None for non-owner
    with patch.object(ac, 'get_db', fake):
        r = _client().post('/ai/chat/sessions/sX/close', headers=_h())
    assert r.status_code == 404

def test_reopen_closed_to_active():
    import routes.ai_chat as ac
    fake, cur = _db([('s1', 'u1', 'oc1', 'closed', '/ws')])
    with patch.object(ac, 'get_db', fake), patch.object(ac, 'log_operation'):
        r = _client().post('/ai/chat/sessions/s1/reopen', headers=_h())
    assert r.status_code == 200
    assert any("status = 'active'" in str(c.args[0]) for c in cur.execute.call_args_list)

def test_reopen_archived_forbidden():
    import routes.ai_chat as ac
    fake, cur = _db([('s1', 'u1', 'oc1', 'archived', '/ws')])
    with patch.object(ac, 'get_db', fake):
        r = _client().post('/ai/chat/sessions/s1/reopen', headers=_h())
    assert r.status_code == 403
```
> `_load_session_for_user` 已返回 `(id, user_id, opencode_session_id, status, workspace_path)`；测试用 `fetchone` 第一项作为它的返回，第二项作为后续 UPDATE 后的（不读）。

- [ ] **Step 2: Run, confirm FAIL**: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_ai_chat_governance.py -v`

- [ ] **Step 3: 实现 close/reopen** in `server/routes/ai_chat.py`. Add import near the top: `from utils.operation_log import log_operation`. Then add（放在 `delete_session` 附近）:
```python
@ai_chat_bp.route('/sessions/<sid>/close', methods=['POST'])
@write_required
def close_session(sid):
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    # close 是软关闭、可 reopen：仅改 status + 停 listener；
    # 保留 token / workspace / OpenCode session，使 reopen 能续上（失效则 M3 重建）。
    stop_listener(sid)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE ai_chat_sessions SET status='closed' WHERE id=%s AND user_id=%s",
                    (sid, user['userId']))
    log_operation('update', 'ai_chat_session', sid, sid, '关闭会话')
    return jsonify({'ok': True, 'status': 'closed'})


@ai_chat_bp.route('/sessions/<sid>/reopen', methods=['POST'])
@write_required
def reopen_session(sid):
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    if sess[3] == 'archived':
        return jsonify({'error': '已归档会话不可重开', 'code': 'SESSION_ARCHIVED'}), 403
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE ai_chat_sessions SET status='active' WHERE id=%s AND user_id=%s",
                    (sid, user['userId']))
    log_operation('update', 'ai_chat_session', sid, sid, '重开会话')
    return jsonify({'ok': True, 'status': 'active'})
```
> `_load_session_for_user` 的 token 续期副作用对 `archived` 也会 bump `last_active_at`；可接受（archived 仍被本人加载到才返回，403 早返回前已 bump，无害）。

- [ ] **Step 4: Run, confirm PASS** (4 passed).

- [ ] **Step 5: Commit**:
```
cd E:/wsl/check/check-manage
git add server/routes/ai_chat.py server/tests/test_ai_chat_governance.py
git commit -m "feat(ai-chat): close/reopen session (soft, re-openable) + audit"
```

## Context
- ALREADY on branch `feat/ai-chat-session-governance`. Backend `server/`.
- `_load_session_for_user(sid, user_id)` returns `(id, user_id, opencode_session_id, status, workspace_path)` or None（仅本人）。
- `log_operation(action, target_type, target_id, target_name, description)` 自动从 `flask_g.current_user` 记 operator。`action` 应是 `create/update/delete`（审计 UI 的 ACTION_LABELS 仅识别这三种），用 description 区分 close/reopen。
- `stop_listener(sid)` 已 import 在该文件（chat_persist）。`write_required` 已 import。

---

## Task 2：移除个人物理删 + list 改造 + create 审计

**Files:** Modify `server/routes/ai_chat.py`; Test `server/tests/test_ai_chat_governance.py`.

- [ ] **Step 1: 追加失败测试**:
```python
def test_physical_delete_endpoint_removed():
    r = _client().delete('/ai/chat/sessions/s1', headers=_h())
    assert r.status_code in (404, 405)  # 个人物理删已移除

def test_list_includes_closed():
    import routes.ai_chat as ac
    conn = MagicMock(); cur = MagicMock()
    cur.fetchall.return_value = [('s1','会话1',None,None,None), ('s2','会话2',None,None,None)]
    conn.cursor.return_value = cur
    from contextlib import contextmanager
    @contextmanager
    def fake():
        yield conn
    with patch.object(ac, 'get_db', fake):
        r = _client().get('/ai/chat/sessions', headers=_h())
    assert r.status_code == 200
    # query must include 'closed' in the status filter
    sql = ' '.join(str(c.args[0]) for c in cur.execute.call_args_list)
    assert 'closed' in sql
```

- [ ] **Step 2: Run, confirm FAIL** (delete still 200; list query has no 'closed').

- [ ] **Step 3: 改 ai_chat.py**:
  - **移除** `delete_session`（整个 `@ai_chat_bp.route('/sessions/<sid>', methods=['DELETE'])` 函数）。物理删不再对个人开放。
  - **list 改造**：把 `list_sessions` 的查询 `AND (status = 'active' OR batch_id IS NOT NULL)` 改为
    `AND (status IN ('active','closed') OR batch_id IS NOT NULL)`，并在 SELECT 加 `status`、返回里加 `'status': r[5]`（调整列索引：`SELECT id, title, last_active_at, batch_id, batch_input_file, status` → row 索引 0..5；返回 dict 加 `'status': r[5]`）。
  - **create 审计**：在 `create_session` 成功（持久化 opencode_session_id 后、return 前）加 `log_operation('create', 'ai_chat_session', session_id, '新会话', '创建会话')`。

- [ ] **Step 4: Run, confirm PASS**；并跑既有 chat 测试无回归：
`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/ -k "ai_chat or chat or governance" -q`

- [ ] **Step 5: Commit**:
```
git add server/routes/ai_chat.py server/tests/test_ai_chat_governance.py
git commit -m "feat(ai-chat): remove personal physical delete; list shows closed; audit create"
```

---

## Task 3：admin 治理端点 + 权限 + 审计 UI label

**Files:** Modify `server/routes/ai_chat.py`, `server/utils/permissions.py`, `server/routes/operation_logs.py`; Test `server/tests/test_ai_chat_governance.py`.

- [ ] **Step 1: 失败测试**:
```python
def test_admin_archive_requires_permission():
    # developer (no admin.ai_chat_admin) -> 403
    import routes.ai_chat as ac
    r = _client().post('/ai/chat/sessions/s1/archive', headers=_h(role='developer'))
    assert r.status_code == 403

def test_admin_list_requires_permission():
    r = _client().get('/ai/chat/admin/sessions', headers=_h(role='developer'))
    assert r.status_code == 403
```
> admin 超级用户绕过；developer 无 `admin.ai_chat_admin` → 403。

- [ ] **Step 2: Run, confirm FAIL** (endpoints missing → 404 not 403).

- [ ] **Step 3: 实现**:
  - `server/utils/permissions.py` `PERMISSION_CATALOG` 加一项：
    `{'key': 'admin.ai_chat_admin', 'label': 'AI 会话治理', 'group': '平台管理'},`
  - `server/routes/ai_chat.py` 顶部 import：`from auth import require_permission`（确认已存在 require_permission；与 require_page_action 同模块）。新增：
```python
@ai_chat_bp.route('/admin/sessions', methods=['GET'])
@require_permission('admin.ai_chat_admin')
def admin_list_sessions():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, user_id, title, status, last_active_at FROM ai_chat_sessions "
            "WHERE batch_id IS NULL ORDER BY last_active_at DESC NULLS LAST, id DESC LIMIT 500")
        rows = cur.fetchall()
    return jsonify({'sessions': [
        {'id': r[0], 'userId': r[1], 'title': r[2] or '新会话', 'status': r[3],
         'lastActiveAt': r[4].isoformat() if r[4] else None} for r in rows]})


@ai_chat_bp.route('/sessions/<sid>/archive', methods=['POST'])
@require_permission('admin.ai_chat_admin')
def archive_session(sid):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE ai_chat_sessions SET status='archived' WHERE id=%s", (sid,))
        if cur.rowcount == 0:
            return jsonify({'error': 'session not found'}), 404
    log_operation('update', 'ai_chat_session', sid, sid, '归档会话（admin）')
    return jsonify({'ok': True, 'status': 'archived'})
```
  - `server/routes/operation_logs.py` 的 `TARGET_LABELS` 加：`'ai_chat_session': 'AI会话',`（审计列表友好显示）。

- [ ] **Step 4: Run, confirm PASS**.

- [ ] **Step 5: Commit**:
```
git add server/routes/ai_chat.py server/utils/permissions.py server/routes/operation_logs.py server/tests/test_ai_chat_governance.py
git commit -m "feat(ai-chat): admin session governance (list/archive) + RBAC + audit label"
```

## Context
- `require_permission('admin.x')` 装饰器在 `auth.py`（RBAC capability gate）。admin 内置超级用户对所有 `admin.*` 放行；其它角色需显式授予。
- `archive` 是 admin 操作，对任意会话（不限本人）。

---

## Task 4：前端 close/reopen

**Files:** Modify `src/api/aiChat.ts`, `src/stores/aiChat.ts`, `src/views/ai-chat/AiChatView.vue`.

- [ ] **Step 1: api** — 在 `src/api/aiChat.ts` 把 `deleteSession` 替换为：
```typescript
export function closeSession(id: string) {
  return post<{ ok: boolean; status: string }>(`/ai/chat/sessions/${id}/close`, {})
}
export function reopenSession(id: string) {
  return post<{ ok: boolean; status: string }>(`/ai/chat/sessions/${id}/reopen`, {})
}
```
（确认 `post` 已从 `@/utils/request` 导入；移除不再用的 `del`/`deleteSession` 若仅此处用。）

- [ ] **Step 2: store** — 在 `src/stores/aiChat.ts`：把 import 的 `deleteSession` 换成 `closeSession, reopenSession`；把第 359 行附近的 `await deleteSession(id)` 所在 action 改名/改为 `closeSessionAction(id)` 调 `closeSession(id)`（关闭后把该会话 status 置 'closed' 而非从列表移除），并加 `reopenSessionAction(id)` 调 `reopenSession`（status→'active'）。会话列表项需带 `status` 字段（来自 list 返回）。

- [ ] **Step 3: view** — `src/views/ai-chat/AiChatView.vue`：
  - 把第 525 行的 `<ElIcon @click="removeSession(s.id)"><Delete /></ElIcon>` 改为按 `s.status` 渲染：`active` 显示"关闭"（调 `store.closeSessionAction`），`closed` 显示"重开"（调 `store.reopenSessionAction`）。用合适的 Element Plus 图标（如 `<Close/>` / `<RefreshRight/>`）。
  - `removeSession`（324 行）改为 `closeSessionItem`/`reopenSessionItem`，去掉物理删除语义与确认文案（关闭无需"永久删除"警告）。

- [ ] **Step 4: 类型检查**: `cd E:/wsl/check/check-manage && npx vue-tsc --noEmit -p tsconfig.json` → 无新增错误。

- [ ] **Step 5: Commit**:
```
git add src/api/aiChat.ts src/stores/aiChat.ts src/views/ai-chat/AiChatView.vue
git commit -m "feat(ai-chat): UI close/reopen instead of delete"
```

---

## Task 5：文档

**Files:** `docs/user-guide/ai/`（新增或并入现有 AI 助手文档）、`CLAUDE.md`。

- [ ] **Step 1:** 用户指南补充：会话只能"关闭"（不删除），关闭后可"重开"，历史永久保留；admin 可在审计日志查会话操作。
- [ ] **Step 2:** `CLAUDE.md` AI Agent Chat 段加一句：会话治理——个人 close/reopen（软状态，保留 token/workspace/OpenCode）、物理删除已移除、admin `admin.ai_chat_admin` 可列出/归档；生命周期写 `operation_logs`（`target_type='ai_chat_session'`）。
- [ ] **Step 3: Commit**:
```
git add docs/user-guide CLAUDE.md
git commit -m "docs(ai-chat): session governance & audit"
```

---

## 验收
- [ ] `pytest tests/test_ai_chat_governance.py` 全绿；既有 chat 测试无回归。
- [ ] 个人物理 DELETE 端点已移除；close→closed+审计、reopen→active、archived 不可 reopen、非本人 404。
- [ ] admin list/archive 受 `admin.ai_chat_admin` 保护（无权限 403）；审计写入 operation_logs。
- [ ] 前端 `vue-tsc` clean；列表按 status 显示关闭/重开。
- [ ] 手动：关闭会话→列表灰显+可重开→重开后能继续（OpenCode 失效则 M3 重建）；admin 审计页按 `target_type=ai_chat_session` 看到操作记录。

---

## 测试验证记录（更正）

> 更正 PR #63 描述里的 Test Plan。

实测结果：**后端完整测试套件 `953 passed, 0 failed`（含 `test_backup.py` 全部 40 个用例），`vue-tsc` clean，前端 aiChat store 测试通过。**

PR #63 原 Test Plan 写的 "913 passed，`test_backup.py` 因 chromadb 原生崩溃被排除" **不准确**：那是把前台 `pytest > 文件` 的输出在崩溃前误判为进程崩溃所致——实为输出缓冲被截断的假象，并非真实 segfault。三次独立复核确认无崩溃：`test_backup.py` 单独跑 40 passed；mem0/chromadb 相关测试 + backup 同进程 49 passed；完整套件 953 passed。

补充：`server/utils/memory.py` 注释提到的 chromadb/onnxruntime segfault 是**运行态 Flask 服务**下跨线程使用原生库的历史问题（已由"mem0 调用钉单线程 executor"修复），与 pytest 全量运行无关。
