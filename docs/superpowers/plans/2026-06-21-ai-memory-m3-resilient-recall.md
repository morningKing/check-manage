# AI 长期记忆 — M3 实现计划（会话韧性恢复）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** OpenCode session 失效（进程重启/过期）时，发消息不再 500——自动新建 OpenCode session、把最近历史 + 长期记忆重新注入、更新会话绑定并重发，让旧会话「无缝复活」继续。

**Architecture:** 只在 `send_message` 里 `send_prompt_async` 抛错时触发恢复（正常路径零改动）。恢复 = `create_session`（复用旧 workspace）→ 注入「最近 N 轮历史 + 已有的记忆段」→ `UPDATE opencode_session_id` → 重发一次；若恢复仍失败则抛原错（优雅降级，不无限重试）。

**Tech Stack:** Flask、psycopg2、OpenCode HTTP client、pytest。

**Spec:** `docs/superpowers/specs/2026-06-20-ai-session-longterm-memory-design.md` §8。依赖 M1（记忆注入已在 `send_message`）。

**关键假设（诚实标注）:** OpenCode session 失效时 `POST /session/<id>/prompt_async` 返回非 2xx → `send_prompt_async` 抛 `requests.HTTPError`。恢复策略**不依赖精确状态码**：任何发送失败都尝试重建+重发一次，再失败则放弃。真实失效行为需在合并后手动验证（重启 OpenCode 后向旧会话发消息）。

---

## File Structure（M3）
- `server/routes/ai_chat.py` — `send_message` 加恢复分支；新增 `_render_history_block` + `_recover_session_and_resend`
- `server/tests/test_ai_chat_recall.py`（新）
- `docs/user-guide/ai/long-term-memory.md` + `CLAUDE.md` — 补充

---

## Task 1：韧性恢复逻辑

**Files:** Modify `server/routes/ai_chat.py`; Test `server/tests/test_ai_chat_recall.py`.

- [ ] **Step 1: 写失败测试** `server/tests/test_ai_chat_recall.py`:
```python
import sys, os
from unittest.mock import patch, MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import routes.ai_chat as ai_chat

def test_render_history_block_excludes_current_and_formats():
    rows = [
        ('m1', 'user', [{'type': 'text', 'text': '你好'}]),
        ('m2', 'assistant', [{'type': 'text', 'text': '你好，有什么可以帮你'}]),
    ]
    conn = MagicMock(); cur = MagicMock()
    cur.fetchall.return_value = rows
    conn.cursor.return_value = cur
    from contextlib import contextmanager
    @contextmanager
    def fake_db():
        yield conn
    with patch.object(ai_chat, 'get_db', fake_db):
        block = ai_chat._render_history_block('sid', exclude_msg_id='m3', max_turns=6)
    assert '你好' in block and '你好，有什么可以帮你' in block
    assert block.startswith('[此前对话摘要')
    assert block.endswith('\n\n')

def test_render_history_block_empty_returns_empty():
    conn = MagicMock(); cur = MagicMock()
    cur.fetchall.return_value = []
    conn.cursor.return_value = cur
    from contextlib import contextmanager
    @contextmanager
    def fake_db():
        yield conn
    with patch.object(ai_chat, 'get_db', fake_db):
        assert ai_chat._render_history_block('sid', exclude_msg_id='m3') == ''

def test_recover_creates_session_injects_history_and_resends():
    client = MagicMock()
    client.create_session.return_value = 'new-oc-sid'
    sent = {}
    client.send_prompt_async.side_effect = lambda oc, content, **kw: sent.update(oc=oc, content=content)
    conn = MagicMock(); cur = MagicMock()
    conn.cursor.return_value = cur
    from contextlib import contextmanager
    @contextmanager
    def fake_db():
        yield conn
    with patch.object(ai_chat, 'get_db', fake_db), \
         patch.object(ai_chat, '_render_history_block', return_value='[此前对话摘要]\n用户: 你好\n\n'):
        new_id = ai_chat._recover_session_and_resend(
            client, sid='sid', workspace_path='/ws', current_msg_id='m3',
            prompt='原始PROMPT', model='m', agent='', agent_parts=[])
    assert new_id == 'new-oc-sid'
    client.create_session.assert_called_once()
    # opencode_session_id updated in DB
    assert any('opencode_session_id' in str(c.args[0]) for c in cur.execute.call_args_list)
    # resent prompt includes the history block prepended to the original prompt
    assert sent['oc'] == 'new-oc-sid'
    assert '此前对话摘要' in sent['content'] and '原始PROMPT' in sent['content']
```

