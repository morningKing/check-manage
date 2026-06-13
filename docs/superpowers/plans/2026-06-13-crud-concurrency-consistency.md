# CRUD 并发读写一致性加固 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除创建路径的并发缺陷——autoSequence 改后端原子分配、手填主键用 advisory lock 串行去重——并把已生效的更新乐观锁补齐为全前端契约 + 统一 409 冲突 UX；还原后重播种序列计数器。

**Architecture:** 后端新增计数表 `dynamic_sequences` 与一组可复用函数（`allocate_sequence` / `reseed_sequences` / `acquire_pk_lock`），接入 `create_item`/`update_item`/`restore_backup`；前端停止客户端序列生成、autoSequence 表单字段只读「保存后生成」、统一 409 刷新处理。维持 READ COMMITTED + 定点锁，仅新增一表。

**Tech Stack:** Python + psycopg2 + PostgreSQL（后端）；Vue 3 + TS + Pinia + Vitest（前端）；pytest（含多线程并发测试）。

设计依据：`docs/superpowers/specs/2026-06-13-crud-concurrency-consistency-design.md`

---

## 既有锚点（实现前必读）

**后端 `server/routes/dynamic.py`：**
- `get_primary_key_fields(cur, collection)`（`:~60`）→ `page_configs.fields` 中 `isPrimaryKey` 为真的 `fieldName` 列表。
- `check_primary_key_unique(cur, collection, data, pk_fields, exclude_id=None, branch_id=None)`（`:69`）→ 返回错误串或 None（**当前竞态点**）。
- `get_page_info(cur, collection)` → `(page_name, fields)`；`fields` 每项含 `controlType`、`sequenceConfig`（`{prefix, max}`）、`isPrimaryKey`。
- `create_item`（`:488`）：现 `check_primary_key_unique`（`:526`）→ validation → `INSERT INTO dynamic_data (id, collection, data, created_at, branch_id)`（`:550`）→ relations。**autoSequence 当前由前端填好后随 data 传入。**
- `update_item`（`:594`）：`SELECT data, version … `（`:642`）→ `check_primary_key_unique(exclude_id)`（`:658`）→ 乐观锁版本检查（`:680`）→ CAS UPDATE `WHERE version=db_version`（`:704`/`:709`）+ `rowcount==0` 兜底（`:714`）。
- `get_db()`（`server/db.py`）：`with get_db() as conn:` 产出连接，正常退出 `commit`、异常 `rollback`；`cur = conn.cursor()`。`psycopg2.extras.Json(...)` 写 JSONB。

**`dynamic_data`（`server/init_db.py:33`）：** `PRIMARY KEY (id, branch_id)`、`version INTEGER NOT NULL DEFAULT 1`、`branch_id`。

**前端：**
- `src/stores/pageConfig.ts` `addPageData`：`:500-503` 循环用 `generateNextSequenceValue` 填 autoSequence（**待移除**）；`:536` POST；`:538` 把返回记录入缓存。`updatePageData`：`:583-584` 取 `cached._version` 回传（已生效）。`generateNextSequenceValue`（`:731`）、`batchGenerateSequenceValues`（`:765`）。
- `src/components/dynamic-form/FormRenderer.vue:108`：`.filter(f => f.controlType !== 'autoTimestamp' && f.controlType !== 'autoSequence')` —— autoSequence 当前**被排除出表单**。
- `src/components/dynamic-form/controls/AutoSequence.vue`：已是只读展示控件，无值显示「自动生成」；`controls/index.ts:50` 映射 `autoSequence → AutoSequence`。
- `src/api/data.ts`：`createData`（`:37`）、`updateData`（`:48`）。

**备份还原 `server/utils/backup.py`：** `restore_backup(zip_path, tables=None, mode='upsert')`（`:569`）单事务，`dynamic_data` 含 `version` 原样写回，**不触碰序列**。`BACKUP_TABLE_MAP['dynamic_data']` 列含 `version`（`:31`）。

**测试约定（Windows）：** `cd server ; $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ; python -m pytest <path> -v`。fixture `db_conn`（`server/tests/conftest.py:96`，真实库连接，提交可见）。前端 `npx vitest run <path>`。

---

## Task 1: 计数表 + reseed_sequences + 迁移

**Files:**
- Create: `server/utils/sequences.py`
- Modify: `server/init_db.py`（建表 DDL）
- Create: `server/migrations/2026_06_13_dynamic_sequences.py`
- Test: `server/tests/test_sequences_reseed.py`

- [ ] **Step 1: 写失败测试**

