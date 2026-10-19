# 批任务管理页（管理员）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给管理员一个独立页面，跨用户查看界面与 API 触发的全部批任务、只读查看子任务对话、并手动重试失败的子任务。

**Architecture:** 后端新增一个独立蓝图 `ai_batch_admin_bp`（`/ai/chat/admin/batches`），5 个端点全部由 `admin.ai_chat_admin` 把关；数据层在 `batch_repo` 新增**管理员作用域的读函数**（不改动既有按归属过滤的函数），写路径则先查出批任务归属用户、再复用既有写函数。前端在设置中心新增 `/admin/ai-batches`，页面为「筛选栏 + 表格 + 详情抽屉 + 只读对话弹窗」。

**Tech Stack:** Flask + psycopg2（后端）、pytest；Vue 3 + TypeScript + Element Plus + Pinia（前端）、Vitest；Playwright（端到端）。

## Global Constraints

- 权限键复用 **`admin.ai_chat_admin`**，不新建能力键。5 个端点全部挂 `@require_permission('admin.ai_chat_admin')`。
- 操作范围限定为**查看 + 重试**。不实现删除、不实现改配置。
- 管理员作用域的**读**函数必须新增，**不得**把既有 `list_batches` / `get_batch_detail` 的 `user_id` 参数改为可空。
- 写路径**不新增无作用域的变体**：先查批任务归属用户，再用该 `user_id` 调用既有的 `reset_failed_to_pending` / `reexecute_child`。
- 新蓝图必须在 `app.py` 中于 `dynamic_bp`（catch-all）**之前**注册。
- 消息查询按 **`seq`** 排序，不得使用 `created_at`（同事务写入的消息 `created_at` 相同，顺序不确定）。
- 消息响应有界：默认最多 **500** 条，响应带 `truncated` 与 `total`。
- 对话查看器**只读**，不得有输入框。
- 管理员的两个重试动作写入 `operation_logs`，`target_type='ai_chat_batch'`。
- 后端测试命令（Windows 必须带该环境变量）：`cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/xxx.py -v`
- 提交信息用中文，带 conventional commit 前缀。

**既有签名（不要改动，供调用）：**

```python
# server/utils/batch_repo.py
list_batches(user_id: str, *, page: int, page_size: int, api_key_id: str | None = None) -> dict
get_batch_detail(user_id: str, batch_id: str, *, api_key_id: str | None = None) -> dict | None
reset_failed_to_pending(user_id: str, batch_id: str, *, api_key_id: str | None = None) -> int
reexecute_child(user_id: str, batch_id: str, session_id: str) -> dict | None   # 非终态子任务抛 ValueError
# server/utils/operation_log.py
log_operation(action, target_type, target_id, target_name, description, field_changes=None, branch_id=None)
#   action 取值限 'create' | 'update' | 'delete'
# server/auth.py
require_permission(capability_key: str)     # 装饰器
```

---

## 文件结构

**新建**

| 文件 | 职责 |
|---|---|
| `server/routes/ai_batch_admin.py` | 蓝图 + 5 个管理员端点 |
| `server/tests/test_ai_batch_admin_repo.py` | 数据层测试（Task 1） |
| `server/tests/test_ai_batch_admin_routes.py` | 端点测试（Task 2、3） |
| `src/api/aiBatchAdmin.ts` | API 层 |
| `src/stores/aiBatchAdmin.ts` | 管理态 store（与用户态 `aiChatBatches.ts` 分开） |
| `src/stores/__tests__/aiBatchAdmin.test.ts` | store 测试 |
| `src/views/admin/AiBatchAdmin.vue` | 页面：筛选栏 + 表格 + 详情抽屉 |
| `src/components/ai-chat/BatchConversationView.vue` | 只读对话查看器 |

**修改**

| 文件 | 改动 |
|---|---|
| `server/utils/batch_repo.py` | 追加 4 个管理员作用域函数 |
| `server/app.py` | 导入并注册新蓝图（`dynamic_bp` 之前） |
| `src/views/admin/hub/settingsCatalog.ts` | 「AI 能力」组新增一条 |
| `docs/user-guide/ai/batch-tasks.md` | 新增「管理员：批任务管理页」一节 |
| `CLAUDE.md` | 记录新页面与管理端点 |

---

## Task 1: 数据层 —— 管理员作用域的读函数

**Files:**
- Modify: `server/utils/batch_repo.py`（在文件末尾追加）
- Test: `server/tests/test_ai_batch_admin_repo.py`（新建）

**Interfaces:**
- Consumes: 该文件已有的 `get_db`、`RealDictCursor` 导入。
- Produces:
  ```python
  MAX_ADMIN_MESSAGES = 500

  admin_list_batches(*, page: int, page_size: int,
                     status: str | None = None,
                     owner_keyword: str | None = None,
                     source: str | None = None,
                     name_keyword: str | None = None) -> dict
      # -> {'items': [batch dict + owner_username + source], 'total': int,
      #     'page': int, 'pageSize': int}

  admin_get_batch_detail(batch_id: str) -> dict | None
      # -> {'batch': {... , 'owner_username', 'source'}, 'sessions': [...]}

  admin_get_batch_owner(batch_id: str) -> str | None
      # -> 该批任务的 user_id；不存在返回 None

  admin_get_child_messages(batch_id: str, session_id: str,
                           limit: int = MAX_ADMIN_MESSAGES) -> dict | None
      # -> {'messages': [...], 'truncated': bool, 'total': int}
      # session 不属于该 batch 时返回 None
  ```

- [ ] **Step 1: 写失败测试**

新建 `server/tests/test_ai_batch_admin_repo.py`：

```python
"""管理员作用域读函数的测试（真实 DB）。

这些函数与既有的 list_batches / get_batch_detail 的语义**相反**：它们刻意不按
归属用户过滤。因此测试的重点不是"能查到"，而是"跨用户能查到"与"筛选真的收窄了"。
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from db import get_db                    # noqa: E402
from utils import batch_repo             # noqa: E402


@pytest.fixture
def two_users_two_batches():
    """两个用户各一个批任务：一个来自界面（api_key_id 为 NULL），一个来自 API。

    收尾把造的数据全删掉 —— 这些测试跑在共享开发库上，留下的行会污染后续断言。
    """
    uid_a = 'u-adm-' + uuid.uuid4().hex[:8]
    uid_b = 'u-adm-' + uuid.uuid4().hex[:8]
    key_id = 'ak-adm-' + uuid.uuid4().hex[:8]
    bid_ui = 'b-adm-' + uuid.uuid4().hex[:8]
    bid_api = 'b-adm-' + uuid.uuid4().hex[:8]
    sid = 's-adm-' + uuid.uuid4().hex[:8]
    with get_db() as conn:
        with conn.cursor() as cur:
            for uid, uname in ((uid_a, 'alice-' + uid_a), (uid_b, 'bob-' + uid_b)):
                cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                            "VALUES (%s,%s,'x',%s,'developer')", (uid, uname, uname))
            cur.execute("INSERT INTO api_keys (id, name, key_hash, owner_user_id) "
                        "VALUES (%s,'adm-test',%s,%s)", (key_id, 'h-' + key_id, uid_b))
            cur.execute("INSERT INTO ai_chat_batches (id,user_id,name,prompt,status,total) "
                        "VALUES (%s,%s,'界面批任务','p','completed',1)", (bid_ui, uid_a))
            cur.execute("INSERT INTO ai_chat_batches (id,user_id,name,prompt,status,total,api_key_id) "
                        "VALUES (%s,%s,'接口批任务','p','failed',1,%s)", (bid_api, uid_b, key_id))
            cur.execute("INSERT INTO ai_chat_sessions (id,user_id,status,batch_id,batch_seq,batch_input_file) "
                        "VALUES (%s,%s,'failed',%s,0,'uploads/a.txt')", (sid, uid_b, bid_api))
        conn.commit()
    yield {'uid_a': uid_a, 'uid_b': uid_b, 'bid_ui': bid_ui,
           'bid_api': bid_api, 'sid': sid,
           'uname_a': 'alice-' + uid_a}
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_batches WHERE id IN (%s,%s)", (bid_ui, bid_api))
            cur.execute("DELETE FROM api_keys WHERE id=%s", (key_id,))
            cur.execute("DELETE FROM users WHERE id IN (%s,%s)", (uid_a, uid_b))
        conn.commit()


def _ids(res):
    return {b['id'] for b in res['items']}


def test_list_spans_all_users(two_users_two_batches):
    """核心断言：两个不同用户的批任务同时出现在结果里。"""
    f = two_users_two_batches
    res = batch_repo.admin_list_batches(page=1, page_size=100)
    got = _ids(res)
    assert f['bid_ui'] in got and f['bid_api'] in got


def test_list_carries_owner_username_and_source(two_users_two_batches):
    """跨用户列表没有归属列无法使用；来源列直接对应"界面触发还是 API 触发"。"""
    f = two_users_two_batches
    res = batch_repo.admin_list_batches(page=1, page_size=100)
    by_id = {b['id']: b for b in res['items']}
    assert by_id[f['bid_ui']]['owner_username'] == f['uname_a']
    assert by_id[f['bid_ui']]['source'] == 'ui'
    assert by_id[f['bid_api']]['source'] == 'api'


def test_filter_by_source_excludes_the_other(two_users_two_batches):
    """筛选必须真的收窄 —— 只断言"包含"会让一个永远返回全集的实现也通过。"""
    f = two_users_two_batches
    only_api = _ids(batch_repo.admin_list_batches(page=1, page_size=100, source='api'))
    assert f['bid_api'] in only_api
    assert f['bid_ui'] not in only_api


def test_filter_by_status_excludes_the_other(two_users_two_batches):
    f = two_users_two_batches
    only_failed = _ids(batch_repo.admin_list_batches(page=1, page_size=100, status='failed'))
    assert f['bid_api'] in only_failed
    assert f['bid_ui'] not in only_failed


def test_filter_by_owner_keyword_excludes_the_other(two_users_two_batches):
    f = two_users_two_batches
    only_alice = _ids(batch_repo.admin_list_batches(page=1, page_size=100,
                                                    owner_keyword=f['uname_a']))
    assert f['bid_ui'] in only_alice
    assert f['bid_api'] not in only_alice


def test_filter_by_name_keyword_excludes_the_other(two_users_two_batches):
    f = two_users_two_batches
    only_api = _ids(batch_repo.admin_list_batches(page=1, page_size=100,
                                                  name_keyword='接口'))
    assert f['bid_api'] in only_api
    assert f['bid_ui'] not in only_api


def test_detail_spans_users_and_lists_children(two_users_two_batches):
    f = two_users_two_batches
    d = batch_repo.admin_get_batch_detail(f['bid_api'])
    assert d is not None
    assert d['batch']['owner_username'].startswith('bob-')
    assert d['batch']['source'] == 'api'
    assert [s['id'] for s in d['sessions']] == [f['sid']]


def test_detail_missing_returns_none():
    assert batch_repo.admin_get_batch_detail('b-does-not-exist') is None


def test_get_owner_returns_the_real_owner(two_users_two_batches):
    """写路径靠它拿归属用户；返回错的用户会让重试静默失效。"""
    f = two_users_two_batches
    assert batch_repo.admin_get_batch_owner(f['bid_api']) == f['uid_b']
    assert batch_repo.admin_get_batch_owner('b-nope') is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_batch_admin_repo.py -v`

