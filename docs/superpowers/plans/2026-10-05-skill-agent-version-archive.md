# Skill/Agent 定义版本归档与对比 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 `ai_skill_def_versions` 归档定义正文（SKILL.md / agent md），提供版本内容预览、相邻版本 unified diff、前滚式回滚，并在 steps 回写时自动登记带默认 label 的版本。

**Architecture:** 版本实体表加 `content`/`content_captured_at` 两列；统一注册函数 `register_def_version`（后到补齐正文、已有正文不覆盖）被三条路径共用——拟合登记、steps 回写主动归档、manifest 扫描被动兜底。三个新 admin 端点（content/compare/rollback）+ 时间线 UI 三个动作。回滚按 bytes 精确写回，sha256 复原命中同一版本行（不产生虚假回滚版本）。

**Tech Stack:** Flask + psycopg2（真实 dev PG 测试）、difflib（标准库，服务端出 diff）、Vue3 + Element Plus + TS。

**Spec:** `docs/superpowers/specs/2026-10-05-skill-agent-version-archive-design.md`（执行者须同读）

## Global Constraints

- 服务端测试跑真实开发 PG（非 mock），从 `server/` 目录执行；命令统一带 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`。
- 种子行用 uuid 后缀隔离命名，`finally` 中 DELETE 清理（仓库种子行清理约定）。
- 所有新端点挂 `ai_execution_admin_bp`（前缀 `/ai/chat/admin`），权限 `@require_permission('admin.ai_chat_admin')`；变更动作必须 `log_operation` 留痕。
- 归档/回滚全程 **bytes 精确保留**：读 `rb`、写 `wb` + `encode('utf-8')`，保证「写回后文件 sha256 == content_hash」不变性；禁止经 text 模式转换换行符。
- 前端不引 diff 渲染依赖：unified diff 由服务端 difflib 生成，前端按行着色。
- `content_hash = ''` 的异常行不参与内容 diff/回滚。
- 后端 :3002 不自动 reload——Task 9 E2E 前须手动重启后端。
- 路由测试的 `client` fixture 沿用 `test_skill_fit_routes.py` 的真实 DB shadow 模式（conftest 的 `app` 把 get_db patch 成 mock，需 rebind）。

---

### Task 1: 迁移——def_versions 加 content / content_captured_at 列

**Files:**
- Create: `server/migrations/2026_10_05_def_version_content_archive.py`
- Create: `server/tests/test_def_version_archive.py`（本任务起建立，后续任务追加）

**Interfaces:**
- Consumes: `db.get_db`；迁移由 `db_schema/global_skills.py::_run_dated_migrations` 按文件名日期序自动执行（init_db 与 app 启动钩子共用）。
- Produces: `ai_skill_def_versions.content TEXT`、`ai_skill_def_versions.content_captured_at TIMESTAMPTZ`（幂等迁移）。

- [ ] **Step 1: 写失败测试**

创建 `server/tests/test_def_version_archive.py`：

```python
# -*- coding: utf-8 -*-
"""定义版本正文归档测试（迁移列 / register_def_version / 扫描兜底）。

db_conn 为 conftest 的真实 dev 库连接；种子行用 uuid 后缀隔离，
finally 清理（仓库种子行清理约定）。
"""
import hashlib
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _load_migration():
    """迁移文件名以数字开头，无法常规 import——按 _run_dated_migrations
    同款 importlib 方式加载。"""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..',
                        'migrations', '2026_10_05_def_version_content_archive.py')
    spec = importlib.util.spec_from_file_location('_mig_def_version_content', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_columns_exist_and_idempotent(db_conn):
    mig = _load_migration()
    mig.run()
    mig.run()  # 幂等：重复执行无异常
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'ai_skill_def_versions' "
            "AND column_name IN ('content', 'content_captured_at')")
        cols = {r[0] for r in cur.fetchall()}
    assert {'content', 'content_captured_at'} <= cols
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_def_version_archive.py -v
```

预期：FAIL——迁移文件不存在，`spec_from_file_location` 加载报错。

- [ ] **Step 3: 写迁移**

创建 `server/migrations/2026_10_05_def_version_content_archive.py`（模式对齐 `2026_10_01_skill_fit_tables.py`）：

```python
"""SkillOpt 定义版本正文归档列（2026-10-05）。

ai_skill_def_versions 增加正文与定格时间两列（spec §3）：

  content             主定义文件正文（SKILL.md / agent md 全文，NULL=未归档）
  content_captured_at 正文定格时间（与 first_seen_at「版本首见」语义分离）

全部幂等，可重复执行。历史存量行 content 为 NULL → UI 标「未归档」，
不做伪回填（正文已不存在）。

用法（在 server/ 目录下）：
    python migrations/2026_10_05_def_version_content_archive.py
也可由 init_db.py 在建库流程末尾经 _run_dated_migrations() 调用 run()。
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db

DDL = [
    "ALTER TABLE ai_skill_def_versions "
    "ADD COLUMN IF NOT EXISTS content TEXT",
    "ALTER TABLE ai_skill_def_versions "
    "ADD COLUMN IF NOT EXISTS content_captured_at TIMESTAMPTZ",
]


def run():
    with get_db() as conn:
        with conn.cursor() as cur:
            for stmt in DDL:
                cur.execute(stmt)
        conn.commit()
    print("def version content archive columns ready "
          "(content/content_captured_at).")


if __name__ == "__main__":
    run()
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_def_version_archive.py -v
```

预期：PASS（真实 dev 库上加列，重复执行幂等）。

- [ ] **Step 5: Commit**

```bash
git add server/migrations/2026_10_05_def_version_content_archive.py server/tests/test_def_version_archive.py
git commit -m "feat(skillopt): 定义版本归档列 content/content_captured_at（幂等迁移）"
```

---

### Task 2: register_def_version 公开重构——正文后到补齐、已有不覆盖

**Files:**
- Modify: `server/utils/skill_fit.py`（`_register_def_version` 约 236-248 行 → 公开 `register_def_version`；`compute_attempt_fit` 约 351 行调用点适配）
- Modify: `server/tests/test_def_version_archive.py`（追加）

**Interfaces:**
- Consumes: Task 1 的列。
- Produces: `utils.skill_fit.register_def_version(cur, def_kind: str, def_name: str, content_hash: str, content: str | None = None) -> None`（接收已开启的事务 cursor，不自行 commit）。Task 3/4/5/6 依赖此签名。

- [ ] **Step 1: 写失败测试**

追加到 `server/tests/test_def_version_archive.py`：

```python
def _def_versions(cur, kind, name):
    cur.execute(
        "SELECT content_hash, content, content_captured_at "
        "FROM ai_skill_def_versions WHERE def_kind=%s AND def_name=%s "
        "ORDER BY first_seen_at", (kind, name))
    return cur.fetchall()


def test_register_def_version_content_backfill_and_no_overwrite(db_conn):
    """新 hash 带正文注册；无正文行被后到注册补齐；已有正文永不覆盖。"""
    from utils.skill_fit import register_def_version
    name = f'arch-skill-{uuid.uuid4().hex[:8]}'
    h = hashlib.sha256(b'v1').hexdigest()
    try:
        with db_conn.cursor() as cur:
            register_def_version(cur, 'skill', name, h)  # 拟合路径：无正文
            db_conn.commit()
            rows = _def_versions(cur, 'skill', name)
            assert len(rows) == 1 and rows[0][1] is None and rows[0][2] is None
            register_def_version(cur, 'skill', name, h, content='正文 v1')
            db_conn.commit()
            rows = _def_versions(cur, 'skill', name)
            assert len(rows) == 1                        # 不翻倍
            assert rows[0][1] == '正文 v1' and rows[0][2] is not None
            register_def_version(cur, 'skill', name, h, content='偷换正文')
            db_conn.commit()
            rows = _def_versions(cur, 'skill', name)
            assert len(rows) == 1 and rows[0][1] == '正文 v1'  # 已有正文不覆盖
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s", (name,))
        db_conn.commit()


def test_register_def_version_empty_hash_allowed(db_conn):
    """manifest 无 hash 按空串注册（content_hash NOT NULL 兜底，现状行为保持）。"""
    from utils.skill_fit import register_def_version
    name = f'arch-skill-{uuid.uuid4().hex[:8]}'
    try:
        with db_conn.cursor() as cur:
            register_def_version(cur, 'agent', name, '')
            db_conn.commit()
            rows = _def_versions(cur, 'agent', name)
            assert len(rows) == 1 and rows[0][0] == ''
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_skill_def_versions "
                        "WHERE def_kind='agent' AND def_name=%s", (name,))
        db_conn.commit()
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_def_version_archive.py -v
```

预期：新两条 FAIL——`ImportError: cannot import name 'register_def_version'`。

- [ ] **Step 3: 改造 register 函数**

`server/utils/skill_fit.py`：将 `_register_def_version`（约 236-248 行）整体替换为：

```python
def register_def_version(cur, def_kind: str, def_name: str,
                         content_hash: str, content: str | None = None) -> None:
    """定义版本注册（spec §3.4/§4.1）：(def_kind, def_name, content_hash)
    UNIQUE upsert。冲突不再 DO NOTHING，而是「后到补齐正文」——已有正文
    永不覆盖（COALESCE 保旧），无正文行被带正文的注册补齐
    （content_captured_at 随正文补 NOW）。manifest 无 hash 时按空串注册
    （版本表 content_hash NOT NULL，空 hash 行不参与内容 diff/回滚）。"""
    cur.execute(
        """
        INSERT INTO ai_skill_def_versions
          (id, def_kind, def_name, content_hash, content, content_captured_at)
        VALUES (%s, %s, %s, %s, %s,
                CASE WHEN %s IS NULL THEN NULL ELSE NOW() END)
        ON CONFLICT (def_kind, def_name, content_hash) DO UPDATE SET
          content             = COALESCE(ai_skill_def_versions.content,
                                         EXCLUDED.content),
          content_captured_at = COALESCE(ai_skill_def_versions.content_captured_at,
                                         EXCLUDED.content_captured_at)
        """,
        ('defv_' + secrets.token_hex(6), def_kind, def_name,
         content_hash or '', content, content))
```

同文件 `compute_attempt_fit` 的调用点（约 351 行）由

```python
                    for vals in vals_list:
                        out.append(_upsert_result(cur, vals))
                        _register_def_version(cur, vals)
```

改为：

```python
                    for vals in vals_list:
                        out.append(_upsert_result(cur, vals))
                        register_def_version(cur, vals['def_kind'],
                                             vals['def_name'], vals['def_hash'])
```

- [ ] **Step 4: 跑新旧测试确认通过**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_def_version_archive.py tests/test_skill_fit.py tests/test_skill_fit_routes.py -v
```

预期：全部 PASS（拟合路径行为不变）。

- [ ] **Step 5: Commit**

```bash
git add server/utils/skill_fit.py server/tests/test_def_version_archive.py
git commit -m "feat(skillopt): register_def_version 支持正文归档——后到补齐/已有不覆盖"
```

---

### Task 3: 扫描被动兜底归档（execution_audit）

**Files:**
- Modify: `server/utils/execution_audit.py`（`collect_and_save_workspace_manifests` 约 336-338 行；其前新增 `register_definition_versions`；顶部已 import hashlib/logging/os，无需新增）
- Modify: `server/tests/test_def_version_archive.py`（追加）

**Interfaces:**
- Consumes: Task 2 的 `utils.skill_fit.register_def_version`；本模块既有 `_safe` 包装器（约 63 行）。
- Produces: `utils.execution_audit.register_definition_versions(manifests: list[dict]) -> int`（返回新登记行数）；`collect_and_save_workspace_manifests(attempt_id, workspace_path)` 新增副作用——每次执行对未见 hash 兜底归档。

- [ ] **Step 1: 写失败测试**

追加到 `server/tests/test_def_version_archive.py`：

```python
def test_register_definition_versions_archives_known_and_mismatch(db_conn, tmp_path):
    """spec §4.3：未登记 hash 读文件带正文注册；已知 hash 不重读（返回 0）；
    manifest hash 与重读正文 sha256 不一致 → 只登记 hash 不归档。"""
    from utils.execution_audit import register_definition_versions
    name = f'arch-skill-{uuid.uuid4().hex[:8]}'
    d = tmp_path / name
    d.mkdir()
    md = d / 'SKILL.md'
    md.write_bytes(b'v1')
    h1 = hashlib.sha256(b'v1').hexdigest()
    m1 = [{'kind': 'skill', 'name': name, 'path': str(md), 'content_hash': h1}]
    try:
        assert register_definition_versions(m1) == 1
        with db_conn.cursor() as cur:
            cur.execute("SELECT content, content_captured_at "
                        "FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s AND content_hash=%s",
                        (name, h1))
            row = cur.fetchone()
        assert row and row[0] == 'v1' and row[1] is not None
        assert register_definition_versions(m1) == 0          # check-first 不重复
        md.write_bytes(b'v2')                                  # 新 hash → 新版本行
        h2 = hashlib.sha256(b'v2').hexdigest()
        assert register_definition_versions(
            [{'kind': 'skill', 'name': name, 'path': str(md),
              'content_hash': h2}]) == 1
        bad = 'f' * 64                                         # hash 与正文不一致
        assert register_definition_versions(
            [{'kind': 'skill', 'name': name, 'path': str(md),
              'content_hash': bad}]) == 1
        with db_conn.cursor() as cur:
            cur.execute("SELECT content FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s AND content_hash=%s",
                        (name, bad))
            assert cur.fetchone()[0] is None                   # 只登记不归档
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s", (name,))
        db_conn.commit()


def test_collect_and_save_manifests_registers_versions(db_conn, tmp_path, monkeypatch):
    """collect_and_save_workspace_manifests 接线：执行落 manifest 后兜底归档。"""
    import utils.execution_audit as _ea
    name = f'arch-skill-{uuid.uuid4().hex[:8]}'
    ws = tmp_path / 'ws'
    skills = ws / '.opencode' / 'skills' / name
    skills.mkdir(parents=True)
    (skills / 'SKILL.md').write_bytes(b'collect-v1')
    uid, bid, sid, attempt = str(uuid.uuid4()), str(uuid.uuid4()), \
        str(uuid.uuid4()), str(uuid.uuid4())
    try:
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s, %s, 'x', 'FR', 'developer')", (uid, f'fr_{uid[:8]}'))
            cur.execute("INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
                        "VALUES (%s, %s, 'fr', 'p', 1)", (bid, uid))
            cur.execute("INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, "
                        "  batch_seq, workspace_path, session_token) "
                        "VALUES (%s, %s, 'completed', %s, 0, %s, %s)",
                        (sid, uid, bid, str(ws), f'tok-{sid[:12]}'))
            cur.execute("INSERT INTO ai_execution_attempts (id, session_id, source_type, "
                        "  operation, started_at, finished_at) "
                        "VALUES (%s, %s, 'batch', 'send', NOW(), NOW())", (attempt, sid))
        db_conn.commit()
        saved = _ea.collect_and_save_workspace_manifests(attempt, str(ws))
        assert saved >= 1
        h = hashlib.sha256(b'collect-v1').hexdigest()
        with db_conn.cursor() as cur:
            cur.execute("SELECT content FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s AND content_hash=%s",
                        (name, h))
            assert cur.fetchone()[0] == 'collect-v1'
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_execution_manifests WHERE attempt_id=%s", (attempt,))
            cur.execute("DELETE FROM ai_execution_attempts WHERE id=%s", (attempt,))
            cur.execute("DELETE FROM ai_chat_sessions WHERE id=%s", (sid,))
            cur.execute("DELETE FROM ai_chat_batches WHERE id=%s", (bid,))
            cur.execute("DELETE FROM users WHERE id=%s", (uid,))
            cur.execute("DELETE FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s", (name,))
        db_conn.commit()
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_def_version_archive.py -v
```

预期：新两条 FAIL——`ImportError: cannot import name 'register_definition_versions'`（第二条为 AttributeError 或同 import 失败）。

- [ ] **Step 3: 实现**

`server/utils/execution_audit.py`：在 `collect_and_save_workspace_manifests`（约 336 行）之前插入：

```python
def register_definition_versions(manifests: list[dict]) -> int:
    """定义版本被动兜底归档（spec §4.3）：扫描出的 kind∈(skill,agent) 且
    path/hash 齐备的行，def_versions 未登记的 hash 读文件带正文注册
    （check-first：已知 hash 不重读文件）。重读文件 sha256 与 manifest hash
    不一致（两次读之间被改）只登记 hash 不归档——正文无法自证与 hash 对应，
    warning 留痕。返回新登记行数。失败经 _safe 吞掉，不阻断执行链路。"""
    from utils.skill_fit import register_def_version

    def _impl() -> int:
        from db import get_db
        cands = [(m.get('kind'), m.get('name'), m.get('content_hash'), m.get('path'))
                 for m in manifests or []
                 if m.get('kind') in ('skill', 'agent')
                 and m.get('path') and m.get('content_hash')]
        if not cands:
            return 0
        conds = ','.join(['(%s, %s, %s)'] * len(cands))
        flat = [v for c in cands for v in c[:3]]
        registered = 0
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT def_kind, def_name, content_hash "
                    "FROM ai_skill_def_versions "
                    f"WHERE (def_kind, def_name, content_hash) IN ({conds})", flat)
                known = set(cur.fetchall())
                for kind, name, chash, path in cands:
                    if (kind, name, chash) in known:
                        continue
                    try:
                        with open(path, 'rb') as f:
                            raw = f.read()
                    except OSError as e:
                        logger.warning('def version archive: 定义文件读取失败 %s (%s)',
                                       path, e)
                        continue
                    if hashlib.sha256(raw).hexdigest() != chash:
                        logger.warning('def version archive: hash 与重读正文不一致，'
                                       '只登记不归档 %s (%s)', path, name)
                        register_def_version(cur, kind, name, chash)
                        registered += 1
                        continue
                    register_def_version(cur, kind, name, chash,
                                         content=raw.decode('utf-8'))
                    registered += 1
            conn.commit()
        return registered
    return _safe(_impl) or 0
```

并把 `collect_and_save_workspace_manifests` 改为：

```python
def collect_and_save_workspace_manifests(attempt_id: str,
                                         workspace_path: str | None) -> int:
    manifests = scan_workspace_manifests(workspace_path)
    saved = save_manifests(attempt_id, manifests)
    register_definition_versions(manifests)  # 被动兜底归档（spec §4.3）
    return saved
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_def_version_archive.py tests/test_execution_audit.py -v
```

预期：全部 PASS（execution_audit 既有用例回归不受影响）。

- [ ] **Step 5: Commit**

```bash
git add server/utils/execution_audit.py server/tests/test_def_version_archive.py
git commit -m "feat(skillopt): manifest 扫描被动兜底归档定义版本（check-first/不一致降级）"
```

---

### Task 4: steps 回写主动归档 + 默认版本 label

**Files:**
- Modify: `server/routes/ai_session_admin.py`（`skill_def_steps_apply` 约 1254-1278 行；顶部 import 区补 `import hashlib`、`from datetime import datetime`——现只有 os/secrets/logging）
- Modify: `server/tests/test_skill_fit_routes.py`（追加）

**Interfaces:**
- Consumes: Task 2 的 `register_def_version`；既有 `_path_in_allowed_roots`（约 1207 行）。
- Produces: `POST /ai/chat/admin/skill-def-steps/apply` body 增可选 `versionLabel: string`；副作用——写盘成功后登记版本（正文+hash+label，默认 `AI步骤优化 <YYYY-MM-DD>`）；响应形状不变 `{path}`。

- [ ] **Step 1: 写失败测试**

追加到 `server/tests/test_skill_fit_routes.py`：

```python
def test_skill_def_steps_apply_registers_version(client, admin_headers, db_conn,
                                                 tmp_path, monkeypatch):
    """apply 写盘后登记定义版本并归档正文（spec §4.2）：hash 取自落盘
    内容回读；默认 label「AI步骤优化 <日期>」；versionLabel 显式覆盖。"""
    import config as _config
    import routes.ai_session_admin as _admin
    monkeypatch.setattr(_config, 'AI_WORKSPACE_ROOT', str(tmp_path))
    monkeypatch.setattr(_admin, 'log_operation', lambda *a, **kw: None)
    name = f'demo-skill-{uuid.uuid4().hex[:6]}'
    d = tmp_path / name
    d.mkdir()
    p = d / 'SKILL.md'
    p.write_text('# v1\n', encoding='utf-8')
    steps = [{'id': 's1', 'name': '步骤1', 'expect': [{'tool': 'bash'}]}]
    try:
        r = client.post('/ai/chat/admin/skill-def-steps/apply',
                        headers=admin_headers,
                        json={'path': str(p), 'steps': steps})
        assert r.status_code == 200
        chash = hashlib.sha256(p.read_bytes()).hexdigest()
        with db_conn.cursor() as cur:
            cur.execute("SELECT content, version_label, content_captured_at "
                        "FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s AND content_hash=%s",
                        (name, chash))
            row = cur.fetchone()
        assert row and row[0] == p.read_bytes().decode('utf-8')
        assert row[1] and row[1].startswith('AI步骤优化 ')
        assert row[2] is not None
        r2 = client.post('/ai/chat/admin/skill-def-steps/apply',
                         headers=admin_headers,
                         json={'path': str(p), 'steps': steps,
                               'versionLabel': '手工标注'})
        assert r2.status_code == 200
        chash2 = hashlib.sha256(p.read_bytes()).hexdigest()
        with db_conn.cursor() as cur:
            cur.execute("SELECT version_label FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s AND content_hash=%s",
                        (name, chash2))
            assert cur.fetchone()[0] == '手工标注'
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s", (name,))
        db_conn.commit()
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_fit_routes.py::test_skill_def_steps_apply_registers_version -v
```

预期：FAIL——apply 后 def_versions 无行（`assert row` 处 `row is None`）。

- [ ] **Step 3: 实现**

`server/routes/ai_session_admin.py` 顶部 import 区（`import secrets` 之后）补：

```python
import hashlib
from datetime import datetime
```

`skill_def_steps_apply` 整体替换为：

```python
@ai_execution_admin_bp.post('/skill-def-steps/apply')
@require_permission('admin.ai_chat_admin')
def skill_def_steps_apply():
    """回写 fit.steps 到定义文件（body {path, steps, versionLabel?}）：
    path 逃出允许根 400；apply 前对每个 args_pattern 跑 validate_pg_regex，
    非法 400 且不写文件；文件不存在 404。写盘成功后按落盘内容登记定义
    版本并归档正文（spec §4.2）：bytes 回读保证 hash 与正文自洽，默认
    label「AI步骤优化 <YYYY-MM-DD>」，body.versionLabel 可覆盖。"""
    from utils import skill_fit_ai
    from utils.skill_fit import register_def_version
    body = request.get_json(silent=True) or {}
    path = (body.get('path') or '').strip()
    steps = body.get('steps')
    if not path or not isinstance(steps, list):
        return jsonify({'error': 'path 必填，steps 必须是数组'}), 400
    if not _path_in_allowed_roots(path):
        return jsonify({'error': 'path escapes allowed roots'}), 400
    if not os.path.isfile(path):
        return jsonify({'error': '定义文件不存在'}), 404
    try:
        out_path = skill_fit_ai.apply_steps(path, steps)
    except FileNotFoundError as e:
        return jsonify({'error': f'定义文件不存在: {e}'}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    # 版本登记（spec §4.2）：bytes 回读一次，sha256 与刚落盘内容自洽
    with open(out_path, 'rb') as f:
        raw = f.read()
    chash = hashlib.sha256(raw).hexdigest()
    if os.path.basename(out_path) == 'SKILL.md':
        def_kind, def_name = 'skill', os.path.basename(os.path.dirname(out_path))
    else:
        def_kind = 'agent'
        def_name = os.path.splitext(os.path.basename(out_path))[0]
    label = (body.get('versionLabel') or '').strip() or \
        f'AI步骤优化 {datetime.now():%Y-%m-%d}'
    with get_db() as conn:
        with conn.cursor() as cur:
            register_def_version(cur, def_kind, def_name, chash,
                                 content=raw.decode('utf-8'))
            cur.execute("UPDATE ai_skill_def_versions SET version_label = %s "
                        "WHERE def_kind = %s AND def_name = %s "
                        "AND content_hash = %s",
                        (label, def_kind, def_name, chash))
        conn.commit()
    log_operation('update', 'ai_skill_def_steps', out_path, None,
                  'SkillOpt 回写 fit.steps 到定义文件')
    return jsonify({'path': out_path})
```

- [ ] **Step 4: 跑路由测试确认通过**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_fit_routes.py -v
```

预期：全部 PASS（既有 confinement/reject 用例不回归）。

- [ ] **Step 5: Commit**

```bash
git add server/routes/ai_session_admin.py server/tests/test_skill_fit_routes.py
git commit -m "feat(skillopt): steps 回写主动归档定义版本+默认 label（spec §4.2）"
```

---

### Task 5: 版本列表补 archived 字段 + content / compare 端点

**Files:**
- Modify: `server/routes/ai_session_admin.py`（`list_skill_def_versions` 约 1059-1120 行 SELECT 与输出；其后新增两个端点）
- Modify: `server/tests/test_skill_fit_routes.py`（追加）

**Interfaces:**
- Consumes: Task 1 的列；Task 2 的 `register_def_version`（测试种子用）。
- Produces:
  - `GET /skill-def-versions` 的 versions[] 增 `archived: boolean`、`contentCapturedAt: string|null`；
  - `GET /skill-def-versions/<id>/content` → `{id, defKind, defName, contentHash, versionLabel, contentCapturedAt, content}`；404 不存在 / 400 未归档；
  - `GET /skill-def-versions/compare?fromId=&toId=` → `{from, to, diff}`（difflib unified，lineterm=''）；404 任一不存在 / 400 跨定义或任一未归档。
  - 注意路由匹配：`/skill-def-versions/compare` 与 `/skill-def-versions/<id>` 形状不同段且方法不同（PATCH），无冲突。

- [ ] **Step 1: 写失败测试**

追加到 `server/tests/test_skill_fit_routes.py`：

```python
def _seed_two_versions(db_conn, name):
    """种子：同一定义两个已归档版本 + 一个未归档版本。返回 (id_v1, id_v2, id_v0)。"""
    from utils.skill_fit import register_def_version
    h1 = hashlib.sha256('v1 正文'.encode('utf-8')).hexdigest()
    h2 = hashlib.sha256('v1 正文\n+v2 新增行'.encode('utf-8')).hexdigest()
    ids = {}
    with db_conn.cursor() as cur:
        register_def_version(cur, 'skill', name, h1, content='v1 正文')
        cur.execute("SELECT id FROM ai_skill_def_versions WHERE def_kind='skill' "
                    "AND def_name=%s AND content_hash=%s", (name, h1))
        ids['v1'] = cur.fetchone()[0]
        register_def_version(cur, 'skill', name, h2,
                             content='v1 正文\n+v2 新增行')
        cur.execute("SELECT id FROM ai_skill_def_versions WHERE def_kind='skill' "
                    "AND def_name=%s AND content_hash=%s", (name, h2))
        ids['v2'] = cur.fetchone()[0]
        register_def_version(cur, 'skill', name, 'a' * 64)   # 未归档
        cur.execute("SELECT id FROM ai_skill_def_versions WHERE def_kind='skill' "
                    "AND def_name=%s AND content_hash=%s", (name, 'a' * 64))
        ids['v0'] = cur.fetchone()[0]
    db_conn.commit()
    return ids['v1'], ids['v2'], ids['v0']


def test_skill_def_version_content_endpoint(client, admin_headers, db_conn):
    """content 端点：归档版本回全文；未归档 400；不存在 404。"""
    name = f'cmp-skill-{uuid.uuid4().hex[:6]}'
    v1, v2, v0 = _seed_two_versions(db_conn, name)
    try:
        r = client.get(f'/ai/chat/admin/skill-def-versions/{v2}/content',
                       headers=admin_headers)
        assert r.status_code == 200
        body = r.get_json()
        assert body['content'] == 'v1 正文\n+v2 新增行'
        assert body['defName'] == name and body['defKind'] == 'skill'
        assert body['contentHash'] == hashlib.sha256(
            'v1 正文\n+v2 新增行'.encode('utf-8')).hexdigest()
        r0 = client.get(f'/ai/chat/admin/skill-def-versions/{v0}/content',
                        headers=admin_headers)
        assert r0.status_code == 400 and r0.get_json()['error'] == '版本未归档'
        r404 = client.get('/ai/chat/admin/skill-def-versions/defv_nosuch/content',
                          headers=admin_headers)
        assert r404.status_code == 404
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s", (name,))
        db_conn.commit()


def test_skill_def_versions_compare_endpoint(client, admin_headers, db_conn):
    """compare 端点：相邻版本 unified diff；未归档 400；跨定义 400。"""
    from utils.skill_fit import register_def_version
    name = f'cmp-skill-{uuid.uuid4().hex[:6]}'
    v1, v2, v0 = _seed_two_versions(db_conn, name)
    other = f'cmp-skill-{uuid.uuid4().hex[:6]}'
    try:
        with db_conn.cursor() as cur:
            register_def_version(cur, 'skill', other,
                                 hashlib.sha256(b'x').hexdigest(), content='x')
            cur.execute("SELECT id FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s", (other,))
            other_id = cur.fetchone()[0]
        db_conn.commit()
        r = client.get('/ai/chat/admin/skill-def-versions/compare',
                       headers=admin_headers,
                       query_string={'fromId': v1, 'toId': v2})
        assert r.status_code == 200
        body = r.get_json()
        assert '+v2 新增行' in body['diff']
        assert body['from']['id'] == v1 and body['to']['id'] == v2
        r0 = client.get('/ai/chat/admin/skill-def-versions/compare',
                        headers=admin_headers,
                        query_string={'fromId': v0, 'toId': v2})
        assert r0.status_code == 400 and r0.get_json()['error'] == '版本未归档'
        rx = client.get('/ai/chat/admin/skill-def-versions/compare',
                        headers=admin_headers,
                        query_string={'fromId': v1, 'toId': other_id})
        assert rx.status_code == 400 and '同一定义' in rx.get_json()['error']
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name IN (%s, %s)",
                        (name, other))
        db_conn.commit()


def test_skill_def_versions_list_has_archived_flag(client, admin_headers, db_conn):
    """列表端点补 archived/contentCapturedAt（UI 置灰依据）。"""
    name = f'cmp-skill-{uuid.uuid4().hex[:6]}'
    v1, v2, v0 = _seed_two_versions(db_conn, name)
    try:
        r = client.get('/ai/chat/admin/skill-def-versions', headers=admin_headers,
                       query_string={'defName': name})
        assert r.status_code == 200
        rows = {v['id']: v for v in r.get_json()['versions']}
        assert rows[v2]['archived'] is True and rows[v2]['contentCapturedAt']
        assert rows[v0]['archived'] is False and rows[v0]['contentCapturedAt'] is None
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s", (name,))
        db_conn.commit()
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_fit_routes.py::test_skill_def_version_content_endpoint tests/test_skill_fit_routes.py::test_skill_def_versions_compare_endpoint tests/test_skill_fit_routes.py::test_skill_def_versions_list_has_archived_flag -v
```

预期：三条 FAIL——content/compare 404（路由不存在）；list 断言 `KeyError: 'archived'`。

- [ ] **Step 3: 实现**

`server/routes/ai_session_admin.py`：

(a) `list_skill_def_versions` 的 SELECT 中

```python
                SELECT v.id, v.def_kind, v.def_name, v.content_hash,
                       v.version_label, v.note, v.first_seen_at,
```

改为：

```python
                SELECT v.id, v.def_kind, v.def_name, v.content_hash,
                       v.version_label, v.note, v.first_seen_at,
                       v.content IS NOT NULL                AS archived,
                       v.content_captured_at,
```

输出 dict（`versions.append({...})`，约 1105 行）追加两项：

```python
            'archived': r.get('archived') or False,
            'contentCapturedAt': r['content_captured_at'].isoformat()
            if r.get('content_captured_at') else None,
```

(b) `list_skill_def_versions` 之后（`patch_skill_def_version` 之前）插入：

```python
@ai_execution_admin_bp.get('/skill-def-versions/compare')
@require_permission('admin.ai_chat_admin')
def compare_skill_def_versions():
    """两版本 unified diff（spec §5）：fromId/toId 须属同一定义且均已归档。
    服务端 difflib 生成（标准库），前端按行着色渲染，不引 diff 依赖。"""
    import difflib
    from_id = (request.args.get('fromId') or '').strip()
    to_id = (request.args.get('toId') or '').strip()
    if not from_id or not to_id:
        return jsonify({'error': 'fromId/toId 必填'}), 400
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, def_kind, def_name, content_hash, version_label, "
                "       content, content_captured_at "
                "FROM ai_skill_def_versions WHERE id IN (%s, %s)",
                (from_id, to_id))
            cols = [d[0] for d in cur.description]
            rows = {r['id']: r
                    for r in (dict(zip(cols, x)) for x in cur.fetchall())}
    src, dst = rows.get(from_id), rows.get(to_id)
    if not src or not dst:
        return jsonify({'error': '版本不存在'}), 404
    if (src['def_kind'], src['def_name']) != (dst['def_kind'], dst['def_name']):
        return jsonify({'error': '两个版本不属于同一定义'}), 400
    if src['content'] is None or dst['content'] is None:
        return jsonify({'error': '版本未归档'}), 400
    diff = '\n'.join(difflib.unified_diff(
        src['content'].splitlines(), dst['content'].splitlines(),
        fromfile=f"{src['def_name']}@{(src['content_hash'] or '')[:12]}",
        tofile=f"{dst['def_name']}@{(dst['content_hash'] or '')[:12]}",
        lineterm=''))

    def _meta(r):
        return {'id': r['id'], 'defName': r['def_name'],
                'contentHash': r['content_hash'],
                'versionLabel': r['version_label'],
                'contentCapturedAt': r['content_captured_at'].isoformat()
                if r['content_captured_at'] else None}

    return jsonify({'from': _meta(src), 'to': _meta(dst), 'diff': diff})


@ai_execution_admin_bp.get('/skill-def-versions/<version_id>/content')
@require_permission('admin.ai_chat_admin')
def get_skill_def_version_content(version_id):
    """版本正文预览（spec §5）。不存在 404；未归档（content 为 NULL）400。"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, def_kind, def_name, content_hash, version_label, "
                "       content, content_captured_at "
                "FROM ai_skill_def_versions WHERE id = %s", (version_id,))
            row = cur.fetchone()
    if not row:
        return jsonify({'error': '版本不存在'}), 404
    cols = ['id', 'def_kind', 'def_name', 'content_hash', 'version_label',
            'content', 'content_captured_at']
    r = dict(zip(cols, row))
    if r['content'] is None:
        return jsonify({'error': '版本未归档'}), 400
    return jsonify({
        'id': r['id'], 'defKind': r['def_kind'], 'defName': r['def_name'],
        'contentHash': r['content_hash'], 'versionLabel': r['version_label'],
        'contentCapturedAt': r['content_captured_at'].isoformat()
        if r['content_captured_at'] else None,
        'content': r['content'],
    })