`server/tests/test_sequences_reseed.py`:
```python
import json
import psycopg2.extras
from db import get_db
from utils.sequences import reseed_sequences, seq_max_from_data


def _setup_page(cur, collection, seq_field, prefix):
    page_id = f'page-{collection}'
    fields = [{'fieldName': seq_field, 'controlType': 'autoSequence',
               'sequenceConfig': {'prefix': prefix, 'max': 999}, 'isPrimaryKey': True}]
    cur.execute("DELETE FROM page_configs WHERE id=%s", (page_id,))
    cur.execute("INSERT INTO page_configs (id, name, fields) VALUES (%s,%s,%s)",
                (page_id, collection, psycopg2.extras.Json(fields)))


def _add_record(cur, collection, rid, data, branch='main'):
    cur.execute("INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s,%s,%s,%s)",
                (rid, collection, psycopg2.extras.Json(data), branch))


def test_seq_max_and_reseed(db_conn):
    cur = db_conn.cursor()
    coll = 'zzseqtest'
    cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
    cur.execute("DELETE FROM dynamic_sequences WHERE collection=%s", (coll,))
    _setup_page(cur, coll, 'code', 'IC-')
    _add_record(cur, coll, 'r1', {'code': 'IC-003'})
    _add_record(cur, coll, 'r2', {'code': 'IC-007'})
    _add_record(cur, coll, 'r3', {'code': 'IC-002'})
    db_conn.commit()

    assert seq_max_from_data(cur, coll, 'main', 'code', 'IC-') == 7

    reseed_sequences(cur)
    db_conn.commit()
    cur.execute("SELECT current_value FROM dynamic_sequences WHERE collection=%s AND branch_id='main' AND field_name='code'", (coll,))
    assert cur.fetchone()[0] == 7

    # GREATEST 语义：再次 reseed 不回退（即便手动抬高计数）
    cur.execute("UPDATE dynamic_sequences SET current_value=20 WHERE collection=%s AND field_name='code'", (coll,))
    db_conn.commit()
    reseed_sequences(cur)
    db_conn.commit()
    cur.execute("SELECT current_value FROM dynamic_sequences WHERE collection=%s AND field_name='code'", (coll,))
    assert cur.fetchone()[0] == 20  # 不回退到 7

    cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
    cur.execute("DELETE FROM dynamic_sequences WHERE collection=%s", (coll,))
    cur.execute("DELETE FROM page_configs WHERE id=%s", (f'page-{coll}',))
    db_conn.commit()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd server ; $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ; python -m pytest tests/test_sequences_reseed.py -v`
Expected: FAIL —— `dynamic_sequences` 表不存在 / `utils.sequences` 不存在。

- [ ] **Step 3: 建表 DDL（`server/init_db.py`）**

在 `dynamic_data` 建表语句之后、其索引附近，加入（与既有 `CREATE TABLE IF NOT EXISTS` 风格一致）：
```sql
CREATE TABLE IF NOT EXISTS dynamic_sequences (
    collection    VARCHAR(200) NOT NULL,
    branch_id     VARCHAR(100) NOT NULL DEFAULT 'main',
    field_name    VARCHAR(200) NOT NULL,
    current_value BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (collection, branch_id, field_name)
);
```
（找到 `init_db.py` 中执行 DDL 的位置——它把多条 `CREATE TABLE` 拼在一个大 SQL 字符串里执行；把上面这段追加进同一批 DDL。）

- [ ] **Step 4: 写实现 `server/utils/sequences.py`**

```python
"""autoSequence 后端原子分配 + 计数器播种（迁移与还原共用）。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _autoseq_fields_by_collection(cur, collections=None):
    """返回 {collection: [(field_name, prefix), ...]}，仅 autoSequence 字段。"""
    if collections:
        page_ids = [f'page-{c}' for c in collections]
        cur.execute("SELECT id, fields FROM page_configs WHERE id = ANY(%s)", (page_ids,))
    else:
        cur.execute("SELECT id, fields FROM page_configs")
    out = {}
    for pid, fields in cur.fetchall():
        coll = pid[len('page-'):] if pid.startswith('page-') else pid
        for f in (fields or []):
            if f.get('controlType') == 'autoSequence':
                prefix = (f.get('sequenceConfig') or {}).get('prefix', '')
                out.setdefault(coll, []).append((f['fieldName'], prefix))
    return out


def seq_max_from_data(cur, collection, branch_id, field_name, prefix):
    """扫描 dynamic_data，取该字段去前缀后的最大数值；无则 0。"""
    cur.execute(
        "SELECT data->>%s FROM dynamic_data WHERE collection=%s AND branch_id=%s AND data ? %s",
        (field_name, collection, branch_id, field_name),
    )
    mx = 0
    plen = len(prefix)
    for (val,) in cur.fetchall():
        if not isinstance(val, str):
            continue
        s = val[plen:] if (prefix and val.startswith(prefix)) else val
        try:
            n = int(s)
        except (ValueError, TypeError):
            continue
        if n > mx:
            mx = n
    return mx


def reseed_sequences(cur, collections=None, branch_id=None):
    """为每个 (collection, branch, autoSequence字段) 重播种计数器。
    GREATEST 语义：current_value = max(已有计数, 数据中的 max)，绝不回退（避免重用已删 ID）。
    branch_id=None 时对数据中出现的所有分支播种。"""
    fields_map = _autoseq_fields_by_collection(cur, collections)
    for coll, fld_list in fields_map.items():
        # 找出该 collection 下涉及的分支
        if branch_id is not None:
            branches = [branch_id]
        else:
            cur.execute("SELECT DISTINCT branch_id FROM dynamic_data WHERE collection=%s", (coll,))
            branches = [r[0] for r in cur.fetchall()] or ['main']
        for br in branches:
            for field_name, prefix in fld_list:
                mx = seq_max_from_data(cur, coll, br, field_name, prefix)
                cur.execute(
                    "INSERT INTO dynamic_sequences (collection, branch_id, field_name, current_value) "
                    "VALUES (%s,%s,%s,%s) "
                    "ON CONFLICT (collection, branch_id, field_name) "
                    "DO UPDATE SET current_value = GREATEST(dynamic_sequences.current_value, EXCLUDED.current_value)",
                    (coll, br, field_name, mx),
                )
```

