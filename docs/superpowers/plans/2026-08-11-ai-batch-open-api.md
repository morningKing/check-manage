# AI 批任务对外 API 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已有的「AI 批任务」能力（N 个文件 + 1 个 prompt → N 个隔离 AI 会话）对外暴露为 API Key 鉴权的 REST 接口，并配套调用文档。

**Architecture:** 新建独立蓝图 `/api/v1/ai-batches/*` 作为**对外契约层**，只调用 `batch_repo` / `batch_engine` / `workspace` 的底层函数，不复用内部 `ai_chat_batches.py` 的路由处理器 —— 这样内部返回体重构不会破坏外部集成。`api_keys` 新增 `owner_user_id` 绑定创建者，`ai_chat_batches` 新增 `api_key_id` 实现按密钥的严格隔离。

**Tech Stack:** Python Flask + psycopg2 + PostgreSQL；Pytest。纯后端，无前端改动。

设计文档：`docs/superpowers/specs/2026-08-11-ai-batch-open-api-design.md`

## Global Constraints

- **本改造从 `main` 另开分支**（`main` 当前是 `5d2c051`，已含行操作与设置扁平化）。
- 后端测试命令必须带 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`（Windows 必需）：
  `cd /e/wsl/check/check-manage/server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/xxx.py -v`
- **前台跑测试，不要放后台。**
- 所有面向用户的错误信息用中文。
- **绝对不新增任何依赖。**
- 提交信息中文 + conventional commit 前缀。
- **不改批任务引擎的执行逻辑**，**不改内部 `ai_chat_batches.py` 的任何路由**。
- `open_api_batches_bp` 必须在 `app.py` 中**注册在 `dynamic_bp` 之前**（那个 catch-all 永远最后）。
- **绝对不要用 `taskkill /IM node.exe` 或任何批量杀 node/python 的命令** —— 会干掉客户端托管的 Playwright MCP。按 PID 精确 kill。
- 后端测试基线：`main` 上是 **1448 passed**。

---

### Task 1: DDL 迁移 + 密钥绑定用户

**Files:**
- Modify: `server/init_db.py`（两列迁移 + 一个索引）
- Modify: `server/auth.py`（`api_key_required` 取 `owner_user_id`）
- Modify: `server/routes/api_keys.py`（创建密钥时写 `owner_user_id`）
- Test: `server/tests/test_api_key_owner.py`（新建）

**Interfaces:**
- Consumes: 无
- Produces:
  - DB 列 `api_keys.owner_user_id`、`ai_chat_batches.api_key_id`
  - `g.api_key_info = {'id': str, 'name': str, 'ownerUserId': str | None}`

- [ ] **Step 1: 加迁移**

在 `server/init_db.py` 里，找到已有的列迁移区（形如「Migration: add xxx column to yyy」的连续块），追加三段：

```python
        # Migration: add owner_user_id to api_keys (AI 批任务对外 API 需要把密钥绑定到用户)
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'api_keys' AND column_name = 'owner_user_id'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE api_keys ADD COLUMN owner_user_id VARCHAR(100) REFERENCES users(id)")
            conn.commit()
            print("Added owner_user_id column to api_keys table.")

        # Migration: add api_key_id to ai_chat_batches (对外 API 创建的批任务按密钥隔离)
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'ai_chat_batches' AND column_name = 'api_key_id'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE ai_chat_batches ADD COLUMN api_key_id VARCHAR(100) REFERENCES api_keys(id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ai_chat_batches_api_key "
                        "ON ai_chat_batches(api_key_id, created_at DESC)")
            conn.commit()
            print("Added api_key_id column to ai_chat_batches table.")
```

**存量数据处理**：两列都保持 NULL，不回填。`owner_user_id` 为 NULL 的密钥会被 Task 3 的守卫挡在 AI 批任务接口之外（这是设计意图，见设计文档 §3.2）；`api_key_id` 为 NULL 表示 UI 创建的批任务，永远不会被 API 命中。

- [ ] **Step 2: 跑迁移，确认幂等**

```bash
cd /e/wsl/check/check-manage/server && python init_db.py
```
首次应打印两行 `Added ... column ...`；**再跑一次不应再打印**。两次输出都记进报告。

- [ ] **Step 3: 写失败的测试**

创建 `server/tests/test_api_key_owner.py`：

```python
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _fake_db(row):
    """mock get_db，让 api_key_required 的 SELECT 返回指定行。"""
    cur = MagicMock()
    cur.fetchone.return_value = row
    conn = MagicMock()
    conn.cursor.return_value = cur
    from contextlib import contextmanager

    @contextmanager
    def fake_get_db():
        yield conn

    return fake_get_db


def test_api_key_info_carries_owner_user_id():
    """api_key_required 必须把 owner_user_id 放进 g.api_key_info。"""
    import auth as auth_mod
    from flask import Flask, g, jsonify

    app = Flask(__name__)

    @app.get('/probe')
    @auth_mod.api_key_required
    def probe():
        return jsonify(g.api_key_info)

    # (id, name, is_active, owner_user_id)
    with patch.object(auth_mod, 'get_db', _fake_db(('ak-1', '集成密钥', True, 'user-42'))):
        resp = app.test_client().get('/probe', headers={'X-API-Key': 'cm_whatever'})

    assert resp.status_code == 200
    assert resp.get_json() == {'id': 'ak-1', 'name': '集成密钥', 'ownerUserId': 'user-42'}


def test_api_key_info_owner_can_be_none_for_legacy_keys():
    """存量密钥 owner_user_id 为 NULL 时不报错，ownerUserId 为 None。"""
    import auth as auth_mod
    from flask import Flask, g, jsonify

    app = Flask(__name__)

    @app.get('/probe')
    @auth_mod.api_key_required
    def probe():
        return jsonify(g.api_key_info)

    with patch.object(auth_mod, 'get_db', _fake_db(('ak-old', '老密钥', True, None))):
        resp = app.test_client().get('/probe', headers={'X-API-Key': 'cm_whatever'})

    assert resp.status_code == 200
    assert resp.get_json()['ownerUserId'] is None
```

> 若 `auth.py` 里 `get_db` 是**函数体内局部** `from db import get_db`，`patch.object(auth_mod, 'get_db', ...)` 打不中 —— 那就改用 `patch('db.get_db', ...)`。**先读一眼 `server/auth.py` 顶部确认它是模块级还是局部 import**，以实际写法为准（本仓库两种都存在）。

- [ ] **Step 4: 跑测试确认失败**

```bash
cd /e/wsl/check/check-manage/server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_api_key_owner.py -v
```
预期：FAIL —— 当前 `g.api_key_info` 只有 `{'id', 'name'}`。

- [ ] **Step 5: 改 `api_key_required`**

`server/auth.py` 的 `api_key_required` 里，把
```python
                'SELECT id, name, is_active FROM api_keys WHERE key_hash = %s',
```
改成
```python
                'SELECT id, name, is_active, owner_user_id FROM api_keys WHERE key_hash = %s',
```
并把
```python
        g.api_key_info = {'id': row[0], 'name': row[1]}
```
改成
```python
        g.api_key_info = {'id': row[0], 'name': row[1], 'ownerUserId': row[3]}
