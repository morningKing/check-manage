# 普通会话 bypass 模式 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 普通会话新增 bypass 模式——服务端监听器自动放行 OpenCode 权限询问（once），会话级开关落库，developer+ 门控。

**Architecture:** 权限询问事件到达 `chat_persist._run_listener` 全量事件循环时查库读 `permission_mode`，bypass 则 `reply_permission('once')`（子代理询问按父会话模式归属）；切换端点翻转库值并立即放行存量挂起；前端按模式分流 PermissionCard→「⚡ 已自动放行」chip。

**Tech Stack:** Flask + psycopg2、Pinia + Vue3 + Element Plus、pytest / vitest / Playwright。

**Spec:** `docs/superpowers/specs/2026-10-31-ai-chat-bypass-mode-design.md`（计划与 spec 一起读；决策以 spec 为准）

## Global Constraints

- 源码改动（含 `server/**/*.py`）必须走 Edit/Write 工具；迁移**双注册**（migrations/ 文件 + `server/app.py` 注册块 exec_module 不可省 + `server/db_schema/core.py` 幂等 DDL）。
- server 单测跑 `casemanage_test`（conftest 引导）；**`tests/test_routes_ai_chat.py` 单独执行会挂起**（boot 迁移回填循环 × MagicMock，根因已钉）——新测试落独立文件；需要其 `setup` fixture 时本地复制并注明出处；要跑该文件内某个测试时，把一个先 import app 的测试文件放在同一命令前面（如 `pytest tests/test_ai_chat_feedback.py "tests/test_routes_ai_chat.py::xxx" -q`）。
- TDD；每任务独立 commit；E2E 用 `e2e/ai-full/helpers.ts`（api/openChatSession/tag/waitFor）；store 测试 mock 工厂同步补新 API 函数。
- 角色语义：`@write_required` = 非 guest（developer/admin 放行、guest 403）——即 spec 的「developer+」门。
- 权限事件形状（已核实）：oc 事件 `{event: 'permission.asked', data: {properties: {id, sessionID, permission, patterns, metadata}}}`；前端 `PermissionRequest`（`api/aiChat.ts:498`）同构。

---

### Task 1: permission_mode 列迁移（三处落点）

**Files:**
- Create: `server/migrations/2026_10_31_session_permission_mode.py`
- Modify: `server/db_schema/core.py`（ai_chat_sessions 建表块之后）
- Modify: `server/app.py`（迁移注册块末尾，序号顺延现有最大 `_mpNN`——先 `grep -n "_mp" server/app.py | tail -3` 取基线）
- Test: `server/tests/test_ai_chat_permission_mode.py`（新建，本特性后端测试都放这里）

**Interfaces:**
- Produces: `ai_chat_sessions.permission_mode VARCHAR(10) DEFAULT 'ask'`；Task 2/3 全部依赖。

- [ ] **Step 1: 写失败测试**（fixture 模式照 `test_ai_chat_messages_paging.py` 的 `_client/_h/seeded`——seeded 只需建一个会话，无需消息）：

```python
"""ai_chat_sessions.permission_mode 列迁移 + 会话 bypass 开关测试。"""
import sys, os, uuid
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_permission_mode_column_exists():
    """迁移幂等 + 新会话默认 'ask'。"""
    import importlib, psycopg2
    from config import DB_CONFIG
    mig = importlib.import_module('migrations.2026_10_31_session_permission_mode')
    mig.run()
    mig.run()  # 幂等可重跑
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT column_name, column_default FROM information_schema.columns "
                "WHERE table_name='ai_chat_sessions' AND column_name='permission_mode'")
    name, default = cur.fetchone()
    assert name == 'permission_mode' and "'ask'" in (default or '')
    sid = f'sess_pm_{uuid.uuid4().hex[:12]}'
    cur.execute("INSERT INTO ai_chat_sessions (id, user_id, title, status) "
                "VALUES (%s, 'user-owner', 'pm-test', 'active')", (sid,))
    conn.commit()
    cur.execute("SELECT permission_mode FROM ai_chat_sessions WHERE id=%s", (sid,))
    assert cur.fetchone()[0] == 'ask'
    cur.execute("DELETE FROM ai_chat_sessions WHERE id=%s", (sid,))
    conn.commit()
    conn.close()
```