- [ ] **Step 5: 运行确认通过**

Run: `cd server ; $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ; python -m pytest tests/test_sequences_reseed.py -v`
Expected: PASS。（注意：`dynamic_sequences` 表需先存在——本地若未重跑 `init_db.py`，先手动建表：`cd server ; python -c "from db import get_db; \nwith get_db() as c: c.cursor().execute('CREATE TABLE IF NOT EXISTS dynamic_sequences (collection VARCHAR(200) NOT NULL, branch_id VARCHAR(100) NOT NULL DEFAULT ''main'', field_name VARCHAR(200) NOT NULL, current_value BIGINT NOT NULL DEFAULT 0, PRIMARY KEY (collection, branch_id, field_name))')"`。）

- [ ] **Step 6: 迁移脚本**

`server/migrations/2026_06_13_dynamic_sequences.py`:
```python
"""幂等迁移：建 dynamic_sequences 表 + 按现有数据播种计数器。
用法（server/ 下）：python -m migrations.2026_06_13_dynamic_sequences"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db
from utils.sequences import reseed_sequences

DDL = """
CREATE TABLE IF NOT EXISTS dynamic_sequences (
    collection    VARCHAR(200) NOT NULL,
    branch_id     VARCHAR(100) NOT NULL DEFAULT 'main',
    field_name    VARCHAR(200) NOT NULL,
    current_value BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (collection, branch_id, field_name)
);
"""


def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(DDL)
        reseed_sequences(cur)
        conn.commit()
    return {"status": "ok"}


if __name__ == "__main__":
    print(run())
```

- [ ] **Step 7: 应用迁移到本地库 + 提交**

Run: `cd server ; python -m migrations.2026_06_13_dynamic_sequences`
Expected: 打印 `{'status': 'ok'}`。
```bash
git add server/utils/sequences.py server/init_db.py server/migrations/2026_06_13_dynamic_sequences.py server/tests/test_sequences_reseed.py
git commit -m "feat(concurrency): dynamic_sequences 计数表 + reseed_sequences 播种 + 幂等迁移"
```

---

## Task 2: 原子序列分配 allocate_sequence

**Files:**
- Modify: `server/utils/sequences.py`
- Test: `server/tests/test_sequences_allocate.py`

- [ ] **Step 1: 写失败测试**

`server/tests/test_sequences_allocate.py`:
```python
import psycopg2.extras
from concurrent.futures import ThreadPoolExecutor
from db import get_db
from utils.sequences import allocate_sequence


def _setup(coll='zzalloc'):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
        cur.execute("DELETE FROM dynamic_sequences WHERE collection=%s", (coll,))
        conn.commit()
    return coll


def test_allocate_basic_and_format():
    coll = _setup()
    with get_db() as conn:
        cur = conn.cursor()
        vals = allocate_sequence(cur, coll, 'main', 'code', 'IC-', 3, count=1)
        conn.commit()
    assert vals == ['IC-001']
    with get_db() as conn:
        cur = conn.cursor()
        vals = allocate_sequence(cur, coll, 'main', 'code', 'IC-', 3, count=2)
        conn.commit()
    assert vals == ['IC-002', 'IC-003']


def test_allocate_seeds_from_existing():
    coll = _setup('zzalloc2')
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s,%s,%s,'main')",
                    ('x', coll, psycopg2.extras.Json({'code': 'IC-050'})))
        conn.commit()
    with get_db() as conn:
        cur = conn.cursor()
        # 计数行不存在 → 从现有 max(50) 播种 → 下一个 51
        vals = allocate_sequence(cur, coll, 'main', 'code', 'IC-', 3, count=1)
        conn.commit()
    assert vals == ['IC-051']


def test_allocate_concurrent_no_dup():
    coll = _setup('zzalloc3')
    def one(_):
        with get_db() as conn:
            cur = conn.cursor()
            v = allocate_sequence(cur, coll, 'main', 'code', 'IC-', 4, count=1)
            conn.commit()
            return v[0]
    with ThreadPoolExecutor(max_workers=12) as ex:
        results = list(ex.map(one, range(40)))
    assert len(set(results)) == 40  # 无重复
```