```

- [ ] **Step 6: 创建密钥时写 owner_user_id**

`server/routes/api_keys.py` 的 `create_api_key` 里，把
```python
            'INSERT INTO api_keys (id, name, key_hash) VALUES (%s, %s, %s)',
            (key_id, name, key_hash_val),
```
改成
```python
            'INSERT INTO api_keys (id, name, key_hash, owner_user_id) VALUES (%s, %s, %s, %s)',
            (key_id, name, key_hash_val, g.current_user['userId']),
```

确认该文件已 import `g`（`from flask import ... g`）；没有就加上。该路由的装饰器已保证登录态，`g.current_user` 必然存在。

- [ ] **Step 7: 跑测试 + 全量回归**

```bash
cd /e/wsl/check/check-manage/server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_api_key_owner.py -v
cd /e/wsl/check/check-manage/server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/ -q
```
预期：新测试 2 passed；全量 1448 + 2 = 1450。若既有的 api_keys 相关测试因 INSERT 列数变化而红，按新签名更新它们。

- [ ] **Step 8: 提交**

```bash
git add server/init_db.py server/auth.py server/routes/api_keys.py server/tests/test_api_key_owner.py
git commit -m "feat(open-api): API 密钥绑定创建者，批任务表记录来源密钥"
```

---

### Task 2: batch_repo 扩展 —— 密钥归属与结果读取

**Files:**
- Modify: `server/utils/batch_repo.py`
- Test: `server/tests/test_batch_repo_api_scope.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 DB 列
- Produces:
  - `create_batch(..., api_key_id: str | None = None)`（新增带默认值的关键字参数）
  - `list_batches(user_id, *, page, page_size, api_key_id: str | None = None)`
  - `get_batch_detail(user_id, batch_id, *, api_key_id: str | None = None)`
  - `delete_batch(user_id, batch_id, *, api_key_id: str | None = None)`
  - `reset_failed_to_pending(user_id, batch_id, *, api_key_id: str | None = None)`
  - `get_batch_results(batch_id: str) -> list[dict]` —— 每项 `{'name', 'status', 'output', 'error'}`

**背景**：`batch_repo` 现有的查询函数都按 `user_id` 圈定范围。对外 API 还要**再按 `api_key_id` 圈一层**（严格按密钥隔离）。做法是给这些函数加一个带默认值的可选关键字参数 —— `None` 时行为与今天**完全一致**，所以 UI 创建、AI 扫描任务、行操作按钮这些现有调用点一行都不用改。

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_batch_repo_api_scope.py`：

```python
import os
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _capture_db():
    """mock get_db；返回 (fake_get_db, cur)，cur 上能读到执行过的 SQL 与参数。"""
    cur = MagicMock()
    cur.fetchall.return_value = []
    cur.fetchone.return_value = {'n': 0}
    cur.rowcount = 0
    ctx = MagicMock()
    ctx.__enter__ = lambda s: cur
    ctx.__exit__ = lambda s, *a: None
    conn = MagicMock()
    conn.cursor.return_value = ctx

    @contextmanager
    def fake_get_db():
        yield conn

    return fake_get_db, cur


def _sqls(cur):
    return [c[0][0] for c in cur.execute.call_args_list]


def _params(cur):
    return [c[0][1] for c in cur.execute.call_args_list if len(c[0]) > 1]


# ---------- api_key_id 过滤 ----------

def test_list_batches_without_api_key_id_keeps_old_behavior():
    import utils.batch_repo as repo
    fake, cur = _capture_db()
    with patch.object(repo, 'get_db', fake):
        repo.list_batches('user-1', page=1, page_size=20)
    for sql in _sqls(cur):
        assert 'api_key_id' not in sql


def test_list_batches_with_api_key_id_filters():
    import utils.batch_repo as repo
    fake, cur = _capture_db()
    with patch.object(repo, 'get_db', fake):
        repo.list_batches('user-1', page=1, page_size=20, api_key_id='ak-1')
    assert all('api_key_id = %s' in s for s in _sqls(cur))
    assert any('ak-1' in p for p in _params(cur))


def test_get_batch_detail_with_api_key_id_filters():
    import utils.batch_repo as repo
    fake, cur = _capture_db()
    cur.fetchone.return_value = None
    with patch.object(repo, 'get_db', fake):
        assert repo.get_batch_detail('user-1', 'b-1', api_key_id='ak-1') is None
    assert 'api_key_id = %s' in _sqls(cur)[0]


def test_delete_batch_with_api_key_id_filters():
    import utils.batch_repo as repo
    fake, cur = _capture_db()
    with patch.object(repo, 'get_db', fake):
        repo.delete_batch('user-1', 'b-1', api_key_id='ak-1')
    assert 'api_key_id = %s' in _sqls(cur)[0]


def test_reset_failed_with_api_key_id_filters():
    import utils.batch_repo as repo
    fake, cur = _capture_db()
    with patch.object(repo, 'get_db', fake):
        repo.reset_failed_to_pending('user-1', 'b-1', api_key_id='ak-1')
    assert any('api_key_id = %s' in s for s in _sqls(cur))


def test_create_batch_writes_api_key_id():
    import utils.batch_repo as repo
    fake, cur = _capture_db()
    cur.fetchone.return_value = {'id': 'b-1'}
    with patch.object(repo, 'get_db', fake):
        repo.create_batch('user-1', name='n', prompt='p', template_id=None,
                          files=[{'name': 'a.pdf', 'path': 'batch-staging/user-1/s/a.pdf'}],
                          api_key_id='ak-1')
    insert_sql = _sqls(cur)[0]
    assert 'api_key_id' in insert_sql
    assert 'ak-1' in _params(cur)[0]


# ---------- get_batch_results ----------

def test_get_batch_results_uses_basename_not_full_path():
    """batch_input_file 存的是工作区相对路径，直接返回会泄漏内部 userId。"""
    import utils.batch_repo as repo
    fake, cur = _capture_db()
    cur.fetchall.return_value = [
        {'batch_input_file': 'batch-staging/user-42/abc/订单A.pdf',
         'status': 'completed', 'error_message': None,
         'content': [{'type': 'text', 'text': '第一行\n第二行'}]},
    ]
    with patch.object(repo, 'get_db', fake):
        results = repo.get_batch_results('b-1')
    assert results[0]['name'] == '订单A.pdf'
    assert 'user-42' not in str(results[0])


def test_get_batch_results_returns_full_text_not_first_line():
    """output 必须是完整回复，不能是 last_message_preview 的首行。"""
    import utils.batch_repo as repo
    fake, cur = _capture_db()
    cur.fetchall.return_value = [
        {'batch_input_file': 'batch-staging/u/s/a.pdf',
         'status': 'completed', 'error_message': None,
         'content': [{'type': 'text', 'text': '第一行\n第二行'}]},
    ]
    with patch.object(repo, 'get_db', fake):
        results = repo.get_batch_results('b-1')
    assert '第二行' in results[0]['output']


def test_get_batch_results_concatenates_multiple_text_parts():
    import utils.batch_repo as repo
    fake, cur = _capture_db()
    cur.fetchall.return_value = [
        {'batch_input_file': 'batch-staging/u/s/a.pdf',
         'status': 'completed', 'error_message': None,
         'content': [{'type': 'text', 'text': 'AAA'},
                     {'type': 'tool_use', 'name': 'read'},
                     {'type': 'text', 'text': 'BBB'}]},
    ]
    with patch.object(repo, 'get_db', fake):
        results = repo.get_batch_results('b-1')
    assert results[0]['output'] == 'AAA\nBBB'   # tool_use 部分被丢弃