```

- [ ] **Step 4: 跑路由测试确认通过**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_fit_routes.py tests/test_def_version_archive.py -v
```

预期：全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add server/routes/ai_session_admin.py server/tests/test_skill_fit_routes.py
git commit -m "feat(skillopt): 版本列表 archived 标志 + content/compare 端点（spec §5）"
```

---

### Task 6: rollback 端点——前滚式回滚

**Files:**
- Modify: `server/routes/ai_session_admin.py`（`patch_skill_def_version` 约 1123-1150 行之后插入）
- Modify: `server/tests/test_skill_fit_routes.py`（追加）

**Interfaces:**
- Consumes: Task 2 `register_def_version`、Task 5 的种子 helper `_seed_two_versions`、既有 `_path_in_allowed_roots`、`_seed_fit_fixture`（本文件已有）。
- Produces: `POST /skill-def-versions/<id>/rollback` → `{ok: true, path, contentHash}`；404 版本不存在/定位不到文件；400 未归档/路径逃逸；500 写盘 OSError。

- [ ] **Step 1: 写失败测试**

追加到 `server/tests/test_skill_fit_routes.py`：

```python
def test_skill_def_version_rollback(client, admin_headers, db_conn, tmp_path,
                                    monkeypatch):
    """回滚（spec §4.4）：归档正文 bytes 写回 manifest 最新 path；写回后
    文件 sha256 == content_hash；版本行数不膨胀；动作留操作日志。"""
    import config as _config
    import routes.ai_session_admin as _admin
    monkeypatch.setattr(_config, 'AI_WORKSPACE_ROOT', str(tmp_path))
    logged = []
    monkeypatch.setattr(_admin, 'log_operation',
                        lambda *a, **kw: logged.append(a))
    name = f'rlb-skill-{uuid.uuid4().hex[:6]}'
    target = tmp_path / f'{name}' / 'SKILL.md'
    target.parent.mkdir(parents=True)
    target.write_text('当前已是新版正文', encoding='utf-8')
    from utils.skill_fit import register_def_version
    h_old = hashlib.sha256('v1 正文'.encode('utf-8')).hexdigest()
    try:
        with db_conn.cursor() as cur:
            register_def_version(cur, 'skill', name, h_old, content='v1 正文')
            cur.execute("SELECT id FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s AND content_hash=%s",
                        (name, h_old))
            vid = cur.fetchone()[0]
            # manifest 行指向 tmp 定义文件（FK 链沿用 _seed_fit_fixture 模式）
            uid, bid, sid, attempt = (str(uuid.uuid4()), str(uuid.uuid4()),
                                      str(uuid.uuid4()), str(uuid.uuid4()))
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s, %s, 'x', 'FR', 'developer')", (uid, f'fr_{uid[:8]}'))
            cur.execute("INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
                        "VALUES (%s, %s, 'fr', 'p', 1)", (bid, uid))
            cur.execute("INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, "
                        "  batch_seq, workspace_path, session_token) "
                        "VALUES (%s, %s, 'completed', %s, 0, %s, %s)",
                        (sid, uid, bid, str(tmp_path), f'tok-{sid[:12]}'))
            cur.execute("INSERT INTO ai_execution_attempts (id, session_id, source_type, "
                        "  operation, started_at, finished_at) "
                        "VALUES (%s, %s, 'batch', 'send', NOW(), NOW())", (attempt, sid))
            cur.execute("INSERT INTO ai_execution_manifests (id, attempt_id, kind, name, "
                        "  source, path, content_hash, injected) "
                        "VALUES (%s, %s, 'skill', %s, 'session', %s, %s, true)",
                        ('man_' + uuid.uuid4().hex[:8], attempt, name,
                         str(target), 'f' * 64))
        db_conn.commit()
        r = client.post(f'/ai/chat/admin/skill-def-versions/{vid}/rollback',
                        headers=admin_headers)
        assert r.status_code == 200
        body = r.get_json()
        assert body['ok'] is True and body['contentHash'] == h_old
        assert target.read_bytes() == 'v1 正文'.encode('utf-8')  # bytes 精确写回
        assert hashlib.sha256(target.read_bytes()).hexdigest() == h_old
        assert logged and logged[0][0] == 'update'        # 操作日志留痕
        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s", (name,))
            assert cur.fetchone()[0] == 1                 # 版本行不膨胀
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_execution_manifests WHERE attempt_id=%s", (attempt,))
            cur.execute("DELETE FROM ai_execution_attempts WHERE id=%s", (attempt,))
            cur.execute("DELETE FROM ai_chat_sessions WHERE id=%s", (sid,))
            cur.execute("DELETE FROM ai_chat_batches WHERE id=%s", (bid,))
            cur.execute("DELETE FROM users WHERE id=%s", (uid,))
            cur.execute("DELETE FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s", (name,))
        db_conn.commit()


