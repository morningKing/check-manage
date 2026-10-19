# 智能客服 人工接管（Phase 2）子项目 A：后端 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 后端实现客服人工接管：访客转人工 → 管理员接管（消息不再走 AI，改人工回复）→ 释放，含状态机、消息路由、访客/管理端接口与审计。

**Architecture:** 复用现有 `ai_chat_sessions`（含 `needs_human`/`human_takeover`）+ `kefu_repo`。新增 `human_agent_id` 列；`kefu_repo` 加状态/查询函数；`kefu_public.send_message` 接管时跳过 OpenCode；`kefu_admin` 加队列/接管/释放/人工回复接口。纯后端，pytest 可测。

**Tech Stack:** Python + Flask + psycopg2 + pytest（mock_cursor 仓库测试 + client/`@patch` 路由测试）。

## Global Constraints

- **纯后端**，不改前端。实时送达用轮询（前端子项目 B），后端只存取/改状态。
- 人工回复存 `ai_chat_messages`：`role='assistant'`、`meta={'author':'human','agent_user_id':<userId>}`。
- 权限：管理端接口 `@require_permission('admin.kefu')`；访客接口靠 `load_kefu_session(sid, vid)` 的 X-Visitor-Id 归属校验。
- 接管中 `send_message` **必须跳过** `send_prompt_async` + `ensure_listener`（不派 AI）；非接管行为**逐字不变**。
- 当前管理员 id：`g.current_user['userId']`。审计用 `log_operation(action, 'kefu_session', sid, None, desc)`（自动带 actor）。
- `load_kefu_session` 末尾**追加** `human_takeover`（新 index 6）；现有索引 0..5 不变（4 处调用均按 `sess[n]` 取值，无解包，已核实）。
- 测试：`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/<file> -v`。提交只 `git add` 该任务确切文件，禁止 `git add -A`。

---

### Task 1: 迁移 + 仓库层（`kefu_repo`）

**Files:**
- Modify: `server/init_db.py`（加 `human_agent_id` 幂等 ALTER）
- Modify: `server/utils/kefu_repo.py`（扩展 `load_kefu_session` + 新增 6 函数 + 2 helper）
- Create: `server/tests/test_kefu_takeover_repo.py`

**Interfaces:**
- Produces: `set_needs_human(sid, value=True)->bool`、`get_kefu_session_admin(sid)->dict|None`、`takeover_session(sid, agent_id)->bool`、`release_session(sid)->bool`、`insert_human_message(sid, text, agent_id)->str`、`list_kefu_sessions_admin(instance_id=None, needs_human=None, takeover=None, status='active')->list`、`get_messages(sid)->list`；`load_kefu_session` 返回 7 元组（index 6 = `human_takeover`）。

- [ ] **Step 1: 写失败测试**

`server/tests/test_kefu_takeover_repo.py`:

```python
from contextlib import contextmanager
from unittest.mock import patch
import utils.kefu_repo as repo


def _cm(conn):
    @contextmanager
    def cm():
        yield conn
    return cm()


def test_load_kefu_session_includes_human_takeover(mock_conn, mock_cursor):
    mock_cursor.fetchone.return_value = (
        'sess_1', 'user-bot', 'oc_1', 'active', '/ws', 'kf_1', True)
    with patch('utils.kefu_repo.get_db', lambda: _cm(mock_conn)):
        sess = repo.load_kefu_session('sess_1', 'v1')
    assert sess[6] is True  # human_takeover at index 6
    sql = ' '.join(c.args[0] for c in mock_cursor.execute.call_args_list)
    assert 'human_takeover' in sql


def test_set_needs_human_updates_scoped(mock_conn, mock_cursor):
    mock_cursor.rowcount = 1
    with patch('utils.kefu_repo.get_db', lambda: _cm(mock_conn)):
        ok = repo.set_needs_human('sess_1', True)
    assert ok is True
    sql = ' '.join(c.args[0] for c in mock_cursor.execute.call_args_list)
    assert 'needs_human' in sql and 'kefu_instance_id IS NOT NULL' in sql


def test_takeover_sets_flags_and_clears_needs_human(mock_conn, mock_cursor):
    mock_cursor.rowcount = 1
    with patch('utils.kefu_repo.get_db', lambda: _cm(mock_conn)):
        ok = repo.takeover_session('sess_1', 'user-admin')
    assert ok is True
    sql = ' '.join(c.args[0] for c in mock_cursor.execute.call_args_list)
    assert 'human_takeover=true' in sql and 'needs_human=false' in sql
    params = [p for c in mock_cursor.execute.call_args_list if c.args[1] for p in c.args[1]]
    assert 'user-admin' in params and 'sess_1' in params


def test_release_clears_flags(mock_conn, mock_cursor):
    mock_cursor.rowcount = 1
    with patch('utils.kefu_repo.get_db', lambda: _cm(mock_conn)):
        ok = repo.release_session('sess_1')
    assert ok is True
    sql = ' '.join(c.args[0] for c in mock_cursor.execute.call_args_list)
    assert 'human_takeover=false' in sql and 'human_agent_id=NULL' in sql


def test_insert_human_message_marks_meta(mock_conn, mock_cursor):
    with patch('utils.kefu_repo.get_db', lambda: _cm(mock_conn)):
        mid = repo.insert_human_message('sess_1', '你好', 'user-admin')
    assert mid.startswith('msg_')
    sql = ' '.join(c.args[0] for c in mock_cursor.execute.call_args_list)
    assert 'ai_chat_messages' in sql and "'assistant'" in sql
    params = [p for c in mock_cursor.execute.call_args_list if c.args[1] for p in c.args[1]]
    joined = ' '.join(str(p) for p in params)
    assert 'human' in joined and 'user-admin' in joined  # meta author + agent id


def test_list_sessions_admin_filters(mock_conn, mock_cursor):
    mock_cursor.fetchall.return_value = []
    with patch('utils.kefu_repo.get_db', lambda: _cm(mock_conn)):
        repo.list_kefu_sessions_admin(instance_id='kf_1', needs_human=True)
    call = mock_cursor.execute.call_args_list[-1]
    sql, params = call.args[0], call.args[1]
    assert 'kefu_instance_id=%s' in sql and 'needs_human=%s' in sql
    assert 'kf_1' in params and True in params


def test_get_messages_returns_meta(mock_conn, mock_cursor):
    mock_cursor.fetchall.return_value = [
        ('msg_1', 'assistant', [{'type': 'text', 'text': 'hi'}],
         {'author': 'human'}, None)]
    with patch('utils.kefu_repo.get_db', lambda: _cm(mock_conn)):
        out = repo.get_messages('sess_1')
    assert out[0]['role'] == 'assistant' and out[0]['meta'] == {'author': 'human'}
    sql = ' '.join(c.args[0] for c in mock_cursor.execute.call_args_list)
    assert 'meta' in sql and 'ai_chat_messages' in sql
```