Expected: FAIL，`AttributeError: module 'utils.batch_repo' has no attribute 'admin_list_batches'`

- [ ] **Step 3: 实现前三个函数**

在 `server/utils/batch_repo.py` 末尾追加：

```python
# ---------------------------------------------------------------------------
# 管理员作用域（跨用户）—— 只读
#
# 与上面那些函数的语义**相反**：这里刻意不按归属用户过滤。之所以另起函数而不是把
# 既有函数的 user_id 改成可空，是因为那样一次手滑（忘传参数）会从"报错"变成
# "返回全部用户的数据"——这类默认值是安全事故的常见来源。写路径不设这种函数：
# 重试走 admin_get_batch_owner 拿到归属用户后，复用既有的按归属过滤的写函数。
# ---------------------------------------------------------------------------

MAX_ADMIN_MESSAGES = 500

_ADMIN_SELECT = """
    SELECT b.*, u.username AS owner_username,
           CASE WHEN b.api_key_id IS NULL THEN 'ui' ELSE 'api' END AS source
      FROM ai_chat_batches b
      JOIN users u ON u.id = b.user_id
"""


def _admin_filters(status, owner_keyword, source, name_keyword):
    """把筛选条件编成 (where_sql, params)。空条件不产生任何谓词。"""
    where, params = [], []
    if status:
        where.append("b.status = %s")
        params.append(status)
    if owner_keyword:
        where.append("u.username ILIKE %s")
        params.append(f'%{owner_keyword}%')
    if source == 'ui':
        where.append("b.api_key_id IS NULL")
    elif source == 'api':
        where.append("b.api_key_id IS NOT NULL")
    if name_keyword:
        where.append("b.name ILIKE %s")
        params.append(f'%{name_keyword}%')
    return (' WHERE ' + ' AND '.join(where)) if where else '', params


def admin_list_batches(*, page: int, page_size: int,
                       status: str | None = None,
                       owner_keyword: str | None = None,
                       source: str | None = None,
                       name_keyword: str | None = None) -> dict:
    """跨全部用户列出批任务，附归属用户名与来源。"""
    where, params = _admin_filters(status, owner_keyword, source, name_keyword)
    offset = (page - 1) * page_size
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                _ADMIN_SELECT + where + " ORDER BY b.created_at DESC LIMIT %s OFFSET %s",
                (*params, page_size, offset))
            items = [dict(r) for r in cur.fetchall()]
            cur.execute(
                "SELECT count(*) AS n FROM ai_chat_batches b "
                "JOIN users u ON u.id = b.user_id" + where, tuple(params))
            total = cur.fetchone()['n']
    return {'items': items, 'total': total, 'page': page, 'pageSize': page_size}


def admin_get_batch_detail(batch_id: str) -> dict | None:
    """跨用户取批任务详情 + 子任务清单。"""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(_ADMIN_SELECT + " WHERE b.id = %s", (batch_id,))
            batch = cur.fetchone()
            if not batch:
                return None
            cur.execute(
                "SELECT id, status, batch_seq, batch_input_file, error_message, "
                "       last_message_preview "
                "  FROM ai_chat_sessions WHERE batch_id = %s ORDER BY batch_seq",
                (batch_id,))
            sessions = [dict(r) for r in cur.fetchall()]
    return {'batch': dict(batch), 'sessions': sessions}


def admin_get_batch_owner(batch_id: str) -> str | None:
    """该批任务的归属用户 id。写路径专用：拿到它之后复用按归属过滤的写函数，
    这样系统里永远不存在"不按归属过滤的写"。"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM ai_chat_batches WHERE id = %s", (batch_id,))
            row = cur.fetchone()
    return row[0] if row else None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_batch_admin_repo.py -v`

Expected: 除 `admin_get_child_messages` 相关外全部 PASS（该函数在 Step 5 加）

- [ ] **Step 5: 追加消息查询的失败测试**

在 `server/tests/test_ai_batch_admin_repo.py` 末尾追加：

```python
@pytest.fixture
def batch_with_many_messages(two_users_two_batches):
    """给子任务塞 3 条消息，且**故意让 created_at 完全相同、seq 乱序**。

    这是本文件最关键的构造：批任务子会话的消息由 worker 批量持久化，同事务内
    now() 是常量、created_at 相同，按它排序取不出确定结果。若实现改回
    ORDER BY created_at，本用例会红。
    """
    f = two_users_two_batches
    sid = f['sid']
    import json
    with get_db() as conn:
        with conn.cursor() as cur:
            for mid, role, text in (('m-c', 'assistant', '第三条'),
                                    ('m-a', 'user', '第一条'),
                                    ('m-b', 'assistant', '第二条')):
                cur.execute(
                    "INSERT INTO ai_chat_messages (id, session_id, role, content, created_at) "
                    "VALUES (%s,%s,%s,%s::jsonb, TIMESTAMPTZ '2026-01-01 00:00:00+08')",
                    (mid + '-' + sid, sid, role,
                     json.dumps([{'type': 'text', 'text': text}])))
        conn.commit()
    # seq 由 BIGSERIAL 按插入顺序生成：m-c < m-a < m-b
    yield {**f, 'expected_order': ['第三条', '第一条', '第二条']}


def _texts(res):
    out = []
    for m in res['messages']:
        for p in (m['content'] or []):
            if p.get('type') == 'text':
                out.append(p['text'])
    return out


def test_messages_ordered_by_seq_not_created_at(batch_with_many_messages):
    f = batch_with_many_messages
    res = batch_repo.admin_get_child_messages(f['bid_api'], f['sid'])
    assert _texts(res) == f['expected_order']
    assert res['total'] == 3
    assert res['truncated'] is False


def test_messages_truncate_to_the_most_recent(batch_with_many_messages):
    """截断必须取**最近**的若干条，并仍以 seq 升序返回（自然阅读顺序）。"""
    f = batch_with_many_messages
    res = batch_repo.admin_get_child_messages(f['bid_api'], f['sid'], limit=2)
    assert res['truncated'] is True
    assert res['total'] == 3
    assert _texts(res) == f['expected_order'][-2:]     # 最近 2 条，升序


def test_messages_reject_session_from_another_batch(batch_with_many_messages):
    """sessionId 必须属于该 batchId，否则这个路径就成了"用任意 batchId 读任意会话"。"""
    f = batch_with_many_messages
    assert batch_repo.admin_get_child_messages(f['bid_ui'], f['sid']) is None
```

