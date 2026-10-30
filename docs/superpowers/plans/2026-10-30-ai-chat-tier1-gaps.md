# AI 会话页第一档差距补齐 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 AI 会话页补齐三项第一档差距：A 消息点赞/点踩、B 会话导出 Markdown、C 图片输入（含 spike 闸门）。

**Architecture:** A 用独立小表 + get_messages LEFT JOIN + 消息操作行按钮；B 是后端人读渲染器 + 直链下载 + 会话更多菜单入口；C 是 mime 分流（文本内联 vs 图片 part）+ OpenCode prompt parts 透传 + 输入区三入口。三项相互独立，A→B→C 串行实施，C 以 Task 8 spike 为闸门。

**Tech Stack:** Flask + psycopg2（后端）、Pinia + Vue3 + Element Plus（前端）、pytest / vitest / Playwright（测试）。

**Spec:** `docs/superpowers/specs/2026-10-30-ai-chat-tier1-gaps-design.md`（计划与 spec 一起阅读；每项设计决策以 spec 为准）

## Global Constraints

- 后端 3002 端口、不自动 reload：改 `server/` 代码后手动重启后端进程才生效。
- server 单测跑独立库 `casemanage_test`（`server/tests/conftest.py` 引导）；新测试照 `server/tests/test_ai_chat_messages_paging.py` 的播种/清理模式写，勿连 dev 库。
- 源码改动（含 `server/**/*.py`）必须走 Edit/Write 工具，禁止 Bash 重定向写源码（Mimosa 钩子会拦）。
- 迁移**双注册**：`server/migrations/` 新文件 + `server/app.py` 注册块（`exec_module` 不可省）+ `server/db_schema/core.py` 的 `CREATE TABLE IF NOT EXISTS`（新库）。
- 每个任务独立 commit；TDD——测试先写、先跑失败、再实现、再通过。
- E2E 属 `e2e/ai-full` 套件，用 `helpers.ts` 的 `api/openChatSession/gotoWithAuth/tag/waitFor`；会话用 `tag()` 命名，靠 dev 库定点清理收尾（套件惯例，不留专属清理）。
- 前端 EP 组件测试用真实 Element Plus；store 测试 mock `@/api/aiChat`（`src/stores/__tests__/aiChat.test.ts` 既有 `vi.mock` 工厂，新增 API 函数必须同步补进工厂）。

---

## Feature A：消息点赞/点踩

### Task 1: 反馈表迁移（三处落点）

**Files:**
- Create: `server/migrations/2026_10_30_message_feedback.py`
- Modify: `server/db_schema/core.py`（`ai_chat_messages` 块之后，约 :453）
- Modify: `server/app.py`（迁移注册块末尾）
- Test: `server/tests/test_ai_chat_feedback.py`（新建，Feature A 后端测试都放这里）

**Interfaces:**
- Produces: 表 `ai_chat_message_feedback(message_id PK, session_id, rating SMALLINT IN (1,-1), updated_at)`，后续任务全部依赖它。

- [ ] **Step 1: 写失败测试**（新建 `server/tests/test_ai_chat_feedback.py`，头部与 fixture 照抄 `test_ai_chat_messages_paging.py` 的 `_client/_h/seeded` 模式，seeded 里追加一条 assistant 消息）：

```python
def test_feedback_table_and_cascade(seeded):
    """迁移幂等建表 + ON DELETE CASCADE 随消息删除。"""
    import importlib
    mig = importlib.import_module('migrations.2026_10_30_message_feedback')
    mig.run()  # 幂等，可重跑
    import psycopg2
    from config import DB_CONFIG
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    mid = seeded['ids'][0]
    cur.execute("INSERT INTO ai_chat_message_feedback (message_id, session_id, rating) "
                "VALUES (%s, %s, 1)", (mid, seeded['sid']))
    conn.commit()
    cur.execute("DELETE FROM ai_chat_messages WHERE id = %s", (mid,))
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM ai_chat_message_feedback WHERE message_id = %s", (mid,))
    assert cur.fetchone()[0] == 0
    conn.close()
```

- [ ] **Step 2: 跑测试确认失败**：`pytest server/tests/test_ai_chat_feedback.py::test_feedback_table_and_cascade -v` → FAIL（表不存在）。
- [ ] **Step 3: 写迁移文件** `server/migrations/2026_10_30_message_feedback.py`：

```python
# -*- coding: utf-8 -*-
"""消息点赞/点踩反馈表（2026-10-30 第一档差距补齐 Feature A）。

独立小表而非消息表加列：ai_chat_messages.meta 由服务端持久化占用，热点
表不加列；后续 SkillOpt 消费反馈时本表自带 session_id 可直接 JOIN。
幂等可重跑。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db


def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_chat_message_feedback (
                message_id VARCHAR(100) PRIMARY KEY
                    REFERENCES ai_chat_messages(id) ON DELETE CASCADE,
                session_id VARCHAR(100) NOT NULL REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
                rating     SMALLINT NOT NULL CHECK (rating IN (1, -1)),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )""")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_feedback_sess "
            "ON ai_chat_message_feedback(session_id)")
```

- [ ] **Step 4: core.py 加同款 CREATE TABLE**（`ai_chat_messages` 表与 `ALTER ... ADD meta` 行之后、子代理表注释之前，注释一行 `-- 消息点赞/点踩反馈（2026-10-30 Feature A）`）。
- [ ] **Step 5: app.py 注册**：在最后一个 `_mpNN` 注册块之后追加，序号变量顺延（先 `grep -n "_mp" server/app.py` 取现有最大 N）：

```python
# 消息反馈表（2026-10-30 第一档 Feature A）：点赞/点踩独立小表。随启动幂等执行。
try:
    _mpNN = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'migrations', '2026_10_30_message_feedback.py')
    _specNN = _ilu.spec_from_file_location('_msg_feedback_boot', _mpNN)
    _mNN = _ilu.module_from_spec(_specNN)
    _specNN.loader.exec_module(_mNN)
    _mNN.run()
except Exception as _e:
    logging.warning('message feedback migration on boot failed: %s', _e)
```

- [ ] **Step 6: 跑测试通过**：同 Step 2 命令 → PASS。
- [ ] **Step 7: Commit**：`git add server/migrations/2026_10_30_message_feedback.py server/db_schema/core.py server/app.py server/tests/test_ai_chat_feedback.py && git commit -m "feat(ai-chat): 消息反馈表迁移（Feature A 三处落点）"`

### Task 2: POST feedback 端点

**Files:**
- Modify: `server/routes/ai_chat.py`（`get_messages` 路由之前插入）
- Test: `server/tests/test_ai_chat_feedback.py`