- [ ] **Step 2: 跑失败**：`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest server/tests/test_ai_chat_permission_mode.py -v` → FAIL（列不存在）。
- [ ] **Step 3: 迁移文件**：

```python
# -*- coding: utf-8 -*-
"""会话级权限模式列（2026-10-31 bypass spec §2.1）。

'ask'=照常弹权限卡（默认）；'bypass'=监听器自动放行权限询问（once）。
新会话一律 'ask'（安全默认，不继承）。幂等可重跑。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db


def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("ALTER TABLE ai_chat_sessions "
                    "ADD COLUMN IF NOT EXISTS permission_mode VARCHAR(10) DEFAULT 'ask'")
```

- [ ] **Step 4: core.py**：ai_chat_sessions 的 CREATE TABLE 块后加一行 `ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS permission_mode VARCHAR(10) DEFAULT 'ask';`（带一行注释 `-- 会话级权限模式：ask=弹卡 / bypass=自动放行（2026-10-31 bypass spec）`）。
- [ ] **Step 5: app.py 注册块**（照 2026_10_30 反馈表迁移的块形态，变量后缀顺延、`exec_module` 不可省，warning 文案 `'session permission_mode migration on boot failed: %s'`）。
- [ ] **Step 6: 跑通过** → PASS。
- [ ] **Step 7: Commit**：`git commit -m "feat(ai-chat): 会话 permission_mode 列迁移（bypass 三处落点）"`

### Task 2: 切换端点 PATCH /permission-mode

**Files:**
- Modify: `server/routes/ai_chat.py`（`reply_permission` 路由之后）
- Test: `server/tests/test_ai_chat_permission_mode.py`（追加）

**Interfaces:**
- Consumes: Task 1 列；`_load_session_for_user`（七元组：id,user_id,oc_id,status,workspace,batch_id,orchestration_run_id）；`_execution_controlled(sess)`（batch_id=sess[5]/orch=sess[6] + 非终态 status → True）；`_child_session_agents(client, sess)`；`OpenCodeClient(OPENCODE_BASE_URL)` 的 `list_permissions(directory)`/`reply_permission(request_id, response, directory=)`。
- Produces: `PATCH /ai/chat/sessions/<sid>/permission-mode` body `{"mode": "ask"|"bypass"}` → `{"sessionId", "mode"}`；错误 `SESSION_NOT_FOUND(404)/BATCH_CONTROLLED(409)/BAD_MODE(400)`；bypass 翻转即放行存量挂起（含 kids）。

- [ ] **Step 1: 写失败测试**。文件头部加 setup 夹具（**本地复制** `test_routes_ai_chat.py:22` 的 `setup` fixture 与 `_make_mock_db` + 其 mock_conn/mock_cursor 来自 conftest，头部注释 `# setup 夹具复制自 test_routes_ai_chat.py（该文件单独执行挂起，根因=boot 迁移回填循环×MagicMock）`）。用例：

