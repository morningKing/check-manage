# 子代理执行轨迹可见性 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** OpenCode 委托子代理（`SubtaskPart`）时，实时聊天与批任务两条路径都能持久化并展示子代理自己的完整对话轨迹（文本 + 工具调用），可递归展开到孙代，深度上限 5 层。

**Architecture:** 新建 `ai_chat_subtasks`/`ai_chat_subtask_messages` 两张专用表（不改动 `ai_chat_sessions`/`ai_chat_messages` 的鉴权语义列）；抽取一个共享的纯函数模块 `server/utils/opencode_parts.py` 统一"OpenCode part → 存库内容"的映射（对现有 text/tool 两种类型是行为不变的搬迁，subtask 是新增）；`chat_persist.py`（SSE）与 `batch_engine.py`（REST 轮询）各自在已有的持久化循环里递归发现子代理 sessionID 并追踪其内容/状态；前端一个递归的 `SubtaskBubble.vue` 惰性拉取展开。

**Tech Stack:** Flask + psycopg2 + pytest（后端）；Vue 3 + TypeScript + Element Plus + Vitest（前端）；OpenCode HTTP API。

## Global Constraints

- `ai_chat_subtasks.id` **就是** OpenCode 自己的子会话 `sessionID`（`ses...` 格式），不发明并行的合成 id——子代理总是先在 OpenCode 侧存在、我们才发现它，不像顶层会话是反过来的顺序。
- 深度上限 **5 层**，超过不建行、不渲染，只留提示。
- 子代理消息端点截断到 **500 条**，响应带 `truncated` 与 `total`，按 `seq` 排序（不用 `created_at`）。
- 归属校验：实时聊天端点按 `root_session_id` 的所有权判定；批任务管理端点走 `admin.ai_chat_admin`。猜错 `subtaskId` 一律 **404**。
- 对 `server/utils/opencode_parts.py` 里 text/tool 两种既有 part 类型的映射，**必须是行为不变的纯搬迁**——不得在抽取过程中顺手改动，既有测试（`test_chat_persist.py`、`test_batch_engine.py`）必须原样通过，作为"没有引入行为变化"的证据。
- `apply_event` 现有的返回值契约（`'idle'` / `'changed'` / `None`，供 `_run_listener` 与既有测试直接匹配）**不得改变语义**；子代理相关信号用一个新增的返回值 `'subtask'` 承载，不复用/不覆盖现有三个值的含义。
- 后端测试命令（Windows 必须带该环境变量）：`cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/xxx.py -v`
- 提交信息用中文，带 conventional commit 前缀。

---

## 文件结构

**新建**

| 文件 | 职责 |
|---|---|
| `server/utils/opencode_parts.py` | 共享的 part 映射（`map_part`）+ 错误格式化（`format_opencode_error`） |
| `server/utils/subtask_repo.py` | 子代理数据的查询/写入函数，供 SSE 与批任务两条路径、以及两个只读端点共用 |
| `server/tests/test_opencode_parts.py` | `map_part`/`format_opencode_error` 单测 |
| `server/tests/test_subtask_repo.py` | 仓储函数测试（真实 DB） |
| `server/tests/test_ai_chat_subtasks_routes.py` | 实时聊天子代理端点测试 |
| `src/components/ai-chat/SubtaskBubble.vue` | 递归、惰性加载的子代理气泡 |

**修改**

| 文件 | 改动 |
|---|---|
| `server/init_db.py` | 新增两张表 |
| `server/utils/backup.py` | 两张新表加入备份/还原清单 + RESTORE_ORDER + seq 重播种 |
| `server/tests/conftest.py` | `_rebind_module_get_db_to_real` 的模块名单加 `utils.subtask_repo` |
| `server/utils/chat_persist.py` | 递归发现/追踪/持久化子代理内容与状态（SSE 路径） |
| `server/utils/batch_engine.py` | 递归发现/轮询/持久化子代理内容与状态（REST 路径）；`_TurnFailed` 改为委托 `format_opencode_error` |
| `server/routes/ai_chat.py` | 新增 `GET /ai/chat/sessions/:sid/subtasks/:subtaskId/messages`；`clear_session` 补一步清子代理数据 |
| `server/routes/ai_batch_admin.py` | 新增 `GET /ai/chat/admin/batches/:batchId/sessions/:sid/subtasks/:subtaskId/messages` |
| `src/api/aiChat.ts` | `AiContentPart` 新增 `subtask_use` 变体；新增 `getSubtaskMessages` |
| `src/api/aiBatchAdmin.ts` | 新增 `getAdminSubtaskMessages` |
| `src/views/ai-chat/AiChatView.vue` | 消息渲染循环接入 `SubtaskBubble` |
| `src/components/ai-chat/BatchConversationView.vue` | 同上 |
| `docs/user-guide/ai/assistant.md` | 说明子代理委托可展开查看 |
| `CLAUDE.md` | 记录子代理可见性的架构要点 |

---

## Task 1: 数据库表 + 备份/还原清单

**Files:**
- Modify: `server/init_db.py`（在 `ai_chat_messages` 表定义之后追加）
- Modify: `server/utils/backup.py`
- Modify: `server/tests/conftest.py`
- Test: `server/tests/test_subtask_repo.py`（本任务只写建表验证部分，仓储函数测试见 Task 3）

**Interfaces:**
- Produces: 表 `ai_chat_subtasks(id, root_session_id, parent_subtask_id, parent_part_id, agent, prompt, description, status, error_message, created_at, completed_at)`；表 `ai_chat_subtask_messages(id, subtask_id, role, content, meta, created_at, seq)`。

- [ ] **Step 1: 在 `server/init_db.py` 里 `ai_chat_messages` 表定义之后追加两张新表**

找到这一段（`CREATE TABLE IF NOT EXISTS ai_chat_messages ... ALTER TABLE ai_chat_messages ADD COLUMN IF NOT EXISTS meta JSONB;` 之后），追加：

```sql
-- ==================== 子代理（subtask/subagent 委托）执行轨迹 ====================
-- id 就是 OpenCode 自己的子会话 sessionID（ses... 格式），不发明并行的合成 id——
-- 子代理总是先在 OpenCode 侧存在、我们才从 SubtaskPart 里发现它，跟顶层会话
-- "先建行再补 opencode_session_id" 的顺序正好相反。
CREATE TABLE IF NOT EXISTS ai_chat_subtasks (
    id                VARCHAR(100) PRIMARY KEY,
    root_session_id   VARCHAR(100) NOT NULL REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
    parent_subtask_id VARCHAR(100) REFERENCES ai_chat_subtasks(id) ON DELETE CASCADE,
    parent_part_id    VARCHAR(100),
    agent             TEXT,
    prompt            TEXT,
    description       TEXT,
    status            VARCHAR(20) NOT NULL DEFAULT 'running'
                      CHECK (status IN ('running','completed','failed')),
    error_message     TEXT,
    created_at        TIMESTAMPTZ DEFAULT NOW(),
    completed_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_chat_subtask_root ON ai_chat_subtasks(root_session_id);
CREATE INDEX IF NOT EXISTS idx_chat_subtask_parent ON ai_chat_subtasks(parent_subtask_id);

CREATE TABLE IF NOT EXISTS ai_chat_subtask_messages (
    id          VARCHAR(100) PRIMARY KEY,
    subtask_id  VARCHAR(100) NOT NULL REFERENCES ai_chat_subtasks(id) ON DELETE CASCADE,
    role        VARCHAR(20) NOT NULL,
    content     JSONB NOT NULL,
    meta        JSONB,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    seq         BIGSERIAL
);
CREATE INDEX IF NOT EXISTS idx_chat_subtask_msg_subtask ON ai_chat_subtask_messages(subtask_id, seq);
```

- [ ] **Step 2: 跑一次 `init_db.py` 确认建表不报错**

Run: `cd server && python init_db.py`
Expected: 无报错退出；`CREATE TABLE IF NOT EXISTS` 对已初始化的库是幂等的。

- [ ] **Step 3: 写建表验证测试**

新建 `server/tests/test_subtask_repo.py`：

```python
"""server/utils/subtask_repo.py 的测试（真实 DB）。"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from db import get_db  # noqa: E402


def test_tables_exist_with_expected_columns():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'ai_chat_subtasks' ORDER BY column_name
            """)
            cols = {r[0] for r in cur.fetchall()}
    assert cols == {'id', 'root_session_id', 'parent_subtask_id', 'parent_part_id',
                    'agent', 'prompt', 'description', 'status', 'error_message',
                    'created_at', 'completed_at'}


def test_subtask_messages_table_has_seq_bigserial():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT column_name, data_type FROM information_schema.columns
                WHERE table_name = 'ai_chat_subtask_messages' AND column_name = 'seq'
            """)
            row = cur.fetchone()
    assert row is not None
    assert row[1] == 'bigint'   # BIGSERIAL 底层类型


def test_deleting_root_session_cascades_to_subtasks_and_messages():
    """外键 ON DELETE CASCADE：删顶层会话要连带清掉子代理数据，不留孤儿。"""
    uid = 'u-subtask-' + uuid.uuid4().hex[:8]
    sid = 's-subtask-' + uuid.uuid4().hex[:8]
    subid = 'ses_subtask_' + uuid.uuid4().hex[:12]
    msg_id = 'm-subtask-' + uuid.uuid4().hex[:8]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s,%s,'x',%s,'developer')", (uid, uid, uid))
            cur.execute("INSERT INTO ai_chat_sessions (id, user_id, workspace_path, "
                        "session_token, token_expires_at) VALUES "
                        "(%s,%s,'/tmp/x',%s, now() + interval '1 day')",
                        (sid, uid, 'tok-' + uuid.uuid4().hex))
            cur.execute("INSERT INTO ai_chat_subtasks (id, root_session_id, agent, description) "
                        "VALUES (%s,%s,'build','测试子代理')", (subid, sid))
            cur.execute("INSERT INTO ai_chat_subtask_messages (id, subtask_id, role, content) "
                        "VALUES (%s,%s,'assistant','[]'::jsonb)", (msg_id, subid))
        conn.commit()

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
        conn.commit()

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ai_chat_subtasks WHERE id = %s", (subid,))
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM ai_chat_subtask_messages WHERE id = %s", (msg_id,))
            assert cur.fetchone()[0] == 0
            cur.execute("DELETE FROM users WHERE id = %s", (uid,))
        conn.commit()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_subtask_repo.py -v`
Expected: 3 passed

- [ ] **Step 5: 加入备份/还原清单**

在 `server/utils/backup.py` 的 `BACKUP_TABLES` 列表里，`('ai_chat_messages', [...], {3, 4}, 'AI消息'),` 这一行之后追加：

```python
    # 子代理（subtask/subagent 委托）执行轨迹。id 就是 OpenCode 自己的子会话
    # sessionID，还原时原样写回即可，不需要额外的 id 映射。
    ('ai_chat_subtasks', ['id', 'root_session_id', 'parent_subtask_id', 'parent_part_id',
                          'agent', 'prompt', 'description', 'status', 'error_message',
                          'created_at', 'completed_at'], set(), 'AI子代理'),
    ('ai_chat_subtask_messages', ['id', 'subtask_id', 'role', 'content', 'meta',
                                  'created_at', 'seq'], {3, 4}, 'AI子代理消息'),
```

在 `RESTORE_ORDER` 列表里，`'ai_chat_sessions',` 那一行之后（Level 4）追加 `'ai_chat_subtasks',`，`'ai_chat_messages',`（Level 5）之后追加 `'ai_chat_subtask_messages',`：

```python
    'ai_chat_sessions',             # Depends on users, ai_chat_batches
    'ai_chat_subtasks',             # Depends on ai_chat_sessions
    # Level 5
    'ai_chat_messages',             # Depends on ai_chat_sessions
    'ai_chat_subtask_messages',     # Depends on ai_chat_subtasks
```

在 `restore_backup` 函数里，紧跟着现有的
```python
if 'ai_chat_messages' in tables_to_restore:
    cur.execute(
        "SELECT setval(pg_get_serial_sequence('ai_chat_messages', 'seq'), "
        "  COALESCE((SELECT MAX(seq) FROM ai_chat_messages), 1))"
    )
```
这一段之后，追加对称的一段：

```python
        # 同样的道理：ai_chat_subtask_messages.seq 也是真正的 Postgres 序列，
        # 还原后必须重播种，否则新插入的子代理消息会拿到 <= 已还原数据里最大
        # seq 的值，subtask_repo 靠 seq 定序的前提就被还原操作本身破坏了。
        if 'ai_chat_subtask_messages' in tables_to_restore:
            cur.execute(
                "SELECT setval(pg_get_serial_sequence('ai_chat_subtask_messages', 'seq'), "
                "  COALESCE((SELECT MAX(seq) FROM ai_chat_subtask_messages), 1))"
            )
```

- [ ] **Step 6: 写备份/还原 round-trip 测试**

在 `server/tests/test_subtask_repo.py` 末尾追加：

```python
def test_subtask_tables_are_in_backup_table_map():
    """本仓库已经因为"忘了把新表加进备份清单"栽过三次（row_actions、seq、meta）。
    这条测试把它钉死——新表必须出现在 BACKUP_TABLE_MAP 与 RESTORE_ORDER 里。"""
    from utils.backup import BACKUP_TABLE_MAP, RESTORE_ORDER
    assert 'ai_chat_subtasks' in BACKUP_TABLE_MAP
    assert 'ai_chat_subtask_messages' in BACKUP_TABLE_MAP
    assert 'ai_chat_subtasks' in RESTORE_ORDER
    assert 'ai_chat_subtask_messages' in RESTORE_ORDER
    # 依赖顺序：subtasks 早于 subtask_messages，且都晚于 ai_chat_sessions
    assert (RESTORE_ORDER.index('ai_chat_sessions')
            < RESTORE_ORDER.index('ai_chat_subtasks')
            < RESTORE_ORDER.index('ai_chat_subtask_messages'))
```

- [ ] **Step 7: 跑测试确认通过**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_subtask_repo.py -v`
Expected: 4 passed

- [ ] **Step 8: 更新 conftest 的模块健复名单**

`server/tests/conftest.py` 的 `_rebind_module_get_db_to_real` fixture 里，把

```python
    for mod_name in ('utils.prompt_template', 'utils.batch_repo',
                     'utils.batch_engine'):