**Interfaces:**
- Consumes: Task 1 的表；`_load_session_for_user(sid, user_id)`（返回七元组或 None）。
- Produces: `POST /ai/chat/sessions/<sid>/messages/<mid>/feedback`，body `{"rating": 1|-1|0}`，0=清除；响应 `{"messageId", "rating"}`。错误码：`BAD_RATING`/`SESSION_NOT_FOUND`/`MESSAGE_NOT_FOUND`/`NOT_ASSISTANT`。

- [ ] **Step 1: 写失败测试**（同文件追加，复用 seeded）：

```python
def test_feedback_set_switch_clear(seeded):
    c, h = _client(), _h()
    mid = seeded['ids'][1]
    r = c.post(f"/ai/chat/sessions/{seeded['sid']}/messages/{mid}/feedback",
               headers=h, json={'rating': 1})
    assert r.status_code == 200 and r.get_json() == {'messageId': mid, 'rating': 1}
    r = c.post(f"/ai/chat/sessions/{seeded['sid']}/messages/{mid}/feedback",
               headers=h, json={'rating': -1})
    assert r.status_code == 200 and r.get_json()['rating'] == -1
    r = c.post(f"/ai/chat/sessions/{seeded['sid']}/messages/{mid}/feedback",
               headers=h, json={'rating': 0})
    assert r.status_code == 200 and r.get_json()['rating'] == 0

def test_feedback_validation(seeded):
    c, h = _client(), _h()
    sid, mid = seeded['sid'], seeded['ids'][2]
    assert c.post(f"/ai/chat/sessions/{sid}/messages/{mid}/feedback",
                  headers=h, json={'rating': 2}).status_code == 400
    assert c.post(f"/ai/chat/sessions/{sid}/messages/{mid}/feedback",
                  headers=h, json={'rating': 'x'}).status_code == 400
    assert c.post(f"/ai/chat/sessions/{sid}/messages/{mid}/feedback",
                  headers=h, json={'rating': 1}).status_code == 200
    # 消息不属于该会话
    assert c.post("/ai/chat/sessions/other-sess/messages/" + mid + "/feedback",
                  headers=h, json={'rating': 1}).status_code == 404
    # 用户消息不可评
    import psycopg2, uuid
    from config import DB_CONFIG
    conn = psycopg2.connect(**DB_CONFIG); cur = conn.cursor()
    umid = f'msg_u_{uuid.uuid4().hex[:8]}'
    cur.execute("INSERT INTO ai_chat_messages (id, session_id, role, content) "
                "VALUES (%s, %s, 'user', '[{\"type\":\"text\",\"text\":\"hi\"}]')", (umid, sid))
    conn.commit()
    r = c.post(f"/ai/chat/sessions/{sid}/messages/{umid}/feedback",
               headers=h, json={'rating': 1})
    assert r.status_code == 400 and r.get_json()['code'] == 'NOT_ASSISTANT'
    cur.execute("DELETE FROM ai_chat_messages WHERE id=%s", (umid,)); conn.commit()
    conn.close()
```

注意：seeded fixture 的消息全部是 assistant 角色（paging 模板如此），user 消息用例里现插。
- [ ] **Step 2: 跑失败**：`pytest server/tests/test_ai_chat_feedback.py -v` → 新增两例 404。
- [ ] **Step 3: 实现端点**（`ai_chat.py`，`get_messages` 之前）：

```python
@ai_chat_bp.route('/sessions/<sid>/messages/<mid>/feedback', methods=['POST'])
@login_required
def set_message_feedback(sid, mid):
    """消息点赞/点踩（2026-10-30 第一档 Feature A）。rating 0=清除。"""
    user = flask_g.current_user
    if not _load_session_for_user(sid, user['userId']):
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    body = request.get_json(force=True)
    try:
        rating = int(body.get('rating'))
    except (TypeError, ValueError):
        return jsonify({'error': 'rating must be 1/-1/0', 'code': 'BAD_RATING'}), 400
    if rating not in (1, -1, 0):
        return jsonify({'error': 'rating must be 1/-1/0', 'code': 'BAD_RATING'}), 400
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT role FROM ai_chat_messages WHERE id=%s AND session_id=%s",
                    (mid, sid))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': 'message not found', 'code': 'MESSAGE_NOT_FOUND'}), 404
        if row[0] != 'assistant':
            return jsonify({'error': 'only assistant messages can be rated',
                            'code': 'NOT_ASSISTANT'}), 400
        if rating == 0:
            cur.execute("DELETE FROM ai_chat_message_feedback WHERE message_id=%s", (mid,))
        else:
            cur.execute(
                "INSERT INTO ai_chat_message_feedback (message_id, session_id, rating, updated_at) "
                "VALUES (%s,%s,%s,NOW()) "
                "ON CONFLICT (message_id) DO UPDATE SET rating=EXCLUDED.rating, updated_at=NOW()",
                (mid, sid, rating))
    return jsonify({'messageId': mid, 'rating': rating})
```

- [ ] **Step 4: 跑通过** → PASS。
- [ ] **Step 5: Commit**：`git commit -am "feat(ai-chat): POST feedback 端点（1/-1/0 三态，assistant-only）"`

### Task 3: get_messages 带出 rating

**Files:**
- Modify: `server/routes/ai_chat.py:1241-1278`（get_messages 三条 SELECT + 响应映射）
- Test: `server/tests/test_ai_chat_feedback.py`

**Interfaces:**
- Produces: GET messages 响应的 assistant 消息对象新增 `"rating": 1|-1|null`（无反馈 null）。前端 Task 4/5 依赖此字段。

- [ ] **Step 1: 写失败测试**：

```python
def test_messages_response_carries_rating(seeded):
    c, h = _client(), _h()
    sid = seeded['sid']
    c.post(f"/ai/chat/sessions/{sid}/messages/{seeded['ids'][3]}/feedback",
           headers=h, json={'rating': -1})
    r = c.get(f"/ai/chat/sessions/{sid}/messages", headers=h)
    by_id = {m['id']: m for m in r.get_json()['messages']}
    assert by_id[seeded['ids'][3]]['rating'] == -1
    assert by_id[seeded['ids'][4]]['rating'] is None
```

- [ ] **Step 2: 跑失败** → FAIL（KeyError 'rating'）。
- [ ] **Step 3: 实现**：三条 SELECT 的列改为

```sql
SELECT m.id, m.role, m.content, m.created_at, m.meta, m.seq, f.rating
FROM ai_chat_messages m
LEFT JOIN ai_chat_message_feedback f ON f.message_id = m.id
WHERE ...（原 since/before/全量三支谓词不变，表名换 m. 别名）
```

