# 会话大纲（用户输入精准定位）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AI 对话页右侧新增「会话大纲」面板：列出会话全部用户输入（不受 200 条分页窗口限制），点击任意条目按需加载历史并精准滚动定位 + 脉冲高亮；滚动时大纲跟随高亮当前回合；跨会话搜索内容命中时直接定位到命中消息。

**Architecture:** 后端加一个轻量 `/turns` 投影端点（role='user' 消息的 id/seq/createdAt/preview，用户消息稀疏、不分页）；`search_sessions` 的 content 命中顺带返回消息锚点。前端 store 持有 `turnsBySession` 并提供 `ensureLoadedUntil`（以 `oldestLoadedSeq` 为游标、1000/页循环前插，消息流始终连续）；视图层用稳定的 `data-msg-id` DOM 锚点 + id 版脉冲高亮定位，scroll-spy 在既有 `onChatScroll` 上节流 200ms 计算视口顶部所属回合。

**Tech Stack:** Flask + psycopg2（后端既有蓝图 `ai_chat_bp`）、Vue 3 `<script setup>` + Pinia、vitest、pytest（真实库播种）、Playwright（e2e/ai-full 真实链路）。

**Spec:** `docs/superpowers/specs/2026-10-25-session-outline-design.md`（计划从 spec 出发；执行者同时读两份）

## Global Constraints

- TDD 纪律（用户硬性要求）：每个任务**先写测试、跑红、再实现、跑绿**；新测试必须在实现提交之前的 commit 上失败过。
- 源码改动一律用 Edit/Write 工具完成（Mimosa 钩子规则：shell 重定向写文件、open-w 变量路径会被拦）。
- 后端单测命令形态：`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/<file> -v`；播种/清理模式对齐 `server/tests/test_ai_chat_messages_paging.py`（真实库 + psycopg2 + fixture 清理）。
- 前端单测命令形态：`npx vitest run <file>`；类型检查 `npx vue-tsc --noEmit`（若 package.json 已有 type-check 脚本则用脚本）。
- E2E：`npm run test:e2e -- ai-session-outline`；`/ai-chat` 有常驻 SSE，**禁止 networkidle 等待**；种子/清理只用 `e2e/ai-full/db-helpers.ts`（AITEST 前缀纪律，插值只允许自造值）。
- 不动既有 `/messages` 分页契约、不动 `locateTodoStep`（下标锚点）与 `.msg--located` 既有样式。
- 提交信息：`feat(ai-chat): 中文描述` / `test(e2e): ...`，每任务一个 commit。

## File Structure

| 文件 | 职责 |
|------|------|
| `server/routes/ai_chat.py`（改） | `/turns` 端点 + `_turn_preview` 提炼；`search_sessions` 换 LATERAL 子查询附消息锚点 |
| `server/tests/test_ai_chat_turns.py`（新） | /turns 端点真实库测试 |
| `server/tests/test_ai_chat_search.py`（改） | 锚点字段测试 + 行 shape 适配 |
| `src/api/aiChat.ts`（改） | `SessionTurn` 类型 + `getTurns()`；`AiSessionSearchHit` 补 `messageId/seq` |
| `src/stores/aiChat.ts`（改） | `turnsBySession`/`turnsError` 状态、`loadTurns`、`_prependOlderWindow`、`ensureLoadedUntil`；挂点 selectSession/_reloadPersisted |
| `src/stores/__tests__/aiChat.outline.test.ts`（新） | store 大纲逻辑测试 |
| `src/stores/__tests__/aiChat*.test.ts`（9 个改） | vi.mock factory 补 `getTurns` |
| `src/components/ai-chat/outlineUtils.ts`（新） | `nearestUserTurnSeq` / `previewOfParts` 纯函数 |
| `src/components/ai-chat/SessionOutlinePanel.vue`（新） | 大纲面板纯展示组件 |
| `src/components/ai-chat/__tests__/SessionOutlinePanel.test.ts` + `outlineUtils.test.ts`（新） | 组件与纯函数测试 |
| `src/views/ai-chat/AiChatView.vue`（改） | 右栏布局、`data-msg-id`、`locateMessageById`、scroll-spy、折叠持久化、`selectSearchHit` 跳转 |
| `src/utils/storage.ts`（改） | `STORAGE_KEYS.AI_CHAT_OUTLINE_COLLAPSED` |
| `e2e/ai-full/ai-session-outline.spec.ts`（新） | 四条真实链路用例 |

---

### Task 1: 后端 `/turns` 回合投影端点

**Files:**
- Modify: `server/routes/ai_chat.py`（`get_messages`（:1095）之后新增路由；`_load_session_for_user`（:757）复用）
- Test: `server/tests/test_ai_chat_turns.py`（新）

**Interfaces:**
- Produces: `GET /ai/chat/sessions/:sid/turns` → `{"turns":[{"id":str,"seq":int,"createdAt":iso|null,"preview":str}]}`，按 seq 正序；`_turn_preview(content, limit=160) -> str|None`。

- [ ] **Step 1: 写失败测试**

新建 `server/tests/test_ai_chat_turns.py`：

```python
"""GET /ai/chat/sessions/<sid>/turns 会话大纲端点测试（spec 2026-10-25-session-outline §4.1）。

用户回合投影：id/seq/createdAt/preview；空壳用户消息（无文本无文件）跳过；
assistant 消息不进大纲。真实库播种，模式对齐 test_ai_chat_messages_paging.py。
"""
import sys
import os
import json
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from auth import create_token


def _client():
    from app import app
    app.config['TESTING'] = True
    return app.test_client()


def _h():
    return {'Authorization': 'Bearer ' + create_token(
        {'id': 'user-owner', 'username': 'turns_owner', 'role': 'developer'})}


@pytest.fixture
def seeded():
    import psycopg2
    from config import DB_CONFIG
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    sid = f'sess_turns_{uuid.uuid4().hex[:12]}'
    cur.execute(
        "INSERT INTO users (id, username, password_hash, display_name, role) "
        "VALUES ('user-owner', 'turns_owner', 'x', 'T', 'developer') "
        "ON CONFLICT (id) DO NOTHING")
    cur.execute(
        "INSERT INTO ai_chat_sessions (id, user_id, title, status) "
        "VALUES (%s, 'user-owner', 'turns-test', 'active')", (sid,))
    rows = [
        ('msg_t1', 'user', [{"type": "text", "text": "第一问"}], 1),
        ('msg_t2', 'assistant', [{"type": "text", "text": "回答"}], 2),
        ('msg_t3', 'user', [
            {"type": "file", "name": "a.xlsx", "path": "uploads/a.xlsx"},
            {"type": "file", "name": "b.csv", "path": "uploads/b.csv"}], 3),
        ('msg_t4', 'user', [], 4),                                # 空壳：跳过
        ('msg_t5', 'user', [{"type": "text", "text": "   "}], 5),  # 空白文本：跳过
        ('msg_t6', 'user', [{"type": "text", "text": "长" * 200}], 6),
    ]
    for mid, role, content, seq in rows:
        cur.execute(
            "INSERT INTO ai_chat_messages (id, session_id, role, content, seq) "
            "VALUES (%s, %s, %s, %s, %s)",
            (mid, sid, role, json.dumps(content, ensure_ascii=False), seq))
    conn.commit()
    yield {'sid': sid}
    cur.execute("DELETE FROM ai_chat_messages WHERE session_id = %s", (sid,))
    cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
    conn.commit()
    conn.close()


def test_turns_projection_shape_and_order(seeded):
    r = _client().get(f"/ai/chat/sessions/{seeded['sid']}/turns", headers=_h())
    assert r.status_code == 200
    turns = r.get_json()['turns']
    # 只含用户回合：t1/t3/t6（t4 空壳、t5 空白文本被跳过，assistant t2 排除）
    assert [t['id'] for t in turns] == ['msg_t1', 'msg_t3', 'msg_t6']
    assert [t['seq'] for t in turns] == [1, 3, 6]
    assert turns[0]['preview'] == '第一问'
    assert turns[0]['createdAt']  # 有值即可（iso 字符串）


def test_turns_file_only_preview(seeded):
    r = _client().get(f"/ai/chat/sessions/{seeded['sid']}/turns", headers=_h())
    turns = r.get_json()['turns']
    assert turns[1]['preview'] == '📎 a.xlsx、b.csv'


def test_turns_preview_truncated_to_160(seeded):
    r = _client().get(f"/ai/chat/sessions/{seeded['sid']}/turns", headers=_h())
    turns = r.get_json()['turns']
    assert len(turns[2]['preview']) == 160


def test_turns_unknown_session_404():
    r = _client().get("/ai/chat/sessions/sess_nope/turns", headers=_h())
    assert r.status_code == 404


def test_turns_requires_auth():
    r = _client().get("/ai/chat/sessions/whatever/turns")
    assert r.status_code in (401, 403)
```