def test_skill_def_version_rollback_error_paths(client, admin_headers, db_conn,
                                                tmp_path, monkeypatch):
    """未归档 400；定位不到定义文件 404；路径逃逸 400。"""
    import config as _config
    import routes.ai_session_admin as _admin
    monkeypatch.setattr(_config, 'AI_WORKSPACE_ROOT', str(tmp_path))
    monkeypatch.setattr(_admin, 'log_operation', lambda *a, **kw: None)
    from utils.skill_fit import register_def_version
    name = f'rlb-skill-{uuid.uuid4().hex[:6]}'
    name2 = name  # 404 分支用第二个定义；预先赋值保证 finally 可清理
    # FK 链种子（escape 用例的 manifest 依赖）——变量初始化在 try 之前，
    # 保证 finally 清理不依赖用例中途失败点
    uid, bid, sid, attempt = (str(uuid.uuid4()), str(uuid.uuid4()),
                              str(uuid.uuid4()), str(uuid.uuid4()))
    try:
        with db_conn.cursor() as cur:
            register_def_version(cur, 'skill', name, 'b' * 64)  # 未归档
            cur.execute("SELECT id FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s", (name,))
            vid0 = cur.fetchone()[0]
            h = hashlib.sha256('v1 正文'.encode('utf-8')).hexdigest()
            register_def_version(cur, 'skill', name, h, content='v1 正文')
            cur.execute("SELECT id FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s AND content_hash=%s",
                        (name, h))
            vid1 = cur.fetchone()[0]
            escape = 'C:\\Windows\\win.ini' if os.name == 'nt' else '/etc/passwd'
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s, %s, 'x', 'FR', 'developer')", (uid, f'fr_{uid[:8]}'))
            cur.execute("INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
                        "VALUES (%s, %s, 'fr', 'p', 1)", (bid, uid))
            cur.execute("INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, "
                        "  batch_seq, workspace_path, session_token) "
                        "VALUES (%s, %s, 'completed', %s, 0, %s, %s)",
                        (sid, uid, bid, str(tmp_path), f'tok-{sid[:12]}'))
            cur.execute("INSERT INTO ai_execution_attempts (id, session_id, source_type, "
                        "  operation, started_at, finished_at) "
                        "VALUES (%s, %s, 'batch', 'send', NOW(), NOW())", (attempt, sid))
            cur.execute("INSERT INTO ai_execution_manifests (id, attempt_id, kind, name, "
                        "  source, path, content_hash, injected) "
                        "VALUES (%s, %s, 'skill', %s, 'session', %s, %s, true)",
                        ('man_' + uuid.uuid4().hex[:8], attempt, name,
                         escape, 'f' * 64))
        db_conn.commit()
        r = client.post(f'/ai/chat/admin/skill-def-versions/{vid0}/rollback',
                        headers=admin_headers)
        assert r.status_code == 400 and r.get_json()['error'] == '版本未归档'
        r404 = client.post(f'/ai/chat/admin/skill-def-versions/{vid1}/rollback',
                           headers=admin_headers)
        # manifest 最新 path = escape 路径 → 先过「定位」再被 confinement 拦下
        assert r404.status_code == 400
        assert r404.get_json()['error'] == 'path escapes allowed roots'
        # 再验证完全无 manifest 的 404 分支：换一个无 manifest 的定义
        name2 = f'rlb-skill-{uuid.uuid4().hex[:6]}'
        with db_conn.cursor() as cur:
            register_def_version(cur, 'skill', name2, h, content='v1 正文')
            cur.execute("SELECT id FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s AND content_hash=%s",
                        (name2, h))
            vid2 = cur.fetchone()[0]
        db_conn.commit()
        r404b = client.post(f'/ai/chat/admin/skill-def-versions/{vid2}/rollback',
                            headers=admin_headers)
        assert r404b.status_code == 404
        assert r404b.get_json()['error'] == '无法定位定义文件'
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_execution_manifests WHERE attempt_id=%s", (attempt,))
            cur.execute("DELETE FROM ai_execution_attempts WHERE id=%s", (attempt,))
            cur.execute("DELETE FROM ai_chat_sessions WHERE id=%s", (sid,))
            cur.execute("DELETE FROM ai_chat_batches WHERE id=%s", (bid,))
            cur.execute("DELETE FROM users WHERE id=%s", (uid,))
            cur.execute("DELETE FROM ai_skill_def_versions WHERE def_kind='skill' "
                        "AND def_name IN (%s, %s)", (name, name2))
        db_conn.commit()