```python
def test_toggle_validation_and_roles(setup):
    client, cursor, oc, dev_h, guest_h, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active',
                                    '/tmp/ws', None, None)
    # guest 403（write_required）
    assert client.patch('/ai/chat/sessions/sess_x/permission-mode',
                        json={'mode': 'bypass'}, headers=guest_h).status_code == 403
    # 非属主 404
    assert client.patch('/ai/chat/sessions/other-sess/permission-mode',
                        json={'mode': 'bypass'}, headers=dev_h).status_code == 404
    # 非法值 400
    assert client.patch('/ai/chat/sessions/sess_x/permission-mode',
                        json={'mode': 'yolo'}, headers=dev_h).status_code == 400


def test_toggle_bypass_backfills_pending(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active',
                                    '/tmp/ws', None, None)
    oc.list_permissions.return_value = [
        {'id': 'req1', 'sessionID': 'oc_sess_42', 'permission': 'external_directory'},
        {'id': 'req2', 'sessionID': 'oc_child_x', 'permission': 'bash'},   # 他人会话
    ]
    r = client.patch('/ai/chat/sessions/sess_x/permission-mode',
                     json={'mode': 'bypass'}, headers=dev_h)
    assert r.status_code == 200 and r.get_json() == {'sessionId': 'sess_x', 'mode': 'bypass'}
    calls = [c for c in oc.reply_permission.call_args_list]
    assert len(calls) == 1
    assert calls[0].args[0] == 'req1' and calls[0].args[1] == 'once'
    assert calls[0].kwargs.get('directory') == '/tmp/ws'
    # UPDATE 落库发生
    assert any('UPDATE ai_chat_sessions SET permission_mode' in c.args[0]
               for c in cursor.execute.call_args_list)


def test_toggle_ask_no_backfill(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active',
                                    '/tmp/ws', None, None)
    oc.list_permissions.return_value = [{'id': 'req1', 'sessionID': 'oc_sess_42'}]
    r = client.patch('/ai/chat/sessions/sess_x/permission-mode',
                     json={'mode': 'ask'}, headers=dev_h)
    assert r.status_code == 200
    oc.reply_permission.assert_not_called()


def test_toggle_batch_controlled_409(setup):
    client, cursor, oc, dev_h, _, _ = setup
    # batch_id 非空 + 非终态 status → _execution_controlled True（AI_BATCH_STRICT_OWNERSHIP 默认严格）
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active',
                                    '/tmp/ws', 'batch_x', None)
    r = client.patch('/ai/chat/sessions/sess_x/permission-mode',
                     json={'mode': 'bypass'}, headers=dev_h)
    assert r.status_code == 409 and r.get_json()['code'] == 'BATCH_CONTROLLED'
```

- [ ] **Step 2: 跑失败** → 404（路由不存在）。
- [ ] **Step 3: 实现端点**（`reply_permission` 路由后）：

```python
@ai_chat_bp.route('/sessions/<sid>/permission-mode', methods=['PATCH'])
@write_required
def set_permission_mode(sid):
    """会话级 bypass 开关（bypass spec 2026-10-31 §2.2）。'ask'=照常弹权限卡；
    'bypass'=监听器自动放行权限询问（once）。切到 bypass 时立即放行存量挂起
    （含本会话委派的子代理的询问，归属与 reply_permission._owned 同款）。
    批任务受控会话拒绝（其权限处置走自动拒绝链，不混）。"""
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    if _execution_controlled(sess):
        return jsonify({'error': '批任务受控会话不支持切换 bypass 模式',
                        'code': 'BATCH_CONTROLLED'}), 409
    body = request.get_json(force=True) or {}
    mode = body.get('mode')
    if mode not in ('ask', 'bypass'):
        return jsonify({'error': "mode must be 'ask' or 'bypass'",
                        'code': 'BAD_MODE'}), 400
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE ai_chat_sessions SET permission_mode=%s WHERE id=%s",
                    (mode, sid))
    if mode == 'bypass':
        client = OpenCodeClient(OPENCODE_BASE_URL)
        kids = _child_session_agents(client, sess)
        for q in client.list_permissions(directory=sess[4]):
            if q.get('sessionID') != sess[2] and q.get('sessionID') not in kids:
                continue
            try:
                client.reply_permission(q.get('id'), 'once', directory=sess[4])
                logger.info('permission auto-approved (bypass toggle backfill) '
                            'session=%s request=%s type=%s',
                            sid, q.get('id'), q.get('permission') or 'unknown')
            except Exception as e:
                logger.warning('toggle backfill reply failed session=%s request=%s: %s',
                               sid, q.get('id'), e)
    return jsonify({'sessionId': sid, 'mode': mode})
```

（`_execution_controlled`/`_child_session_agents`/`OpenCodeClient`/`OPENCODE_BASE_URL` 均为该文件既有名字，确认 import 区已有；缺则补。）
- [ ] **Step 4: 跑通过** → 4 例 PASS。
- [ ] **Step 5: Commit**：`git commit -am "feat(ai-chat): PATCH permission-mode 端点（developer+ 门控、批受控 409、bypass 翻转放行存量挂起）"`

### Task 3: 监听器自动应答钩子

**Files:**
- Modify: `server/utils/chat_persist.py`（模块级新函数 `_auto_approve_if_bypass` + `_run_listener` 事件循环内一行调用）
- Test: `server/tests/test_chat_persist_auto_approve.py`（新建）