- [ ] **Step 2: 跑红**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_ai_chat_turns.py -v`
Expected: FAIL（404，路由不存在）

- [ ] **Step 3: 实现**

在 `server/routes/ai_chat.py` 的 `get_messages`（:1095-1154）函数之后追加：

```python
@ai_chat_bp.route('/sessions/<sid>/turns', methods=['GET'])
@login_required
def get_turns(sid):
    """会话大纲（spec 2026-10-25-session-outline §4.1）：用户输入回合投影。

    用户消息在会话内稀疏，不分页一次全量（兜底 LIMIT 500）；preview 为
    单行摘要（text 优先，纯文件给文件名列表），空壳用户消息跳过。
    """
    user = flask_g.current_user
    if not _load_session_for_user(sid, user['userId']):
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, seq, created_at, content FROM ai_chat_messages "
            "WHERE session_id = %s AND role = 'user' ORDER BY seq LIMIT 500",
            (sid,),
        )
        rows = cur.fetchall()
    turns = []
    for r in rows:
        preview = _turn_preview(r[3])
        if preview is None:
            continue
        turns.append({'id': r[0], 'seq': r[1],
                      'createdAt': r[2].isoformat() if r[2] else None,
                      'preview': preview})
    return jsonify({'turns': turns})


def _turn_preview(content, limit=160):
    """从用户消息 content parts 提炼单行预览：text 优先，纯文件给文件名摘要。

    返回 None 表示无任何可展示内容（调用方跳过该消息）。
    """
    parts = content if isinstance(content, list) else []
    for p in parts:
        if isinstance(p, dict) and p.get('type') == 'text':
            text = ' '.join(str(p.get('text') or '').split()).strip()
            if text:
                return text[:limit]
    names = [str(p.get('name') or '') for p in parts
             if isinstance(p, dict) and p.get('type') == 'file' and p.get('name')]
    if names:
        shown = '、'.join(names[:3])
        if len(names) > 3:
            shown += f' 等 {len(names)} 个'
        return ('📎 ' + shown)[:limit]
    return None
```

- [ ] **Step 4: 跑绿**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_ai_chat_turns.py -v`
Expected: 5 PASS

- [ ] **Step 5: 回归相邻套件**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_ai_chat_messages_paging.py -v`
Expected: PASS（未动 /messages）

- [ ] **Step 6: Commit**

```bash
git add server/routes/ai_chat.py server/tests/test_ai_chat_turns.py
git commit -m "feat(ai-chat): /turns 会话大纲端点（用户回合投影）"
```

---

### Task 2: `search_sessions` content 命中附消息锚点

**Files:**
- Modify: `server/routes/ai_chat.py:598-745`（两条查询换 LEFT JOIN LATERAL；hit 组装加两字段）
- Test: `server/tests/test_ai_chat_search.py`（改：`_row`/`_batch_row` 行 shape + 新用例）

**Interfaces:**
- Produces: search hit 对象新增 `messageId: str|null`、`seq: int|null`——**仅 `matchField='content'` 携带**；title/file/preview 命中为 null（匹配目标是会话行字段，无消息锚点）。
- 实现约束：不能用 `SELECT (m.content, m.id, m.seq)` 匿名 record——psycopg2 对 record 类型返回原始字符串。必须用 `LEFT JOIN LATERAL` 得到独立类型列。

- [ ] **Step 1: 写失败测试**

在 `server/tests/test_ai_chat_search.py` 末尾追加（该文件用 mock DB + `_row`/`_batch_row` 行工厂）：

```python
# ---------------------------------------------------------------------------
# 消息锚点（spec 2026-10-25-session-outline §4.2）：仅 content 命中携带
# ---------------------------------------------------------------------------

def test_content_hit_carries_message_anchor():
    row = _row(sid='s1', title_match=False,
               hit_content='[{"type":"text","text":"部门汇总结果"}]',
               hit_msg_id='msg_hit1', hit_seq=17)
    fake, _ = _db([row])
    with patch('routes.ai_chat.get_db', fake):
        r = _client().get('/ai/chat/sessions/search?q=汇总', headers=_h())
    hit = r.get_json()['sessions'][0]
    assert hit['matchField'] == 'content'
    assert hit['messageId'] == 'msg_hit1'
    assert hit['seq'] == 17


def test_title_hit_has_no_message_anchor():
    row = _row(sid='s1', title_match=True)
    fake, _ = _db([row])
    with patch('routes.ai_chat.get_db', fake):
        r = _client().get('/ai/chat/sessions/search?q=标题', headers=_h())
    hit = r.get_json()['sessions'][0]
    assert hit['matchField'] == 'title'
    assert hit['messageId'] is None
    assert hit['seq'] is None


def test_batch_content_hit_carries_message_anchor():
    row = _batch_row(title_match=False, file_match=False, preview_match=False,
                     hit_content='[{"type":"text","text":"批内命中"}]',
                     hit_msg_id='msg_b1', hit_seq=9)
    fake, _ = _db([row])
    with patch('routes.ai_chat.get_db', fake):
        r = _client().get('/ai/chat/sessions/search?q=命中&batchId=batch-123',
                          headers=_h())
    hit = r.get_json()['sessions'][0]
    assert hit['matchField'] == 'content'
    assert hit['messageId'] == 'msg_b1'
    assert hit['seq'] == 9
```

同时把两个行工厂加两个默认参数（shape 对齐新 SQL 的 10/15 列）：

```python
def _row(sid='s1', title='会话标题', status='active', title_match=False,
         hit_content=None, ts=None, batch_id=None, batch_input_file=None,
         hit_msg_id=None, hit_seq=None):
    return (sid, title, ts, status, batch_id, batch_input_file, title_match,
            hit_content, hit_msg_id, hit_seq)


def _batch_row(sid='b1', title=None, status='completed', batch_seq=1,
               batch_name='季度报告分析', input_file='uploads/report-001.pdf',
               preview=None, title_match=False, file_match=False,
               preview_match=False, hit_content=None, ts=None,
               hit_msg_id=None, hit_seq=None):
    return (sid, title, ts, status, 'batch-123', input_file, batch_seq,
            batch_name, preview, title_match, file_match, preview_match,
            hit_content, hit_msg_id, hit_seq)
```

- [ ] **Step 2: 跑红**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_ai_chat_search.py -v`
Expected: 新 3 例 FAIL（hit 里没有 messageId/seq）；既有用例里若有整 dict 相等断言也会 FAIL（缺新键）——一并记录。

- [ ] **Step 3: 实现**

`search_sessions`（`ai_chat.py:598`）内，把两条查询里的命中子查询从标量子查询换成 `LEFT JOIN LATERAL`。

批任务分支（原 :633-669 的 SQL）改为：