```

注：第二个测试 finally 里 batch/user 的清理为防御式（uid/bid 可能未赋值即失败——实现时若 `uid` 未定义会 NameError，可按本文件既有 `_seed_fit_fixture` 的清理习惯把 INSERT 提前到 try 内已知赋值后；上面代码将 uid/bid 赋值放在 try 内 escape 用例前，finally 需改为只清理确定存在的行——直接采用第一个测试的逐表 DELETE 模式并把 uid/bid/sid/attempt 初始化挪到 try 之前即可，实现时统一）。

- [ ] **Step 2: 跑测试确认失败**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_fit_routes.py::test_skill_def_version_rollback tests/test_skill_fit_routes.py::test_skill_def_version_rollback_error_paths -v
```

预期：FAIL——rollback 路由不存在（404，`body['ok']` KeyError）。

- [ ] **Step 3: 实现**

`server/routes/ai_session_admin.py` 在 `patch_skill_def_version`（约 1150 行）之后插入：

```python
@ai_execution_admin_bp.post('/skill-def-versions/<version_id>/rollback')
@require_permission('admin.ai_chat_admin')
def rollback_skill_def_version(version_id):
    """回滚到归档版本（spec §4.4/§5）：归档正文按 bytes 原样写回该定义
    最新 manifest 记录的文件路径。写回内容 sha256 == content_hash，下次
    扫描注册时 UNIQUE 命中同一版本行——时间线不产生虚假回滚版本，回滚
    动作本身经 log_operation 留痕。"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT def_kind, def_name, content_hash, content "
                "FROM ai_skill_def_versions WHERE id = %s", (version_id,))
            row = cur.fetchone()
            if not row:
                return jsonify({'error': '版本不存在'}), 404
            def_kind, def_name, content_hash, content = row
            if content is None or not content_hash:
                return jsonify({'error': '版本未归档'}), 400
            cur.execute(
                "SELECT path FROM ai_execution_manifests "
                "WHERE kind = %s AND name = %s "
                "  AND path IS NOT NULL AND path <> '' "
                "ORDER BY created_at DESC LIMIT 1", (def_kind, def_name))
            mrow = cur.fetchone()
        conn.commit()
    if not mrow or not mrow[0]:
        return jsonify({'error': '无法定位定义文件'}), 404
    path = mrow[0]
    if not _path_in_allowed_roots(path):
        return jsonify({'error': 'path escapes allowed roots'}), 400
    try:
        with open(path, 'wb') as f:
            f.write(content.encode('utf-8'))
    except OSError as e:
        return jsonify({'error': f'定义文件写入失败: {e}'}), 500
    log_operation('update', 'ai_skill_def_versions', version_id, def_name,
                  'SkillOpt 定义版本回滚')
    return jsonify({'ok': True, 'path': path, 'contentHash': content_hash})
```