**Interfaces:**
- Consumes: Task 1 列；`chat_persist` 既有 `event_session_id(props)`（:36）、`get_runtime()`（`get_runtime().get_client().reply_permission(...)`）、`get_db`。
- Produces: `_auto_approve_if_bypass(sid, evt, parent_oc_sid, state, directory) -> None`（模块级、可直测、异常自吞）。

- [ ] **Step 1: 写失败测试**：

```python
"""_auto_approve_if_bypass：bypass 会话权限询问自动放行（spec §2.3）。"""
import sys, os
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils import chat_persist


def _evt(req_id='req1', oc_sid='oc_parent', permission='external_directory'):
    return {'event': 'permission.asked',
            'data': {'properties': {'id': req_id, 'sessionID': oc_sid,
                                    'permission': permission}}}


@pytest.fixture
def env(monkeypatch):
    """伪 get_db（可配置返回 mode）+ 伪 runtime client。可从用例改 db_mode。"""
    state = {'mode': 'bypass', 'reply': MagicMock(), 'db': None}
    fake_client = MagicMock()

    class _FakeCursor:
        def execute(self, *a, **k): pass
        def fetchone(self): return (state['mode'],)
        def __enter__(self): return self
        def __exit__(self, *a): return False

    @pytest.fixture  # noqa: 占位防误读——真实实现在下方 fixture 函数体内
    def _unused(): pass

    import contextlib
    @contextlib.contextmanager
    def fake_get_db():
        yield state['db'] if state['db'] else _FakeCtx(state)

    class _FakeCtx:
        def cursor(self): return _FakeCursor()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _FakeRuntime:
        def get_client(self): return fake_client

    monkeypatch.setattr(chat_persist, 'get_db', fake_get_db)
    monkeypatch.setattr(chat_persist, 'get_runtime', lambda: _FakeRuntime())
    return {'state': state, 'client': fake_client}


def test_bypass_parent_ask_replied_once(env):
    st = env['state']
    chat_persist._auto_approve_if_bypass(
        'sess_x', _evt(), 'oc_parent', {'subtasks': {}}, '/ws')
    env['client'].reply_permission.assert_called_once_with('req1', 'once', directory='/ws')


def test_ask_mode_not_replied(env):
    env['state']['mode'] = 'ask'
    chat_persist._auto_approve_if_bypass(
        'sess_x', _evt(), 'oc_parent', {'subtasks': {}}, '/ws')
    env['client'].reply_permission.assert_not_called()


def test_child_ask_uses_parent_mode(env):
    chat_persist._auto_approve_if_bypass(
        'sess_x', _evt(oc_sid='oc_child1'), 'oc_parent',
        {'subtasks': {'oc_child1': {}}}, '/ws')
    env['client'].reply_permission.assert_called_once()


def test_foreign_ask_ignored(env):
    chat_persist._auto_approve_if_bypass(
        'sess_x', _evt(oc_sid='oc_stranger'), 'oc_parent', {'subtasks': {}}, '/ws')
    env['client'].reply_permission.assert_not_called()


def test_non_permission_event_ignored(env):
    chat_persist._auto_approve_if_bypass(
        'sess_x', {'event': 'message.updated', 'data': {'properties': {}}},
        'oc_parent', {'subtasks': {}}, '/ws')
    env['client'].reply_permission.assert_not_called()


def test_reply_failure_swallowed(env, monkeypatch):
    env['client'].reply_permission.side_effect = RuntimeError('boom')
    # 不抛异常即通过（fail-open：回落手动权限卡）
    chat_persist._auto_approve_if_bypass(
        'sess_x', _evt(), 'oc_parent', {'subtasks': {}}, '/ws')
```

（夹具骨架若与 pytest fixture 语法细节冲突，以「monkeypatch chat_persist.get_db/get_runtime、fake_client=MagicMock、可切换 mode」三点为准落地，断言不变——先跑通再收敛夹具写法。）
- [ ] **Step 2: 跑失败** → AttributeError（函数不存在）。
- [ ] **Step 3: 实现**。`chat_persist.py` 模块级（`event_session_id` 之后）：

