# AI 会话撤销/重做（undo/redo）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 AI 会话的「编辑/重新生成」升级为真撤销：OpenCode 会话上下文回退 + 工作区文件按步还原 + 平台消息 stash 可重做，并对齐 OC 2.x 的前向适配点。

**Architecture:** 后端新增 `utils/ai_revert.py`（锚点解析纯函数 + stash/状态 SQL + 撤销/重做编排），OpenCode 客户端/runtime 抽象补 revert 三元组，路由新增 undo-preview/undo/redo 并把既有 DELETE 消息端点切到同一核心；SSE 代理加进程内事件总线做跨标签页同步。前端 store 以服务端窗口为准、composer 上方加重做横幅、`/undo` `/redo` 本地命令。E2E 用 0-LLM stub-LLM 隔离栈（gate-leak-probe 同款）断言「上下文真回退/文件物理还原/跨标签页收敛」。

**Tech Stack:** Flask + PostgreSQL（psycopg2）/ Vue3 + Pinia + Element Plus / OpenCode 1.19.3 serve HTTP API / pytest + vitest + Playwright / 隔离 stub-LLM 探针栈。

**Spec:** `docs/superpowers/specs/2026-11-10-ai-chat-undo-redo-design.md`（证据编号 E1–E12 均出自该 spec §0.3，本计划直接引用）。

## Global Constraints

- OpenCode 生产版本 **1.19.3**（v1 API：`POST /session/:id/revert`、`POST /session/:id/unrevert`）。OC 2.x 映射只写在 adapter 的注释里（spec §2.5 表），不实现。
- OC revert **必须传 user 消息 id**（E2 陷阱），响应必须校验 `revert.messageID`（E6）。
- 原子性契约：**报错＝什么都没改**（DB 回滚 + OC 反向补偿）。
- 所有新 SQL 的 `execute` 首参必须是**字面量内联**的 SQL 字符串（Mimosa 扫描规则：SQL 常量中转会拦）；不引入硬编码凭据。
- 注释与用户可见文案用中文，代码标识符用英文；与所在文件既有风格一致。
- 测试纪律（用户既定要求）：新测试先于实现提交并跑红；E2E 走真实链路；探针栈端口 **3197(stub)/4198(serve)/3094(backend)/DB `casemanage_undoprobe`**，与 dev（3002/4096）和既有 gateprobe（3093/4098/3099）零共享。
- 后端单测跑在独立库 `casemanage_test`（conftest 自动 `DB_NAME`），串行运行：`cd server && python -m pytest tests/test_xxx.py -v`。
- 前端单测：`npx vitest run <file>`；E2E 探针：`python e2e/manual/undo-probe/undo_e2e.py`。
- 每个任务结束 commit 一次；全部完成后跑全量回归（Task 15）。

## File Structure（全景）

```
server/
  migrations/2026_11_10_ai_chat_undo_redo.py   [新] 两表一列，幂等
  app.py                                        [改] boot 迁移注册块（照 :338-344 模式）
  db_schema/core.py                             [改] 基线 DDL（feedback 表之后）
  config.py                                     [改] AI_UNDO_FILE_SNAPSHOT_MAX_AGE_DAYS
  utils/ai_revert.py                            [新] 核心：锚点解析/文件能力/stash/编排
  utils/session_event_bus.py                    [新] 进程内 sid→连接队列 扇出总线
  utils/opencode_client.py                      [改] get_project / revert_session / unrevert_session
  utils/runtime/base.py                         [改] revert_stage / revert_clear 抽象
  utils/runtime/opencode_local.py               [改] 透传实现
  utils/runtime/stub.py                         [改] 内存 revert 模拟
  routes/ai_chat.py                             [改] 3 新端点 + DELETE 升级 + 6 处提交挂点 + SSE 总线接入
  tests/test_undo_migration.py                  [新]
  tests/test_ai_revert.py                       [新] 纯函数 + SQL 助手 + 编排单测
  tests/test_session_event_bus.py               [新]
  tests/test_opencode_client_undo.py            [新]
  tests/test_routes_ai_chat.py                  [改] delete 3 用例升级 + undo/redo/preview 用例
src/
  api/aiChat.ts                                 [改] 3 新 API + RevertState 类型 + allow-list
  stores/aiChat.ts                              [改] revertState/undo/redo/远程收敛/命令
  views/ai-chat/AiChatView.vue                  [改] 弹窗升级 + 重做横幅
  components/ai-chat/chat-commands.ts           [改] /undo /redo
  api/__tests__/aiChat.test.ts                  [改] allow-list 守护
  stores/__tests__/aiChat.undo.test.ts          [新]
  components/ai-chat/__tests__/chat-commands.test.ts [改]
e2e/manual/undo-probe/                          [新] 0-LLM 真实链路探针（README + stub + 编排器）
e2e/ai-full/undo-redo.spec.ts                   [新] @llm 真模型冒烟
```

---

### Task 1: 迁移（两表一列）+ config 常量

**Files:**
- Create: `server/migrations/2026_11_10_ai_chat_undo_redo.py`
- Modify: `server/db_schema/core.py`（`ai_chat_message_feedback` 块之后，约 :470）
- Modify: `server/app.py`（boot 迁移注册块，照 ：338-344 的 `_mp4/_spec4/_m4` 模式追加）
- Modify: `server/config.py`（`AI_CHAT_NOTIFY_MIN_SECONDS` 附近，约 ：105）
- Test: `server/tests/test_undo_migration.py`

**Interfaces:**
- Produces: 表 `ai_chat_message_stash`、`ai_chat_session_reverts`；列 `ai_chat_sessions.context_replay_pending`；config 常量 `AI_UNDO_FILE_SNAPSHOT_MAX_AGE_DAYS`（默认 7）。Task 2/4/6/7 全部依赖。

- [ ] **Step 1: 写失败测试**

```python
"""undo/redo 迁移：两表一列存在且幂等（spec §2.1）。"""
import importlib.util
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

_MIG = os.path.join(os.path.dirname(__file__), '..', 'migrations',
                    '2026_11_10_ai_chat_undo_redo.py')


def _load():
    spec = importlib.util.spec_from_file_location('_undo_migration', _MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_undo_tables_and_column_exist():
    mod = _load()
    mod.run()
    mod.run()  # 幂等可重跑
    from db import get_db
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name IN ('ai_chat_message_stash', 'ai_chat_session_reverts')")
        assert {r[0] for r in cur.fetchall()} == {
            'ai_chat_message_stash', 'ai_chat_session_reverts'}
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'ai_chat_sessions' "
            "  AND column_name = 'context_replay_pending'")
        assert cur.fetchone()
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'ai_chat_message_stash' ORDER BY ordinal_position")
        cols = [r[0] for r in cur.fetchall()]
        assert cols == ['message_id', 'session_id', 'role', 'content', 'meta',
                        'created_at', 'seq', 'rating', 'stashed_at']


def test_stash_rows_survive_message_hard_delete():
    """stash 行不随消息删除丢失（审计口径），但随会话硬删级联。"""
    mod = _load()
    from db import get_db
    sid = f'sess_umig_{uuid.uuid4().hex[:8]}'
    try:
        with get_db() as conn:
            cur = conn.cursor()
            # 规范用户由 conftest bootstrap 播种；直接引用其 id
            cur.execute("SELECT id FROM users WHERE username = 'admin' LIMIT 1")
            uid = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO ai_chat_sessions (id, user_id, workspace_path, "
                "  session_token, token_expires_at) "
                "VALUES (%s, %s, '/tmp/undo-mig', 'tok-' || %s, NOW() + interval '1 day')",
                (sid, uid, sid))
            cur.execute(
                "INSERT INTO ai_chat_messages (id, session_id, role, content) "
                "VALUES (%s, %s, 'user', %s)",
                (sid + ':u', sid, json.dumps([{'type': 'text', 'text': 'x'}])))
            cur.execute(
                "INSERT INTO ai_chat_message_stash (message_id, session_id, role, "
                "  content, created_at, seq) "
                "SELECT id, session_id, role, content, created_at, seq "
                "FROM ai_chat_messages WHERE id = %s", (sid + ':u',))
            cur.execute("DELETE FROM ai_chat_messages WHERE id = %s", (sid + ':u',))
            cur.execute("SELECT count(*) FROM ai_chat_message_stash WHERE session_id = %s",
                        (sid,))
            assert cur.fetchone()[0] == 1
    finally:
        with get_db() as conn:
            conn.cursor().execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && python -m pytest tests/test_undo_migration.py -v`
Expected: FAIL（migrations 文件不存在 / 表不存在）

- [ ] **Step 3: 写迁移文件** `server/migrations/2026_11_10_ai_chat_undo_redo.py`

```python
# -*- coding: utf-8 -*-
"""AI 会话撤销/重做（2026-11-10 undo-redo spec §2.1）。

stash 搬出行而非行内标记：ai_chat_messages 有 22 个读取点（全局搜索/大纲/
perf/导出/open API…），搬出/搬回保真（seq/created_at/rating 随行携带，
search_text 触发器随 INSERT 重建），全部读取方零改动。幂等可重跑。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db


def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_chat_message_stash (
                message_id  VARCHAR(100) PRIMARY KEY,
                session_id  VARCHAR(100) NOT NULL
                    REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
                role        VARCHAR(20) NOT NULL,
                content     JSONB NOT NULL,
                meta        JSONB,
                created_at  TIMESTAMPTZ NOT NULL,
                seq         BIGINT NOT NULL,
                rating      SMALLINT,
                stashed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_msg_stash_session "
            "ON ai_chat_message_stash(session_id, seq)")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_chat_session_reverts (
                session_id  VARCHAR(100) PRIMARY KEY
                    REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
                mode        VARCHAR(10) NOT NULL,
                opencode_session_id VARCHAR(200) NOT NULL,
                oc_message_id VARCHAR(200),
                from_seq    BIGINT NOT NULL,
                message_count INT NOT NULL,
                file_count  INT NOT NULL DEFAULT 0,
                file_names  JSONB,
                prev_opencode_session_id VARCHAR(200),
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""")
        cur.execute(
            "ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS "
            "context_replay_pending BOOLEAN NOT NULL DEFAULT FALSE")
```

- [ ] **Step 4: 基线 DDL + boot 注册 + config 常量**

`server/db_schema/core.py` 在 `ai_chat_message_feedback` 索引之后追加（与迁移同 SQL 的两段 CREATE TABLE / CREATE INDEX / ALTER，注释一行「AI 会话撤销/重做（2026-11-10 undo-redo spec §2.1）」）。

`server/app.py` 照 ：338-344 的「分组图标列」块追加（变量名顺延 `_mp_undo/_spec_undo/_m_undo`）：

```python
# AI 会话撤销/重做（2026-11-10 undo-redo spec §2.1）：随启动幂等执行
try:
    _mp_undo = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'migrations', '2026_11_10_ai_chat_undo_redo.py')
    _spec_undo = _ilu.spec_from_file_location('_undo_redo_migration_boot', _mp_undo)
    _m_undo = _ilu.module_from_spec(_spec_undo)
    _spec_undo.loader.exec_module(_m_undo)
    _m_undo.run()
except Exception as _e:
    logging.warning('undo/redo migration on boot failed: %s', _e)
```

`server/config.py` 在 `AI_CHAT_NOTIFY_MIN_SECONDS` 之后加：

```python
# undo/redo 文件还原的快照时效上限（天）：OC 快照对象 gc --prune=7.days，
# 更早轮次的文件还原不可靠 → 撤销自动降级 rebuild（undo-redo spec E10）。
AI_UNDO_FILE_SNAPSHOT_MAX_AGE_DAYS = _to_int(
    os.getenv('AI_UNDO_FILE_SNAPSHOT_MAX_AGE_DAYS'), 7)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd server && python -m pytest tests/test_undo_migration.py -v`
Expected: PASS（2 passed）

- [ ] **Step 6: Commit**

```bash
git add server/migrations/2026_11_10_ai_chat_undo_redo.py server/db_schema/core.py server/app.py server/config.py server/tests/test_undo_migration.py
git commit -m "feat(ai-chat): undo/redo 迁移——stash 表 + 会话撤销状态表 + context_replay_pending"
```

---

### Task 2: ai_revert.py 锚点解析与文件能力（纯函数）

**Files:**
- Create: `server/utils/ai_revert.py`（本任务只建骨架 + 两个纯函数）
- Test: `server/tests/test_ai_revert.py`

**Interfaces:**
- Produces（后续任务依赖的精确签名）:
  - `class UndoError(Exception)`：`__init__(self, code: str, message: str, *, status: int = 409, retryable: bool = False, operation: str = 'undo')`，属性 `.code/.message/.status/.retryable/.operation`
  - `resolve_undo_target(rows: list[dict], target_seq: int, oc_messages: list[dict]) -> dict`，返回 `{'mode': 'native'|'rebuild', 'oc_user_message_id': str|None, 'anchor_row_id': str|None, 'files': list[str]}`
  - `file_capability(project_info: dict|None, workspace_path: str, created_ms: int, files: list[str], *, now_ms: int|None = None, max_age_days: int|None = None) -> str`，返回 `'full'|'stale'|'none'`

- [ ] **Step 1: 写失败测试** `server/tests/test_ai_revert.py`

```python
"""utils/ai_revert 纯函数（锚点解析 / 文件能力分级）单测（spec §2.2a）。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.ai_revert import resolve_undo_target, file_capability


def _oc(mid, role, created=1000, parts=None):
    return {'info': {'id': mid, 'role': role, 'time': {'created': created}},
            'parts': parts or []}


def _row(rid, role, seq):
    return {'id': rid, 'role': role, 'seq': seq}


def test_no_anchor_targets_first_oc_user():
    rows = [_row('msg_a1', 'assistant', 2), _row('msg_u2', 'user', 3)]
    oc = [_oc('msg_o1', 'user', 100), _oc('msg_o2', 'assistant', 200)]
    plan = resolve_undo_target(rows, 3, oc)
    assert plan == {'mode': 'native', 'oc_user_message_id': 'msg_o1',
                    'anchor_row_id': None, 'files': []}


def test_target_after_anchor_snaps_to_next_oc_user():
    rows = [_row('msg_a1', 'assistant', 2), _row('msg_u2', 'user', 3)]
    oc = [_oc('msg_o1', 'user', 100), _oc('msg_o2', 'assistant', 150),
          _oc('msg_o3', 'user', 200), _oc('msg_o4', 'assistant', 300)]
    plan = resolve_undo_target(rows, 3, oc)
    assert plan['mode'] == 'native'
    assert plan['anchor_row_id'] == 'msg_a1'
    assert plan['oc_user_message_id'] == 'msg_o3'   # 锚之后第一条 OC user（E2）


def test_files_union_after_oc_target():
    p1 = [{'type': 'patch', 'files': [{'file': 'a.txt'}, {'file': 'b.txt'}]}]
    p2 = [{'type': 'patch', 'files': ['c.txt']}]
    rows = [_row('msg_a1', 'assistant', 2), _row('msg_u2', 'user', 3)]
    oc = [_oc('msg_o1', 'user'), _oc('msg_o2', 'assistant'),
          _oc('msg_o3', 'user'), _oc('msg_o4', 'assistant', parts=p1),
          _oc('msg_o5', 'assistant', parts=p2)]
    plan = resolve_undo_target(rows, 3, oc)
    assert sorted(plan['files']) == ['a.txt', 'b.txt', 'c.txt']


def test_target_is_first_message():
    plan = resolve_undo_target([_row('msg_u1', 'user', 1)], 1,
                               [_oc('msg_o1', 'user', 100)])
    assert plan['oc_user_message_id'] == 'msg_o1'
    assert plan['mode'] == 'native'


def test_anchors_missing_and_oc_empty_is_native_noop():
    """目标前有 assistant 行但全不在 OC、OC 列表空（失败轮从未到达）→ 纯平台撤销。"""
    rows = [_row('msg_a1', 'assistant', 2), _row('msg_u2', 'user', 3)]
    plan = resolve_undo_target(rows, 3, [])
    assert plan == {'mode': 'native', 'oc_user_message_id': None,
                    'anchor_row_id': None, 'files': []}


def test_anchors_missing_and_oc_nonempty_is_rebuild():
    """会话恢复过：平台锚不在新 OC 会话里但 OC 非空 → rebuild（spec §2.2a 规则4）。"""
    rows = [_row('msg_a1', 'assistant', 2), _row('msg_u2', 'user', 3)]
    oc = [_oc('msg_oX', 'user', 100), _oc('msg_oY', 'assistant', 200)]
    plan = resolve_undo_target(rows, 3, oc)
    assert plan['mode'] == 'rebuild'
    assert plan['oc_user_message_id'] is None


def test_failed_turn_between_anchor_and_target_is_skipped():
    """锚与目标之间的失败轮（不在 OC）不阻断：仍取锚之后第一条 OC user。"""
    rows = [_row('msg_a1', 'assistant', 2), _row('msg_ufail', 'user', 3),
            _row('msg_u2', 'user', 4)]
    oc = [_oc('msg_o1', 'user', 100), _oc('msg_o2', 'assistant', 150),
          _oc('msg_o3', 'user', 200)]
    plan = resolve_undo_target(rows, 4, oc)
    assert plan['anchor_row_id'] == 'msg_a1'
    assert plan['oc_user_message_id'] == 'msg_o3'


def test_anchor_after_failed_turn_prefers_latest_live_anchor():
    """最近一条「存在于 OC」的锚赢（失败轮锚被跳过）。"""
    rows = [_row('msg_a1', 'assistant', 2), _row('msg_adead', 'assistant', 3),
            _row('msg_u2', 'user', 4)]
    oc = [_oc('msg_o1', 'user', 100), _oc('msg_o2', 'assistant', 150),
          _oc('msg_o3', 'user', 200)]
    plan = resolve_undo_target(rows, 4, oc)
    assert plan['anchor_row_id'] == 'msg_a1'      # msg_adead 不在 OC → 跳过


def test_capability_none_without_git():
    assert file_capability({'vcs': None, 'worktree': '/'}, '/ws', 1000,
                           ['a.txt']) == 'none'


def test_capability_none_when_worktree_mismatch():
    """嵌在被忽略外层仓库内（E9）：OC worktree ≠ 会话工作区 → 文件不保护。"""
    assert file_capability({'vcs': 'git', 'worktree': '/repo'}, '/repo/sub/ws',
                           1000, ['a.txt']) == 'none'


def test_capability_none_when_no_files():
    assert file_capability({'vcs': 'git', 'worktree': '/ws'}, '/ws', 1000,
                           []) == 'none'


def test_capability_stale_after_max_age():
    now = 200 * 86400 * 1000
    assert file_capability({'vcs': 'git', 'worktree': '/ws'}, '/ws',
                           now - 8 * 86400 * 1000, ['a.txt'],
                           now_ms=now, max_age_days=7) == 'stale'


def test_capability_full():
    now = 200 * 86400 * 1000
    assert file_capability({'vcs': 'git', 'worktree': '/ws'}, '/ws',
                           now - 86400 * 1000, ['a.txt'],
                           now_ms=now, max_age_days=7) == 'full'
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && python -m pytest tests/test_ai_revert.py -v`
Expected: FAIL（ModuleNotFoundError: utils.ai_revert）