def test_get_batch_results_pending_child_has_null_output():
    import utils.batch_repo as repo
    fake, cur = _capture_db()
    cur.fetchall.return_value = [
        {'batch_input_file': 'batch-staging/u/s/a.pdf',
         'status': 'pending', 'error_message': None, 'content': None},
    ]
    with patch.object(repo, 'get_db', fake):
        results = repo.get_batch_results('b-1')
    assert results[0] == {'name': 'a.pdf', 'status': 'pending',
                          'output': None, 'error': None}


def test_get_batch_results_failed_child_carries_error():
    import utils.batch_repo as repo
    fake, cur = _capture_db()
    cur.fetchall.return_value = [
        {'batch_input_file': 'batch-staging/u/s/a.pdf',
         'status': 'failed', 'error_message': '超时', 'content': None},
    ]
    with patch.object(repo, 'get_db', fake):
        results = repo.get_batch_results('b-1')
    assert results[0]['status'] == 'failed'
    assert results[0]['error'] == '超时'
    assert results[0]['output'] is None
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /e/wsl/check/check-manage/server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_batch_repo_api_scope.py -v
```
预期：多数 FAIL（参数不接受 / 函数不存在）。

- [ ] **Step 3: 给四个查询函数加 `api_key_id` 过滤**

在 `server/utils/batch_repo.py` 中：

`list_batches` 改为：
```python
def list_batches(user_id: str, *, page: int, page_size: int,
                 api_key_id: str | None = None) -> dict:
    """列出批任务。

    `api_key_id` 非 None 时**再**按来源密钥圈一层 —— 对外 API 要求严格按密钥隔离。
    传 None 时行为与加这个参数之前完全一致，故 UI / 扫描任务 / 行操作等既有调用点无需改动。
    """
    offset = (page - 1) * page_size
    scope = "WHERE user_id = %s"
    base = [user_id]
    if api_key_id is not None:
        scope += " AND api_key_id = %s"
        base.append(api_key_id)
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"SELECT * FROM ai_chat_batches {scope} "
                "ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (*base, page_size, offset),
            )
            items = [dict(r) for r in cur.fetchall()]
            cur.execute(f"SELECT count(*) AS n FROM ai_chat_batches {scope}", tuple(base))
            total = cur.fetchone()['n']
    return {'items': items, 'total': total, 'page': page, 'pageSize': page_size}
```

`get_batch_detail`：签名加 `, *, api_key_id: str | None = None`，第一条 SQL 改为
```python
            sql = "SELECT * FROM ai_chat_batches WHERE id=%s AND user_id=%s"
            params = [batch_id, user_id]
            if api_key_id is not None:
                sql += " AND api_key_id = %s"
                params.append(api_key_id)
            cur.execute(sql, tuple(params))
```

`delete_batch`：同样加参数，DELETE 语句追加 `AND api_key_id = %s`。

`reset_failed_to_pending`：同样加参数，把内层
```sql
AND batch_id IN (SELECT id FROM ai_chat_batches WHERE user_id=%s)
```
改成在 `api_key_id` 非 None 时追加 `AND api_key_id = %s`。

> f-string 只拼**字面量片段**（`WHERE user_id = %s` 这类），所有值一律走 `%s` 占位符。不要把 `user_id` / `api_key_id` 的**值**拼进 SQL。

- [ ] **Step 4: `create_batch` 接受并写入 `api_key_id`**

签名末尾加 `api_key_id: str | None = None`（带默认值，放在现有关键字参数之后）。INSERT 改为：
```python
                "INSERT INTO ai_chat_batches "
                "  (id, user_id, name, prompt, template_id, total, status, agent, model, "
                "   provision_repo, provision_ref, api_key_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s, %s) RETURNING *",
                (batch_id, user_id, name, prompt, template_id, len(files), agent, model,
                 provision_repo, provision_ref, api_key_id),
```
数一遍 `%s` 个数与参数个数是否相等（应为 11 个 `%s` + 字面量 `'pending'`）。

- [ ] **Step 5: 新增 `get_batch_results`**

追加到 `server/utils/batch_repo.py`：

```python
def get_batch_results(batch_id: str) -> list[dict]:
    """按 batch_seq 顺序返回每个子任务的结果。

    `output` 取该子会话**最后一条 assistant 消息**里全部 text 片段的拼接，
    而不是 ai_chat_sessions.last_message_preview —— 后者存的是
    batch_engine._preview_from() 取的第一行，读它会让调用方拿到被截断的输出。

    `name` 取 batch_input_file 的 basename：该列存的是工作区相对路径
    （batch-staging/<userId>/<uploadId>/<文件名>），直接返回会泄漏内部 userId。
    """
    sql = """
        SELECT s.batch_input_file, s.status, s.error_message,
               (SELECT m.content FROM ai_chat_messages m
                 WHERE m.session_id = s.id AND m.role = 'assistant'
                 ORDER BY m.created_at DESC LIMIT 1) AS content
          FROM ai_chat_sessions s
         WHERE s.batch_id = %s
         ORDER BY s.batch_seq
    """
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (batch_id,))
            rows = [dict(r) for r in cur.fetchall()]

    results = []
    for r in rows:
        raw = r.get('batch_input_file') or ''
        name = raw.replace('\\', '/').rsplit('/', 1)[-1]
        results.append({
            'name': name,
            'status': r['status'],
            'output': _text_from_content(r.get('content')),
            'error': r.get('error_message'),
        })
    return results


def _text_from_content(content) -> str | None:
    """把 ai_chat_messages.content（typed parts 数组）里的 text 片段拼成纯文本。

    工具调用等非 text 片段一律丢弃 —— 对外只承诺「AI 的最终回复文本」，
    不暴露 OpenCode 的消息结构与 MCP 工具名。
    """
    if not content or not isinstance(content, list):
        return None
    texts = [p.get('text') for p in content
             if isinstance(p, dict) and p.get('type') == 'text' and p.get('text')]
    return '\n'.join(texts) if texts else None
```

- [ ] **Step 6: 跑测试确认通过 + 全量回归**

```bash
cd /e/wsl/check/check-manage/server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_batch_repo_api_scope.py -v
cd /e/wsl/check/check-manage/server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/ -q
```
预期：新测试 11 passed；全量 1450 + 11 = 1461。**既有的 batch 相关测试必须仍全绿** —— 这验证了「不传 `api_key_id` 时行为完全不变」。若有红，说明默认值没做对。

- [ ] **Step 7: 提交**

```bash
git add server/utils/batch_repo.py server/tests/test_batch_repo_api_scope.py
git commit -m "feat(open-api): batch_repo 支持按密钥圈定范围，新增结果读取"
```

---

### Task 3: 上传端点 + 路径校验 + 孤儿清理

**Files:**
- Create: `server/routes/open_api_batches.py`
- Modify: `server/app.py`（注册蓝图）
- Test: `server/tests/test_open_api_batches_upload.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `g.api_key_info['ownerUserId']`
- Produces:
  - 蓝图 `open_api_batches_bp`（`url_prefix='/api/v1/ai-batches'`）
  - 装饰器 `require_bound_key`（挡住 `ownerUserId` 为 NULL 的密钥）
  - `_validate_staged_path(path: str, owner_user_id: str) -> bool`
  - `POST /api/v1/ai-batches/uploads`

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_open_api_batches_upload.py`：

```python
import io
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