```python
cur.execute(
    "SELECT s.id, s.title, s.last_active_at, s.status, "
    "       s.batch_id, s.batch_input_file, s.batch_seq, b.name AS batch_name, "
    "       s.last_message_preview, "
    "       COALESCE(s.title ILIKE %(pat)s ESCAPE '\\', false) AS title_match, "
    "       COALESCE(s.batch_input_file ILIKE %(pat)s ESCAPE '\\', false) AS file_match, "
    "       COALESCE(s.last_message_preview ILIKE %(pat)s ESCAPE '\\', false) AS preview_match, "
    "       cm.content AS hit_content, cm.id AS hit_msg_id, cm.seq AS hit_seq "
    "FROM ai_chat_sessions s "
    "JOIN ai_chat_batches b ON b.id = s.batch_id AND b.user_id = %(uid)s "
    "LEFT JOIN LATERAL ( "
    "  SELECT m.content, m.id, m.seq FROM ai_chat_messages m "
    "  WHERE m.session_id = s.id "
    "    AND m.search_text ILIKE %(pat)s ESCAPE '\\' "
    "  ORDER BY m.created_at DESC, m.seq DESC LIMIT 1 "
    ") cm ON true "
    "WHERE s.user_id = %(uid)s AND COALESCE(s.kind, 'chat') <> 'trace_analysis' "
    "  AND s.batch_id = %(bid)s "
    "  AND s.status IN %(statuses)s "
    "  AND ( "
    "    COALESCE(s.title ILIKE %(pat)s ESCAPE '\\', false) "
    "    OR COALESCE(s.batch_input_file ILIKE %(pat)s ESCAPE '\\', false) "
    "    OR COALESCE(s.last_message_preview ILIKE %(pat)s ESCAPE '\\', false) "
    "    OR EXISTS ( "
    "      SELECT 1 FROM ai_chat_messages m2 "
    "      WHERE m2.session_id = s.id "
    "        AND m2.search_text ILIKE %(pat)s ESCAPE '\\' "
    "    ) "
    "  ) "
    "ORDER BY title_match DESC, file_match DESC, preview_match DESC, "
    "         s.last_active_at DESC NULLS LAST, s.batch_seq ASC NULLS LAST "
    "LIMIT %(limit)s",
    {'pat': pat, 'uid': user['userId'], 'bid': batch_id,
     'statuses': tuple(BATCH_CHILD_SEARCH_STATUSES),
     'limit': _SEARCH_LIMIT},
)
```

普通分支（原 :672-694 的 SQL）改为：

```python
cur.execute(
    "SELECT s.id, s.title, s.last_active_at, s.status, "
    "       s.batch_id, s.batch_input_file, "
    "       COALESCE(s.title ILIKE %s ESCAPE '\\', false) AS title_match, "
    "       cm.content AS hit_content, cm.id AS hit_msg_id, cm.seq AS hit_seq "
    "FROM ai_chat_sessions s "
    "LEFT JOIN LATERAL ( "
    "  SELECT m.content, m.id, m.seq FROM ai_chat_messages m "
    "  WHERE m.session_id = s.id "
    "    AND m.search_text ILIKE %s ESCAPE '\\' "
    "  ORDER BY m.created_at DESC, m.seq DESC LIMIT 1 "
    ") cm ON true "
    "WHERE s.user_id = %s "
    "  AND s.status IN ('active', 'closed') "
    "  AND ( "
    "    COALESCE(s.title ILIKE %s ESCAPE '\\', false) "
    "    OR EXISTS ( "
    "      SELECT 1 FROM ai_chat_messages m2 "
    "      WHERE m2.session_id = s.id "
    "        AND m2.search_text ILIKE %s ESCAPE '\\' "
    "    ) "
    "  ) "
    "ORDER BY title_match DESC, s.last_active_at DESC NULLS LAST "
    "LIMIT %s",
    (pat, pat, user['userId'], pat, pat, _SEARCH_LIMIT),
)
```

（普通分支参数顺序不变：title_match pat、lateral pat、uid、WHERE title pat、EXISTS pat、limit。）

hit 组装（原 :699-733）两处解包与 dict 改为：

```python
        if batch_id:
            (sid, title, ts, status, _bid, input_file, batch_seq, batch_name,
             preview, title_match, file_match, preview_match,
             hit_content, hit_msg_id, hit_seq) = r
            if title_match:
                match_field, snippet = 'title', ''
            elif file_match:
                match_field, snippet = 'file', ''
            elif preview_match:
                match_field, snippet = 'preview', _single_line(preview or '')
            else:
                match_field, snippet = 'content', _content_snippet(hit_content, q)
            sessions.append({
                'id': sid,
                'title': _session_title(title, batch_id, input_file),
                'lastActiveAt': ts.isoformat() if ts else None,
                'status': status,
                'matchField': match_field,
                'snippet': snippet,
                'batchId': batch_id,
                'batchName': batch_name,
                'batchSeq': batch_seq,
                'inputFileName': (input_file or '').rsplit('/', 1)[-1] or None,
                'isBatchChild': True,
                # 消息锚点只有 content 命中有（title/file/preview 匹配的是会话行字段）
                'messageId': hit_msg_id if match_field == 'content' else None,
                'seq': hit_seq if match_field == 'content' else None,
            })
        else:
            (sid, title, ts, status, _bid, input_file, title_match,
             hit_content, hit_msg_id, hit_seq) = r
            sessions.append({
                'id': sid,
                'title': _session_title(title, _bid, input_file),
                'lastActiveAt': ts.isoformat() if ts else None,
                'status': status,
                'matchField': 'title' if title_match else 'content',
                'snippet': '' if title_match else _content_snippet(hit_content, q),
                'messageId': None if title_match else hit_msg_id,
                'seq': None if title_match else hit_seq,
            })
```