- [ ] **Step 2: 运行确认失败**

Run: `cd server ; $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ; python -m pytest tests/test_sequences_allocate.py -v`
Expected: FAIL —— `allocate_sequence` 未定义。

- [ ] **Step 3: 实现 `allocate_sequence`（追加到 `server/utils/sequences.py`）**

```python
def allocate_sequence(cur, collection, branch_id, field_name, prefix, pad, count=1):
    """原子分配 count 个序列值，返回格式化后的字符串列表。
    计数行 SELECT ... FOR UPDATE 串行化；计数行不存在则按现有数据 max 播种。
    必须在调用方的事务内执行（提交前不释放行锁）。"""
    cur.execute(
        "SELECT current_value FROM dynamic_sequences "
        "WHERE collection=%s AND branch_id=%s AND field_name=%s FOR UPDATE",
        (collection, branch_id, field_name),
    )
    row = cur.fetchone()
    if row is None:
        base = seq_max_from_data(cur, collection, branch_id, field_name, prefix)
        new_value = base + count
        cur.execute(
            "INSERT INTO dynamic_sequences (collection, branch_id, field_name, current_value) "
            "VALUES (%s,%s,%s,%s)",
            (collection, branch_id, field_name, new_value),
        )
        start = base + 1
    else:
        base = row[0]
        new_value = base + count
        cur.execute(
            "UPDATE dynamic_sequences SET current_value=%s "
            "WHERE collection=%s AND branch_id=%s AND field_name=%s",
            (new_value, collection, branch_id, field_name),
        )
        start = base + 1
    return [f"{prefix}{n:0{pad}d}" for n in range(start, start + count)]
```
注：`seq_max_from_data` 已在 Task 1 定义于同文件。`pad` = `len(str(sequenceConfig.max))`（补零位数）。

> 并发说明：计数行不存在时两个事务同时走 INSERT 分支会撞 PK（`ON CONFLICT` 未用），其一报错回滚——可接受（调用方将重试或返回错误）。为消除该窄窗，可改为先 `INSERT ... ON CONFLICT DO NOTHING` 建零行再 `SELECT ... FOR UPDATE`。**实现采用后者更稳**：在函数开头加
> `cur.execute("INSERT INTO dynamic_sequences (collection,branch_id,field_name,current_value) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", (collection,branch_id,field_name, seq_max_from_data(cur,collection,branch_id,field_name,prefix)))`
> 然后总走 `SELECT ... FOR UPDATE` + `UPDATE` 分支（去掉 row is None 分支）。请按此「先 upsert 零行再锁」版本实现，测试不变。

- [ ] **Step 4: 运行确认通过**

Run: `cd server ; $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ; python -m pytest tests/test_sequences_allocate.py -v`
Expected: PASS（3 项，含 40 线程并发无重复）。

- [ ] **Step 5: 提交**

```bash
git add server/utils/sequences.py server/tests/test_sequences_allocate.py
git commit -m "feat(concurrency): allocate_sequence 原子分配（先 upsert 零行再 FOR UPDATE）"
```

---

## Task 3: 接入 create_item / update_item（分配 + advisory lock）

**Files:**
- Modify: `server/routes/dynamic.py`
- Test: `server/tests/test_create_concurrency.py`

- [ ] **Step 1: 写失败测试（直接测可复用的「加锁去重创建」helper + 序列接入）**

`server/tests/test_create_concurrency.py`:
```python
import psycopg2.extras
from concurrent.futures import ThreadPoolExecutor
from db import get_db
from routes.dynamic import acquire_pk_lock, check_primary_key_unique


def _setup(coll='zzcreate'):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
        conn.commit()
    return coll


def _try_create_locked(coll, pk_value):
    """模拟 create_item 的「advisory lock → 唯一检查 → 插入」段。返回 'ok' 或 'dup'。"""
    with get_db() as conn:
        cur = conn.cursor()
        acquire_pk_lock(cur, coll, {'code': pk_value})
        if check_primary_key_unique(cur, coll, {'code': pk_value}, ['code'], branch_id='main'):
            conn.commit()
            return 'dup'
        rid = f'{coll}-{pk_value}-{psycopg2.extras.uuid.uuid4().hex[:6]}' if hasattr(psycopg2.extras, 'uuid') else f'{coll}-{pk_value}'
        import uuid
        rid = f'{coll}-{uuid.uuid4().hex[:8]}'
        cur.execute("INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s,%s,%s,'main')",
                    (rid, coll, psycopg2.extras.Json({'code': pk_value})))
        conn.commit()
        return 'ok'


def test_advisory_lock_serializes_same_pk():
    coll = _setup()
    def one(_):
        return _try_create_locked(coll, 'PK-1')
    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(one, range(20)))
    assert results.count('ok') == 1
    assert results.count('dup') == 19
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM dynamic_data WHERE collection=%s AND data->>'code'='PK-1'", (coll,))
        assert cur.fetchone()[0] == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `cd server ; $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ; python -m pytest tests/test_create_concurrency.py -v`
Expected: FAIL —— `acquire_pk_lock` 未定义。

- [ ] **Step 3: 加 `acquire_pk_lock` + 接入 create_item / update_item（`server/routes/dynamic.py`）**

文件顶部 import 区加：
```python
from utils.sequences import allocate_sequence
```
在 `check_primary_key_unique` 附近新增：
```python
def acquire_pk_lock(cur, collection, pk_values):
    """对 (collection + 主键值拼接) 取事务级 advisory lock，串行化同主键并发写。
    pk_values: {field: value}。空值/无主键则不加锁。"""
    parts = [str(pk_values.get(f, '')) for f in sorted(pk_values)]
    key = collection + '|' + '|'.join(parts)
    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))", (collection, key))