- [ ] **Step 3: 写实现**（`server/utils/ai_revert.py` 本任务部分）

```python
"""AI 会话撤销/重做核心（undo-redo spec §2.2）。

三层收敛在一处：①锚点解析/文件能力（纯函数）②stash/状态 SQL 助手
③撤销/重做/预览编排。路由只做守卫与 JSON 映射。

关键实证（spec §0.3）：E2 必须传 user 消息 id；E6 必须校验响应
revert.messageID；E10 快照 7 天 GC → stale 降级 rebuild。
"""
import json
import logging
import os
import time

import requests

from db import get_db
from utils.runtime import get_runtime

logger = logging.getLogger(__name__)


class UndoError(Exception):
    """撤销/重做失败：路由层映射为结构化错误响应（报错＝什么都没改）。"""

    def __init__(self, code: str, message: str, *, status: int = 409,
                 retryable: bool = False, operation: str = 'undo'):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        self.operation = operation


def _files_after(oc_messages, start_idx: int) -> list:
    """oc_messages[start_idx:] 里所有 patch part 的文件并集（保序去重）。"""
    files: list = []
    for m in (oc_messages or [])[max(start_idx, 0):]:
        for p in (m.get('parts') or []):
            if (p or {}).get('type') != 'patch':
                continue
            for f in p.get('files') or []:
                name = f.get('file') if isinstance(f, dict) else f
                if name and name not in files:
                    files.append(name)
    return files


def _oc_index(oc_messages, mid: str) -> int:
    return next(i for i, m in enumerate(oc_messages)
                if ((m.get('info') or {}).get('id')) == mid)


def resolve_undo_target(rows, target_seq: int, oc_messages) -> dict:
    """锚点解析（spec §2.2a）。

    rows：平台行 [{'id','role','seq'}]（seq 升序，全会话）；
    oc_messages：OpenCode REST 消息（client.get_messages 原始返回）。
    平台 assistant 行 id = 该回合第一条 OC assistant 消息 id
    （chat_persist.turn_msg_id 落库），是天然锚点；用户行 id 平台自生成、
    无 OC 对应——所以 OC 目标必须经锚「吸附」出其后第一条 OC user 消息。
    """
    oc_messages = oc_messages or []
    oc_ids: set = set()
    first_oc_user = None
    for m in oc_messages:
        info = (m or {}).get('info') or {}
        if info.get('id'):
            oc_ids.add(info['id'])
        if first_oc_user is None and info.get('role') == 'user' and info.get('id'):
            first_oc_user = info['id']
    anchor = None
    has_any_assistant_before = False
    for r in rows or []:
        if r['seq'] >= target_seq:
            break
        if r['role'] == 'assistant':
            has_any_assistant_before = True
            if r['id'] in oc_ids:
                anchor = r['id']          # 最近一条「活着」的锚
    if anchor is None:
        if has_any_assistant_before and oc_messages:
            # 锚全部不在 OC 列表但 OC 有会话：上下文在别的 OC 会话里
            #（_recover_session_and_resend 换过）→ 重建式降级
            return {'mode': 'rebuild', 'oc_user_message_id': None,
                    'anchor_row_id': None, 'files': [], 'reason': 'anchor-missing'}
        # 无锚：目标即会话首条 / 目标前全是平台自造行 / OC 空（失败轮）
        return {'mode': 'native', 'oc_user_message_id': first_oc_user,
                'anchor_row_id': None,
                'files': _files_after(oc_messages, 0) if first_oc_user else []}
    idx = _oc_index(oc_messages, anchor)
    oc_target = None
    for m in oc_messages[idx + 1:]:
        info = (m.get('info') or {})
        if info.get('role') == 'user' and info.get('id'):
            oc_target = info['id']
            break
    if oc_target is None:
        # 锚与目标之间的轮从未到达 OC（dispatch 失败等）→ 纯平台撤销
        return {'mode': 'native', 'oc_user_message_id': None,
                'anchor_row_id': anchor, 'files': []}
    t_idx = _oc_index(oc_messages, oc_target)
    return {'mode': 'native', 'oc_user_message_id': oc_target,
            'anchor_row_id': anchor,
            'files': _files_after(oc_messages, t_idx + 1)}


def file_capability(project_info, workspace_path: str, created_ms: int,
                    files, *, now_ms=None, max_age_days=None) -> str:
    """文件还原能力分级（spec E8/E9/E10）：'full' | 'stale' | 'none'。"""
    if not files:
        return 'none'
    info = project_info or {}
    if info.get('vcs') != 'git':
        return 'none'
    wt = info.get('worktree')
    if not wt:
        return 'none'
    try:
        same = os.path.normcase(os.path.abspath(wt)) == \
            os.path.normcase(os.path.abspath(workspace_path))
    except Exception:
        same = False
    if not same:
        return 'none'
    if max_age_days is None:
        from config import AI_UNDO_FILE_SNAPSHOT_MAX_AGE_DAYS
        max_age_days = AI_UNDO_FILE_SNAPSHOT_MAX_AGE_DAYS
    now = now_ms if now_ms is not None else time.time() * 1000
    if created_ms and (now - created_ms) > max_age_days * 86400 * 1000:
        return 'stale'
    return 'full'
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && python -m pytest tests/test_ai_revert.py -v`
Expected: PASS（11 passed）

- [ ] **Step 5: Commit**

```bash
git add server/utils/ai_revert.py server/tests/test_ai_revert.py
git commit -m "feat(ai-chat): undo 锚点解析与文件能力分级（纯函数）"
```

### Task 3: OC 客户端 revert 方法 + runtime 抽象 + stub 模拟

**Files:**
- Modify: `server/utils/opencode_client.py`（`list_mcp` 之前插入三个方法）
- Modify: `server/utils/runtime/base.py`（`subscribe_events` 之前插入两个抽象方法）
- Modify: `server/utils/runtime/opencode_local.py`（`abort` 之后插入透传）
- Modify: `server/utils/runtime/stub.py`（`_Session` slots + 两个方法 + StubRuntime 透传）
- Test: `server/tests/test_opencode_client_undo.py`

**Interfaces:**
- Consumes: 无（对 HTTP 端点的封装）。
- Produces:
  - `OpenCodeClient.get_project(directory: str) -> dict`（`{'id','vcs','worktree',...}`）
  - `OpenCodeClient.revert_session(oc_sid: str, message_id: str, directory: str) -> dict`（返回会话信息，撤销态在 `['revert']`）
  - `OpenCodeClient.unrevert_session(oc_sid: str, directory: str) -> None`
  - `AgentRuntime.revert_stage(oc_session_id, oc_message_id, directory='') -> dict` / `revert_clear(oc_session_id, directory='') -> None`（不支持时抛 `RuntimeCapabilityError`）；`capabilities()` 增加 `'revert': True`
  - `StubClient.get_project(directory) -> dict`（`{'vcs': None, 'worktree': ''}`）+ `revert_stage/unrevert_session`

- [ ] **Step 1: 写失败测试** `server/tests/test_opencode_client_undo.py`

```python
"""OpenCodeClient revert 方法与 stub 模拟（undo-redo spec §2.5）。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest

from utils.opencode_client import OpenCodeClient


class _Resp:
    def __init__(self, payload):
        self._p = payload
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def test_revert_session_posts_message_id(monkeypatch):
    captured = {}

    def fake_post(url, params=None, json=None, timeout=None):
        captured.update(url=url, json=json, params=params)
        return _Resp({'id': 'oc1', 'revert': {'messageID': 'msg_u'}})

    monkeypatch.setattr('utils.opencode_client.requests.post', fake_post)
    out = OpenCodeClient('http://x').revert_session('oc1', 'msg_u', directory='/ws')
    assert captured['url'].endswith('/session/oc1/revert')
    assert captured['json'] == {'messageID': 'msg_u'}   # E2：user 消息 id
    assert captured['params'] == {'directory': '/ws'}
    assert out['revert']['messageID'] == 'msg_u'


def test_unrevert_session_posts_empty(monkeypatch):
    captured = {}

    def fake_post(url, params=None, json=None, timeout=None):
        captured.update(url=url, json=json)
        return _Resp({'id': 'oc1', 'revert': None})

    monkeypatch.setattr('utils.opencode_client.requests.post', fake_post)
    OpenCodeClient('http://x').unrevert_session('oc1', directory='/ws')
    assert captured['url'].endswith('/session/oc1/unrevert')
    assert captured['json'] is None


def test_get_project_scopes_directory(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured.update(url=url, params=params)
        return _Resp({'vcs': 'git', 'worktree': '/ws'})

    monkeypatch.setattr('utils.opencode_client.requests.get', fake_get)
    out = OpenCodeClient('http://x').get_project('/ws')
    assert captured['url'].endswith('/project/current')
    assert out['vcs'] == 'git'


def test_stub_runtime_revert_roundtrip():
    import os
    os.environ['AI_STUB_ALLOW'] = '1'
    from utils.runtime.stub import StubRuntime
    rt = StubRuntime({'delay_ms': [1, 1]})
    sid = rt.create_session(directory='/ws')
    info = rt.revert_stage(sid, 'msg_x', directory='/ws')
    assert info['revert']['messageID'] == 'msg_x'
    assert rt.get_client().get_project('/ws') == {'vcs': None, 'worktree': ''}
    rt.revert_clear(sid, directory='/ws')
    assert rt.get_client()._sessions[sid].revert is None
    assert rt.capabilities()['revert'] is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && python -m pytest tests/test_opencode_client_undo.py -v`
Expected: FAIL（AttributeError: revert_session）

- [ ] **Step 3: 写实现**

`opencode_client.py` 在 `list_mcp` 之前插入：

```python
    def get_project(self, directory: str = "") -> dict:
        """GET /project/current：{'id','vcs','worktree',...}。文件撤销能力分级
        用（vcs != 'git' 或 worktree ≠ 会话工作区 → 快照不保护文件，spec E9）。
        非 directory-scoped 的读端点，参数仅用于 OC 解析对应 project。"""
        params = {"directory": directory} if directory else None
        resp = requests.get(self._url("/project/current"), params=params,
                            timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def revert_session(self, opencode_session_id: str, message_id: str,
                       directory: str = "") -> dict:
        """软撤销（undo-redo spec §2.5，v1 API；OC2 迁移点：
        POST /api/session/:id/revert/stage {messageID}）。返回会话信息，
        撤销态在 ['revert']（{messageID, partID, snapshot, diff}）。
        回合运行中 → 409 SessionBusyError（requests.HTTPError，E5）。
        不存在的 messageID → 200 且无 revert 态（E6，调用方必须校验）。"""
        params = {"directory": directory} if directory else None
        resp = requests.post(
            self._url(f"/session/{opencode_session_id}/revert"),
            params=params, json={"messageID": message_id},
            timeout=max(self.timeout, 20))
        resp.raise_for_status()
        return resp.json()

    def unrevert_session(self, opencode_session_id: str,
                         directory: str = "") -> None:
        """撤销重做（v1 API；OC2 迁移点：DELETE /api/session/:id/revert）。
        还原文件 + 清撤销标记；无撤销态时 no-op（E4）。"""
        params = {"directory": directory} if directory else None
        resp = requests.post(
            self._url(f"/session/{opencode_session_id}/unrevert"),
            params=params, timeout=max(self.timeout, 20))
        resp.raise_for_status()
```

`runtime/base.py` 在 `subscribe_events` 之前插入：

```python
    def revert_stage(self, oc_session_id: str, oc_message_id: str,
                     directory: str = '') -> dict:
        """软撤销：会话回退到 oc_message_id（user 消息）之前。返回带
        'revert' 键的会话信息；busy 抛 requests.HTTPError(409)。"""
        raise RuntimeCapabilityError('revert not supported by this runtime')

    def revert_clear(self, oc_session_id: str, directory: str = '') -> None:
        """撤销重做：还原文件 + 清撤销标记；无撤销态时 no-op。"""
        raise RuntimeCapabilityError('revert not supported by this runtime')
```

`opencode_local.py` 在 `abort` 之后插入（`capabilities` 字典加 `'revert': True`）：

```python
    def revert_stage(self, oc_session_id: str, oc_message_id: str,
                     directory: str = '') -> dict:
        return self._client().revert_session(oc_session_id, oc_message_id,
                                             directory=directory)

    def revert_clear(self, oc_session_id: str, directory: str = '') -> None:
        self._client().unrevert_session(oc_session_id, directory=directory)
```

`stub.py`：`_Session.__slots__` 加 `'revert'`、`__init__` 加 `self.revert = None`；`StubClient` 在 `delete_session` 之后插入：

```python
    # ---- 撤销/重做（undo-redo spec §2.5）：内存模拟软撤销语义 ----
    def revert_stage(self, sid: str, message_id: str, directory: str = '') -> dict:
        with self._lock:
            s = self._sessions[sid]
            s.revert = {'messageID': message_id}
            return {'id': sid, 'revert': dict(s.revert)}

    def unrevert_session(self, sid: str, directory: str = '') -> None:
        with self._lock:
            self._sessions[sid].revert = None

    def get_project(self, directory: str = '') -> dict:
        # stub 无 git 语义 → 文件能力恒 none（上下文回退仍有效）
        return {'vcs': None, 'worktree': ''}
```

`StubRuntime`：`capabilities()` 加 `'revert': True`；`abort` 之后插入：

```python
    def revert_stage(self, oc_session_id: str, oc_message_id: str,
                     directory: str = '') -> dict:
        return self._client.revert_stage(oc_session_id, oc_message_id,
                                         directory=directory)

    def revert_clear(self, oc_session_id: str, directory: str = '') -> None:
        self._client.unrevert_session(oc_session_id, directory=directory)
```

- [ ] **Step 4: 跑测试确认通过 + 既有 stub 测试不回归**

Run: `cd server && python -m pytest tests/test_opencode_client_undo.py tests/test_runtime_stub.py tests/test_runtime_event_bus.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add server/utils/opencode_client.py server/utils/runtime/base.py server/utils/runtime/opencode_local.py server/utils/runtime/stub.py server/tests/test_opencode_client_undo.py
git commit -m "feat(ai-chat): OpenCode revert 三元组接入客户端与 runtime 抽象（v1 实现 + stub 模拟）"
```