- [ ] **Step 4: 跑绿 + 修既有断言**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_ai_chat_search.py -v`

新 3 例 PASS；若既有用例因整 dict 相等断言缺新键而 FAIL，给这些断言的期望 dict 补 `'messageId': None, 'seq': None`（用 `grep -n "matchField" server/tests/test_ai_chat_search.py` 找到所有期望 dict 逐个核对）。

- [ ] **Step 5: Commit**

```bash
git add server/routes/ai_chat.py server/tests/test_ai_chat_search.py
git commit -m "feat(ai-chat): 搜索命中附消息锚点 messageId/seq（仅 content 命中）"
```

---

### Task 3: 前端 API + store（turns 状态 / loadTurns / ensureLoadedUntil）

**Files:**
- Modify: `src/api/aiChat.ts`（`AiSessionSearchHit`（:20 附近）补字段；`getMessages`（:221）后加 `SessionTurn`/`getTurns`）
- Modify: `src/stores/aiChat.ts`（state（:232 附近）、`selectSession`（:395）、`loadOlderMessages`（:459）、`_reloadPersisted`（:1323））
- Test: `src/stores/__tests__/aiChat.outline.test.ts`（新）
- Test（适配）: `src/stores/__tests__/` 下 9 个既有文件的 `vi.mock('@/api/aiChat')` factory

**Interfaces:**
- Consumes: `GET /turns`（Task 1）、既有 `getMessages(id, {before, limit})`。
- Produces:
  - `interface SessionTurn { id: string; seq?: number; createdAt?: string; preview: string; local?: boolean }`（`src/api/aiChat.ts` 导出）
  - `getTurns(id: string)` → `Promise<{ turns: SessionTurn[] }>`
  - store 状态 `turnsBySession: Record<string, SessionTurn[]>`、`turnsError: Record<string, boolean>`
  - store action `loadTurns(id: string): Promise<void>`
  - store action `ensureLoadedUntil(id: string, targetSeq: number): Promise<boolean>`（targetSeq 对应消息是否已在内存窗口）
  - `AiSessionSearchHit.messageId?: string | null; seq?: number | null`

- [ ] **Step 1: 写失败测试**

新建 `src/stores/__tests__/aiChat.outline.test.ts`：

```typescript
/**
 * 会话大纲 store 侧（spec 2026-10-25-session-outline §5）：
 * loadTurns 状态落位与失败标记；ensureLoadedUntil 跨窗口加载语义。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/aiChat', () => ({
  createSession: vi.fn(),
  listSessions: vi.fn(),
  renameSession: vi.fn(),
  deleteSession: vi.fn(),
  clearSession: vi.fn(),
  getMessages: vi.fn(),
  getTurns: vi.fn(),
  sendMessage: vi.fn(),
  uploadFile: vi.fn(),
  listFiles: vi.fn(() => Promise.resolve({ files: [] })),
  getChanges: vi.fn(),
  createEventStream: vi.fn(() => ({ close: vi.fn() })),
}))

import { useAiChatStore } from '../aiChat'
import * as api from '@/api/aiChat'
import type { AiMessage } from '@/api/aiChat'

const msg = (id: string, seq: number, role: 'user' | 'assistant' = 'assistant'): AiMessage =>
  ({ id, role, content: [{ type: 'text', text: id }], seq })

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('loadTurns', () => {
  it('stores server turns and clears error flag', async () => {
    vi.mocked(api.getTurns).mockResolvedValue({
      turns: [{ id: 'm1', seq: 3, createdAt: '2026-10-25T09:00:00', preview: '第一问' }],
    })
    const store = useAiChatStore()
    store.turnsError['s1'] = true
    await store.loadTurns('s1')
    expect(store.turnsBySession['s1']).toHaveLength(1)
    expect(store.turnsBySession['s1'][0].preview).toBe('第一问')
    expect(store.turnsError['s1']).toBe(false)
  })

  it('marks error and keeps stale turns on failure', async () => {
    vi.mocked(api.getTurns).mockRejectedValue(new Error('boom'))
    const store = useAiChatStore()
    store.turnsBySession['s1'] = [{ id: 'm1', seq: 3, preview: '旧' }]
    await store.loadTurns('s1')
    expect(store.turnsError['s1']).toBe(true)
    expect(store.turnsBySession['s1'][0].preview).toBe('旧')
  })
})

describe('ensureLoadedUntil', () => {
  it('returns true without fetching when target already in window', async () => {
    const store = useAiChatStore()
    store.messages['s1'] = [msg('a', 1), msg('b', 2)]
    store.oldestLoadedSeq['s1'] = 1
    store.hasMoreMessages['s1'] = true
    await expect(store.ensureLoadedUntil('s1', 2)).resolves.toBe(true)
    expect(api.getMessages).not.toHaveBeenCalled()
  })

  it('pages backwards with 1000-limit windows, prepends, and flips hasMore', async () => {
    const store = useAiChatStore()
    store.messages['s1'] = [msg('m9', 9)]
    store.oldestLoadedSeq['s1'] = 9
    store.hasMoreMessages['s1'] = true
    vi.mocked(api.getMessages).mockResolvedValueOnce({ messages: [msg('m1', 1)], hasMore: false })
    await expect(store.ensureLoadedUntil('s1', 1)).resolves.toBe(true)
    expect(api.getMessages).toHaveBeenCalledWith('s1', { before: '9', limit: 1000 })
    expect(store.messages['s1'].map(m => m.id)).toEqual(['m1', 'm9'])
    expect(store.hasMoreMessages['s1']).toBe(false)
  })

  it('returns false when history exhausted without the target', async () => {
    const store = useAiChatStore()
    store.messages['s1'] = [msg('m9', 9)]
    store.oldestLoadedSeq['s1'] = 9
    store.hasMoreMessages['s1'] = true
    vi.mocked(api.getMessages).mockResolvedValueOnce({ messages: [msg('m5', 5)], hasMore: false })
    await expect(store.ensureLoadedUntil('s1', 1)).resolves.toBe(false)
  })

  it('stops after one page when the cursor does not advance (防死循环)', async () => {
    const store = useAiChatStore()
    store.messages['s1'] = [msg('m9', 9)]
    store.oldestLoadedSeq['s1'] = 9
    store.hasMoreMessages['s1'] = true
    // 异常服务端：同一窗口原样返回（游标不动）
    vi.mocked(api.getMessages).mockResolvedValue({ messages: [msg('m9', 9)], hasMore: true })
    await expect(store.ensureLoadedUntil('s1', 1)).resolves.toBe(false)
    expect(api.getMessages).toHaveBeenCalledTimes(1)
  })
})
```

- [ ] **Step 2: 跑红**

Run: `npx vitest run src/stores/__tests__/aiChat.outline.test.ts`
Expected: FAIL（store 无 turnsBySession/loadTurns/ensureLoadedUntil）

- [ ] **Step 3: 实现**

`src/api/aiChat.ts`：

```typescript
/** 会话大纲条目：用户输入回合投影（GET /turns）；local 标记为前端在途补尾 */
export interface SessionTurn {
  id: string
  seq?: number
  createdAt?: string
  preview: string
  local?: boolean
}

export function getTurns(id: string) {
  return get<{ turns: SessionTurn[] }>(
    `/ai/chat/sessions/${encodeURIComponent(id)}/turns`)
}
```

`AiSessionSearchHit` 接口内追加：

```typescript
  /** content 命中时的消息锚点（title/file/preview 命中为 null） */
  messageId?: string | null
  seq?: number | null
```

`src/stores/aiChat.ts`：

1. import 行（:29 附近）补 `type SessionTurn`（从 `@/api/aiChat`），并把 `getTurns` 加进该文件的 api import。
2. state（:232 附近 `messages: {}` 旁）加：

```typescript
    turnsBySession: {} as Record<string, SessionTurn[]>,
    turnsError: {} as Record<string, boolean>,
```

3. `selectSession`（:395 起）在 `this.loadSessionSkillFit(id)`（:409）之后加一行：

```typescript
      void this.loadTurns(id)
```

4. `_reloadPersisted`（:1323 起，`this._recomputeUsage(sid)`（:1335）之后）加一行：

```typescript
      void this.loadTurns(sid)  // 回合结束：新输入进大纲
```

5. `loadOlderMessages`（:459-473）重写为共用前插 + 新增两个 action（整体替换）：

```typescript
    /** 加载更早的历史消息（before 游标向前翻页，按 seq 时序）。返回是否真的加载到。 */
    async loadOlderMessages(id: string): Promise<boolean> {
      const before = this.oldestLoadedSeq[id]
      if (!before) return false
      try {
        const history = await getMessages(id, { before: String(before), limit: 200 })
        return this._prependOlderWindow(id, history)
      } catch { return false }
    },

    /** 把一页更早历史前插进消息数组并推进游标（loadOlderMessages /
     *  ensureLoadedUntil 共用；前插保证消息流连续，流式目标按 id 定位不受影响）。 */
    _prependOlderWindow(id: string, history: { messages: AiMessage[]; hasMore?: boolean }): boolean {
      if (!history.messages.length) {
        this.hasMoreMessages[id] = false
        return false
      }
      this.messages[id] = [...history.messages, ...(this.messages[id] ?? [])]
      this.hasMoreMessages[id] = !!history.hasMore
      this.oldestLoadedSeq[id] = history.messages[0]?.seq ?? this.oldestLoadedSeq[id]
      return true
    },

    /** 大纲/搜索定位（spec §5）：把历史窗口向前加载到覆盖 targetSeq 为止
     *  （1000/页）。返回 targetSeq 对应消息是否已在内存窗口中。 */
    async ensureLoadedUntil(id: string, targetSeq: number): Promise<boolean> {
      while ((this.oldestLoadedSeq[id] ?? 0) > targetSeq) {
        const before = this.oldestLoadedSeq[id]
        if (before == null) break
        let history: { messages: AiMessage[]; hasMore?: boolean }
        try {
          history = await getMessages(id, { before: String(before), limit: 1000 })
        } catch { break }
        const ok = this._prependOlderWindow(id, history)
        if (!ok || this.oldestLoadedSeq[id] === before) break  // 游标不动 → 防死循环
      }
      return (this.messages[id] ?? []).some(m => m.seq === targetSeq)
    },

    /** 会话大纲：拉取用户回合投影；失败保留旧列表并标记错误（面板行内重试）。 */
    async loadTurns(id: string) {
      try {
        const { turns } = await getTurns(id)
        this.turnsBySession[id] = turns
        this.turnsError[id] = false
      } catch {
        this.turnsError[id] = true
      }
    },