- [ ] **Step 4: 跑路由测试确认通过**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_fit_routes.py -v
```

预期：全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add server/routes/ai_session_admin.py server/tests/test_skill_fit_routes.py
git commit -m "feat(skillopt): 定义版本回滚端点——bytes 写回/hash 复原/操作日志"
```

---

### Task 7: 前端 API client（aiSkills.ts）

**Files:**
- Modify: `src/api/aiSkills.ts`（`SkillDefVersion` 接口约 110-124 行；`applySkillDefSteps` 约 190-192 行；文件尾追加类型与函数）

**Interfaces:**
- Consumes: Task 4/5/6 的后端契约（camelCase 字段已在端点锁定）。
- Produces: `SkillDefVersion.archived/contentCapturedAt`；`getSkillDefVersionContent(id)`、`compareSkillDefVersions({fromId,toId})`、`rollbackSkillDefVersion(id)`、`applySkillDefSteps` body 增 `versionLabel?`。Task 8 依赖。

- [ ] **Step 1: 扩展类型与函数**

`SkillDefVersion` 接口追加两个字段（放 `divergedCount` 之后）：

```ts
  /** 正文是否已归档（历史存量行为 false，内容动作置灰） */
  archived: boolean
  contentCapturedAt: string | null
```

`applySkillDefSteps` 改为：