```

在 `create_item` 的 `with get_db() as conn:` 块内、`check_primary_key_unique` 调用**之前**，加入序列分配 + 加锁：
```python
        cur = conn.cursor()
        page_name, fields = get_page_info(cur, collection)
        # 后端原子分配 autoSequence（忽略客户端传入值）
        for f in (fields or []):
            if f.get('controlType') == 'autoSequence':
                cfg = f.get('sequenceConfig') or {}
                prefix = cfg.get('prefix', '')
                pad = len(str(cfg.get('max', 999)))
                data[f['fieldName']] = allocate_sequence(
                    cur, collection, branch_id, f['fieldName'], prefix, pad, count=1)[0]
        # 手填主键 advisory lock（非 autoSequence 主键）
        pk_fields = get_primary_key_fields(cur, collection)
        manual_pk = {f: data.get(f) for f in pk_fields
                     if not any(fl.get('fieldName') == f and fl.get('controlType') == 'autoSequence' for fl in (fields or []))}
        if manual_pk:
            acquire_pk_lock(cur, collection, manual_pk)
```
然后保留原有 `check_primary_key_unique(...)`（它会用上面已填好的 autoSequence 值/锁内重查）、validation、INSERT。**删除原 `:526` 处单独的 `pk_fields = get_primary_key_fields(...)`**（已在上面取过；避免重复，复用变量）。注意 `get_page_info` 现在在块内提前调用，原后续若再调用可去重。

在 `update_item` 的唯一检查前，若本次更新会改主键值，则加锁：
```python
        manual_pk = {f: merged_data.get(f) for f in (pk_fields or [])
                     if not any(fl.get('fieldName') == f and fl.get('controlType') == 'autoSequence' for fl in (fields or []))
                     and f in data}  # 仅当请求改了该主键字段
        if manual_pk:
            acquire_pk_lock(cur, collection, manual_pk)
```
（放在 `check_primary_key_unique(exclude_id=...)` 之前；`pk_fields`/`fields` 此处已可得。）

- [ ] **Step 4: 运行确认通过 + 回归**

Run: `cd server ; $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ; python -m pytest tests/test_create_concurrency.py -v`
Expected: PASS（20 并发同主键 → 恰 1 成功）。
Run: `cd server ; $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ; python -m pytest -q`
Expected: 全绿（既有 + 新增）。

- [ ] **Step 5: 提交**

```bash
git add server/routes/dynamic.py server/tests/test_create_concurrency.py
git commit -m "feat(concurrency): create/update 接入原子序列分配 + 手填主键 advisory lock"
```

---

## Task 4: 还原后重播种序列计数器

**Files:**
- Modify: `server/utils/backup.py`
- Test: `server/tests/test_restore_reseed.py`

- [ ] **Step 1: 写失败测试**

`server/tests/test_restore_reseed.py`:
```python
import psycopg2.extras
from db import get_db
from utils.sequences import reseed_sequences, allocate_sequence


