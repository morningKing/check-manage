# 批任务会话对外接管页 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 外部系统通过 API 为批子会话签发短时接管令牌，人工打开公开会话页（仅历史消息+输入框）与 AI 对话；paused 子会话接管轮完成后回到 paused（repark）。

**Architecture:** 新公开蓝图 `/external/takeover/*`（无 JWT，令牌三元组校验+双层限速，kefu_public 同纪律）；令牌表只存 SHA-256 哈希；发消息复用 `continue_child` 通道（准入扩展 paused + `continue_repark` 标记，worker 完成路径前置 repark 转移）；前端 `meta.public` 无壳路由复用 MarkdownView；实时性用 3s 轮询（worker 回合结束才整体落库，SSE 无增益——见 spec 修订 §6）。

**Tech Stack:** Flask + PostgreSQL（CAS/fencing 不变式）；Vue 3 + Element Plus + md-editor-v3（MarkdownView 复用）；pytest（真库 casemanage_test）+ vitest + Playwright。

**Spec:** `docs/superpowers/specs/2026-10-25-batch-session-external-takeover-design.md`（D1 令牌链接 / D2 单子会话 / D3 continue 扩展 paused；§10 三个默认值已获用户确认）。

## Global Constraints

- 未挂 JWT 的公开路由全系统仅允许三处：`/auth/login`、`/kefu/*`、`/external/takeover/*`（评审不变式，新公开端点必须落在新蓝图内）
- 令牌：`tk_` + `secrets.token_urlsafe(32)`，库中仅存 SHA-256；TTL 默认 24h、钳制 1h–168h（7d）；可吊销；非一次性
- 无效/过期/吊销令牌一律 404 `{'error': '链接无效或已过期'}`（不区分原因，防存在性探测），响应带 `Cache-Control: no-store`
- 接管发送 prompt ≤ 20000 字符（与开放 API 一致）；准入状态 = 终态（completed/failed/cancelled/needs_review）或 paused；pending/running → 409 `TASK_RUNNING`
- 消息读端点出参仅 `id/role/content/created_at`（`batch_repo.get_child_messages` 现成口径，不新增字段）
- 限速默认：令牌桶 10/min + 200/day；IP 桶 60/min + 1000/day（对齐 kefu 地板值）；`utils.rate_limit.RateLimiter`
- 令牌不写 localStorage，仅 URL + 内存
- 迁移必须双注册：`server/migrations/` 文件 + `server/app.py` 显式 boot 块（照抄 2026_10_21 块样式，app.py:592）
- CAS 不变式不可破坏：`transition_child` 仍是终态写入唯一入口；fencing_token 必填
- 每个任务：先写失败测试 → 跑挂 → 实现 → 跑绿 → 独立 commit

---

### Task 1: spec 修订——实时性从 SSE 改为轮询

**Files:**
- Modify: `docs/superpowers/specs/2026-10-25-batch-session-external-takeover-design.md`（§6 与 §5 的 events 端点）

**Interfaces:** 无代码接口。后续任务依赖本修订：外部蓝图**不含** events 端点，前端只做轮询。

- [ ] **Step 1: 修订 spec**

§5 删除 `GET /external/takeover/session/events` 行，替换为：

```markdown
  - ~~SSE events 端点~~（2026-10-25 修订取消）：批子会话由 worker 独家写入、
    回合结束才整体落库（`batch_engine._persist_conversation`），流式传输对该
    数据流无增益；内部会话 SSE 与 OpenCode 事件代理/持久化归属深度耦合
    （`ai_chat.py:1447`），不值得为新鉴权面复制。实时性改为前端 3s 轮询
    messages+status。
```

§6 的「实时性：SSE 增量追加 + 断线降级轮询兜底」改为：

```markdown
  - 实时性：3s 轮询 messages+childStatus（页面隐藏时暂停，visibilitychange）；
    发送后立即触发一次拉取。
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-10-25-batch-session-external-takeover-design.md
git commit -m "docs(spec): 接管页实时性修订 SSE→轮询（worker 回合末整体落库，流式无增益）"
```

---

### Task 2: 迁移——batch_takeover_tokens 表 + continue_repark 列

**Files:**
- Create: `server/migrations/2026_10_25_batch_takeover.py`
- Modify: `server/app.py`（在 2026_10_21 块之后加 boot 块，app.py:592 附近）

**Interfaces:**
- Produces: 表 `batch_takeover_tokens(id, token_hash UNIQUE, api_key_id, owner_user_id, batch_id, session_id, expires_at, revoked_at, created_at, last_used_at)`；列 `ai_chat_sessions.continue_repark BOOLEAN NOT NULL DEFAULT false`。后续任务的 repo 函数直接依赖这两个结构。
- 不建外键（api_keys 可被删除；令牌生命周期由 TTL 约束，刻意解耦）。

- [ ] **Step 1: 写迁移文件**

```python
# -*- coding: utf-8 -*-
"""幂等迁移：批任务接管令牌表 + 子会话「接管续跑完成后重停」标记列。

continue_repark：paused 子会话经接管通道续跑时置 true；worker 收口时见到
true 则回到 paused 而非终态（不占 done/failed 计数），把控制权还给外部系统。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db


def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS batch_takeover_tokens ("
            " id TEXT PRIMARY KEY,"
            " token_hash TEXT NOT NULL UNIQUE,"
            " api_key_id TEXT NOT NULL,"
            " owner_user_id TEXT NOT NULL,"
            " batch_id TEXT NOT NULL,"
            " session_id TEXT NOT NULL,"
            " expires_at TIMESTAMPTZ NOT NULL,"
            " revoked_at TIMESTAMPTZ,"
            " created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
            " last_used_at TIMESTAMPTZ)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_takeover_tokens_session "
                    "ON batch_takeover_tokens (session_id)")
        cur.execute("ALTER TABLE ai_chat_sessions ADD COLUMN IF NOT EXISTS "
                    "continue_repark BOOLEAN NOT NULL DEFAULT false")
        conn.commit()
    print("batch takeover tokens migration ready.")


if __name__ == "__main__":
    run()
```

- [ ] **Step 2: app.py 注册 boot 块**

紧随 2026_10_21 块（`_m_se_att.run()` 之后的 `except` 结束处）追加，样式逐字对齐：