```ts
export function applySkillDefSteps(body: { path: string; steps: FitStep[]; versionLabel?: string }) {
  return post<{ path: string }>(`${ADMIN}/skill-def-steps/apply`, body)
}
```

文件尾追加：

```ts
/** 版本归档正文（spec §5） */
export interface SkillDefVersionContent {
  id: string
  defKind: string
  defName: string
  contentHash: string | null
  versionLabel: string | null
  contentCapturedAt: string | null
  content: string
}

/** compare 端点的单端元信息 */
export interface SkillDefVersionDiffMeta {
  id: string
  defName: string
  contentHash: string | null
  versionLabel: string | null
  contentCapturedAt: string | null
}

export function getSkillDefVersionContent(id: string) {
  return get<SkillDefVersionContent>(
    `${ADMIN}/skill-def-versions/${encodeURIComponent(id)}/content`)
}

export function compareSkillDefVersions(params: { fromId: string; toId: string }) {
  return get<{ from: SkillDefVersionDiffMeta; to: SkillDefVersionDiffMeta; diff: string }>(
    `${ADMIN}/skill-def-versions/compare`, params)
}

export function rollbackSkillDefVersion(id: string) {
  return post<{ ok: boolean; path: string; contentHash: string }>(
    `${ADMIN}/skill-def-versions/${encodeURIComponent(id)}/rollback`)
}
```