def test_reseed_after_data_change_prevents_collision():
    """模拟还原把编号更大的数据写回后，重播种使后续分配不撞号。"""
    coll = 'zzrestore'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
        cur.execute("DELETE FROM dynamic_sequences WHERE collection=%s", (coll,))
        cur.execute("DELETE FROM page_configs WHERE id=%s", (f'page-{coll}',))
        fields = [{'fieldName': 'code', 'controlType': 'autoSequence',
                   'sequenceConfig': {'prefix': 'IC-', 'max': 999}, 'isPrimaryKey': True}]
        cur.execute("INSERT INTO page_configs (id,name,fields) VALUES (%s,%s,%s)",
                    (f'page-{coll}', coll, psycopg2.extras.Json(fields)))
        # 计数器停在 5
        cur.execute("INSERT INTO dynamic_sequences VALUES (%s,'main','code',5)", (coll,))
        # 模拟还原写回编号到 IC-030 的数据
        cur.execute("INSERT INTO dynamic_data (id,collection,data,branch_id) VALUES (%s,%s,%s,'main')",
                    ('big', coll, psycopg2.extras.Json({'code': 'IC-030'})))
        conn.commit()
        # 还原钩子：reseed
        reseed_sequences(cur, collections=[coll])
        conn.commit()
        nxt = allocate_sequence(cur, coll, 'main', 'code', 'IC-', 3, count=1)[0]
        conn.commit()
    assert nxt == 'IC-031'  # 不与 IC-030 撞号
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
        cur.execute("DELETE FROM dynamic_sequences WHERE collection=%s", (coll,))
        cur.execute("DELETE FROM page_configs WHERE id=%s", (f'page-{coll}',))
        conn.commit()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd server ; $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ; python -m pytest tests/test_restore_reseed.py -v`
Expected: FAIL —— 当前 `allocate` 返回 `IC-006`（计数器停在 5，未 reseed 到 30），断言失败；或 reseed 未被还原调用。
（注：此测试直接验证 reseed+allocate 协作；Step 3 把 reseed 接入 restore_backup。）

- [ ] **Step 3: 在 `restore_backup` 写回 dynamic_data 后调用 reseed（`server/utils/backup.py`）**

在 `restore_backup` 内，`dynamic_data` 完成写回、`conn.commit()` **之前**，加入：
```python
        # 还原 dynamic_data 后重播种序列计数器，避免后续创建与还原记录重号
        if any(t == 'dynamic_data' or t.startswith('dynamic_data:') or t is None
               for t in ([None] if tables is None else tables)) or 'dynamic_data' in restored_base_tables:
            from utils.sequences import reseed_sequences
            affected = list(collection_filters.get('dynamic_data')) if collection_filters.get('dynamic_data') else None
            reseed_sequences(cur, collections=affected)
```
（按 `restore_backup` 实际可见的变量名调整：`tables` 入参、`collection_filters`（`:620` 附近）、还原所用的游标 `cur`/连接。核心：还原 `dynamic_data` 后、提交前，用同一游标对受影响 collection（整库则 None）调用 `reseed_sequences`。读 `restore_backup` 主体确定提交点与变量名。）

- [ ] **Step 4: 运行 + 回归**

Run: `cd server ; $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ; python -m pytest tests/test_restore_reseed.py tests/test_backup.py -v`
Expected: PASS（含既有 backup 测试）。

- [ ] **Step 5: 提交**

```bash
git add server/utils/backup.py server/tests/test_restore_reseed.py
git commit -m "feat(concurrency): restore_backup 还原后重播种序列计数器，防重号"
```

---

## Task 5: 前端——停止客户端序列生成 + autoSequence 表单只读

**Files:**
- Modify: `src/stores/pageConfig.ts`
- Modify: `src/components/dynamic-form/FormRenderer.vue`
- Modify: `src/components/dynamic-form/controls/AutoSequence.vue`
- Test: `src/components/dynamic-form/controls/__tests__/AutoSequence.test.ts`（新建）

- [ ] **Step 1: 移除创建期客户端序列生成（`src/stores/pageConfig.ts`）**

删除 `addPageData` 中（`:500-503`）：
```js
    // 自动填充 autoSequence 字段
    for (const field of getAutoSequenceFields(pageId)) {
      newRecord[field.fieldName] = generateNextSequenceValue(pageId, field)
    }
```
（`autoTimestamp`、`compositeText` 的填充保留。`generateNextSequenceValue`/`batchGenerateSequenceValues` 函数本身可保留导出但不再于创建期调用；若 grep 确认创建路径外无调用，可一并删除——本步仅删上面这段调用。）

- [ ] **Step 2: 表单放行 autoSequence 为只读展示（`FormRenderer.vue`）**

把 `:108` 的过滤：
```js
.filter((f) => f.controlType !== 'autoTimestamp' && f.controlType !== 'autoSequence')
```
改为仅排除 autoTimestamp（autoSequence 改为渲染只读控件）：
```js
.filter((f) => f.controlType !== 'autoTimestamp')
```
（`controls/index.ts:50` 已映射 `autoSequence → AutoSequence`，会渲染为只读 span。）

- [ ] **Step 3: AutoSequence 占位文案改为「保存后生成」（`controls/AutoSequence.vue`）**

把 `displayValue` 的占位：
```js
  if (!props.modelValue) return '自动生成'
```
改为：
```js
  if (!props.modelValue) return '保存后生成'
```

- [ ] **Step 4: 写测试——AutoSequence 控件只读展示（`src/components/dynamic-form/controls/__tests__/AutoSequence.test.ts`，新建）**

测被改动的控件（纯展示，稳定、无 store 依赖）：
```ts
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import AutoSequence from '@/components/dynamic-form/controls/AutoSequence.vue'