```

6. **适配既有 mock**：以下 9 个文件的 `vi.mock('@/api/aiChat', () => ({...}))` factory 各补一行 `getTurns: vi.fn(async () => ({ turns: [] })),`（否则任何走到 selectSession/_reloadPersisted 的既有用例会因 `getTurns is not a function` 崩）：
   `aiChat.test.ts`、`aiChat.agent.test.ts`、`aiChat.batchsse.test.ts`、`aiChat.command.test.ts`、`aiChat.mcp.test.ts`、`aiChat.permission.test.ts`、`aiChat.question.test.ts`、`aiChat.skill.test.ts`、`aiChat.subagent-ownership.test.ts`

- [ ] **Step 4: 跑绿 + 全量 store 回归**

Run: `npx vitest run src/stores/__tests__/aiChat.outline.test.ts`
Expected: 6 PASS

Run: `npx vitest run src/stores/__tests__/`
Expected: 全 PASS（若组件目录下 mock '@/api/aiChat' 的用例（SubtaskBubble 等）也有崩的，同样补 `getTurns: vi.fn(async () => ({ turns: [] })),`）

- [ ] **Step 5: Commit**

```bash
git add src/api/aiChat.ts src/stores/aiChat.ts src/stores/__tests__/
git commit -m "feat(ai-chat): store 回合状态 loadTurns + ensureLoadedUntil 跨窗口加载"
```

---

### Task 4: `SessionOutlinePanel` 组件 + 大纲纯函数

**Files:**
- Create: `src/components/ai-chat/outlineUtils.ts`
- Create: `src/components/ai-chat/SessionOutlinePanel.vue`
- Test: `src/components/ai-chat/__tests__/outlineUtils.test.ts`、`src/components/ai-chat/__tests__/SessionOutlinePanel.test.ts`

**Interfaces:**
- Consumes: `SessionTurn`（Task 3）。
- Produces:
  - `nearestUserTurnSeq(messages: AiMessage[], upToIdx: number): number | null`
  - `previewOfParts(parts: AiContentPart[]): string`
  - `SessionOutlinePanel` props `{ turns: SessionTurn[]; activeSeq: number | null; locatedId: string | null; error: boolean }`，emits `locate(turn)` / `retry` / `collapse`；DOM 契约 `[data-test="outline-item"]`、`[data-turn-id]`、`.is-active`、`.is-located`、`[data-test="outline-collapse"]`、`[data-test="outline-retry"]`、`[data-test="outline-error"]`、`.outline__truncated`（Task 5/7 依赖）。

- [ ] **Step 1: 写失败测试**

`src/components/ai-chat/__tests__/outlineUtils.test.ts`：

```typescript
import { describe, it, expect } from 'vitest'
import { nearestUserTurnSeq, previewOfParts } from '../outlineUtils'
import type { AiMessage, AiContentPart } from '@/api/aiChat'

const m = (id: string, role: AiMessage['role'], seq?: number): AiMessage =>
  ({ id, role, content: [], seq })

describe('nearestUserTurnSeq', () => {
  const msgs: AiMessage[] = [
    m('u1', 'user', 2), m('a1', 'assistant', 3), m('a2', 'assistant', 5), m('u2', 'user', 8),
  ]
  it('maps a visible index back to the latest user turn at or before it', () => {
    expect(nearestUserTurnSeq(msgs, 2)).toBe(2)
    expect(nearestUserTurnSeq(msgs, 3)).toBe(2)
  })
  it('returns null when nothing above the first user turn', () => {
    const above = [m('a0', 'assistant', 1), ...msgs]
    expect(nearestUserTurnSeq(above, 0)).toBe(null)
  })
  it('skips local user messages without seq', () => {
    const withLocal: AiMessage[] = [...msgs, { ...m('local_1', 'user'), seq: undefined }]
    expect(nearestUserTurnSeq(withLocal, 4)).toBe(8)
  })
})

describe('previewOfParts', () => {
  it('prefers first non-empty text part, whitespace-collapsed, capped at 160', () => {
    expect(previewOfParts([{ type: 'text', text: '  hello \n world  ' }])).toBe('hello world')
    expect(previewOfParts([{ type: 'text', text: 'x'.repeat(200) }])).toHaveLength(160)
  })
  it('falls back to file names with 📎 and 等 N 个', () => {
    const f = (n: string): AiContentPart => ({ type: 'file', name: n, path: 'uploads/' + n })
    expect(previewOfParts([f('a.xlsx'), f('b.csv')])).toBe('📎 a.xlsx、b.csv')
    expect(previewOfParts([f('1'), f('2'), f('3'), f('4')])).toBe('📎 1、2、3 等 4 个')
  })
  it('placeholder when nothing to show', () => {
    expect(previewOfParts([])).toBe('（无文本）')
    expect(previewOfParts([{ type: 'text', text: '   ' }])).toBe('（无文本）')
  })
})
```

`src/components/ai-chat/__tests__/SessionOutlinePanel.test.ts`：

```typescript
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SessionOutlinePanel from '@/components/ai-chat/SessionOutlinePanel.vue'
import type { SessionTurn } from '@/api/aiChat'

const today = new Date().toISOString()
const oldDay = new Date(Date.now() - 3 * 86400000).toISOString()
const turns: SessionTurn[] = [
  { id: 't1', seq: 1, createdAt: oldDay, preview: '旧回合' },
  { id: 't2', seq: 9, createdAt: today, preview: '今天的问题' },
]

describe('SessionOutlinePanel', () => {
  it('renders turns; cross-day entries get MM-DD prefix, today only HH:mm', () => {
    const w = mount(SessionOutlinePanel, {
      props: { turns, activeSeq: null, locatedId: null, error: false },
    })
    expect(w.findAll('[data-test="outline-item"]')).toHaveLength(2)
    expect(w.find('[data-turn-id="t1"] .outline__time').text()).toMatch(/^\d{2}-\d{2} \d{2}:\d{2}$/)
    expect(w.find('[data-turn-id="t2"] .outline__time').text()).toMatch(/^\d{2}:\d{2}$/)
  })

  it('emits locate with the clicked turn', async () => {
    const w = mount(SessionOutlinePanel, {
      props: { turns, activeSeq: null, locatedId: null, error: false },
    })
    await w.find('[data-turn-id="t2"]').trigger('click')
    expect(w.emitted('locate')![0][0]).toEqual(turns[1])
  })

  it('is-active follows activeSeq; is-located follows locatedId (independent)', () => {
    const w = mount(SessionOutlinePanel, {
      props: { turns, activeSeq: 9, locatedId: 't1', error: false },
    })
    expect(w.find('[data-turn-id="t2"]').classes()).toContain('is-active')
    expect(w.find('[data-turn-id="t1"]').classes()).toContain('is-located')
  })

  it('error state shows inline retry; header collapse emits', async () => {
    const w = mount(SessionOutlinePanel, {
      props: { turns: [], activeSeq: null, locatedId: null, error: true },
    })
    expect(w.find('[data-test="outline-error"]').exists()).toBe(true)
    await w.find('[data-test="outline-retry"]').trigger('click')
    expect(w.emitted('retry')).toHaveLength(1)
    await w.find('[data-test="outline-collapse"]').trigger('click')
    expect(w.emitted('collapse')).toHaveLength(1)
  })

  it('flags truncation at 500 entries', () => {
    const many: SessionTurn[] = Array.from({ length: 500 }, (_, i) => ({ id: 'x' + i, preview: 'p' }))
    const w = mount(SessionOutlinePanel, {
      props: { turns: many, activeSeq: null, locatedId: null, error: false },
    })
    expect(w.find('.outline__truncated').exists()).toBe(true)
  })
})
```

- [ ] **Step 2: 跑红**

Run: `npx vitest run src/components/ai-chat/__tests__/outlineUtils.test.ts src/components/ai-chat/__tests__/SessionOutlinePanel.test.ts`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**

`src/components/ai-chat/outlineUtils.ts`：

```typescript
/**
 * 会话大纲纯逻辑（spec 2026-10-25-session-outline §6）：与组件/视图解耦，
 * 便于单测。previewOfParts 与后端 _turn_preview 同规则（text 优先、纯文件
 * 给文件名摘要）。
 */