响应映射加 `'rating': r[6]`。
- [ ] **Step 4: 跑通过 + 回归**：`pytest server/tests/test_ai_chat_feedback.py server/tests/test_ai_chat_messages_paging.py -v` → 全 PASS（分页测试确认 JOIN 未破坏窗口语义）。
- [ ] **Step 5: Commit**：`git commit -am "feat(ai-chat): get_messages LEFT JOIN 反馈表带出 rating"`

### Task 4: 前端 API + store

**Files:**
- Modify: `src/api/aiChat.ts`（`AiMessage` 接口 :62 附近 + 新函数）
- Modify: `src/stores/aiChat.ts`
- Test: `src/stores/__tests__/aiChat.test.ts`

**Interfaces:**
- Consumes: Task 2/3 的端点与响应字段。
- Produces: `api.setMessageFeedback(sessionId, messageId, rating)`；`AiMessage.rating?: number | null`；`store.setFeedback(sessionId, messageId, rating: 1|-1|0)`（乐观更新、失败回滚并 rethrow）。

- [ ] **Step 1: store 测试先行**。`src/stores/__tests__/aiChat.test.ts` 的 `vi.mock` 工厂加 `setMessageFeedback: vi.fn(),`，然后：

```ts
it('setFeedback 乐观更新 + 失败回滚', async () => {
  const store = useAiChatStore()
  store.messages['sess_1'] = [
    { id: 'm1', role: 'assistant', content: [{ type: 'text', text: 'hi' }] },
  ]
  vi.mocked(api.setMessageFeedback).mockRejectedValueOnce(new Error('net'))

  await expect(store.setFeedback('sess_1', 'm1', 1)).rejects.toThrow('net')
  expect(store.messages['sess_1'][0].rating).toBeNull()   // 回滚

  vi.mocked(api.setMessageFeedback).mockResolvedValueOnce({ messageId: 'm1', rating: 1 })
  await store.setFeedback('sess_1', 'm1', 1)
  expect(store.messages['sess_1'][0].rating).toBe(1)

  await store.setFeedback('sess_1', 'm1', 0)
  expect(store.messages['sess_1'][0].rating).toBeNull()
})
```

- [ ] **Step 2: 跑失败**：`npx vitest run src/stores/__tests__/aiChat.test.ts` → FAIL（setFeedback 不存在）。
- [ ] **Step 3: 实现**。`src/api/aiChat.ts`：

```ts
export interface AiMessage {
  // ...既有字段不动...
  /** 点赞/点踩：1=赞 -1=踩 null=未评（服务端 LEFT JOIN 反馈表带出） */
  rating?: number | null
}

export function setMessageFeedback(sessionId: string, messageId: string,
                                   rating: 1 | -1 | 0) {
  return post<{ messageId: string; rating: number }>(
    `/ai/chat/sessions/${encodeURIComponent(sessionId)}/messages/`
    + `${encodeURIComponent(messageId)}/feedback`,
    { rating },
  )
}
```

`src/stores/aiChat.ts`（放在 reloadMessages 附近的 action 区）：

```ts
async setFeedback(sessionId: string, messageId: string, rating: 1 | -1 | 0) {
  const target = this.messages[sessionId]?.find(m => m.id === messageId)
  if (!target) return
  const prev = target.rating ?? null
  target.rating = rating === 0 ? null : rating
  try {
    await setMessageFeedback(sessionId, messageId, rating)
  } catch (e) {
    target.rating = prev
    throw e
  }
}
```

（`setMessageFeedback` 加入该文件顶部既有 `from '@/api/aiChat'` 导入清单。）
- [ ] **Step 4: 跑通过** → PASS。
- [ ] **Step 5: Commit**：`git commit -am "feat(ai-chat): 反馈前端 API 与 store action（乐观更新/回滚）"`

### Task 5: 操作行按钮 + E2E

**Files:**
- Modify: `src/views/ai-chat/AiChatView.vue`（消息操作行 :1763-1783 + script 处理函数）
- Test: `e2e/ai-full/ai-chat-feedback.spec.ts`（新建）

**Interfaces:**
- Consumes: `store.setFeedback`（Task 4）、`m.rating`（Task 3）。

- [ ] **Step 1: 改操作行**。把 assistant 分支的裸按钮改为 `<template v-else-if="m.role === 'assistant'">` 包三个按钮（重新生成 + 赞 + 踩；赞/踩用内联 SVG，Element Plus 无 thumb 图标）：

```html
<template v-else-if="m.role === 'assistant'">
  <button class="msg__action-btn" type="button" title="重新生成" aria-label="重新生成"
    :disabled="streaming" @click="retryAssistantMessage(m)"
  ><ElIcon><RefreshRight /></ElIcon></button>
  <button class="msg__action-btn" :class="{ 'feedback-on': m.rating === 1 }"
    type="button" data-test="feedback-up" title="赞" aria-label="赞"
    @click="onFeedback(m, 1)"
  ><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 10v12"/><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"/></svg></button>
  <button class="msg__action-btn" :class="{ 'feedback-on': m.rating === -1 }"
    type="button" data-test="feedback-down" title="踩" aria-label="踩"
    @click="onFeedback(m, -1)"
  ><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 14V2"/><path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z"/></svg></button>
</template>
```

script 区（挨着 `copyMessage`/`retryAssistantMessage`）：

```ts
async function onFeedback(m: AiMessage, next: 1 | -1) {
  const sid = store.activeSessionId
  if (!sid || streaming.value) return
  const rating = m.rating === next ? 0 : next
  try {
    await store.setFeedback(sid, m.id, rating)
  } catch {
    ElMessage.error('反馈保存失败')
  }
}
```

样式：`.feedback-on { color: var(--el-color-primary); }` 加到既有 `.msg__action-btn` 样式旁。
- [ ] **Step 2: 手动验证按钮渲染**（重启前端 dev server 后打开一个有 AI 回复的会话，悬浮消息见 👍/👎；此处无独立组件测试——按钮在 2900 行大视图内，E2E 才是既定验收口径）。
- [ ] **Step 3: 写 E2E** `e2e/ai-full/ai-chat-feedback.spec.ts`（照 ai-session-core.spec.ts 套路）：