```

改成

```python
    for mod_name in ('utils.prompt_template', 'utils.batch_repo',
                     'utils.batch_engine', 'utils.subtask_repo'):
```

（`utils/subtask_repo.py` 还没建，这一步先改好，Task 3 建那个文件后自然生效；先改的原因是这条改动容易在后面被忘掉——这个 fixture 存在的全部意义就是防止"新模块的 get_db 绑定被别的测试污染"，新模块必须从一开始就在清单里。）

- [ ] **Step 9: 提交**

```bash
git add server/init_db.py server/utils/backup.py server/tests/conftest.py server/tests/test_subtask_repo.py
git commit -m "feat(subtask): 新增子代理执行轨迹的数据库表 + 备份/还原清单"
```

---

## Task 2: 共享的 part 映射模块

**Files:**
- Create: `server/utils/opencode_parts.py`
- Modify: `server/utils/batch_engine.py`（`_content_from_parts` 改为委托；`_TurnFailed` 改为委托 `format_opencode_error`）
- Modify: `server/utils/chat_persist.py`（text/tool 分支改为委托，为 subtask 铺路——本任务只做映射函数，不做子代理发现/追踪逻辑，那是 Task 4）
- Test: `server/tests/test_opencode_parts.py`（新建）

**Interfaces:**
- Produces:
  ```python
  # server/utils/opencode_parts.py
  def map_part(part: dict, *, subtask_status: dict[str, str] | None = None) -> dict | None
      # part.type in ('text','tool','subtask') -> 对应的存库内容 dict；
      # 其余类型（或 subtask 缺 sessionID）-> None。
      # 不过滤空文本——由调用方在 flatten 阶段各自处理（与两处既有实现的
      # 既有分工一致，见下方"行为不变"约束）。
  def format_opencode_error(error: dict) -> str
      # 把 AssistantMessage.error 格式化成人类可读的一句话
  ```
- Consumes（本任务内部）: `utils.ai_message_meta.tool_duration_ms`

**行为不变约束**：`_content_from_parts`（batch_engine）与 `apply_event` 的 text/tool 分支（chat_persist）的**外部可观察行为**——尤其是空文本过滤发生在哪一步、tool 分支的字段集合——必须与改动前逐字节一致。验证方式是本任务改完后跑 `test_batch_engine.py` 和 `test_chat_persist.py` 的既有用例全部原样通过，不改一行既有测试的断言。

- [ ] **Step 1: 写 `map_part`/`format_opencode_error` 的失败测试**

新建 `server/tests/test_opencode_parts.py`：

```python
"""server/utils/opencode_parts.py 的测试。

map_part 是从 chat_persist.py（apply_event 的 text/tool 分支）与
batch_engine.py（_content_from_parts）里抽出来的共享映射——这两处原来的实现
逻辑分别独立测试过，这里额外补一份直接测 map_part 本身，并且要能证明：把两处
调用点接到它之后，两边的既有测试不用改一行断言就能继续通过（那两个文件各自
的测试文件里不新增关于这个抽取的用例，靠它们原样通过来当证据）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.opencode_parts import map_part, format_opencode_error  # noqa: E402


def test_map_part_text_unconditional_no_empty_filter():
    """text 分支不过滤空文本——过滤留给调用方（见既有两处实现的分工）。"""
    assert map_part({'type': 'text', 'text': 'hi'}) == {'type': 'text', 'text': 'hi'}
    assert map_part({'type': 'text', 'text': ''}) == {'type': 'text', 'text': ''}
    assert map_part({'type': 'text'}) == {'type': 'text', 'text': ''}


def test_map_part_tool_maps_all_fields():
    part = {'type': 'tool', 'tool': 'bash',
            'state': {'status': 'completed', 'title': 't', 'input': {'cmd': 'ls'},
                      'output': 'ok', 'time': {'start': 1000, 'end': 1123}}}
    assert map_part(part) == {
        'type': 'tool_use', 'name': 'bash', 'title': 't', 'status': 'completed',
        'input': {'cmd': 'ls'}, 'result': 'ok', 'durationMs': 123,
    }


def test_map_part_tool_falls_back_to_state_result_when_output_absent():
    part = {'type': 'tool', 'tool': 'x', 'state': {'status': 'error', 'result': 'boom'}}
    mapped = map_part(part)
    assert mapped['result'] == 'boom'


def test_map_part_subtask_maps_agent_and_description():
    part = {'type': 'subtask', 'sessionID': 'ses_child1', 'agent': 'build',
            'description': '重构模块 X'}
    assert map_part(part) == {
        'type': 'subtask_use', 'subtaskId': 'ses_child1',
        'agent': 'build', 'description': '重构模块 X', 'status': 'running',
    }


def test_map_part_subtask_uses_status_lookup_when_provided():
    part = {'type': 'subtask', 'sessionID': 'ses_child1', 'agent': 'build',
            'description': 'x'}
    mapped = map_part(part, subtask_status={'ses_child1': 'failed'})
    assert mapped['status'] == 'failed'


def test_map_part_subtask_without_session_id_is_dropped():
    """畸形数据（缺 sessionID 的 subtask part）不应该产出一个没法拉取的占位。"""
    assert map_part({'type': 'subtask', 'agent': 'build'}) is None


def test_map_part_unknown_type_is_dropped():
    assert map_part({'type': 'reasoning', 'text': 'thinking...'}) is None


def test_format_opencode_error_with_provider_and_message():
    err = {'name': 'ProviderAuthError', 'data': {'providerID': 'anthropic',
                                                  'message': 'API key not found'}}
    msg = format_opencode_error(err)
    assert 'ProviderAuthError' in msg
    assert 'anthropic' in msg
    assert 'API key not found' in msg


def test_format_opencode_error_without_detail():
    msg = format_opencode_error({'name': 'UnknownError', 'data': {}})
    assert 'UnknownError' in msg


def test_format_opencode_error_handles_none():
    msg = format_opencode_error(None)
    assert 'UnknownError' in msg
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_opencode_parts.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'utils.opencode_parts'`

- [ ] **Step 3: 实现 `server/utils/opencode_parts.py`**

```python
"""共享的 OpenCode part → 存库内容映射，供实时聊天（chat_persist.py）与批任务
（batch_engine.py）两条持久化路径复用，避免同一套映射逻辑维护两份。

`map_part` 对 text/tool 两种既有类型的行为，是从这两处原有实现里逐字搬来的
（不是重新设计）——调用方各自决定要不要过滤空文本，这里保持"不过滤"的中立
契约，与两处原有实现"各自在 flatten 阶段过滤"的既有分工一致。