- [ ] **Step 2: 运行确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_kefu_takeover_repo.py -v`
Expected: FAIL（`AttributeError: module 'utils.kefu_repo' has no attribute 'set_needs_human'` 等）。

- [ ] **Step 3: 加迁移列（`init_db.py`）**

在 `server/init_db.py` 现有 kefu 会话增列处（`ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS human_takeover ...` 那一段之后）加一行：

```python
ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS human_agent_id VARCHAR(100);
```

（该 DDL 位于 init_db 的 SQL 字符串块中，与相邻 `needs_human`/`human_takeover` 的 `ALTER` 并列。）

- [ ] **Step 4: 扩展 `load_kefu_session` + 新增仓库函数**

在 `server/utils/kefu_repo.py`，把 `load_kefu_session` 改为（追加 `human_takeover`）：

```python
def load_kefu_session(session_id: str, visitor_id: str):
    """返回 (id, user_id, opencode_session_id, status, workspace_path,
    kefu_instance_id, human_takeover) 或 None（session 不存在或 visitor 不匹配）。"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, user_id, opencode_session_id, status, workspace_path, "
            "kefu_instance_id, human_takeover FROM ai_chat_sessions "
            "WHERE id=%s AND visitor_id=%s AND kefu_instance_id IS NOT NULL",
            (session_id, visitor_id),
        )
        return cur.fetchone()
```

在文件末尾追加以下 helper 与函数：

```python
def _msg_preview(content, n: int = 60) -> str:
    """从消息 content（parts 数组）提取纯文本预览。"""
    if not isinstance(content, list):
        return ''
    text = ''.join(p.get('text', '') for p in content
                   if isinstance(p, dict) and p.get('type') == 'text')
    return text[:n]


def _iso(ts):
    return ts.isoformat() if ts else None


def set_needs_human(session_id: str, value: bool = True) -> bool:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ai_chat_sessions SET needs_human=%s "
            "WHERE id=%s AND kefu_instance_id IS NOT NULL",
            (value, session_id))
        return cur.rowcount > 0


def get_kefu_session_admin(session_id: str):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, kefu_instance_id, visitor_id, needs_human, human_takeover, "
            "human_agent_id, status, opencode_session_id, workspace_path "
            "FROM ai_chat_sessions WHERE id=%s AND kefu_instance_id IS NOT NULL",
            (session_id,))
        r = cur.fetchone()
    if not r:
        return None
    return {'id': r[0], 'kefu_instance_id': r[1], 'visitor_id': r[2],
            'needs_human': r[3], 'human_takeover': r[4], 'human_agent_id': r[5],
            'status': r[6], 'opencode_session_id': r[7], 'workspace_path': r[8]}


def takeover_session(session_id: str, agent_id: str) -> bool:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ai_chat_sessions SET human_takeover=true, human_agent_id=%s, "
            "needs_human=false WHERE id=%s AND kefu_instance_id IS NOT NULL",
            (agent_id, session_id))
        return cur.rowcount > 0


def release_session(session_id: str) -> bool:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ai_chat_sessions SET human_takeover=false, human_agent_id=NULL "
            "WHERE id=%s AND kefu_instance_id IS NOT NULL",
            (session_id,))
        return cur.rowcount > 0


def insert_human_message(session_id: str, text: str, agent_id: str) -> str:
    msg_id = 'msg_' + secrets.token_hex(6)
    content = json.dumps([{'type': 'text', 'text': text}])
    meta = json.dumps({'author': 'human', 'agent_user_id': agent_id})
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ai_chat_messages (id, session_id, role, content, meta) "
            "VALUES (%s,%s,'assistant',%s,%s)",
            (msg_id, session_id, content, meta))
    return msg_id


def list_kefu_sessions_admin(instance_id=None, needs_human=None,
                             takeover=None, status='active') -> list:
    clauses = ["kefu_instance_id IS NOT NULL"]
    params = []
    if instance_id:
        clauses.append("kefu_instance_id=%s"); params.append(instance_id)
    if needs_human is not None:
        clauses.append("needs_human=%s"); params.append(needs_human)
    if takeover is not None:
        clauses.append("human_takeover=%s"); params.append(takeover)
    if status:
        clauses.append("status=%s"); params.append(status)
    where = " AND ".join(clauses)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, kefu_instance_id, visitor_id, needs_human, human_takeover, "
            "human_agent_id, status, created_at, last_active_at, "
            "(SELECT content FROM ai_chat_messages m WHERE m.session_id=s.id "
            " ORDER BY created_at DESC LIMIT 1) "
            "FROM ai_chat_sessions s WHERE " + where +
            " ORDER BY last_active_at DESC NULLS LAST",
            tuple(params))
        rows = cur.fetchall()
    return [{'id': r[0], 'kefu_instance_id': r[1], 'visitor_id': r[2],
             'needs_human': r[3], 'human_takeover': r[4], 'human_agent_id': r[5],
             'status': r[6], 'created_at': _iso(r[7]), 'last_active_at': _iso(r[8]),
             'last_message': _msg_preview(r[9])} for r in rows]


def get_messages(session_id: str) -> list:
    """管理员视角取会话全部消息（含 meta，用于区分人工/AI）。"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, role, content, meta, created_at FROM ai_chat_messages "
            "WHERE session_id=%s ORDER BY created_at ASC", (session_id,))
        rows = cur.fetchall()
    return [{'id': r[0], 'role': r[1], 'content': r[2], 'meta': r[3],
             'createdAt': _iso(r[4])} for r in rows]
```

- [ ] **Step 5: 运行确认通过**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_kefu_takeover_repo.py -v`
Expected: PASS（7 用例）。

- [ ] **Step 6: Commit**

```bash
git add server/init_db.py server/utils/kefu_repo.py server/tests/test_kefu_takeover_repo.py
git commit -m "feat(kefu): 人工接管仓库层（状态/查询/人工消息）+ human_agent_id 列"
```

---