```ts
/**
 * 消息点赞/点踩 E2E（2026-10-30 第一档 Feature A）：真实链路——
 * API 建会话 + 真实 OpenCode 回复落库 → UI 点赞 → 刷新保留 → 切踩 → 清除。
 */
import { test, expect } from '@playwright/test'
import { api, openChatSession, tag, waitFor } from './helpers'

test.setTimeout(420_000)

test('消息反馈：点赞持久化 + 切换 + 清除', async ({ page, request }) => {
  const created = await api(request, 'POST', '/ai/chat/sessions', {})
  const sid = created.json.id
  await api(request, 'PATCH', `/ai/chat/sessions/${sid}`, { title: tag('feedback') })
  await api(request, 'POST', `/ai/chat/sessions/${sid}/messages`, {
    content: '请直接回复四个字符：GOOD，不要输出任何其他内容。',
  })
  await waitFor(async () => {
    const r = await api(request, 'GET', `/ai/chat/sessions/${sid}/messages`)
    return (r.json?.messages || []).some((m: any) => m.role === 'assistant') ? true : null
  }, { timeoutMs: 300_000, intervalMs: 4000 })

  await openChatSession(page, sid)
  const up = page.locator('[data-test="feedback-up"]').last()
  await expect(up).toBeVisible({ timeout: 30_000 })
  await up.click()
  // API 侧确认落库
  await waitFor(async () => {
    const r = await api(request, 'GET', `/ai/chat/sessions/${sid}/messages`)
    const last = (r.json?.messages || []).filter((m: any) => m.role === 'assistant').pop()
    return last?.rating === 1 ? true : null
  }, { timeoutMs: 15_000, intervalMs: 1000 })
  // 刷新保留
  await page.reload()
  await expect(page.locator('[data-test="feedback-up"]').last(),
    '刷新后仍高亮').toHaveClass(/feedback-on/, { timeout: 30_000 })
  // 切踩 → 清除
  await page.locator('[data-test="feedback-down"]').last().click()
  await expect(page.locator('[data-test="feedback-down"]').last(),
    '切踩后踩高亮').toHaveClass(/feedback-on/)
  await expect(page.locator('[data-test="feedback-up"]').last(),
    '切踩后赞取消').not.toHaveClass(/feedback-on/)
  await page.locator('[data-test="feedback-down"]').last().click()
  await expect(page.locator('[data-test="feedback-down"]').last(),
    '再点取消高亮').not.toHaveClass(/feedback-on/)
})
```

- [ ] **Step 4: 跑 E2E**：`npx playwright test e2e/ai-full/ai-chat-feedback.spec.ts` → PASS。
- [ ] **Step 5: Commit**：`git commit -am "feat(ai-chat): 消息操作行赞/踩按钮 + 真实链路 E2E"`

---

## Feature B：会话导出 Markdown

### Task 6: 导出端点（渲染器 + 路由）

**Files:**
- Modify: `server/routes/ai_chat.py`（渲染函数放 `_recover_session_and_resend` 附近，路由放 `files/download` 附近）
- Test: `server/tests/test_ai_chat_export.py`（新建）

**Interfaces:**
- Produces: `GET /ai/chat/sessions/<sid>/export.md` → `text/markdown` 附件下载，`Content-Disposition` 带 RFC5987 UTF-8 文件名。渲染函数 `_render_export_markdown(title, rows)` 纯函数可直测。

- [ ] **Step 1: 写失败测试**（fixture 模式同 Task 1，播种含五类 parts 的消息）：

```python
# -*- coding: utf-8 -*-
"""GET /ai/chat/sessions/<sid>/export.md 会话导出（Feature B）测试。"""
import sys, os, uuid, json
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from routes.ai_chat import _render_export_markdown, _export_filename


def test_render_markdown_all_part_types():
    rows = [
        ('user', [{'type': 'text', 'text': '看下 @data/a.csv'},
                  {'type': 'file', 'name': 'a.csv', 'path': 'uploads/a.csv'}]),
        ('assistant', [{'type': 'text', 'text': '好的，见下表'},
                       {'type': 'tool_use', 'name': 'read_csv', 'input': {'path': 'a.csv'}},
                       {'type': 'image', 'name': 'chart.png', 'path': 'uploads/chart.png'}]),
    ]
    md = _render_export_markdown('测试会话', rows)
    assert md.startswith('# 测试会话')
    assert '## 🧑 用户' in md and '## 🤖 助手' in md
    assert '看下 @data/a.csv' in md
    assert '<details><summary>🔧 read_csv</summary>' in md
    assert '- 📎 a.csv' in md and '- 🖼️ chart.png' in md


def test_export_filename_safety():
    fname = _export_filename('a/b:c*d?"<>|')
    assert '\\' not in fname and ':' not in fname
    assert _export_filename('   ').startswith('session-')
    long = _export_filename('很' * 100)
    assert len(long.split('-')[0]) <= 60        # safe 段截 60（时间戳含一个 '-'）


def test_export_route_auth_and_content(seeded):
    c, h = _client(), _h()
    r = c.get(f"/ai/chat/sessions/{seeded['sid']}/export.md", headers=h)
    assert r.status_code == 200
    assert 'attachment' in r.headers.get('Content-Disposition', '')
    body = r.get_data(as_text=True)
    assert body.startswith('# ')
    # 越权
    assert c.get("/ai/chat/sessions/other-sess/export.md", headers=h).status_code == 404


def test_export_empty_session_header_only():
    """空会话：只输出 header 块，200 不报错（spec B.1）。"""
    md = _render_export_markdown('空会话', [])
    assert md.startswith('# 空会话')
    assert '消息数：0' in md
```

（`seeded` fixture 与 paging 测试同款；`from routes.ai_chat import ...` 若包结构不同按 conftest 既有 sys.path 方式调整——paging 测试是 `from app import app`，此处直接 import 被测纯函数。）
- [ ] **Step 2: 跑失败**：`pytest server/tests/test_ai_chat_export.py -v` → FAIL（ImportError：函数不存在）。
- [ ] **Step 3: 实现**（`ai_chat.py`）：