- [ ] **Step 6: 跑测试确认失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_batch_admin_repo.py -v -k messages`

Expected: FAIL，`AttributeError: ... has no attribute 'admin_get_child_messages'`

- [ ] **Step 7: 实现消息查询**

在 `server/utils/batch_repo.py` 末尾继续追加：

```python
def admin_get_child_messages(batch_id: str, session_id: str,
                             limit: int = MAX_ADMIN_MESSAGES) -> dict | None:
    """某个子任务的对话（只读）。session 不属于该 batch 时返回 None。

    按 `seq` 而非 `created_at` 排序：worker 批量持久化时同事务内 now() 是常量，
    多条消息的 created_at 相同，按它排序取不出确定顺序（`ai_chat_messages.seq`
    这一列正是为此新增的）。

    有界：取**最近** limit 条后再反转为升序，让调用方拿到的始终是自然阅读顺序。
    子会话持久化的是完整对话含工具调用，长 agent 运行可能数百条、体积可观。
    """
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT 1 FROM ai_chat_sessions WHERE id = %s AND batch_id = %s",
                (session_id, batch_id))
            if not cur.fetchone():
                return None
            cur.execute("SELECT count(*) AS n FROM ai_chat_messages WHERE session_id = %s",
                        (session_id,))
            total = cur.fetchone()['n']
            cur.execute(
                "SELECT id, role, content, created_at, meta FROM ai_chat_messages "
                " WHERE session_id = %s ORDER BY seq DESC LIMIT %s",
                (session_id, limit))
            rows = [dict(r) for r in cur.fetchall()]
    rows.reverse()
    return {'messages': rows, 'truncated': total > len(rows), 'total': total}
```

- [ ] **Step 8: 跑全部数据层测试**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_batch_admin_repo.py -v`

Expected: 11 passed

- [ ] **Step 9: 变异验证排序测试确有鉴别力**

把 `admin_get_child_messages` 里的 `ORDER BY seq DESC` 临时改成 `ORDER BY created_at DESC`，重跑 `-k messages`，确认 `test_messages_ordered_by_seq_not_created_at` 变红；改回后确认变绿。把红/绿两次的失败信息原文记进任务报告。

- [ ] **Step 10: 提交**

```bash
git add server/utils/batch_repo.py server/tests/test_ai_batch_admin_repo.py
git commit -m "feat(batch-admin): 数据层新增管理员作用域的跨用户读函数"
```

---

## Task 2: 只读端点（列表 / 详情 / 对话）

**Files:**
- Create: `server/routes/ai_batch_admin.py`
- Modify: `server/app.py`
- Test: `server/tests/test_ai_batch_admin_routes.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `admin_list_batches` / `admin_get_batch_detail` / `admin_get_child_messages` / `MAX_ADMIN_MESSAGES`；`auth.require_permission`。
- Produces:
  ```python
  ai_batch_admin_bp    # Blueprint, url_prefix='/ai/chat/admin/batches'
  # GET  ''                                    -> {'items':[...], 'total':int}
  # GET  '/<batch_id>'                         -> {'batch':{...}, 'sessions':[...]}
  # GET  '/<batch_id>/sessions/<sid>/messages' -> {'messages':[...], 'truncated':bool, 'total':int}
  _batch_out(b: dict) -> dict                  # 出参白名单
  ```

- [ ] **Step 1: 写失败测试**

新建 `server/tests/test_ai_batch_admin_routes.py`：

```python
"""管理员批任务端点的测试。

最重要的一条是权限门的**参数化遍历**：不挑单个端点测，而是遍历 url_map 里该前缀
下的全部规则逐个断言。此前有过教训——某个装饰器只在一个端点上有测试，其余端点漏挂
不会被任何测试发现。这样写之后，新增端点忘挂装饰器会立刻红。
"""
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = '/ai/chat/admin/batches'


def _admin_routes(app):
    """url_map 里属于本蓝图的全部规则，展开成 (method, url) 列表。
    路径参数用占位值填上，只关心是否被权限门拦住。"""
    out = []
    for rule in app.url_map.iter_rules():
        if not str(rule).startswith(BASE):
            continue
        url = str(rule).replace('<batch_id>', 'b-x').replace('<sid>', 's-x')
        for m in (rule.methods or set()) & {'GET', 'POST'}:
            out.append((m, url))
    return sorted(out)


def test_blueprint_exposes_the_expected_routes(app):
    """先钉住"有哪些路由"，否则下一条遍历断言可能在零条规则上空转通过。"""
    routes = _admin_routes(app)
    assert len(routes) >= 3
    assert ('GET', BASE) in routes


@pytest.mark.parametrize('method,url', [
    pytest.param(m, u, id=f'{m} {u}')
    for m, u in [('GET', BASE),
                 ('GET', f'{BASE}/b-x'),
                 ('GET', f'{BASE}/b-x/sessions/s-x/messages')]
])
def test_read_routes_require_admin_capability(client, method, url):
    """无 token 一律不放行（401/403），绝不能返回 200。"""
    resp = client.open(url, method=method)
    assert resp.status_code in (401, 403)


def test_every_admin_route_is_permission_gated(app, client):
    """遍历式兜底：任何新增端点漏挂 require_permission 都会在这里红。"""
    leaked = []
    for method, url in _admin_routes(app):
        resp = client.open(url, method=method)
        if resp.status_code not in (401, 403):
            leaked.append((method, url, resp.status_code))
    assert leaked == []


def test_list_passes_filters_through(client, admin_headers):
    with patch('routes.ai_batch_admin.admin_list_batches',
               return_value={'items': [], 'total': 0}) as lb:
        resp = client.get(f'{BASE}?status=failed&source=api&owner=alice&keyword=报告'
                          f'&page=2&pageSize=50', headers=admin_headers)
    assert resp.status_code == 200
    kw = lb.call_args[1]
    assert kw['status'] == 'failed'
    assert kw['source'] == 'api'
    assert kw['owner_keyword'] == 'alice'
    assert kw['name_keyword'] == '报告'
    assert kw['page'] == 2 and kw['page_size'] == 50


def test_list_caps_page_size_at_100(client, admin_headers):
    with patch('routes.ai_batch_admin.admin_list_batches',
               return_value={'items': [], 'total': 0}) as lb:
        client.get(f'{BASE}?pageSize=9999', headers=admin_headers)
    assert lb.call_args[1]['page_size'] == 100


def test_list_returns_contract_fields_only(client, admin_headers):
    """不能把 prompt / workspace_path 这类内部字段吐给前端。"""
    row = {'id': 'b-1', 'name': 'n', 'status': 'failed', 'total': 2, 'done': 0,
           'failed': 2, 'agent': None, 'model': None, 'created_at': None,
           'completed_at': None, 'owner_username': 'alice', 'source': 'api',
           'user_id': 'u-1', 'api_key_id': 'ak-1', 'prompt': '内部 prompt'}
    with patch('routes.ai_batch_admin.admin_list_batches',
               return_value={'items': [row], 'total': 1}):
        resp = client.get(BASE, headers=admin_headers)
    body = resp.get_json()
    assert set(body['items'][0]) == {
        'batchId', 'name', 'status', 'total', 'done', 'failed', 'agent', 'model',
        'createdAt', 'completedAt', 'ownerUsername', 'source'}
    assert 'prompt' not in resp.get_data(as_text=True)
    assert 'api_key_id' not in resp.get_data(as_text=True)


def test_detail_not_found_is_404(client, admin_headers):
    with patch('routes.ai_batch_admin.admin_get_batch_detail', return_value=None):
        resp = client.get(f'{BASE}/b-nope', headers=admin_headers)
    assert resp.status_code == 404


def test_messages_not_found_is_404(client, admin_headers):
    """子任务不属于该批任务时，仓储返回 None -> 端点必须 404。"""
    with patch('routes.ai_batch_admin.admin_get_child_messages', return_value=None):
        resp = client.get(f'{BASE}/b-1/sessions/s-x/messages', headers=admin_headers)
    assert resp.status_code == 404


def test_messages_pass_through_truncation_info(client, admin_headers):
    payload = {'messages': [], 'truncated': True, 'total': 900}
    with patch('routes.ai_batch_admin.admin_get_child_messages', return_value=payload):
        resp = client.get(f'{BASE}/b-1/sessions/s-1/messages', headers=admin_headers)
    body = resp.get_json()
    assert body['truncated'] is True and body['total'] == 900