### Task 2: 访客侧 — 消息路由改造 + request-human（`kefu_public`）

**Files:**
- Modify: `server/routes/kefu_public.py`（`send_message` 加接管分支 + 新增 `request_human` 路由）
- Create: `server/tests/test_kefu_takeover_public.py`

**Interfaces:**
- Consumes: `kefu_repo.load_kefu_session`（7 元组，`sess[6]=human_takeover`）、`kefu_repo.set_needs_human`。

- [ ] **Step 1: 写失败测试**

`server/tests/test_kefu_takeover_public.py`:

```python
from unittest.mock import patch, MagicMock

_H = {'X-Visitor-Id': 'v1'}
# load_kefu_session 7-tuple: (id,user,oc,status,ws,kefu_instance_id,human_takeover)
def _sess(takeover):
    return ('sess_1', 'user-bot', 'oc_1', 'active', '/ws', 'kf_1', takeover)


def test_send_message_takeover_skips_opencode(client):
    with patch('routes.kefu_public.kefu_repo.load_kefu_session', return_value=_sess(True)), \
         patch('routes.kefu_public.kefu_repo.get_instance',
               return_value={'id': 'kf_1', 'model': None, 'agent': None, 'rate_limit': {}}), \
         patch('routes.kefu_public.OpenCodeClient') as OC, \
         patch('routes.kefu_public.ensure_listener') as EL:
        resp = client.post('/kefu/sessions/sess_1/messages',
                           json={'content': '你好'}, headers=_H)
    assert resp.status_code == 202
    OC.return_value.send_prompt_async.assert_not_called()
    EL.assert_not_called()


def test_send_message_normal_calls_opencode(client):
    with patch('routes.kefu_public.kefu_repo.load_kefu_session', return_value=_sess(False)), \
         patch('routes.kefu_public.kefu_repo.get_instance',
               return_value={'id': 'kf_1', 'model': None, 'agent': None, 'rate_limit': {}}), \
         patch('routes.kefu_public.OpenCodeClient') as OC, \
         patch('routes.kefu_public.ensure_listener'):
        resp = client.post('/kefu/sessions/sess_1/messages',
                           json={'content': '你好'}, headers=_H)
    assert resp.status_code == 202
    OC.return_value.send_prompt_async.assert_called_once()


def test_request_human_sets_flag(client):
    with patch('routes.kefu_public.kefu_repo.load_kefu_session', return_value=_sess(False)), \
         patch('routes.kefu_public.kefu_repo.set_needs_human', return_value=True) as SN:
        resp = client.post('/kefu/sessions/sess_1/request-human', headers=_H)
    assert resp.status_code == 200
    assert resp.get_json()['needsHuman'] is True
    SN.assert_called_once_with('sess_1', True)


def test_request_human_wrong_visitor_404(client):
    with patch('routes.kefu_public.kefu_repo.load_kefu_session', return_value=None):
        resp = client.post('/kefu/sessions/sess_1/request-human', headers=_H)
    assert resp.status_code == 404
```

- [ ] **Step 2: 运行确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_kefu_takeover_public.py -v`
Expected: FAIL（request-human 路由不存在 404 不匹配 / 接管分支未实现 → send_prompt_async 被调用）。

- [ ] **Step 3: send_message 加接管分支**

在 `server/routes/kefu_public.py` 的 `send_message` 里，找到存完用户消息后、创建 OpenCode 客户端之前的位置（现有代码）：

```python
    client = OpenCodeClient(OPENCODE_BASE_URL)
    oc_sid = sess[2]
```

在这两行**之前**插入接管分支：

```python
    if sess[6]:  # human_takeover：不派 AI，等人工回复（访客轮询取回复）
        return jsonify({'messageId': msg_id, 'humanTakeover': True}), 202

```

（其余 OpenCode 派发逻辑保持不变。）

- [ ] **Step 4: 新增 request-human 路由**

在 `server/routes/kefu_public.py` 里 `send_message` 函数之后（`events` 路由之前）新增：

```python
@kefu_public_bp.route('/sessions/<sid>/request-human', methods=['POST'])
def request_human(sid):
    vid = _visitor_id()
    sess = kefu_repo.load_kefu_session(sid, vid)
    if not sess:
        return jsonify({'error': 'session not found'}), 404
    kefu_repo.set_needs_human(sid, True)
    return jsonify({'needsHuman': True}), 200