UPLOAD_URL = '/api/v1/ai-batches/uploads'


def _key_info(owner='user-42'):
    return {'id': 'ak-1', 'name': '集成密钥', 'ownerUserId': owner}


def test_missing_api_key_is_401(client):
    resp = client.post(UPLOAD_URL)
    assert resp.status_code == 401


def test_key_without_owner_is_403(client):
    """存量密钥（owner_user_id 为 NULL）不能用 AI 批任务接口。"""
    with patch('routes.open_api_batches._current_key', return_value=_key_info(None)):
        resp = client.post(UPLOAD_URL, headers={'X-API-Key': 'cm_x'})
    assert resp.status_code == 403
    assert '未绑定用户' in resp.get_json()['error']


def test_no_files_is_400(client, tmp_path):
    with patch('routes.open_api_batches._current_key', return_value=_key_info()), \
         patch('routes.open_api_batches._workspace_root', return_value=str(tmp_path)):
        resp = client.post(UPLOAD_URL, headers={'X-API-Key': 'cm_x'},
                           content_type='multipart/form-data', data={})
    assert resp.status_code == 400


def test_uploads_multiple_files_and_returns_paths(client, tmp_path):
    data = {'files': [(io.BytesIO(b'a' * 10), '订单A.pdf'),
                      (io.BytesIO(b'b' * 10), '订单B.pdf')]}
    with patch('routes.open_api_batches._current_key', return_value=_key_info()), \
         patch('routes.open_api_batches._workspace_root', return_value=str(tmp_path)):
        resp = client.post(UPLOAD_URL, headers={'X-API-Key': 'cm_x'},
                           content_type='multipart/form-data', data=data)
    assert resp.status_code == 201
    files = resp.get_json()['files']
    assert [f['name'] for f in files] == ['订单A.pdf', '订单B.pdf']
    for f in files:
        assert f['path'].startswith('batch-staging/user-42/')


def test_single_file_over_limit_is_400(client, tmp_path):
    from routes.open_api_batches import MAX_FILE_BYTES
    big = io.BytesIO(b'x' * (MAX_FILE_BYTES + 1))
    with patch('routes.open_api_batches._current_key', return_value=_key_info()), \
         patch('routes.open_api_batches._workspace_root', return_value=str(tmp_path)):
        resp = client.post(UPLOAD_URL, headers={'X-API-Key': 'cm_x'},
                           content_type='multipart/form-data',
                           data={'files': (big, 'big.bin')})
    assert resp.status_code == 400
    assert '20' in resp.get_json()['error']


# ---------- 路径校验（这是对外后唯一新增的横向越权面）----------

def test_validate_staged_path_accepts_own_dir():
    from routes.open_api_batches import _validate_staged_path
    assert _validate_staged_path('batch-staging/user-42/abc/a.pdf', 'user-42') is True


def test_validate_staged_path_rejects_other_user():
    from routes.open_api_batches import _validate_staged_path
    assert _validate_staged_path('batch-staging/user-99/abc/a.pdf', 'user-42') is False


def test_validate_staged_path_rejects_traversal():
    from routes.open_api_batches import _validate_staged_path
    for bad in ['batch-staging/user-42/../user-99/a.pdf',
                'batch-staging/user-42/abc/../../user-99/a.pdf',
                '../../../etc/passwd',
                '/etc/passwd']:
        assert _validate_staged_path(bad, 'user-42') is False, bad


def test_validate_staged_path_rejects_prefix_lookalike():
    """user-4 不能借 user-42 的前缀混进来，反之亦然。"""
    from routes.open_api_batches import _validate_staged_path
    assert _validate_staged_path('batch-staging/user-420/abc/a.pdf', 'user-42') is False


def test_validate_staged_path_rejects_backslash_traversal():
    """Windows 分隔符也要挡住。"""
    from routes.open_api_batches import _validate_staged_path
    assert _validate_staged_path('batch-staging\\user-99\\a.pdf', 'user-42') is False
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /e/wsl/check/check-manage/server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_open_api_batches_upload.py -v
```
预期：全部 FAIL（模块不存在 / 路由 404）。

- [ ] **Step 3: 新建蓝图与上传端点**

创建 `server/routes/open_api_batches.py`：

```python
"""AI 批任务对外 API（/api/v1/ai-batches）。

这是一层**对外契约层**：只调 batch_repo / batch_engine / workspace 的底层函数，
刻意不复用内部 routes/ai_chat_batches.py 的处理器。原因是内部返回体是给前端用的
（含 opencode_session_id / workspace_path 等实现细节），一旦对外就变成公开承诺，
以后为前端改字段会破坏外部集成。这里只吐出约定好的字段。
"""

import os
import time
import uuid
from pathlib import PurePosixPath

from flask import Blueprint, g, jsonify, request

from auth import api_key_required
from utils.workspace import batch_staging_dir, safe_filename, WorkspacePathError

open_api_batches_bp = Blueprint('open_api_batches', __name__,
                                url_prefix='/api/v1/ai-batches')

MAX_FILE_BYTES = 20 * 1024 * 1024          # 单个文件 20 MB
MAX_UPLOAD_TOTAL_BYTES = 100 * 1024 * 1024  # 单次上传总计 100 MB
STAGING_TTL_SECONDS = 24 * 3600            # 暂存目录保留 24 小时


def _current_key() -> dict:
    """当前请求的密钥信息。抽成函数便于测试打桩。"""
    return getattr(g, 'api_key_info', {}) or {}


def _workspace_root() -> str:
    return os.environ.get('AI_CHAT_WORKSPACE_ROOT', 'ai-workspaces')


def require_bound_key(f):
    """挡住未绑定用户的密钥。

    存量密钥的 owner_user_id 是 NULL。放行它们会导致外部调用以某个人的名义
    跑 AI、烧他的额度、把任务塞进他的侧边栏 —— 所以一律拒绝，要求重建密钥。
    """
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if not _current_key().get('ownerUserId'):
            return jsonify({'error': '该密钥未绑定用户，请在密钥管理中重新创建'}), 403
        return f(*args, **kwargs)
    return decorated


def _validate_staged_path(path: str, owner_user_id: str) -> bool:
    """校验 files[].path 确实落在本密钥属主的暂存目录下。

    内部创建接口不做这个校验（它在 JWT 之后，路径由前端从上传响应原样带回）。
    对外必须做：否则调用方可以构造 batch-staging/<别人的userId>/... 或用 ..
    往上跳，把别人工作区的文件塞进自己的批任务让 AI 读出来。
    """
    if not path or not owner_user_id:
        return False
    normalized = str(path).replace('\\', '/')
    if normalized.startswith('/'):
        return False
    parts = PurePosixPath(normalized).parts
    if any(p == '..' for p in parts):
        return False
    if len(parts) < 3:
        return False
    return parts[0] == 'batch-staging' and parts[1] == owner_user_id