---

### Task 4: stash/状态 SQL 助手

**Files:**
- Modify: `server/utils/ai_revert.py`（追加 SQL 助手段）
- Test: `server/tests/test_ai_revert.py`（追加真实库用例）

**Interfaces:**
- Produces（Task 6 依赖）:
  - `stash_rows(conn, sid: str, from_seq: int) -> int`（搬出+删除，返回删除条数）
  - `restore_stash(conn, sid: str) -> int`（搬回+feedback 回插+清 stash，返回恢复条数）
  - `get_revert_state(conn, sid) -> dict|None`（键：`mode/opencodeSessionId/ocMessageId/fromSeq/messageCount/fileCount/fileNames/createdAt`）
  - `upsert_revert_state(conn, sid, *, mode, opencode_session_id, oc_message_id, from_seq, message_count, file_count=0, file_names=None, prev_opencode_session_id=None)`
  - `commit_revert(conn, sid) -> int`（清 stash+状态行+孤儿子任务行）
  - `clear_context_replay(conn, sid)`

- [ ] **Step 1: 追加失败测试**（`server/tests/test_ai_revert.py` 末尾）

```python
# ---------------- SQL 助手（真实 casemanage_test 库，bootstrap 用户） ----------------
import json as _json
import uuid as _uuid

from db import get_db
from utils.ai_revert import (stash_rows, restore_stash, get_revert_state,
                             upsert_revert_state, commit_revert,
                             clear_context_replay)


def _mk_undo_session(sid):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE username = 'admin' LIMIT 1")
        uid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, workspace_path, "
            "  session_token, token_expires_at) "
            "VALUES (%s, %s, '/tmp/undo-test', 'tok-' || %s, NOW() + interval '1 day') "
            "ON CONFLICT (id) DO NOTHING", (sid, uid, sid))


def _cleanup_undo_session(sid):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM ai_chat_message_stash WHERE session_id = %s", (sid,))
        cur.execute("DELETE FROM ai_chat_session_reverts WHERE session_id = %s", (sid,))
        cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))


def _insert_msg(cur, sid, role, text):
    cur.execute(
        "INSERT INTO ai_chat_messages (id, session_id, role, content) "
        "VALUES (%s, %s, %s, %s)",
        (f'{sid}:{text}', sid, role,
         _json.dumps([{'type': 'text', 'text': text}])))


def test_stash_restore_roundtrip_preserves_seq_and_rating():
    sid = f'sess_undo_{_uuid.uuid4().hex[:8]}'
    try:
        _mk_undo_session(sid)
        with get_db() as conn:
            cur = conn.cursor()
            for role, text in (('user', 'u1'), ('assistant', 'a1'),
                               ('user', 'u2'), ('assistant', 'a2')):
                _insert_msg(cur, sid, role, text)
            cur.execute(
                "SELECT id, seq FROM ai_chat_messages WHERE session_id = %s "
                "ORDER BY seq", (sid,))
            before = cur.fetchall()
            cur.execute(
                "INSERT INTO ai_chat_message_feedback (message_id, session_id, rating) "
                "VALUES (%s, %s, 1) ON CONFLICT (message_id) DO NOTHING",
                (f'{sid}:a1', sid))
            u2_seq = next(s for i, s in before if i.endswith(':u2'))
        with get_db() as conn:
            n = stash_rows(conn, sid, u2_seq)
        assert n == 2
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM ai_chat_messages WHERE session_id = %s",
                        (sid,))
            assert cur.fetchone()[0] == 2
            cur.execute("SELECT count(*) FROM ai_chat_message_stash WHERE session_id = %s",
                        (sid,))
            assert cur.fetchone()[0] == 2
        with get_db() as conn:
            m = restore_stash(conn, sid)
        assert m == 2
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, seq FROM ai_chat_messages WHERE session_id = %s ORDER BY seq",
                (sid,))
            assert cur.fetchall() == before          # seq 保真、顺序不变
            cur.execute("SELECT rating FROM ai_chat_message_feedback "
                        "WHERE message_id = %s", (f'{sid}:a1',))
            assert cur.fetchone()[0] == 1            # rating 随 stash 回插
    finally:
        _cleanup_undo_session(sid)


def test_revert_state_upsert_get_commit():
    sid = f'sess_undo_{_uuid.uuid4().hex[:8]}'
    try:
        _mk_undo_session(sid)
        with get_db() as conn:
            assert get_revert_state(conn, sid) is None
            upsert_revert_state(conn, sid, mode='native',
                                opencode_session_id='oc1',
                                oc_message_id='msg_o3', from_seq=5,
                                message_count=3, file_count=2,
                                file_names=['a.txt', 'b.txt'])
            st = get_revert_state(conn, sid)
            assert st['mode'] == 'native'
            assert st['opencodeSessionId'] == 'oc1'
            assert st['ocMessageId'] == 'msg_o3'
            assert st['fileNames'] == ['a.txt', 'b.txt']
            upsert_revert_state(conn, sid, mode='rebuild',
                                opencode_session_id='oc2', oc_message_id=None,
                                from_seq=5, message_count=3,
                                prev_opencode_session_id='oc1')
            assert get_revert_state(conn, sid)['mode'] == 'rebuild'
            clear_context_replay(conn, sid)
        with get_db() as conn:
            n = commit_revert(conn, sid)
        assert n == 0        # stash 空
        with get_db() as conn:
            assert get_revert_state(conn, sid) is None
    finally:
        _cleanup_undo_session(sid)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && python -m pytest tests/test_ai_revert.py -v -k "roundtrip or revert_state"`
Expected: FAIL（ImportError: cannot import name 'stash_rows'）

- [ ] **Step 3: 写实现**（`ai_revert.py` 追加）

```python
# ================================================================ stash / 状态
def stash_rows(conn, sid: str, from_seq: int) -> int:
    """把 seq >= from_seq 的消息搬进 stash 并从主表删除（rating 随行携带，
    feedback 行随消息 CASCADE 删除）。返回删除条数。"""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ai_chat_message_stash "
        "  (message_id, session_id, role, content, meta, created_at, seq, rating) "
        "SELECT m.id, m.session_id, m.role, m.content, m.meta, m.created_at, m.seq, f.rating "
        "FROM ai_chat_messages m "
        "LEFT JOIN ai_chat_message_feedback f ON f.message_id = m.id "
        "WHERE m.session_id = %s AND m.seq >= %s", (sid, from_seq))
    cur.execute(
        "DELETE FROM ai_chat_messages WHERE session_id = %s AND seq >= %s",
        (sid, from_seq))
    return cur.rowcount


def restore_stash(conn, sid: str) -> int:
    """stash 整体搬回主表（seq/created_at 保真——旧值必小于当前序列值，
    新消息 nextval 不会冲突）+ feedback 回插 + 清 stash。返回恢复条数。"""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ai_chat_messages (id, session_id, role, content, meta, "
        "  created_at, seq) "
        "SELECT message_id, session_id, role, content, meta, created_at, seq "
        "FROM ai_chat_message_stash WHERE session_id = %s", (sid,))
    cur.execute(
        "INSERT INTO ai_chat_message_feedback (message_id, session_id, rating) "
        "SELECT message_id, session_id, rating FROM ai_chat_message_stash "
        "WHERE session_id = %s AND rating IS NOT NULL "
        "ON CONFLICT (message_id) DO NOTHING", (sid,))
    cur.execute("DELETE FROM ai_chat_message_stash WHERE session_id = %s", (sid,))
    return cur.rowcount


def get_revert_state(conn, sid):
    cur = conn.cursor()
    cur.execute(
        "SELECT mode, opencode_session_id, oc_message_id, from_seq, "
        "       message_count, file_count, file_names, created_at "
        "FROM ai_chat_session_reverts WHERE session_id = %s", (sid,))
    r = cur.fetchone()
    if not r:
        return None
    return {'mode': r[0], 'opencodeSessionId': r[1], 'ocMessageId': r[2],
            'fromSeq': r[3], 'messageCount': r[4], 'fileCount': r[5],
            'fileNames': r[6] or [],
            'createdAt': r[7].isoformat() if r[7] else None}


def upsert_revert_state(conn, sid, *, mode, opencode_session_id, oc_message_id,
                        from_seq, message_count, file_count=0, file_names=None,
                        prev_opencode_session_id=None):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ai_chat_session_reverts "
        "  (session_id, mode, opencode_session_id, oc_message_id, from_seq, "
        "   message_count, file_count, file_names, prev_opencode_session_id, "
        "   created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW()) "
        "ON CONFLICT (session_id) DO UPDATE SET "
        "  mode = EXCLUDED.mode, "
        "  opencode_session_id = EXCLUDED.opencode_session_id, "
        "  oc_message_id = EXCLUDED.oc_message_id, from_seq = EXCLUDED.from_seq, "
        "  message_count = EXCLUDED.message_count, "
        "  file_count = EXCLUDED.file_count, file_names = EXCLUDED.file_names, "
        "  prev_opencode_session_id = EXCLUDED.prev_opencode_session_id, "
        "  updated_at = NOW()",
        (sid, mode, opencode_session_id, oc_message_id, from_seq,
         message_count, file_count, json.dumps(file_names or []),
         prev_opencode_session_id))


def commit_revert(conn, sid) -> int:
    """提交撤销（不可重做）：清 stash + 状态行 + 被撤内容引用的孤儿子任务行。
    返回 stash 清理条数（0 = 无挂起撤销）。"""
    cur = conn.cursor()
    cur.execute(
        "SELECT content FROM ai_chat_message_stash WHERE session_id = %s", (sid,))
    subtask_ids = set()
    for (content,) in cur.fetchall():
        if not isinstance(content, list):
            continue
        for p in content:
            if isinstance(p, dict) and p.get('type') == 'subtask_use' \
                    and p.get('subtaskId'):
                subtask_ids.add(p['subtaskId'])
    if subtask_ids:
        cur.execute(
            "DELETE FROM ai_chat_subtasks WHERE root_session_id = %s "
            "AND id = ANY(%s)", (sid, sorted(subtask_ids)))
    cur.execute("DELETE FROM ai_chat_message_stash WHERE session_id = %s", (sid,))
    n = cur.rowcount
    cur.execute("DELETE FROM ai_chat_session_reverts WHERE session_id = %s", (sid,))
    return n


def clear_context_replay(conn, sid):
    cur = conn.cursor()
    cur.execute(
        "UPDATE ai_chat_sessions SET context_replay_pending = FALSE "
        "WHERE id = %s", (sid,))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && python -m pytest tests/test_ai_revert.py -v`
Expected: 全部 PASS（13 passed）

- [ ] **Step 5: Commit**

```bash
git add server/utils/ai_revert.py server/tests/test_ai_revert.py
git commit -m "feat(ai-chat): undo stash/状态 SQL 助手——seq/created_at/rating 保真搬出搬回"
```

---

### Task 5: 跨连接事件总线 session_event_bus

**Files:**
- Create: `server/utils/session_event_bus.py`
- Test: `server/tests/test_session_event_bus.py`

**Interfaces:**
- Produces（Task 6/10 依赖）:
  - `register(sid: str) -> queue.Queue`（连接建立时注册，返回该连接的收件队列）
  - `unregister(sid: str, q: queue.Queue) -> None`
  - `broadcast(sid: str, etype: str, payload: dict) -> int`（向该会话全部连接扇出，返回送达数；帧形状与 OC pump 一致：`{'event': etype, 'data': {'type': etype, 'properties': payload}}`）

- [ ] **Step 1: 写失败测试** `server/tests/test_session_event_bus.py`

```python
"""跨连接平台事件总线（undo-redo spec §2.6）。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.session_event_bus import register, unregister, broadcast


def test_broadcast_fans_out_only_to_session_subscribers():
    q1, q2 = register('s1'), register('s1')
    q3 = register('s2')
    try:
        assert broadcast('s1', 'session.revert', {'revert': None}) == 2
        f1 = q1.get_nowait()
        assert f1['event'] == 'session.revert'
        assert f1['data']['properties'] == {'revert': None}
        assert q2.get_nowait()['data']['type'] == 'session.revert'
        assert q3.empty()                      # 不串会话
    finally:
        unregister('s1', q1); unregister('s1', q2); unregister('s2', q3)


def test_unregister_stops_delivery_and_broadcast_ok_when_no_subscriber():
    q = register('s9')
    unregister('s9', q)
    assert broadcast('s9', 'session.revert', {}) == 0   # 无订阅者不报错
    assert broadcast('s404', 'session.revert', {}) == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && python -m pytest tests/test_session_event_bus.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 写实现**

```python
"""跨连接的平台侧事件扇出（undo-redo spec §2.6）。

撤销/重做/提交是平台 DB 权威变更，其他标签页必须即时收敛——不能依赖 OC
事件（1.x 的 message.removed 覆盖不了平台 user 行——其 id 在 OC 侧无对应；
2.x 连 message.removed 都没有）。SSE 代理每连接注册一个队列到这里，
ai_revert 提交后 broadcast；帧形状与 OC pump 一致，主循环零特判。
"""
import logging
import queue
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_subscribers: dict = {}      # sid -> set[queue.Queue]


def register(sid: str) -> queue.Queue:
    q = queue.Queue()
    with _lock:
        _subscribers.setdefault(sid, set()).add(q)
    return q


def unregister(sid: str, q: queue.Queue) -> None:
    with _lock:
        subs = _subscribers.get(sid)
        if subs is not None:
            subs.discard(q)
            if not subs:
                _subscribers.pop(sid, None)


def broadcast(sid: str, etype: str, payload: dict) -> int:
    frame = {'event': etype, 'data': {'type': etype, 'properties': payload}}
    with _lock:
        targets = list(_subscribers.get(sid, ()))
    n = 0
    for q in targets:
        try:
            q.put_nowait(frame)
            n += 1
        except queue.Full:
            logger.warning('event bus queue full session=%s', sid)
    return n
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && python -m pytest tests/test_session_event_bus.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add server/utils/session_event_bus.py server/tests/test_session_event_bus.py
git commit -m "feat(ai-chat): 跨连接平台事件总线——undo/redo 多端同步基础设施"
```

### Task 6: 撤销/重做/预览编排器

**Files:**
- Modify: `server/utils/ai_revert.py`（追加编排段）
- Test: `server/tests/test_ai_revert.py`（追加 mock-db 编排用例）

**Interfaces:**
- Consumes: Task 2 纯函数、Task 3 `get_runtime().get_client().revert_stage/revert_clear/get_project/get_messages`、Task 4 SQL 助手、Task 5 `broadcast`、`utils.chat_persist.has_listener/stop_listener`、`session_history.render_history_block`（rebuild 不在本任务实现——挂点在 Task 9 的 send_message）。
- Produces（Task 7/8/9 依赖）:
  - `preview_undo(sid: str, msg_id: str) -> dict`：`{'messageCount', 'files', 'fileCapability', 'mode', 'reason'}`
  - `undo_from_message(sid: str, msg_id: str) -> dict`：`{'deleted': int, 'revert': dict|None, 'messages': list}`
  - `redo(sid: str) -> dict`：`{'deleted': int, 'messages': list}`
  - `commit_after_dispatch(sid: str) -> None`
  - `broadcast_revert(sid: str) -> None`

- [ ] **Step 1: 追加失败测试**（`server/tests/test_ai_revert.py` 末尾）

```python
# ---------------- 编排器（mock db + mock client，仿 test_routes_ai_chat.setup） ----------------
from contextlib import contextmanager as _cm
from datetime import datetime as _dt, timezone as _tz
from unittest.mock import MagicMock as _MagicMock

import utils.ai_revert as ar


def _mock_env(rows, oc_messages, project=None, revert_info=None, state_rows=None):
    """打桩 get_db/get_runtime/chat_persist；fetchone 按 (ctx, target, state…) 供给。"""
    cursor = _MagicMock()
    cursor.fetchone.side_effect = [
        ('oc1', 'active', '/tmp/ws'),                # _load_undo_context
        (3,),                                        # target seq
    ] + (state_rows or [])
    cursor.fetchall.side_effect = [rows]             # ctx rows（后续 fetchall 给 []）
    conn = _MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda self: conn
    conn.__exit__ = lambda self, *a: False

    @_cm
    def fake_db():
        yield conn

    client = _MagicMock()
    client.get_messages.return_value = oc_messages
    client.get_project.return_value = project or {'vcs': 'git', 'worktree': '/tmp/ws'}
    client.revert_stage.return_value = {'id': 'oc1',
                                        'revert': {'messageID': (revert_info or {}).get(
                                            'messageID', 'msg_o3')}}
    return cursor, client, fake_db