import type { AiMessage, AiContentPart } from '@/api/aiChat'

/** 视口顶部覆盖到 messages[upToIdx] 时，向前找最近一条已落库用户回合的 seq。 */
export function nearestUserTurnSeq(messages: AiMessage[], upToIdx: number): number | null {
  for (let i = Math.min(upToIdx, messages.length - 1); i >= 0; i--) {
    const m = messages[i]
    if (m && m.role === 'user' && m.seq != null) return m.seq
  }
  return null
}

/** 用户消息 parts 的单行预览（≤160 字符）。 */
export function previewOfParts(parts: AiContentPart[]): string {
  for (const p of parts) {
    if (p.type === 'text' && p.text.trim()) {
      return p.text.replace(/\s+/g, ' ').trim().slice(0, 160)
    }
  }
  const names = parts
    .filter((p): p is Extract<AiContentPart, { type: 'file' }> => p.type === 'file')
    .map(p => p.name)
    .filter(Boolean)
  if (!names.length) return '（无文本）'
  const shown = names.slice(0, 3).join('、')
  return '📎 ' + shown + (names.length > 3 ? ` 等 ${names.length} 个` : '')
}
```

`src/components/ai-chat/SessionOutlinePanel.vue`：

```vue
<script setup lang="ts">
/**
 * 会话大纲面板（spec 2026-10-25-session-outline）：列出会话内全部用户输入
 * （后端 /turns 投影 + 父视图本地补尾），点击条目由父视图定位到对应消息。
 * 纯展示组件——数据合并/跳转/scroll-spy 都在 AiChatView。
 */
import { computed } from 'vue'
import type { SessionTurn } from '@/api/aiChat'

const props = defineProps<{
  turns: SessionTurn[]
  /** scroll-spy 跟随高亮的回合 seq（视口顶部所在回合） */
  activeSeq: number | null
  /** 点击定位后的脉冲目标（2.4s 自动清除，与消息流 .msg--located 同步） */
  locatedId: string | null
  /** /turns 加载失败（保留旧列表 + 行内重试） */
  error: boolean
}>()

const emit = defineEmits<{
  (e: 'locate', t: SessionTurn): void
  (e: 'retry'): void
  (e: 'collapse'): void
}>()