subtask 是本次新增的类型：OpenCode 委托子代理时产出的 SubtaskPart，携带子代理
自己的 OpenCode sessionID——它的完整对话轨迹在别处（ai_chat_subtasks /
ai_chat_subtask_messages）单独持久化，这里只映射成一个轻量占位（前端据此惰性
拉取），`status` 由调用方通过 `subtask_status` 传入当前已知状态（map_part 本身
不做数据库查询，保持纯函数）。
"""
from utils.ai_message_meta import tool_duration_ms


def map_part(part: dict, *, subtask_status: dict | None = None) -> dict | None:
    """把一个 OpenCode message part 映射成持久化内容的形状。

    返回 None 表示这个 part 不需要持久化（未知类型，或 subtask 缺 sessionID
    这种畸形数据——没有 sessionID 就没法在前端拉取，占位没有意义）。
    """
    t = part.get('type')
    if t == 'text':
        return {'type': 'text', 'text': part.get('text', '')}
    if t == 'tool':
        st = part.get('state') or {}
        return {
            'type': 'tool_use',
            'name': part.get('tool') or 'tool',
            'title': st.get('title') or '',
            'status': st.get('status'),
            'input': st.get('input'),
            'result': st.get('output') if st.get('output') is not None else st.get('result'),
            'durationMs': tool_duration_ms(st),
        }
    if t == 'subtask':
        sid = part.get('sessionID')
        if not sid:
            return None
        return {
            'type': 'subtask_use',
            'subtaskId': sid,
            'agent': part.get('agent'),
            'description': part.get('description'),
            'status': (subtask_status or {}).get(sid, 'running'),
        }
    return None


# OpenCode 的 AssistantMessage.error 是七选一：ProviderAuthError / UnknownError /
# MessageOutputLengthError / MessageAbortedError / StructuredOutputError /
# ContextOverflowError / APIError，形状均为 {name, data: {providerID?, message?}}。
def format_opencode_error(error: dict | None) -> str:
    """把 error 格式化成人类可读的一句话。batch_engine._TurnFailed 与子代理的
    错误状态判定共用这一份，不各自拼一遍。"""
    error = error or {}
    name = error.get('name') or 'UnknownError'
    data = error.get('data') or {}
    detail = data.get('message') or ''
    provider = data.get('providerID') or ''
    head = f'OpenCode 报告本轮失败: {name}'
    if provider:
        head += f'（provider={provider}）'
    return f'{head}: {detail}' if detail else head
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_opencode_parts.py -v`
Expected: 10 passed

- [ ] **Step 5: 把 `batch_engine.py` 的 `_content_from_parts` 改为委托，`_TurnFailed` 改为委托 `format_opencode_error`**

把 `server/utils/batch_engine.py` 里现在的：

```python
    @staticmethod
    def _content_from_parts(parts) -> list:
        """Map one OpenCode message's parts to persisted typed content: text +
        tool_use (matches interactive build_content + the AiContentPart schema).
        Drops reasoning/step markers."""
        out = []
        for p in (parts or []):
            t = p.get('type')
            if t == 'text':
                if (p.get('text') or '').strip():
                    out.append({'type': 'text', 'text': p['text']})
            elif t == 'tool':
                st = p.get('state') or {}
                out.append({
                    'type': 'tool_use',
                    'name': p.get('tool') or 'tool',
                    'title': st.get('title') or '',
                    'status': st.get('status'),
                    'input': st.get('input'),
                    'result': st.get('output'),
                    'durationMs': tool_duration_ms(st),
                })
        return out
```

改成：

```python
    @staticmethod
    def _content_from_parts(parts, subtask_status: dict | None = None) -> list:
        """Map one OpenCode message's parts to persisted typed content: text +
        tool_use + subtask_use (matches interactive build_content + the
        AiContentPart schema). Drops reasoning/step markers. 委托给
        utils.opencode_parts.map_part（chat_persist.py 共用同一份映射）；空文本
        的过滤保留在这里——这是原有实现的分工，map_part 本身不过滤。"""
        from utils.opencode_parts import map_part
        out = []
        for p in (parts or []):
            mapped = map_part(p, subtask_status=subtask_status)
            if mapped is None:
                continue
            if mapped['type'] == 'text' and not mapped['text'].strip():
                continue
            out.append(mapped)
        return out
```

**注意**：原实现里 tool 分支的 `result` 字段是 `st.get('output')`（不带 `output is not None else state.get('result')` 的回落）——`map_part` 的 tool 映射带了这个回落（那是抽取自 `chat_persist.py` 的既有行为，两处原本就不完全一致）。这里必须确认这个差异不会导致 `_content_from_parts` 的既有测试断言变化：由于本文件目前没有直接测试 `_content_from_parts` 的既有测试用例（已用 `grep -rn "_content_from_parts" tests/` 确认过），这个差异不会让任何既有测试变红，但仍属于一次**可观察的行为变化**（原来 `output` 为 `None` 时 `result` 会是 `None`，现在会回落到 `state.result`）。这是刻意接受的：`chat_persist.py` 的既有行为（有回落）更完整，统一到它更合理，且没有测试锁死旧行为。跑完 Step 7 的全量回归确认无意外。

把 `_TurnFailed` 类改为委托：

```python
class _TurnFailed(Exception):
    """OpenCode 明确报告这一轮失败了（AssistantMessage.error）..."""  # docstring 保持不变

    def __init__(self, error: dict):
        from utils.opencode_parts import format_opencode_error
        self.error = error or {}
        super().__init__(format_opencode_error(self.error))
        self.name = self.error.get('name') or 'UnknownError'
```

（原来的 docstring 全文保留，只改 `__init__` 的实现体——错误信息的拼装逻辑现在委托给 `format_opencode_error`，两者产出的字符串格式完全一致，因为 `format_opencode_error` 就是把原来内联在这里的那几行原样搬过去的。）

- [ ] **Step 6: 把 `chat_persist.py` 的 text/tool 分支改为委托**

把 `server/utils/chat_persist.py::apply_event` 里现在的：

```python
    elif etype == 'message.part.updated':
        part = props.get('part') or {}
        pid = part.get('id')
        if pid and part.get('messageID') in state['assistant_msg_ids']:
            ptype = part.get('type')
            if ptype == 'text':
                if pid not in state['parts_by_id']:
                    state['part_order'].append(pid)
                state['parts_by_id'][pid] = {'type': 'text', 'text': part.get('text', '')}
                return 'changed'
            elif ptype == 'tool':
                if pid not in state['parts_by_id']:
                    state['part_order'].append(pid)
                st = part.get('state') or {}
                state['parts_by_id'][pid] = {
                    'type': 'tool_use',
                    'name': part.get('tool') or 'tool',
                    'title': st.get('title'),
                    'status': st.get('status'),
                    'input': st.get('input'),
                    'result': st.get('output') if st.get('output') is not None else st.get('result'),
                    'durationMs': tool_duration_ms(st),
                }
                return 'changed'
```

改成：

```python
    elif etype == 'message.part.updated':
        part = props.get('part') or {}
        pid = part.get('id')
        if pid and part.get('messageID') in state['assistant_msg_ids']:
            ptype = part.get('type')
            if ptype in ('text', 'tool'):
                from utils.opencode_parts import map_part
                mapped = map_part(part)
                if pid not in state['parts_by_id']:
                    state['part_order'].append(pid)
                state['parts_by_id'][pid] = mapped
                return 'changed'
```

（`subtask` 类型不在这个 `ptype in (...)` 元组里——本任务只做行为不变的搬迁，子代理的追踪逻辑是 Task 4 的事，Task 4 会扩展这里的判断。`map_part` 的 tool 输出用了 `'title': st.get('title')`（原代码是 `st.get('title')`，不带 `or ''`）——这与 `map_part` 里 `'title': st.get('title') or ''` 不完全一致！**这个差异必须处理**：见下一步。）

**处理 title 字段的 `or ''` 差异**：`chat_persist.py` 原来的 tool 分支是 `'title': st.get('title')`（可能是 `None`），`batch_engine.py` 原来的 `_content_from_parts` 是 `'title': st.get('title') or ''`（`map_part` 继承了这个）。两处原本就不一致，抽取成一份必须二选一。选 `map_part` 现在的 `or ''`（batch_engine 的版本）——检查 `test_apply_event_captures_meta_and_tool_duration`（Step 7 会跑）用的测试数据里 tool part 没有传 `title`，断言里也没有读 `tool['title']`，所以这个选择不会让既有测试变红。跑 Step 7 确认。

- [ ] **Step 7: 跑两个文件的既有测试，确认行为不变**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_chat_persist.py tests/test_batch_engine.py tests/test_chat_persist_logging.py -v`
Expected: 全部 PASS（这是"抽取行为不变"的证据——一个既有断言都不应该改）

- [ ] **Step 8: 跑后端全量确认无回归**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest -q`
Expected: 全部通过

- [ ] **Step 9: 提交**

```bash
git add server/utils/opencode_parts.py server/utils/batch_engine.py server/utils/chat_persist.py server/tests/test_opencode_parts.py
git commit -m "refactor(subtask): 抽取共享的 OpenCode part 映射模块，为子代理铺路"
```

---

## Task 3: 仓储函数 + 两个只读端点

**Files:**
- Create: `server/utils/subtask_repo.py`
- Modify: `server/routes/ai_chat.py`
- Modify: `server/routes/ai_batch_admin.py`
- Test: `server/tests/test_ai_chat_subtasks_routes.py`（新建，实时聊天端点）
- Test: 追加到 `server/tests/test_ai_batch_admin_routes.py`（管理员端点）

**Interfaces:**
- Consumes: Task 1 的两张表。
- Produces:
  ```python
  # server/utils/subtask_repo.py
  MAX_SUBTASK_MESSAGES = 500

  get_subtask_messages(subtask_id: str, *, owner_user_id: str | None = None,
                       limit: int = MAX_SUBTASK_MESSAGES) -> dict | None
      # -> {'subtask': {...}, 'messages': [...], 'truncated': bool, 'total': int}
      # owner_user_id 非 None 时额外校验该子代理归属的顶层会话属于该用户
      # （实时聊天走这条）；为 None 时不做归属校验，调用方必须已经鉴权
      # （管理员端点）。查不到 / 不满足归属校验 -> None。
  ```

- [ ] **Step 1: 写失败测试（仓储函数）**

新建 `server/tests/test_subtask_repo.py` 的追加（Task 1 已建这个文件，这里追加内容）：

```python
def _seed_subtask_with_messages(owner_uid, other_uid, tied_created_at=True):
    """造一个属于 owner_uid 的顶层会话 + 一个子代理 + 若干消息。返回
    (root_session_id, subtask_id, message_ids_in_seq_order)。tied_created_at=True
    时全部消息 created_at 相同（模拟批量持久化），用来验证排序走 seq。"""
    import json
    sid = 's-subtask-' + uuid.uuid4().hex[:8]
    subid = 'ses_subtask_' + uuid.uuid4().hex[:12]
    with get_db() as conn:
        with conn.cursor() as cur:
            for uid in (owner_uid, other_uid):
                cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                            "VALUES (%s,%s,'x',%s,'developer') ON CONFLICT (id) DO NOTHING",
                            (uid, uid, uid))
            cur.execute("INSERT INTO ai_chat_sessions (id, user_id, workspace_path, "
                        "session_token, token_expires_at) VALUES "
                        "(%s,%s,'/tmp/x',%s, now() + interval '1 day')",
                        (sid, owner_uid, 'tok-' + uuid.uuid4().hex))
            cur.execute("INSERT INTO ai_chat_subtasks (id, root_session_id, agent, description) "
                        "VALUES (%s,%s,'build','测试子代理')", (subid, sid))
            mids = []
            for i, text in enumerate(('第三条', '第一条', '第二条')):
                mid = f'm-{subid}-{i}'
                mids.append(mid)
                if tied_created_at:
                    cur.execute(
                        "INSERT INTO ai_chat_subtask_messages (id, subtask_id, role, content, created_at) "
                        "VALUES (%s,%s,'assistant',%s::jsonb, TIMESTAMPTZ '2026-01-01 00:00:00+08')",
                        (mid, subid, json.dumps([{'type': 'text', 'text': text}])))
                else:
                    cur.execute(
                        "INSERT INTO ai_chat_subtask_messages (id, subtask_id, role, content) "
                        "VALUES (%s,%s,'assistant',%s::jsonb)",
                        (mid, subid, json.dumps([{'type': 'text', 'text': text}])))
        conn.commit()
    return sid, subid, mids


def _cleanup_subtask_seed(owner_uid, other_uid, sid):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
            cur.execute("DELETE FROM users WHERE id IN (%s,%s)", (owner_uid, other_uid))
        conn.commit()


def test_get_subtask_messages_ordered_by_seq_not_created_at():
    """三条消息 created_at 全同（模拟子代理批量持久化），必须按 seq（插入顺序）
    返回，而不是 created_at 的不确定 tie-break——这个坑在本仓库已经出现过三次
    以上（批任务消息、管理员子任务消息），这次从一开始就用对的。"""
    from utils.subtask_repo import get_subtask_messages
    owner, other = 'u-sub-a-' + uuid.uuid4().hex[:6], 'u-sub-b-' + uuid.uuid4().hex[:6]
    sid, subid, _ = _seed_subtask_with_messages(owner, other, tied_created_at=True)
    try:
        res = get_subtask_messages(subid)
        texts = [p['text'] for m in res['messages'] for p in m['content']
                if p.get('type') == 'text']
        assert texts == ['第三条', '第一条', '第二条']   # 插入顺序 = seq 顺序
    finally:
        _cleanup_subtask_seed(owner, other, sid)


def test_get_subtask_messages_truncates_to_limit():
    from utils.subtask_repo import get_subtask_messages
    owner, other = 'u-sub-a-' + uuid.uuid4().hex[:6], 'u-sub-b-' + uuid.uuid4().hex[:6]
    sid, subid, _ = _seed_subtask_with_messages(owner, other)
    try:
        res = get_subtask_messages(subid, limit=2)
        assert res['truncated'] is True
        assert res['total'] == 3
        assert len(res['messages']) == 2
    finally:
        _cleanup_subtask_seed(owner, other, sid)


def test_get_subtask_messages_owner_scoped_rejects_wrong_user():
    """实时聊天路径必须校验归属；传错 owner_user_id 一律 None（404），不泄漏存在性。"""
    from utils.subtask_repo import get_subtask_messages
    owner, other = 'u-sub-a-' + uuid.uuid4().hex[:6], 'u-sub-b-' + uuid.uuid4().hex[:6]
    sid, subid, _ = _seed_subtask_with_messages(owner, other)
    try:
        assert get_subtask_messages(subid, owner_user_id=other) is None
        assert get_subtask_messages(subid, owner_user_id=owner) is not None
    finally:
        _cleanup_subtask_seed(owner, other, sid)


def test_get_subtask_messages_unscoped_for_admin_path():
    """owner_user_id=None（管理员路径）不做归属校验——鉴权已经在路由层的
    require_permission('admin.ai_chat_admin') 做过了。"""
    from utils.subtask_repo import get_subtask_messages
    owner, other = 'u-sub-a-' + uuid.uuid4().hex[:6], 'u-sub-b-' + uuid.uuid4().hex[:6]
    sid, subid, _ = _seed_subtask_with_messages(owner, other)
    try:
        assert get_subtask_messages(subid) is not None
    finally:
        _cleanup_subtask_seed(owner, other, sid)


def test_get_subtask_messages_missing_subtask_is_none():
    from utils.subtask_repo import get_subtask_messages
    assert get_subtask_messages('ses_does_not_exist') is None


def test_get_subtask_messages_returns_subtask_summary():
    from utils.subtask_repo import get_subtask_messages
    owner, other = 'u-sub-a-' + uuid.uuid4().hex[:6], 'u-sub-b-' + uuid.uuid4().hex[:6]
    sid, subid, _ = _seed_subtask_with_messages(owner, other)
    try:
        res = get_subtask_messages(subid)
        assert res['subtask']['id'] == subid
        assert res['subtask']['agent'] == 'build'
        assert res['subtask']['description'] == '测试子代理'
        assert res['subtask']['status'] == 'running'
    finally:
        _cleanup_subtask_seed(owner, other, sid)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_subtask_repo.py -v -k get_subtask_messages`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 实现 `server/utils/subtask_repo.py`**

```python
"""子代理（subtask/subagent 委托）数据的查询函数。

供两条持久化路径（chat_persist.py / batch_engine.py，见 Task 4/5）与两个只读
端点（routes/ai_chat.py 的所有权校验版本、routes/ai_batch_admin.py 的管理员
版本）共用——跟 utils/batch_repo.py 里 `api_key_id: str | None = None` 那个既有
模式一致：一个可选的作用域参数决定是否额外校验归属，而不是写成两个互相独立、
容易漏掉某一层校验的函数。
"""
from psycopg2.extras import RealDictCursor

from db import get_db

MAX_SUBTASK_MESSAGES = 500


def get_subtask_messages(subtask_id: str, *, owner_user_id: str | None = None,
                         limit: int = MAX_SUBTASK_MESSAGES) -> dict | None:
    """某个子代理的完整对话（只读）。

    `owner_user_id` 非 None 时，额外校验该子代理归属的顶层会话（沿 root_session_id
    一路追溯，不管嵌套多深）属于该用户——实时聊天端点这样调用。为 None 时不做
    归属校验，调用方必须已经用别的方式鉴权（管理员端点靠 require_permission）。

    按 `seq` 排序（不是 `created_at`）：子代理消息由持久化路径批量写入，同事务
    内 created_at 可能相同，seq 才是可靠的顺序来源。取最近 `limit` 条后反转为
    升序，调用方拿到的始终是自然阅读顺序。
    """
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            sql = "SELECT * FROM ai_chat_subtasks WHERE id = %s"
            params = [subtask_id]
            if owner_user_id is not None:
                sql += (" AND root_session_id IN "
                       "(SELECT id FROM ai_chat_sessions WHERE user_id = %s)")
                params.append(owner_user_id)
            cur.execute(sql, tuple(params))
            subtask = cur.fetchone()
            if not subtask:
                return None

            cur.execute("SELECT count(*) AS n FROM ai_chat_subtask_messages "
                       "WHERE subtask_id = %s", (subtask_id,))
            total = cur.fetchone()['n']
            cur.execute(
                "SELECT id, role, content, created_at, meta FROM ai_chat_subtask_messages "
                " WHERE subtask_id = %s ORDER BY seq DESC LIMIT %s",
                (subtask_id, limit))
            rows = [dict(r) for r in cur.fetchall()]
    rows.reverse()
    return {'subtask': dict(subtask), 'messages': rows,
            'truncated': total > len(rows), 'total': total}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_subtask_repo.py -v`
Expected: 10 passed（Task 1 的 4 条 + 本任务的 6 条）

- [ ] **Step 5: 变异验证排序测试确有鉴别力**

把 `get_subtask_messages` 里的 `ORDER BY seq DESC` 临时改成 `ORDER BY created_at DESC`，重跑 `-k ordered_by_seq`，**这条测试因为 `tied_created_at=True`（三条消息 created_at 全同）预期会保持不确定，可能仍然"恰好"通过或者失败，取决于 Postgres 的执行计划**——这正是本仓库在批任务那条分支上踩过的坑（tie-break 靠执行计划巧合）。要让变异**必然**变红，改一下这条测试的夹具，让 `created_at` 与 `seq` 故意反序（比照 Task 1（批任务管理页）分支里 `batch_with_seq_created_at_reversed` 那个夹具的做法）：

在 `_seed_subtask_with_messages` 里，把 `tied_created_at=True` 分支的每条 INSERT 加一个**与插入顺序相反**的 `created_at`（第一条插入的给最新时间戳，最后一条给最老时间戳），断言改为验证"按 seq 是插入顺序，按 created_at 会是反序"，这样改错排序列 100% 变红，不依赖执行计划。跑变异确认，记录红/绿原文，改回。

- [ ] **Step 6: 提交仓储函数**

```bash
git add server/utils/subtask_repo.py server/tests/test_subtask_repo.py
git commit -m "feat(subtask): 子代理消息查询函数（所有权作用域 + 管理员作用域共用）"
```

- [ ] **Step 7: 写实时聊天端点的失败测试**

新建 `server/tests/test_ai_chat_subtasks_routes.py`：

```python
"""GET /ai/chat/sessions/:sid/subtasks/:subtaskId/messages 的测试。"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = '/ai/chat/sessions'


def test_subtask_messages_requires_login(client):
    resp = client.get(f'{BASE}/s-1/subtasks/ses_x/messages')
    assert resp.status_code == 401


def test_subtask_messages_not_found_is_404(client, dev_headers):
    with patch('routes.ai_chat.get_subtask_messages', return_value=None):
        resp = client.get(f'{BASE}/s-1/subtasks/ses_x/messages', headers=dev_headers)
    assert resp.status_code == 404


def test_subtask_messages_passes_owner_scope(client, dev_headers):
    """必须带当前登录用户的 id 做归属校验，不能不传（那样就是不做校验的
    管理员路径了）。"""
    payload = {'subtask': {'id': 'ses_x', 'agent': 'build', 'description': 'd',
                           'status': 'running'},
              'messages': [], 'truncated': False, 'total': 0}
    with patch('routes.ai_chat.get_subtask_messages', return_value=payload) as gm:
        resp = client.get(f'{BASE}/s-1/subtasks/ses_x/messages', headers=dev_headers)
    assert resp.status_code == 200
    assert gm.call_args[0][0] == 'ses_x'
    assert gm.call_args[1]['owner_user_id'] == 'user-dev'