def _sweep_stale_staging(root: str, owner_user_id: str) -> None:
    """清理该属主下超过 TTL 未被使用的暂存目录。

    上传后从不创建批任务的话，暂存目录会永久堆积。对外后任何持 key 的人都能
    无限制刷，所以在上传时顺带清 —— 零新增组件，且触发频率与滥用程度天然正相关。
    已被 create_batch 消费掉的目录本就不在了，不受影响。
    """
    import shutil
    base = os.path.join(root, 'batch-staging', owner_user_id)
    if not os.path.isdir(base):
        return
    cutoff = time.time() - STAGING_TTL_SECONDS
    for entry in os.scandir(base):
        try:
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry.path, ignore_errors=True)
        except OSError:
            pass


@open_api_batches_bp.post('/uploads')
@api_key_required
@require_bound_key
def upload_files():
    owner = _current_key()['ownerUserId']
    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': '请通过 files 字段上传至少一个文件'}), 400

    root = _workspace_root()
    _sweep_stale_staging(root, owner)

    upload_session_id = uuid.uuid4().hex[:16]
    try:
        staging = batch_staging_dir(root, owner, upload_session_id)
    except WorkspacePathError as e:
        return jsonify({'error': str(e)}), 400

    total = 0
    saved = []
    for f in files:
        name = safe_filename(f.filename or '')
        if not name:
            return jsonify({'error': '文件名无效'}), 400
        blob = f.read()
        if len(blob) > MAX_FILE_BYTES:
            return jsonify({
                'error': f'文件「{name}」超过单个文件 '
                         f'{MAX_FILE_BYTES // 1024 // 1024} MB 的上限'
            }), 400
        total += len(blob)
        if total > MAX_UPLOAD_TOTAL_BYTES:
            return jsonify({
                'error': f'单次上传总大小超过 '
                         f'{MAX_UPLOAD_TOTAL_BYTES // 1024 // 1024} MB 的上限'
            }), 400
        dest = staging / name
        dest.write_bytes(blob)
        saved.append({'name': name,
                      'path': dest.relative_to(root).as_posix()})

    return jsonify({'files': saved}), 201
```

> **超限时的落盘问题**：上面的写法是「边读边判边写」，超限时前面的文件已经落盘了。这可以接受 —— 那个暂存目录不会被任何批任务引用，24 小时后由 `_sweep_stale_staging` 清掉。**不要**为了「不落盘」把所有文件先读进内存再判断，那样 100 MB 会直接进内存，比落盘更糟。

- [ ] **Step 4: 注册蓝图**

在 `server/app.py` 中：
- import 区（在 `from routes.open_api import open_api_bp` 附近）加
  ```python
  from routes.open_api_batches import open_api_batches_bp
  ```
- 注册区（在 `app.register_blueprint(open_api_bp)` 之后、**且必须在 `app.register_blueprint(dynamic_bp)` 之前**）加
  ```python
  app.register_blueprint(open_api_batches_bp)
  ```

- [ ] **Step 5: 跑测试确认通过**

```bash
cd /e/wsl/check/check-manage/server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_open_api_batches_upload.py -v
```
预期：10 passed。

> `safe_filename` 与 `WorkspacePathError` 是否真的从 `utils.workspace` 导出，**请先 grep 确认**（`grep -n "def safe_filename\|class WorkspacePathError" server/utils/workspace.py`）。名字对不上就按实际的改 import。

- [ ] **Step 6: 提交**

```bash
git add server/routes/open_api_batches.py server/app.py server/tests/test_open_api_batches_upload.py
git commit -m "feat(open-api): AI 批任务上传端点，含路径归属校验与暂存清理"
```

---

### Task 4: 创建 / 列表 / 详情端点

**Files:**
- Modify: `server/routes/open_api_batches.py`
- Test: `server/tests/test_open_api_batches_crud.py`（新建）

**Interfaces:**
- Consumes: Task 2 的 `create_batch(..., api_key_id=)` / `list_batches(..., api_key_id=)` / `get_batch_detail(..., api_key_id=)`；Task 3 的 `require_bound_key` / `_validate_staged_path` / `_current_key`
- Produces: `POST /`、`GET /`、`GET /<batch_id>`

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_open_api_batches_crud.py`：

```python
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = '/api/v1/ai-batches'
HDR = {'X-API-Key': 'cm_x'}


def _key(owner='user-42'):
    return {'id': 'ak-1', 'name': 'k', 'ownerUserId': owner}


def _ok_file():
    return {'name': 'a.pdf', 'path': 'batch-staging/user-42/abc/a.pdf'}


def test_create_requires_name_and_prompt(client):
    with patch('routes.open_api_batches._current_key', return_value=_key()):
        resp = client.post(BASE, headers=HDR, json={'files': [_ok_file()]})
    assert resp.status_code == 400


def test_create_rejects_empty_files(client):
    with patch('routes.open_api_batches._current_key', return_value=_key()):
        resp = client.post(BASE, headers=HDR, json={'name': 'n', 'prompt': 'p', 'files': []})
    assert resp.status_code == 400


def test_create_rejects_foreign_path(client):
    """核心安全断言：不能引用别人暂存目录里的文件。"""
    bad = {'name': 'a.pdf', 'path': 'batch-staging/user-99/abc/a.pdf'}
    with patch('routes.open_api_batches._current_key', return_value=_key()):
        resp = client.post(BASE, headers=HDR,
                           json={'name': 'n', 'prompt': 'p', 'files': [bad]})
    assert resp.status_code == 400
    assert '文件路径无效' in resp.get_json()['error']


def test_create_rejects_too_many_files(client):
    from utils.batch_repo import MAX_FILES_PER_BATCH
    files = [_ok_file() for _ in range(MAX_FILES_PER_BATCH + 1)]
    with patch('routes.open_api_batches._current_key', return_value=_key()):
        resp = client.post(BASE, headers=HDR,
                           json={'name': 'n', 'prompt': 'p', 'files': files})
    assert resp.status_code == 400
    assert str(MAX_FILES_PER_BATCH) in resp.get_json()['error']


def test_create_rejects_overlong_prompt(client):
    from routes.open_api_batches import MAX_PROMPT_CHARS
    with patch('routes.open_api_batches._current_key', return_value=_key()):
        resp = client.post(BASE, headers=HDR,
                           json={'name': 'n', 'prompt': 'x' * (MAX_PROMPT_CHARS + 1),
                                 'files': [_ok_file()]})
    assert resp.status_code == 400


def test_create_passes_api_key_id_and_owner(client):
    created = {'batch': {'id': 'b-1', 'status': 'pending', 'total': 1}, 'sessions': []}
    with patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.create_batch', return_value=created) as cb, \
         patch('routes.open_api_batches.get_worker'):
        resp = client.post(BASE, headers=HDR,
                           json={'name': 'n', 'prompt': 'p', 'files': [_ok_file()],
                                 'agent': 'build', 'model': 'm'})
    assert resp.status_code == 201
    assert resp.get_json() == {'batchId': 'b-1', 'status': 'pending', 'total': 1}
    assert cb.call_args[0][0] == 'user-42'          # 归属用户
    assert cb.call_args[1]['api_key_id'] == 'ak-1'  # 来源密钥
    assert cb.call_args[1]['agent'] == 'build'


def test_list_is_scoped_to_key(client):
    with patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.list_batches',
               return_value={'items': [], 'total': 0}) as lb:
        resp = client.get(BASE, headers=HDR)
    assert resp.status_code == 200
    assert lb.call_args[1]['api_key_id'] == 'ak-1'


def test_list_page_size_capped_at_100(client):
    with patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.list_batches',
               return_value={'items': [], 'total': 0}) as lb:
        client.get(f'{BASE}?pageSize=9999', headers=HDR)
    assert lb.call_args[1]['page_size'] == 100


def test_detail_not_found_is_404(client):
    with patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail', return_value=None):
        resp = client.get(f'{BASE}/b-nope', headers=HDR)
    assert resp.status_code == 404


def test_detail_returns_contract_fields_only(client):
    """不能把 opencode_session_id / workspace_path 这类内部字段吐出去。"""
    detail = {'batch': {'id': 'b-1', 'name': 'n', 'status': 'running',
                        'total': 3, 'done': 1, 'failed': 0,
                        'agent': 'build', 'model': None,
                        'created_at': None, 'completed_at': None,
                        'user_id': 'user-42', 'api_key_id': 'ak-1',
                        'prompt': '内部 prompt'},
              'sessions': [{'opencode_session_id': 'oc-1'}]}
    with patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail', return_value=detail):
        resp = client.get(f'{BASE}/b-1', headers=HDR)
    body = resp.get_json()
    assert set(body) == {'batchId', 'name', 'status', 'total', 'done', 'failed',
                         'agent', 'model', 'createdAt', 'completedAt'}
    assert 'opencode_session_id' not in resp.get_data(as_text=True)
    assert 'user_id' not in resp.get_data(as_text=True)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /e/wsl/check/check-manage/server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_open_api_batches_crud.py -v
```
预期：全部 FAIL（路由不存在）。