```python
def _auto_approve_if_bypass(sid, evt, parent_oc_sid, state, directory):
    """bypass 会话的权限询问自动放行（bypass spec 2026-10-31 §1/§2.3）。

    事件时查库读 permission_mode（询问稀有，一次索引查可忽略；保证中途
    翻转对后续询问即时生效，无缓存失同步）。归属与 reply_permission._owned
    同款：父会话自己的询问 + 本会话委派的子代理（state['subtasks'] 已注册
    的 child oc id）的询问都按父会话模式处理。best-effort：任何异常只落
    日志、绝不阻断持久化主循环——失败最坏结果是回落手动权限卡（fail-open），
    不会卡死回合。
    """
    try:
        if not isinstance(evt, dict) or evt.get('event') != 'permission.asked':
            return
        props = (evt.get('data') or {}).get('properties') or {}
        req_id = props.get('id')
        if not req_id:
            return
        evt_sid = event_session_id(props) or props.get('sessionID') or ''
        if evt_sid != parent_oc_sid and evt_sid not in (state.get('subtasks') or {}):
            return
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT permission_mode FROM ai_chat_sessions WHERE id=%s",
                        (sid,))
            row = cur.fetchone()
        if not row or row[0] != 'bypass':
            return
        get_runtime().get_client().reply_permission(req_id, 'once', directory=directory)
        logger.info('permission auto-approved (bypass) session=%s request=%s type=%s',
                    sid, req_id, props.get('permission') or 'unknown')
    except Exception as e:
        logger.warning('permission auto-approve skipped session=%s: %s', sid, e)
```

`_run_listener` 事件循环内，`_audit_capture(evt)` 行之后加：

```python
        _auto_approve_if_bypass(sid, evt, opencode_session_id, state, directory)
```

- [ ] **Step 4: 跑通过 + 回归**：`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest server/tests/test_chat_persist_auto_approve.py server/tests/test_chat_persist.py -q` → 全绿（persist 套件确认主循环未受扰）。
- [ ] **Step 5: Commit**：`git commit -am "feat(ai-chat): 监听器 bypass 自动放行钩子（事件时查库、子代理归属、fail-open）"`

### Task 4: 前端 API + store

**Files:**
- Modify: `src/api/aiChat.ts`（`AiGroupedSessionSummary` 加字段 + 新函数）
- Modify: `src/stores/aiChat.ts`（SessionMeta、sessions 映射两处、autoApproved 状态、`_handleEvent` permission.asked 分流、setPermissionMode action、sendUserMessage 清 chip）
- Test: `src/stores/__tests__/aiChat.test.ts`

**Interfaces:**
- Consumes: Task 2 端点；`patch` from `@/utils/request`（`api/aiChat.ts:10` 已导入）。
- Produces: `AiGroupedSessionSummary.permissionMode?: 'ask' | 'bypass'`；`setPermissionMode(id, mode)`；store `setPermissionMode(sessionId, mode)`（乐观+回滚+bypass 清卡）、`autoApproved: Record<string, { permission: string; at: number }[]>`、`clearAutoApproved(sessionId)`；SessionMeta.permissionMode。

- [ ] **Step 1: store 测试先行**（mock 工厂加 `setPermissionMode: vi.fn(),`）：