def test_subtask_messages_returns_contract_fields(client, dev_headers):
    payload = {'subtask': {'id': 'ses_x', 'agent': 'build', 'description': 'd',
                           'status': 'failed', 'error_message': 'boom'},
              'messages': [{'id': 'm1', 'role': 'assistant', 'content': [],
                           'created_at': None, 'meta': None}],
              'truncated': True, 'total': 900}
    with patch('routes.ai_chat.get_subtask_messages', return_value=payload):
        resp = client.get(f'{BASE}/s-1/subtasks/ses_x/messages', headers=dev_headers)
    body = resp.get_json()
    assert body['truncated'] is True and body['total'] == 900
    assert body['subtask']['status'] == 'failed'
    assert body['subtask']['error'] == 'boom'
    assert len(body['messages']) == 1
```

- [ ] **Step 8: 跑测试确认失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_chat_subtasks_routes.py -v`
Expected: FAIL，404（路由不存在）

- [ ] **Step 9: 实现实时聊天端点**

在 `server/routes/ai_chat.py` 顶部导入区加一行：

```python
from utils.subtask_repo import get_subtask_messages
```

在文件里找到既有的 `GET /sessions/<sid>/messages`（`def get_messages(sid):`）端点定义之后，追加：

```python
@ai_chat_bp.route('/sessions/<sid>/subtasks/<subtask_id>/messages', methods=['GET'])
@login_required
def get_subtask_messages_route(sid, subtask_id):
    """子代理的完整对话，只读。归属校验靠子代理自己的 root_session_id 一路
    追溯到当前用户，不需要额外确认 sid 就是那个 root（subtaskId 本身已经
    是全局唯一的 OpenCode sessionID，subtask_repo 已经做了归属判断）。"""
    user = flask_g.current_user
    data = get_subtask_messages(subtask_id, owner_user_id=user['userId'])
    if data is None:
        return jsonify({'error': 'subtask not found', 'code': 'SUBTASK_NOT_FOUND'}), 404
    st = data['subtask']
    return jsonify({
        'subtask': {
            'id': st['id'], 'agent': st.get('agent'), 'description': st.get('description'),
            'status': st['status'], 'error': st.get('error_message'),
        },
        'messages': [
            {'id': m['id'], 'role': m['role'], 'content': m['content'],
             'createdAt': m['created_at'].isoformat() if m.get('created_at') else None,
             'meta': m.get('meta')}
            for m in data['messages']
        ],
        'truncated': data['truncated'],
        'total': data['total'],
    })
```

- [ ] **Step 10: 跑测试确认通过**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_chat_subtasks_routes.py -v`
Expected: 4 passed

- [ ] **Step 11: 写管理员端点的失败测试**

在 `server/tests/test_ai_batch_admin_routes.py` 末尾追加：

```python
# --- 子代理对话（管理员，只读） -----------------------------------------------

def test_admin_subtask_messages_requires_admin(client, dev_headers):
    resp = client.get(f'{BASE}/b-1/sessions/s-1/subtasks/ses_x/messages',
                      headers=dev_headers)
    assert resp.status_code == 403


def test_admin_subtask_messages_not_found_is_404(client, admin_headers):
    with patch('routes.ai_batch_admin.get_subtask_messages', return_value=None):
        resp = client.get(f'{BASE}/b-1/sessions/s-1/subtasks/ses_x/messages',
                          headers=admin_headers)
    assert resp.status_code == 404


def test_admin_subtask_messages_does_not_scope_by_owner(client, admin_headers):
    """管理员路径不传 owner_user_id——鉴权已经在 require_permission 做过，
    子代理数据不按归属过滤（这正是管理员视角存在的意义）。"""
    payload = {'subtask': {'id': 'ses_x', 'agent': 'build', 'description': 'd',
                           'status': 'running'},
              'messages': [], 'truncated': False, 'total': 0}
    with patch('routes.ai_batch_admin.get_subtask_messages', return_value=payload) as gm:
        resp = client.get(f'{BASE}/b-1/sessions/s-1/subtasks/ses_x/messages',
                          headers=admin_headers)
    assert resp.status_code == 200
    assert 'owner_user_id' not in gm.call_args[1]
```

- [ ] **Step 12: 跑测试确认失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_batch_admin_routes.py -v -k admin_subtask`
Expected: FAIL

- [ ] **Step 13: 实现管理员端点**

在 `server/routes/ai_batch_admin.py` 顶部导入区加一行：

```python
from utils.subtask_repo import get_subtask_messages
```

文件末尾追加：

```python
@ai_batch_admin_bp.get('/<batch_id>/sessions/<sid>/subtasks/<subtask_id>/messages')
@require_permission('admin.ai_chat_admin')
def child_subtask_messages(batch_id, sid, subtask_id):
    """子代理的完整对话（管理员视角，只读）。不传 owner_user_id——鉴权已经
    在 require_permission 做过，跨用户可见正是管理员视角存在的意义。"""
    data = get_subtask_messages(subtask_id)
    if data is None:
        return jsonify({'error': '子代理不存在'}), 404
    st = data['subtask']
    return jsonify({
        'subtask': {
            'id': st['id'], 'agent': st.get('agent'), 'description': st.get('description'),
            'status': st['status'], 'error': st.get('error_message'),
        },
        'messages': [
            {'id': m['id'], 'role': m['role'], 'content': m['content'],
             'createdAt': m['created_at'].isoformat() if m.get('created_at') else None,
             'meta': m.get('meta')}
            for m in data['messages']
        ],
        'truncated': data['truncated'],
        'total': data['total'],
    })
```

- [ ] **Step 14: 跑测试确认通过**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_batch_admin_routes.py -v`
Expected: 全部 PASS（含新增 3 条 + 遍历式权限门测试自动覆盖新端点）

- [ ] **Step 15: 跑后端全量确认无回归**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest -q`
Expected: 全部通过

- [ ] **Step 16: 提交**

```bash
git add server/routes/ai_chat.py server/routes/ai_batch_admin.py server/tests/test_ai_chat_subtasks_routes.py server/tests/test_ai_batch_admin_routes.py
git commit -m "feat(subtask): 实时聊天与管理员两条子代理对话只读端点"
```

---

## Task 4: 实时聊天集成（SSE 路径）

**Files:**
- Modify: `server/utils/chat_persist.py`
- Modify: `server/routes/ai_chat.py`（`clear_session` 补一步清子代理数据）
- Test: 追加到 `server/tests/test_chat_persist.py`

**Interfaces:**
- Consumes: Task 2 的 `map_part`/`format_opencode_error`；Task 1 的两张表。
- Produces: `apply_event` 新增返回值 `'subtask'`（既有 `'idle'`/`'changed'`/`None` 语义不变）；`state['subtasks']` 结构。

**背景（为什么这样设计是安全的）**：委托子代理是父级的一次阻塞式工具调用，OpenCode 不会把父级那一轮标记为完成（`session.idle`）直到子代理跑完（`Event.session.idle` 的 schema 里 `sessionID` 是必填字段，每个 session 独立触发自己的 idle）——所以子代理的整个生命周期天然被包在**一次**父级 turn 的 `new_state()` 生命周期之内，把子代理追踪状态挂在同一个 `state` 字典里是安全的，不会出现 state 对象已经被丢弃但子代理还在跑的情况。

**核心设计要点（贯穿本任务）**：子代理占位 part（前端要渲染成 `SubtaskBubble` 的那个 `subtask_use` 条目）必须真实出现在**父级自己的**内容里——只在 `state['subtasks']` 里记录追踪状态是不够的，Task 6 的渲染完全依赖父级消息的 `content` 数组里有一条 `type: 'subtask_use'`。而且子代理的状态会变（running → completed/failed），父级里已经持久化过的占位不能定型不变。解决办法：**占位 part 存的是原始数据（`{'_subtask_part': part}`），不是已经映射好的 dict；"当前状态"在每次落库（flatten）时才从 `state['subtasks']` 现查现填**。这样任何一次持久化都会用最新状态刷新它引用到的每一层占位气泡，不需要"子代理状态变了、反过来找到是哪个父级引用了它、单独去更新那一条"这种复杂的反向追踪。

- [ ] **Step 1: 写失败测试**

在 `server/tests/test_chat_persist.py` 末尾追加：

```python
def test_apply_event_ignores_unrelated_session_still_works():
    """回归防护：新逻辑必须不影响"真正无关的会话被忽略"这个既有行为。"""
    from utils.chat_persist import new_state, apply_event
    s = new_state()
    apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'totally-unrelated'}}), 'oc')
    assert s['turn_msg_id'] is None
    assert s['subtasks'] == {}


def test_apply_event_subtask_part_appears_in_parent_content():
    """核心断言：父级消息里出现 subtask part 之后，父级自己 flatten 出来的
    content 里必须真的有一条 subtask_use——这是 Task 6 渲染唯一的数据来源，
    只在 state['subtasks'] 里记追踪状态而不放进父级 content 就是白做。"""
    from utils.chat_persist import new_state, apply_event, build_content
    s = new_state()
    apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc'}}), 'oc')
    sig = apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'p1', 'messageID': 'm1', 'type': 'subtask',
                  'sessionID': 'ses_child1', 'agent': 'build', 'description': 'x'}}), 'oc')
    assert sig == 'subtask'
    content = build_content(s)
    stubs = [p for p in content if p['type'] == 'subtask_use']
    assert stubs == [{'type': 'subtask_use', 'subtaskId': 'ses_child1',
                      'agent': 'build', 'description': 'x', 'status': 'running'}]


def test_apply_event_discovers_subtask_with_depth_and_parent():
    from utils.chat_persist import new_state, apply_event
    s = new_state()
    apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc'}}), 'oc')
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'p1', 'messageID': 'm1', 'type': 'subtask',
                  'sessionID': 'ses_child1', 'agent': 'build', 'description': 'x'}}), 'oc')
    assert s['subtasks']['ses_child1']['depth'] == 1
    assert s['subtasks']['ses_child1']['parent_id'] is None
    assert s['subtasks']['ses_child1']['status'] == 'running'


def test_apply_event_routes_child_events_into_subtask_scope():
    """子代理自己的 sessionID 上的事件，一旦它已被发现，要路由进它自己的
    累加器，而不是被当成"无关会话"丢弃。"""
    from utils.chat_persist import new_state, apply_event
    s = new_state()
    apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc'}}), 'oc')
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'p1', 'messageID': 'm1', 'type': 'subtask',
                  'sessionID': 'ses_child1', 'agent': 'build', 'description': 'x'}}), 'oc')
    apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'cm1', 'sessionID': 'ses_child1'}}), 'oc')
    sig = apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'cp1', 'messageID': 'cm1', 'type': 'text', 'text': 'working',
                  'sessionID': 'ses_child1'}}), 'oc')
    assert sig == 'subtask'
    assert s['subtasks']['ses_child1']['parts_by_id']['cp1'] == {'type': 'text', 'text': 'working'}


def test_apply_event_subtask_idle_marks_completed():
    from utils.chat_persist import new_state, apply_event
    s = new_state()
    apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc'}}), 'oc')
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'p1', 'messageID': 'm1', 'type': 'subtask',
                  'sessionID': 'ses_child1', 'agent': 'build', 'description': 'x'}}), 'oc')
    sig = apply_event(s, _ev('session.idle', {'sessionID': 'ses_child1'}), 'oc')
    assert sig == 'subtask'
    assert s['subtasks']['ses_child1']['status'] == 'completed'


def test_apply_event_parent_stub_reflects_child_status_after_it_finishes():
    """状态更新不能只停留在 state['subtasks']——父级自己再次 flatten 时，
    占位气泡里的 status 必须跟着变成最新值，而不是停在发现时刻的 'running'。
    这就是"占位存原始 part、状态现查现填"这个设计要解决的问题，用一条测试
    钉死它。"""
    from utils.chat_persist import new_state, apply_event, build_content
    s = new_state()
    apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc'}}), 'oc')
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'p1', 'messageID': 'm1', 'type': 'subtask',
                  'sessionID': 'ses_child1', 'agent': 'build', 'description': 'x'}}), 'oc')
    assert build_content(s)[0]['status'] == 'running'
    apply_event(s, _ev('session.idle', {'sessionID': 'ses_child1'}), 'oc')
    assert build_content(s)[0]['status'] == 'completed'


def test_apply_event_subtask_error_marks_failed_immediately():
    """子代理自己的轮次报错时立刻标 failed，不等 idle——透出原因，跟批任务
    那条 _TurnFailed 是同一个道理。"""
    from utils.chat_persist import new_state, apply_event
    s = new_state()
    apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc'}}), 'oc')
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'p1', 'messageID': 'm1', 'type': 'subtask',
                  'sessionID': 'ses_child1', 'agent': 'build', 'description': 'x'}}), 'oc')
    sig = apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'cm1', 'sessionID': 'ses_child1',
                  'error': {'name': 'ProviderAuthError',
                           'data': {'providerID': 'anthropic', 'message': 'no key'}}}}), 'oc')
    assert sig == 'subtask'
    assert s['subtasks']['ses_child1']['status'] == 'failed'
    assert 'ProviderAuthError' in s['subtasks']['ses_child1']['error']
    assert 'anthropic' in s['subtasks']['ses_child1']['error']


def test_apply_event_nested_subtask_depth_increments():
    """子代理自己委托孙代——depth 从子代理的 depth 累加，不是恒为 1；孙代的
    占位 part 出现在**子代理自己**的 content 里（不是顶层的）。"""
    from utils.chat_persist import new_state, apply_event, build_content
    s = new_state()
    apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc'}}), 'oc')
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'p1', 'messageID': 'm1', 'type': 'subtask',
                  'sessionID': 'ses_child1', 'agent': 'build', 'description': 'x'}}), 'oc')
    apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'cm1', 'sessionID': 'ses_child1'}}), 'oc')
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'cp1', 'messageID': 'cm1', 'type': 'subtask',
                  'sessionID': 'ses_grandchild', 'agent': 'review', 'description': 'y'}}), 'oc')
    assert s['subtasks']['ses_grandchild']['depth'] == 2
    assert s['subtasks']['ses_grandchild']['parent_id'] == 'ses_child1'
    # 顶层 content 里只有对 ses_child1 的占位，没有孙代
    top_stub_ids = [p['subtaskId'] for p in build_content(s) if p['type'] == 'subtask_use']
    assert top_stub_ids == ['ses_child1']


def test_apply_event_depth_limit_stops_at_five():
    """超过 5 层不再追踪新的子代理——用一条深度已经是 5 的子代理去委托第 6 层，
    第 6 层不应该出现在 state['subtasks'] 里。"""
    from utils.chat_persist import new_state, apply_event
    s = new_state()
    apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc'}}), 'oc')
    # 手工把一个 depth=5 的子代理塞进去（不必真的建 5 层，直接构造边界条件）
    s['subtasks']['ses_deep'] = {
        'assistant_msg_ids': {'dm1'}, 'parts_by_id': {}, 'part_order': [],
        'parent_id': None, 'depth': 5, 'agent': 'x', 'description': 'y',
        'status': 'running', 'error': None,
    }
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'dp1', 'messageID': 'dm1', 'type': 'subtask',
                  'sessionID': 'ses_too_deep', 'agent': 'z', 'description': 'w'}}), 'oc')
    assert 'ses_too_deep' not in s['subtasks']
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_chat_persist.py -v -k subtask`
Expected: FAIL（`s['subtasks']` 不存在等）