```

- [ ] **Step 5: 运行确认通过**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_kefu_takeover_public.py tests/test_kefu_public_routes.py -v`
Expected: PASS（新 4 用例 + 现有 kefu_public 路由测试无回归）。

- [ ] **Step 6: Commit**

```bash
git add server/routes/kefu_public.py server/tests/test_kefu_takeover_public.py
git commit -m "feat(kefu): 访客转人工端点 + 接管中 send_message 跳过 AI"
```

---

### Task 3: 管理端 — 队列/接管/释放/人工回复（`kefu_admin`）

**Files:**
- Modify: `server/routes/kefu_admin.py`（import `g` + 5 个 session 路由）
- Create: `server/tests/test_kefu_takeover_admin.py`

**Interfaces:**
- Consumes: `kefu_repo.list_kefu_sessions_admin`、`get_kefu_session_admin`、`takeover_session`、`release_session`、`insert_human_message`、`get_messages`（会话消息读取，见下）。

- [ ] **Step 1: 写失败测试**

`server/tests/test_kefu_takeover_admin.py`:

```python
from unittest.mock import patch

_SESS = {'id': 'sess_1', 'kefu_instance_id': 'kf_1', 'visitor_id': 'v1',
         'needs_human': True, 'human_takeover': False, 'human_agent_id': None,
         'status': 'active', 'opencode_session_id': 'oc_1', 'workspace_path': '/ws'}


def test_queue_requires_admin_kefu(client, dev_headers):
    resp = client.get('/admin/kefu/sessions', headers=dev_headers)
    assert resp.status_code == 403


def test_queue_lists_sessions(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.list_kefu_sessions_admin',
               return_value=[{'id': 'sess_1', 'needs_human': True}]) as L:
        resp = client.get('/admin/kefu/sessions?needs_human=1&instance=kf_1',
                          headers=admin_headers)
    assert resp.status_code == 200
    assert resp.get_json()['sessions'][0]['id'] == 'sess_1'
    kwargs = L.call_args.kwargs
    assert kwargs['needs_human'] is True and kwargs['instance_id'] == 'kf_1'


def test_takeover_sets_and_logs(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.get_kefu_session_admin', return_value=dict(_SESS)), \
         patch('routes.kefu_admin.kefu_repo.takeover_session', return_value=True) as T, \
         patch('routes.kefu_admin.log_operation') as LG:
        resp = client.post('/admin/kefu/sessions/sess_1/takeover', headers=admin_headers)
    assert resp.status_code == 200
    assert resp.get_json()['humanTakeover'] is True
    T.assert_called_once_with('sess_1', 'user-admin')
    LG.assert_called_once()


def test_takeover_missing_session_404(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.get_kefu_session_admin', return_value=None):
        resp = client.post('/admin/kefu/sessions/sess_x/takeover', headers=admin_headers)
    assert resp.status_code == 404


def test_release_clears_and_logs(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.get_kefu_session_admin', return_value=dict(_SESS)), \
         patch('routes.kefu_admin.kefu_repo.release_session', return_value=True) as R, \
         patch('routes.kefu_admin.log_operation'):
        resp = client.post('/admin/kefu/sessions/sess_1/release', headers=admin_headers)
    assert resp.status_code == 200
    assert resp.get_json()['humanTakeover'] is False
    R.assert_called_once_with('sess_1')


def test_human_reply_when_takeover(client, admin_headers):
    s = dict(_SESS, human_takeover=True)
    with patch('routes.kefu_admin.kefu_repo.get_kefu_session_admin', return_value=s), \
         patch('routes.kefu_admin.kefu_repo.insert_human_message', return_value='msg_1') as I:
        resp = client.post('/admin/kefu/sessions/sess_1/messages',
                           json={'content': '您好'}, headers=admin_headers)
    assert resp.status_code == 201
    assert resp.get_json()['messageId'] == 'msg_1'
    I.assert_called_once_with('sess_1', '您好', 'user-admin')


def test_human_reply_not_in_takeover_409(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.get_kefu_session_admin', return_value=dict(_SESS)), \
         patch('routes.kefu_admin.kefu_repo.insert_human_message') as I:
        resp = client.post('/admin/kefu/sessions/sess_1/messages',
                           json={'content': '您好'}, headers=admin_headers)
    assert resp.status_code == 409
    I.assert_not_called()


def test_human_reply_empty_content_400(client, admin_headers):
    s = dict(_SESS, human_takeover=True)
    with patch('routes.kefu_admin.kefu_repo.get_kefu_session_admin', return_value=s):
        resp = client.post('/admin/kefu/sessions/sess_1/messages',
                           json={'content': '   '}, headers=admin_headers)
    assert resp.status_code == 400
```