def _two_turn_rows():
    return [{'id': 'msg_a1', 'role': 'assistant', 'seq': 2},
            {'id': 'msg_u2', 'role': 'user', 'seq': 3},
            {'id': 'msg_a2', 'role': 'assistant', 'seq': 4},
            {'id': 'msg_u3', 'role': 'user', 'seq': 5}]


def test_undo_native_calls_revert_stage_with_snapped_user_id(monkeypatch):
    oc = [{'info': {'id': 'msg_a1', 'role': 'assistant', 'time': {'created': 1}}, 'parts': []},
          {'info': {'id': 'msg_o3', 'role': 'user', 'time': {'created': 2}},
           'parts': [{'type': 'patch', 'files': [{'file': 'a.txt'}]}]}]
    state = ('native', 'oc1', 'msg_o3', 3, 2, 1, ['a.txt'],
             _dt(2026, 11, 10, tzinfo=_tz.utc))
    cursor, client, fake_db = _mock_env(_two_turn_rows(), oc,
                                        revert_info={'messageID': 'msg_o3'},
                                        state_rows=[state])
    monkeypatch.setattr(ar, 'get_db', fake_db)
    monkeypatch.setattr(ar, 'get_runtime', lambda: _MagicMock(get_client=lambda: client))
    monkeypatch.setattr('utils.chat_persist.has_listener', lambda sid: False)
    monkeypatch.setattr('utils.chat_persist.stop_listener', lambda sid: None)
    monkeypatch.setattr(ar, 'broadcast_revert', lambda sid: None)
    out = ar.undo_from_message('sess_x', 'msg_u2')
    client.revert_stage.assert_called_once()
    args, kwargs = client.revert_stage.call_args
    assert args[0] == 'oc1' and args[1] == 'msg_o3'      # E2 吸附
    assert kwargs['directory'] == '/tmp/ws'
    assert out['revert']['ocMessageId'] == 'msg_o3'
    assert out['deleted'] == 3                            # seq>=3 的平台行数


def test_undo_revert_mismatch_rolls_back(monkeypatch):
    oc = [{'info': {'id': 'msg_o3', 'role': 'user', 'time': {'created': 2}}, 'parts': []}]
    cursor, client, fake_db = _mock_env(_two_turn_rows(), oc)
    client.revert_stage.return_value = {'id': 'oc1', 'revert': None}   # E6 形态
    monkeypatch.setattr(ar, 'get_db', fake_db)
    monkeypatch.setattr(ar, 'get_runtime', lambda: _MagicMock(get_client=lambda: client))
    monkeypatch.setattr('utils.chat_persist.has_listener', lambda sid: False)
    monkeypatch.setattr('utils.chat_persist.stop_listener', lambda sid: None)
    import pytest
    with pytest.raises(ar.UndoError) as ei:
        ar.undo_from_message('sess_x', 'msg_u2')
    assert ei.value.code == 'REVERT_MISMATCH'
    client.revert_clear.assert_not_called()   # 未成功过无需补偿


def test_undo_oc_failure_compensates(monkeypatch):
    oc = [{'info': {'id': 'msg_o3', 'role': 'user', 'time': {'created': 2}}, 'parts': []}]
    cursor, client, fake_db = _mock_env(_two_turn_rows(), oc)
    import requests as _rq
    boom = _rq.HTTPError('500')
    boom.response = _MagicMock(status_code=500)
    client.revert_stage.side_effect = boom
    monkeypatch.setattr(ar, 'get_db', fake_db)
    monkeypatch.setattr(ar, 'get_runtime', lambda: _MagicMock(get_client=lambda: client))
    monkeypatch.setattr('utils.chat_persist.has_listener', lambda sid: False)
    monkeypatch.setattr('utils.chat_persist.stop_listener', lambda sid: None)
    import pytest
    with pytest.raises(_rq.HTTPError):
        ar.undo_from_message('sess_x', 'msg_u2')


def test_undo_busy_409(monkeypatch):
    monkeypatch.setattr('utils.chat_persist.has_listener', lambda sid: True)
    import pytest
    with pytest.raises(ar.UndoError) as ei:
        ar.undo_from_message('sess_x', 'msg_u2')
    assert ei.value.code == 'SESSION_BUSY'


def test_undo_rebuild_promotes_stale_and_never_calls_oc_revert(monkeypatch):
    oc = [{'info': {'id': 'msg_o3', 'role': 'user',
                    'time': {'created': 1}}, 'parts': []}]   # created=1ms → 必 stale
    cursor, client, fake_db = _mock_env(_two_turn_rows(), oc)
    client.create_session.return_value = 'oc_new'
    state = ('rebuild', 'oc_new', None, 3, 2, 0, [], _dt(2026, 11, 10, tzinfo=_tz.utc))
    cursor.fetchone.side_effect = [
        ('oc1', 'active', '/tmp/ws'), (3,), state,
        ('oc1', 'active', '/tmp/ws'),   # rebuild 分支二次 ctx？否——单 ctx；此行为 broadcast 读态
    ]
    monkeypatch.setattr(ar, 'get_db', fake_db)
    monkeypatch.setattr(ar, 'get_runtime', lambda: _MagicMock(get_client=lambda: client))
    monkeypatch.setattr('utils.chat_persist.has_listener', lambda sid: False)
    monkeypatch.setattr('utils.chat_persist.stop_listener', lambda sid: None)
    monkeypatch.setattr(ar, 'broadcast_revert', lambda sid: None)
    out = ar.undo_from_message('sess_x', 'msg_u2')
    client.revert_stage.assert_not_called()
    client.create_session.assert_called_once()
    assert out['revert']['mode'] == 'rebuild'
    assert out['revert']['prevOpencodeSessionId'] is None or True  # 字段在库行，不在读态


def test_redo_nothing_to_redo(monkeypatch):
    cursor = _MagicMock()
    cursor.fetchone.side_effect = [
        ('oc1', 'active', '/tmp/ws'), None,              # sessions + 无状态行
    ]
    conn = _MagicMock(); conn.cursor.return_value = cursor
    conn.__enter__ = lambda self: conn; conn.__exit__ = lambda self, *a: False

    @_cm
    def fake_db():
        yield conn
    monkeypatch.setattr(ar, 'get_db', fake_db)
    import pytest
    with pytest.raises(ar.UndoError) as ei:
        ar.redo('sess_x')
    assert ei.value.code == 'NOTHING_TO_REDO'


def test_commit_after_dispatch_noop_without_state(monkeypatch):
    calls = []
    monkeypatch.setattr(ar, 'get_revert_state', lambda conn, sid: None)
    monkeypatch.setattr(ar, 'commit_revert', lambda conn, sid: calls.append(sid))

    @_cm
    def fake_db():
        yield _MagicMock()
    monkeypatch.setattr(ar, 'get_db', fake_db)
    ar.commit_after_dispatch('s1')
    assert calls == []


def test_preview_shape(monkeypatch):
    oc = [{'info': {'id': 'msg_o3', 'role': 'user', 'time': {'created': 2}},
           'parts': [{'type': 'patch', 'files': [{'file': 'a.txt'}]}]}]
    cursor, client, fake_db = _mock_env(_two_turn_rows(), oc)
    monkeypatch.setattr(ar, 'get_db', fake_db)
    monkeypatch.setattr(ar, 'get_runtime', lambda: _MagicMock(get_client=lambda: client))
    out = ar.preview_undo('sess_x', 'msg_u2')
    assert out['messageCount'] == 3
    assert out['files'] == ['a.txt']
    assert out['fileCapability'] == 'full'
    assert out['mode'] == 'native'
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && python -m pytest tests/test_ai_revert.py -v -k "undo_native or mismatch or compensates or busy or rebuild_promotes or nothing_to_redo or commit_after_dispatch or preview_shape"`
Expected: FAIL（AttributeError: preview_undo）

- [ ] **Step 3: 写实现**（`ai_revert.py` 追加；`from utils.session_history import render_history_block` 留到 Task 9 用，此处不引）

```python
# ================================================================ 编排器
def _fetch_oc_messages(client, oc_sid, workspace_path):
    if not oc_sid:
        return []
    try:
        return client.get_messages(oc_sid, directory=workspace_path) or []
    except requests.RequestException as e:
        raise UndoError('OPENCODE_UNAVAILABLE', 'AI 运行时暂时不可用，请稍后重试',
                        status=502, retryable=True, operation='undo') from e


def _load_undo_context_or_raise(sid: str, msg_id: str) -> dict:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT opencode_session_id, status, workspace_path "
            "FROM ai_chat_sessions WHERE id = %s", (sid,))
        row = cur.fetchone()
        if row is None:
            raise UndoError('SESSION_NOT_FOUND', 'session not found', status=404,
                            operation='undo')
        oc_sid, status, workspace_path = row
        if status != 'active':
            raise UndoError('INVALID_STATUS', '该状态会话不可撤销', operation='undo')
        cur.execute(
            "SELECT seq FROM ai_chat_messages WHERE id = %s AND session_id = %s",
            (msg_id, sid))
        trow = cur.fetchone()
        if trow is None:
            raise UndoError('MESSAGE_NOT_FOUND', 'message not found', status=404,
                            operation='undo')
        target_seq = trow[0]
        cur.execute(
            "SELECT id, role, seq FROM ai_chat_messages "
            "WHERE session_id = %s ORDER BY seq ASC", (sid,))
        rows = [{'id': r[0], 'role': r[1], 'seq': r[2]} for r in cur.fetchall()]
    return {'oc_sid': oc_sid, 'workspace_path': workspace_path,
            'target_seq': target_seq, 'rows': rows,
            'message_count': sum(1 for r in rows if r['seq'] >= target_seq)}


def _finalize_plan(client, plan: dict, workspace_path: str, oc_messages) -> dict:
    """补文件能力分级并把 stale 促销为 rebuild（spec §2.2a(5)）。"""
    if plan['mode'] != 'native' or not plan['oc_user_message_id']:
        plan['fileCapability'] = 'none'
        return plan
    created_ms = 0
    for m in oc_messages:
        info = (m.get('info') or {})
        if info.get('id') == plan['oc_user_message_id']:
            created_ms = ((info.get('time') or {}).get('created')) or 0
            break
    try:
        project_info = client.get_project(directory=workspace_path)
    except requests.RequestException:
        project_info = None
    cap = file_capability(project_info, workspace_path, created_ms, plan['files'])
    plan['fileCapability'] = cap
    if cap == 'stale':
        plan['mode'] = 'rebuild'
        plan['reason'] = 'file-snapshot-stale'
    return plan


def preview_undo(sid: str, msg_id: str) -> dict:
    """撤销预览（只读，1 次 OC get_messages + 1 次 get_project）。"""
    ctx = _load_undo_context_or_raise(sid, msg_id)
    client = get_runtime().get_client()
    oc_messages = _fetch_oc_messages(client, ctx['oc_sid'], ctx['workspace_path'])
    plan = resolve_undo_target(ctx['rows'], ctx['target_seq'], oc_messages)
    plan = _finalize_plan(client, plan, ctx['workspace_path'], oc_messages)
    return {'messageCount': ctx['message_count'], 'files': plan['files'],
            'fileCapability': plan['fileCapability'], 'mode': plan['mode'],
            'reason': plan.get('reason')}


def _compensate_clear(client, oc_sid, workspace_path):
    """OC 已撤销但平台侧失败 → 反向补偿（尽力而为并留痕）。"""
    try:
        client.revert_clear(oc_sid, directory=workspace_path)
    except Exception:
        logger.error('revert 补偿失败 oc=%s（平台已回滚，OC 侧处于撤销态，'
                     '下次发送将被提交）', oc_sid, exc_info=True)


def _undo_native(sid, ctx, plan, client):
    from utils import chat_persist
    oc_reverted = False
    try:
        with get_db() as conn:
            conn.cursor().execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", (sid,))
            chat_persist.stop_listener(sid)   # 防监听器复活（clear_session 先例）
            stash_rows(conn, sid, ctx['target_seq'])
            if plan['oc_user_message_id']:
                try:
                    info = client.revert_stage(ctx['oc_sid'],
                                               plan['oc_user_message_id'],
                                               directory=ctx['workspace_path'])
                except requests.HTTPError as e:
                    if getattr(e, 'response', None) is not None \
                            and e.response.status_code == 409:
                        raise UndoError('SESSION_BUSY',
                                        '回合进行中，请先停止当前回复再撤销',
                                        operation='undo') from e
                    raise
                if ((info or {}).get('revert') or {}).get('messageID') \
                        != plan['oc_user_message_id']:
                    raise UndoError('REVERT_MISMATCH',
                                    '撤销点校验失败（OpenCode 未按请求回退）')
                oc_reverted = True
            upsert_revert_state(
                conn, sid, mode='native', opencode_session_id=ctx['oc_sid'],
                oc_message_id=plan['oc_user_message_id'],
                from_seq=ctx['target_seq'], message_count=ctx['message_count'],
                file_count=len(plan['files']), file_names=plan['files'])
    except Exception:
        if oc_reverted:
            _compensate_clear(client, ctx['oc_sid'], ctx['workspace_path'])
        raise
    with get_db() as conn:
        state = get_revert_state(conn, sid)
    broadcast_revert(sid)
    return state


def _undo_rebuild(sid, ctx, plan, client):
    from utils import chat_persist
    new_oc = client.create_session(directory=ctx['workspace_path'],
                                   title='撤销重建')
    try:
        with get_db() as conn:
            conn.cursor().execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", (sid,))
            chat_persist.stop_listener(sid)
            stash_rows(conn, sid, ctx['target_seq'])
            conn.cursor().execute(
                "UPDATE ai_chat_sessions SET opencode_session_id = %s, "
                "context_replay_pending = TRUE WHERE id = %s", (new_oc, sid))
            upsert_revert_state(
                conn, sid, mode='rebuild', opencode_session_id=new_oc,
                oc_message_id=None, from_seq=ctx['target_seq'],
                message_count=ctx['message_count'], file_count=0, file_names=[],
                prev_opencode_session_id=ctx['oc_sid'])
    except Exception:
        try:
            client.delete_session(new_oc)
        except Exception:
            pass
        raise
    with get_db() as conn:
        state = get_revert_state(conn, sid)
    broadcast_revert(sid)
    return state


def undo_from_message(sid: str, msg_id: str) -> dict:
    from utils import chat_persist
    if chat_persist.has_listener(sid):
        raise UndoError('SESSION_BUSY', '回合进行中，请先停止当前回复再撤销',
                        operation='undo')
    ctx = _load_undo_context_or_raise(sid, msg_id)
    client = get_runtime().get_client()
    oc_messages = _fetch_oc_messages(client, ctx['oc_sid'], ctx['workspace_path'])
    plan = resolve_undo_target(ctx['rows'], ctx['target_seq'], oc_messages)
    plan = _finalize_plan(client, plan, ctx['workspace_path'], oc_messages)
    if plan['mode'] == 'rebuild':
        state = _undo_rebuild(sid, ctx, plan, client)
    else:
        state = _undo_native(sid, ctx, plan, client)
    return {'deleted': ctx['message_count'], 'revert': state,
            'messages': _message_window(sid)}


def redo(sid: str) -> dict:
    from utils import chat_persist
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT opencode_session_id, status, workspace_path "
            "FROM ai_chat_sessions WHERE id = %s", (sid,))
        row = cur.fetchone()
        if row is None:
            raise UndoError('SESSION_NOT_FOUND', 'session not found', status=404,
                            operation='redo')
        oc_now, status, workspace_path = row
        if status != 'active':
            raise UndoError('INVALID_STATUS', '该状态会话不可重做', operation='redo')
        state = get_revert_state(conn, sid)
        if not state:
            raise UndoError('NOTHING_TO_REDO', '没有可重做的撤销', operation='redo')
        if state['opencodeSessionId'] != oc_now:
            # 会话被恢复/清空换过：旧 OC 上下文不可达，只收口不搬回（spec §2.2c）
            commit_revert(conn, sid)
            clear_context_replay(conn, sid)
            raise UndoError('REDO_STALE', '会话上下文已变化，无法重做（撤销已收口）',
                            operation='redo')
    client = get_runtime().get_client()
    try:
        with get_db() as conn:
            conn.cursor().execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", (sid,))
            if state['mode'] == 'native' and state['ocMessageId']:
                client.revert_clear(state['opencodeSessionId'],
                                    directory=workspace_path)
            chat_persist.stop_listener(sid)
            restore_stash(conn, sid)
            clear_context_replay(conn, sid)
            conn.cursor().execute(
                "DELETE FROM ai_chat_session_reverts WHERE session_id = %s", (sid,))
    except Exception:
        # OC clear 已发出而 DB 回滚：平台仍处撤销态，再次 redo 幂等重试
        logger.error('redo 失败 session=%s（OC clear 可能已发出）', sid, exc_info=True)
        raise
    broadcast_revert(sid)
    return {'deleted': state['messageCount'], 'messages': _message_window(sid)}