```python
# 批任务接管令牌表 + continue_repark 列（对外接管页，2026-10-25 spec）
try:
    import importlib.util as _ilu_bt
    _mp_bt = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'migrations', '2026_10_25_batch_takeover.py')
    _spec_bt = _ilu_bt.spec_from_file_location('_batch_takeover_boot', _mp_bt)
    _m_bt = _ilu_bt.module_from_spec(_spec_bt)
    _spec_bt.loader.exec_module(_m_bt)
    _m_bt.run()
except Exception as _e:
    logging.warning('batch takeover migration on boot failed: %s', _e)
```

（若 app.py 顶部未 import os/logging 则对齐相邻块的同名导入方式。）

- [ ] **Step 3: 真库验证迁移**

```bash
cd server && python migrations/2026_10_25_batch_takeover.py
python -c "from db import get_db; import psycopg2.extras;
conn=get_db().__enter__(); cur=conn.cursor();
cur.execute(\"SELECT column_name FROM information_schema.columns WHERE table_name='batch_takeover_tokens'\");
print([r[0] for r in cur.fetchall()]);
cur.execute(\"SELECT column_name FROM information_schema.columns WHERE table_name='ai_chat_sessions' AND column_name='continue_repark'\");
print(cur.fetchall())"
```

Expected: 打印 10 列名 + `[['continue_repark']]`。重复执行一次迁移确认幂等（无异常）。

- [ ] **Step 4: Commit**

```bash
git add server/migrations/2026_10_25_batch_takeover.py server/app.py
git commit -m "feat(takeover): 迁移 batch_takeover_tokens + ai_chat_sessions.continue_repark"
```

---

### Task 3: takeover_repo 令牌仓储

**Files:**
- Create: `server/utils/takeover_repo.py`
- Test: `server/tests/test_takeover_repo.py`

**Interfaces:**
- Consumes: Task 2 的表结构；`db.get_db`（真库测试走 conftest `db_conn` fixture，模式照抄 `test_batch_execution_safety_p0.py`）。
- Produces（Task 6/7 依赖，签名逐字）:
  - `create_takeover_token(owner_user_id: str, batch_id: str, session_id: str, api_key_id: str, ttl_hours: int = 24) -> tuple[str, datetime]`（返回明文 token 与过期时刻；TTL 钳制 1–168h）
  - `resolve_takeover_token(token_plain: str | None) -> dict | None`（无效/过期/吊销返回 None；命中返回含 `token_id, expires_at, owner_user_id, batch_id, session_id, child_status, batch_seq, batch_input_file, title, batch_name, batch_status`，并顺带 touch `last_used_at`）
  - `revoke_takeover_token(owner_user_id: str, batch_id: str, session_id: str, token_id: str) -> bool`（三元组全匹配才置 revoked_at；返回是否命中）

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""takeover_repo 真库测试：哈希存证/TTL 钳制/过期吊销失效/三元组越权拒绝。"""
import os, sys, uuid
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils import takeover_repo  # noqa: E402


@pytest.fixture
def takeover_ids(db_conn):
    uid = str(uuid.uuid4())
    bid, sid = f'bt_{uuid.uuid4().hex[:12]}', f'sess_{uuid.uuid4().hex[:12]}'
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s,%s,'x','tk user','developer')", (uid, f'tk_{uid[:8]}'))
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, status, total, done, failed) "
            "VALUES (%s,%s,'接管E2E','p','running',1,0,0)", (bid, uid))
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, title, status, batch_id, batch_seq) "
            "VALUES (%s,%s,'子任务1','paused',%s,1)", (sid, uid, bid))
    db_conn.commit()
    yield {'uid': uid, 'bid': bid, 'sid': sid}
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM batch_takeover_tokens WHERE session_id=%s", (sid,))
        cur.execute("DELETE FROM ai_chat_messages WHERE session_id=%s", (sid,))
        cur.execute("DELETE FROM ai_chat_sessions WHERE batch_id=%s", (bid,))
        cur.execute("DELETE FROM ai_chat_batches WHERE id=%s", (bid,))
        cur.execute("DELETE FROM users WHERE id=%s", (uid,))
    db_conn.commit()


def test_create_and_resolve_roundtrip(db_conn, takeover_ids):
    token, expires = takeover_repo.create_takeover_token(
        takeover_ids['uid'], takeover_ids['bid'], takeover_ids['sid'], 'ak-1')
    assert token.startswith('tk_')
    row = takeover_repo.resolve_takeover_token(token)
    assert row and row['session_id'] == takeover_ids['sid']
    assert row['batch_name'] == '接管E2E' and row['child_status'] == 'paused'
    # 库中只存哈希
    with db_conn.cursor() as cur:
        cur.execute("SELECT token_hash FROM batch_takeover_tokens WHERE session_id=%s",
                    (takeover_ids['sid'],))
        (h,) = cur.fetchone()
    assert token not in h and len(h) == 64


def test_expired_or_revoked_or_bogus_token_returns_none(db_conn, takeover_ids):
    token, _ = takeover_repo.create_takeover_token(
        takeover_ids['uid'], takeover_ids['bid'], takeover_ids['sid'], 'ak-1')
    with db_conn.cursor() as cur:  # 手动改过期
        cur.execute("UPDATE batch_takeover_tokens SET expires_at = NOW() - interval '1 minute' "
                    "WHERE session_id=%s", (takeover_ids['sid'],))
    db_conn.commit()
    assert takeover_repo.resolve_takeover_token(token) is None

    # 吊销：先 resolve 拿 token_id，再按 id 吊销（三元组匹配才生效）
    token2, _ = takeover_repo.create_takeover_token(
        takeover_ids['uid'], takeover_ids['bid'], takeover_ids['sid'], 'ak-1')
    token_id = takeover_repo.resolve_takeover_token(token2)['token_id']
    # 他人三元组吊销不命中
    assert takeover_repo.revoke_takeover_token('other-user', takeover_ids['bid'],
                                               takeover_ids['sid'], token_id) is False
    assert takeover_repo.resolve_takeover_token(token2) is not None
    # 属主吊销命中 → 立即失效
    assert takeover_repo.revoke_takeover_token(takeover_ids['uid'], takeover_ids['bid'],
                                               takeover_ids['sid'], token_id) is True
    assert takeover_repo.resolve_takeover_token(token2) is None
    # 重复吊销（已吊销）返回 False
    assert takeover_repo.revoke_takeover_token(takeover_ids['uid'], takeover_ids['bid'],
                                               takeover_ids['sid'], token_id) is False
    assert takeover_repo.resolve_takeover_token('tk_bogus') is None
    assert takeover_repo.resolve_takeover_token(None) is None


def test_ttl_clamped(db_conn, takeover_ids):
    _, exp = takeover_repo.create_takeover_token(
        takeover_ids['uid'], takeover_ids['bid'], takeover_ids['sid'], 'ak-1', ttl_hours=9999)
    assert exp - datetime.now(timezone.utc) <= timedelta(days=7, minutes=1)
```

> 注：Step 1 测试中 revoke 用例已按最终签名（`token_id`）写定。

- [ ] **Step 2: 跑挂**

```bash
cd server && python -m pytest tests/test_takeover_repo.py -q
```
Expected: FAIL（`ModuleNotFoundError: utils.takeover_repo`）

- [ ] **Step 3: 实现**

```python
"""批任务接管令牌仓储（对外接管页 spec D1）。