```ts
describe('permissionMode（bypass）', () => {
  it('setPermissionMode 乐观更新、bypass 清卡、失败回滚', async () => {
    const store = useAiChatStore()
    store.sessions = [{ id: 'sess_1', title: 'T', permissionMode: 'ask' }]
    store.pendingPermissions['sess_1'] = [{ id: 'r1' } as never]

    vi.mocked(api.setPermissionMode).mockRejectedValueOnce(new Error('net'))
    await expect(store.setPermissionMode('sess_1', 'bypass')).rejects.toThrow('net')
    expect(store.sessions[0].permissionMode).toBe('ask')     // 回滚

    vi.mocked(api.setPermissionMode).mockResolvedValueOnce({ sessionId: 'sess_1', mode: 'bypass' })
    await store.setPermissionMode('sess_1', 'bypass')
    expect(store.sessions[0].permissionMode).toBe('bypass')
    expect(store.pendingPermissions['sess_1']).toEqual([])   // 翻转清卡
  })

  it('bypass 会话的 permission.asked 进 autoApproved，不进 pendingPermissions', () => {
    const store = useAiChatStore()
    store.sessions = [{ id: 'sess_1', title: 'T', permissionMode: 'bypass' }]
    store._handleEvent('sess_1', 'permission.asked', {
      id: 'r9', sessionID: 'oc1', permission: 'external_directory',
    } as never)
    expect(store.pendingPermissions['sess_1'] ?? []).toEqual([])
    expect(store.autoApproved['sess_1']).toEqual([
      { permission: 'external_directory', at: expect.any(Number) },
    ])
  })

  it('ask 会话的 permission.asked 仍进 pendingPermissions', () => {
    const store = useAiChatStore()
    store.sessions = [{ id: 'sess_1', title: 'T', permissionMode: 'ask' }]
    store._handleEvent('sess_1', 'permission.asked', {
      id: 'r9', sessionID: 'oc1', permission: 'external_directory',
    } as never)
    expect(store.pendingPermissions['sess_1']).toHaveLength(1)
    expect(store.autoApproved['sess_1']).toBeUndefined()
  })
})
```

- [ ] **Step 2: 跑失败**：`npx vitest run src/stores/__tests__/aiChat.test.ts` → FAIL。
- [ ] **Step 3: 实现**。`src/api/aiChat.ts`：

```ts
export interface AiGroupedSessionSummary extends AiSessionSummary {
  // ...既有字段...
  /** 会话级权限模式：ask=弹卡（默认）/ bypass=监听器自动放行 */
  permissionMode?: 'ask' | 'bypass'
}

export function setPermissionMode(id: string, mode: 'ask' | 'bypass') {
  return patch<{ sessionId: string; mode: string }>(
    `/ai/chat/sessions/${encodeURIComponent(id)}/permission-mode`, { mode })
}
```

`src/stores/aiChat.ts`：
1. `SessionMeta` 加 `permissionMode?: 'ask' | 'bypass'`；state 加 `autoApproved: Record<string, { permission: string; at: number }[]>({})`（初始 `autoApproved: {}` 紧邻既有 `pendingPermissions` 初始化）。
2. 会话列表映射（`this.sessions = sessions.map(...)`，~:345）加 `permissionMode: s.permissionMode`；新建会话 unshift（~:399）加 `permissionMode: 'ask' as const`。
3. `_handleEvent` 的 `case 'permission.asked'`：在**现有按 id 去重之前**加分流——

```ts
        const meta = this.sessions.find((s) => s.id === sid)
        if (meta?.permissionMode === 'bypass') {
          const chips = this.autoApproved[sid] ?? (this.autoApproved[sid] = [])
          chips.push({ permission: req.permission, at: Date.now() })
          break
        }
```

（放在 `const req = data as PermissionRequest; if (!req?.id) break` 之后、pending list 操作之前；`break` 直接跳出 switch，不进 pending。）
4. actions 区（挨着 Task-4-上轮的 `setFeedback`）：

```ts
    async setPermissionMode(sessionId: string, mode: 'ask' | 'bypass') {
      const target = this.sessions.find((s) => s.id === sessionId)
      const prev = target?.permissionMode
      if (target) target.permissionMode = mode
      if (mode === 'bypass') this.pendingPermissions[sessionId] = []
      try {
        await setPermissionMode(sessionId, mode)
      } catch (e) {
        if (target) target.permissionMode = prev
        throw e
      }
    },

    clearAutoApproved(sessionId: string) {
      this.autoApproved[sessionId] = []
    },
```

5. `sendUserMessage` 开头（sid 判空之后）加 `this.autoApproved[sid] = []`（新回合开始，旧 chip 消散）。
- [ ] **Step 4: 跑通过** → PASS。
- [ ] **Step 5: Commit**：`git commit -am "feat(ai-chat): bypass 前端 API+store（模式乐观翻转、asked 分流 autoApproved、翻转清卡）"`

### Task 5: 开关钮 + chip 渲染 + E2E

**Files:**
- Modify: `src/views/ai-chat/AiChatView.vue`（状态条行 :2071-2073 区域加开关钮；composer 卡内 attachments 区之后加 chip 行；script 加 computed/handler）
- Test: `e2e/ai-full/ai-chat-bypass.spec.ts`（新建）