- [ ] **Step 3: 实现三个端点**

在 `server/routes/open_api_batches.py` 顶部补 import：
```python
from utils.batch_repo import (MAX_FILES_PER_BATCH, create_batch, get_batch_detail,
                              list_batches)
from utils.batch_engine import get_worker
```
加常量：
```python
MAX_PROMPT_CHARS = 20000
```

追加端点：

```python
def _batch_out(b: dict) -> dict:
    """内部批任务行 → 对外契约字段。刻意只吐这几个，内部字段一律不外泄。"""
    return {
        'batchId': b['id'],
        'name': b.get('name'),
        'status': b.get('status'),
        'total': b.get('total'),
        'done': b.get('done'),
        'failed': b.get('failed'),
        'agent': b.get('agent'),
        'model': b.get('model'),
        'createdAt': b['created_at'].isoformat() if b.get('created_at') else None,
        'completedAt': b['completed_at'].isoformat() if b.get('completed_at') else None,
    }


@open_api_batches_bp.post('')
@api_key_required
@require_bound_key
def create():
    key = _current_key()
    owner = key['ownerUserId']
    body = request.get_json(silent=True) or {}

    name = (body.get('name') or '').strip()
    prompt = (body.get('prompt') or '').strip()
    files = body.get('files') or []

    if not name or not prompt:
        return jsonify({'error': 'name 与 prompt 均为必填'}), 400
    if len(prompt) > MAX_PROMPT_CHARS:
        return jsonify({'error': f'prompt 超过 {MAX_PROMPT_CHARS} 字符的上限'}), 400
    if not isinstance(files, list) or not files:
        return jsonify({'error': '请至少提供一个文件'}), 400
    if len(files) > MAX_FILES_PER_BATCH:
        return jsonify({'error': f'单批最多 {MAX_FILES_PER_BATCH} 个文件'}), 400

    for f in files:
        if not isinstance(f, dict) or not f.get('name') or not f.get('path'):
            return jsonify({'error': '每个文件需包含 name 与 path'}), 400
        if not _validate_staged_path(f['path'], owner):
            return jsonify({'error': '文件路径无效'}), 400

    result = create_batch(
        owner,
        name=name, prompt=prompt, template_id=None, files=files,
        agent=(body.get('agent') or '').strip() or None,
        model=(body.get('model') or '').strip() or None,
        api_key_id=key['id'],
    )
    get_worker().notify()
    b = result['batch']
    return jsonify({'batchId': b['id'], 'status': b['status'], 'total': b['total']}), 201


@open_api_batches_bp.get('')
@api_key_required
@require_bound_key
def list_():
    key = _current_key()
    try:
        page = max(1, int(request.args.get('page', 1)))
        page_size = min(max(1, int(request.args.get('pageSize', 20))), 100)
    except (TypeError, ValueError):
        return jsonify({'error': 'page 与 pageSize 必须是整数'}), 400

    data = list_batches(key['ownerUserId'], page=page, page_size=page_size,
                        api_key_id=key['id'])
    return jsonify({'items': [_batch_out(b) for b in data['items']],
                    'total': data['total']})


@open_api_batches_bp.get('/<batch_id>')
@api_key_required
@require_bound_key
def detail(batch_id):
    key = _current_key()
    d = get_batch_detail(key['ownerUserId'], batch_id, api_key_id=key['id'])
    if not d:
        return jsonify({'error': '批任务不存在'}), 404
    return jsonify(_batch_out(d['batch']))
```

- [ ] **Step 4: 跑测试 + 全量回归**

```bash
cd /e/wsl/check/check-manage/server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_open_api_batches_crud.py -v
cd /e/wsl/check/check-manage/server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/ -q
```
预期：新测试 10 passed；全量在 Task 3 之后的数字上 +10。

- [ ] **Step 5: 提交**

```bash
git add server/routes/open_api_batches.py server/tests/test_open_api_batches_crud.py
git commit -m "feat(open-api): AI 批任务创建/列表/详情端点"
```

---

### Task 5: 结果 / 删除 / 重试端点

**Files:**
- Modify: `server/routes/open_api_batches.py`
- Test: `server/tests/test_open_api_batches_results.py`（新建）

**Interfaces:**
- Consumes: Task 2 的 `get_batch_results(batch_id)` / `delete_batch(..., api_key_id=)` / `reset_failed_to_pending(..., api_key_id=)`；Task 3/4 的既有辅助
- Produces: `GET /<batch_id>/results`、`DELETE /<batch_id>`、`POST /<batch_id>/retry-failed`

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_open_api_batches_results.py`：

```python
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = '/api/v1/ai-batches'
HDR = {'X-API-Key': 'cm_x'}


def _key(owner='user-42'):
    return {'id': 'ak-1', 'name': 'k', 'ownerUserId': owner}


def _detail(status='completed'):
    return {'batch': {'id': 'b-1', 'name': 'n', 'status': status, 'total': 1,
                      'done': 1, 'failed': 0, 'agent': None, 'model': None,
                      'created_at': None, 'completed_at': None},
            'sessions': []}


# ---------- results ----------

def test_results_404_when_not_owned(client):
    with patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail', return_value=None):
        resp = client.get(f'{BASE}/b-1/results', headers=HDR)
    assert resp.status_code == 404


def test_results_returns_per_child_contract(client):
    rows = [{'name': 'a.pdf', 'status': 'completed', 'output': '完整\n文本', 'error': None},
            {'name': 'b.pdf', 'status': 'failed', 'output': None, 'error': '超时'}]
    with patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail', return_value=_detail()), \
         patch('routes.open_api_batches.get_batch_results', return_value=rows):
        resp = client.get(f'{BASE}/b-1/results', headers=HDR)
    body = resp.get_json()
    assert body['batchId'] == 'b-1'
    assert body['status'] == 'completed'
    assert body['results'] == rows