```python
def _export_filename(title: str) -> str:
    """文件名安全化：替换非法字符/控制符，压缩空白，截 60 字符，加时间戳。"""
    safe = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', (title or '').strip())
    safe = re.sub(r'\s+', '_', safe).strip('._')[:60] or 'session'
    return f"{safe}-{datetime.now().strftime('%Y%m%d-%H%M')}.md"


def _render_export_parts(parts) -> str:
    lines = []
    for p in parts or []:
        if not isinstance(p, dict):
            continue
        t = p.get('type')
        if t == 'text' and p.get('text'):
            lines.append(str(p['text']))
        elif t == 'reasoning' and p.get('text'):
            lines.append(f"<details><summary>🤔 思考过程</summary>\n\n{p['text']}\n\n</details>")
        elif t == 'tool_use':
            try:
                args = json.dumps(p.get('input'), ensure_ascii=False)[:2000]
            except (TypeError, ValueError):
                args = ''
            lines.append(f"<details><summary>🔧 {p.get('name') or 'tool'}</summary>\n\n"
                         f"```json\n{args}\n```\n\n</details>")
        elif t == 'file':
            lines.append(f"- 📎 {p.get('name')}")
        elif t == 'image':
            lines.append(f"- 🖼️ {p.get('name')}")
    return '\n\n'.join(lines)


def _render_export_markdown(title: str, rows) -> str:
    """rows: [(role, content_parts)] 按 seq 时序。人读归档（区别于 LLM 恢复的
    render_history_block：那个有损、不含工具细节）。"""
    head = (f"# {title}\n\n"
            f"> 导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M')} ｜ 消息数：{len(rows)}\n")
    body = []
    for role, parts in rows:
        who = '🧑 用户' if role == 'user' else '🤖 助手'
        body.append(f"## {who}\n\n{_render_export_parts(parts)}")
    return head + '\n' + '\n\n'.join(body)


@ai_chat_bp.route('/sessions/<sid>/export.md', methods=['GET'])
@login_required
def export_session_markdown(sid):
    """会话归档导出（2026-10-30 第一档 Feature B）。全量不截断。"""
    from urllib.parse import quote
    user = flask_g.current_user
    if not _load_session_for_user(sid, user['userId']):
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT title FROM ai_chat_sessions WHERE id=%s", (sid,))
        title = cur.fetchone()[0] or '会话'
        cur.execute("SELECT role, content FROM ai_chat_messages "
                    "WHERE session_id=%s ORDER BY seq", (sid,))
        rows = [(r[0], r[1] if isinstance(r[1], list) else []) for r in cur.fetchall()]
    md = _render_export_markdown(title, rows)
    resp = flask.Response(md, mimetype='text/markdown')
    resp.headers['Content-Disposition'] = (
        'attachment; filename="export.md"; filename*=UTF-8\'\''
        + quote(_export_filename(title)))
    return resp
```

（`flask.Response`/`re`/`datetime`/`json` 若未导入则在该文件 import 区补；`datetime.now()` 用本地时区即可——归档时间戳，无跨口径断言。）
- [ ] **Step 4: 跑通过** → PASS。
- [ ] **Step 5: Commit**：`git commit -am "feat(ai-chat): 会话导出 Markdown 端点（人读渲染器 + RFC5987 文件名）"`

### Task 7: 导出入口 + E2E

**Files:**
- Modify: `src/components/ai-chat/SessionRowActions.vue`（emits :25、onCommand :51、菜单项）
- Modify: `src/views/ai-chat/AiChatView.vue`（三处 `<SessionRowActions` :1509/:1526/:1558 加 `@export` + 处理函数）
- Test: `e2e/ai-full/ai-chat-export.spec.ts`（新建）

**Interfaces:**
- Consumes: Task 6 端点；`authParam()` from `@/utils/request`。
- Produces: SessionRowActions 新 emit `export`。

- [ ] **Step 1: SessionRowActions**。emits 类型加一行 `export: () => true`；`onCommand` 的映射链加 `if (key === 'export') return emit('export')`；菜单项插在「清空会话」项之后、「移动到分组」小节之前：

```html
<ElDropdownItem command="export" divided>
  <ElIcon class="more-menu__icon"><Download /></ElIcon>导出 Markdown
</ElDropdownItem>
```

（`Download` 图标加入该文件 `@element-plus/icons-vue` 导入。）
- [ ] **Step 2: AiChatView 接线**。三处 `<SessionRowActions` 均加 `@export="exportSessionMarkdown(s)"`；script 区（挨着 `renameSession` 等函数）：

```ts
function exportSessionMarkdown(s: { id: string; title?: string }) {
  const a = document.createElement('a')
  a.href = `/api/ai/chat/sessions/${s.id}/export.md${authParam('?')}`
  a.download = ''
  document.body.appendChild(a)
  a.click()
  a.remove()
}
```

（顶部 `import { authParam } from '@/utils/request'`，若无则加。）
- [ ] **Step 3: E2E** `e2e/ai-full/ai-chat-export.spec.ts`：