只存 SHA-256 哈希；resolve 时三元组联查会话与批（任一不存在即视为无效）。
令牌不建外键：api_keys 可被删除，令牌生命周期由 TTL 约束，刻意解耦。
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from psycopg2.extras import RealDictCursor

from db import get_db

MIN_TTL_H, MAX_TTL_H = 1, 24 * 7


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def create_takeover_token(owner_user_id: str, batch_id: str, session_id: str,
                          api_key_id: str, ttl_hours: int = 24) -> tuple[str, datetime]:
    ttl_h = max(MIN_TTL_H, min(MAX_TTL_H, int(ttl_hours or 24)))
    token = 'tk_' + secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=ttl_h)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO batch_takeover_tokens "
                "(id, token_hash, api_key_id, owner_user_id, batch_id, session_id, expires_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                ('tkt_' + secrets.token_urlsafe(8), _hash(token), api_key_id,
                 owner_user_id, batch_id, session_id, expires))
        conn.commit()
    return token, expires


def resolve_takeover_token(token_plain: str | None) -> dict | None:
    if not token_plain or len(token_plain) > 128:
        return None
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT t.id AS token_id, t.expires_at, t.owner_user_id,"
                " t.batch_id, t.session_id,"
                " s.status AS child_status, s.batch_seq, s.batch_input_file, s.title,"
                " b.name AS batch_name, b.status AS batch_status"
                " FROM batch_takeover_tokens t"
                " JOIN ai_chat_sessions s ON s.id = t.session_id AND s.batch_id = t.batch_id"
                " JOIN ai_chat_batches b ON b.id = t.batch_id"
                " WHERE t.token_hash = %s AND t.revoked_at IS NULL"
                " AND t.expires_at > NOW()",
                (_hash(token_plain),))
            row = cur.fetchone()
            if not row:
                return None
            cur.execute("UPDATE batch_takeover_tokens SET last_used_at = NOW() WHERE id = %s",
                        (row['token_id'],))
        conn.commit()
        return dict(row)


def revoke_takeover_token(owner_user_id: str, batch_id: str,
                          session_id: str, token_id: str) -> bool:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE batch_takeover_tokens SET revoked_at = NOW() "
                "WHERE id = %s AND owner_user_id = %s AND batch_id = %s "
                "AND session_id = %s AND revoked_at IS NULL",
                (token_id, owner_user_id, batch_id, session_id))
            hit = cur.rowcount == 1
        conn.commit()
    return hit
```

- [ ] **Step 4: 跑绿**

```bash
cd server && python -m pytest tests/test_takeover_repo.py -q
```
Expected: PASS（清理 Step 1 中标注的冗余 revoke 行）

- [ ] **Step 5: Commit**

```bash
git add server/utils/takeover_repo.py server/tests/test_takeover_repo.py
git commit -m "feat(takeover): 令牌仓储——哈希存证/TTL 钳制/三元组吊销"
```

---

### Task 4: batch_repo CAS 扩展——expect_repark / continue_child 支持 paused

**Files:**
- Modify: `server/utils/batch_repo.py:85`（`transition_child` 签名与 SQL）
- Modify: `server/utils/batch_repo.py:831`（`continue_child`）
- Modify: `server/utils/batch_repo.py:885`（`resume_child` 的 SET 子句）
- Test: `server/tests/test_batch_takeover_repo.py`（新建，模式照抄 `test_batch_execution_safety_p0.py` 的 `db_conn`/`user_id` fixture 纪律）

**Interfaces:**
- Consumes: Task 2 的 `continue_repark` 列。
- Produces（Task 5/7 依赖，签名逐字）:
  - `transition_child(..., expect_repark: bool | None = False)`——新增 keyword-only 参数；WHERE 增加 `AND (%s IS NULL OR continue_repark = %s)`；SET 恒清 `continue_repark = false`（防御性默认清除，对既有路径无害）
  - `continue_child(user_id, batch_id, session_id, prompt, repark: bool = False)`——`repark=True` 时准入 paused（置 `continue_repark=true`、不占计数）；终态路径行为不变
  - `try_repark_transition(session_id: str, *, generation: int, fencing_token: int) -> dict | None`——CAS 收口接管轮到 paused；None=未命中（含「本就不是接管轮」）

- [ ] **Step 1: 写失败测试**（追加到新文件 `server/tests/test_batch_takeover_repo.py`，fixture 复制 `test_batch_execution_safety_p0.py` 的 `user_id` 模式并补 batch/child 播种 helper）

核心用例（每个都是独立 `it`，断言精确）：

```python
def test_transition_child_expect_repark_cas(db_conn, seeded_paused_child):
    """repark CAS：continue_repark=true + running + generation 命中才落到 paused；标志被清。"""
    # 播种 running + continue_repark=true + fencing_token=7, generation=3
    res = batch_repo.try_repark_transition(seeded_paused_child['sid'],
                                           generation=3, fencing_token=7)
    assert res is not None
    status, repark = _fetch_status_flag(db_conn, seeded_paused_child['sid'])
    assert (status, repark) == ('paused', False)
    # 批计数不动（done/failed 不变）
    assert _fetch_counters(db_conn, seeded_paused_child['bid']) == (0, 0)


def test_try_repark_miss_when_flag_absent(db_conn, seeded_running_child):
    res = batch_repo.try_repark_transition(seeded_running_child['sid'],
                                           generation=3, fencing_token=7)
    assert res is None  # 非接管轮 → 调用方回落常规终态路径
    assert _fetch_status_flag(db_conn, seeded_running_child['sid'])[0] == 'running'


def test_continue_child_paused_repark_sets_flag_and_keeps_counters(db_conn, seeded_paused_child):
    batch_repo.continue_child(seeded_paused_child['uid'], seeded_paused_child['bid'],
                              seeded_paused_child['sid'], '人工纠正指令', repark=True)
    row = _fetch_child(db_conn, seeded_paused_child['sid'])
    assert row['status'] == 'pending' and row['continue_repark'] is True
    assert row['continue_prompt'] == '人工纠正指令'
    assert row['pause_requested'] is False
    assert _fetch_counters(db_conn, seeded_paused_child['bid']) == (0, 0)  # paused 不占计数