function fmtTime(iso?: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  const startOfToday = new Date().setHours(0, 0, 0, 0)
  if (d.getTime() >= startOfToday) return hm
  const md = `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  return `${md} ${hm}`
}

const truncated = computed(() => props.turns.length >= 500)
</script>

<template>
  <div class="outline" data-test="session-outline">
    <div class="outline__head">
      <span class="outline__title">会话大纲</span>
      <span class="outline__count">{{ turns.length }}</span>
      <button class="outline__collapse" type="button" title="收起大纲"
              aria-label="收起大纲" data-test="outline-collapse" @click="emit('collapse')">‹</button>
    </div>
    <div v-if="error" class="outline__error" data-test="outline-error">
      大纲加载失败
      <button type="button" data-test="outline-retry" @click="emit('retry')">重试</button>
    </div>
    <div v-else-if="!turns.length" class="outline__empty">暂无输入</div>
    <div v-else class="outline__list">
      <button
        v-for="t in turns" :key="t.id" type="button" class="outline__item"
        :class="{ 'is-active': t.seq != null && t.seq === activeSeq, 'is-located': t.id === locatedId }"
        data-test="outline-item" :data-turn-id="t.id" :title="t.preview"
        @click="emit('locate', t)"
      >
        <span class="outline__time">{{ fmtTime(t.createdAt) }}</span>
        <span class="outline__preview">{{ t.local ? '（发送中）' : t.preview }}</span>
      </button>
      <div v-if="truncated" class="outline__truncated">仅显示前 500 条</div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.outline { display: flex; flex-direction: column; height: 100%; overflow: hidden; }
.outline__head {
  display: flex; align-items: center; gap: 6px;
  padding: 8px 10px; font-size: 12px; color: var(--el-text-color-secondary);
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.outline__title { font-weight: 600; }
.outline__count { color: var(--el-text-color-placeholder); }
.outline__collapse {
  margin-left: auto; border: none; background: none; cursor: pointer;
  color: var(--el-text-color-secondary); font-size: 14px; line-height: 1;
  &:hover { color: var(--el-color-primary); }
}
.outline__list { overflow-y: auto; flex: 1; }
.outline__item {
  display: block; width: 100%; text-align: left; border: none; background: none;
  padding: 6px 10px; cursor: pointer; border-left: 2px solid transparent;
  &:hover { background: var(--el-fill-color-light); }
  &.is-active { border-left-color: var(--el-color-primary); background: var(--el-fill-color); }
  &.is-located { background: var(--el-color-primary-light-9); }
}
.outline__time { display: block; font-size: 11px; color: var(--el-text-color-placeholder); }
.outline__preview {
  display: block; font-size: 12px; color: var(--el-text-color-primary);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.outline__error, .outline__empty, .outline__truncated {
  padding: 10px; font-size: 12px; color: var(--el-text-color-secondary);
}
.outline__error button { margin-left: 6px; }
</style>
```

- [ ] **Step 4: 跑绿**

Run: `npx vitest run src/components/ai-chat/__tests__/outlineUtils.test.ts src/components/ai-chat/__tests__/SessionOutlinePanel.test.ts`
Expected: 10 PASS

- [ ] **Step 5: Commit**

```bash
git add src/components/ai-chat/outlineUtils.ts src/components/ai-chat/SessionOutlinePanel.vue src/components/ai-chat/__tests__/outlineUtils.test.ts src/components/ai-chat/__tests__/SessionOutlinePanel.test.ts
git commit -m "feat(ai-chat): SessionOutlinePanel 组件 + 大纲纯函数"
```

---

### Task 5: AiChatView 集成（右栏 / 定位 / scroll-spy / 折叠持久化）

**Files:**
- Modify: `src/utils/storage.ts:32`（STORAGE_KEYS 加一项）
- Modify: `src/views/ai-chat/AiChatView.vue`（模板 :1526/:1540/:1825 附近；script import 区、locateTodoStep（:600）之后；样式区）

**Interfaces:**
- Consumes: Task 3 的 store 状态/action、Task 4 的组件与 `nearestUserTurnSeq`/`previewOfParts`、`STORAGE_KEYS`。
- Produces（Task 6/7 依赖）:
  - 消息节点带 `data-msg-id="<m.id>"`；`.msg--located` 同时受 `locatedMsgId` 驱动
  - `locateMessageById(mid: string)` / `jumpToMessage(sid: string, messageId: string, seq: number | null): Promise<void>`
  - DOM 契约：`[data-test="session-outline-panel"]`（右栏）、`[data-test="outline-open"]`（收起后的再入口按钮）、`@scroll` 改绑 `onChatScrollAll`
  - localStorage key `check-manage:aiChatOutlineCollapsed`（boolean）

说明：视图层（2760 行）无既有组件测试；本任务的可测纯逻辑已拆入 Task 4，行为验证走 Task 7 E2E。本任务验证 = 类型检查 + 既有前端测试不回归。

- [ ] **Step 1: storage key**

`src/utils/storage.ts` 的 `STORAGE_KEYS`（:32 `AI_CHAT_SIDEBAR_WIDTH` 之后）加：

```typescript
  /** AI 对话页会话大纲右栏折叠状态 */
  AI_CHAT_OUTLINE_COLLAPSED: 'check-manage:aiChatOutlineCollapsed',
```

- [ ] **Step 2: script 集成**

`AiChatView.vue` script：

1. import 区（:53 既有 `@/api/aiChat` 类型 import 行）补 `type SessionTurn`；新增：

```typescript
import SessionOutlinePanel from '@/components/ai-chat/SessionOutlinePanel.vue'
import { nearestUserTurnSeq, previewOfParts } from '@/components/ai-chat/outlineUtils'
```

2. 在 `locateTodoStep`（:600-609）之后插入：

```typescript
// ---- 会话大纲（右侧面板，spec 2026-10-25-session-outline §6）：
// 用户输入导航 + 跨分页精准定位 + 滚动跟随 ----
const outlineCollapsed = ref<boolean>(getStorage(STORAGE_KEYS.AI_CHAT_OUTLINE_COLLAPSED, false))
function toggleOutline() {
  outlineCollapsed.value = !outlineCollapsed.value
  setStorage(STORAGE_KEYS.AI_CHAT_OUTLINE_COLLAPSED, outlineCollapsed.value)
}
// 大纲 = 后端 turns + 内存里仍为 local_* 的在途用户消息（发送成功后气泡 id
// 被就地改写成服务端 id，改写后自然只从 turns 来，不存在重复）
const outlineTurns = computed<SessionTurn[]>(() => {
  const id = activeId.value
  if (!id) return []
  const serverTurns = store.turnsBySession[id] ?? []
  const local = (store.messages[id] ?? [])
    .filter(m => m.role === 'user' && m.id.startsWith('local_'))
    .map(m => ({ id: m.id, seq: m.seq, createdAt: m.createdAt, preview: previewOfParts(m.content), local: true }))
  return [...serverTurns, ...local]
})
const activeTurnSeq = ref<number | null>(null)
// 点击脉冲：id 版（todo 面板 locatedMsgIdx 是下标版，两者并存；历史前插不打歪 id 锚点）
const locatedMsgId = ref<string | null>(null)
let locatedIdTimer: ReturnType<typeof setTimeout> | null = null
function locateMessageById(mid: string) {
  locatedMsgId.value = null
  void nextTick(() => {
    document.querySelector(`[data-msg-id="${CSS.escape(mid)}"]`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    locatedMsgId.value = mid
    if (locatedIdTimer) clearTimeout(locatedIdTimer)
    locatedIdTimer = setTimeout(() => { locatedMsgId.value = null }, 2400)
  })
}
async function jumpToMessage(sid: string, messageId: string, seq: number | null) {
  if (seq == null || !(await store.ensureLoadedUntil(sid, seq))) {
    void store.loadTurns(sid)
    ElMessage.warning('该消息可能已被删除')
    return
  }
  locateMessageById(messageId)
}
async function locateTurn(t: SessionTurn) {
  const id = activeId.value
  if (!id) return
  if (t.seq == null) { locateMessageById(t.id); return }  // 本地在途消息必在窗口内
  await jumpToMessage(id, t.id, t.seq)
}
// scroll-spy：视口顶部覆盖到哪条消息 → 大纲跟随高亮其所属回合
function updateActiveTurn() {
  const el = getScrollEl()
  const id = activeId.value
  if (!el || !id) return
  const msgs = store.messages[id] ?? []
  const top = el.getBoundingClientRect().top + 8
  let mi = 0
  el.querySelectorAll<HTMLElement>('[data-msg-idx]').forEach((n) => {
    const idx = Number(n.dataset.msgIdx)
    if (!Number.isNaN(idx) && n.getBoundingClientRect().top <= top) mi = Math.max(mi, idx)
  })
  activeTurnSeq.value = nearestUserTurnSeq(msgs, mi)
}
let outlineSpyTimer: ReturnType<typeof setTimeout> | null = null
function onChatScrollAll() {
  onChatScroll()  // 既有贴底/未读逻辑（useChatScroll）
  if (outlineSpyTimer) return
  outlineSpyTimer = setTimeout(() => { outlineSpyTimer = null; updateActiveTurn() }, 200)
}
watch(() => messages.value.length, () => { void nextTick(updateActiveTurn) })
```

- [ ] **Step 3: 模板集成**

1. `ElScrollbar`（:1526）的 `@scroll="onChatScroll"` 改为 `@scroll="onChatScrollAll"`。
2. `.msg` 节点（:1540-1544）改为：

```html
            <div
              v-for="(m, mi) in messages" :key="m.id"
              class="msg" :class="[`msg--${m.role}`, { 'msg--located': locatedMsgIdx === mi || locatedMsgId === m.id }]"
              :data-msg-idx="mi"
              :data-msg-id="m.id"
            >
```

3. 「回到底部」浮动按钮（:1827-1839）之前，加收起态再入口按钮（同一滚动容器内、绝对定位）：

```html
        <button
          v-if="activeId && outlineCollapsed" type="button" class="ai-chat__outline-open"
          title="展开会话大纲" aria-label="展开会话大纲" data-test="outline-open"
          @click="toggleOutline"
        >☰</button>
```

4. `</section>`（`ai-chat__main` 结束、根 div 结束之前）后加右栏：

```html
    <!-- 会话大纲右栏：全部用户输入，点击精准定位（spec 2026-10-25-session-outline） -->
    <aside v-if="activeId && !outlineCollapsed" class="ai-chat__outline" data-test="session-outline-panel">
      <SessionOutlinePanel
        :turns="outlineTurns"
        :active-seq="activeTurnSeq"
        :located-id="locatedMsgId"
        :error="!!store.turnsError[activeId]"
        @locate="locateTurn"
        @retry="activeId && store.loadTurns(activeId)"
        @collapse="toggleOutline"
      />
    </aside>
```

- [ ] **Step 4: 样式**

`<style scoped lang="scss">` 区（`.ai-chat__sidebar`（:2110）附近，同级追加）：

```scss
.ai-chat__outline {
  flex-shrink: 0;
  width: 260px;
  border-left: 1px solid var(--el-border-color-lighter);
  background: var(--el-bg-color);
  overflow: hidden;
}
.ai-chat__outline-open {
  position: absolute; top: 10px; right: 12px; z-index: 5;
  width: 28px; height: 28px; border-radius: 6px;
  border: 1px solid var(--el-border-color-lighter);
  background: var(--el-bg-color-overlay); color: var(--el-text-color-secondary);
  cursor: pointer; font-size: 14px; line-height: 1;
  &:hover { color: var(--el-color-primary); border-color: var(--el-color-primary-light-5); }
}
```

定位父级：`.ai-chat__messages-wrap` 若未设 `position: relative`，加上（对齐 `.ai-chat__jump-bottom` 的定位做法——先 grep 该类样式确认其定位父级再落位）。

- [ ] **Step 5: 验证**

Run: `npx vue-tsc --noEmit`（或 package.json 既有 type-check 脚本）
Expected: 无新增错误

Run: `npx vitest run src`
Expected: 全 PASS

- [ ] **Step 6: Commit**

```bash
git add src/utils/storage.ts src/views/ai-chat/AiChatView.vue
git commit -m "feat(ai-chat): 会话大纲右栏集成（定位/跟随/折叠持久化）"
```

---

### Task 6: 搜索命中定位接线

**Files:**
- Modify: `src/views/ai-chat/AiChatView.vue:347-360`（`selectSearchHit`）

**Interfaces:**
- Consumes: `AiSessionSearchHit.messageId/seq`（Task 3 类型 + Task 2 后端）、`jumpToMessage`（Task 5）。
- Produces: 搜索/批子会话命中打开后自动定位（Task 7 TC-OUTLINE-04 验证）。

- [ ] **Step 1: 实现（接线改动，行为验证在 Task 7 E2E）**

`selectSearchHit` 整体替换为：

```typescript
async function selectSearchHit(h: AiSessionSearchHit) {
  // 批任务子会话：加载所属批次 → 展开分组 → 打开子会话（Spec §4.4）。
  // 保留搜索条件，允许返回结果继续查看其余命中。
  if (h.isBatchChild && h.batchId) {
    try {
      await batches.ensureBatchInList(h.batchId)
      await batches.selectBatch(h.batchId)
      await selectBatchChild(h.id)
      if (h.messageId) await jumpToMessage(h.id, h.messageId, h.seq ?? null)
    } catch { ElMessage.error('打开批任务子会话失败') }
    return
  }
  clearSessionSearch()
  await selectSession(h.id)
  if (h.messageId) await jumpToMessage(h.id, h.messageId, h.seq ?? null)
}
```

（`jumpToMessage` 是函数声明，声明提升使其在 :347 可前向引用。）

- [ ] **Step 2: 验证**

Run: `npx vue-tsc --noEmit` && `npx vitest run src`
Expected: 无新增错误、全 PASS

- [ ] **Step 3: Commit**

```bash
git add src/views/ai-chat/AiChatView.vue
git commit -m "feat(ai-chat): 搜索命中定位接线（selectSearchHit 跳转）"
```

---

### Task 7: E2E 真实链路 + 全量回归

**Files:**
- Create: `e2e/ai-full/ai-session-outline.spec.ts`

**Interfaces:**
- Consumes: Task 1-6 全部（`/turns`、搜索锚点、面板 DOM 契约、跳转）、`e2e/ai-full/helpers.ts` 的 `openChatSession`、`e2e/ai-full/db-helpers.ts` 的 `seedPlainSession`/`cleanupSessionsByPrefix`。

前置：dev 栈在跑（后端 3002 + 前端 dev server，沿用 ai-full 套件现行栈；`seq` 为 BIGSERIAL 自动分配、`search_text` 由 DB 触发器物化，种子直插即可被搜索命中）。

- [ ] **Step 1: 写 E2E（在 Task 6 commit 上先跑，允许红）**

`e2e/ai-full/ai-session-outline.spec.ts`：

```typescript
/**
 * 会话大纲（spec 2026-10-25-session-outline）：右栏全量用户输入、跨分页精准
 * 定位、滚动跟随高亮、搜索命中定位。真实链路：seed → dev 栈页面行为。
 */
import { expect, test } from '@playwright/test'
import { openChatSession } from './helpers'
import { seedPlainSession, cleanupSessionsByPrefix } from './db-helpers'

const OUTLINE_ITEM = '[data-test="outline-item"]'

test.describe.serial('会话大纲', () => {
  let sid: string

  test.beforeAll(async () => {
    // 260 条消息（130 轮用户输入×2）：初始窗口 200 条装不下 → 必须跨分页
    const msgs: { role: 'user' | 'assistant'; text: string }[] = []
    for (let i = 1; i <= 130; i++) {
      msgs.push({ role: 'user', text: `AITEST 大纲第${i}轮用户输入` })
      msgs.push({ role: 'assistant', text: `第${i}轮回复：处理完毕，共三步。` })
    }
    sid = await seedPlainSession({ key: 'outline', messages: msgs })
  })

  test.afterAll(() => { cleanupSessionsByPrefix() })

  test('TC-OUTLINE-01 大纲全量显示 + 折叠往返', async ({ page }) => {
    const turnsResp = page.waitForResponse(r => r.url().includes(`/sessions/${sid}/turns`))
    await openChatSession(page, sid)
    const { turns } = await (await turnsResp).json()
    expect(turns).toHaveLength(130)
    await expect(page.locator(OUTLINE_ITEM)).toHaveCount(130)
    // 消息窗口只加载最新 200 条 → 「加载更早消息」仍在
    await expect(page.locator('.ai-chat__load-older')).toBeVisible()
    // 折叠往返
    await page.locator('[data-test="outline-collapse"]').click()
    await expect(page.locator('[data-test="session-outline-panel"]')).toBeHidden()
    await page.locator('[data-test="outline-open"]').click()
    await expect(page.locator(OUTLINE_ITEM).first()).toBeVisible()
  })

  test('TC-OUTLINE-02 点击最旧回合：自动加载更早并精准定位', async ({ page }) => {
    const turnsResp = page.waitForResponse(r => r.url().includes(`/sessions/${sid}/turns`))
    await openChatSession(page, sid)
    const { turns } = await (await turnsResp).json()
    const oldest = turns[0]
    await page.locator(OUTLINE_ITEM).first().click()
    await expect(page.locator(`[data-msg-id="${oldest.id}"]`)).toHaveClass(/msg--located/)
    // 历史已全部加载 → 「加载更早消息」消失
    await expect(page.locator('.ai-chat__load-older')).toBeHidden()
  })

  test('TC-OUTLINE-03 滚动跟随：滚到底部大纲高亮最后一轮', async ({ page }) => {
    const turnsResp = page.waitForResponse(r => r.url().includes(`/sessions/${sid}/turns`))
    await openChatSession(page, sid)
    const { turns } = await (await turnsResp).json()
    const last = turns[turns.length - 1]
    const wrap = page.locator('.ai-chat__messages .el-scrollbar__wrap')
    await wrap.evaluate(el => { el.scrollTop = el.scrollHeight })
    await expect(page.locator(`[data-turn-id="${last.id}"]`)).toHaveClass(/is-active/)
  })

  test('TC-OUTLINE-04 搜索内容命中：打开会话并定位到命中消息', async ({ page }) => {
    const searchResp = page.waitForResponse(r => r.url().includes('/sessions/search'))
    await openChatSession(page, sid)
    await page.locator('.ai-sidebar__search input').fill('大纲第3轮用户输入')
    const { sessions } = await (await searchResp).json()
    const hit = (sessions as any[]).find(s => s.id === sid)
    expect(hit?.messageId).toBeTruthy()
    // 命中行点击 → selectSearchHit → 定位（行点击目标与既有搜索 UI 一致，
    // 用标题文本定位行；若行选择器不同，按 :1330-1368 模板实际结构调整）
    await page.getByText('AITEST-SESSV2-outline', { exact: false }).first().click()
    await expect(page.locator(`[data-msg-id="${hit.messageId}"]`)).toHaveClass(/msg--located/)
  })
})
```

注意：本套件的串行/清理惯例对齐 ai-full 既有 spec（跑前查 `playwright.config.ts` 的 workers/fullyParallel 设置与其他 spec 的 afterAll 用法；`cleanupSessionsByPrefix` 会删所有 AITEST-SESSV2 前缀会话，若配置允许多文件并行需确认不被并行套件共用）。

- [ ] **Step 2: 跑 E2E**

Run: `npm run test:e2e -- ai-session-outline`
Expected: 4 PASS（若 TC-04 行点击目标与模板实际不符，按 `AiChatView.vue:1330-1368` 的搜索结果行结构修正定位器——只许改选择器，不许放宽断言）

- [ ] **Step 3: 全量回归**

Run: `npx vitest run src`
Expected: 全 PASS

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_ai_chat_turns.py tests/test_ai_chat_search.py tests/test_ai_chat_messages_paging.py -v`
Expected: 全 PASS

（AI 页面其余 e2e/ai-full 套件是否全跑由执行者按当时栈状态决定；本特性触碰 AiChatView，至少补跑一个既打开会话的 spec 冒烟，如 `npm run test:e2e -- ai-chat-plantuml`。）

- [ ] **Step 4: Commit**

```bash
git add e2e/ai-full/ai-session-outline.spec.ts
git commit -m "test(e2e): 会话大纲 E2E——全量/跳转/跟随/搜索定位"
```