describe('AutoSequence 只读控件', () => {
  it('无值时显示「保存后生成」', () => {
    const w = mount(AutoSequence, { props: { field: { fieldName: 'code' } as any, modelValue: null } })
    expect(w.text()).toBe('保存后生成')
  })
  it('有值时显示实际编号', () => {
    const w = mount(AutoSequence, { props: { field: { fieldName: 'code' } as any, modelValue: 'IC-007' } })
    expect(w.text()).toBe('IC-007')
  })
})
```
> 「创建流程不再客户端生成序列」由 Step 1 的删除 + Task 7 集成验证覆盖（store 创建流程单测成本高、价值低，不强测）；`AutoSequence` 控件的「保存后生成」占位是本任务唯一新增的可断言行为，故此处覆盖它。

- [ ] **Step 5: 运行 + 回归 + 类型检查**

Run: `npx vitest run src/stores/__tests__/pageConfig.sequence.test.ts`
Expected: PASS。
Run: `npx vitest run src/` → 全绿（既有涉及 autoSequence 的测试若断言旧行为需同步更新，说明原因）。
Run: `npx vue-tsc --noEmit` → 干净。

- [ ] **Step 6: 提交**

```bash
git add src/stores/pageConfig.ts src/components/dynamic-form/FormRenderer.vue src/components/dynamic-form/controls/AutoSequence.vue src/components/dynamic-form/controls/__tests__/AutoSequence.test.ts
git commit -m "feat(concurrency): 前端停止客户端序列生成，autoSequence 表单只读「保存后生成」"
```

---

## Task 6: 统一 409 冲突 UX + 更新乐观锁路径审计

**Files:**
- Modify: `src/views/dynamic/DynamicPage.vue`（统一 409 处理）
- Audit/Modify: Excel 批量保存 / 批量更新路径（确保回传 `_version`）
- Test: `src/views/dynamic/__tests__/conflictHandling.test.ts`（新建，纯逻辑）

- [ ] **Step 1: 抽出可测的 409 判定纯函数 + 写测试**

`src/views/dynamic/__tests__/conflictHandling.test.ts`:
```ts
import { describe, it, expect } from 'vitest'
import { isVersionConflict, conflictMessage } from '@/views/dynamic/conflict'