- [ ] **Step 2: 运行确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_kefu_takeover_admin.py -v`
Expected: FAIL（路由不存在 → 404/405）。

- [ ] **Step 3: 加 `g` 导入 + 5 个 session 路由**

在 `server/routes/kefu_admin.py` 顶部把：

```python
from flask import Blueprint, request, jsonify
```

改为：

```python
from flask import Blueprint, request, jsonify, g
```

在文件末尾追加：

```python
# ==================== 人工接管（会话） ====================

@kefu_admin_bp.route('/sessions', methods=['GET'])
@require_permission('admin.kefu')
def list_sessions():
    def _b(v):
        return None if v is None else v in ('1', 'true', 'True')
    sessions = kefu_repo.list_kefu_sessions_admin(
        instance_id=request.args.get('instance') or None,
        needs_human=_b(request.args.get('needs_human')),
        takeover=_b(request.args.get('takeover')),
        status=request.args.get('status', 'active') or None,
    )
    return jsonify({'sessions': sessions})


@kefu_admin_bp.route('/sessions/<sid>/messages', methods=['GET'])
@require_permission('admin.kefu')
def get_session_messages(sid):
    if not kefu_repo.get_kefu_session_admin(sid):
        return jsonify({'error': 'not found'}), 404
    return jsonify({'messages': kefu_repo.get_messages(sid)})


@kefu_admin_bp.route('/sessions/<sid>/takeover', methods=['POST'])
@require_permission('admin.kefu')
def takeover_session_route(sid):
    if not kefu_repo.get_kefu_session_admin(sid):
        return jsonify({'error': 'not found'}), 404
    kefu_repo.takeover_session(sid, g.current_user['userId'])
    log_operation('takeover', 'kefu_session', sid, None, '接管客服会话')
    return jsonify({'humanTakeover': True})


@kefu_admin_bp.route('/sessions/<sid>/release', methods=['POST'])
@require_permission('admin.kefu')
def release_session_route(sid):
    if not kefu_repo.get_kefu_session_admin(sid):
        return jsonify({'error': 'not found'}), 404
    kefu_repo.release_session(sid)
    log_operation('release', 'kefu_session', sid, None, '释放客服会话')
    return jsonify({'humanTakeover': False})


@kefu_admin_bp.route('/sessions/<sid>/messages', methods=['POST'])
@require_permission('admin.kefu')
def human_reply(sid):
    sess = kefu_repo.get_kefu_session_admin(sid)
    if not sess:
        return jsonify({'error': 'not found'}), 404
    if not sess['human_takeover']:
        return jsonify({'error': '会话未处于人工接管状态'}), 409
    content = ((request.get_json(silent=True) or {}).get('content') or '').strip()
    if not content:
        return jsonify({'error': 'content required'}), 400
    mid = kefu_repo.insert_human_message(sid, content, g.current_user['userId'])
    return jsonify({'messageId': mid}), 201
```

  （`get_session_messages` 路由调用 Task 1 已实现的 `kefu_repo.get_messages(sid)`。）

- [ ] **Step 4: 运行确认通过 + 无回归**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_kefu_takeover_admin.py tests/test_kefu_admin_routes.py -v`
Expected: PASS（新 8 用例 + 现有 kefu_admin 路由测试无回归）。

- [ ] **Step 5: Commit**

```bash
git add server/routes/kefu_admin.py server/tests/test_kefu_takeover_admin.py
git commit -m "feat(kefu): 管理端会话队列/接管/释放/人工回复接口 + 审计"
```

---

### Task 4: 文档 + 实机 curl 冒烟

**Files:**
- Modify: `docs/user-guide/ai/smart-customer-service.md`（人工接管后端接口小节）

- [ ] **Step 1: 确认服务在运行**