**Interfaces:**
- Consumes: Task 4 store（`setPermissionMode`/`autoApproved`/`clearAutoApproved`/SessionMeta.permissionMode）；auth store `isGuest`（`src/stores/auth.ts:50`）；视图既有 `batchControlled`（:864）、`activeId`（:210）。
- Produces: `data-test="bypass-toggle"` 开关钮（guest 不可见、批受控禁用）、`data-test="auto-approved-chip"` chip 行。

- [ ] **Step 1: 视图改动**。script 区：

```ts
const authStore = useAuthStore()          // import { useAuthStore } from '@/stores/auth'；isGuest 取 authStore.isGuest
const isGuest = computed(() => authStore.isGuest)
const permissionMode = computed(() =>
  store.sessions.find((s) => s.id === activeId.value)?.permissionMode ?? 'ask')
const autoApprovedChips = computed(() =>
  activeId.value ? store.autoApproved[activeId.value] ?? [] : [])

async function onToggleBypass() {
  const sid = activeId.value
  if (!sid) return
  const next = permissionMode.value === 'bypass' ? 'ask' : 'bypass'
  try {
    await store.setPermissionMode(sid, next)
    ElMessage.success(next === 'bypass' ? 'Bypass 已开启：权限询问将自动放行' : '已切回确认模式')
  } catch {
    ElMessage.error('权限模式切换失败')
  }
}
```

模板——状态条行（`<!-- 用量状态条（F1）` 注释块旁、`<ContextStatusBar` 同一行的容器内，置于状态条之前）：

```html
            <button
              v-if="activeId && !isGuest"
              class="bypass-toggle" :class="{ 'bypass-toggle--on': permissionMode === 'bypass' }"
              type="button" data-test="bypass-toggle"
              :disabled="batchControlled"
              :title="permissionMode === 'bypass'
                ? 'Bypass 开启：工具权限询问自动放行，点击切回确认模式'
                : 'Bypass 模式：自动放行工具权限询问（developer+，仅本会话生效）'"
              :aria-label="`权限模式 ${permissionMode === 'bypass' ? 'bypass（自动放行）' : 'ask（逐次确认）'}`"
              @click="onToggleBypass"
            ><ElIcon><component :is="permissionMode === 'bypass' ? Unlock : Lock" /></ElIcon>
              {{ permissionMode === 'bypass' ? 'Bypass' : 'Ask' }}</button>
```

（`Lock`/`Unlock` 加入 `@element-plus/icons-vue` 导入；样式 `.bypass-toggle { ... } .bypass-toggle--on { color: var(--el-color-warning); }` 随既有状态行样式落。）
chip 行——composer 卡内 `.composer-attachments` 块之后：

```html
            <div v-if="autoApprovedChips.length" class="auto-approved" data-test="auto-approved-chip">
              <span
                v-for="(c, i) in autoApprovedChips" :key="`${c.at}-${i}`"
                class="auto-approved__chip"
                title="bypass 模式已自动放行该权限询问；点击清除"
                @click="store.clearAutoApproved(activeId!)"
              >⚡ 已自动放行 {{ c.permission }}</span>
            </div>
```

- [ ] **Step 2: vue-tsc**：`npx vue-tsc --noEmit` → 0 错。
- [ ] **Step 3: E2E** `e2e/ai-full/ai-chat-bypass.spec.ts`（照 `ai-permission-ask.spec.ts` 的登录与触发先例；该先例读 `C:\Windows\...\hosts` 必触发 `external_directory=ask`）：