- [ ] **Step 3: 实现 `chat_persist.py` 的子代理追踪与状态现查现填**

把 `new_state()` 改成：

```python
def new_state():
    """Fresh per-turn accumulator. `subtasks` maps a child OpenCode sessionID
    to its own nested accumulator (see `_new_subtask_scope`), discovered
    recursively during this turn — see the subagent-trace-visibility design
    for why this is safe to scope per-turn: delegating to a subagent is a
    *blocking* tool call from the parent's perspective, so the parent's own
    session.idle cannot fire before every subtask under it has already
    idled/failed."""
    return {'assistant_msg_ids': set(), 'parts_by_id': {}, 'part_order': [],
            'turn_msg_id': None, 'meta_by_msg': {}, 'subtasks': {}}


def _new_subtask_scope(parent_id, depth, agent, description):
    return {'assistant_msg_ids': set(), 'parts_by_id': {}, 'part_order': [],
            'parent_id': parent_id, 'depth': depth, 'agent': agent,
            'description': description, 'status': 'running', 'error': None}


MAX_SUBTASK_DEPTH = 5
```

替换整个 `apply_event` 函数为：

```python
def apply_event(state, evt, opencode_session_id):
    """Consume one subscribe_events() item ({'event','data'}). Accumulate
    assistant text/tool parts into `state`. Returns 'idle' on session.idle,
    'changed' when an assistant text/tool part was (re)captured, 'subtask'
    when a subtask-scoped event was processed (see `state['subtasks']` for
    which one / what changed — this file's caller inspects the dict, the
    return value only signals "go look"), else None. Events for genuinely
    unrelated sessions are ignored — unchanged from before subtask support."""
    etype = evt.get('event', '')
    props = (evt.get('data') or {}).get('properties') or {}
    ev_sid = event_session_id(props)

    scope, is_subtask = _resolve_scope(state, ev_sid, opencode_session_id)
    if scope is None:
        return None

    if etype == 'message.updated':
        info = props.get('info') or {}
        if info.get('role') == 'assistant' and info.get('id'):
            scope['assistant_msg_ids'].add(info['id'])
            if not is_subtask and state['turn_msg_id'] is None:
                state['turn_msg_id'] = info['id']
            m = meta_from_info(info)
            if m:
                scope['meta_by_msg'] = scope.get('meta_by_msg') or {}
                scope['meta_by_msg'][info['id']] = m
            if is_subtask and info.get('error'):
                from utils.opencode_parts import format_opencode_error
                scope['status'] = 'failed'
                scope['error'] = format_opencode_error(info['error'])
                return 'subtask'
    elif etype == 'message.part.updated':
        part = props.get('part') or {}
        pid = part.get('id')
        if pid and part.get('messageID') in scope['assistant_msg_ids']:
            ptype = part.get('type')
            if ptype in ('text', 'tool'):
                from utils.opencode_parts import map_part
                mapped = map_part(part)
                if pid not in scope['parts_by_id']:
                    scope['part_order'].append(pid)
                scope['parts_by_id'][pid] = mapped
                return 'subtask' if is_subtask else 'changed'
            if ptype == 'subtask':
                # 占位存**原始 part**，不是已经映射好的 dict——状态在 flatten
                # 时（_flatten_scope）才现查现填，这样子代理跑完之后，父级
                # 下次 flatten 自然会拿到最新状态，不需要反向去更新这一条。
                if pid not in scope['parts_by_id']:
                    scope['part_order'].append(pid)
                scope['parts_by_id'][pid] = {'_subtask_part': part}
                _discover_subtask(state, scope, ev_sid, is_subtask, part)
                return 'subtask'
    elif etype == 'session.idle':
        if is_subtask:
            if scope['status'] == 'running':
                scope['status'] = 'completed'
            return 'subtask'
        return 'idle'
    return None


def _resolve_scope(state, ev_sid, opencode_session_id):
    """Which accumulator does this event belong to: the top-level `state`
    itself, a known subtask's nested scope, or neither (unrelated session,
    ignored — matches the pre-subtask behavior exactly)."""
    if not ev_sid or ev_sid == opencode_session_id:
        return state, False
    sub = state['subtasks'].get(ev_sid)
    if sub is not None:
        return sub, True
    return None, False


def _discover_subtask(state, current_scope, current_sid, current_is_subtask, part):
    """A `subtask` part was seen inside `current_scope`'s own messages —
    register its child sessionID for tracking, unless we're already at the
    depth cap. `current_scope`'s own depth (0 for the top-level) determines
    the new entry's depth."""
    sid = part.get('sessionID')
    if not sid or sid in state['subtasks']:
        return
    parent_depth = current_scope['depth'] if current_is_subtask else 0
    if parent_depth + 1 > MAX_SUBTASK_DEPTH:
        return
    parent_id = current_sid if current_is_subtask else None
    state['subtasks'][sid] = _new_subtask_scope(
        parent_id, parent_depth + 1, part.get('agent'), part.get('description'))


def _status_snapshot(state):
    """{子代理 sessionID: 当前状态}，供 flatten 时给 subtask_use 占位填最新值。"""
    return {sid: sub['status'] for sid, sub in state.get('subtasks', {}).items()}


def _flatten_scope(scope, subtask_status):
    """把一个累加器（顶层 state 本身，或某个子代理的嵌套 scope）的
    part_order/parts_by_id 展开成持久化内容。text 分支保留既有的"空文本
    过滤"（build_content 原有行为，见 Task 2 的行为不变约束）；subtask 分支
    每次都用当前的 `subtask_status` 重新调 map_part，状态不会定型不变。"""
    from utils.opencode_parts import map_part
    content = []
    for pid in scope['part_order']:
        p = scope['parts_by_id'].get(pid)
        if not p:
            continue
        if '_subtask_part' in p:
            mapped = map_part(p['_subtask_part'], subtask_status=subtask_status)
            if mapped:
                content.append(mapped)
            continue
        if p['type'] == 'text':
            if (p.get('text') or '').strip():
                content.append({'type': 'text', 'text': p['text']})
        elif p['type'] == 'tool_use':
            content.append(p)
    return content
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_chat_persist.py -v`
Expected: 全部 PASS（既有用例 + 本任务新增 9 条）——**注意**既有的 `build_content` 函数还没改，Step 4 此刻应该仍然全绿是因为新测试目前调的是新加的 `_flatten_scope`/`apply_event`，`build_content` 本身要到 Step 5 才改。如果 `test_apply_event_subtask_part_appears_in_parent_content` 等直接调用 `build_content` 的新测试此时失败（`build_content` 还不认识 `_subtask_part` 包装），属预期中的失败，继续往下做 Step 5 即可，不要回头怀疑 Step 3。

- [ ] **Step 5: 把 `build_content` 改为委托 `_flatten_scope`，实现 `persist_subtasks`，接入 `_run_listener`**

把现在的：

```python
def build_content(state):
    """Build the persisted assistant content (arrival order). Empty text parts
    dropped; tool_use parts kept so rendered results survive a reload."""
    content = []
    for pid in state['part_order']:
        p = state['parts_by_id'].get(pid)
        if not p:
            continue
        if p['type'] == 'text':
            if (p.get('text') or '').strip():
                content.append({'type': 'text', 'text': p['text']})
        elif p['type'] == 'tool_use':
            content.append(p)
    return content
```

改成：

```python
def build_content(state):
    """Build the persisted assistant content (arrival order). Delegates to
    _flatten_scope so the top-level's own subtask_use stubs (if any) get the
    same "status re-derived fresh at flatten time" treatment as everything
    else — see _flatten_scope's docstring."""
    return _flatten_scope(state, _status_snapshot(state))
```

在 `persist_turn` 函数之后追加：

```python
def persist_subtasks(root_session_id, state):
    """把 state['subtasks'] 里每一个已知子代理的当前内容落库：
    ai_chat_subtasks 一行（首次插入，之后只更新 status/error/completed_at），
    ai_chat_subtask_messages 一行持续更新的 assistant 消息（跟 persist_turn
    对顶层的处理方式一致——单行按 id 幂等 upsert，不是每次都插新行）。
    子代理自己的 content 里如果又有更深一层的占位，同样交给 _flatten_scope
    用当前状态现查现填。Best-effort；不抛异常，不打断调用方的事件循环。"""
    snapshot = _status_snapshot(state)
    for sid, sub in state.get('subtasks', {}).items():
        try:
            content = _flatten_scope(sub, snapshot)
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO ai_chat_subtasks "
                    "  (id, root_session_id, parent_subtask_id, agent, description, status, "
                    "   error_message, completed_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, "
                    "        CASE WHEN %s IN ('completed','failed') THEN now() ELSE NULL END) "
                    "ON CONFLICT (id) DO UPDATE SET "
                    "  status = EXCLUDED.status, error_message = EXCLUDED.error_message, "
                    "  completed_at = COALESCE(ai_chat_subtasks.completed_at, EXCLUDED.completed_at)",
                    (sid, root_session_id, sub['parent_id'], sub['agent'],
                     sub['description'], sub['status'], sub.get('error'), sub['status']),
                )
                if content:
                    cur.execute(
                        "INSERT INTO ai_chat_subtask_messages (id, subtask_id, role, content) "
                        "VALUES (%s, %s, 'assistant', %s) "
                        "ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content",
                        (f'{sid}:a', sid, json.dumps(content)),
                    )
                conn.commit()
        except Exception as e:
            logger.warning('persist_subtasks DB error root=%s subtask=%s: %s',
                           root_session_id, sid, e)
```

在 `_run_listener` 里，把：

```python
        sig = apply_event(state, evt, opencode_session_id)
        if sig == 'idle':
            persist_turn(sid, state)
```

改成：

```python
        sig = apply_event(state, evt, opencode_session_id)
        if sig == 'subtask':
            # 子代理相关信号可能只改了它自己的内容/状态，也可能是顶层第一次
            # 发现委托——两种情况都要把顶层重新 flatten 一遍：占位气泡的
            # status 现查现填（_flatten_scope），子代理状态变了但顶层没有
            # "新事件"时，只有重新持久化顶层才会带出刷新后的状态。
            persist_turn(sid, state)
            persist_subtasks(sid, state)
            continue
        if sig == 'idle':
            persist_turn(sid, state)
```

（子代理事件密度通常低于顶层文本流式输出，这里刻意不加 `INCREMENTAL_PERSIST_INTERVAL` 防抖节流；如果实测发现写入过于频繁，是可以后续优化的点，不阻塞本次交付。）