后端 `:3002` 在跑（`python app.py` 或 `npm run dev:all`）；改了后端代码需**重启**后端加载。OpenCode `:4096` 供正常 AI 路径（接管路径不需要）。

- [ ] **Step 2: 实机 curl 冒烟整条链路**

以 admin 登录取 JWT（`POST /auth/login` admin/admin123 → token）。用一次性访客 id：

```bash
# 建会话（访客）
curl -s -X POST localhost:3002/kefu/i/demo/sessions -H "X-Visitor-Id: takeover-smoke" -H "Content-Type: application/json" -d '{}'
# → 记下 sid
# 转人工
curl -s -X POST localhost:3002/kefu/sessions/<sid>/request-human -H "X-Visitor-Id: takeover-smoke"
# 管理员接管
curl -s -X POST localhost:3002/admin/kefu/sessions/<sid>/takeover -H "Authorization: Bearer <JWT>"
# 访客在接管中发消息（应存但不触发 AI）
curl -s -X POST localhost:3002/kefu/sessions/<sid>/messages -H "X-Visitor-Id: takeover-smoke" -H "Content-Type: application/json" -d '{"content":"我要人工"}'
# 人工回复
curl -s -X POST localhost:3002/admin/kefu/sessions/<sid>/messages -H "Authorization: Bearer <JWT>" -H "Content-Type: application/json" -d '{"content":"您好，我是人工客服"}'
# 访客取历史（应看到 user 消息 + 人工 assistant 消息，且 meta.author=human）
curl -s localhost:3002/kefu/sessions/<sid>/messages -H "X-Visitor-Id: takeover-smoke"
# 释放
curl -s -X POST localhost:3002/admin/kefu/sessions/<sid>/release -H "Authorization: Bearer <JWT>"
```

断言：接管后访客发消息**未产生 AI 回复**（历史里只有 user + 人工 assistant）；`meta.author=human`；释放后 `human_takeover=false`。事后删除该测试会话行（`DELETE FROM ai_chat_sessions WHERE visitor_id='takeover-smoke'`）。

- [ ] **Step 3: 更新用户指南**

在 `docs/user-guide/ai/smart-customer-service.md` 增补「## 人工接管（Phase 2 · 后端接口）」小节：状态机（needs_human/human_takeover）、访客 `POST /kefu/sessions/<sid>/request-human`、管理端 `admin.kefu` 下 `GET /admin/kefu/sessions`（队列）/`GET .../messages`/`POST .../takeover`/`POST .../release`/`POST .../messages`（人工回复，非接管 409），接管中 AI 停派、访客靠轮询；标注管理端控制台 UI 属子项目 B（后续）。

- [ ] **Step 4: Commit**

```bash
git add docs/user-guide/ai/smart-customer-service.md
git commit -m "docs(kefu): 人工接管后端接口说明"
```

---

## Self-Review

**Spec coverage:**
- `human_agent_id` 迁移 + 仓库函数 + `load_kefu_session` 扩展 → Task 1 ✓
- 消息路由（接管跳过 OpenCode，非接管不变）+ 访客 request-human → Task 2 ✓
- 管理端队列/读消息/takeover/release/人工回复（409/400/404/403）+ 审计 → Task 3 ✓
- 人工消息 role=assistant + meta.author=human → Task 1 `insert_human_message` + Task 3 调用 ✓
- 权限 admin.kefu、访客归属校验 → Task 2/3 ✓
- 文档 + 实机冒烟 → Task 4 ✓

**Placeholder scan:** 无 TBD/TODO；code step 均含完整代码；测试含具体断言。Task 3 Step 4 对 `get_messages` 存在性留了「已存在则复用」的判定——非占位，是明确的择一指令。✓

**Type consistency:**
- 仓库函数签名（Task 1 定义）与路由调用（Task 2/3）一致：`set_needs_human(sid, True)`、`takeover_session(sid, agent_id)`、`release_session(sid)`、`insert_human_message(sid, content, agent_id)`、`get_kefu_session_admin(sid)['human_takeover']`、`list_kefu_sessions_admin(instance_id=, needs_human=, takeover=, status=)` ✓
- `load_kefu_session` 7 元组 `sess[6]=human_takeover` 在 Task 1 产出、Task 2 `send_message` 消费一致 ✓
- `g.current_user['userId']`（Task 3）与 conftest admin_token（id→userId='user-admin'）一致 ✓