```

在同文件顶部追加两个 fixture（`app` / `client` 由 `conftest.py` 提供，此处只补管理员请求头）：

```python
@pytest.fixture
def admin_headers(app):
    """签一个 admin 角色的 JWT。权限解析读的是角色 slug，admin 是永久超管。"""
    import jwt as _jwt
    from config import JWT_SECRET
    token = _jwt.encode({'userId': 'user-admin', 'username': 'admin',
                         'role': 'admin'}, JWT_SECRET, algorithm='HS256')
    return {'Authorization': f'Bearer {token}'}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_batch_admin_routes.py -v`

Expected: FAIL，`test_blueprint_exposes_the_expected_routes` 断言 `len(routes) >= 3` 失败（蓝图尚未注册，url_map 里一条都没有）

- [ ] **Step 3: 新建蓝图与三个只读端点**

新建 `server/routes/ai_batch_admin.py`：

```python
"""批任务的**管理员**视图：跨全部用户查看与重试。

为什么独立成文件而不是塞进 routes/ai_chat_batches.py 或 routes/ai_chat.py：
前者的 url_prefix 被钉死在 /ai/chat/batches，装不下 /ai/chat/admin/batches；
后者已逾千行。更要紧的是，这一层每个端点的安全假设与普通端点**相反**——跨用户查，
而不是按归属过滤。物理隔开能让这条边界一眼可见。

权限一律走 admin.ai_chat_admin（"AI 会话治理"）。不新建能力键：该能力现在已能
列出全部用户的 AI 会话，而批任务的子任务就是会话——能看会话就已经能看到这里的
全部内容，再拆一个键是假粒度。
"""
from flask import Blueprint, jsonify, request

from auth import require_permission
from utils.batch_repo import (MAX_ADMIN_MESSAGES, admin_get_batch_detail,
                              admin_get_child_messages, admin_list_batches)

ai_batch_admin_bp = Blueprint('ai_batch_admin', __name__,
                              url_prefix='/ai/chat/admin/batches')


def _iso(v):
    return v.isoformat() if v else None


def _batch_out(b: dict) -> dict:
    """出参白名单。用固定键集合而不是"删掉几个字段"——后者在数据层新增列时会
    悄悄把内部字段泄出去。"""
    return {
        'batchId': b.get('id'),
        'name': b.get('name'),
        'status': b.get('status'),
        'total': b.get('total'),
        'done': b.get('done'),
        'failed': b.get('failed'),
        'agent': b.get('agent'),
        'model': b.get('model'),
        'createdAt': _iso(b.get('created_at')),
        'completedAt': _iso(b.get('completed_at')),
        'ownerUsername': b.get('owner_username'),
        'source': b.get('source'),
    }


def _session_out(s: dict) -> dict:
    return {
        'sessionId': s.get('id'),
        'seq': s.get('batch_seq'),
        'name': (s.get('batch_input_file') or '').replace('\\', '/').rsplit('/', 1)[-1],
        'status': s.get('status'),
        'error': s.get('error_message'),
        'preview': s.get('last_message_preview'),
    }


@ai_batch_admin_bp.get('')
@require_permission('admin.ai_chat_admin')
def list_all():
    try:
        page = max(1, int(request.args.get('page', 1)))
        page_size = min(max(1, int(request.args.get('pageSize', 20))), 100)
    except (TypeError, ValueError):
        return jsonify({'error': 'page 与 pageSize 必须是整数'}), 400

    data = admin_list_batches(
        page=page, page_size=page_size,
        status=(request.args.get('status') or '').strip() or None,
        owner_keyword=(request.args.get('owner') or '').strip() or None,
        source=(request.args.get('source') or '').strip() or None,
        name_keyword=(request.args.get('keyword') or '').strip() or None,
    )
    return jsonify({'items': [_batch_out(b) for b in data['items']],
                    'total': data['total']})


@ai_batch_admin_bp.get('/<batch_id>')
@require_permission('admin.ai_chat_admin')
def detail(batch_id):
    d = admin_get_batch_detail(batch_id)
    if not d:
        return jsonify({'error': '批任务不存在'}), 404
    return jsonify({'batch': _batch_out(d['batch']),
                    'sessions': [_session_out(s) for s in d['sessions']]})


@ai_batch_admin_bp.get('/<batch_id>/sessions/<sid>/messages')
@require_permission('admin.ai_chat_admin')
def child_messages(batch_id, sid):
    """子任务的完整对话，只读。sid 必须属于该 batch_id，否则 404 ——
    不做这层校验的话，这个路径就成了"用任意 batchId 读任意会话"的通道。"""
    data = admin_get_child_messages(batch_id, sid, limit=MAX_ADMIN_MESSAGES)
    if data is None:
        return jsonify({'error': '子任务不存在'}), 404
    return jsonify({
        'messages': [{'id': m['id'], 'role': m['role'], 'content': m['content'],
                      'createdAt': _iso(m.get('created_at')), 'meta': m.get('meta')}
                     for m in data['messages']],
        'truncated': data['truncated'],
        'total': data['total'],
    })
```

- [ ] **Step 4: 注册蓝图**

在 `server/app.py` 的导入区（`from routes.ai_chat_batches import ai_chat_batches_bp` 之后）加：

```python
from routes.ai_batch_admin import ai_batch_admin_bp
```

在注册区 `app.register_blueprint(ai_chat_batches_bp)` 之后、`app.register_blueprint(dynamic_bp)` **之前**加：

```python
app.register_blueprint(ai_batch_admin_bp)
```

`dynamic_bp` 是 catch-all `/<collection>`，任何在它之后注册的蓝图都会被抢占。

- [ ] **Step 5: 跑测试确认通过**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_batch_admin_routes.py -v`

Expected: 全部 PASS

- [ ] **Step 6: 变异验证权限门测试确有鉴别力**

临时注释掉 `list_all` 上的 `@require_permission('admin.ai_chat_admin')`，重跑，确认 `test_every_admin_route_is_permission_gated` 与对应的参数化用例变红；恢复后变绿。把失败信息原文记进任务报告。

- [ ] **Step 7: 提交**

```bash
git add server/routes/ai_batch_admin.py server/app.py server/tests/test_ai_batch_admin_routes.py
git commit -m "feat(batch-admin): 管理员只读端点（列表/详情/子任务对话）"
```

---

## Task 3: 重试端点（整批 / 单条）+ 审计

**Files:**
- Modify: `server/routes/ai_batch_admin.py`
- Test: `server/tests/test_ai_batch_admin_routes.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `admin_get_batch_owner`；既有的 `reset_failed_to_pending(user_id, batch_id, *, api_key_id=None) -> int`、`reexecute_child(user_id, batch_id, session_id) -> dict | None`（非终态抛 `ValueError`）；`utils.batch_engine.get_worker`；`utils.operation_log.log_operation`。
- Produces:
  ```
  POST '/<batch_id>/retry-failed'                    -> {'retried': int}
  POST '/<batch_id>/sessions/<sid>/reexecute'        -> {'reexecuted': True}
  ```

**行为约定（与既有实现刻意不同，实施时不要"顺手统一"）：**

| 情形 | 本端点 | 既有实现 |
|---|---|---|
| 批任务不存在 | **404** | 内部端点返回 `200 {"retried": 0}` |
| 非终态批任务调 retry-failed | **允许执行** | 对外 API 返回 409 |
| 单条 reexecute 非终态子任务 | **409** | repo 抛 `ValueError` |

非终态允许重试是刻意的：`reset_failed_to_pending` 只动已经 `failed` 的子任务，一个仍在跑的大批任务里已经挂掉的那几条，管理员应该能立刻重试而不必等整批结束。

- [ ] **Step 1: 写失败测试**

在 `server/tests/test_ai_batch_admin_routes.py` 末尾追加：

```python
# --- 重试端点 ---------------------------------------------------------------

def test_retry_uses_the_batch_real_owner_not_the_admin(client, admin_headers):
    """本文件最要紧的一条。

    管理员重试**别人的**批任务时，底层写函数必须收到该批任务真实归属用户的 id。
    传成管理员自己的 id 会让重试静默失效（按归属过滤查不到行，retried 恒为 0），
    而一个只断言 200 的测试对此完全无感。
    """
    with patch('routes.ai_batch_admin.admin_get_batch_owner', return_value='u-owner'), \
         patch('routes.ai_batch_admin.reset_failed_to_pending', return_value=2) as rf, \
         patch('routes.ai_batch_admin.admin_get_batch_detail',
               return_value={'batch': {'id': 'b-1', 'name': 'n'}, 'sessions': []}), \
         patch('routes.ai_batch_admin.get_worker'), \
         patch('routes.ai_batch_admin.log_operation'):
        resp = client.post(f'{BASE}/b-1/retry-failed', headers=admin_headers)

    assert resp.status_code == 200
    assert resp.get_json() == {'retried': 2}
    assert rf.call_args[0][0] == 'u-owner'      # 归属用户，不是 user-admin
    assert rf.call_args[0][1] == 'b-1'


def test_retry_missing_batch_is_404(client, admin_headers):
    """不同于内部端点的 200 {"retried": 0}：管理工具里静默返回 0 会让 batchId
    打错看起来像"没有失败的子任务"。"""
    with patch('routes.ai_batch_admin.admin_get_batch_owner', return_value=None), \
         patch('routes.ai_batch_admin.reset_failed_to_pending') as rf:
        resp = client.post(f'{BASE}/b-nope/retry-failed', headers=admin_headers)
    assert resp.status_code == 404
    rf.assert_not_called()