```ts
/**
 * 会话导出 Markdown E2E（2026-10-30 第一档 Feature B）：真实链路——
 * 建会话 + 真实回复 → 更多菜单导出 → 下载文件内容断言。
 */
import { test, expect } from '@playwright/test'
import { api, openChatSession, tag, waitFor } from './helpers'

test.setTimeout(420_000)

test('会话导出：更多菜单 → 下载 md 含标题与正文', async ({ page, request }) => {
  const created = await api(request, 'POST', '/ai/chat/sessions', {})
  const sid = created.json.id
  const title = tag('export-md')
  await api(request, 'PATCH', `/ai/chat/sessions/${sid}`, { title })
  await api(request, 'POST', `/ai/chat/sessions/${sid}/messages`, {
    content: '请直接回复四个字符：EXPT，不要输出任何其他内容。',
  })
  await waitFor(async () => {
    const r = await api(request, 'GET', `/ai/chat/sessions/${sid}/messages`)
    return (r.json?.messages || []).some((m: any) => m.role === 'assistant') ? true : null
  }, { timeoutMs: 300_000, intervalMs: 4000 })

  await openChatSession(page, sid)
  await page.locator('[data-test="session-more-btn"]').first().click()
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByText('导出 Markdown').click(),
  ])
  const path = await download.path()
  const fs = await import('node:fs')
  const md = fs.readFileSync(path!, 'utf-8')
  expect(md).toContain(`# ${title}`)
  expect(md).toContain('EXP')
  expect(md).toContain('## 🧑 用户')
})
```

（注意：`download.path()` 在 Windows 需等待下载完成，Playwright 自动等待；`EXP` 匹配 `EXP T` 产出内容——真实模型回复四个字符 `EXP T` 中 `EXP` 子串稳定出现，避免全等断言脆裂。若模型回复超时按套件既有 `waitFor` 口径排查。）
- [ ] **Step 4: 跑 E2E**：`npx playwright test e2e/ai-full/ai-chat-export.spec.ts` → PASS。
- [ ] **Step 5: Commit**：`git commit -am "feat(ai-chat): 会话更多菜单导出 Markdown + E2E 下载断言"`

---

## Feature C：图片输入

### Task 8: Phase 0 spike（闸门——失败则 Task 9/10 取消并回填 spec）

**Files:**
- Create: `e2e/manual/image-parts-spike/spike.py`
- Create: `e2e/manual/image-parts-spike/README.md`
- Evidence: `docs/ai-testing/evidence/image-parts-spike-<日期>.json`

**Interfaces:**
- Produces: `IMAGE_PART_MODE` 结论（`dataurl` 或 `filepath`），Task 9 依赖；spec C.1 节回填结论。

- [ ] **Step 1: 生成 1×1 红色 PNG**（探测图）：`python -c "import base64,sys; open('e2e/manual/image-parts-spike/probe.png','wb').write(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='))"`
- [ ] **Step 2: 写 spike 脚本** `e2e/manual/image-parts-spike/spike.py`：

```python
# -*- coding: utf-8 -*-
"""OpenCode 图片 part 透传 spike（2026-10-30 第一档 Feature C 闸门）。

对 pinned oc serve 实测两种图片 part 形态，判定模型是否真的"看到"图：
  dataurl  —— {"type":"file","mime":"image/png","url":"data:image/png;base64,..."}
  filepath —— {"type":"file","mime":"image/png","url":"file://<workspace 内绝对路径>"}
用法：
  python spike.py --base-url http://127.0.0.1:4096 --token <oc-serve.log 里插件 token 行> \
                  --model <providerID/modelID 必须是视觉模型> --workspace <会话工作区绝对路径> \
                  --image probe.png --mode dataurl
退出码 0=该形态可用；非 0=不可用。结果 JSON 落 docs/ai-testing/evidence/。
"""
import argparse, base64, json, os, sys, time
import requests


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base-url', required=True)
    ap.add_argument('--token', required=True)
    ap.add_argument('--model', required=True)   # providerID/modelID
    ap.add_argument('--workspace', required=True)
    ap.add_argument('--image', required=True)
    ap.add_argument('--mode', choices=['dataurl', 'filepath'], required=True)
    args = ap.parse_args()

    sess = requests.post(f"{args.base_url}/session",
                         params={'directory': args.workspace},
                         headers={'Authorization': f'Bearer {args.token}'},
                         json={'title': 'image-parts-spike'}).json()
    sid = sess['id']
    if args.mode == 'dataurl':
        with open(args.image, 'rb') as f:
            url = 'data:image/png;base64,' + base64.b64encode(f.read()).decode()
    else:
        url = 'file://' + os.path.abspath(args.image)
    provider, model = args.model.split('/', 1)
    requests.post(f"{args.base_url}/session/{sid}/prompt_async",
                  params={'directory': args.workspace},
                  headers={'Authorization': f'Bearer {args.token}'},
                  json={'model': {'providerID': provider, 'modelID': model},
                        'parts': [
                            {'type': 'text',
                             'text': '用不超过五个字描述这张图片的主要内容。'},
                            {'type': 'file', 'mime': 'image/png', 'url': url},
                        ]})
    texts, deadline = [], time.time() + 120
    while time.time() < deadline:
        time.sleep(3)
        msgs = requests.get(f"{args.base_url}/session/{sid}/message",
                            params={'directory': args.workspace},
                            headers={'Authorization': f'Bearer {args.token}'}).json()
        for m in msgs if isinstance(msgs, list) else msgs.get('data', []):
            info = m.get('info') or m
            if info.get('role') == 'assistant':
                for p in info.get('parts') or []:
                    if p.get('type') == 'text' and p.get('text'):
                        texts.append(p['text'])
        if texts:
            break
    ok = any('红' in t or 'red' in t.lower() for t in texts)
    out = {'mode': args.mode, 'model': args.model, 'ok': ok,
           'assistant_texts': texts[:3], 'session': sid}
    ev = os.path.join('docs', 'ai-testing', 'evidence',
                      f"image-parts-spike-{time.strftime('%Y%m%d-%H%M%S')}-{args.mode}.json")
    os.makedirs(os.path.dirname(ev), exist_ok=True)
    with open(ev, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(0 if ok else 2)


if __name__ == '__main__':
    main()
```

- [ ] **Step 3: 取运行参数**：token 从 `oc-serve.log` 的插件 token 行取（serve 启动即打印）；模型清单 `curl -H "Authorization: Bearer <token>" <base-url>/provider`，选平台已接入的视觉模型（无视觉模型→Feature C 直接砍，理由登记）。
- [ ] **Step 4: 跑两种形态**：各执行一次 `python e2e/manual/image-parts-spike/spike.py ... --mode dataurl` 与 `--mode filepath`（filepath 的 `--workspace` 须包含 probe.png 复制目标——先把 probe.png 拷进工作区）。
- [ ] **Step 5: 判定与回填**：退出码 0 的形态即 `IMAGE_PART_MODE`；把结论（含 evidence 文件名）回填 spec C.1 节。**两种都失败 → 回填"砍掉"，Task 9/10 整体取消，本 commit 只含 spike 资产与 spec 回填**。
- [ ] **Step 6: Commit**：`git commit -am "test(spike): OpenCode 图片 part 透传探测（dataurl/filepath 两形态）+ 结论回填 spec"`
- [ ] **Step 7: GATE**：spec C.1 结论为"砍掉"→ 停在本任务；否则继续 Task 9。

### Task 9: 后端 mime 分流 + image_parts 透传

**Files:**
- Modify: `server/routes/ai_chat.py`（send_message 附件循环 :1018-1026、dispatch :1132、`_recover_session_and_resend` :1206）
- Modify: `server/utils/opencode_client.py:84-113`
- Test: `server/tests/test_routes_ai_chat.py`（**追加用例**，复用既有 `setup` fixture——`MagicMock` oc client + mock DB，见该文件 :22 的 fixture 与 :115 `test_send_message_persists_user_and_calls_opencode` 先例）

**Interfaces:**
- Consumes: Task 8 的 `IMAGE_PART_MODE`。
- Produces: `send_prompt_async(..., image_parts=None)`，元素 `{name, mime, url}`；stored_parts 新 part `{'type':'image','name','path'}`（其落库形状由 Task 10 E2E 在真库断言，单测不重复）；常量 `IMAGE_MIMES = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp', 'image/gif': '.gif'}`、`IMAGE_MAX_BYTES = 5 * 1024 * 1024`。

- [ ] **Step 1: 写失败测试**（追加到 `test_routes_ai_chat.py`）：

```python
def test_image_attachment_parts(setup, tmp_path):
    """图片：不内联、进 image_parts（URL 形态按 IMAGE_PART_MODE）；文本照旧内联。"""
    from routes.ai_chat import IMAGE_PART_MODE
    client, cursor, oc, dev_h, _, _ = setup
    ws = tmp_path / 'ws'
    (ws / 'uploads').mkdir(parents=True)
    (ws / 'uploads' / 'x.png').write_bytes(b'\x89PNG\r\n\x1a\n' + b'0' * 32)
    (ws / 'uploads' / 'a.txt').write_text('hello', encoding='utf-8')
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active',
                                    str(ws), None, None)
    resp = client.post(
        '/ai/chat/sessions/sess_x/messages',
        json={'content': '看图', 'attachments': ['uploads/x.png', 'uploads/a.txt']},
        headers=dev_h,
    )
    assert resp.status_code == 202
    args, kwargs = oc.send_prompt_async.call_args
    assert 'hello' in args[1]                       # 文本仍内联
    ip = kwargs['image_parts']
    assert ip == [{'name': 'x.png', 'mime': 'image/png', 'url': ip[0]['url']}]
    if IMAGE_PART_MODE == 'dataurl':
        assert ip[0]['url'].startswith('data:image/png;base64,')
    else:
        assert ip[0]['url'].startswith('file://')
    # 非图片二进制走既有 file 分支，不进 image_parts
    resp = client.post(
        '/ai/chat/sessions/sess_x/messages',
        json={'content': '看文件', 'attachments': ['uploads/x.exe']},
        headers=dev_h,
    )
    assert resp.status_code == 202
    _, kwargs2 = oc.send_prompt_async.call_args
    assert kwargs2.get('image_parts') in (None, [])


def test_image_missing_file_no_500(setup, tmp_path):
    """图片文件缺失/不可读：降级 file:// 形态，不 500（dataurl 读失败兜底）。"""
    client, cursor, oc, dev_h, _, _ = setup
    ws = tmp_path / 'ws2'
    (ws / 'uploads').mkdir(parents=True)            # 不造 x.png
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active',
                                    str(ws), None, None)
    resp = client.post(
        '/ai/chat/sessions/sess_x/messages',
        json={'content': '看图', 'attachments': ['uploads/x.png']},
        headers=dev_h,
    )
    assert resp.status_code == 202
    _, kwargs = oc.send_prompt_async.call_args
    assert kwargs['image_parts'][0]['url'].startswith('file://')
```
- [ ] **Step 2: 跑失败** → FAIL（无 image part）。
- [ ] **Step 3: 实现 send_message**（`ai_chat.py`）：

```python
IMAGE_MIMES = {'image/png': '.png', 'image/jpeg': '.jpg',
               'image/webp': '.webp', 'image/gif': '.gif'}
IMAGE_MAX_BYTES = 5 * 1024 * 1024
# Task 8 spike 结论；砍掉场景本代码不存在
IMAGE_PART_MODE = 'dataurl'   # 或 'filepath'


def _image_mime(rel: str):
    ext = os.path.splitext(rel)[1].lower()
    for mime, e in IMAGE_MIMES.items():
        if e == ext:
            return mime
    return None


def _build_image_parts(workspace_path, images):
    """images: [{name, path, mime}] → OpenCode file part 入参。URL 形态按 spike；
    dataurl 读文件失败（缺失/权限）降级 file:// 形态，不让发消息 500。"""
    out = []
    for img in images:
        abs_path = _safe_workspace_path(workspace_path, img['path'])
        url = None
        if IMAGE_PART_MODE == 'dataurl' and abs_path and os.path.isfile(abs_path):
            try:
                with open(abs_path, 'rb') as f:
                    b64 = base64.b64encode(f.read()).decode()
                url = f"data:{img['mime']};base64,{b64}"
            except OSError:
                url = None
        if url is None:
            url = 'file://' + abs_path
        out.append({'name': img['name'], 'mime': img['mime'], 'url': url})
    return out
```

附件循环改为：

```python
    image_atts = []          # (name, rel, mime) 供 dispatch 透传
    for rel in attachments:
        name = os.path.basename(rel)
        mime = _image_mime(rel)
        if mime:
            stored_parts.append({'type': 'image', 'name': name, 'path': rel})
            image_atts.append({'name': name, 'path': rel, 'mime': mime})
            continue
        stored_parts.append({'type': 'file', 'name': name, 'path': rel})
        inlined = _read_text_attachment(workspace_path, rel)
        if inlined is not None:
            prompt += f"\n\n[用户上传的文件 {name}]\n```\n{inlined}\n```"
        else:
            abs_path = _safe_workspace_path(workspace_path, rel)
            prompt += f"\n\n[用户上传的文件 {name}，路径：{abs_path}（如需要可用工具读取）]"
```

图片大小闸门（循环后）：

```python
    for img in image_atts:
        ap_ = _safe_workspace_path(workspace_path, img['path'])
        if ap_ and os.path.getsize(ap_) > IMAGE_MAX_BYTES:
            return jsonify({'error': 'image too large (max 5MB)',
                            'code': 'IMAGE_TOO_LARGE'}), 400
```

dispatch 调用（:1132）加 `image_parts=_build_image_parts(workspace_path, image_atts) if image_atts else None`；`_recover_session_and_resend` 签名加 `image_parts=None` 形参并透传给 `client.send_prompt_async`（恢复重发不丢图），主路径 `except` 分支的恢复调用同步传参。
- [ ] **Step 4: 实现 client**（`opencode_client.py`）：`send_prompt_async` 签名加 `image_parts=None`，parts 拼装在 text part 之后：

```python
        parts = [{"type": "text", "text": content}]
        for img in (image_parts or []):
            parts.append({"type": "file", "mime": img.get("mime") or "image/png",
                          "url": img.get("url")})
        for m in (agent_parts or []):
            ...
```

（agent part 仍在最后，语义不变；缺省 None 时行为与现在逐字节一致。）
- [ ] **Step 5: 跑通过 + 回归**：`pytest server/tests/test_routes_ai_chat.py -v` → 全 PASS（同文件既有 send_message 用例确认文本内联/model/directory 契约不受扰）。
- [ ] **Step 6: Commit**：`git commit -am "feat(ai-chat): 图片附件 mime 分流 + OpenCode image parts 透传"`

### Task 10: 前端三入口 + 图片气泡 + E2E

**Files:**
- Modify: `src/api/aiChat.ts`（`AiContentPart` 联合类型）
- Modify: `src/views/ai-chat/AiChatView.vue`（ElInput `@paste`、composer-card `@drop/@dragover`、attach-chip 图标分流、user 消息 image part 渲染）
- Test: `e2e/ai-full/ai-chat-image-input.spec.ts`（新建）

**Interfaces:**
- Consumes: `uploadFile`（既有）、Task 9 后端 part 类型。
- Produces: `AiContentPart` 增加 `| { type: 'image'; name: string; path: string }`；视图函数 `uploadImageFiles(files: File[])`（paste/drop 共用）。

- [ ] **Step 1: 类型**。`AiContentPart` 联合加 `| { type: 'image'; name: string; path: string }`。
- [ ] **Step 2: 视图逻辑**。script 区：

```ts
const IMAGE_EXT_RE = /\.(png|jpe?g|webp|gif)$/i

function isImagePath(p: string) { return IMAGE_EXT_RE.test(p) }

async function uploadImageFiles(files: (File | null)[]) {
  for (const f of files) {
    if (!f || !f.type.startsWith('image/')) continue
    await store.uploadAttachment(f)   // store aiChat.ts:889——内部自取 activeSessionId，
  }                                   // 落 attachments + loadFiles，与 onFilesPicked 同通路
}

function onPaste(e: ClipboardEvent) {
  const files = Array.from(e.clipboardData?.items || [])
    .filter(i => i.kind === 'file').map(i => i.getAsFile())
  if (files.some(Boolean)) { e.preventDefault(); void uploadImageFiles(files) }
}

function onDrop(e: DragEvent) {
  const files = Array.from(e.dataTransfer?.files || [])
  if (files.some(f => f.type.startsWith('image/'))) {
    e.preventDefault()
    void uploadImageFiles(files)
  }
}
```

（`store.uploadAttachment` 以 `onFilesPicked` 现用方法为准对齐——先看该方法体再落名，不新造第二条上传通路。）
- [ ] **Step 3: 模板接线**。ElInput 加 `@paste="onPaste"`（EP 2.13 attrs 落到内部 textarea，直接可绑）；`composer-card` 加 `@dragover.prevent` 与 `@drop="onDrop"`；attach-chip 图标分流：

```html
<ElIcon v-if="isImagePath(a.path)"><Picture /></ElIcon>
<ElIcon v-else><Document /></ElIcon>
```

（`Picture` 加入 icons 导入。）
- [ ] **Step 4: user 气泡渲染**。user 分支 parts 循环（:1730 `v-else` UserMentionText 之前）插：

```html
<template v-else-if="p.type === 'image'">
  <ElImage
    class="msg-image" :src="downloadFileUrl(activeId!, p.path)"
    :alt="p.name" fit="cover"
    :preview-src-list="[downloadFileUrl(activeId!, p.path)]" hide-on-click-modal
  />
</template>
```

（`downloadFileUrl` 已在 api 层 :558，import 后直接用；样式 `.msg-image { max-width: 240px; max-height: 180px; border-radius: 8px; }`。）
- [ ] **Step 5: E2E** `e2e/ai-full/ai-chat-image-input.spec.ts`：

```ts
/**
 * 图片输入 E2E（2026-10-30 第一档 Feature C）：粘贴 + 选择器两入口真实链路。
 * 断言：芯片图标分流、发送后 user 气泡含 <img>、stored parts 含 image 类型。
 */
import { test, expect } from '@playwright/test'
import { api, openChatSession, tag, waitFor } from './helpers'
import fs from 'node:fs'
import path from 'node:path'

const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64')

test.setTimeout(420_000)

test('图片输入：粘贴入口 → 芯片 → 发送 → 气泡图片', async ({ page, request }) => {
  const created = await api(request, 'POST', '/ai/chat/sessions', {})
  const sid = created.json.id
  await openChatSession(page, sid)
  await page.waitForSelector('.composer-input textarea', { timeout: 30_000 })

  // ① 粘贴：向 textarea 派发带剪贴板文件的 paste 事件
  await page.locator('.composer-input textarea').evaluate((el, buf) => {
    const dt = new DataTransfer()
    dt.items.add(new File([new Uint8Array(buf)], 'probe.png', { type: 'image/png' }))
    el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true }))
  }, Array.from(PNG))
  await expect(page.locator('.attach-chip', { hasText: 'probe.png' })).toBeVisible()

  // ② 发送（真实模型栈；不依赖模型"看懂"图，只断言链路）
  await page.locator('.composer-input textarea').fill('看看这张图')
  await page.keyboard.press('Enter')

  // ③ user 气泡出现图片；服务端 parts 含 image 类型
  await expect(page.locator('.msg-image').first()).toBeVisible({ timeout: 60_000 })
  await waitFor(async () => {
    const r = await api(request, 'GET', `/ai/chat/sessions/${sid}/messages`)
    const user = (r.json?.messages || []).filter((m: any) => m.role === 'user').pop()
    return user?.content?.some((p: any) => p.type === 'image') ? true : null
  }, { timeoutMs: 15_000, intervalMs: 1000 })
})
```

（粘贴事件若 Chromium 对 `ClipboardEvent` 构造限制导致派发无效——实测该构造在 Chromium 可用——回退方案是同一 spec 里把入口 ① 换成 `setInputFiles` 打既有 `fileInputEl`：`page.setInputFiles('input[type=file]', { name: 'probe.png', mimeType: 'image/png', buffer: PNG })`，断言不变。）
- [ ] **Step 6: 跑 E2E**：`npx playwright test e2e/ai-full/ai-chat-image-input.spec.ts` → PASS。
- [ ] **Step 7: Commit**：`git commit -am "feat(ai-chat): 图片输入三入口 + 芯片图标分流 + user 气泡图片渲染 + E2E"`

---

## 收尾（全部任务后）

- [ ] 全量回归：`pytest server/tests -x -q`（casemanage_test，串行基线口径）+ `npx vitest run` + `npx playwright test e2e/ai-full/`（至少跑本计划新增三支 + ai-session-core 冒烟）。
- [ ] spec 状态改「已实施」，随最后一个 commit 上库；push main。

## Self-Review 记录

- spec 覆盖：A.1→Task1、A.2→Task2、A.3→Task3/4/5、A.5→Task2/3/4/5 测试；B.1→Task6、B.2→Task7、B.3→Task6/7；C.1→Task8、C.2→Task9、C.3→Task10、C.4→Task9/10。A.4（SkillOpt 不接线）与风险表无对应实现任务——设计上是"不做"，符合。
- 类型一致：`setFeedback(sid, mid, rating)` Task4 定义/Task5 消费一致；`image_parts` 元素 `{name, mime, url}` Task9 定义、client 消费一致；`rating` 字段 Task3 产出、Task4 `AiMessage` 消费一致。
- 已修正：①Task9 测试原稿用不存在的 stub 文件——已改为追加进 `test_routes_ai_chat.py` 复用其 `setup` fixture（:22，MagicMock client + mock DB，send 返回 **202**），并补缺失文件降级用例；②Task10 store 方法名查实为 `uploadAttachment(file)`（aiChat.ts:889，内部自取 activeSessionId）；③Task6 文件名断言改为按 '-' 分段、补空会话 header-only 用例（spec B.1）；④`_build_image_parts` 读文件失败降级 file://，不发 500。