# ---------- delete ----------

def test_delete_404_when_not_owned(client):
    with patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.delete_batch', return_value=False):
        resp = client.delete(f'{BASE}/b-1', headers=HDR)
    assert resp.status_code == 404


def test_delete_passes_api_key_id(client):
    with patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.delete_batch', return_value=True) as db:
        resp = client.delete(f'{BASE}/b-1', headers=HDR)
    assert resp.status_code == 200
    assert db.call_args[1]['api_key_id'] == 'ak-1'


# ---------- retry-failed ----------

def test_retry_404_when_not_owned(client):
    with patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail', return_value=None):
        resp = client.post(f'{BASE}/b-1/retry-failed', headers=HDR)
    assert resp.status_code == 404


def test_retry_409_when_still_running(client):
    for st in ('pending', 'running'):
        with patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=_detail(st)):
            resp = client.post(f'{BASE}/b-1/retry-failed', headers=HDR)
        assert resp.status_code == 409, st
        assert '仍在执行' in resp.get_json()['error']


def test_retry_ok_on_terminal_states(client):
    for st in ('completed', 'partial', 'failed'):
        with patch('routes.open_api_batches._current_key', return_value=_key()), \
             patch('routes.open_api_batches.get_batch_detail', return_value=_detail(st)), \
             patch('routes.open_api_batches.reset_failed_to_pending', return_value=2), \
             patch('routes.open_api_batches.get_worker'):
            resp = client.post(f'{BASE}/b-1/retry-failed', headers=HDR)
        assert resp.status_code == 200, st
        assert resp.get_json() == {'retried': 2}


def test_retry_zero_is_200_not_error(client):
    """终态但没有可重试的子任务 → 200 + retried 0，不报错。"""
    with patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail', return_value=_detail('completed')), \
         patch('routes.open_api_batches.reset_failed_to_pending', return_value=0), \
         patch('routes.open_api_batches.get_worker'):
        resp = client.post(f'{BASE}/b-1/retry-failed', headers=HDR)
    assert resp.status_code == 200
    assert resp.get_json() == {'retried': 0}
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /e/wsl/check/check-manage/server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_open_api_batches_results.py -v
```
预期：全部 FAIL。

- [ ] **Step 3: 实现三个端点**

顶部 import 补上：
```python
from utils.batch_repo import delete_batch, get_batch_results, reset_failed_to_pending
```
加常量：
```python
TERMINAL_STATUSES = ('completed', 'partial', 'failed')
```

追加：

```python
@open_api_batches_bp.get('/<batch_id>/results')
@api_key_required
@require_bound_key
def results(batch_id):
    key = _current_key()
    d = get_batch_detail(key['ownerUserId'], batch_id, api_key_id=key['id'])
    if not d:
        return jsonify({'error': '批任务不存在'}), 404
    return jsonify({
        'batchId': batch_id,
        'status': d['batch']['status'],
        'results': get_batch_results(batch_id),
    })


@open_api_batches_bp.delete('/<batch_id>')
@api_key_required
@require_bound_key
def remove(batch_id):
    key = _current_key()
    ok = delete_batch(key['ownerUserId'], batch_id, api_key_id=key['id'])
    if not ok:
        return jsonify({'error': '批任务不存在'}), 404
    return jsonify({'deleted': True})


@open_api_batches_bp.post('/<batch_id>/retry-failed')
@api_key_required
@require_bound_key
def retry_failed(batch_id):
    key = _current_key()
    d = get_batch_detail(key['ownerUserId'], batch_id, api_key_id=key['id'])
    if not d:
        return jsonify({'error': '批任务不存在'}), 404
    if d['batch']['status'] not in TERMINAL_STATUSES:
        return jsonify({'error': '该批任务仍在执行中'}), 409

    n = reset_failed_to_pending(key['ownerUserId'], batch_id, api_key_id=key['id'])
    if n:
        get_worker().notify()
    return jsonify({'retried': n})