```ts
/**
 * bypass 会话权限自动放行 E2E（spec 2026-10-31 §3，真实链路）：
 * bypass 开 → external_directory 询问自动放行 → 权限卡绝不出现、
 * 「已自动放行」chip 出现、回合正常完成。
 */
import { test, expect } from '@playwright/test'
import { api, tag, waitFor } from './helpers'

test.setTimeout(300_000)

test('bypass：external_directory 询问自动放行，无卡、chip 出现、回合完成',
     async ({ page, request }) => {
  const created = await api(request, 'POST', '/ai/chat/sessions', {})
  expect(created.status).toBeLessThan(300)
  const sid = created.json.id
  await api(request, 'PATCH', `/ai/chat/sessions/${sid}`, { title: tag('bypass') })
  const toggled = await api(request, 'PATCH',
                            `/ai/chat/sessions/${sid}/permission-mode`, { mode: 'bypass' })
  expect(toggled.status).toBeLessThan(300)

  // 真实 UI 登录（照 ai-permission-ask 先例，规避 .auth 过期风险），直开会话
  await page.goto('/')
  await page.fill('input[placeholder*="用户名"]', 'admin')
  await page.fill('input[placeholder*="密码"]', 'admin123')
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await page.waitForTimeout(2000)
  await page.goto(`/ai-chat?session=${sid}`)
  const input = page.getByPlaceholder(/给 AI 助手发消息/)
  await input.waitFor({ state: 'visible', timeout: 60_000 })
  await expect(page.locator('[data-test="bypass-toggle"]')).toContainText('Bypass')

  await input.fill(
    '必须真的调用 read 工具读取文件 C:\\Windows\\System32\\drivers\\etc\\hosts '
    + '的前 3 行，不要凭记忆回答，然后把读到的内容原样贴出来。')
  await page.getByRole('button', { name: '发送' }).click()

  // chip 出现 = 询问已发生且被自动放行；随后权限卡必须恒为 0
  await expect(page.locator('[data-test="auto-approved-chip"]'))
    .toBeVisible({ timeout: 180_000 })
  await expect(page.locator('[data-test="permission-card"]')).toHaveCount(0)

  // 回合正常完成（服务端落库为准）
  await waitFor(async () => {
    const r = await api(request, 'GET', `/ai/chat/sessions/${sid}/messages`)
    return (r.json?.messages || []).some((m: any) => m.role === 'assistant' &&
      JSON.stringify(m.content || '').includes('hosts')) ? true : null
  }, { timeoutMs: 300_000, intervalMs: 4000 })
})
```

- [ ] **Step 4: 跑 E2E**：`npx playwright test e2e/ai-full/ai-chat-bypass.spec.ts` → PASS（后端 :3002 须已重启至含 Task 1-3 代码；执行前按 restartBackend 过程重启）。
- [ ] **Step 5: 回归**：`npx playwright test e2e/ai-full/ai-permission-ask.spec.ts` → PASS（ask 行为未受扰）。
- [ ] **Step 6: Commit**：`git commit -am "feat(ai-chat): bypass 开关钮+自动放行 chip+真实链路 E2E"`

---

## 收尾（全部任务后）

- [ ] 全量回归：`pytest server/tests -q`（串行，~4min）+ `npx vitest run` + 三支 E2E（本计划新增 1 支 + `ai-permission-ask.spec.ts` + `ai-session-core` 冒烟）。
- [ ] spec 状态改「已实施」随最后 commit 上库；push main。

## Self-Review 记录

- spec 覆盖：§2.1→Task1、§2.2→Task2、§2.3→Task3、§2.4→Task4/5、§2.5→Task2/3 日志行、§3→各任务测试+Task5 Step5 回归、§4 风险行→Task3 fail-open 与 Task2 吞错。spec §2.4「list_sessions 与 openSession 消费载荷」——openSession 无独立载荷端点（会话对象经 listSessions，:345 映射即唯一来源），已按实际链路落 Task4。
- 类型一致：`permissionMode: 'ask'|'bypass'` 全链一致；`autoApproved` 元素 `{permission, at}` Task4 定义/Task5 消费一致；`_auto_approve_if_bypass(sid, evt, parent_oc_sid, state, directory)` Task3 定义与循环调用一致。
- 已核实的锚点：`_handleEvent(sid, event, data)`（aiChat.ts:1167）、`SessionMeta`（:41）、sessions 映射（:345/:399）、`_execution_controlled` 元组位（batch_id=sess[5]/orch=sess[6]/status=sess[3]）、`reply_permission(request_id, response, directory=)`、`event_session_id(props)`（chat_persist:36）、`_run_listener` 循环内 `_audit_capture(evt)` 调用位。
- 已修正：Task3 夹具初稿混入了非法嵌套 @pytest.fixture——已改为「三点为准落地」的实现注记（fake_get_db/fake runtime/可切 mode），断言逐字保留。