- [ ] **Step 2: 类型检查确认通过**

```bash
npx vue-tsc --noEmit
```

预期：无错误（此时 Task 8 尚未开始，新函数暂无调用方，导出即可）。

- [ ] **Step 3: Commit**

```bash
git add src/api/aiSkills.ts
git commit -m "feat(skillopt): 前端版本归档 API client——content/compare/rollback"
```

---

### Task 8: DefVersionTimeline 三个动作 + diff 渲染组件

**Files:**
- Create: `src/components/admin/skillopt/DefContentDiff.vue`
- Modify: `src/components/admin/skillopt/DefVersionTimeline.vue`（head 行加动作、脚本加对话框状态与处理、模板尾加两个 ElDialog、style 追加）

**Interfaces:**
- Consumes: Task 7 的 API 函数与 `VersionView.archived`。
- Produces: `DefContentDiff.vue` props `{ diff: string }`；时间线每节点「内容 / 对比 / 回滚」动作（未归档置灰；首个版本对比置灰）。

- [ ] **Step 1: 创建 DefContentDiff.vue**

```vue
<template>
  <pre class="vcd"><code><span v-for="(ln, i) in lines" :key="i"
    :class="lineClass(ln)">{{ ln || ' ' }}
</span></code></pre>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ diff: string }>()

const lines = computed(() => props.diff.split('\n'))

/** unified diff 行着色：@@ hunk 头 / --- +++ 文件头 / + 新增 / - 删除 */
function lineClass(ln: string) {
  if (ln.startsWith('@@') || ln.startsWith('--- ') || ln.startsWith('+++ ')) {
    return 'vcd__hunk'
  }
  if (ln.startsWith('+')) return 'vcd__add'
  if (ln.startsWith('-')) return 'vcd__del'
  return ''
}
</script>

<style scoped>
.vcd {
  margin: 0; max-height: 60vh; overflow: auto;
  font-family: monospace; font-size: 12px; line-height: 1.5;
  background: var(--el-fill-color-light); padding: 8px; border-radius: 4px;
}
.vcd__add {
  color: var(--el-color-success);
  background: rgba(103, 194, 58, 0.12);
  display: inline-block; width: 100%;
}
.vcd__del {
  color: var(--el-color-danger);
  background: rgba(245, 108, 108, 0.12);
  display: inline-block; width: 100%;
}
.vcd__hunk { color: var(--el-color-info); font-weight: 600; }
</style>
```

- [ ] **Step 2: 改造 DefVersionTimeline.vue**

模板 `vt__head` 行（`生成步骤` 按钮之后）追加：

```vue
          <ElButton link size="small" :disabled="!v.archived"
                    @click="openContent(v)">内容</ElButton>
          <ElButton link size="small" :disabled="!v.archived || !prevOf(v)"
                    @click="openDiff(v)">对比</ElButton>
          <ElButton link size="small" type="warning" :disabled="!v.archived"
                    @click="rollback(v)">回滚</ElButton>
```

模板根 div `.vt` 末尾（`</ElTimeline>` 之后）追加两个对话框：

```vue
    <ElDialog v-model="contentDialog.visible"
              :title="`版本内容 — ${contentDialog.label}`" width="720px">
      <div v-loading="contentDialog.loading">
        <pre class="vt__content">{{ contentDialog.content }}</pre>
      </div>
    </ElDialog>
    <ElDialog v-model="diffDialog.visible"
              :title="`版本对比 — ${diffDialog.title}`" width="860px">
      <div v-loading="diffDialog.loading">
        <DefContentDiff v-if="!diffDialog.loading" :diff="diffDialog.diff" />
      </div>
    </ElDialog>
```

脚本：import 行改为

```ts
import {
  ElTimeline, ElTimelineItem, ElInput, ElButton, ElTag, ElMessage,
  ElDialog, ElMessageBox,
} from 'element-plus'
import DefContentDiff from './DefContentDiff.vue'
import {
  listSkillDefVersions, updateSkillDefVersion, getSkillDefVersionContent,
  compareSkillDefVersions, rollbackSkillDefVersion,
} from '@/api/aiSkills'
```

`saveLabel` 函数之后追加：

```ts
// ── 版本归档动作（spec §6）：内容预览 / 相邻对比 / 前滚式回滚 ───────────

const contentDialog = ref<{ visible: boolean; loading: boolean; label: string
  ; content: string }>({ visible: false, loading: false, label: '', content: '' })
const diffDialog = ref<{ visible: boolean; loading: boolean; title: string
  ; diff: string }>({ visible: false, loading: false, title: '', diff: '' })

/** displayVersions 新→旧；v 的「上一版」= 列表中其后第一个已归档版本 */
function prevOf(v: VersionView): VersionView | undefined {
  const idx = displayVersions.value.findIndex(x => x.id === v.id)
  return displayVersions.value.slice(idx + 1).find(x => x.archived)
}

async function openContent(v: VersionView) {
  contentDialog.value = { visible: true, loading: true,
    label: v.versionLabel || v.defName, content: '' }
  try {
    const res = await getSkillDefVersionContent(v.id)
    contentDialog.value.content = res.content
  } catch {
    contentDialog.value.visible = false   // 全局 toast 已提示
  } finally {
    contentDialog.value.loading = false
  }
}

async function openDiff(v: VersionView) {
  const prev = prevOf(v)
  if (!prev) return
  diffDialog.value = { visible: true, loading: true, title: '', diff: '' }
  try {
    const res = await compareSkillDefVersions({ fromId: prev.id, toId: v.id })
    diffDialog.value.title =
      `${prev.versionLabel || prev.defName}@${shortHash(prev.contentHash)}` +
      ` → ${v.versionLabel || v.defName}@${shortHash(v.contentHash)}`
    diffDialog.value.diff = res.diff
  } catch {
    diffDialog.value.visible = false
  } finally {
    diffDialog.value.loading = false
  }
}

async function rollback(v: VersionView) {
  try {
    await ElMessageBox.confirm(
      '将把该版本归档的正文原样写回当前定义文件（下次执行起生效）。确定回滚？',
      '回滚确认', { type: 'warning', confirmButtonText: '回滚',
                    cancelButtonText: '取消' })
  } catch { return }
  try {
    await rollbackSkillDefVersion(v.id)
    ElMessage.success('已回滚：正文已写回定义文件')
    await load()
  } catch { /* 全局 toast 已提示 */ }
}
```