def test_continue_child_paused_without_repark_rejected(db_conn, seeded_paused_child):
    with pytest.raises(ValueError):
        batch_repo.continue_child(seeded_paused_child['uid'], seeded_paused_child['bid'],
                                  seeded_paused_child['sid'], 'x', repark=False)


def test_continue_child_terminal_rolls_back_counter_and_clears_flag(db_conn, seeded_completed_child):
    # 播种 completed 子任务（done=1）且带残留 continue_repark=true
    batch_repo.continue_child(seeded_completed_child['uid'], seeded_completed_child['bid'],
                              seeded_completed_child['sid'], '追问')
    row = _fetch_child(db_conn, seeded_completed_child['sid'])
    assert row['status'] == 'pending' and row['continue_repark'] is False
    assert _fetch_counters(db_conn, seeded_completed_child['bid'])[0] == 0  # done 回滚


def test_resume_child_clears_repark_flag(db_conn, seeded_paused_child):
    batch_repo.continue_child(seeded_paused_child['uid'], seeded_paused_child['bid'],
                              seeded_paused_child['sid'], 'x', repark=True)
    # 模拟接管轮被协作暂停回 paused（带残留标志），再 resume
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET status='paused', continue_repark=true "
                    "WHERE id=%s", (seeded_paused_child['sid'],))
    db_conn.commit()
    batch_repo.resume_child(seeded_paused_child['uid'], seeded_paused_child['bid'],
                            seeded_paused_child['sid'])
    assert _fetch_status_flag(db_conn, seeded_paused_child['sid']) == ('pending', False)
```

（`seeded_*` fixtures 各自 INSERT users/batches/sessions 行并在 teardown 清理，列集合参考 Task 3 的 `takeover_ids`。文件顶部统一定义只读断言 helper：）

```python
def _fetch_child(db_conn, sid):
    with db_conn.cursor() as cur:
        cur.execute("SELECT status, continue_repark, continue_prompt, pause_requested "
                    "FROM ai_chat_sessions WHERE id=%s", (sid,))
        r = cur.fetchone()
        assert r, 'child row missing'
    return dict(zip(('status', 'continue_repark', 'continue_prompt', 'pause_requested'), r))


def _fetch_status_flag(db_conn, sid):
    r = _fetch_child(db_conn, sid)
    return r['status'], r['continue_repark']


def _fetch_counters(db_conn, bid):
    with db_conn.cursor() as cur:
        cur.execute("SELECT done, failed FROM ai_chat_batches WHERE id=%s", (bid,))
        return cur.fetchone()
```

- [ ] **Step 2: 跑挂**

```bash
cd server && python -m pytest tests/test_batch_takeover_repo.py -q
```
Expected: FAIL（`try_repark_transition` 不存在 / `continue_child()` got unexpected keyword 'repark'）

- [ ] **Step 3: 实现**

`transition_child`：签名加 `expect_repark: bool | None = False`；SQL 的 SET 首段加 `continue_repark = false,`；WHERE 的 pause 期望行后加：

```python
                   AND (%s IS NULL OR continue_repark = %s)
```
参数元组在 `expect_pause, bool(expect_pause)` 之后插入 `expect_repark, bool(expect_repark)`（fencing 之前），并同步更新 docstring 三态说明。

`continue_child`：签名加 `repark: bool = False`；状态校验改为三分支：

```python
            prev_status = row[0]
            if prev_status == 'paused':
                if not repark:
                    raise ValueError('paused 子任务须经接管通道（repark）续跑')
                cur.execute(
                    "UPDATE ai_chat_sessions SET status='pending', "
                    "  continue_prompt=%s, error_message=NULL, cancel_requested=false, "
                    "  pause_requested=false, continue_repark=true, "
                    "  execution_generation = execution_generation + 1 "
                    "WHERE id = %s AND status = 'paused'",
                    (prompt, session_id))
                if cur.rowcount != 1:
                    conn.rollback()
                    raise ValueError('子任务状态已变化，请刷新后重试')
                # paused 不占 done/failed 计数：无需回滚
            else:
                if prev_status not in ('completed', 'failed', 'cancelled', 'needs_review'):
                    raise ValueError('only completed/failed/cancelled/needs_review children can be continued')
                cur.execute(  # 原终态 SQL 不变，SET 追加 continue_repark=false,
                    ...)
                # 原 done/failed 计数回滚不变
```

`resume_child`：SET 子句追加 `continue_repark = false,`（防接管轮被协作暂停后 resume 带残留标志重停）。

新增函数（放在 `resume_child` 之后）：

```python
def try_repark_transition(session_id: str, *, generation: int,
                          fencing_token: int) -> dict | None:
    """接管续跑收口（spec D3）：continue_repark=true 的 running 子会话回
    paused，不动 done/failed 计数，把控制权交还外部系统。CAS 未命中返回
    None——含「本就不是接管轮」，调用方（batch_engine）据此回落常规终态
    路径，无需预读标志（check-then-act 免除）。"""
    return transition_child(session_id, 'paused', generation=generation,
                            expect_pause=None, expect_repark=True,
                            turn_status='paused', fencing_token=fencing_token)
```

- [ ] **Step 4: 跑绿 + P0 回归**

```bash
cd server && python -m pytest tests/test_batch_takeover_repo.py tests/test_batch_execution_safety_p0.py -q
```
Expected: 全 PASS（P0 套件证明既有 CAS 语义零回归）

- [ ] **Step 5: Commit**

```bash
git add server/utils/batch_repo.py server/tests/test_batch_takeover_repo.py
git commit -m "feat(takeover): continue_child 准入 paused + repark CAS 收口（transition_child expect_repark）"
```

---

### Task 5: batch_engine——完成路径前置 repark 尝试

**Files:**
- Modify: `server/utils/batch_engine.py:3110`（`_mark_done` 开头）
- Modify: `server/utils/batch_engine.py:3139`（`_mark_failed` 开头，注意在 `execution_audit.finish_latest_running('failed')` **之前**）
- Test: `server/tests/test_batch_takeover_engine.py`（新建）

**Interfaces:**
- Consumes: Task 4 的 `batch_repo.try_repark_transition(session_id, *, generation, fencing_token)`。
- Produces: `_mark_done`/`_mark_failed` 对接管轮（`continue_repark=true`）不再落终态/计数，改为回 paused；其余行为逐字节不变。

- [ ] **Step 1: 写失败测试**（mock 模式：patch `utils.batch_repo.try_repark_transition`，参照 server/tests 既有 mock_conn 风格直接构造 `BatchWorker` 实例调用方法）

```python
def test_mark_done_reparks_takeover_turn(monkeypatch):
    hit = {'called': 0}

    def fake_repark(sid, *, generation, fencing_token):
        hit['called'] += 1
        assert (generation, fencing_token) == (3, 9)
        return {'id': sid}

    monkeypatch.setattr('utils.batch_repo.try_repark_transition', fake_repark)
    audit = []
    monkeypatch.setattr('utils.execution_audit.finish_latest_running',
                        lambda sid, status, **kw: audit.append(status))
    worker = BatchWorker.__new__(BatchWorker)  # 不跑 __init__，仅测方法
    worker._resolve_generation = lambda sid, gen: gen  # 桩：直接采用传入 generation
    worker._mark_done('sess-1', 'b-1', 'preview', generation=3, fencing_token=9)
    assert audit == ['stopped']            # repark 命中：审计按 stopped 收口
    assert hit['called'] == 1