def test_retry_allowed_on_running_batch(client, admin_headers):
    """刻意不设终态门（与对外 API 的 409 相反）。这条正向测试是为了防止日后有人
    照抄对外实现把 409 加回来，悄悄改掉管理场景的语义。"""
    with patch('routes.ai_batch_admin.admin_get_batch_owner', return_value='u-owner'), \
         patch('routes.ai_batch_admin.reset_failed_to_pending', return_value=1), \
         patch('routes.ai_batch_admin.admin_get_batch_detail',
               return_value={'batch': {'id': 'b-1', 'name': 'n', 'status': 'running'},
                             'sessions': []}), \
         patch('routes.ai_batch_admin.get_worker'), \
         patch('routes.ai_batch_admin.log_operation'):
        resp = client.post(f'{BASE}/b-1/retry-failed', headers=admin_headers)
    assert resp.status_code == 200


def test_retry_notifies_worker_only_when_something_was_reset(client, admin_headers):
    """没重置任何东西就不必唤醒 worker。"""
    with patch('routes.ai_batch_admin.admin_get_batch_owner', return_value='u-owner'), \
         patch('routes.ai_batch_admin.reset_failed_to_pending', return_value=0), \
         patch('routes.ai_batch_admin.admin_get_batch_detail',
               return_value={'batch': {'id': 'b-1', 'name': 'n'}, 'sessions': []}), \
         patch('routes.ai_batch_admin.get_worker') as gw, \
         patch('routes.ai_batch_admin.log_operation'):
        client.post(f'{BASE}/b-1/retry-failed', headers=admin_headers)
    gw.return_value.notify.assert_not_called()


def test_retry_writes_operation_log(client, admin_headers):
    """管理员代替他人重跑任务却不留痕，出问题无法追溯是谁动的。"""
    with patch('routes.ai_batch_admin.admin_get_batch_owner', return_value='u-owner'), \
         patch('routes.ai_batch_admin.reset_failed_to_pending', return_value=3), \
         patch('routes.ai_batch_admin.admin_get_batch_detail',
               return_value={'batch': {'id': 'b-1', 'name': '报告批'}, 'sessions': []}), \
         patch('routes.ai_batch_admin.get_worker'), \
         patch('routes.ai_batch_admin.log_operation') as lg:
        client.post(f'{BASE}/b-1/retry-failed', headers=admin_headers)
    args = lg.call_args[0]
    assert args[1] == 'ai_chat_batch'      # target_type
    assert args[2] == 'b-1'                # target_id


def test_reexecute_uses_the_batch_real_owner(client, admin_headers):
    with patch('routes.ai_batch_admin.admin_get_batch_owner', return_value='u-owner'), \
         patch('routes.ai_batch_admin.reexecute_child',
               return_value={'batch': {}, 'sessions': []}) as rx, \
         patch('routes.ai_batch_admin.get_worker'), \
         patch('routes.ai_batch_admin.log_operation'):
        resp = client.post(f'{BASE}/b-1/sessions/s-1/reexecute', headers=admin_headers)
    assert resp.status_code == 200
    assert rx.call_args[0] == ('u-owner', 'b-1', 's-1')


def test_reexecute_non_terminal_child_is_409_not_500(client, admin_headers):
    """repo 对非终态子任务抛 ValueError；必须落成 409，不能裸 500。"""
    with patch('routes.ai_batch_admin.admin_get_batch_owner', return_value='u-owner'), \
         patch('routes.ai_batch_admin.reexecute_child',
               side_effect=ValueError('only completed/failed children can be re-executed')), \
         patch('routes.ai_batch_admin.get_worker'), \
         patch('routes.ai_batch_admin.log_operation'):
        resp = client.post(f'{BASE}/b-1/sessions/s-1/reexecute', headers=admin_headers)
    assert resp.status_code == 409
    assert 're-executed' in resp.get_json()['error']


def test_reexecute_missing_child_is_404(client, admin_headers):
    with patch('routes.ai_batch_admin.admin_get_batch_owner', return_value='u-owner'), \
         patch('routes.ai_batch_admin.reexecute_child', return_value=None), \
         patch('routes.ai_batch_admin.get_worker'), \
         patch('routes.ai_batch_admin.log_operation'):
        resp = client.post(f'{BASE}/b-1/sessions/s-1/reexecute', headers=admin_headers)
    assert resp.status_code == 404
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_batch_admin_routes.py -v -k "retry or reexecute"`

Expected: FAIL，404（路由不存在）

- [ ] **Step 3: 实现两个写端点**

在 `server/routes/ai_batch_admin.py` 的导入区补充：

```python
from utils.batch_engine import get_worker
from utils.batch_repo import (admin_get_batch_owner, reexecute_child,
                              reset_failed_to_pending)
from utils.operation_log import log_operation
```

在文件末尾追加：

```python
# --- 写操作 -----------------------------------------------------------------
# 这里**不使用**任何"不按归属过滤的写函数"：先用 admin_get_batch_owner 查出批任务
# 的归属用户，再拿它调用既有的按归属过滤的写函数。读路径泄漏的是可见性，写路径泄漏
# 的是数据完整性，后者严重得多——所以系统里干脆不存在这种写函数。

@ai_batch_admin_bp.post('/<batch_id>/retry-failed')
@require_permission('admin.ai_chat_admin')
def retry_failed(batch_id):
    """重试该批任务中全部失败的子任务。

    刻意**不设终态门**（与对外 API 的 409 相反）：reset_failed_to_pending 只动
    已经 failed 的子任务，一个仍在跑的大批任务里已经挂掉的那几条，管理员应该能
    立刻重试而不必等整批结束。
    """
    owner = admin_get_batch_owner(batch_id)
    if owner is None:
        return jsonify({'error': '批任务不存在'}), 404

    n = reset_failed_to_pending(owner, batch_id)
    if n:
        get_worker().notify()

    d = admin_get_batch_detail(batch_id)
    name = (d or {}).get('batch', {}).get('name') or batch_id
    log_operation('update', 'ai_chat_batch', batch_id, name,
                  f'管理员重试批任务「{name}」中 {n} 个失败的子任务')
    return jsonify({'retried': n})


@ai_batch_admin_bp.post('/<batch_id>/sessions/<sid>/reexecute')
@require_permission('admin.ai_chat_admin')
def reexecute(batch_id, sid):
    """重跑单个子任务（从头开始，清掉旧消息）。"""
    owner = admin_get_batch_owner(batch_id)
    if owner is None:
        return jsonify({'error': '批任务不存在'}), 404

    try:
        d = reexecute_child(owner, batch_id, sid)
    except ValueError as e:
        # repo 只在"子任务不是 completed/failed"时抛这个——它正在跑，重跑会与
        # 运行中的会话冲突。409 而不是 500。
        return jsonify({'error': str(e)}), 409
    if d is None:
        return jsonify({'error': '子任务不存在'}), 404

    get_worker().notify()
    log_operation('update', 'ai_chat_batch', batch_id, batch_id,
                  f'管理员重跑批任务 {batch_id} 的子任务 {sid}')
    return jsonify({'reexecuted': True})
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_batch_admin_routes.py -v`

Expected: 全部 PASS（含 Task 2 的用例，且遍历式权限门测试会自动覆盖这两个新端点）

- [ ] **Step 5: 变异验证归属作用域测试确有鉴别力**

把 `retry_failed` 里的 `reset_failed_to_pending(owner, batch_id)` 临时改成
`reset_failed_to_pending(g.current_user['userId'], batch_id)`（需 `from flask import g`），
重跑，确认 `test_retry_uses_the_batch_real_owner_not_the_admin` 变红；恢复后变绿。
把失败信息原文记进任务报告。

- [ ] **Step 6: 跑后端全量确认无回归**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest -q`

Expected: 全部通过（基线以实施时 `main` 的数字为准，新增用例数应等于本任务与前序任务新增的总和）

- [ ] **Step 7: 提交**

```bash
git add server/routes/ai_batch_admin.py server/tests/test_ai_batch_admin_routes.py
git commit -m "feat(batch-admin): 管理员重试端点（整批/单条）+ 操作审计"
```

---

## Task 4: 前端 API 层与 store

**Files:**
- Create: `src/api/aiBatchAdmin.ts`, `src/stores/aiBatchAdmin.ts`
- Test: `src/stores/__tests__/aiBatchAdmin.test.ts`

**Interfaces:**
- Consumes: Task 2、3 的端点（前端调用时带 `/api` 前缀，Vite 与 `proxy.py` 都会把它剥掉）。
- Produces:
  ```ts
  // src/api/aiBatchAdmin.ts
  export interface AdminBatch { batchId: string; name: string; status: string;
    total: number; done: number; failed: number; agent: string | null;
    model: string | null; createdAt: string | null; completedAt: string | null;
    ownerUsername: string; source: 'ui' | 'api' }
  export interface AdminChild { sessionId: string; seq: number; name: string;
    status: string; error: string | null; preview: string | null }
  export interface AdminFilters { status?: string; owner?: string;
    source?: string; keyword?: string }
  listAdminBatches(filters: AdminFilters, page: number, pageSize: number)
  getAdminBatch(batchId: string)
  getAdminChildMessages(batchId: string, sessionId: string)
  retryAdminBatch(batchId: string)
  reexecuteAdminChild(batchId: string, sessionId: string)

  // src/stores/aiBatchAdmin.ts
  useAiBatchAdminStore()   // { items, total, page, pageSize, filters, loading,
                           //   fetchList, startPolling, stopPolling, TERMINAL }
  ```