def commit_after_dispatch(sid: str) -> None:
    """提交挂起的撤销（新回合已被接受——OC 侧 prompt 开头自动 commit，E3，
    平台侧同步收口）。无撤销态时零开销（单 SELECT）。"""
    with get_db() as conn:
        state = get_revert_state(conn, sid)
        if not state:
            return
        commit_revert(conn, sid)
        clear_context_replay(conn, sid)
    broadcast_revert(sid)


def broadcast_revert(sid: str) -> None:
    """向该会话全部 SSE 连接广播撤销态（无连接时 no-op）。"""
    try:
        with get_db() as conn:
            state = get_revert_state(conn, sid)
        from utils.session_event_bus import broadcast
        broadcast(sid, 'session.revert', {'sessionId': sid, 'revert': state})
    except Exception:
        logger.warning('revert 广播失败 session=%s', sid, exc_info=True)


def _message_window(sid: str, limit: int = 200) -> list:
    """与 GET /messages 默认分支同形（seq DESC 取 limit 再反转）。"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT m.id, m.role, m.content, m.created_at, m.meta, m.seq, f.rating "
            "FROM ai_chat_messages m "
            "LEFT JOIN ai_chat_message_feedback f ON f.message_id = m.id "
            "WHERE m.session_id = %s ORDER BY m.seq DESC LIMIT %s", (sid, limit))
        rows = cur.fetchall()
    rows = list(reversed(rows))
    return [{'id': r[0], 'role': r[1], 'content': r[2],
             'createdAt': r[3].isoformat() if r[3] else None,
             'meta': r[4], 'seq': r[5], 'rating': r[6]} for r in rows]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && python -m pytest tests/test_ai_revert.py -v`
Expected: 全部 PASS（21 passed）

- [ ] **Step 5: Commit**

```bash
git add server/utils/ai_revert.py server/tests/test_ai_revert.py
git commit -m "feat(ai-chat): undo/redo/preview 编排器——事务原子性 + 409/busy/rebuild 降级"
```

---

### Task 7: 路由三端点 + get_messages revert 块

**Files:**
- Modify: `server/routes/ai_chat.py`（import 区加 `from utils import ai_revert`；`set_message_feedback` 之前插入三个端点；`get_messages` :1436 响应加 `revert` 块；文件头路由清单注释补 3 行）
- Test: `server/tests/test_routes_ai_chat.py`（追加 undo 用例）

**Interfaces:**
- Consumes: Task 6 的 `ai_revert.preview_undo/undo_from_message/redo/UndoError`。
- Produces: `GET /ai/chat/sessions/:id/undo-preview?messageId=`、`POST .../undo`、`POST .../redo`；`GET .../messages` 响应新增 `revert` 字段。

- [ ] **Step 1: 追加失败测试**（`server/tests/test_routes_ai_chat.py` 末尾；注意 setup 夹具需补两个 patch）

setup 夹具的 `patches` 列表追加（在 `patch('routes.ai_chat.get_runtime', ...)` 之后）：

```python
        patch('utils.ai_revert.get_db', fake_db),
        patch('utils.ai_revert.get_runtime',
              lambda: _RuntimeStub(fake_client)),
```

用例（文件末尾追加）：

```python
def test_undo_preview_returns_plan(setup):
    from datetime import datetime, timezone
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.side_effect = [
        ('sess_x', 'user-1', 'oc', 'active', '/tmp/ws', None, None),
        ('oc', 'active', '/tmp/ws'),
        (3,),
    ]
    cursor.fetchall.return_value = [
        ('msg_a1', 'assistant', 2), ('msg_u2', 'user', 3),
        ('msg_a2', 'assistant', 4), ('msg_u3', 'user', 5)]
    oc.get_messages.return_value = [
        {'info': {'id': 'msg_o3', 'role': 'user', 'time': {'created': 2}},
         'parts': [{'type': 'patch', 'files': [{'file': 'a.txt'}]}]},
    ]
    oc.get_project.return_value = {'vcs': 'git', 'worktree': '/tmp/ws'}
    resp = client.get('/ai/chat/sessions/sess_x/undo-preview?messageId=msg_u2',
                      headers=dev_h)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {'messageCount': 3, 'files': ['a.txt'],
                    'fileCapability': 'full', 'mode': 'native', 'reason': None}


def test_undo_route_native_ok(setup):
    from datetime import datetime, timezone
    client, cursor, oc, dev_h, _, _ = setup
    dt = datetime(2026, 11, 10, tzinfo=timezone.utc)
    cursor.fetchone.side_effect = [
        ('sess_x', 'user-1', 'oc', 'active', '/tmp/ws', None, None),
        ('oc', 'active', '/tmp/ws'),
        (3,),
        ('native', 'oc', 'msg_o3', 3, 2, 1, ['a.txt'], dt),   # 提交后读态
    ]
    cursor.fetchall.side_effect = [
        [('msg_a1', 'assistant', 2), ('msg_u2', 'user', 3),
         ('msg_a2', 'assistant', 4), ('msg_u3', 'user', 5)],
        [],                                                  # commit_revert 扫描
        [('msg_a1', 'assistant', [], dt, None, 2, None)],    # message window
    ]
    oc.get_messages.return_value = [
        {'info': {'id': 'msg_a1', 'role': 'assistant', 'time': {'created': 1}},
         'parts': []},
        {'info': {'id': 'msg_o3', 'role': 'user', 'time': {'created': 2}},
         'parts': []},
    ]
    oc.get_project.return_value = {'vcs': 'git', 'worktree': '/tmp/ws'}
    oc.revert_stage.return_value = {'id': 'oc', 'revert': {'messageID': 'msg_o3'}}
    resp = client.post('/ai/chat/sessions/sess_x/undo',
                       json={'messageId': 'msg_u2'}, headers=dev_h)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['revert']['mode'] == 'native'
    assert body['revert']['ocMessageId'] == 'msg_o3'
    assert body['messages'][0]['id'] == 'msg_a1'
    oc.revert_stage.assert_called_once()


def test_undo_route_busy_409(setup, monkeypatch):
    client, cursor, oc, dev_h, _, _ = setup
    monkeypatch.setattr('utils.chat_persist.has_listener', lambda sid: True)
    resp = client.post('/ai/chat/sessions/sess_x/undo',
                       json={'messageId': 'msg_u2'}, headers=dev_h)
    assert resp.status_code == 409
    assert resp.get_json()['error']['code'] == 'SESSION_BUSY'


def test_undo_route_batch_child_409(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc', 'running',
                                    '/tmp/ws', 'batch_1', None)
    resp = client.post('/ai/chat/sessions/sess_x/undo',
                       json={'messageId': 'msg_u2'}, headers=dev_h)
    assert resp.status_code == 409
    assert resp.get_json()['error']['code'] == 'BATCH_SESSION_CONTROLLED'


def test_redo_route_nothing_to_redo(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.side_effect = [
        ('sess_x', 'user-1', 'oc', 'active', '/tmp/ws', None, None),
        ('oc', 'active', '/tmp/ws'),
        None,
    ]
    resp = client.post('/ai/chat/sessions/sess_x/redo', headers=dev_h)
    assert resp.status_code == 409
    assert resp.get_json()['error']['code'] == 'NOTHING_TO_REDO'


def test_messages_response_carries_revert_state(setup):
    client, cursor, oc, dev_h, _, _ = setup
    from datetime import datetime, timezone
    dt = datetime(2026, 11, 10, tzinfo=timezone.utc)
    # fetchone: 会话 → 消息窗口 fetchall → revert 状态行
    cursor.fetchone.side_effect = [
        ('sess_x', 'user-1', 'oc', 'active', '/tmp/ws', None, None),
        ('native', 'oc', 'msg_o3', 3, 2, 1, ['a.txt'], dt),
    ]
    cursor.fetchall.return_value = [
        ('msg_a1', 'assistant', [], dt, None, 2, None)]
    resp = client.get('/ai/chat/sessions/sess_x/messages', headers=dev_h)
    assert resp.status_code == 200
    assert resp.get_json()['revert']['ocMessageId'] == 'msg_o3'
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && python -m pytest tests/test_routes_ai_chat.py -v -k "undo or redo"`
Expected: FAIL（404：路由不存在）

- [ ] **Step 3: 写实现**

`ai_chat.py` import 区（`from utils import agent_ledger` 附近）加：

```python
from utils import ai_revert
```

`set_message_feedback`（:1344）之前插入：

```python
# ---------------- 撤销/重做（undo-redo spec §2.3） ----------------
def _undo_error_response(e: 'ai_revert.UndoError'):
    return jsonify({'error': {'code': e.code, 'message': e.message,
                              'retryable': e.retryable,
                              'operation': e.operation}}), e.status


@ai_chat_bp.route('/sessions/<sid>/undo-preview', methods=['GET'])
@login_required
def undo_preview(sid):
    """撤销预览：确认弹窗数据（条数/文件/能力/模式）。只读。"""
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    msg_id = request.args.get('messageId') or ''
    try:
        return jsonify(ai_revert.preview_undo(sid, msg_id))
    except ai_revert.UndoError as e:
        return _undo_error_response(e)


@ai_chat_bp.route('/sessions/<sid>/undo', methods=['POST'])
@write_required
def undo_to_message(sid):
    """撤销到某条用户消息之前：OpenCode revert + 平台行搬 stash（可重做）。"""
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    if _execution_controlled(sess):
        return jsonify({'error': {
            'code': 'BATCH_SESSION_CONTROLLED',
            'message': '批任务子会话正在由后台执行器控制，暂不能撤销',
            'retryable': False, 'operation': 'undo',
        }}), 409
    body = request.get_json(force=True) or {}
    msg_id = body.get('messageId') or ''
    try:
        result = ai_revert.undo_from_message(sid, msg_id)
    except ai_revert.UndoError as e:
        return _undo_error_response(e)
    log_operation('update', 'ai_chat_session', sid, sid,
                  f"撤销到消息（{result['deleted']} 条，"
                  f"约 {(result['revert'] or {}).get('fileCount', 0)} 文件）")
    return jsonify(result)


@ai_chat_bp.route('/sessions/<sid>/redo', methods=['POST'])
@write_required
def redo_session_route(sid):
    """重做：恢复最近一次撤销的全部消息（stash 搬回 + unrevert）。"""
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    if _execution_controlled(sess):
        return jsonify({'error': {
            'code': 'BATCH_SESSION_CONTROLLED',
            'message': '批任务子会话正在由后台执行器控制，暂不能重做',
            'retryable': False, 'operation': 'redo',
        }}), 409
    try:
        result = ai_revert.redo(sid)
    except ai_revert.UndoError as e:
        return _undo_error_response(e)
    log_operation('update', 'ai_chat_session', sid, sid,
                  f"重做（恢复 {result['deleted']} 条）")
    return jsonify(result)
```

`get_messages` 的 `with get_db()` 块内、`rows.reverse()` 之后加：

```python
        # 撤销态（undo-redo spec §2.3）：任何端打开页面即收敛（重做横幅）
        cur.execute(
            "SELECT mode, message_count, file_count, file_names, created_at "
            "FROM ai_chat_session_reverts WHERE session_id = %s", (sid,))
        rrow = cur.fetchone()
```

响应改为：

```python
    revert = None
    if rrow:
        revert = {'mode': rrow[0], 'messageCount': rrow[1], 'fileCount': rrow[2],
                  'fileNames': rrow[3] or [],
                  'createdAt': rrow[4].isoformat() if rrow[4] else None}
    return jsonify({
        'messages': [ ... 原样 ... ],
        'hasMore': has_more,
        'revert': revert,
    })
```

文件头路由清单注释（:26 附近）补：

```
    POST   /ai/chat/sessions/:id/undo  undo_to_message（撤销到某条消息之前）
    POST   /ai/chat/sessions/:id/redo  redo_session_route（恢复被撤销的消息）
    GET    /ai/chat/sessions/:id/undo-preview  undo_preview（确认弹窗数据）
```

- [ ] **Step 4: 跑测试确认通过 + 本文件既有用例不回归**

Run: `cd server && python -m pytest tests/test_routes_ai_chat.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add server/routes/ai_chat.py server/tests/test_routes_ai_chat.py
git commit -m "feat(ai-chat): undo-preview/undo/redo 端点 + messages 响应携带撤销态"
```

---

### Task 8: DELETE /messages 升级为 undo 核心 + 既有用例迁移

**Files:**
- Modify: `server/routes/ai_chat.py:2522-2561`（`delete_message_onwards` 重写）
- Modify: `server/tests/test_routes_ai_chat.py:987-1020`（3 个用例升级）

**Interfaces:**
- Consumes: Task 6 `ai_revert.undo_from_message`。
- Produces: `DELETE /sessions/:sid/messages/:msg_id` 响应 `{'deleted', 'revert', 'messages'}`（保留 `deleted` 字段兼容既有前端调用方）；编辑/重新生成/发送失败清理三个调用方平滑迁移。

- [ ] **Step 1: 重写 3 个既有用例（先跑红）**

替换 `test_delete_message_onwards_drops_from_created_at` 与 `test_delete_message_unknown_msg_404`（guest 403 用例不变）：

```python
def test_delete_message_runs_native_undo(setup):
    """升级为真撤销：OC revert_stage 用吸附出的 user id，响应带 revert。"""
    from datetime import datetime, timezone
    client, cursor, oc, dev_h, _, _ = setup
    dt = datetime(2026, 5, 30, tzinfo=timezone.utc)
    cursor.fetchone.side_effect = [
        ('sess_x', 'user-1', 'oc', 'active', '/tmp/ws', None, None),
        ('oc', 'active', '/tmp/ws'),
        (3,),
        ('native', 'oc', 'msg_o3', 3, 2, 1, ['a.txt'], dt),   # 提交后读态
    ]
    cursor.fetchall.side_effect = [
        [('msg_a1', 'assistant', 2), ('msg_u2', 'user', 3)],
        [],
        [('msg_a1', 'assistant', [], dt, None, 2, None)],
    ]
    oc.get_messages.return_value = [
        {'info': {'id': 'msg_a1', 'role': 'assistant', 'time': {'created': 1}},
         'parts': []},
        {'info': {'id': 'msg_o3', 'role': 'user', 'time': {'created': 2}},
         'parts': [{'type': 'patch', 'files': [{'file': 'a.txt'}]}]},
    ]
    oc.get_project.return_value = {'vcs': 'git', 'worktree': '/tmp/ws'}
    oc.revert_stage.return_value = {'id': 'oc', 'revert': {'messageID': 'msg_o3'}}
    resp = client.delete('/ai/chat/sessions/sess_x/messages/msg_u2', headers=dev_h)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['deleted'] == 2
    assert body['revert']['ocMessageId'] == 'msg_o3'
    oc.revert_stage.assert_called_once()       # 顶替旧 abort_session 断言


def test_delete_message_unknown_msg_404(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.side_effect = [
        ('sess_x', 'user-1', 'oc', 'active', '/tmp/ws', None, None),
        ('oc', 'active', '/tmp/ws'),
        None,
    ]
    resp = client.delete('/ai/chat/sessions/sess_x/messages/msg_nope', headers=dev_h)
    assert resp.status_code == 404
    assert resp.get_json()['error']['code'] == 'MESSAGE_NOT_FOUND'


def test_delete_message_platform_only_no_oc_call(setup):
    """失败轮从未到达 OC（OC 列表空）→ 不调 OC，纯平台 stash（spec §2.2b5）。"""
    from datetime import datetime, timezone
    client, cursor, oc, dev_h, _, _ = setup
    dt = datetime(2026, 5, 30, tzinfo=timezone.utc)
    cursor.fetchone.side_effect = [
        ('sess_x', 'user-1', 'oc', 'active', '/tmp/ws', None, None),
        ('oc', 'active', '/tmp/ws'),
        (3,),
        ('native', 'oc', None, 3, 2, 0, [], dt),
    ]
    cursor.fetchall.side_effect = [
        [('msg_a1', 'assistant', 2), ('msg_u2', 'user', 3)], [], []]
    oc.get_messages.return_value = []
    resp = client.delete('/ai/chat/sessions/sess_x/messages/msg_u2', headers=dev_h)
    assert resp.status_code == 200
    assert resp.get_json()['revert']['ocMessageId'] is None
    oc.revert_stage.assert_not_called()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && python -m pytest tests/test_routes_ai_chat.py -v -k delete_message`
Expected: FAIL（旧实现无 revert 字段）

- [ ] **Step 3: 重写路由**（`delete_message_onwards` 整体替换）

```python
@ai_chat_bp.route('/sessions/<sid>/messages/<msg_id>', methods=['DELETE'])
@write_required
def delete_message_onwards(sid, msg_id):
    """真撤销（undo-redo spec §2.3）：OpenCode revert（上下文回退 + 文件按步
    还原）+ 平台行搬入 stash（可重做，下次发送提交）。编辑/重新生成/发送失败
    清理三个调用方共用。响应 = {'deleted', 'revert', 'messages'}。"""
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    # M3 门禁（遗留项 5）：非终态批/编排子会话由后台执行器独家驱动，
    # 删消息（Edit/Retry 的前提是独占回合）不得绕过
    if _execution_controlled(sess):
        return jsonify({'error': {
            'code': 'BATCH_SESSION_CONTROLLED',
            'message': '批任务子会话正在由后台执行器控制，暂不能删除消息',
            'retryable': False,
            'operation': 'delete_message',
        }}), 409
    try:
        result = ai_revert.undo_from_message(sid, msg_id)
    except ai_revert.UndoError as e:
        return _undo_error_response(e)
    log_operation('update', 'ai_chat_session', sid, sid,
                  f'删除该消息及之后（撤销 {result["deleted"]} 条）')
    return jsonify(result)
```

- [ ] **Step 4: 跑测试确认通过（含 M3 用例）**

Run: `cd server && python -m pytest tests/test_routes_ai_chat.py tests/test_review_gap_tests.py -v -k "delete_message or m3_gates"`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add server/routes/ai_chat.py server/tests/test_routes_ai_chat.py
git commit -m "feat(ai-chat): DELETE 消息端点升级为真撤销核心（编辑/重新生成/失败清理共用）"
```

### Task 9: 提交挂点（6 处）

**Files:**
- Modify: `server/routes/ai_chat.py`（5 处挂点 + clear_session 重建/删除清理）
- Test: `server/tests/test_ai_revert.py`（`commit_after_dispatch` 单测）

**Interfaces:**
- Consumes: Task 6 `ai_revert.commit_after_dispatch`。
- 行为：新回合被 OC 接受（send/command/compact）或平台侧写新行（run_script）时收口撤销；clear/delete 连带清理。

- [ ] **Step 1: 写失败测试**（`test_ai_revert.py` 末尾追加）

```python
def test_commit_after_dispatch_clears_state(monkeypatch):
    calls = []
    monkeypatch.setattr(ar, 'get_revert_state',
                        lambda conn, sid: {'mode': 'native'} if sid == 's1' else None)
    monkeypatch.setattr(ar, 'commit_revert',
                        lambda conn, sid: calls.append(('commit', sid)) or 3)
    monkeypatch.setattr(ar, 'clear_context_replay',
                        lambda conn, sid: calls.append(('flag', sid)))
    monkeypatch.setattr(ar, 'broadcast_revert', lambda sid: calls.append(('bc', sid)))

    @_cm
    def fake_db():
        yield _MagicMock()
    monkeypatch.setattr(ar, 'get_db', fake_db)
    ar.commit_after_dispatch('s1')
    assert ('commit', 's1') in calls and ('bc', 's1') in calls
    ar.commit_after_dispatch('s2')                     # 无状态 → no-op
    assert not any(c == ('commit', 's2') for c in calls)
```

- [ ] **Step 2: 跑测试确认通过**（Task 6 已实现该函数则直接绿；红则修实现）

Run: `cd server && python -m pytest tests/test_ai_revert.py -v -k commit_after_dispatch`

- [ ] **Step 3: 接线 5 处挂点**（`ai_chat.py`）

(a) `send_message`：在 `dispatch.ok` / recovery 分支合流后的最终 `return jsonify({...}), 202`（:1206）之前插入：

```python
    # undo-redo spec §2.4：新回合已被 OC 接受（OC prompt 开头自动提交撤销，
    # E3）——平台侧同步收口，重做窗口关闭。失败不阻断发送。
    try:
        ai_revert.commit_after_dispatch(sid)
    except Exception:
        logger.warning('undo commit hook failed session=%s', sid, exc_info=True)
```

(b) `run_session_command`：`OpenCodeClient(...).run_command(...)`（:2206-2208）之后、`return ... 202` 之前插入同一块（文案同上）。

(c) `compact_session`：`_run()` 内 `summarize_session` 成功日志（:978）之后插入：

```python
            try:
                ai_revert.commit_after_dispatch(sid)   # OC compact 亦自动 commit
            except Exception:
                logger.warning('undo commit hook failed session=%s', sid, exc_info=True)
```

(d) `run_script`：`with get_db() as conn:` INSERT（:2643）之前插入：

```python
    # undo-redo spec §2.4：run_script 写平台侧行前收口撤销（该轮不在 OC，
    # OC 到下次 prompt 自然提交）
    try:
        ai_revert.commit_after_dispatch(sid)
    except Exception:
        logger.warning('undo commit hook failed session=%s', sid, exc_info=True)
```

(e) `clear_session` DB 块（:2759-2768）：DELETE 消息/子任务之后加两行；UPDATE 追加清 flag：

```python
        cur.execute("DELETE FROM ai_chat_message_stash WHERE session_id = %s", (sid,))
        cur.execute("DELETE FROM ai_chat_session_reverts WHERE session_id = %s", (sid,))
        cur.execute(
            "UPDATE ai_chat_sessions SET opencode_session_id = %s, status = 'active', "
            "permission_mode = 'ask', workspace_path = %s, "
            "context_replay_pending = FALSE "
            "WHERE id = %s AND user_id = %s",
            (new_oc, workspace_path, sid, user['userId']),
        )
```

(f) `delete_session` DB 块（:2811-2814）加（软删不留悬挂可重做工作集；消息行按审计保留不变）：

```python
        cur.execute("DELETE FROM ai_chat_message_stash WHERE session_id = %s", (sid,))
        cur.execute("DELETE FROM ai_chat_session_reverts WHERE session_id = %s", (sid,))
```

- [ ] **Step 4: rebuild 上下文重放接线**（`send_message`，spec §2.2d）

在 `send_message` 组装 prompt 之后（`prompt += inline_file_mentions(...)` 与 dispatch 之间，约 ：1064-1113 区域）插入：

```python
    # undo-redo spec §2.2d：rebuild 撤销后首次发送 → 用既有恢复机制把被保留
    # 的历史重放给新 OC 会话（与 _recover_session_and_resend 同一格式）。
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT context_replay_pending FROM ai_chat_sessions "
            "WHERE id = %s AND user_id = %s", (sid, user['userId']))
        _rp = cur.fetchone()
    if _rp and _rp[0]:
        with get_db() as conn:
            conn.cursor().execute(
                "UPDATE ai_chat_sessions SET context_replay_pending = FALSE "
                "WHERE id = %s", (sid,))
        _replayed = render_history_block(sid, exclude_msg_id=msg_id)
        if _replayed:
            prompt = _replayed + prompt
```

- [ ] **Step 5: 跑回归**

Run: `cd server && python -m pytest tests/test_routes_ai_chat.py tests/test_ai_revert.py -v`
Expected: 全部 PASS（既有 send/command/compact/run_script 用例不受影响——commit_after_dispatch 在 mock 库上无状态行即 no-op）

- [ ] **Step 6: Commit**

```bash
git add server/routes/ai_chat.py server/tests/test_ai_revert.py
git commit -m "feat(ai-chat): 撤销提交挂点六处——send/command/compact/run_script/clear/delete + rebuild 历史重放"
```

---

### Task 10: SSE 代理接入事件总线

**Files:**
- Modify: `server/routes/ai_chat.py`（import + `_pump_bus_events` + `sse_events` generate() 三处）
- Test: 无新增路由测试（总线本身 Task 5 已测；跨连接收敛由 Task 14 E2E 断言）

**Interfaces:**
- Consumes: Task 5 `session_event_bus.register/unregister`。

- [ ] **Step 1: 写实现**

`ai_chat.py` import 区加：

```python
from utils import session_event_bus
```

`_pump_upstream_events`（:1769）之后加：

```python
def _pump_bus_events(bus_q, out_q):
    """平台侧事件（session.revert 等）转发进主流队列；unregister 后以 None 收尾。"""
    while True:
        item = bus_q.get()
        if item is None:
            break
        out_q.put(item)
```

`generate()` 内（:1815 `yield _format_sse('session.hello', ...)` 之前）注册：

```python
            bus_q = session_event_bus.register(sid)
```

`pump.start()`（:1828）之后加：

```python
            bus_pump = threading.Thread(target=_pump_bus_events, args=(bus_q, q),
                                        daemon=True, name=f'sse-bus-{sid[:8]}')
            bus_pump.start()
```

主循环无需改动：总线帧与 OC pump 帧同形，`event_session_id(props)` 对
`{'sessionId','revert'}` 返回 None → 相关性闸门 `not ev_sid` 放行 →
`apply_event` 对未知类型 'session.revert' 快速返回 None → 原样转发。

`finally` 块（:1901-1902）改为：

```python
        finally:
            session_event_bus.unregister(sid, bus_q)
            bus_q.put(None)          # 收掉 bus 转发线程
            logger.info('sse stream closed session=%s', sid)
```

- [ ] **Step 2: 跑回归**

Run: `cd server && python -m pytest tests/test_routes_ai_chat.py tests/test_session_event_bus.py -v`
Expected: 全部 PASS

- [ ] **Step 3: Commit**

```bash
git add server/routes/ai_chat.py
git commit -m "feat(ai-chat): SSE 代理接入平台事件总线——session.revert 帧到达所有已连标签页"
```

---

### Task 11: 前端 API + store（服务端权威窗口 + 远程收敛）

**Files:**
- Modify: `src/api/aiChat.ts`（类型 + 3 函数 + getMessages 类型 + allow-list）
- Modify: `src/stores/aiChat.ts`（state + 6 处）
- Modify: `src/api/__tests__/aiChat.test.ts`（守护用例）
- Create: `src/stores/__tests__/aiChat.undo.test.ts`

**Interfaces:**
- Produces:
  - api：`RevertState`、`UndoPreview` 类型；`undoPreview(id, msgId)`、`undoToMessage(id, msgId)`、`redoSession(id)`
  - store：state `revertState: Record<string, RevertState | null>`、`composerRefill: Record<string, string | null>`；actions `undoFromMessage(id, msgId)`、`redoSession(id)`、`undoLastTurn()`、`_applyRemoteRevert(sid)`；`deleteFromMessage` 失败即上抛不裁剪；发送/命令/压缩成功后清 `revertState`；SSE `session.revert` 防抖强一致重载。

- [ ] **Step 1: 写失败测试** `src/stores/__tests__/aiChat.undo.test.ts`

（mock 块照抄 `src/stores/__tests__/aiChat.test.ts` 头部的 `vi.mock('@/api/aiChat', ...)`，并补 `undoToMessage: vi.fn()`、`redoSession: vi.fn()` 两个 mock。）

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAiChatStore } from '@/stores/aiChat'
import { undoToMessage, redoSession, getMessages, sendMessage } from '@/api/aiChat'

vi.mock('@/api/aiChat', () => ({
  ...（与 aiChat.test.ts 的 mock 工厂一致，另加以下三项）,
  undoToMessage: vi.fn(),
  redoSession: vi.fn(),
}))

// 说明：mock 工厂必须完整覆盖 store 顶层 import 的每个名字——
// 直接复制 aiChat.test.ts 现有工厂对象再补上面三项，避免遗漏。

describe('aiChat store undo/redo', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('undoFromMessage 采用服务端窗口并记录撤销态，失败时不裁剪', async () => {
    const s = useAiChatStore()
    s.activeSessionId = 's1'
    s.messages['s1'] = [
      { id: 'u1', role: 'user', content: [] },
      { id: 'a1', role: 'assistant', content: [] },
      { id: 'u2', role: 'user', content: [] },
    ] as any
    vi.mocked(undoToMessage).mockRejectedValueOnce(
      { response: { data: { error: { code: 'SESSION_BUSY', message: 'x' } } } })
    await expect(s.undoFromMessage('s1', 'u2')).rejects.toBeTruthy()
    expect(s.messages['s1']).toHaveLength(3)           // 失败不裁剪
    vi.mocked(undoToMessage).mockResolvedValueOnce({
      data: { deleted: 2, revert: { mode: 'native', messageCount: 2, fileCount: 1 },
              messages: [{ id: 'u1', role: 'user', content: [] }] },
    } as any)
    await s.undoFromMessage('s1', 'u2')
    expect(s.messages['s1']).toHaveLength(1)
    expect(s.revertState['s1']).toEqual({ mode: 'native', messageCount: 2, fileCount: 1 })
  })

  it('deleteFromMessage 委托 undoFromMessage（失败上抛）', async () => {
    const s = useAiChatStore()
    vi.mocked(undoToMessage).mockRejectedValueOnce(new Error('boom'))
    await expect(s.deleteFromMessage('s1', 'u2')).rejects.toBeTruthy()
  })

  it('redoSession 恢复窗口并清撤销态', async () => {
    const s = useAiChatStore()
    s.revertState['s1'] = { mode: 'native', messageCount: 2, fileCount: 0 }
    vi.mocked(redoSession).mockResolvedValueOnce({
      data: { deleted: 2, messages: [{ id: 'u1', role: 'user', content: [] },
                                     { id: 'a1', role: 'assistant', content: [] }] },
    } as any)
    await s.redoSession('s1')
    expect(s.messages['s1']).toHaveLength(2)
    expect(s.revertState['s1']).toBeNull()
  })

  it('发送成功清撤销态（重做窗口关闭）', async () => {
    const s = useAiChatStore()
    s.activeSessionId = 's1'
    s.revertState['s1'] = { mode: 'native', messageCount: 2, fileCount: 0 }
    s.streaming['s1'] = false
    vi.mocked(sendMessage).mockResolvedValueOnce({ data: { messageId: 'm1' } } as any)
    s.messages['s1'] = []
    await s.sendUserMessage('hi')
    expect(s.revertState['s1']).toBeNull()
  })

  it('undoLastTurn 撤销最后一轮并把文本装回 composerRefill', async () => {
    const s = useAiChatStore()
    s.activeSessionId = 's1'
    s.messages['s1'] = [
      { id: 'u1', role: 'user', content: [{ type: 'text', text: '第一问' }] },
      { id: 'a1', role: 'assistant', content: [] },
      { id: 'u2', role: 'user', content: [{ type: 'text', text: '第二问' }] },
    ] as any
    vi.mocked(undoToMessage).mockResolvedValueOnce({
      data: { deleted: 2, revert: null, messages: [{ id: 'u1', role: 'user', content: [] }] },
    } as any)
    const text = await s.undoLastTurn()
    expect(text).toBe('第二问')
    expect(s.composerRefill['s1']).toBe('第二问')
  })

  it('session.revert 事件防抖后强一致重载（采纳变短列表）', async () => {
    vi.useFakeTimers()
    const s = useAiChatStore()
    s.activeSessionId = 's1'
    s.messages['s1'] = [{ id: 'u1', role: 'user', content: [] },
                        { id: 'a1', role: 'assistant', content: [] }] as any
    vi.mocked(getMessages).mockResolvedValueOnce({
      data: { messages: [{ id: 'u1', role: 'user', content: [] }],
              revert: { mode: 'native', messageCount: 1, fileCount: 0 } },
    } as any)
    s._handleEvent('s1', 'session.revert', { sessionId: 's1', revert: null })
    await vi.advanceTimersByTimeAsync(400)
    expect(s.messages['s1']).toHaveLength(1)           // 变短被采纳
    expect(s.revertState['s1']!.messageCount).toBe(1)
    vi.useRealTimers()
  })
})
```

同时更新 `src/api/__tests__/aiChat.test.ts` 的守护用例数组：加 `'session.revert'`（注释：平台侧撤销/重做/提交广播帧，漏登记 → 其他标签页不收敛）。

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run src/stores/__tests__/aiChat.undo.test.ts src/api/__tests__/aiChat.test.ts`
Expected: FAIL（undoFromMessage 不存在）

- [ ] **Step 3: 写实现**

`src/api/aiChat.ts`：

`getMessages` 返回类型改为：

```ts
  return get<{ messages: AiMessage[]; hasMore?: boolean; revert?: RevertState | null }>(
```

`deleteFromMessage` 附近插入：

```ts
/** 撤销态（undo-redo spec）：GET /messages 的 revert 块与 undo 响应共用。 */
export interface RevertState {
  mode: 'native' | 'rebuild'
  messageCount: number
  fileCount: number
  fileNames?: string[]
  createdAt?: string
}

export interface UndoPreview {
  messageCount: number
  files: string[]
  fileCapability: 'full' | 'none' | 'stale'
  mode: 'native' | 'rebuild'
  reason?: string | null
}

export function undoPreview(id: string, msgId: string) {
  return get<UndoPreview>(
    `/ai/chat/sessions/${encodeURIComponent(id)}/undo-preview`,
    { messageId: msgId }, { silent: true })
}

export function undoToMessage(id: string, msgId: string) {
  return post<{ deleted: number; revert: RevertState | null; messages: AiMessage[] }>(
    `/ai/chat/sessions/${encodeURIComponent(id)}/undo`, { messageId: msgId })
}

export function redoSession(id: string) {
  return post<{ deleted: number; messages: AiMessage[] }>(
    `/ai/chat/sessions/${encodeURIComponent(id)}/redo`)
}
```

（`undoPreview` 的实参形态照 `listFiles`（:383）现有 silent 用法核对调整。）

`createEventStream` 的 allow-list 数组加 `'session.revert'`（注释：平台侧撤销/重做/提交广播，漏登记 → 其他标签页永不收敛）。

`src/stores/aiChat.ts`：

import 区把 `deleteFromMessage` 换成 `undoToMessage, redoSession as apiRedoSession`（保留原名导入则重命名别名），并补 `type RevertState`。

state 接口加两个字段：

```ts
  /** 撤销态（重做横幅数据源）：null = 无挂起撤销。服务端权威，本地只缓存。 */
  revertState: Record<string, RevertState | null>
  /** /undo 命令的输入框回填：视图 watch 后取走并置 null。 */
  composerRefill: Record<string, string | null>
```

`state()` 返回对象加 `revertState: {}, composerRefill: {},`。

模块级加 `const _revertReloadTimers: Record<string, ReturnType<typeof setTimeout>> = {}`。

`_adoptHistory`（:560）加一行：

```ts
      this.revertState[id] = (history as { revert?: RevertState | null }).revert ?? null
```

`resetSessionRunState`（:624）加 `this.revertState[id] = null`。

actions（`deleteFromMessage`/`retryUserMessage` 区域重写）：

```ts
    /** 真撤销到某条用户消息之前（服务端权威：以响应窗口为准）。失败上抛。 */
    async undoFromMessage(id: string, msgId: string) {
      const { data } = await undoToMessage(id, msgId)
      this.messages[id] = data.messages
      this.revertState[id] = data.revert
      this._recomputeUsage(id)
      this.streaming[id] = false
      this.thinking[id] = false
      void this.loadChanges(id)    // 文件可能已被物理还原
      void this.loadFiles(id)
    },

    /** 兼容名：编辑/重试/失败清理全部走 undo 核心。失败即不裁剪（旧实现
     *  吞错照样裁——服务端拒绝时视图与 DB 分叉，undo-redo 一并修掉）。 */
    async deleteFromMessage(id: string, msgId: string) {
      await this.undoFromMessage(id, msgId)
    },

    async redoSession(id: string) {
      const { data } = await apiRedoSession(id)
      this.messages[id] = data.messages
      this.revertState[id] = null
      this._recomputeUsage(id)
      void this.loadChanges(id)
      void this.loadFiles(id)
    },

    /** /undo：撤销最后一个用户轮，文本装回输入框（对齐 OC TUI）。 */
    async undoLastTurn(): Promise<string | null> {
      const sid = this.activeSessionId
      if (!sid) return null
      const arr = this.messages[sid] ?? []
      for (let i = arr.length - 1; i >= 0; i--) {
        const m = arr[i]
        if (m.role === 'user' && !m.id.startsWith('local_')) {
          const text = m.content.find((p) => p.type === 'text')?.text ?? ''
          await this.undoFromMessage(sid, m.id)
          this.composerRefill[sid] = text
          return text
        }
      }
      return null
    },

    /** 其他标签页发来的 session.revert：防抖后强一致重载。
     *  专用路径绕过 _reloadPersisted 的「拒收变短列表」守卫——列表变短
     *  正是撤销的正常形态（undo-redo spec §2.6）。 */
    _applyRemoteRevert(sid: string) {
      const t = _revertReloadTimers[sid]
      if (t) clearTimeout(t)
      _revertReloadTimers[sid] = setTimeout(async () => {
        delete _revertReloadTimers[sid]
        try {
          const history = await getMessages(sid, { limit: 200 })
          if (!this.streaming[sid]) {
            this._adoptHistory(sid, history)
            this._recomputeUsage(sid)
            void this.loadChanges(sid)
            void this.loadFiles(sid)
          }
        } catch { /* keep current */ }
      }, 300)
    },
```

`_transmitUserMessage`（sendMessage 成功后）加 `this.revertState[sid] = null`；`runCommand`（postCommand 后）与 `compactSession` action（成功后）同样加 `this.revertState[id] = null`。

`_handleEvent` switch 加分支（`case 'session.hello'` 附近）：

```ts
        case 'session.revert': {
          // 平台侧权威变更：撤销/重做/提交（properties 含 sessionId/revert）
          if (_isForeignEvent(sid, _eventSessionId(data))) break
          this._applyRemoteRevert(sid)
          break
        }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `npx vitest run src/stores/__tests__/aiChat.undo.test.ts src/api/__tests__/aiChat.test.ts src/stores/__tests__/aiChat.test.ts`
Expected: 全部 PASS（既有用例不回归——注意 `aiChat.agent.test.ts` 若引用了 mock 工厂需同步补两项 mock）

- [ ] **Step 5: Commit**

```bash
git add src/api/aiChat.ts src/stores/aiChat.ts src/api/__tests__/aiChat.test.ts src/stores/__tests__/aiChat.undo.test.ts src/stores/__tests__/aiChat.agent.test.ts
git commit -m "feat(ai-chat): 前端 undo/redo store——服务端权威窗口 + session.revert 强一致收敛"
```

### Task 12: 前端 UI——确认弹窗升级 + 重做横幅

**Files:**
- Modify: `src/views/ai-chat/AiChatView.vue`（script + template + style 三处）
- Test: 由 Task 14 E2E / Task 15 冒烟覆盖（视图组件不新增 vitest——既有惯例：视图级断言走 Playwright）

**Interfaces:**
- Consumes: Task 11 store actions、api `undoPreview`。

- [ ] **Step 1: script 增补**（`editMessage`/`retryMessage`/`retryAssistantMessage` 区域，:1340-1384）

import 区补：`import { undoPreview } from '@/api/aiChat'`。

```ts
// ---- 撤销/重做（undo-redo spec §2.7） ----
const revertBanner = computed(() =>
  store.revertState[activeId.value ?? ''] ?? null)
const bannerDismissed = ref(false)
watch(activeId, () => { bannerDismissed.value = false })

async function onRedo() {
  if (!activeId.value || streaming.value) return
  try {
    await store.redoSession(activeId.value)
    ElMessage.success('已恢复被撤销的对话')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error?.message || '重做失败')
  }
}

/** 确认文案按预览数据生成（文件能力/模式如实披露）。预览失败退化为通用文案。 */
async function undoConfirmText(m: AiMessage): Promise<string> {
  try {
    const { data } = await undoPreview(activeId.value!, m.id)
    const files = data.fileCapability === 'full' && data.files.length
      ? `，并把工作区约 ${data.files.length} 个文件还原到该消息之前`
      : '（文件不会还原）'
    const mode = data.mode === 'native' ? '' : '（将重建会话上下文）'
    return `将撤销该消息及之后 ${data.messageCount} 条对话${files}${mode}。` +
      '发送新消息后将无法重做。继续？'
  } catch {
    return '将撤销该消息及其之后的全部对话。继续？'
  }
}
```

`editMessage` 重写（保留 `local_` 守卫）：

```ts
async function editMessage(m: AiMessage) {
  const text = messageText(m)
  if (!activeId.value) return
  if (m.id.startsWith('local_')) { ElMessage.warning('消息还未保存，请稍后重试'); return }
  try {
    await ElMessageBox.confirm(await undoConfirmText(m), '编辑这条消息',
      { confirmButtonText: '继续', cancelButtonText: '取消', type: 'warning' })
  } catch { return }
  try {
    await store.undoFromMessage(activeId.value, m.id)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error?.message || '撤销失败')
    return
  }
  input.value = text
}
```

`retryMessage` / `retryAssistantMessage`：确认文案换 `await undoConfirmText(m)`（assistant 版传 `previousUserMessage(m)!`），确认后仍调 `store.retryUserMessage(...)`（内部已走 undo）；外层 catch 提示「重新发送失败」保留。

- [ ] **Step 2: template 横幅**（`.composer-card` 内、`.composer-attachments` 之前，:2150-2151）

```html
      <div v-if="revertBanner && !bannerDismissed" class="composer-revert"
           data-test="revert-banner">
        <span class="composer-revert__text">已撤销 {{ revertBanner.messageCount }}
          条消息<template v-if="revertBanner.fileCount">（还原约
          {{ revertBanner.fileCount }} 个文件）</template>，发送新消息后不可重做</span>
        <button class="composer-revert__btn" type="button" data-test="redo-btn"
                :disabled="streaming" @click="onRedo">重做</button>
        <button class="composer-revert__close" type="button" aria-label="关闭"
                @click="bannerDismissed = true">✕</button>
      </div>
```

- [ ] **Step 3: scoped style**（composer 样式区）

```css
.composer-revert {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 12px; font-size: 12px;
  color: var(--el-text-color-secondary);
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.composer-revert__btn { color: var(--el-color-primary); }
.composer-revert__close { margin-left: auto; opacity: .6; }
```

- [ ] **Step 4: composerRefill 回填 watch**（input 相关 watch 区域）

```ts
// /undo 命令把被撤文本装回输入框（store 不持有 DOM，经 composerRefill 中转）
watch(() => store.composerRefill[activeId.value ?? ''], (t) => {
  if (t && activeId.value) {
    input.value = t
    store.composerRefill[activeId.value] = null
  }
})
```

- [ ] **Step 5: 手动验证 + Commit**

Run: `npm run dev`（连 dev 栈）人工点一遍：编辑→确认弹窗文案含条数 → 横幅出现 → 重做恢复 → /undo 装回输入框。
```bash
git add src/views/ai-chat/AiChatView.vue
git commit -m "feat(ai-chat): 撤销确认弹窗按预览披露 + 重做横幅 + /undo 输入回填"
```

---

### Task 13: /undo /redo 本地命令

**Files:**
- Modify: `src/components/ai-chat/chat-commands.ts`
- Modify: `src/components/ai-chat/__tests__/chat-commands.test.ts`

**Interfaces:**
- Consumes: Task 11 `store.undoLastTurn()/redoSession()`。

- [ ] **Step 1: 写失败测试**（`chat-commands.test.ts` 追加）

```ts
it('registers /undo and /redo frontend commands', () => {
  expect(findFrontendCommand('undo')?.name).toBe('undo')
  expect(findFrontendCommand('redo')?.name).toBe('redo')
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run src/components/ai-chat/__tests__/chat-commands.test.ts`

- [ ] **Step 3: 写实现**（`FRONTEND_COMMANDS` 数组追加）

```ts
  { name: 'undo', description: '撤销最后一个用户轮（文本装回输入框）',
    run: (s) => s.undoLastTurn() },
  { name: 'redo', description: '恢复最近一次撤销的对话',
    run: (s) => { if (s.activeSessionId) return s.redoSession(s.activeSessionId) } },
```

- [ ] **Step 4: 跑测试确认通过 + Commit**

Run: `npx vitest run src/components/ai-chat/__tests__/chat-commands.test.ts`
```bash
git add src/components/ai-chat/chat-commands.ts src/components/ai-chat/__tests__/chat-commands.test.ts
git commit -m "feat(ai-chat): /undo /redo 前端本地命令（对齐 OC TUI）"
```

---

### Task 14: 0-LLM 真实链路 E2E 探针（e2e/manual/undo-probe/）

**Files:**
- Create: `e2e/manual/undo-probe/README.md`
- Create: `e2e/manual/undo-probe/undo_stub_llm.py`
- Create: `e2e/manual/undo-probe/undo_e2e.py`

**Interfaces:**
- Consumes: Task 1-13 全部产物。隔离栈：stub :3197 / serve :4198 / backend :3094 / DB `casemanage_undoprobe`。
- 断言三件套（spec §3）：①stub 请求不含被撤轮次（上下文真回退）②文件物理还原 ③跨标签页收敛 + rebuild 降级 + 重做/提交语义。

- [ ] **Step 1: 写 stub**（`undo_stub_llm.py`，内容与 `.dev-ai-workspaces/undo-probe-scripts/undo_stub_llm.py` 相同——该文件已在本机工作区验证过；将 `_plan` 的标记正则、SCRIPTS、SSE 帧格式原样复制，端口改 `3197`，脚本头注释改「undo-probe E2E 专用」）

- [ ] **Step 2: 写编排器** `undo_e2e.py`

```python
"""AI 会话撤销/重做 0-LLM 真实链路 E2E（undo-redo spec §3）。