def test_mark_done_falls_through_when_not_takeover(monkeypatch):
    monkeypatch.setattr('utils.batch_repo.try_repark_transition',
                        lambda sid, *, generation, fencing_token: None)
    done = {}
    monkeypatch.setattr('utils.batch_repo.transition_child',
                        lambda sid, status, **kw: done.setdefault('status', status))
    monkeypatch.setattr('utils.execution_audit.finish_latest_running', lambda *a, **kw: None)
    monkeypatch.setattr('utils.skill_fit.compute_for_session', lambda sid: None)
    worker = BatchWorker.__new__(BatchWorker)
    worker._resolve_generation = lambda sid, gen: gen
    worker._mark_done('sess-1', 'b-1', 'preview', generation=3, fencing_token=9)
    assert done['status'] == 'completed'   # 未命中 → 常规 completed 路径
```

（`_mark_failed` 对称两例：repark 命中时审计 `stopped` 而非 `failed`；未命中回落 `failed` 且 `execution_audit.finish_latest_running('failed')` 先于 repark 尝试的顺序不被破坏——以实现定稿顺序核对断言。）

- [ ] **Step 2: 跑挂**

```bash
cd server && python -m pytest tests/test_batch_takeover_engine.py -q
```
Expected: FAIL（`try_repark_transition` 从未被调用 / audit 断言不等）

- [ ] **Step 3: 实现**

`_mark_done` 在 `_require_fencing(fencing_token)` 之后、常规 `transition_child` 之前插入：

```python
        from utils import batch_repo
        reparked = batch_repo.try_repark_transition(
            session_id, generation=self._resolve_generation(session_id, generation),
            fencing_token=fencing_token)
        if reparked is not None:
            # 接管轮收口（spec D3）：回 paused 交还外部系统，不进终态不计数。
            execution_audit.finish_latest_running(session_id, 'stopped',
                                                  error_code='TAKEOVER_REPARK')
            skill_fit.compute_for_session(session_id)
            return