- [ ] **Step 6: 跑测试确认通过**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_chat_persist.py -v`
Expected: 全部 PASS

- [ ] **Step 7: 写落库持久化的失败测试**

在 `server/tests/test_chat_persist.py` 末尾追加（需要真实 DB，仿照 `test_subtask_repo.py` 的造数据方式）：

```python
def test_persist_subtasks_writes_rows_and_messages():
    """persist_subtasks 要把 state['subtasks'] 里的内容落到
    ai_chat_subtasks / ai_chat_subtask_messages，而不是只停留在内存。"""
    import uuid
    from db import get_db
    from utils.chat_persist import new_state, apply_event, persist_subtasks

    uid = 'u-cp-sub-' + uuid.uuid4().hex[:6]
    sid = 's-cp-sub-' + uuid.uuid4().hex[:6]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s,%s,'x',%s,'developer')", (uid, uid, uid))
            cur.execute("INSERT INTO ai_chat_sessions (id, user_id, workspace_path, "
                        "session_token, token_expires_at) VALUES "
                        "(%s,%s,'/tmp/x',%s, now() + interval '1 day')",
                        (sid, uid, 'tok-' + uuid.uuid4().hex))
        conn.commit()
    try:
        s = new_state()
        apply_event(s, _ev('message.updated',
            {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc'}}), 'oc')
        apply_event(s, _ev('message.part.updated',
            {'part': {'id': 'p1', 'messageID': 'm1', 'type': 'subtask',
                      'sessionID': 'ses_child_persist', 'agent': 'build',
                      'description': 'do x'}}), 'oc')
        apply_event(s, _ev('message.updated',
            {'info': {'role': 'assistant', 'id': 'cm1', 'sessionID': 'ses_child_persist'}}), 'oc')
        apply_event(s, _ev('message.part.updated',
            {'part': {'id': 'cp1', 'messageID': 'cm1', 'type': 'text', 'text': 'hi',
                      'sessionID': 'ses_child_persist'}}), 'oc')

        persist_subtasks(sid, s)

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT root_session_id, agent, description, status FROM "
                           "ai_chat_subtasks WHERE id = %s", ('ses_child_persist',))
                row = cur.fetchone()
                assert row == (sid, 'build', 'do x', 'running')
                cur.execute("SELECT content FROM ai_chat_subtask_messages "
                           "WHERE subtask_id = %s", ('ses_child_persist',))
                content = cur.fetchone()[0]
                assert content == [{'type': 'text', 'text': 'hi'}]

        # 子代理跑完之后再落一次——同一行必须被更新为 completed，不是插入
        # 第二行（single-row-per-subtask upsert 的幂等性）。
        apply_event(s, _ev('session.idle', {'sessionID': 'ses_child_persist'}), 'oc')
        persist_subtasks(sid, s)
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM ai_chat_subtasks WHERE id = %s",
                           ('ses_child_persist',))
                assert cur.fetchone() == ('completed',)
                cur.execute("SELECT count(*) FROM ai_chat_subtask_messages "
                           "WHERE subtask_id = %s", ('ses_child_persist',))
                assert cur.fetchone()[0] == 1   # 还是一行，不是两行
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
                cur.execute("DELETE FROM users WHERE id = %s", (uid,))
            conn.commit()
```

- [ ] **Step 8: 跑测试确认通过，并跑既有测试确认无回归**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_chat_persist.py tests/test_chat_persist_logging.py -v`
Expected: 全部 PASS（本步骤是"`build_content` 改成委托之后，既有非子代理用例——`test_apply_event_accumulates_text_tool_and_detects_idle` 等——一个断言都不应该变"的证据，因为 `_flatten_scope` 对 text/tool 的处理逐字对应原来 `build_content` 的实现）

- [ ] **Step 9: `clear_session` 补一步清子代理数据**

在 `server/routes/ai_chat.py` 的 `clear_session` 函数里，找到：

```python
        cur.execute("DELETE FROM ai_chat_messages WHERE session_id = %s", (sid,))
```

改成：

```python
        cur.execute("DELETE FROM ai_chat_messages WHERE session_id = %s", (sid,))
        cur.execute("DELETE FROM ai_chat_subtasks WHERE root_session_id = %s", (sid,))
```

（`ai_chat_subtask_messages` 靠 `ai_chat_subtasks` 的 `ON DELETE CASCADE` 跟着清掉，不用单独 DELETE。）

- [ ] **Step 10: 写清空会话的回归测试**

找到现有测试 `clear_session` 的测试文件（`grep -rn "clear_session\|/clear" tests/test_ai_chat*.py`），在里面追加一条：

```python
def test_clear_session_deletes_subtasks(client, dev_headers, monkeypatch):
    """clear 是原地重置、不删会话行本身——子代理数据必须跟着清，否则留下一批
    没有任何消息引用得到的孤儿数据。"""
    # 具体的 monkeypatch/夹具写法参照同文件里已有的 clear_session 测试用例，
    # 造一条会话 + 一条属于它的 ai_chat_subtasks 行，调用 clear 端点后
    # 断言该行已被删除。
```

（这一步的具体断言代码，实施者需要先读一遍该测试文件里现有的 `clear_session` 测试用例，复用其中 mock OpenCode client / 数据库连接的方式——不要在这里重新发明一套 mock 策略。）

- [ ] **Step 11: 跑测试确认通过，并跑后端全量**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_chat_persist.py tests/test_ai_chat*.py -v`
Expected: 全部 PASS

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest -q`
Expected: 全部通过

- [ ] **Step 12: 提交**

```bash
git add server/utils/chat_persist.py server/routes/ai_chat.py server/tests/test_chat_persist.py server/tests/test_ai_chat*.py
git commit -m "feat(subtask): 实时聊天路径递归发现/追踪/持久化子代理内容与状态"
```

---

## Task 5: 批任务集成（REST 轮询路径）

**Files:**
- Modify: `server/utils/batch_engine.py`
- Test: 追加到 `server/tests/test_batch_engine.py`

**Interfaces:**
- Consumes: Task 2 的 `map_part`/`format_opencode_error`；Task 1 的两张表。批任务与实时聊天各自的持久化函数（本任务的 `_write_subtask` vs Task 4 的 `persist_subtasks`）**不互相调用**——两条路径的触发模型不同（REST 轮询快照式 vs SSE 增量累加式），硬共用一个函数会把两种不同的调用节奏耦合在一起；两者共用的只是 Task 2 的纯映射函数与 Task 1 的表结构，写入的 SQL 各自独立、但形状一致（都是"子代理一行 + 消息一行/多行 upsert"）。
- Produces: `discover_subtasks(messages, known, parent_depth, parent_sid) -> dict`；`subtask_status_from_messages(messages) -> (status, error)`；`_content_from_parts` 新增可选参数 `subtask_status`（Task 2 已定义，本任务是第一个真正传值的调用方）。

**与实时聊天路径的关键差异**：批任务是 REST 轮询、每次都重新拉取完整消息列表（不是增量事件），子代理的发现与内容持久化不需要维护跨轮询的"累加器"状态。但跟 Task 4 一样必须解决"占位气泡的状态不能定型不变"这个问题——这里用**两阶段**处理：`_persist_conversation` 每次触发时，先递归扫描整棵子代理树、把每一个子代理**当前**的状态都算出来（阶段一，只发现不持久化），拿到一份完整的状态快照后，再统一持久化每一层（阶段二，顶层与每个子代理各自用这份完整快照重新映射自己的 `subtask_use` 占位）。这样任意深度的占位气泡都会用同一份最新状态渲染，不需要"反向找父级更新"。

- [ ] **Step 1: 写失败测试**

在 `server/tests/test_batch_engine.py` 末尾追加：

```python
def test_discover_subtasks_from_messages_finds_new_child():
    """扫描一批消息，发现其中的 subtask part，返回新发现的 sessionID 集合。"""
    import utils.batch_engine as eng
    msgs = [
        {'info': {'role': 'assistant', 'id': 'm1'},
         'parts': [{'type': 'text', 'text': 'hi'},
                  {'type': 'subtask', 'sessionID': 'ses_child1',
                   'agent': 'build', 'description': 'x'}]},
    ]
    found = eng.discover_subtasks(msgs, known={}, parent_depth=0, parent_sid=None)
    assert found['ses_child1']['depth'] == 1
    assert found['ses_child1']['parent_id'] is None
    assert found['ses_child1']['agent'] == 'build'
    assert found['ses_child1']['description'] == 'x'


def test_discover_subtasks_sets_parent_sid_for_nested_scan():
    """递归扫描某个子代理自己的消息时，parent_sid 传该子代理自己的 id，
    发现的孙代的 parent_id 要是它，不是 None。"""
    import utils.batch_engine as eng
    msgs = [{'info': {'role': 'assistant', 'id': 'cm1'},
            'parts': [{'type': 'subtask', 'sessionID': 'ses_grandchild',
                      'agent': 'review', 'description': 'y'}]}]
    found = eng.discover_subtasks(msgs, known={}, parent_depth=1, parent_sid='ses_child1')
    assert found['ses_grandchild']['depth'] == 2
    assert found['ses_grandchild']['parent_id'] == 'ses_child1'


def test_discover_subtasks_skips_already_known():
    import utils.batch_engine as eng
    msgs = [{'info': {'role': 'assistant', 'id': 'm1'},
            'parts': [{'type': 'subtask', 'sessionID': 'ses_child1',
                      'agent': 'build', 'description': 'x'}]}]
    already = {'ses_child1': {'depth': 1, 'parent_id': None}}
    found = eng.discover_subtasks(msgs, known=already, parent_depth=0, parent_sid=None)
    assert found == {}


def test_discover_subtasks_respects_depth_cap():
    import utils.batch_engine as eng
    msgs = [{'info': {'role': 'assistant', 'id': 'm1'},
            'parts': [{'type': 'subtask', 'sessionID': 'ses_too_deep',
                      'agent': 'x', 'description': 'y'}]}]
    found = eng.discover_subtasks(msgs, known={}, parent_depth=eng.MAX_SUBTASK_DEPTH,
                                  parent_sid='whatever')
    assert found == {}


def test_subtask_status_from_messages_detects_error():
    """子代理自己的消息列表里出现 error -> failed；出现 finished 的 assistant
    消息且无 error -> completed；否则 running。跟批任务顶层复用同一套
    finish/error 判定，不是重新发明。"""
    import utils.batch_engine as eng
    errored = [{'info': {'role': 'assistant', 'id': 'm1',
                        'error': {'name': 'ProviderAuthError',
                                 'data': {'providerID': 'a', 'message': 'no key'}}}}]
    status, err = eng.subtask_status_from_messages(errored)
    assert status == 'failed'
    assert 'ProviderAuthError' in err

    running = [{'info': {'role': 'assistant', 'id': 'm1'},
               'parts': [{'type': 'text', 'text': 'wip'}]}]
    status, err = eng.subtask_status_from_messages(running)
    assert status == 'running'
    assert err is None

    finished = [{'info': {'role': 'assistant', 'id': 'm1', 'finish': 'stop',
                         'time': {'created': 1, 'completed': 2}}}]
    status, err = eng.subtask_status_from_messages(finished)
    assert status == 'completed'


def test_persist_conversation_persists_discovered_subtask_and_refreshes_parent_stub(monkeypatch):
    """_persist_conversation 拉到的顶层消息里含 subtask part 时，要：
    (1) 递归拉取该子代理自己的消息并落库到 ai_chat_subtasks/ai_chat_subtask_messages；
    (2) 顶层自己持久化的 ai_chat_messages 内容里，也要有一条 subtask_use 占位，
        且它的 status 反映子代理**当前**的真实状态（这条子代理已经 finish 了，
        占位不能还停在 'running'）——这是本任务的核心断言，不是只落子代理
        自己的数据就算完事。"""
    import uuid
    import json
    import utils.batch_engine as eng
    from db import get_db
    from unittest.mock import MagicMock

    uid = 'u-be-sub-' + uuid.uuid4().hex[:6]
    sid = 's-be-sub-' + uuid.uuid4().hex[:6]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s,%s,'x',%s,'developer')", (uid, uid, uid))
            cur.execute("INSERT INTO ai_chat_sessions (id, user_id, workspace_path, "
                        "session_token, token_expires_at) VALUES "
                        "(%s,%s,'/tmp/x',%s, now() + interval '1 day')",
                        (sid, uid, 'tok-' + uuid.uuid4().hex))
        conn.commit()
    try:
        top_msgs = [
            {'info': {'role': 'assistant', 'id': 'm1'},
             'parts': [{'type': 'subtask', 'sessionID': 'ses_child_be',
                       'agent': 'build', 'description': 'do y'}]},
        ]
        # 子代理自己已经跑完了（finish='stop' + time.completed）
        child_msgs = [
            {'info': {'role': 'assistant', 'id': 'cm1', 'finish': 'stop',
                      'time': {'created': 1, 'completed': 2}},
             'parts': [{'type': 'text', 'text': 'child hi'}]},
        ]
        fake = MagicMock()
        fake.get_messages.side_effect = lambda oc_id, directory='': (
            top_msgs if oc_id == 'oc-top' else child_msgs)
        monkeypatch.setattr(eng, 'opencode_client', fake)

        w = eng.BatchWorker()
        w._persist_conversation(sid, 'prompt', 'oc-top', None, directory='/tmp/x')

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM ai_chat_subtasks WHERE id = %s",
                           ('ses_child_be',))
                assert cur.fetchone() == ('completed',)
                cur.execute("SELECT content FROM ai_chat_subtask_messages "
                           "WHERE subtask_id = %s", ('ses_child_be',))
                content = cur.fetchone()[0]
                assert content == [{'type': 'text', 'text': 'child hi'}]
                # 顶层自己的消息里必须有一条状态已刷新的占位
                cur.execute("SELECT content FROM ai_chat_messages WHERE session_id = %s "
                           "AND role = 'assistant'", (sid,))
                top_content = cur.fetchone()[0]
                stubs = [p for p in top_content if p['type'] == 'subtask_use']
                assert stubs == [{'type': 'subtask_use', 'subtaskId': 'ses_child_be',
                                  'agent': 'build', 'description': 'do y',
                                  'status': 'completed'}]
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
                cur.execute("DELETE FROM users WHERE id = %s", (uid,))
            conn.commit()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_batch_engine.py -v -k subtask`
Expected: FAIL，`AttributeError: module 'utils.batch_engine' has no attribute 'discover_subtasks'`

- [ ] **Step 3: 实现 `discover_subtasks`/`subtask_status_from_messages`，两阶段接入 `_persist_conversation`**

在 `server/utils/batch_engine.py` 里 `_SessionTimeout`/`_TurnFailed` 类定义之后（模块级，不是 `BatchWorker` 的方法），追加：

```python
MAX_SUBTASK_DEPTH = 5


def discover_subtasks(messages: list, known: dict, parent_depth: int,
                      parent_sid: str | None) -> dict:
    """扫描一批（同一个会话拉到的）原始 OpenCode 消息，找出其中携带的 subtask
    part、且还不在 `known` 里的子代理，返回新发现的
    {sessionID: {'depth', 'parent_id', 'agent', 'description'}}。不递归深入
    已知子代理自己的消息——调用方对每个已知子代理的消息列表各自再调一次本
    函数，`parent_depth`/`parent_sid` 传该子代理自己的 depth/id（顶层扫描
    传 depth=0, parent_sid=None）。"""
    found = {}
    for m in (messages or []):
        for p in (m.get('parts') or []):
            if p.get('type') != 'subtask':
                continue
            sid = p.get('sessionID')
            if not sid or sid in known or sid in found:
                continue
            if parent_depth + 1 > MAX_SUBTASK_DEPTH:
                continue
            found[sid] = {'depth': parent_depth + 1, 'parent_id': parent_sid,
                          'agent': p.get('agent'), 'description': p.get('description')}
    return found


def subtask_status_from_messages(messages: list) -> tuple[str, str | None]:
    """从子代理自己的原始消息列表判定当前状态：('failed', 原因) / ('completed', None)
    / ('running', None)。跟批任务顶层的完成/报错判定同源（复用
    opencode_parts.format_opencode_error），不是另起一套。"""
    from utils.opencode_parts import format_opencode_error
    for m in reversed(messages or []):
        info = m.get('info') or {}
        if info.get('role') != 'assistant':
            continue
        if info.get('error'):
            return 'failed', format_opencode_error(info['error'])
        finish = info.get('finish')
        completed = (info.get('time') or {}).get('completed')
        if completed and finish not in (None, '', 'tool-calls', 'tool_use'):
            return 'completed', None
        return 'running', None
    return 'running', None
```

在 `BatchWorker` 类里，`_persist_conversation` 方法之前追加两个新方法：

```python
    def _collect_subtasks(self, messages: list, known: dict, child_messages: dict,
                          parent_depth: int, parent_sid: str | None, directory: str):
        """阶段一（只发现、不持久化）：递归扫描 messages 里的子代理，拉取
        每一个的自己的消息、算出它自己的状态，全部记进 known/child_messages。
        `known` 在递归调用之间原地累积（同一个字典对象一路传下去），既防止
        重复处理同一个子代理，也是深度判断的依据。持久化留到阶段二统一做——
        必须先拿到整棵树的完整状态快照，各层的占位气泡才能在同一次持久化里
        全部用上最新状态，不会出现"父级先写了、子代理的状态后来才算出来"
        这种顺序问题。"""
        newly = discover_subtasks(messages, known, parent_depth, parent_sid)
        for sid, info in newly.items():
            known[sid] = info
            try:
                msgs = opencode_client.get_messages(sid, directory=directory) or []
            except Exception:
                msgs = []
            status, error = subtask_status_from_messages(msgs)
            info['status'] = status
            info['error'] = error
            child_messages[sid] = msgs
            self._collect_subtasks(msgs, known, child_messages, info['depth'], sid, directory)

    def _write_subtask(self, root_session_id: str, subtask_id: str, info: dict,
                       subtask_status: dict, child_messages: list):
        """阶段二：把一个子代理的摘要行 + 它当前拉到的全部消息 upsert 进库。
        `subtask_status` 是整棵树的完整状态快照（阶段一算好的），传给
        `_content_from_parts` 让这个子代理自己内容里（如果有）更深一层的
        占位气泡也能用上最新状态——跟顶层的处理方式统一。"""
        import json as _json
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO ai_chat_subtasks "
                "  (id, root_session_id, parent_subtask_id, agent, description, status, "
                "   error_message, completed_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, "
                "        CASE WHEN %s IN ('completed','failed') THEN now() ELSE NULL END) "
                "ON CONFLICT (id) DO UPDATE SET "
                "  status = EXCLUDED.status, error_message = EXCLUDED.error_message, "
                "  completed_at = COALESCE(ai_chat_subtasks.completed_at, EXCLUDED.completed_at)",
                (subtask_id, root_session_id, info.get('parent_id'), info.get('agent'),
                 info.get('description'), info['status'], info.get('error'), info['status']),
            )
            for m in child_messages:
                minfo = m.get('info') or {}
                if minfo.get('role') != 'assistant':
                    continue
                content = self._content_from_parts(m.get('parts'), subtask_status)
                if not content:
                    continue
                mid = minfo.get('id') or f'{subtask_id}:a:{id(m)}'
                cur.execute(
                    "INSERT INTO ai_chat_subtask_messages (id, subtask_id, role, content) "
                    "VALUES (%s, %s, 'assistant', %s) "
                    "ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content",
                    (mid, subtask_id, _json.dumps(content)),
                )
            conn.commit()
```

把 `_persist_conversation` 方法改成两阶段：找到

```python
    def _persist_conversation(self, session_id: str, prompt: str,
                              oc_session_id: str, assistant_msg: dict | None,
                              directory: str = ''):
        """..."""
        try:
            import json as _json
            raw = []
            try:
                raw = opencode_client.get_messages(oc_session_id, directory=directory) or []
            except Exception:
                raw = []
            assistant_rows = []   # (message_id, content, meta)
            for m in raw:
                info = m.get('info') or {}
                if info.get('role') != 'assistant':
                    continue
                content = self._content_from_parts(m.get('parts'))
                if content:
```

改成：

```python
    def _persist_conversation(self, session_id: str, prompt: str,
                              oc_session_id: str, assistant_msg: dict | None,
                              directory: str = ''):
        """..."""
        try:
            import json as _json
            raw = []
            try:
                raw = opencode_client.get_messages(oc_session_id, directory=directory) or []
            except Exception:
                raw = []

            # 阶段一：递归发现整棵子代理树 + 各自的当前状态。
            known: dict = {}
            child_messages: dict = {}
            self._collect_subtasks(raw, known, child_messages, parent_depth=0,
                                   parent_sid=None, directory=directory)
            subtask_status = {sid: info['status'] for sid, info in known.items()}

            # 阶段二：先落每个子代理自己的行/消息，再落顶层——都用同一份
            # subtask_status，占位气泡不会有哪一层状态落后。
            for sid, info in known.items():
                self._write_subtask(session_id, sid, info, subtask_status,
                                    child_messages.get(sid, []))

            assistant_rows = []   # (message_id, content, meta)
            for m in raw:
                info = m.get('info') or {}
                if info.get('role') != 'assistant':
                    continue
                content = self._content_from_parts(m.get('parts'), subtask_status)
                if content:
```

（这段之后原有的 `meta = public_meta(meta_from_info(info)); assistant_rows.append(...)` 到函数结尾的代码**不改**，只是 `content = self._content_from_parts(m.get('parts'))` 这一行加了 `subtask_status` 参数。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_batch_engine.py -v`
Expected: 全部 PASS（既有全部 + 本任务新增 6 条）

- [ ] **Step 5: 变异验证错误透出确有鉴别力**

把 `subtask_status_from_messages` 里 `if info.get('error'):` 那个分支临时删掉，重跑 `test_subtask_status_from_messages_detects_error`，确认变红（应该会得到 `'running'` 而不是 `'failed'`）；恢复后确认变绿。记录红/绿原文。

- [ ] **Step 6: 变异验证顶层占位刷新确有鉴别力**

把 `_persist_conversation` 里新加的 `content = self._content_from_parts(m.get('parts'), subtask_status)` 临时改回 `self._content_from_parts(m.get('parts'))`（不传状态快照），重跑 `test_persist_conversation_persists_discovered_subtask_and_refreshes_parent_stub`，确认变红（顶层持久化的占位 `status` 会是 `map_part` 默认值 `'running'`，断言期望的 `'completed'` 对不上）；恢复后确认变绿。记录红/绿原文。

- [ ] **Step 7: 跑后端全量确认无回归**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest -q`
Expected: 全部通过

- [ ] **Step 8: 提交**

```bash
git add server/utils/batch_engine.py server/tests/test_batch_engine.py
git commit -m "feat(subtask): 批任务路径两阶段递归发现/轮询/持久化子代理内容与状态"
```

---

## Task 6: 前端组件 + 两条路径接入

**Files:**
- Create: `src/components/ai-chat/SubtaskBubble.vue`
- Modify: `src/api/aiChat.ts`
- Modify: `src/api/aiBatchAdmin.ts`
- Modify: `src/views/ai-chat/AiChatView.vue`
- Modify: `src/components/ai-chat/BatchConversationView.vue`
- Test: `src/components/ai-chat/__tests__/SubtaskBubble.test.ts`（新建）

**Interfaces:**
- Consumes: Task 3 的两个端点。
- Produces:
  ```ts
  // src/api/aiChat.ts 新增
  export interface SubtaskSummary { id: string; agent: string | null;
    description: string | null; status: 'running'|'completed'|'failed'; error: string | null }
  export interface SubtaskMessagesResult { subtask: SubtaskSummary;
    messages: AiMessage[]; truncated: boolean; total: number }
  getSubtaskMessages(sessionId: string, subtaskId: string): Promise<SubtaskMessagesResult>

  // src/api/aiBatchAdmin.ts 新增
  getAdminSubtaskMessages(batchId: string, sid: string, subtaskId: string): Promise<SubtaskMessagesResult>
  ```

- [ ] **Step 1: 扩展 `AiContentPart` 类型 + 新增 API 函数**

在 `src/api/aiChat.ts` 的 `AiContentPart` union 里追加一个变体：

```ts
export type AiContentPart =
  | { type: 'text'; text: string }
  | { type: 'tool_use'; name: string; title?: string; status?: string; input?: unknown; result?: unknown; durationMs?: number }
  | { type: 'file'; name: string; path: string }
  | { type: 'run_result'; filename: string; exitCode: number; timedOut: boolean; stdout: string; stderr: string; outputFiles: string[] }
  | { type: 'mcp_services'; servers: McpServer[] }
  | { type: 'subtask_use'; subtaskId: string; agent: string | null; description: string | null; status: 'running' | 'completed' | 'failed' }
```

在文件里合适位置（`getMessages` 函数附近）追加：

```ts
export interface SubtaskSummary {
  id: string
  agent: string | null
  description: string | null
  status: 'running' | 'completed' | 'failed'
  error: string | null
}

export interface SubtaskMessagesResult {
  subtask: SubtaskSummary
  messages: AiMessage[]
  truncated: boolean
  total: number
}

export function getSubtaskMessages(sessionId: string, subtaskId: string) {
  return get<SubtaskMessagesResult>(
    `/ai/chat/sessions/${encodeURIComponent(sessionId)}/subtasks/${encodeURIComponent(subtaskId)}/messages`)
}
```

在 `src/api/aiBatchAdmin.ts` 里追加（复用 `aiChat.ts` 的 `SubtaskMessagesResult` 类型）：

```ts
import type { SubtaskMessagesResult } from './aiChat'

export function getAdminSubtaskMessages(batchId: string, sid: string, subtaskId: string) {
  return get<SubtaskMessagesResult>(
    `${BASE}/${batchId}/sessions/${sid}/subtasks/${subtaskId}/messages`)
}
```

（`BASE` 是该文件里已有的 `/ai/chat/admin/batches` 常量。）

- [ ] **Step 2: 类型检查确认没有破坏既有用法**

Run: `npx vue-tsc --noEmit`
Expected: 零错误（新增的 union 成员是可选新增，不影响既有 switch/`v-if` 分支的穷尽性——Vue 模板里的 `v-else-if` 链不受 TS 穷尽性检查约束）

- [ ] **Step 3: 写 `SubtaskBubble.vue` 的失败测试**

新建 `src/components/ai-chat/__tests__/SubtaskBubble.test.ts`：

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'

vi.mock('@/api/aiChat', () => ({
  getSubtaskMessages: vi.fn(),
}))

import { getSubtaskMessages } from '@/api/aiChat'
import SubtaskBubble from '@/components/ai-chat/SubtaskBubble.vue'

describe('SubtaskBubble', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('未展开时不发请求', () => {
    mount(SubtaskBubble, {
      props: { subtaskId: 'ses_x', sessionId: 's-1', agent: 'build',
               description: 'do x', status: 'running', depth: 1, fetchFn: getSubtaskMessages },
    })
    expect(getSubtaskMessages).not.toHaveBeenCalled()
  })

  it('点击展开后发起一次请求', async () => {
    vi.mocked(getSubtaskMessages).mockResolvedValue({
      subtask: { id: 'ses_x', agent: 'build', description: 'do x', status: 'completed', error: null },
      messages: [], truncated: false, total: 0,
    })
    const wrapper = mount(SubtaskBubble, {
      props: { subtaskId: 'ses_x', sessionId: 's-1', agent: 'build',
               description: 'do x', status: 'completed', depth: 1, fetchFn: getSubtaskMessages },
    })
    await wrapper.find('.subtask-bubble__head').trigger('click')
    await wrapper.vm.$nextTick()
    expect(getSubtaskMessages).toHaveBeenCalledTimes(1)
  })

  it('再次展开不重复发请求', async () => {
    vi.mocked(getSubtaskMessages).mockResolvedValue({
      subtask: { id: 'ses_x', agent: 'build', description: 'do x', status: 'completed', error: null },
      messages: [], truncated: false, total: 0,
    })
    const wrapper = mount(SubtaskBubble, {
      props: { subtaskId: 'ses_x', sessionId: 's-1', agent: 'build',
               description: 'do x', status: 'completed', depth: 1, fetchFn: getSubtaskMessages },
    })
    const head = wrapper.find('.subtask-bubble__head')
    await head.trigger('click')
    await wrapper.vm.$nextTick()
    await head.trigger('click')   // 收起
    await head.trigger('click')   // 再展开
    await wrapper.vm.$nextTick()
    expect(getSubtaskMessages).toHaveBeenCalledTimes(1)
  })

  it('超过深度上限不渲染可展开内容', () => {
    const wrapper = mount(SubtaskBubble, {
      props: { subtaskId: 'ses_x', sessionId: 's-1', agent: 'build',
               description: 'do x', status: 'running', depth: 6, fetchFn: getSubtaskMessages },
    })
    expect(wrapper.text()).toContain('已达展示深度上限')
    expect(wrapper.find('.subtask-bubble__head').exists()).toBe(false)
  })
})
```

- [ ] **Step 4: 跑测试确认失败**

Run: `npx vitest run src/components/ai-chat/__tests__/SubtaskBubble.test.ts`
Expected: FAIL，找不到该组件文件

- [ ] **Step 5: 实现 `SubtaskBubble.vue`**

```vue
<template>
  <div v-if="depth > 5" class="subtask-bubble subtask-bubble--capped">
    已达展示深度上限
  </div>
  <div v-else class="subtask-bubble" :class="`subtask-bubble--${status}`">
    <div class="subtask-bubble__head" @click="toggle">
      <ElIcon class="subtask-bubble__chev" :class="{ open }"><ArrowRight /></ElIcon>
      <ElIcon class="subtask-bubble__icon"><MagicStick /></ElIcon>
      <span class="subtask-bubble__agent">{{ agent || '子代理' }}</span>
      <span v-if="description" class="subtask-bubble__desc" :title="description">{{ description }}</span>
      <span class="subtask-bubble__status">
        <ElIcon v-if="status === 'completed'" class="ok"><CircleCheck /></ElIcon>
        <ElIcon v-else-if="status === 'failed'" class="err"><CircleClose /></ElIcon>
        <ElIcon v-else class="run spin"><Loading /></ElIcon>
      </span>
    </div>
    <div v-show="open" class="subtask-bubble__body">
      <div v-if="loading" class="subtask-bubble__loading">加载中…</div>
      <template v-else-if="result">
        <el-alert v-if="result.subtask.status === 'failed' && result.subtask.error"
                  type="error" :closable="false" :title="result.subtask.error" />
        <el-alert v-if="result.truncated" type="info" :closable="false" show-icon
                  :title="`仅显示最近 ${result.messages.length} 条，共 ${result.total} 条`" />
        <el-empty v-if="!result.messages.length" description="子代理还没有对话记录" />
        <div v-for="m in result.messages" :key="m.id" class="subtask-bubble__msg">
          <template v-for="(p, i) in m.content" :key="i">
            <MarkdownView v-if="p.type === 'text' && p.text" :text="p.text" />
            <ToolCallBubble
              v-else-if="p.type === 'tool_use'"
              :name="p.name" :title="p.title" :status="p.status"
              :input="p.input" :result="p.result" :duration-ms="p.durationMs"
            />
            <SubtaskBubble
              v-else-if="p.type === 'subtask_use'"
              :subtask-id="p.subtaskId" :session-id="sessionId"
              :agent="p.agent" :description="p.description" :status="p.status"
              :depth="depth + 1" :fetch-fn="fetchFn"
            />
          </template>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElIcon, ElAlert, ElEmpty } from 'element-plus'