describe('版本冲突识别', () => {
  it('识别 409 + VERSION_CONFLICT', () => {
    expect(isVersionConflict({ response: { status: 409, data: { code: 'VERSION_CONFLICT' } } })).toBe(true)
  })
  it('普通错误不算冲突', () => {
    expect(isVersionConflict({ response: { status: 400, data: {} } })).toBe(false)
    expect(isVersionConflict(new Error('x'))).toBe(false)
  })
  it('冲突文案固定', () => {
    expect(conflictMessage()).toContain('其他用户修改')
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/views/dynamic/__tests__/conflictHandling.test.ts`
Expected: FAIL —— `@/views/dynamic/conflict` 不存在。

- [ ] **Step 3: 实现 `src/views/dynamic/conflict.ts`**

```ts
/** 识别后端乐观锁版本冲突（HTTP 409 + code=VERSION_CONFLICT）。 */
export function isVersionConflict(err: any): boolean {
  const r = err?.response
  return !!r && r.status === 409 && r.data?.code === 'VERSION_CONFLICT'
}

export function conflictMessage(): string {
  return '数据已被其他用户修改，请刷新后重试'
}
```

- [ ] **Step 4: 在 DynamicPage 的保存失败分支接入统一处理**

在 `handleFormSubmit`/`handleSubmit` 的 catch（以及编辑保存处）中，区分版本冲突：
```ts
import { isVersionConflict, conflictMessage } from './conflict'
// ... 在保存 catch 内：
if (isVersionConflict(error)) {
  ElMessage.warning(conflictMessage())
  await handleRefresh()          // 重新拉取列表/记录
  // 关闭或保持对话框由现状决定；至少刷新数据让用户基于最新值重做
} else {
  ElMessage.error(/* 原有错误处理 */)
}
```
（按 DynamicPage 现有 catch 结构接入；保持其它错误分支不变。）

- [ ] **Step 5: 审计更新路径回传 `_version`**

Run grep 审计所有更新调用：检查 `src/` 内 `updateData(`、`put(`、Excel 保存（`ExcelView`/`pageConfig` 的 excel 保存）、批量/AI 写回 是否都带 `_version`。参照已生效的 `pageConfig.ts:583-584`（从缓存记录取 `_version` 并并入 body）。
- 对**缺失**的更新路径，补上 `_version`（从对应缓存/源记录取）。
- 若 Excel 批量保存不便逐条带版本（整表覆盖语义），则在 PR 说明中标注其为「整表保存、非逐条乐观锁」并保持现状——**不在本任务强行改造 Excel 整表语义**（YAGNI），仅确保逐条更新型路径带版本。
本步以「审计 + 对逐条更新路径补 `_version`」为交付；把发现与改动记录在提交信息。

- [ ] **Step 6: 运行 + 回归**

Run: `npx vitest run src/views/dynamic/__tests__/conflictHandling.test.ts`
Expected: PASS。
Run: `npx vitest run src/` → 全绿。
Run: `npx vue-tsc --noEmit` → 干净。

- [ ] **Step 7: 提交**

```bash
git add src/views/dynamic/conflict.ts src/views/dynamic/__tests__/conflictHandling.test.ts src/views/dynamic/DynamicPage.vue
# + 审计中补 _version 的文件
git commit -m "feat(concurrency): 统一 409 版本冲突 UX + 审计更新路径回传 _version"
```

---

## Task 7: 集成验证 + 全量回归

**Files:** 无代码改动（除非验证发现问题）

- [ ] **Step 1: 审计批量/导入创建路径的序列安全**

Grep `INSERT INTO dynamic_data` 在 `server/` 内（排除 `create_item`/迁移/测试）：定位**导入/批量**直接插入路径。若它们绕过 `create_item` 且仍依赖客户端/本地生成序列或手填主键无锁，按 Task 3 的模式接入 `allocate_sequence` + `acquire_pk_lock`。grep `batchGenerateSequenceValues` 在 `src/` 的剩余调用（导入预览等），评估是否仍会产生重号——若导入最终走逐条 `create_item`，则已被后端接管，记录结论；若有独立批量端点，补齐。把结论写入提交信息（无改动则跳过提交）。

- [ ] **Step 2: 应用迁移 + 起服务**

确认 Task 1 迁移已在 dev 库执行（`dynamic_sequences` 存在并已播种）。`npm run dev:all` 起前后端。

- [ ] **Step 3: 手工并发验证（dev 库）**

两个浏览器会话/两个 `curl` 并发对同一带 autoSequence 的数据页创建多条 → 确认序列号无重复、连续；并发用相同手填主键创建 → 一条成功其余 409；保存表单时 autoSequence 字段显示「保存后生成」，保存后显示后端分配值。

- [ ] **Step 4: 全量回归**

Run: `cd server ; $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ; python -m pytest -q` → 全绿。
Run: `npx vitest run src/` → 全绿。
Run: `npx vue-tsc --noEmit` → 干净。

- [ ] **Step 5: 提交（若有审计修复）**

```bash
git add -A
git commit -m "fix(concurrency): 批量/导入创建路径序列安全收口 + 集成验证"
```
（无改动则跳过。）

---

## Self-Review（作者自检）

**Spec 覆盖：**
- ① autoSequence 后端原子分配（计数表 + 行锁 + 播种 + 批量区间）→ Task 1（表/播种）+ Task 2（allocate）+ Task 3（接入 create）+ Task 5（前端停生成/只读），✓
- ② 手填主键 advisory lock 去竞态 → Task 3（`acquire_pk_lock` 接入 create/update），✓
- ③ 更新乐观锁全路径契约 + 统一 409 UX → Task 6，✓
- ④ READ COMMITTED + 定点锁 → Task 2（FOR UPDATE 行锁）+ Task 3（advisory lock），未升隔离级别，✓
- ⑤ 还原后重播种 → Task 4，✓
- 迁移 → Task 1；测试策略（并发无重号/无重复主键/播种/还原/409）→ Tasks 2/3/1/4/6 对应，✓
- 批量/导入覆盖 → Task 7 Step 1 审计收口，✓

**占位符扫描：** Task 5 Step 4 的测试含一个显式「占位断言」并配指令要求实现时替换为 (a) spy 断言——已明确「不得留占位」。其余步骤含完整代码。Task 4 Step 3 / Task 6 Step 5 标注「按实际变量名调整 / 审计」是**核对指令**（指明读哪个文件、改哪段、判据），非占位。

**类型/命名一致性：** `dynamic_sequences`(表)、`seq_max_from_data`/`reseed_sequences`/`allocate_sequence`（`utils/sequences.py`）、`acquire_pk_lock`（`dynamic.py`）、`isVersionConflict`/`conflictMessage`（`conflict.ts`）在各 Task 引用一致；`allocate_sequence` 签名 `(cur, collection, branch_id, field_name, prefix, pad, count=1)` 在 Task 2 定义、Task 3/4 调用一致；播种 `GREATEST` 语义在 Task 1/4 一致。

**潜在风险（实现留意）：**
- Task 3 改 create_item 时 `get_page_info`/`get_primary_key_fields` 的调用去重，避免重复查询；保持原 validation/relations/webhook 顺序。
- Task 4 还原接入点的变量名（`tables`/`collection_filters`/游标/提交点）需读 `restore_backup` 主体确认。
- `dynamic_sequences` 表需进入 `init_db.py` 的 DDL 批（Task 1 Step 3）以保证全新库自带该表；迁移脚本对既有库补建 + 播种。