- [ ] **Step 1: 写失败测试**

新建 `src/stores/__tests__/aiBatchAdmin.test.ts`：

```typescript
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/aiBatchAdmin', () => ({
  listAdminBatches: vi.fn(() => Promise.resolve({ items: [], total: 0 })),
  getAdminBatch: vi.fn(),
  getAdminChildMessages: vi.fn(),
  retryAdminBatch: vi.fn(),
  reexecuteAdminChild: vi.fn(),
}))

import { listAdminBatches } from '@/api/aiBatchAdmin'
import { useAiBatchAdminStore } from '@/stores/aiBatchAdmin'

describe('aiBatchAdmin store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('把筛选条件原样传给 API', async () => {
    const store = useAiBatchAdminStore()
    store.filters.status = 'failed'
    store.filters.source = 'api'
    store.filters.owner = 'alice'
    store.filters.keyword = '报告'
    await store.fetchList()

    expect(listAdminBatches).toHaveBeenCalledWith(
      { status: 'failed', source: 'api', owner: 'alice', keyword: '报告' },
      1, 20,
    )
  })

  it('全部批任务都是终态时不启动轮询', async () => {
    // 这条比"会轮询"更容易写成假绿，所以显式推进定时器后断言调用次数没涨。
    vi.mocked(listAdminBatches).mockResolvedValue({
      items: [
        { batchId: 'b1', status: 'completed' } as any,
        { batchId: 'b2', status: 'failed' } as any,
      ],
      total: 2,
    })
    const store = useAiBatchAdminStore()
    await store.fetchList()
    const callsAfterFetch = vi.mocked(listAdminBatches).mock.calls.length

    store.startPolling()
    await vi.advanceTimersByTimeAsync(60_000)   // 远超 10s 间隔

    expect(vi.mocked(listAdminBatches).mock.calls.length).toBe(callsAfterFetch)
  })

  it('存在非终态批任务时按 10 秒间隔轮询', async () => {
    vi.mocked(listAdminBatches).mockResolvedValue({
      items: [{ batchId: 'b1', status: 'running' } as any],
      total: 1,
    })
    const store = useAiBatchAdminStore()
    await store.fetchList()
    const before = vi.mocked(listAdminBatches).mock.calls.length

    store.startPolling()
    await vi.advanceTimersByTimeAsync(10_000)

    expect(vi.mocked(listAdminBatches).mock.calls.length).toBe(before + 1)
    store.stopPolling()
  })

  it('stopPolling 之后不再发请求', async () => {
    vi.mocked(listAdminBatches).mockResolvedValue({
      items: [{ batchId: 'b1', status: 'running' } as any],
      total: 1,
    })
    const store = useAiBatchAdminStore()
    await store.fetchList()
    store.startPolling()
    store.stopPolling()
    const before = vi.mocked(listAdminBatches).mock.calls.length

    await vi.advanceTimersByTimeAsync(60_000)

    expect(vi.mocked(listAdminBatches).mock.calls.length).toBe(before)
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run src/stores/__tests__/aiBatchAdmin.test.ts`

Expected: FAIL，无法解析 `@/stores/aiBatchAdmin`

- [ ] **Step 3: 实现 API 层**

新建 `src/api/aiBatchAdmin.ts`。

⚠️ **照抄本仓库既有的 API 层写法**（见 `src/api/aiChatBatches.ts`）：从 `@/utils/request` 具名导入 `get` / `post`，签名是 `get<T>(url, params?)`、`post<T>(url, data?)` —— 返回的已经是解包后的响应体，**不是** axios 实例上的方法。

```typescript
import { get, post } from '@/utils/request'

export interface AdminBatch {
  batchId: string
  name: string
  status: string
  total: number
  done: number
  failed: number
  agent: string | null
  model: string | null
  createdAt: string | null
  completedAt: string | null
  ownerUsername: string
  source: 'ui' | 'api'
}

export interface AdminChild {
  sessionId: string
  seq: number
  name: string
  status: string
  error: string | null
  preview: string | null
}

export interface AdminMessage {
  id: string
  role: string
  content: any[]
  createdAt: string | null
  meta: any
}

export interface AdminFilters {
  status?: string
  owner?: string
  source?: string
  keyword?: string
}

const BASE = '/ai/chat/admin/batches'

export function listAdminBatches(filters: AdminFilters, page: number, pageSize: number) {
  return get<{ items: AdminBatch[]; total: number }>(BASE, { ...filters, page, pageSize })
}

export function getAdminBatch(batchId: string) {
  return get<{ batch: AdminBatch; sessions: AdminChild[] }>(`${BASE}/${batchId}`)
}

export function getAdminChildMessages(batchId: string, sessionId: string) {
  return get<{ messages: AdminMessage[]; truncated: boolean; total: number }>(
    `${BASE}/${batchId}/sessions/${sessionId}/messages`)
}

export function retryAdminBatch(batchId: string) {
  return post<{ retried: number }>(`${BASE}/${batchId}/retry-failed`)
}

export function reexecuteAdminChild(batchId: string, sessionId: string) {
  return post<{ reexecuted: boolean }>(`${BASE}/${batchId}/sessions/${sessionId}/reexecute`)
}
```

- [ ] **Step 4: 实现 store**

新建 `src/stores/aiBatchAdmin.ts`：

```typescript
import { defineStore } from 'pinia'
import { ref, reactive } from 'vue'
import { listAdminBatches, type AdminBatch, type AdminFilters } from '@/api/aiBatchAdmin'

/** 批任务的终态。轮询只在列表里还存在非终态项时才有意义。 */
const TERMINAL = ['completed', 'partial', 'failed']
const POLL_INTERVAL_MS = 10_000

/**
 * 管理态 store，与用户态的 aiChatBatches.ts **分开**。
 * 两者的过滤语义相反（一个跨用户、一个按归属），混用一个 store 会让
 * “当前是谁的视角”变成隐式状态。
 */
export const useAiBatchAdminStore = defineStore('aiBatchAdmin', () => {
  const items = ref<AdminBatch[]>([])
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(20)
  const loading = ref(false)
  const filters = reactive<AdminFilters>({ status: '', owner: '', source: '', keyword: '' })

  let timer: ReturnType<typeof setInterval> | null = null

  function hasNonTerminal() {
    return items.value.some(b => !TERMINAL.includes(b.status))
  }

  async function fetchList() {
    loading.value = true
    try {
      const clean: AdminFilters = {}
      for (const k of ['status', 'owner', 'source', 'keyword'] as const) {
        const v = (filters[k] || '').trim()
        if (v) clean[k] = v
      }
      const res = await listAdminBatches(clean, page.value, pageSize.value)
      items.value = res.items
      total.value = res.total
    } finally {
      loading.value = false
    }
  }

  /** 仅当当前页存在非终态批任务时才轮询 —— 管理页可能长时间开着无人查看，
   *  无条件轮询是白白消耗后端连接。 */
  function startPolling() {
    stopPolling()
    if (!hasNonTerminal()) return
    timer = setInterval(() => {
      if (!hasNonTerminal()) { stopPolling(); return }
      fetchList()
    }, POLL_INTERVAL_MS)
  }

  function stopPolling() {
    if (timer) { clearInterval(timer); timer = null }
  }

  return { items, total, page, pageSize, loading, filters,
           fetchList, startPolling, stopPolling, TERMINAL }
})
```

- [ ] **Step 5: 跑测试确认通过**

Run: `npx vitest run src/stores/__tests__/aiBatchAdmin.test.ts`

Expected: 4 passed

- [ ] **Step 6: 变异验证"不轮询"那条确有鉴别力**

把 `startPolling` 里的 `if (!hasNonTerminal()) return` 临时删掉，重跑，确认
「全部批任务都是终态时不启动轮询」变红；恢复后变绿。把失败信息原文记进任务报告。

- [ ] **Step 7: 提交**

```bash
git add src/api/aiBatchAdmin.ts src/stores/aiBatchAdmin.ts src/stores/__tests__/aiBatchAdmin.test.ts
git commit -m "feat(batch-admin): 前端 API 层与管理态 store（条件轮询）"
```

---

## Task 5: 页面、只读对话查看器与设置中心入口

**Files:**
- Create: `src/views/admin/AiBatchAdmin.vue`, `src/components/ai-chat/BatchConversationView.vue`
- Modify: `src/views/admin/hub/settingsCatalog.ts`

**Interfaces:**
- Consumes: Task 4 的 `useAiBatchAdminStore()` 与 `src/api/aiBatchAdmin.ts` 的全部导出；既有的 `@/components/ai-chat/MarkdownView.vue`。
- Produces: 路由 `/admin/ai-batches`（由 `settingsCatalog.ts` 自动派生）。

- [ ] **Step 1: 实现只读对话查看器**