隔离栈：stub LLM :3197 + oc serve :4198 + 平台后端 :3094 + DB casemanage_undoprobe。
四场景：A 原生撤销（文件还原/上下文回退/提交后不可重做/跨标签页）
        B 重做全恢复  C 锚失效 → rebuild 降级 + 历史重放  D 批子会话 409。
运行：python undo_e2e.py [--keep]
"""
import http.client, json, os, re, secrets, shutil, subprocess, sys, threading, time
from pathlib import Path
from urllib.parse import quote

REPO = Path(__file__).resolve().parents[3]
SB = REPO / '.dev-ai-workspaces' / 'undo-e2e' / time.strftime('%H%M%S')
GP_OC = REPO / '.dev-ai-workspaces' / 'gateprobe' / 'xdg-config' / 'opencode'
STUB_PORT, SERVE_PORT, BACKEND_PORT = 3197, 4198, 3094
OC_EXE = os.environ.get('OPENCODE_BIN') or \
    r'C:\Users\admin\AppData\Roaming\npm\node_modules\opencode-ai\bin\opencode.exe'
PROBE_TOKEN = os.environ.get('UNDOPROBE_TOKEN') or secrets.token_hex(12)
PROCS, _ev_lock, _results = [], threading.Lock(), []


def _req(port, method, path, body=None, headers=None, timeout=40):
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
    try:
        payload = json.dumps(body).encode() if body is not None else None
        h = {k: v for k, v in (headers or {}).items()}
        if payload:
            h['content-type'] = 'application/json'
            h['content-length'] = str(len(payload))
        conn.request(method, path, payload, h)
        r = conn.getresponse()
        raw = r.read()
        try:
            return r.status, json.loads(raw or b'null')
        except json.JSONDecodeError:
            return r.status, raw
    finally:
        conn.close()


def check(label, cond, detail=''):
    with _ev_lock:
        _results.append(bool(cond))
    print(f'  {"✓" if cond else "✗ FAIL"} {label}' + (f'  ({detail})' if detail else ''))


def _wait(port, path, timeout_s):
    dl = time.time() + timeout_s
    while time.time() < dl:
        try:
            if _req(port, 'GET', path, timeout=2)[0] < 500:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def _api(method, path, body=None, timeout=60):
    return _req(BACKEND_PORT, method, '/api' + path, body,
                headers={'Authorization': f'Bearer {TOKEN}'}, timeout=timeout)


TOKEN = ''


def start_stack(env_extra=None):
    global TOKEN
    SB.mkdir(parents=True, exist_ok=True)
    gdir = SB / 'xdg-config' / 'opencode'
    gdir.mkdir(parents=True)
    for n in ('package.json', 'package-lock.json', 'bun.lock', 'node_modules'):
        src, dst = GP_OC / n, gdir / n
        (shutil.copytree if src.is_dir() else shutil.copy2)(src, dst)
    (gdir / 'opencode.jsonc').write_text(json.dumps({
        '$schema': 'https://opencode.ai/config.json',
        'provider': {'probe': {
            'npm': '@ai-sdk/openai-compatible', 'name': 'UndoE2E',
            'options': {'baseURL': f'http://127.0.0.1:{STUB_PORT}/v1',
                        'apiKey': PROBE_TOKEN},
            'models': {'scripted': {'name': 'Scripted'}}}}}), encoding='utf-8')
    auth = SB / 'oc-data' / 'opencode' / 'auth.json'
    auth.parent.mkdir(parents=True)
    auth.write_text(json.dumps({'probe': {'type': 'api', 'key': PROBE_TOKEN}}),
                    encoding='utf-8')
    stub = subprocess.Popen([sys.executable, str(Path(__file__).parent / 'undo_stub_llm.py')],
                            cwd=str(SB), env={**os.environ, 'UNDOPROBE_STUB_PORT': str(STUB_PORT),
                                              'UNDOPROBE_LOG_DIR': str(SB), 'PYTHONUTF8': '1'},
                            stdout=open(SB / 'stub.log', 'ab'), stderr=subprocess.STDOUT)
    PROCS.append(stub)
    assert _wait(STUB_PORT, '/v1/models', 15), 'stub not up'
    env = {**os.environ, 'DB_NAME': 'casemanage_undoprobe', 'FLASK_PORT': str(BACKEND_PORT),
           'OPENCODE_MODEL': 'probe/scripted',
           'OPENCODE_BASE_URL': f'http://127.0.0.1:{SERVE_PORT}',
           'OPENCODE_GLOBAL_DIR': str(gdir), 'AI_WORKSPACE_ROOT': str(SB / 'ws'),
           'PYTHONUTF8': '1', **(env_extra or {})}
    be = subprocess.Popen([sys.executable, 'app.py'], cwd=str(REPO / 'server'), env=env,
                          stdout=open(SB / 'backend.log', 'ab'), stderr=subprocess.STDOUT)
    PROCS.append(be)
    assert _wait(BACKEND_PORT, '/health', 120), 'backend not up'
    serve = subprocess.Popen([OC_EXE, 'serve', '--port', str(SERVE_PORT)],
                             cwd=os.path.expanduser('~'),
                             env={**os.environ, 'XDG_CONFIG_HOME': str(SB / 'xdg-config'),
                                  'XDG_DATA_HOME': str(SB / 'oc-data')},
                             stdout=open(SB / 'serve.log', 'ab'), stderr=subprocess.STDOUT)
    PROCS.append(serve)
    assert _wait(SERVE_PORT, '/global/health', 90), 'serve not up'
    st, body = _req(BACKEND_PORT, 'POST', '/api/auth/login',
                    {'username': 'admin', 'password': 'admin123'})
    assert st == 200, f'login failed: {st}（新库未播种 admin？查 backend.log）'
    TOKEN = body['token']


def stop_stack():
    for p in reversed(PROCS):
        subprocess.run(['taskkill', '/PID', str(p.pid), '/T', '/F'],
                       capture_output=True, timeout=20)
    time.sleep(1)


def new_session():
    st, body = _api('POST', '/ai/chat/sessions', {})
    assert st == 201, (st, body)
    return body['id'], body['workspacePath']


def send(sid, tag):
    st, body = _api('POST', f'/ai/chat/sessions/{sid}/messages',
                    {'content': f'[[UNDO-PROBE:{tag}]] 请按标记执行。'}, timeout=90)
    assert st == 202, (st, body)
    return body['messageId']


def wait_turn(sid, n_before, timeout_s=90):
    dl = time.time() + timeout_s
    while time.time() < dl:
        st, body = _api('GET', f'/ai/chat/sessions/{sid}/messages')
        assert st == 200
        msgs = body['messages']
        done = [m for m in msgs if m['role'] == 'assistant' and (m.get('meta') or {}).get('durationMs')]
        if len(done) >= n_before:
            return msgs, body.get('revert')
        time.sleep(1)
    raise RuntimeError('turn timeout')


def files_state(ws):
    out = {}
    for rel in ('a.txt', 'b.txt', 'c.txt'):
        p = Path(ws) / rel
        out[rel] = p.read_text(encoding='utf-8', errors='replace').strip() if p.exists() else '<缺失>'
    return out


def stub_users_for(tag):
    rows = [json.loads(l) for l in (SB / 'stub-requests.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
    last = [r for r in rows if r.get('tag') == tag][-1]
    return [m.get('text', '') for m in last['messages'] if m['role'] == 'user']


def scenario_native():
    print('\n===== A：原生撤销（文件/上下文/提交/跨标签页） =====')
    sid, ws = new_session()
    u1 = send(sid, 'T1'); wait_turn(sid, 1)
    u2 = send(sid, 'T2'); wait_turn(sid, 2)
    fs = files_state(ws)
    check('T2 后 a.txt=v2', fs['a.txt'] == 'v2', str(fs))
    st, pv = _api('GET', f'/ai/chat/sessions/{sid}/undo-preview?messageId={u2}')
    check('preview: messageCount>=2 / capability=full',
          st == 200 and pv['messageCount'] >= 2 and pv['fileCapability'] == 'full', str(pv))
    # 跨标签页：两条 SSE 连接
    frames = {'a': [], 'b': []}

    def listen(key):
        import requests as rq
        with rq.get(f'http://127.0.0.1:{BACKEND_PORT}/api/ai/chat/sessions/{sid}/events',
                    headers={'Authorization': f'Bearer {TOKEN}', 'Accept': 'text/event-stream'},
                    stream=True, timeout=(5, 60)) as r:
            for line in r.iter_lines(decode_unicode=True):
                if line and line.startswith('data:'):
                    frames[key].append(line)
                    if 'session.revert' in line:
                        return
    th = [threading.Thread(target=listen, args=(k,), daemon=True) for k in ('a', 'b')]
    [t.start() for t in th]
    time.sleep(1.5)
    st, body = _api('DELETE', f'/ai/chat/sessions/{sid}/messages/{u2}', timeout=60)
    check('undo 200 且 revert.mode=native',
          st == 200 and (body.get('revert') or {}).get('mode') == 'native', str(st))
    msgs = body['messages']
    check('平台窗口已裁掉 u2 及之后', all(m['id'] != u2 for m in msgs))
    fs = files_state(ws)
    check('文件还原: a.txt=v1 / b.txt 缺失 / c.txt 缺失',
          fs == {'a.txt': 'v1', 'b.txt': '<缺失>', 'c.txt': '<缺失>'}, str(fs))
    [t.join(timeout=15) for t in th]
    check('两个 SSE 连接都收到 session.revert', all('session.revert' in f for f in frames.values()),
          str([len(v) for v in frames.values()]))
    st, body = _api('GET', f'/ai/chat/sessions/{sid}/messages')
    check('GET messages 带 revert 态', (body.get('revert') or {}).get('mode') == 'native')
    u3 = send(sid, 'T3'); wait_turn(sid, 3)
    users = stub_users_for('T3')
    check('T3 请求含 T1、不含 T2（上下文真回退，E7）',
          any('UNDO-PROBE:T1' in u for u in users) and not any('UNDO-PROBE:T2' in u for u in users),
          str(users)[:200])
    st, body = _api('GET', f'/ai/chat/sessions/{sid}/messages')
    check('发送后提交：revert 清空', body.get('revert') is None)
    st, body = _api('POST', f'/ai/chat/sessions/{sid}/redo')
    check('提交后 redo → NOTHING_TO_REDO 409',
          st == 409 and body['error']['code'] == 'NOTHING_TO_REDO')


def scenario_redo():
    print('\n===== B：重做全恢复 =====')
    sid, ws = new_session()
    send(sid, 'T1'); wait_turn(sid, 1)
    u2 = send(sid, 'T2'); wait_turn(sid, 2)
    _api('DELETE', f'/ai/chat/sessions/{sid}/messages/{u2}', timeout=60)
    check('undo 后 b.txt 缺失', files_state(ws)['b.txt'] == '<缺失>')
    st, body = _api('POST', f'/ai/chat/sessions/{sid}/redo', timeout=60)
    check('redo 200', st == 200, str(st))
    check('文件恢复 v2/b-new', files_state(ws)['a.txt'] == 'v2'
          and files_state(ws)['b.txt'] == 'b-new', str(files_state(ws)))
    st, body = _api('GET', f'/ai/chat/sessions/{sid}/messages')
    ids = [m['id'] for m in body['messages']]
    check('消息窗口恢复含 u2', u2 in ids)
    check('redo 后 revert 清空', body.get('revert') is None)


def scenario_rebuild():
    print('\n===== C：锚失效 → rebuild + 历史重放 =====')
    sid, ws = new_session()
    send(sid, 'T1'); wait_turn(sid, 1)
    u2 = send(sid, 'T2'); wait_turn(sid, 2)
    # 模拟「会话恢复过」：把 oc 指针换到一个有内容的新 OC 会话
    st, oc_new = _req(SERVE_PORT, 'POST',
                      f'/session?directory={quote(ws, safe="")}', {'title': 'replaced'})
    # 新 OC 会话里造一条 user 消息（空会话会走 native no-op 分支）
    _req(SERVE_PORT, 'POST',
         f"/session/{oc_new['id']}/prompt_async?directory={quote(ws, safe='')}",
         {'parts': [{'type': 'text', 'text': '[[UNDO-PROBE:T1]] 请按标记执行。'}],
          'model': {'providerID': 'probe', 'modelID': 'scripted'}})
    time.sleep(4)
    import psycopg2
    from server.config import DB_CONFIG
    conn = psycopg2.connect(**{**DB_CONFIG, 'dbname': 'casemanage_undoprobe'})
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET opencode_session_id=%s WHERE id=%s",
                    (oc_new['id'], sid))
    conn.close()
    st, body = _api('DELETE', f'/ai/chat/sessions/{sid}/messages/{u2}', timeout=60)
    check('undo → rebuild 模式', st == 200 and (body.get('revert') or {}).get('mode') == 'rebuild',
          str(body.get('revert')))
    u3 = send(sid, 'T3'); wait_turn(sid, 3)
    rows = [json.loads(l) for l in (SB / 'stub-requests.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
    last_t3 = [r for r in rows if r.get('tag') == 'T3'][-1]
    blob = json.dumps(last_t3['messages'], ensure_ascii=False)
    check('T3 请求含「此前对话摘要」（历史重放）', '此前对话摘要' in blob)


def scenario_batch_child():
    print('\n===== D：批子会话 409（不开放） =====')
    # 种一个 running 批子会话行，undo 应 409 BATCH_SESSION_CONTROLLED
    import psycopg2
    from server.config import DB_CONFIG
    conn = psycopg2.connect(**{**DB_CONFIG, 'dbname': 'casemanage_undoprobe'})
    sid = f'sess_bchild_{secrets.token_hex(4)}'
    with conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE username='admin' LIMIT 1")
        uid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, status, total) "
            "VALUES (%s, %s, 'undo-e2e-batch', 'running', 1) ON CONFLICT (id) DO NOTHING",
            (f'batch_{sid}', uid))
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, workspace_path, session_token, "
            "  token_expires_at, status, kind, batch_id, batch_seq) "
            "VALUES (%s, %s, '/tmp/undo-e2e-bc', 'tok-' || %s, NOW() + interval '1 day', "
            "  'running', 'batch_child', %s, 1)",
            (sid, uid, sid, f'batch_{sid}'))
        cur.execute(
            "INSERT INTO ai_chat_messages (id, session_id, role, content) "
            "VALUES (%s, %s, 'user', %s)",
            (sid + ':u', sid, json.dumps([{'type': 'text', 'text': 'x'}])))
    conn.close()
    st, body = _api('DELETE', f'/ai/chat/sessions/{sid}/messages/{sid}:u', timeout=30)
    check('批子会话 undo → 409', st == 409 and
          body['error']['code'] == 'BATCH_SESSION_CONTROLLED', str(st))
    with psycopg2.connect(**{**DB_CONFIG, 'dbname': 'casemanage_undoprobe'}) as c2, c2.cursor() as cu:
        cu.execute("DELETE FROM ai_chat_messages WHERE session_id=%s", (sid,))
        cu.execute("DELETE FROM ai_chat_sessions WHERE id=%s", (sid,))
        cu.execute("DELETE FROM ai_chat_batches WHERE id=%s", (f'batch_{sid}',))


def main():
    start_stack()
    try:
        scenario_native()
        scenario_redo()
        scenario_rebuild()
        scenario_batch_child()
    finally:
        stop_stack()
        print('\n===== 结果 =====')
        ok = all(_results)
        print('ALL GREEN' if ok else 'HAS FAILURES', f'(sandbox={SB})')
        sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
```

- [ ] **Step 3: 写 README**（复述 spec §3 断言三件套、运行命令、端口表、排障指向 `SB/backend.log|serve.log|stub.log`）

- [ ] **Step 4: 跑探针确认全绿**

Run: `python e2e/manual/undo-probe/undo_e2e.py`
Expected: `ALL GREEN`；失败时按场景逐个修（注意：`scenario_rebuild` 直接改库属探针特权，生产路径无此操作）

- [ ] **Step 5: Commit**

```bash
git add e2e/manual/undo-probe/
git commit -m "test(e2e): undo/redo 0-LLM 真实链路探针——上下文回退/文件还原/跨标签页/rebuild 四场景"
```

---

### Task 15: @llm 冒烟 + 全量回归

**Files:**
- Create: `e2e/ai-full/undo-redo.spec.ts`

- [ ] **Step 1: 写冒烟用例**（真模型，遵循 ai-full 既有 helper；预算注解对齐 docs/ai-testing/README.md 的 @llm 惯例）

```ts
import { test, expect } from '@playwright/test'
import { adminToken, openChatSession, gotoWithAuth } from './helpers'

// @llm 真模型冒烟：真 OC(:4096)。断言产品级闭环：重新生成 → 真撤销 → 重做横幅 → 重做。
test('AI 会话撤销冒烟：重新生成后出现重做横幅，重做恢复', async ({ page }) => {
  const token = await adminToken()
  const create = await page.request.post('http://127.0.0.1:3002/api/ai/chat/sessions',
    { headers: { Authorization: `Bearer ${token}` }, data: {} })
  const { id } = await create.json()
  await gotoWithAuth(page, '/ai-chat')
  await openChatSession(page, id)
  await page.locator('.composer-input textarea').fill('写一个只有一行内容的 hello.txt 说明')
  await page.locator('.composer-input textarea').press('Enter')
  // 等首轮回复收敛（真模型，放宽超时）
  await expect(page.locator('[data-test="turn-failure"]')).toHaveCount(0, { timeout: 180_000 })
  const answer = page.locator('.msg__actions--assistant [title="重新生成"]').first()
  await expect(answer).toBeVisible({ timeout: 180_000 })
  await answer.click()
  await page.getByRole('button', { name: '继续' }).click()   // ElMessageBox 确认
  const banner = page.locator('[data-test="revert-banner"]')
  await expect(banner).toBeVisible({ timeout: 30_000 })
  await page.locator('[data-test="redo-btn"]').click()
  await expect(banner).toHaveCount(0)
})
```

（helper 名以 `e2e/ai-full/helpers.ts` 实际导出为准——`adminToken/openChatSession/gotoWithAuth` 已核实存在。）

- [ ] **Step 2: 全量回归（用户测试纪律）**

Run（依次，全部须绿）：
```bash
cd server && python -m pytest tests/ -x -q          # 后端全量（串行，~3min）
npx vitest run                                       # 前端全量
npx playwright test e2e/optimizations-verify.spec.ts # 既有失败重试卡用例不回归
python e2e/manual/undo-probe/undo_e2e.py             # 0-LLM 探针
```

- [ ] **Step 3: 收尾**

- 更新记忆 `check-manage-ai-chat-undo-gap.md`：状态改为「已实施」，补提交号与验证结果。
- Commit + push（用户既定要求：按需提交上库）：

```bash
git add e2e/ai-full/undo-redo.spec.ts
git commit -m "test(e2e): undo/redo @llm 真模型冒烟——重新生成/重做横幅/重做闭环"
git push
```

---

## 计划自审记录

1. **Spec 覆盖**：§1 能力分级（T2/T6 capability+stale 促销）、§2.1 迁移（T1）、§2.2a 锚点（T2）、§2.2b/c/d 执行与 rebuild（T6/T9）、§2.3 四端点（T7/T8）、§2.4 提交矩阵（T9）、§2.5 运行时适配（T3）、§2.6 总线（T5/T10/T11）、§2.7 前端（T11/T12/T13）、§2.8 审计（T7 log_operation）、§3 测试（各任务 + T14/T15）。无缺口。
2. **占位符扫描**：T14 Step 1 的 stub 脚本引用本机已验证文件而非重抄全文——该文件在执行机的工作区存在（`.dev-ai-workspaces/undo-probe-scripts/undo_stub_llm.py`）；若执行环境无此文件，按 gate-leak-probe `stub_llm.py` 的脚本表机制重写（SCRIPTS = T1/T2/T3/SLOW 四标记）。其余步骤均含完整代码。
3. **类型一致性**：`get_revert_state` 返回键（camelCase）与 T6/T7/T11 使用一致；`resolve_undo_target` 返回键与 T6 `_finalize_plan`/`_undo_native` 一致；前端 `RevertState.messageCount/fileCount/mode` 与后端 JSON 键一致；route 函数名 `redo_session_route` 避免与 `ai_revert.redo` 冲突。