```

（`execution_audit`/`skill_fit` 用方法体内既有的局部导入风格；`_mark_failed` 同样处理，但插入点在它现有的 `execution_audit.finish_latest_running(..., 'failed', ...)` **之前**，repark 命中分支审计同样按 `'stopped'` 收口——failed 审计不得先跑，否则接管轮被误记为 failed。）

- [ ] **Step 4: 跑绿 + P0 回归**

```bash
cd server && python -m pytest tests/test_batch_takeover_engine.py tests/test_batch_execution_safety_p0.py -q
```

- [ ] **Step 5: Commit**

```bash
git add server/utils/batch_engine.py server/tests/test_batch_takeover_engine.py
git commit -m "feat(takeover): worker 完成路径前置 repark——接管轮回 paused 不计数"
```

---

### Task 6: 开放 API——签发/吊销接管链接

**Files:**
- Modify: `server/routes/open_api_batches.py`（`session_messages` 端点之后，open_api_batches.py:723 附近）
- Test: `server/tests/test_open_api_takeover_link.py`（模式照抄 `test_open_api_batches_crud.py`：`_fake_auth_db` + `patch('routes.open_api_batches._current_key')`）

**Interfaces:**
- Consumes: Task 3 的 `create_takeover_token/revoke_takeover_token`；既有 `_current_key()/_resolve_child()/err()/NOT_FOUND`。
- Produces（Task 9 E2E 依赖）:
  - `POST /api/v1/ai-batches/<batch_id>/sessions/<child_id>/takeover-link` body `{ttlHours?: int}` → 201 `{token: 'tk_…', path: '/takeover/session?t=tk_…', expiresAt: ISO8601}`；404 批/子任务不存在或不属该密钥
  - `POST /api/v1/ai-batches/<batch_id>/sessions/<child_id>/takeover-link/revoke` body `{tokenId: string}` → 200 `{revoked: true}` / 404 `{revoked: false}`（令牌 id 不属于三元组）

- [ ] **Step 1: 写失败测试**（fixture 从 `test_open_api_batches_crud.py` 复制 `_fake_auth_db/_auth_passes/_key/HDR`；`takeover_repo` 整体 monkeypatch，不碰真库）

核心断言：

```python
def test_issue_link_requires_owned_batch(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail', return_value=None):
        resp = client.post(f'{BASE}/b1/sessions/s1/takeover-link', headers=HDR, json={})
    assert resp.status_code == 404


def test_issue_link_returns_token_path_expires(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    fake_exp = datetime.now(timezone.utc) + timedelta(hours=24)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail', return_value={'id': 'b1'}), \
         patch('routes.open_api_batches._resolve_child', return_value=({'id': 's1', 'batch_seq': 1}, None)), \
         patch('utils.takeover_repo.create_takeover_token',
               return_value=('tk_abc', fake_exp)) as mk:
        resp = client.post(f'{BASE}/b1/sessions/s1/takeover-link', headers=HDR,
                           json={'ttlHours': 48})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body['token'] == 'tk_abc' and body['path'] == '/takeover/session?t=tk_abc'
    assert body['expiresAt'] == fake_exp.isoformat()
    assert mk.call_args.kwargs['ttl_hours'] == 48


def test_revoke_scoped_to_triple(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail', return_value={'id': 'b1'}), \
         patch('routes.open_api_batches._resolve_child', return_value=({'id': 's1'}, None)), \
         patch('utils.takeover_repo.revoke_takeover_token', return_value=False) as rk:
        resp = client.post(f'{BASE}/b1/sessions/s1/takeover-link/revoke',
                           headers=HDR, json={'tokenId': 'tkt_x'})
    assert resp.status_code == 404
    assert rk.call_args.args == ('user-42', 'b1', 's1', 'tkt_x')
```

（补一例 `revoke_takeover_token→True` 断言 200；补一例 `ttlHours: 9999` 仍 201——钳制在 repo 层已测，路由层不重复拒绝。）

- [ ] **Step 2: 跑挂** → Expected: FAIL（404 路由不存在）

- [ ] **Step 3: 实现**

```python
@open_api_batches_bp.post('/<batch_id>/sessions/<child_id>/takeover-link')
@api_key_required
@require_bound_key
def create_takeover_link(batch_id, child_id):
    """签发人工接管链接（spec D1）：短时令牌，仅存哈希，可吊销。"""
    from utils import takeover_repo
    key = _current_key()
    d = get_batch_detail(key['ownerUserId'], batch_id, api_key_id=key['id'])
    if not d:
        return err('批任务不存在', NOT_FOUND, 404)
    child, resolve_err = _resolve_child(d, child_id)
    if resolve_err:
        return resolve_err
    body = request.get_json(silent=True) or {}
    try:
        ttl_hours = int(body.get('ttlHours') or 24)
    except (TypeError, ValueError):
        return err('ttlHours 不合法', 'INVALID_TTL', 400)
    token, expires_at = takeover_repo.create_takeover_token(
        key['ownerUserId'], batch_id, child['id'], key['id'], ttl_hours=ttl_hours)
    log_operation('ai.batch_takeover_link_created', 'ai_batch', batch_id,
                  d.get('name') or batch_id, f'签发子任务接管链接 seq={child.get("batch_seq")}')
    return jsonify({'token': token,
                    'path': f'/takeover/session?t={token}',
                    'expiresAt': expires_at.isoformat()}), 201


@open_api_batches_bp.post('/<batch_id>/sessions/<child_id>/takeover-link/revoke')
@api_key_required
@require_bound_key
def revoke_takeover_link(batch_id, child_id):
    """吊销接管令牌：三元组全匹配才生效（他人/他批令牌一律 404）。"""
    from utils import takeover_repo
    key = _current_key()
    d = get_batch_detail(key['ownerUserId'], batch_id, api_key_id=key['id'])
    if not d:
        return err('批任务不存在', NOT_FOUND, 404)
    child, resolve_err = _resolve_child(d, child_id)
    if resolve_err:
        return resolve_err
    body = request.get_json(silent=True) or {}
    token_id = (body.get('tokenId') or '').strip()
    if not token_id:
        return err('tokenId required', 'INVALID_TOKEN_ID', 400)
    ok = takeover_repo.revoke_takeover_token(
        key['ownerUserId'], batch_id, child['id'], token_id)
    if not ok:
        return err('令牌不存在', NOT_FOUND, 404)
    log_operation('ai.batch_takeover_link_revoked', 'ai_batch', batch_id,
                  d.get('name') or batch_id, f'吊销接管令牌 {token_id}')
    return jsonify({'revoked': True})
```

（`log_operation` 从 `utils.operation_log` 导入，参数序 `(action, target_type, target_id, target_name, description)`——文件顶部补 import。）

- [ ] **Step 4: 跑绿** → `cd server && python -m pytest tests/test_open_api_takeover_link.py -q`
- [ ] **Step 5: Commit**

```bash
git add server/routes/open_api_batches.py server/tests/test_open_api_takeover_link.py
git commit -m "feat(takeover): 开放 API 签发/吊销接管链接（令牌三元组隔离 + oplog）"
```

---

### Task 7: 对外公开蓝图 external_takeover

**Files:**
- Create: `server/routes/external_takeover.py`
- Modify: `server/app.py`（import + `app.register_blueprint(external_takeover_bp)`，紧随 open_api 系列注册，app.py:103 附近）
- Test: `server/tests/test_external_takeover_routes.py`

**Interfaces:**
- Consumes: Task 3 `resolve_takeover_token`；Task 4 `continue_child(..., repark=)`；既有 `batch_repo.get_child_messages`、`batch_engine.get_worker().notify()`、`utils.rate_limit.RateLimiter`、`utils.operation_log.log_operation`。
- Produces（Task 8/9 依赖）:
  - `GET /api/external/takeover/session?t=` → 200 `{batchName, batchSeq, inputFile, title, childStatus, batchStatus, expiresAt}`
  - `GET /api/external/takeover/session/messages?t=` → 200 `{childStatus, messages: [{id,role,content,createdAt}], total, truncated}`（行字段对齐 `get_child_messages`，createdAt ISO 化）
  - `POST /api/external/takeover/session/messages?t=` body `{prompt}` → 202 `{queued: true}`；pending/running → 409 `{error: {'code': 'TASK_RUNNING', message: '任务执行中，请外部系统先暂停后再接管'}}`；prompt 空/>20000 → 400
  - 全部无效令牌 → 404 `{'error': '链接无效或已过期'}` + `Cache-Control: no-store`

- [ ] **Step 1: 写失败测试**（Flask test client + `patch('routes.external_takeover.takeover_repo.resolve_takeover_token')`；限速用小阈值或 monkeypatch `_limiter`）

核心用例：无效令牌 404 且响应头含 no-store；session 档案字段形状；messages 只透传四字段；发送在 childStatus='running' 时 409、'paused' 时以 `repark=True` 调 `continue_child` 并 `notify()`、终态时 `repark=False`；限速第二击 429。

- [ ] **Step 2: 跑挂** → Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**

```python
"""批任务会话人工接管公开入口（无 JWT，接管令牌 + 双层限速）。
公开攻击面收敛于此蓝图（与 kefu_public 同纪律）：全系统未挂 JWT 的路由仅
/auth/login、/kefu/*、/external/takeover/* 三处，评审时按此清单核对。"""
import logging

from flask import Blueprint, jsonify, request, g

from db import get_db
from utils import batch_repo, takeover_repo
from utils.rate_limit import RateLimiter
from utils.operation_log import log_operation

logger = logging.getLogger(__name__)

MAX_PROMPT_CHARS = 20000
SEND_PER_MINUTE, SEND_PER_DAY = 10, 200        # 令牌桶
IP_PER_MINUTE, IP_PER_DAY = 60, 1000           # IP 桶（固定地板，对齐 kefu）

external_takeover_bp = Blueprint('external_takeover', __name__,
                                 url_prefix='/external/takeover')
_limiter = RateLimiter()

_INVALID = ({'error': '链接无效或已过期'}, 404)


def _client_ip() -> str:
    xff = request.headers.get('X-Forwarded-For', '')
    return xff.split(',')[0].strip() if xff else (request.remote_addr or '')


@external_takeover_bp.after_request
def _no_store(resp):
    resp.headers.setdefault('Cache-Control', 'no-store')
    return resp


def _guard():
    """令牌校验 + 限速。失败时直接返回 (body, status)，成功返回 None 并写 g.takeover。"""
    row = takeover_repo.resolve_takeover_token(request.args.get('t'))
    if not row:
        return _INVALID
    key = f"tk:{row['token_id']}:{_client_ip()}"
    if not _limiter.allow(key, SEND_PER_MINUTE, SEND_PER_DAY):
        return {'error': '请求过于频繁，请稍后再试'}, 429
    if not _limiter.allow(f"tkip:{_client_ip()}", IP_PER_MINUTE, IP_PER_DAY):
        return {'error': '请求过于频繁，请稍后再试'}, 429
    g.takeover = row
    return None


def _child_status_fresh() -> str:
    """resolve 时的状态可能陈旧，发送前重读。"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM ai_chat_sessions WHERE id=%s AND batch_id=%s",
                        (g.takeover['session_id'], g.takeover['batch_id']))
            row = cur.fetchone()
    return row[0] if row else ''


@external_takeover_bp.get('/session')
def takeover_session_info():
    denied = _guard()
    if denied:
        return denied
    r = g.takeover
    return jsonify({'batchName': r['batch_name'], 'batchSeq': r['batch_seq'],
                    'inputFile': r['batch_input_file'], 'title': r['title'],
                    'childStatus': r['child_status'], 'batchStatus': r['batch_status'],
                    'expiresAt': r['expires_at'].isoformat()})


@external_takeover_bp.get('/session/messages')
def takeover_messages():
    denied = _guard()
    if denied:
        return denied
    r = g.takeover
    msgs = batch_repo.get_child_messages(r['session_id'])
    return jsonify({
        'childStatus': _child_status_fresh(),
        'messages': [
            {'id': m['id'], 'role': m['role'], 'content': m['content'],
             'createdAt': m['created_at'].isoformat() if m['created_at'] else None}
            for m in msgs['messages']],
        'total': msgs['total'], 'truncated': msgs['truncated'],
    })


@external_takeover_bp.post('/session/messages')
def takeover_send():
    denied = _guard()
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    prompt = (body.get('prompt') or '').strip()
    if not prompt:
        return {'error': 'prompt required'}, 400
    if len(prompt) > MAX_PROMPT_CHARS:
        return {'error': f'prompt 超过 {MAX_PROMPT_CHARS} 字符上限'}, 400
    status = _child_status_fresh()
    if status in ('pending', 'running'):
        return {'error': {'code': 'TASK_RUNNING',
                          'message': '任务执行中，请外部系统先暂停后再接管'}}, 409
    if status not in ('completed', 'failed', 'cancelled', 'needs_review', 'paused'):
        return _INVALID
    from utils.batch_engine import get_worker
    try:
        batch_repo.continue_child(g.takeover['owner_user_id'], g.takeover['batch_id'],
                                  g.takeover['session_id'], prompt,
                                  repark=(status == 'paused'))
    except ValueError as e:
        return {'error': {'code': 'STATE_CONFLICT', 'message': str(e)}}, 409
    get_worker().notify()
    log_operation('ai.batch_takeover_message', 'ai_chat_session',
                  g.takeover['session_id'], g.takeover['title'] or '接管会话',
                  f'接管对话 {len(prompt)} 字符（prev_status={status}）')
    return jsonify({'queued': True}), 202
```

app.py：`from routes.external_takeover import external_takeover_bp` + `app.register_blueprint(external_takeover_bp)`（对齐相邻注册行样式）。

- [ ] **Step 4: 跑绿** → `cd server && python -m pytest tests/test_external_takeover_routes.py -q`
- [ ] **Step 5: 公开路由不变式核对**：`rg -n "@.*_bp\.(get|post|route)" server/routes/external_takeover.py` 确认全部端点无 JWT 装饰器且仅在本蓝图；`rg -rn "login_required" server/routes/external_takeover.py` 应无命中。
- [ ] **Step 6: Commit**

```bash
git add server/routes/external_takeover.py server/app.py server/tests/test_external_takeover_routes.py
git commit -m "feat(takeover): 对外公开蓝图 /external/takeover（令牌+限速+continue 通道）"
```

---

### Task 8: 前端——公开路由 + API 客户端 + 接管页

**Files:**
- Modify: `src/router/index.ts:41-48`（public 路由区新增一条）
- Create: `src/api/takeover.ts`
- Create: `src/views/external/TakeoverSessionPage.vue`
- Test: `src/views/external/__tests__/TakeoverSessionPage.test.ts`

**Interfaces:**
- Consumes: Task 7 三个端点；`@/utils/request` 的 `get/post`（kefuPublic.ts 同款用法）；`@/components/ai-chat/MarkdownView.vue`（props `{text: string}`）。
- Produces: 路由 `/takeover/session?t=…`（`meta.public`）。

- [ ] **Step 1: 写失败测试**（mock `@/api/takeover` 整模块 + stub MarkdownView，聚焦页面逻辑）

核心用例：
1. `t` 缺失 → 错误卡「链接无效或已过期」，不调 API。
2. `getTakeoverSession`/`getTakeoverMessages` 正常返回 → 顶栏批名+seq、历史消息渲染（user 气泡原文本、assistant text part 走 MarkdownView stub、tool part 折叠标记）。
3. childStatus='running' → 输入禁用 + 提示「任务执行中」；'paused'/'completed' → 可发送；send 成功 → 清空输入并立即再拉一次 messages。
4. 发送 409 → 页面显示「任务执行中，请外部系统先暂停后再接管」；404 → 错误卡。
5. 轮询：`vi.useFakeTimers()` 推进 3s → `getTakeoverMessages` 再次被调；`document.hidden=true` 时不调。

- [ ] **Step 2: 跑挂** → `npx vitest run src/views/external` Expected: FAIL

- [ ] **Step 3: 实现**

路由（`/kefu/:slug` 条目之后）：

```ts
{
  path: '/takeover/session',
  name: 'ExternalTakeover',
  component: () => import('@/views/external/TakeoverSessionPage.vue'),
  meta: { title: '任务会话接管', public: true },
},
```

`src/api/takeover.ts`：

```ts
import { get, post } from '@/utils/request'

export interface TakeoverSessionInfo {
  batchName: string
  batchSeq: number | null
  inputFile: string | null
  title: string | null
  childStatus: string
  batchStatus: string
  expiresAt: string
}
export interface TakeoverMessage {
  id: string
  role: string
  content: Array<{ type: string; text?: string }> | any
  createdAt: string | null
}

const qs = (t: string) => `?t=${encodeURIComponent(t)}`

export function getTakeoverSession(t: string) {
  return get<TakeoverSessionInfo>(`/external/takeover/session${qs(t)}`)
}
export function getTakeoverMessages(t: string) {
  return get<{ childStatus: string; messages: TakeoverMessage[]; total: number; truncated: boolean }>(
    `/external/takeover/session/messages${qs(t)}`)
}
export function sendTakeoverMessage(t: string, prompt: string) {
  return post<{ queued: boolean }>(`/external/takeover/session/messages${qs(t)}`, { prompt })
}
```

`TakeoverSessionPage.vue` 要点（完整组件由执行者按下列契约写全）：
- `const token = String(route.query.t || '')`；空 → 错误卡。
- 状态徽标映射：pending 排队 / running 执行中 / paused 已暂停（可接管） / completed 已完成 / failed 失败 / cancelled 已取消 / needs_review 待复核。
- 消息渲染：`role==='user'` 右侧气泡纯文本（content 为 parts 时取 text 拼接）；`role==='assistant'` 左侧，content parts 中 `type==='text'` 逐段 `MarkdownView`，`reasoning`/工具类 part 渲染一行灰色标记「（内部过程，略）」；其余 role 跳过。
- 输入区：textarea + 发送按钮；`busy = ['pending','running'].includes(status)` 时禁用；Enter 发送。
- 轮询：`setInterval(poll, 3000)`，`document.hidden` 时跳过（visibilitychange 监听）；`onUnmounted` 清理；发送成功后立即 `poll()` 一次。
- 404/网络错 → 全页错误卡；409 → 输入区上方内联警告条。
- 页面样式独立完成（无 AppLayout 壳）：白底居中卡片，顶栏含批名/seq/输入文件/徽标/过期时间；消息区独立滚动；样式不用深色模式适配（对外单一样式）。

- [ ] **Step 4: 跑绿** → `npx vitest run src/views/external src/router` 全 PASS
- [ ] **Step 5: Commit**

```bash
git add src/router/index.ts src/api/takeover.ts src/views/external
git commit -m "feat(takeover): 公开接管页（无壳路由 + 轮询 + MarkdownView 复用）"
```

---

### Task 9: E2E 真实链路

**Files:**
- Create: `e2e/external-takeover.spec.ts`

**Interfaces:**
- Consumes: `e2e/ai-full/helpers.ts`（`api/gotoWithAuth/screenshot`）、`e2e/ai-full/db-helpers.ts`（`dbSeed` 经 `batch/toolbox`）、Task 6/7 端点。
- 关键确定性手段：种子批 `agent='e2e-no-such-agent'`（认领即 fail-fast，不烧 LLM，同 `failFastBatch` 纪律）；接管发送后 worker 真实认领 → 秒级 failed，整链路无 LLM。

- [ ] **Step 1: 写 spec**

主用例流程（单 test，`setTimeout(180_000)`）：
1. admin JWT（`helpers.api` 或 `adminToken`）`POST /api/apiKeys` 建密钥 → 明文 key + keyId。
2. `dbSeed` 播种：batch（id `AITEST-TK-…`、user_id `user-admin`、api_key_id=新 keyId、agent `'e2e-no-such-agent'`、status `'running'`、total 1）+ 子会话（batch_id、batch_seq 1、status `'paused'`、title「接管E2E子任务」）+ 两条 `ai_chat_messages`（user/assistant，文本经 `JSON.stringify` 转义——`seedPlainSession` 已修，但此处直接 dbSeed 需自带转义）。
3. `POST /api/v1/ai-batches/<bid>/sessions/<sid>/takeover-link`（`X-API-Key`）→ 201，拿 `path`。
4. `page.goto(path)` → 断言：顶栏批名/「已暂停（可接管）」徽标、种子历史两条可见（user 文本 + assistant 文本）。
5. 输入「人工补充指令 e2e」发送 → 输入清空、消息出现在气泡。
6. 轮询断言（`expect.poll`，60s 上限）：页面徽标变为 `failed`（fail-fast 落地），`GET /api/v1/ai-batches/<bid>` 的子任务状态 failed——worker 消费链路真实走通。
7. 截图 `screenshot(page, 'external-takeover-live')`。
8. 负例同文件另一 test：篡改 `?t=tk_bad` → 错误卡；正常签发后 `revoke` → 刷新页面 → 错误卡。
9. `finally`：`dbSeed` 按前缀清理 batch（CASCADE 会话）/messages/tokens，DELETE api key。

- [ ] **Step 2: 跑绿**

```bash
npx playwright test e2e/external-takeover.spec.ts
```
Expected: 2 passed（栈在跑的前提下；截图落 `e2e/screenshots/ai-full/`）

- [ ] **Step 3: Commit**

```bash
git add e2e/external-takeover.spec.ts
git commit -m "test(takeover): E2E 真实链路——签发/公开页/接管发送/fail-fast 消费/吊销"
```

---

### Task 10: 全量回归 + 上库

- [ ] **Step 1: 后端回归**

```bash
cd server && python -m pytest tests/test_batch_takeover_repo.py tests/test_batch_takeover_engine.py tests/test_takeover_repo.py tests/test_open_api_takeover_link.py tests/test_external_takeover_routes.py tests/test_batch_execution_safety_p0.py tests/test_open_api_batches_crud.py -q
```
Expected: 全 PASS

- [ ] **Step 2: 前端回归**

```bash
npx vitest run        # 全量绿
npx vue-tsc --noEmit  # 无错误
```

- [ ] **Step 3: E2E 相邻回归**：`npx playwright test e2e/ai-session-admin-v2.spec.ts e2e/ai-chat-plantuml.spec.ts`（公开路由/种子 helper 消费方）
- [ ] **Step 4: Mimosa 深度扫描** + 与基线 diff（新增公开端点必须过检）；`rg -n "login_required|require_permission" server/routes/external_takeover.py` 确认零命中后，在报告中重申三蓝图公开面清单
- [ ] **Step 5: push + 记忆更新**（design 记忆改为已实施状态，补 continue_repark/claim 不查批状态/fail-fast E2E 手法三个非显然事实）

```bash
git push origin main
```