新建 `src/components/ai-chat/BatchConversationView.vue`：

```vue
<template>
  <div class="batch-conversation">
    <el-alert v-if="truncated" type="info" :closable="false" show-icon
              :title="`仅显示最近 ${messages.length} 条，共 ${total} 条`" />
    <el-empty v-if="!loading && !messages.length" description="该子任务还没有对话记录" />
    <div v-for="m in messages" :key="m.id" class="msg" :class="`msg--${m.role}`">
      <div class="msg__role">{{ m.role === 'user' ? '提问' : '助手' }}</div>
      <div class="msg__body">
        <template v-for="(p, i) in (m.content || [])" :key="i">
          <MarkdownView v-if="p.type === 'text' && p.text" :text="p.text" />
          <el-collapse v-else-if="p.type === 'tool_use'" class="msg__tool">
            <el-collapse-item :title="`工具调用：${p.name || '未知'}`">
              <pre>{{ JSON.stringify(p, null, 2) }}</pre>
            </el-collapse-item>
          </el-collapse>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import MarkdownView from '@/components/ai-chat/MarkdownView.vue'
import type { AdminMessage } from '@/api/aiBatchAdmin'

// 只读：刻意没有输入框。管理员在别人的会话里发消息语义上说不通（以谁的身份发？）。
defineProps<{
  messages: AdminMessage[]
  truncated: boolean
  total: number
  loading: boolean
}>()
</script>

<style scoped lang="scss">
.batch-conversation { max-height: 60vh; overflow-y: auto; }
.msg { padding: 12px 0; border-bottom: 1px solid var(--el-border-color-lighter); }
.msg__role { font-size: 12px; color: var(--el-text-color-secondary); margin-bottom: 6px; }
.msg__tool pre { font-size: 12px; overflow-x: auto; }
</style>
```

- [ ] **Step 2: 实现页面**

新建 `src/views/admin/AiBatchAdmin.vue`：

```vue
<template>
  <div class="batch-admin">
    <div class="batch-admin__filters">
      <el-select v-model="store.filters.status" placeholder="状态" clearable style="width: 130px">
        <el-option v-for="s in STATUSES" :key="s.value" :label="s.label" :value="s.value" />
      </el-select>
      <el-select v-model="store.filters.source" placeholder="来源" clearable style="width: 120px">
        <el-option label="界面" value="ui" />
        <el-option label="API" value="api" />
      </el-select>
      <el-input v-model="store.filters.owner" placeholder="归属用户" clearable style="width: 160px" />
      <el-input v-model="store.filters.keyword" placeholder="批任务名称" clearable style="width: 200px" />
      <el-button type="primary" @click="reload">查询</el-button>
      <el-button :loading="store.loading" @click="reload">刷新</el-button>
    </div>

    <el-table :data="store.items" v-loading="store.loading" style="width: 100%">
      <el-table-column prop="name" label="名称" min-width="180" show-overflow-tooltip />
      <el-table-column prop="ownerUsername" label="归属用户" width="140" />
      <el-table-column label="来源" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.source === 'api' ? 'warning' : 'info'">
            {{ row.source === 'api' ? 'API' : '界面' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="statusTagType(row.status)">{{ statusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="进度" width="140">
        <template #default="{ row }">
          成功 {{ row.done }} / 失败 {{ row.failed }} / 共 {{ row.total }}
        </template>
      </el-table-column>
      <el-table-column label="Agent·模型" min-width="160">
        <template #default="{ row }">{{ row.agent || '默认' }} · {{ row.model || '默认' }}</template>
      </el-table-column>
      <el-table-column label="创建时间" width="170">
        <template #default="{ row }">{{ fmt(row.createdAt) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openDetail(row)">详情</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination class="batch-admin__pager" background layout="total, prev, pager, next"
                   :total="store.total" :current-page="store.page" :page-size="store.pageSize"
                   @current-change="onPage" />

    <el-drawer v-model="detailOpen" :title="detail?.batch?.name || '批任务详情'" size="60%">
      <div v-if="detail" class="batch-admin__detail">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="归属用户">{{ detail.batch.ownerUsername }}</el-descriptions-item>
          <el-descriptions-item label="来源">{{ detail.batch.source === 'api' ? 'API' : '界面' }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{ statusLabel(detail.batch.status) }}</el-descriptions-item>
          <el-descriptions-item label="进度">
            成功 {{ detail.batch.done }} / 失败 {{ detail.batch.failed }} / 共 {{ detail.batch.total }}
          </el-descriptions-item>
        </el-descriptions>

        <div class="batch-admin__actions">
          <el-button type="warning" :disabled="!detail.batch.failed" :loading="retrying"
                     @click="onRetryAll">重试全部失败（{{ detail.batch.failed }}）</el-button>
        </div>

        <el-table :data="detail.sessions" size="small" style="width: 100%">
          <el-table-column prop="seq" label="#" width="60" />
          <el-table-column prop="name" label="文件" min-width="160" show-overflow-tooltip />
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="statusTagType(row.status)">{{ statusLabel(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="error" label="错误信息" min-width="220" show-overflow-tooltip />
          <el-table-column label="操作" width="150" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openConversation(row)">查看对话</el-button>
              <el-button link type="warning" :disabled="!isTerminal(row.status)"
                         @click="onReexecute(row)">重跑</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-drawer>

    <el-dialog v-model="convOpen" :title="`子任务对话：${convName}`" width="70%" top="6vh">
      <BatchConversationView :messages="convMessages" :truncated="convTruncated"
                             :total="convTotal" :loading="convLoading" />
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useAiBatchAdminStore } from '@/stores/aiBatchAdmin'
import BatchConversationView from '@/components/ai-chat/BatchConversationView.vue'
import {
  getAdminBatch, getAdminChildMessages, retryAdminBatch, reexecuteAdminChild,
  type AdminBatch, type AdminChild, type AdminMessage,
} from '@/api/aiBatchAdmin'

const STATUSES = [
  { value: 'pending', label: '待处理' },
  { value: 'running', label: '执行中' },
  { value: 'completed', label: '已完成' },
  { value: 'partial', label: '部分完成' },
  { value: 'failed', label: '失败' },
]
const LABELS: Record<string, string> = Object.fromEntries(
  STATUSES.map(s => [s.value, s.label]))

const store = useAiBatchAdminStore()
const detailOpen = ref(false)
const detail = ref<{ batch: AdminBatch; sessions: AdminChild[] } | null>(null)
const retrying = ref(false)
const convOpen = ref(false)
const convName = ref('')
const convMessages = ref<AdminMessage[]>([])
const convTruncated = ref(false)
const convTotal = ref(0)
const convLoading = ref(false)

function statusLabel(s: string) { return LABELS[s] || s }
function statusTagType(s: string) {
  if (s === 'failed') return 'danger'
  if (s === 'completed') return 'success'
  if (s === 'partial') return 'warning'
  if (s === 'running') return 'primary'
  return 'info'
}
function isTerminal(s: string) { return s === 'completed' || s === 'failed' }
function fmt(v: string | null) { return v ? new Date(v).toLocaleString('zh-CN') : '-' }

async function reload() {
  store.page = 1
  await store.fetchList()
  store.startPolling()
}

async function onPage(p: number) {
  store.page = p
  await store.fetchList()
  store.startPolling()
}

async function openDetail(row: AdminBatch) {
  detail.value = await getAdminBatch(row.batchId)
  detailOpen.value = true
}

async function onRetryAll() {
  if (!detail.value) return
  retrying.value = true
  try {
    const res = await retryAdminBatch(detail.value.batch.batchId)
    ElMessage.success(`已重置 ${res.retried} 个失败的子任务`)
    detail.value = await getAdminBatch(detail.value.batch.batchId)
    await store.fetchList()
    store.startPolling()
  } finally {
    retrying.value = false
  }
}

async function onReexecute(row: AdminChild) {
  if (!detail.value) return
  await reexecuteAdminChild(detail.value.batch.batchId, row.sessionId)
  ElMessage.success('已提交重跑')
  detail.value = await getAdminBatch(detail.value.batch.batchId)
  await store.fetchList()
  store.startPolling()
}

async function openConversation(row: AdminChild) {
  if (!detail.value) return
  convName.value = row.name
  convOpen.value = true
  convLoading.value = true
  try {
    const res = await getAdminChildMessages(detail.value.batch.batchId, row.sessionId)
    convMessages.value = res.messages
    convTruncated.value = res.truncated
    convTotal.value = res.total
  } finally {
    convLoading.value = false
  }
}

onMounted(reload)
onUnmounted(() => store.stopPolling())
</script>

<style scoped lang="scss">
.batch-admin__filters { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.batch-admin__pager { margin-top: 12px; justify-content: flex-end; }
.batch-admin__actions { margin: 12px 0; }
</style>
```

- [ ] **Step 3: 加设置中心入口**

在 `src/views/admin/hub/settingsCatalog.ts` 的「AI 能力」分组（`{ id: 'ai', label: 'AI 能力', ... }`）的 `items` 数组末尾追加一条：