import { ArrowRight, MagicStick, CircleCheck, CircleClose, Loading } from '@element-plus/icons-vue'
import MarkdownView from '@/components/ai-chat/MarkdownView.vue'
import ToolCallBubble from '@/components/ai-chat/ToolCallBubble.vue'
import type { SubtaskMessagesResult } from '@/api/aiChat'

// 递归组件需要显式声明 name 才能在自己的模板里引用自己。
defineOptions({ name: 'SubtaskBubble' })

const props = defineProps<{
  subtaskId: string
  sessionId: string
  agent: string | null
  description: string | null
  status: 'running' | 'completed' | 'failed'
  depth: number
  fetchFn: (sessionId: string, subtaskId: string) => Promise<SubtaskMessagesResult>
}>()

const open = ref(false)
const loading = ref(false)
const result = ref<SubtaskMessagesResult | null>(null)

async function toggle() {
  open.value = !open.value
  if (open.value && !result.value) {
    loading.value = true
    try {
      result.value = await props.fetchFn(props.sessionId, props.subtaskId)
    } finally {
      loading.value = false
    }
  }
}
</script>

<style scoped lang="scss">
.subtask-bubble {
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  margin: 8px 0;
  background: var(--el-fill-color-lighter);
  font-size: 13px;
  overflow: hidden;
}
.subtask-bubble--capped {
  padding: 8px 12px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.subtask-bubble__head {
  display: flex; align-items: center; gap: 6px; padding: 8px 12px;
  cursor: pointer; user-select: none;
  &:hover { background: var(--el-fill-color-light); }
}
.subtask-bubble__chev { transition: transform 0.15s; color: var(--el-text-color-secondary); &.open { transform: rotate(90deg); } }
.subtask-bubble__icon { color: var(--el-color-primary); flex-shrink: 0; }
.subtask-bubble__agent { font-weight: 600; color: var(--el-text-color-primary); flex-shrink: 0; }
.subtask-bubble__desc {
  color: var(--el-text-color-secondary); font-size: 12px; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; min-width: 0; flex: 1;
}
.subtask-bubble__status { margin-left: auto; flex-shrink: 0; .ok { color: var(--el-color-success); } .err { color: var(--el-color-danger); } .run { color: var(--el-color-primary); } }
.subtask-bubble__body { padding: 4px 12px 12px; border-top: 1px solid var(--el-border-color-lighter); }
.subtask-bubble__loading { padding: 12px; color: var(--el-text-color-secondary); font-size: 12px; }
.subtask-bubble__msg { padding: 8px 0; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
```

- [ ] **Step 6: 跑测试确认通过**

Run: `npx vitest run src/components/ai-chat/__tests__/SubtaskBubble.test.ts`
Expected: 4 passed

- [ ] **Step 7: 变异验证懒加载确有鉴别力**

把 `toggle` 函数里的 `if (open.value && !result.value)` 临时改成 `if (true)`（不管展开与否都请求），重跑「未展开时不发请求」那条，确认变红；恢复后确认变绿。记录红/绿原文。

- [ ] **Step 8: 接入 `AiChatView.vue`**

顶部 `<script setup>` 导入区加一行：

```ts
import SubtaskBubble from '@/components/ai-chat/SubtaskBubble.vue'
import { getSubtaskMessages } from '@/api/aiChat'
```

在消息渲染模板里，找到（第 697-700 行附近）：

```vue
                    <McpServicesBlock
                      v-else-if="p.type === 'mcp_services'"
                      :servers="p.servers"
                    />
```

在它之后、`<template v-else-if="p.type === 'text' && p.text">` 之前插入：

```vue
                    <SubtaskBubble
                      v-else-if="p.type === 'subtask_use'"
                      :subtask-id="p.subtaskId" :session-id="activeId!"
                      :agent="p.agent" :description="p.description" :status="p.status"
                      :depth="1" :fetch-fn="getSubtaskMessages"
                    />
```

（`activeId` 是该组件已有的当前会话 id 响应式变量——确认变量名：`grep -n "const activeId" src/views/ai-chat/AiChatView.vue`，若命名不同以实际为准。）

- [ ] **Step 9: 接入 `BatchConversationView.vue`**

在该文件的 `<script setup>` 导入区加：

```ts
import SubtaskBubble from '@/components/ai-chat/SubtaskBubble.vue'
```

`defineProps` 里加一个新的必填 prop，用来把"当前批任务子会话的 id"和"拉取函数"传下去（批任务走管理员端点还是普通端点，取决于这个查看器是被哪个页面复用的——参照 `BatchConversationView.vue` 现有 `messages`/`truncated`/`total`/`loading` props 的调用方，在两处调用它的地方（普通用户的批任务详情、管理员批任务详情）分别传入各自对应的 fetch 函数）：

```ts
defineProps<{
  messages: AdminMessage[]
  truncated: boolean
  total: number
  loading: boolean
  sessionId: string
  fetchSubtaskFn: (sessionId: string, subtaskId: string) => Promise<import('@/api/aiChat').SubtaskMessagesResult>
}>()
```

模板里 `msg__tool` 那个 `el-collapse` 分支之后追加：

```vue
          <SubtaskBubble
            v-else-if="p.type === 'subtask_use'"
            :subtask-id="p.subtaskId" :session-id="sessionId"
            :agent="p.agent" :description="p.description" :status="p.status"
            :depth="1" :fetch-fn="fetchSubtaskFn"
          />
```

在两处使用 `<BatchConversationView>` 的父组件里（`grep -rn "BatchConversationView" src/` 找到，应该是普通用户批任务详情页与 `AiBatchAdmin.vue`），补上新增的 `session-id` 与 `fetch-subtask-fn` 两个 prop——管理员页传 `getAdminSubtaskMessages`（绑定当前 `batchId`），普通用户页传 `getSubtaskMessages`。**具体绑定方式请先读这两个父组件现有代码，参照它们已经在传的 `messages`/`truncated` 等 prop 的数据来源，不要凭空猜测变量名。**

- [ ] **Step 10: 类型检查 + 相关测试全跑一遍**

Run: `npx vue-tsc --noEmit`
Expected: 零错误

Run: `npx vitest run`
Expected: 全部通过（基线以 Task 5 完成时的实际数字为准）

- [ ] **Step 11: 提交**

```bash
git add src/api/aiChat.ts src/api/aiBatchAdmin.ts src/components/ai-chat/SubtaskBubble.vue src/components/ai-chat/__tests__/SubtaskBubble.test.ts src/views/ai-chat/AiChatView.vue src/components/ai-chat/BatchConversationView.vue
git commit -m "feat(subtask): 递归子代理气泡组件，接入实时聊天与批任务两条渲染路径"
```

---

## Task 7: 文档 + Playwright + proxy.py 验证

**Files:**
- Modify: `docs/user-guide/ai/assistant.md`
- Modify: `docs/user-guide/ai/batch-tasks.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: Task 1-6 的全部成果。

- [ ] **Step 1: 在开发库里造一次真实的子代理委托对话**

用管理员账号在 AI 助手里发起一个会明确触发委托子代理（`@` 提及某个 subagent，或让主 agent 自行判断委托）的对话，确认能在真实 OpenCode 环境下观察到 `subtask` part 出现（这一步同时验证 §4.1/§4.2 里"child session events 复用同一条 directory-scoped SSE 流"这个假设在真实环境下成立——如果观察到子代理的内容没有被追踪到，先回到 Task 4 排查，不要跳过这一步直接走 Playwright）。

- [ ] **Step 2: Playwright 实时聊天路径**

驱动真实浏览器：登录 → 打开 AI 助手 → 发起一次会委托子代理的对话 → 观察气泡列表里出现子代理气泡（折叠态，agent 名 + description + 运行中图标）→ 等它完成（状态图标变成完成）→ 点击展开 → 断言渲染出子代理自己的文本/工具调用内容。截图存 `.playwright-mcp/`。

- [ ] **Step 3: Playwright 批任务路径**

创建一个会委托子代理的批任务（前台或 API 均可） → 等它完成 → 进入批任务详情/管理员批任务管理页 → 打开对话查看器 → 展开子代理气泡 → 断言同样能看到嵌套内容。截图存 `.playwright-mcp/`。

- [ ] **Step 4: proxy.py 验证**

新增的两个端点是新路由形态，按 CLAUDE.md 强制走一遍：

```bash
npm run build
cd server && python proxy.py
```

经 `:8080` 分别打：
```
GET /api/ai/chat/sessions/<sid>/subtasks/<subtaskId>/messages
GET /api/ai/chat/admin/batches/<batchId>/sessions/<sid>/subtasks/<subtaskId>/messages
```
确认返回各自的业务响应（不是 404）。

- [ ] **Step 5: 文档**

在 `docs/user-guide/ai/assistant.md` 里补一段：AI 委托子代理执行任务时，对话里会出现一个可展开的气泡（agent 名称、任务描述、执行状态），点击可以看到子代理自己的完整执行过程（含它调用的工具），支持多层嵌套（子代理又委托子代理），最多展示 5 层。

在 `docs/user-guide/ai/batch-tasks.md` 的对话查看部分补一句：批任务子任务的对话里同样支持展开查看委托的子代理执行轨迹。

- [ ] **Step 6: 更新 CLAUDE.md**

在 "AI Agent Chat (M1)" 小节末尾补一段，说明：子代理（`SubtaskPart`）执行轨迹持久化在独立的 `ai_chat_subtasks`/`ai_chat_subtask_messages` 表（`id` 就是 OpenCode 自己的子会话 sessionID，不发明并行 id）；共享的 part 映射在 `server/utils/opencode_parts.py`（`chat_persist.py`/`batch_engine.py` 都调它）；两个只读端点分别在 `routes/ai_chat.py`（所有权校验）与 `routes/ai_batch_admin.py`（管理员）；深度上限 5 层；前端 `SubtaskBubble.vue` 是递归组件，懒加载展开。

- [ ] **Step 7: 全量回归**

```bash
npm run test:all
```
Expected: 前后端全部通过

- [ ] **Step 8: 提交**

```bash
git add docs/ CLAUDE.md
git commit -m "docs(subtask): 用户文档 + CLAUDE.md，完成 Playwright 与 proxy.py 验证"
```

---

## 自查记录

**1. 规格覆盖** —— 逐节比对 `2026-08-12-subagent-trace-visibility-design.md`：

| 规格 | 落在 |
|---|---|
| §3.1 新建两张表、不改现有表 | Task 1 |
| §3.1 共享映射模块、行为不变约束 | Task 2 |
| §3.2 深度限制 5 层 | Task 4（`MAX_SUBTASK_DEPTH`）、Task 5（同名常量）、Task 6（组件 `depth` prop） |
| §4.1 SSE 路径：已知集合、增量持久化、竞态自愈 | Task 4 |
| §4.2 REST 路径：递归轮询、完成判定复用、错误透出 | Task 5 |
| §5 惰性加载、两个端点、渲染从简、500 截断 | Task 3（端点+截断）、Task 6（组件） |
| §6.1 错误透出 | Task 4/5 的 `error`/`format_opencode_error` |
| §6.2 归属校验 404 | Task 3 |
| §6.3 排序用 seq | Task 3（含变异验证） |
| §6.4 500 截断 | Task 3 |
| §6.5 清空会话清子代理数据 | Task 4 Step 9-10 |
| §6.6 备份/还原清单 | Task 1 |
| §7 测试要求 | 各任务内嵌 |

**2. 占位符扫描** —— 无 TBD/TODO；Task 6 Step 9 里"具体绑定方式请先读现有代码"是因为 `BatchConversationView.vue` 的两个调用方在这份计划编写时尚未被本计划的前序任务改动、其内部变量命名不受本计划控制，属于合理的"参照既有模式"指引，不是逃避给出实现的占位符——已在指引里给出了明确要传什么 prop、传什么值。

**3. 类型一致性** —— `map_part`/`format_opencode_error` 的签名在 Task 2 定义，Task 4/5 按同名同参调用；`get_subtask_messages` 在 Task 3 定义，Task 4/5 各自的持久化函数与 Task 3 的路由按同一份数据形状（`subtask`/`messages`/`truncated`/`total`）消费；前端 `SubtaskMessagesResult`/`SubtaskSummary` 在 Task 6 定义，两处 API 函数与 `SubtaskBubble.vue` 一致引用。`ai_chat_subtasks`/`ai_chat_subtask_messages` 的列名在 Task 1 定义后，Task 3/4/5 的 SQL 逐处核对一致（`root_session_id`/`parent_subtask_id`/`agent`/`description`/`status`/`error_message`/`completed_at`；消息表 `subtask_id`/`role`/`content`/`meta`/`seq`）。

**4. 自查时发现并修正的一处真实设计缺口**：初稿的 Task 4/5 只在 `state['subtasks']`（或批任务的 `known` 字典）里记录子代理的追踪状态，但从未把对应的 `subtask_use` 占位真正写进**父级自己**的持久化内容——Task 6 的渲染完全依赖父级消息 `content` 里有这一条，若不修，前端永远等不到数据可渲染。同时，占位一旦写入后状态会不会随子代理完成/失败而更新也没有设计（子代理跑完之后，已经落库的占位可能永远停在"运行中"）。修正为统一的"占位存原始数据、状态在每次 flatten/持久化时现查现填"模式（Task 4 的 `_flatten_scope` + `_status_snapshot`；Task 5 的两阶段"先发现整棵树状态、再统一持久化"），并在两个任务里各补了一条专门验证"状态刷新"的测试（`test_apply_event_parent_stub_reflects_child_status_after_it_finishes`、`test_persist_conversation_persists_discovered_subtask_and_refreshes_parent_stub`）与对应的变异验证步骤，不是只补了发现漏洞的那一条断言就算完事。