- [ ] **Step 2: Run, confirm FAIL** (`_render_history_block`/`_recover_session_and_resend` don't exist):
`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_ai_chat_recall.py -v`

- [ ] **Step 3: Add the two helpers to `server/routes/ai_chat.py`** (near the other module-level helpers; ensure `get_db` and `OpenCodeClient`/`OPENCODE_BASE_URL` are already imported — they are):
```python
def _render_history_block(sid, exclude_msg_id, max_turns=6):
    """最近 max_turns*2 条消息（不含当前这条）渲染成纯文本摘要，供会话复活时重注上下文。"""
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, role, content FROM ai_chat_messages "
                "WHERE session_id=%s AND id != %s "
                "ORDER BY created_at DESC, id DESC LIMIT %s",
                (sid, exclude_msg_id, max_turns * 2),
            )
            rows = cur.fetchall()
    except Exception:
        return ''
    if not rows:
        return ''
    rows = list(reversed(rows))  # back to chronological
    lines = []
    for _id, role, content in rows:
        text = ''
        if isinstance(content, list):
            text = '\n'.join(p.get('text', '') for p in content
                             if isinstance(p, dict) and p.get('type') == 'text').strip()
        if not text:
            continue
        who = '用户' if role == 'user' else '助手'
        lines.append(f'{who}: {text}')
    if not lines:
        return ''
    return '[此前对话摘要（会话已恢复，供你延续上下文）]\n' + '\n'.join(lines) + '\n\n'


def _recover_session_and_resend(client, sid, workspace_path, current_msg_id,
                                prompt, model, agent, agent_parts):
    """OpenCode session 失效时：新建 session + 注入历史 + 更新绑定 + 重发。返回新的 opencode_session_id。"""
    new_oc = client.create_session(directory=workspace_path, title='恢复会话')
    history = _render_history_block(sid, exclude_msg_id=current_msg_id)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE ai_chat_sessions SET opencode_session_id=%s WHERE id=%s", (new_oc, sid))
    client.send_prompt_async(new_oc, (history + prompt).strip(), model=model,
                             directory=workspace_path, agent=agent, agent_parts=agent_parts)
    return new_oc
```

- [ ] **Step 4: Wrap the send in `send_message` with recovery.** Find the current send (around the `OpenCodeClient(OPENCODE_BASE_URL).send_prompt_async(...)` + `ensure_listener(...)` lines) and change it to:
```python
    import requests as _requests
    client = OpenCodeClient(OPENCODE_BASE_URL)
    oc_sid = sess[2]
    try:
        client.send_prompt_async(
            oc_sid, prompt.strip(), model=effective_model, directory=sess[4],
            agent=requested_agent, agent_parts=agent_mentions,
        )
    except _requests.RequestException:
        # OpenCode session likely gone (restart/expiry) — rebuild + reinject + resend once.
        oc_sid = _recover_session_and_resend(
            client, sid, sess[4], msg_id, prompt.strip(),
            effective_model, requested_agent, agent_mentions,
        )
    ensure_listener(sid, oc_sid, sess[4])
```
> Keep `msg_id` (the just-inserted user message id) as `current_msg_id` so history excludes the current turn. Use the recovered `oc_sid` for `ensure_listener`. The normal path (no exception) is unchanged.

- [ ] **Step 5: Run, confirm PASS** (3 passed).

- [ ] **Step 6: Regression** — `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/ -k "ai_chat or send_message or chat" -q` → no regression (normal path untouched; recovery only on exception).

- [ ] **Step 7: Commit:**
```
cd E:/wsl/check/check-manage
git add server/routes/ai_chat.py server/tests/test_ai_chat_recall.py
git commit -m "feat(memory): resilient session recall — rebuild OpenCode session on failure (M3)"
```

## Context
- ALREADY on branch `feat/ai-session-longterm-memory`. Backend `server/`.
- `send_prompt_async(opencode_session_id, content, *, model, directory, agent, agent_parts)` does a synchronous `requests.post(...).raise_for_status()` — a stale session raises `requests.HTTPError` (a `requests.RequestException`). `create_session(directory=..., title=...)` returns a new opencode session id.
- `send_message` already builds `prompt` (with `_AGENT_DIRECTIVE` + memory block + content); recovery prepends the history block to THAT prompt so memory + recent history both ride along.
- `ai_chat_messages.content` is JSONB (list of `{'type':'text','text':...}`). `msg_id` is the user message just inserted this turn.
- ONLY change the send region; leave everything before it (message insert, prompt build, export hints) intact.

## Your job
TDD; ensure the normal (no-exception) path is byte-for-byte unchanged in behavior; recovery only triggers on `RequestException`; commit; self-review (history excludes current msg; opencode_session_id updated; recovered id used for listener); report.

## Report Format
- **Status / what / tests (fail→pass) / regression / files + SHA / concerns**

---

## Task 2：文档

**Files:** `docs/user-guide/ai/long-term-memory.md`, `CLAUDE.md`.

- [ ] **Step 1:** Append to the user guide:
```markdown
## 会话恢复（M3）

即使后台 AI 运行时（OpenCode）重启过，打开旧会话继续发消息也能**自动恢复**：系统会重建会话、把最近的对话摘要和你的长期记忆重新提供给助手，对话无缝继续——你无需重开会话或重述背景。
```

- [ ] **Step 2:** Append to the CLAUDE.md AI memory sentence:
```
M3：`send_message` 在 `send_prompt_async` 抛错时调 `_recover_session_and_resend`（新建 OpenCode session + 注入最近历史摘要 + 更新 `opencode_session_id` + 重发），实现会话韧性恢复；恢复失败则抛原错（不无限重试）。
```

- [ ] **Step 3: Commit:**
```
git add docs/user-guide/ai/long-term-memory.md CLAUDE.md
git commit -m "docs(memory): resilient session recall guide (M3)"
```

---

## 验收（M3）
- [ ] `pytest tests/test_ai_chat_recall.py` 全绿；既有 chat 测试无回归（正常路径不变）。
- [ ] 手动（合并后）：发消息正常 → 重启 OpenCode → 向同一旧会话再发 → 自动恢复并得到回复。