```ts
    { id: 'ai-batches', label: 'AI 批任务', perm: 'admin.ai_chat_admin',
      component: () => import('@/views/admin/AiBatchAdmin.vue') },
```

侧边栏渲染、路由生成、权限判定全部从该目录自动派生，无需手写路由。

- [ ] **Step 4: 同步"条目总数"断言（必做，不是可选）**

加这一条会把设置项从 21 变成 22，**已有测试会立刻变红**。改这一处：

`src/views/admin/hub/__tests__/settingsCatalog.test.ts:14`

```typescript
    expect(ALL_SETTINGS_ITEMS).toHaveLength(22)
```

同文件第 12 行的用例名「共 7 组 21 条」一并改为「共 7 组 22 条」。

**不要动**这两处 —— 分组数没变，且它是从常量派生的、会自动跟随：

- `settingsCatalog.test.ts:13` 与 `:73` 的 `toHaveLength(7)`（仍是 7 组）
- `src/router/__tests__/settingsHubRoutes.test.ts:36` 的
  `expect(buildSettingsRoutes()).toHaveLength(ALL_SETTINGS_ITEMS.length)`

- [ ] **Step 5: 类型检查与既有测试**

Run: `npm run build`

Expected: vue-tsc 零错误、构建成功

Run: `npx vitest run src/views/admin/hub src/router/__tests__/settingsHubRoutes.test.ts src/stores/__tests__/aiBatchAdmin.test.ts`

Expected: 全部通过

- [ ] **Step 6: 提交**

```bash
git add src/views/admin/AiBatchAdmin.vue src/components/ai-chat/BatchConversationView.vue src/views/admin/hub/settingsCatalog.ts src/views/admin/hub/__tests__/settingsCatalog.test.ts
git commit -m "feat(batch-admin): 管理页、只读对话查看器与设置中心入口"
```

---

## Task 6: 文档、Playwright 与 proxy.py 验证

**Files:**
- Modify: `docs/user-guide/ai/batch-tasks.md`, `CLAUDE.md`
- Create: `docs/user-guide/_images/batch-admin.png`（截图）

**Interfaces:**
- Consumes: Task 1–5 的全部成果。
- Produces: 无代码接口。

- [ ] **Step 1: 造多用户测试数据**

在开发库里造出**至少两个不同用户**、且**来源一 UI 一 API**的批任务，其中至少一个含失败子任务。可复用 Task 1 测试里的 SQL 形状，直接用 psql 或一次性脚本插入。没有跨用户数据的话，这个页面的核心价值（跨用户可见）根本没被验证到。

- [ ] **Step 2: 启动服务并用 Playwright 走通真实流程**

```bash
npm run dev:all      # 后端改了要重启：Werkzeug 启动时加载模块
```

用 Playwright MCP 工具依次完成，并对**渲染结果**断言（不是只看 200）：

1. 以 `admin` / `admin123` 登录
2. 进入 设置中心 → AI 能力 → AI 批任务（`/admin/ai-batches`）
3. 断言表格里**同时出现两个不同 `ownerUsername`** 的行，且「来源」列同时出现「界面」与「API」
4. 状态筛选选「失败」，断言列表收窄
5. 点「详情」，断言抽屉里列出子任务，失败子任务的「错误信息」列非空
6. 点「查看对话」，断言弹窗渲染出消息（或在无消息时显示空态）
7. 点「重试全部失败」，断言出现成功提示
8. **回查数据库**确认该批任务的失败子任务状态真的从 `failed` 变回了 `pending`

截图存入 `.playwright-mcp/`。

- [ ] **Step 3: 经 proxy.py 验证**

这是新的路由形态，CLAUDE.md 要求必须过生产入口这一关（无 SSE、无自定义请求头，所以只需确认可达且未被 catch-all 抢占）：

```bash
npm run build
cd server && python proxy.py     # 自带后端 :3001 + 服务 :8080
```

经 `:8080` 逐个打这 5 个端点（带 admin JWT），确认返回的是各自的业务响应而**不是 404**：

```
GET  /api/ai/chat/admin/batches
GET  /api/ai/chat/admin/batches/<id>
GET  /api/ai/chat/admin/batches/<id>/sessions/<sid>/messages
POST /api/ai/chat/admin/batches/<id>/retry-failed
POST /api/ai/chat/admin/batches/<id>/sessions/<sid>/reexecute
```

在报告里**明确写出"经 proxy.py 验证，不只是 Vite"**，并附实测的请求/响应证据。

- [ ] **Step 4: 写用户文档**

在 `docs/user-guide/ai/batch-tasks.md` 末尾新增一节「管理员：批任务管理页」，覆盖：

- 入口：设置中心 → AI 能力 → AI 批任务；需要「AI 会话治理」（`admin.ai_chat_admin`）权限
- 能看到什么：**全部用户**的批任务，含归属用户与来源（界面 / API）
- 筛选：状态、归属用户、来源、名称
- 能做什么：查看子任务状态与错误信息、只读查看完整对话、重试全部失败的子任务、重跑单个子任务
- 明确说明**不能**做什么：不能删除、不能修改 prompt/agent/模型（这些仍由批任务所有者在 AI 助手侧边栏操作）
- 提示：重试会让批任务从终态回到执行中；对话只显示最近 500 条
- 嵌入 Step 2 拍的页面截图（放 `docs/user-guide/_images/batch-admin.png`）

- [ ] **Step 5: 更新 CLAUDE.md**

在「AI Agent Chat — Batch Tasks (M1.5)」小节末尾补一段，说明：管理页 `/admin/ai-batches`（`src/views/admin/AiBatchAdmin.vue`）+ 管理端点蓝图 `server/routes/ai_batch_admin.py`（`/ai/chat/admin/batches`，`admin.ai_chat_admin` 把关，**必须在 `dynamic_bp` 之前注册**）；数据层是 `batch_repo` 里另起的 `admin_*` 读函数（刻意不把既有函数的 `user_id` 改可空）；写路径先 `admin_get_batch_owner` 再复用按归属过滤的写函数；消息按 `seq` 排序并截断到 500 条。

- [ ] **Step 6: 全量回归**

```bash
npm run test:all
```

Expected: 前后端全部通过

- [ ] **Step 7: 提交**

```bash
git add docs/ CLAUDE.md
git commit -m "docs(batch-admin): 管理页用户文档 + CLAUDE.md，并完成 Playwright 与 proxy.py 验证"
```

---

## 自查记录

**1. 规格覆盖** —— 逐节比对 `2026-08-12-batch-admin-page-design.md`：

| 规格 | 落在 |
|---|---|
| §2.1 新蓝图 5 端点 + 注册顺序 | Task 2 Step 3/4、Task 3 Step 3 |
| §2.2 管理员读函数、不改既有函数、写路径先查归属 | Task 1 Step 3、Task 3 Step 3 |
| §2.3 `ownerUsername` / `source` | Task 1 Step 3、Task 2 Step 3 |
| §2.4 四个筛选 + 分页 | Task 1 Step 3、Task 2 Step 3、Task 5 Step 2 |
| §2.4.1 404 / 不设终态门 / reexecute 409 | Task 3（含正向测试钉住"不设终态门"） |
| §2.5 审计 | Task 3 Step 3 |
| §2.6 按 `seq` 排序 | Task 1 Step 7（含变异验证 Step 9） |
| §3.1 设置中心入口 | Task 5 Step 3 |
| §3.2 页面结构 | Task 5 Step 2 |
| §3.3 只读查看器、不重构 AiChatView | Task 5 Step 1 |
| §3.4 store 分开 | Task 4 Step 4 |
| §3.5 条件轮询 | Task 4 Step 4（含变异验证 Step 6） |
| §4.1 并发重试幂等 | 无需代码改动（`reset_failed_to_pending` 已幂等 + worker `SKIP LOCKED`），Task 3 说明中记录 |
| §4.2 reexecute 409 | Task 3 Step 1/3 |
| §4.3 消息有界 + 升序 | Task 1 Step 7、Task 5 Step 1（截断提示） |
| §4.3.1 `sessionId` 属于 `batchId` | Task 1 Step 7、Task 2 Step 3 |
| §4.4 权限中途失效不静默 | Task 5 —— `request.ts` 拦截器已全局提示 4xx，页面不吞错；不额外实现 |
| §4.5 操作期间被删 → 404 | Task 3 Step 3（`admin_get_batch_owner` 返回 None） |
| §5 测试与验证 | Task 1/2/3 的变异步骤、Task 4、Task 6 |

**2. 占位符扫描** —— 无 TBD/TODO；每个代码步骤都给了可直接落地的完整代码。

**3. 类型一致性** —— `admin_list_batches` / `admin_get_batch_detail` / `admin_get_batch_owner` / `admin_get_child_messages` 在 Task 1 定义，Task 2、3 按同名同参调用；前端 `AdminBatch` / `AdminChild` / `AdminMessage` 在 Task 4 定义，Task 5 按同名导入；`_batch_out` 的键集合与 Task 2 测试里的断言集合逐字一致。