```

> **删除时的工作区清理**：`batch_repo.delete_batch` 的 docstring 明确写着「Callers MUST run per-session workspace cleanup BEFORE invoking this」。请去读 `server/routes/ai_chat_batches.py` 的 `remove` 处理器，看它是怎么做工作区清理的，**在这里照做同样的清理**（否则外部删除会留下孤儿工作区目录）。如果那边的清理逻辑可以直接复用为一个函数，抽出来两边共用；如果内联在路由里，在这里重复一小段并加注释说明来源。

- [ ] **Step 4: 跑测试 + 全量回归**

```bash
cd /e/wsl/check/check-manage/server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_open_api_batches_results.py -v
cd /e/wsl/check/check-manage/server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/ -q
```
预期：新测试 9 passed；全量在 Task 4 之后的数字上 +9。

- [ ] **Step 5: 提交**

```bash
git add server/routes/open_api_batches.py server/tests/test_open_api_batches_results.py
git commit -m "feat(open-api): AI 批任务结果/删除/重试端点"
```

---

### Task 6: proxy.py 验证 + 端到端实跑

**Files:**
- 无代码改动（除非发现缺陷）

**Interfaces:**
- Consumes: 全部前置任务
- Produces: 可写进 PR 的验证结论

**这是 CLAUDE.md 的强制要求**：新增了 `/api` 路由形状，必须过一遍 `proxy.py`（`:8080`），不能只在开发环境验。

- [ ] **Step 1: 起服务**

```bash
netstat -ano | grep -E ":(5173|3002|8080|3001) "
```
有残留占用就**按 PID 精确 kill**（绝不批量杀 node/python）。然后：
```bash
cd /e/wsl/check/check-manage && npm run build
cd /e/wsl/check/check-manage/server && python proxy.py
```
`proxy.py` 会自己起后端在 `:3001` 并在 `:8080` 提供服务。

- [ ] **Step 2: 造一个绑定用户的密钥**

用 admin 登录界面（`admin` / `admin123`）在密钥管理页新建一个密钥，**记下明文 key**（只显示一次）。然后查库确认 `owner_user_id` 被写进去了：

```bash
cd /e/wsl/check/check-manage/server && python -c "
from db import get_db
with get_db() as c:
    cur=c.cursor()
    cur.execute(\"SELECT id,name,owner_user_id FROM api_keys ORDER BY created_at DESC LIMIT 3\")
    print(cur.fetchall())
"
```
预期最新一条的 `owner_user_id` 非 NULL。

- [ ] **Step 3: 经 `:8080` 走完整流程**

```bash
KEY=<上一步的明文 key>

# 1) 上传两个文件（重点验 multipart 经 proxy.py 转发）
curl -s -X POST http://localhost:8080/api/v1/ai-batches/uploads \
  -H "X-API-Key: $KEY" \
  -F "files=@/some/doc1.txt" -F "files=@/some/doc2.txt"

# 2) 用返回的 files 创建批任务
curl -s -X POST http://localhost:8080/api/v1/ai-batches \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"name":"API 冒烟","prompt":"用一句话总结这个文件","files":[...]}'

# 3) 轮询状态
curl -s http://localhost:8080/api/v1/ai-batches/<batchId> -H "X-API-Key: $KEY"

# 4) 取结果
curl -s http://localhost:8080/api/v1/ai-batches/<batchId>/results -H "X-API-Key: $KEY"
```

断言：
- 上传返回 201 且 `path` 以 `batch-staging/<你的userId>/` 开头
- 创建返回 201 + `batchId`
- **`X-API-Key` 没被 `proxy.py` 丢掉**（不是 401）
- 路由被正确转发（不是 404）

- [ ] **Step 4: 大文件 multipart 专项验证**

这是本次唯一的新风险点 —— `proxy.py` 的 `_proxy_to_backend` 是按 `Content-Length` 转发 body 的，大 multipart 可能被截断或缓冲爆内存。

造一个约 15 MB 的文件（低于 20 MB 上限）经 `:8080` 上传，断言：
- 返回 201
- 落盘文件大小与源文件**一致**（`ls -l` 比对，不能只看返回码）

再造一个 25 MB 的（超过上限），断言返回 400 且错误信息含上限值。

- [ ] **Step 5: 越权与隔离的实跑验证**

- 用 **另一个密钥** 查第一个密钥创建的 batchId → 预期 404
- 在 UI 里手工建一个批任务，用 API 查它的 id → 预期 404
- 构造一个指向别的 userId 的 `path` 调创建接口 → 预期 400「文件路径无效」

- [ ] **Step 6: 停服务**

`netstat -ano | grep -E ":(8080|3001) "` 拿 PID，**按 PID 精确 kill**。

- [ ] **Step 7: 写验证报告并提交（若有截图/输出留档）**

把每一步的实际输出记进任务报告。**任何未能验证的部分要明确写明原因**，不要模糊处理。

---

### Task 7: 调用文档

**Files:**
- Create: `docs/user-guide/integration/ai-batch-api.md`
- Modify: `docs/user-guide/README.md`、`docs/user-guide/integration/open-api.md`、`CLAUDE.md`

**Interfaces:**
- Consumes: 全部前置任务
- Produces: 集成方可直接照做的调用文档

- [ ] **Step 1: 写主文档**

创建 `docs/user-guide/integration/ai-batch-api.md`。先读 `docs/user-guide/integration/open-api.md` 对齐风格（中文、任务导向、含完整 curl）。必须覆盖：

1. **这是什么** —— 提交一批文件 + 一个 prompt，平台为每个文件开一个隔离的 AI 会话处理，你轮询取回每个文件的处理结果。
2. **前提：密钥必须绑定用户** —— 在密钥管理页**新建**密钥（本功能上线前创建的存量密钥没有绑定关系，调用会返回 403，需要重新创建）。
3. **完整流程**（一段可直接跑的 curl 序列）：上传 → 创建 → 轮询 → 取结果。
4. **端点参考**：7 个端点各自的方法、路径、请求体、响应体示例。
5. **状态说明**：`pending / running / completed / partial / failed` 各自含义，以及子任务级的 `status`。
6. **限制表**：单批 50 文件、单文件 20 MB、单次上传 100 MB、prompt 20000 字符、`pageSize` ≤ 100。
7. **错误码表**：401 / 403 / 400 / 404 / 409 各自的触发条件。
8. **隔离说明** —— 每个密钥只能看到和操作自己创建的批任务；界面里手工建的批任务对 API 不可见。
9. **注意事项** —— 上传后 24 小时内未创建批任务的暂存文件会被清理；批任务是异步的，`completed` 之前 `output` 为 `null`；删除不可逆。

- [ ] **Step 2: 接进索引与相关文档**

- `docs/user-guide/README.md` 的 `integration/` 分组加一行指向 `ai-batch-api.md`（照现有条目格式）。
- `docs/user-guide/integration/open-api.md` 补一句：`/api/v1` 下除了数据集合接口，还有 AI 批任务接口，见 `ai-batch-api.md`。

- [ ] **Step 3: 更新 CLAUDE.md**

在 "AI Agent Chat — Batch Tasks (M1.5)" 一节末尾追加：

```markdown
**对外 API**：批任务能力另有一套 API Key 鉴权的对外接口 `/api/v1/ai-batches/*`（`server/routes/open_api_batches.py`），刻意**不复用**内部 `ai_chat_batches.py` 的处理器 —— 内部返回体含 `opencode_session_id`/`workspace_path` 等实现细节，一旦对外就成了公开契约。对外层只吐约定字段（`batchId`/`status`/`total`/`done`/`failed`/`agent`/`model`/时间戳；结果为 `{name, status, output, error}`，`output` 从 `ai_chat_messages` 取最后一条 assistant 消息的全部 text 片段拼接，**不是** `last_message_preview`（那只存第一行））。隔离模型：`api_keys.owner_user_id` 绑定密钥创建者、`ai_chat_batches.api_key_id` 记录来源密钥，查/删/重试一律带 `api_key_id` 条件，命中不到返回 404 而非 403；UI 建的批任务 `api_key_id IS NULL`，对 API 永远不可见。存量密钥 `owner_user_id` 为 NULL，被 `require_bound_key` 挡在该接口之外（防止外部调用以某个人的名义跑 AI）。`files[].path` 必须经 `_validate_staged_path` 校验落在本密钥属主的 `batch-staging/<ownerUserId>/` 下 —— 内部接口不做这个校验（JWT 后天然受限），对外不补就是横向越权。用户文档：`docs/user-guide/integration/ai-batch-api.md`。
```

- [ ] **Step 4: 提交**

```bash
git add docs/ CLAUDE.md
git commit -m "docs(open-api): 新增 AI 批任务对外 API 调用文档"
```

---

## 自查记录

**Spec 覆盖检查**：设计文档 §3.1 DDL → Task 1；§3.2 鉴权扩展 → Task 1；§3.3 隔离 → Task 2（repo 层过滤）+ Task 4/5（路由传参）；§3.4 密钥管理页 → Task 1 Step 6；§4.1 上传 → Task 3；§4.2-4.4 创建/列表/详情 → Task 4；§4.5-4.7 结果/删除/重试 → Task 5；§5 路径校验 → Task 3（函数与测试）+ Task 4（创建时调用）；§6 限制 → Task 3（大小）+ Task 4（数量/prompt/pageSize）；§7 错误处理 → Task 3/4/5 的测试逐条覆盖；§8 实现边界 → Task 2/3；§8.1 孤儿清理 → Task 3；§9 测试与验证 → 各任务内嵌 + Task 6；§10 文档 → Task 7。无遗漏。

**需在实现时对齐的实际情况**（计划中已逐处标注，不是占位符）：
- `server/auth.py` 的 `get_db` 是模块级还是函数内局部 import（Task 1 Step 3 的 mock 方式取决于此）
- `utils/workspace.py` 是否真的导出 `safe_filename` 与 `WorkspacePathError`（Task 3 Step 5）
- `routes/ai_chat_batches.py` 的 `remove` 处理器怎么做工作区清理（Task 5 Step 3 要照做）

**类型一致性**：`api_key_id` 在 Task 2 定义为 `str | None = None` 关键字参数，Task 4/5 一律以 `api_key_id=key['id']` 传入，一致。`get_batch_results(batch_id)` 在 Task 2 定义为只收 batch_id（隔离已由调用方先做的 `get_batch_detail` 保证），Task 5 据此调用，一致。`_current_key()` / `_validate_staged_path()` / `require_bound_key` 在 Task 3 定义，Task 4/5 消费，签名一致。