style 末尾追加：

```css
.vt__content {
  margin: 0; max-height: 60vh; overflow: auto;
  font-family: monospace; font-size: 12px; line-height: 1.5; white-space: pre-wrap;
  word-break: break-all;
}
```

- [ ] **Step 3: 类型检查 + 既有前端单测**

```bash
npx vue-tsc --noEmit && npm run test
```

预期：均通过（vitest 既有用例不回归）。

- [ ] **Step 4: Commit**

```bash
git add src/components/admin/skillopt/DefContentDiff.vue src/components/admin/skillopt/DefVersionTimeline.vue
git commit -m "feat(skillopt): 版本时间线内容预览/相邻对比/回滚动作（spec §6）"
```

---

### Task 9: E2E——版本时间线归档链路（真实后端，不烧 LLM）

**Files:**
- Create: `e2e/ai-full/ai-skillopt-version-archive.spec.ts`

**Interfaces:**
- Consumes: Task 1-8 全部落地；dev 栈运行中（后端 :3002 已**手动重启**载入新代码 + vite dev server）；`e2e/ai-full/batch/db_exec.py`（stdin SQL 桥）。
- Produces: `e2e/ai-full` 回归新增一条版本归档链路用例。

**口径说明（对 spec §8 的偏差，有意为之）：** spec §8 的 E2E 行写的是「跑批→回写→时间线→diff→回滚」全链；但本页既有 E2E 约定（`ai-skillopt-page.spec.ts` 头注）为「纯 UI，不烧 LLM」。本用例改为：DB 种子两个已归档版本 + 一个未归档版本 → 走真实页面验证时间线渲染、内容弹窗、对比弹窗、未归档置灰——真实后端/真实库/真实页面，仅不产生 LLM 调用。回滚的**写路径**正确性由 Task 6 的真实 PG 路由测试覆盖（真实写 tmp 文件并校验 hash 复原）。

- [ ] **Step 1: 写 E2E 用例**

创建 `e2e/ai-full/ai-skillopt-version-archive.spec.ts`：

```ts
/**
 * SkillOpt 定义版本归档链路（真实后端，不烧 LLM）。
 * spec: docs/superpowers/specs/2026-10-05-skill-agent-version-archive-design.md §6/§8
 * 约定: 等真实元素,绝不等 networkidle;种子行 finally 清理（db_exec 桥）。
 * 种子: 同一定义两个已归档版本 + 一个未归档版本（UUID 后缀隔离）。
 */
import { test, expect, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import crypto from 'node:crypto'

const V1 = 'v1 正文'
const V2 = 'v1 正文\n+v2 新增行'
const KIND = 'skill'
const NAME = `e2e-arch-${Date.now()}`

function db(sql: string): string {
  const script = path.join(__dirname, 'batch', 'db_exec.py')
  return execFileSync('python', [script], { input: sql, encoding: 'utf-8' })
}

const sha = (s: string) => crypto.createHash('sha256').update(s).digest('hex')

function seed(): { v1: string; v2: string; v0: string } {
  // PG 标准字符串可跨行：V2 的真实换行直接嵌入（'\n' 转义反而是反斜杠+n）
  db(`
    INSERT INTO ai_skill_def_versions (id, def_kind, def_name, content_hash,
      version_label, content, content_captured_at)
    VALUES ('defv_${NAME}_1', '${KIND}', '${NAME}', '${sha(V1)}', 'e2e v1', '${V1}', NOW()),
           ('defv_${NAME}_2', '${KIND}', '${NAME}', '${sha(V2)}', 'e2e v2', '${V2}', NOW()),
           ('defv_${NAME}_0', '${KIND}', '${NAME}', '${'a'.repeat(64)}', NULL, NULL, NULL)
    ON CONFLICT (def_kind, def_name, content_hash) DO NOTHING;`)
  return { v1: `defv_${NAME}_1`, v2: `defv_${NAME}_2`, v0: `defv_${NAME}_0` }
}

function cleanup(): void {
  db(`DELETE FROM ai_skill_def_versions WHERE def_kind='${KIND}' AND def_name='${NAME}';`)
}

async function login(page: Page): Promise<void> {
  await page.goto('/')
  const userInput = page.locator('input[placeholder*="用户名"]')
  try {
    await userInput.waitFor({ state: 'visible', timeout: 30_000 })
    await userInput.fill('admin')
    await page.locator('input[placeholder*="密码"]').fill('admin123')
    await page.getByRole('button', { name: /登\s*录/ }).click()
    await userInput.waitFor({ state: 'hidden', timeout: 15_000 })
  } catch {
    // 已是登录态——直接继续
  }
}

test('版本时间线：内容预览/相邻对比/未归档置灰', async ({ page }) => {
  test.setTimeout(180_000)
  const ids = seed()
  try {
    await login(page)
    await page.goto('/admin/ai-skillopt')
    await expect(page.getByText('SkillOpt — 技能优化')).toBeVisible()
    // 左栏选中目标定义
    const defItem = page.locator('.fit-def', { hasText: NAME }).first()
    await defItem.waitFor({ state: 'visible', timeout: 30_000 })
    await defItem.click()
    // 版本演进子标签
    await page.getByRole('tab', { name: '版本演进' }).click()
    const timeline = page.locator('.vt')
    await expect(timeline).toBeVisible()
    // 两个已归档版本的标注 + hash 短 id 可见
    await expect(timeline.getByText('e2e v2')).toBeVisible()
    await expect(timeline.getByText('e2e v1')).toBeVisible()
    // 未归档版本行内动作置灰（disabled）
    const disabledRollback = timeline.locator('button:has-text("回滚")')
      .and(page.locator('[disabled]'))
    await expect(disabledRollback.first()).toBeVisible()
    // 内容预览：新版本节点
    const newest = timeline.locator('.el-timeline-item').first()
    await newest.getByRole('button', { name: '内容' }).click()
    const contentDialog = page.locator('.el-dialog', { hasText: '版本内容' })
    await expect(contentDialog).toBeVisible()
    await expect(contentDialog.locator('pre')).toContainText('+v2 新增行')
    await contentDialog.locator('.el-dialog__headerbtn').click()
    // 相邻对比：新版本节点「对比」（from=上一版 → to=本版）
    await newest.getByRole('button', { name: '对比' }).click()
    const diffDialog = page.locator('.el-dialog', { hasText: '版本对比' })
    await expect(diffDialog).toBeVisible()
    await expect(diffDialog.locator('pre')).toContainText('+v2 新增行')
    await diffDialog.locator('.el-dialog__headerbtn').click()
    void ids
  } finally {
    cleanup()
  }
})
```

- [ ] **Step 2: 重启后端 + 跑 E2E**

```bash
# 手动重启后端（:3002 不自动 reload），确认启动日志出现
# "migration 2026_10_05_def_version_content_archive.py: ok"
npx playwright test e2e/ai-full/ai-skillopt-version-archive.spec.ts
```

预期：PASS。若 `.fit-def` 未出现，先确认 dev 库定义清单非空（该用例种子了目标定义，理论上必现）。

- [ ] **Step 3: 全量回归**

```bash
npx playwright test e2e/ai-full/ai-skillopt-page.spec.ts   # 页面冒烟不回归
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_fit.py tests/test_skill_fit_routes.py tests/test_execution_audit.py tests/test_def_version_archive.py -v
```

预期：全部 PASS。

- [ ] **Step 4: Commit**

```bash
git add e2e/ai-full/ai-skillopt-version-archive.spec.ts
git commit -m "test(skillopt): 版本归档链路 E2E——时间线/内容/对比/置灰（真实后端不烧 LLM）"
```

---

## 任务依赖与顺序

Task 1 → 2 → 3/4（可并行，均只依赖 2）→ 5 → 6 → 7 → 8 → 9。前端（7/8）可与后端 Task 5/6 并行，但 E2E（9）须全部完成。
