# 跨页可自定义工作流引擎（MVP）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让管理员把现有数据页编排成一条命名的线性跨页流程——记录推进时按字段映射在下一页生成下游记录并通知下一阶段角色，支持简单回退，按角色聚合待办。

**Architecture:** 新增 `workflow_engine`（类比 `trigger_engine`），挂在 `update_item` 状态转换之后；唯一真相源 = `workflow_definitions`（有序阶段）+ `workflow_instances`（递传链实例）。下游记录生成复用 `allocate_sequence`/`acquire_pk_lock`/`data_relations`，通知复用 `notifier`，合法性复用现有 `workflow.py` 状态机。前端：设计器纳入设置中心、按角色待办收件箱、推进/驳回意见对话框。

**Tech Stack:** Python + psycopg2 + PostgreSQL（后端）；Vue 3 + TS + Pinia + Element Plus + Vitest（前端）；pytest。

设计依据：`docs/superpowers/specs/2026-06-14-cross-page-workflow-engine-design.md`

---

## 既有锚点（实现前必读）

- **状态机** `server/utils/workflow.py`：`get_allowed_transitions(fields, field, current_status, role)`、`validate_transition(...)`、`get_workflow_config(fields, field)`。`update_item`（`routes/dynamic.py:687`）已对状态字段做转换校验。
- **触发器建记录范式** `server/utils/trigger_engine.py`：`_execute_action` 的 `create` 分支做**裸 INSERT（不分配 autoSequence）+ reseed**（`:107-120`）；`_resolve_value(expr, source_data, source_id, operator)`（`:154`）解析 `$source.<field>`/`$operator`/`$NOW`。**工作流 spawn 不能照搬裸 INSERT**——下游记录需分配 autoSequence，故复用 `allocate_sequence`/`acquire_pk_lock`。
- **并发原语**（子系统 A 已加）`server/routes/dynamic.py`：`acquire_pk_lock(cur, collection, pk_values)`；`get_primary_key_fields(cur, collection)`；`get_page_info(cur, collection)→(name, fields)`。`server/utils/sequences.py`：`allocate_sequence(cur, collection, branch_id, field, prefix, pad, count=1)`、`reseed_sequences`。
- **通知** `server/utils/notifier.py`：`create_notification(user_id, ntype, title, content=None, source_collection=None, source_record_id=None)`（自开连接，独立事务）。按角色取用户：`SELECT id FROM users WHERE role = %s`（notifier 内已有此范式，`:61/80/99`）。
- **DB** `dynamic_data`（id, collection, data jsonb, branch_id, version）；`page_configs.fields`（含 `workflowConfig`）；`data_relations`（M:N，`(collection, record_id, field_name, related_collection, related_id)`）；`users(id, role)`；`notifications`。
- **蓝图注册** `server/app.py`：admin 蓝图在 `dynamic_bp`（catch-all）之前注册。
- **权限目录** `server/utils/permissions.py` `PERMISSION_CATALOG`（`:11`，`{'key','label','group'}` 列表）。
- **设置中心目录**（子系统设置中心已建）`src/views/admin/hub/settingsCatalog.ts`（`SETTINGS_CATALOG` 分类×tab，`{id,label,perm}`）+ `settingsComponents.ts`（tab id→异步组件）。新设计器作为一个 tab 接入。
- **数据页转换 UI** `src/views/dynamic/DynamicPage.vue`：`handleWorkflowTransition`（推进按钮已存在）；要扩展为带意见对话框 + 驳回。

> **占位符约定**：spec 用 `$src.`，本计划统一对齐代码库现有 `$source.` 约定以复用解析逻辑。

---

## Task 1: 两表 DDL + 迁移

**Files:**
- Modify: `server/init_db.py`（DDL 批）
- Create: `server/migrations/2026_06_14_workflow_tables.py`
- Test: `server/tests/test_workflow_tables.py`

- [ ] **Step 1: 写失败测试**

`server/tests/test_workflow_tables.py`:
```python
import importlib
mig = importlib.import_module("migrations.2026_06_14_workflow_tables")


def test_migration_creates_tables(db_conn):
    mig.run()
    mig.run()  # 幂等
    with db_conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.workflow_definitions')")
        assert cur.fetchone()[0] is not None
        cur.execute("SELECT to_regclass('public.workflow_instances')")
        assert cur.fetchone()[0] is not None
```

- [ ] **Step 2: 运行确认失败**

Run: `cd server ; $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ; python -m pytest tests/test_workflow_tables.py -v`
Expected: FAIL（迁移模块不存在 / 表不存在）。

- [ ] **Step 3: init_db.py 追加 DDL**

在 `server/init_db.py` 的 DDL 批中（紧随其它表）追加：
```sql
CREATE TABLE IF NOT EXISTS workflow_definitions (
    id          VARCHAR(100) PRIMARY KEY,
    name        VARCHAR(200) NOT NULL,
    description TEXT,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    stages      JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS workflow_instances (
    id               VARCHAR(100) PRIMARY KEY,
    workflow_id      VARCHAR(100) NOT NULL,
    status           VARCHAR(20) NOT NULL DEFAULT 'running',
    current_stage_id VARCHAR(100),
    chain            JSONB NOT NULL DEFAULT '[]'::jsonb,
    history          JSONB NOT NULL DEFAULT '[]'::jsonb,
    started_at       TIMESTAMPTZ DEFAULT NOW(),
    started_by       VARCHAR(100),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_wf_inst_status ON workflow_instances(status);
CREATE INDEX IF NOT EXISTS idx_wf_inst_current ON workflow_instances(current_stage_id);
CREATE INDEX IF NOT EXISTS idx_wf_inst_workflow ON workflow_instances(workflow_id);
```

- [ ] **Step 4: 迁移脚本**

`server/migrations/2026_06_14_workflow_tables.py`:
```python
"""幂等迁移：创建 workflow_definitions / workflow_instances 表。
用法（server/ 下）：python -m migrations.2026_06_14_workflow_tables"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db

# DDL 与 init_db.py 中的 workflow_* 保持同步
DDL = """
CREATE TABLE IF NOT EXISTS workflow_definitions (
    id VARCHAR(100) PRIMARY KEY, name VARCHAR(200) NOT NULL, description TEXT,
    enabled BOOLEAN NOT NULL DEFAULT TRUE, stages JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS workflow_instances (
    id VARCHAR(100) PRIMARY KEY, workflow_id VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'running', current_stage_id VARCHAR(100),
    chain JSONB NOT NULL DEFAULT '[]'::jsonb, history JSONB NOT NULL DEFAULT '[]'::jsonb,
    started_at TIMESTAMPTZ DEFAULT NOW(), started_by VARCHAR(100), updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_wf_inst_status ON workflow_instances(status);
CREATE INDEX IF NOT EXISTS idx_wf_inst_current ON workflow_instances(current_stage_id);
CREATE INDEX IF NOT EXISTS idx_wf_inst_workflow ON workflow_instances(workflow_id);
"""


def run():
    with get_db() as conn:
        conn.cursor().execute(DDL)
        conn.commit()
    return {"status": "ok"}


if __name__ == "__main__":
    print(run())
```

- [ ] **Step 5: 应用 + 运行测试 + 提交**

Run: `cd server ; python -m migrations.2026_06_14_workflow_tables` → `{'status': 'ok'}`。
Run: `cd server ; $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ; python -m pytest tests/test_workflow_tables.py -v` → PASS。
```bash
git add server/init_db.py server/migrations/2026_06_14_workflow_tables.py server/tests/test_workflow_tables.py
git commit -m "feat(workflow): workflow_definitions/instances 两表 + 幂等迁移"
```

---

## Task 2: 工作流仓储层 workflow_repo

**Files:**
- Create: `server/utils/workflow_repo.py`
- Test: `server/tests/test_workflow_repo.py`

纯数据访问，便于上层引擎/路由复用与单测。

- [ ] **Step 1: 写失败测试**

`server/tests/test_workflow_repo.py`:
```python
import psycopg2.extras
from db import get_db
from utils import workflow_repo as repo


def _clean(cur):
    cur.execute("DELETE FROM workflow_instances WHERE id LIKE 'wf-t-%'")
    cur.execute("DELETE FROM workflow_definitions WHERE id LIKE 'wf-t-%'")


def test_definition_crud():
    with get_db() as conn:
        cur = conn.cursor(); _clean(cur); conn.commit()
        repo.save_definition(cur, {'id': 'wf-t-1', 'name': '需求流', 'enabled': True,
                                   'stages': [{'id': 's1', 'name': '评审', 'collection': 'req'}]})
        conn.commit()
        d = repo.get_definition(cur, 'wf-t-1')
        assert d['name'] == '需求流' and d['stages'][0]['id'] == 's1'
        assert any(x['id'] == 'wf-t-1' for x in repo.list_definitions(cur))
        cur.execute("DELETE FROM workflow_definitions WHERE id='wf-t-1'"); conn.commit()


def test_instance_lifecycle():
    with get_db() as conn:
        cur = conn.cursor(); _clean(cur)
        inst = repo.create_instance(cur, 'wf-t-inst', 'wf-t-1', 's1', 'req', 'r1', 'admin')
        conn.commit()
        got = repo.get_instance(cur, 'wf-t-inst')
        assert got['status'] == 'running' and got['current_stage_id'] == 's1'
        assert got['chain'][0]['recordId'] == 'r1'
        # 按 (collection, recordId) 定位运行中实例的当前 chain 末项
        found = repo.find_running_instance_by_record(cur, 'req', 'r1')
        assert found['id'] == 'wf-t-inst'
        _clean(cur); conn.commit()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd server ; $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ; python -m pytest tests/test_workflow_repo.py -v`
Expected: FAIL（`utils.workflow_repo` 不存在）。

- [ ] **Step 3: 实现 `server/utils/workflow_repo.py`**

```python
"""workflow_definitions / workflow_instances 数据访问。调用方负责提交。"""
import json
import uuid
import psycopg2.extras


def save_definition(cur, d):
    """upsert 一个工作流定义。d: {id?, name, description?, enabled, stages:list}"""
    wid = d.get('id') or f'wf-{uuid.uuid4().hex[:12]}'
    cur.execute(
        "INSERT INTO workflow_definitions (id, name, description, enabled, stages, updated_at) "
        "VALUES (%s,%s,%s,%s,%s, NOW()) "
        "ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, description=EXCLUDED.description, "
        "enabled=EXCLUDED.enabled, stages=EXCLUDED.stages, updated_at=NOW()",
        (wid, d['name'], d.get('description'), d.get('enabled', True),
         psycopg2.extras.Json(d.get('stages', []))),
    )
    return wid


def get_definition(cur, wid):
    cur.execute("SELECT id,name,description,enabled,stages FROM workflow_definitions WHERE id=%s", (wid,))
    r = cur.fetchone()
    if not r:
        return None
    return {'id': r[0], 'name': r[1], 'description': r[2], 'enabled': r[3], 'stages': r[4] or []}


def list_definitions(cur):
    cur.execute("SELECT id,name,description,enabled,stages FROM workflow_definitions ORDER BY updated_at DESC")
    return [{'id': r[0], 'name': r[1], 'description': r[2], 'enabled': r[3], 'stages': r[4] or []}
            for r in cur.fetchall()]


def delete_definition(cur, wid):
    cur.execute("DELETE FROM workflow_definitions WHERE id=%s", (wid,))


def create_instance(cur, inst_id, workflow_id, stage_id, collection, record_id, started_by):
    chain = [{'stageId': stage_id, 'collection': collection, 'recordId': record_id,
              'enteredAt': _now(), 'completedBy': None}]
    history = [{'ts': _now(), 'action': 'start', 'stageId': stage_id, 'by': started_by, 'comment': None}]
    cur.execute(
        "INSERT INTO workflow_instances (id, workflow_id, status, current_stage_id, chain, history, started_by) "
        "VALUES (%s,%s,'running',%s,%s,%s,%s)",
        (inst_id, workflow_id, stage_id, psycopg2.extras.Json(chain), psycopg2.extras.Json(history), started_by),
    )
    return get_instance(cur, inst_id)


def get_instance(cur, inst_id, for_update=False):
    sql = "SELECT id,workflow_id,status,current_stage_id,chain,history,started_by FROM workflow_instances WHERE id=%s"
    if for_update:
        sql += " FOR UPDATE"
    cur.execute(sql, (inst_id,))
    r = cur.fetchone()
    if not r:
        return None
    return {'id': r[0], 'workflow_id': r[1], 'status': r[2], 'current_stage_id': r[3],
            'chain': r[4] or [], 'history': r[5] or [], 'started_by': r[6]}


def find_running_instance_by_record(cur, collection, record_id, for_update=False):
    """找到 chain 末项 == (collection, record_id) 的运行中实例。"""
    sql = ("SELECT id FROM workflow_instances WHERE status='running' "
           "AND (chain -> -1 ->> 'collection') = %s AND (chain -> -1 ->> 'recordId') = %s LIMIT 1")
    cur.execute(sql, (collection, record_id))
    r = cur.fetchone()
    if not r:
        return None
    return get_instance(cur, r[0], for_update=for_update)


def update_instance(cur, inst_id, *, status=None, current_stage_id=None, chain=None, history=None):
    sets, params = ['updated_at=NOW()'], []
    if status is not None:
        sets.append('status=%s'); params.append(status)
    if current_stage_id is not None:
        sets.append('current_stage_id=%s'); params.append(current_stage_id)
    if chain is not None:
        sets.append('chain=%s'); params.append(psycopg2.extras.Json(chain))
    if history is not None:
        sets.append('history=%s'); params.append(psycopg2.extras.Json(history))
    params.append(inst_id)
    cur.execute(f"UPDATE workflow_instances SET {', '.join(sets)} WHERE id=%s", params)


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
```
注：`chain -> -1` 取 JSONB 数组末项（PostgreSQL 支持负索引）。

- [ ] **Step 4: 运行 + 提交**

Run: `cd server ; $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ; python -m pytest tests/test_workflow_repo.py -v` → PASS。
```bash
git add server/utils/workflow_repo.py server/tests/test_workflow_repo.py
git commit -m "feat(workflow): workflow_repo 定义/实例数据访问层"
```

---

## Task 3: 下游记录生成 spawn_record

**Files:**
- Create: `server/utils/workflow_engine.py`
- Test: `server/tests/test_workflow_spawn.py`

复用 `allocate_sequence` + `acquire_pk_lock`，按 `fieldMapping`（`$source.`）生成下游记录并建反向关联——**不绕过序列/主键一致性**。

- [ ] **Step 1: 写失败测试**

`server/tests/test_workflow_spawn.py`:
```python
import psycopg2.extras
from db import get_db
from utils.workflow_engine import spawn_record


def _mkpage(cur, coll, with_seq=True):
    fields = []
    if with_seq:
        fields.append({'fieldName': 'code', 'controlType': 'autoSequence',
                       'sequenceConfig': {'prefix': 'DS-', 'max': 999}, 'isPrimaryKey': True})
    fields.append({'fieldName': 'title', 'controlType': 'text'})
    fields.append({'fieldName': 'fromReq', 'controlType': 'text'})
    cur.execute("DELETE FROM page_configs WHERE id=%s", (f'page-{coll}',))
    cur.execute("INSERT INTO page_configs (id,name,fields) VALUES (%s,%s,%s)",
                (f'page-{coll}', coll, psycopg2.extras.Json(fields)))


def test_spawn_allocates_sequence_and_maps_fields():
    coll = 'zzwfdown'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
        cur.execute("DELETE FROM dynamic_sequences WHERE collection=%s", (coll,))
        _mkpage(cur, coll)
        conn.commit()
        src = {'id': 'req-1', 'title': '登录需求'}
        new_id = spawn_record(cur, target_collection=coll, branch_id='main',
                              field_mapping={'title': '$source.title', 'fromReq': '$source.id'},
                              source_data=src, source_id='req-1', operator='admin')
        conn.commit()
        cur.execute("SELECT data FROM dynamic_data WHERE id=%s", (new_id,))
        data = cur.fetchone()[0]
    assert data['title'] == '登录需求'
    assert data['fromReq'] == 'req-1'
    assert data['code'] == 'DS-001'   # 后端原子分配，不是空
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
        cur.execute("DELETE FROM dynamic_sequences WHERE collection=%s", (coll,))
        cur.execute("DELETE FROM page_configs WHERE id=%s", (f'page-{coll}',))
        conn.commit()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd server ; $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ; python -m pytest tests/test_workflow_spawn.py -v`
Expected: FAIL（`utils.workflow_engine` 不存在）。

- [ ] **Step 3: 实现 `spawn_record`（`server/utils/workflow_engine.py`）**

```python
"""跨页工作流引擎：阶段推进/回退 + 下游记录生成 + 实例追踪。
挂在 update_item 状态转换之后。复用 sequences/pk-lock/notifier/workflow_repo。"""
import uuid
import psycopg2.extras
from utils.sequences import allocate_sequence


def _resolve(expr, source_data, source_id, operator):
    """解析 $source.<field> / $source.id / $operator / $NOW / 字面量。"""
    from datetime import datetime, timezone
    if not isinstance(expr, str):
        return expr
    if expr.startswith('$source.'):
        f = expr[len('$source.'):]
        return source_id if f == 'id' else source_data.get(f, '')
    if expr == '$operator':
        return operator
    if expr == '$NOW':
        return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    return expr


def spawn_record(cur, target_collection, branch_id, field_mapping, source_data, source_id, operator):
    """在 target_collection 生成一条下游记录：原子分配 autoSequence + 字段映射 + 主键锁 + 插入。
    返回新记录 id。复用 routes.dynamic 的并发原语。"""
    from routes.dynamic import get_page_info, get_primary_key_fields, acquire_pk_lock
    data = {}
    for tgt, expr in (field_mapping or {}).items():
        data[tgt] = _resolve(expr, source_data, source_id, operator)
    page_name, fields = get_page_info(cur, target_collection)
    # 原子分配 autoSequence 字段
    for fcfg in (fields or []):
        if fcfg.get('controlType') == 'autoSequence':
            cfg = fcfg.get('sequenceConfig') or {}
            prefix = cfg.get('prefix', '')
            pad = len(str(cfg.get('max', 999)))
            data[fcfg['fieldName']] = allocate_sequence(
                cur, target_collection, branch_id, fcfg['fieldName'], prefix, pad, count=1)[0]
    # 手填主键 advisory lock
    autoseq = {f['fieldName'] for f in (fields or []) if f.get('controlType') == 'autoSequence'}
    pk_fields = get_primary_key_fields(cur, target_collection)
    manual_pk = {f: data.get(f) for f in (pk_fields or []) if f not in autoseq}
    acquire_pk_lock(cur, target_collection, manual_pk)
    new_id = f'{target_collection[:8]}-{uuid.uuid4().hex[:12]}'
    cur.execute(
        'INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s,%s,%s,%s)',
        (new_id, target_collection, psycopg2.extras.Json(data), branch_id),
    )
    return new_id
```

- [ ] **Step 4: 运行 + 提交**

Run: `cd server ; $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ; python -m pytest tests/test_workflow_spawn.py -v` → PASS。
```bash
git add server/utils/workflow_engine.py server/tests/test_workflow_spawn.py
git commit -m "feat(workflow): spawn_record 下游记录生成（复用 allocate_sequence + pk lock）"
```

---

## Task 4: 推进 on_transition（advance）

**Files:**
- Modify: `server/utils/workflow_engine.py`
- Test: `server/tests/test_workflow_advance.py`

- [ ] **Step 1: 写失败测试**

`server/tests/test_workflow_advance.py`:
```python
import psycopg2.extras
from db import get_db
from utils import workflow_repo as repo
from utils.workflow_engine import on_transition


def _setup(cur):
    # 两阶段流程：req(评审,待评审→已通过) → design(设计)
    for coll, fields in [
        ('zzreq', [{'fieldName': 'status', 'controlType': 'select'},
                   {'fieldName': 'title', 'controlType': 'text'}]),
        ('zzdesign', [{'fieldName': 'dcode', 'controlType': 'autoSequence',
                       'sequenceConfig': {'prefix': 'D-', 'max': 999}, 'isPrimaryKey': True},
                      {'fieldName': 'title', 'controlType': 'text'},
                      {'fieldName': 'srcReq', 'controlType': 'text'}]),
    ]:
        cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
        cur.execute("DELETE FROM dynamic_sequences WHERE collection=%s", (coll,))
        cur.execute("DELETE FROM page_configs WHERE id=%s", (f'page-{coll}',))
        cur.execute("INSERT INTO page_configs (id,name,fields) VALUES (%s,%s,%s)",
                    (f'page-{coll}', coll, psycopg2.extras.Json(fields)))
    wf = {'id': 'wf-adv', 'name': 'demo', 'enabled': True, 'stages': [
        {'id': 's1', 'name': '评审', 'collection': 'zzreq', 'statusField': 'status',
         'advanceTransition': {'from': '待评审', 'to': '已通过'}, 'assignedRoles': ['admin'],
         'spawn': {'fieldMapping': {'title': '$source.title', 'srcReq': '$source.id'}, 'linkBackField': 'srcReq'}},
        {'id': 's2', 'name': '设计', 'collection': 'zzdesign', 'statusField': 'dstatus',
         'advanceTransition': {'from': '设计中', 'to': '完成'}, 'assignedRoles': ['admin']},
    ]}
    repo.save_definition(cur, wf)
    cur.execute("DELETE FROM workflow_instances WHERE id='inst-adv'")
    cur.execute("INSERT INTO dynamic_data (id,collection,data,branch_id) VALUES ('req-a','zzreq',%s,'main')",
                (psycopg2.extras.Json({'status': '待评审', 'title': '登录'}),))
    repo.create_instance(cur, 'inst-adv', 'wf-adv', 's1', 'zzreq', 'req-a', 'admin')


def test_advance_spawns_downstream_and_moves_instance():
    with get_db() as conn:
        cur = conn.cursor(); _setup(cur); conn.commit()
        # 评审通过：status 待评审→已通过
        on_transition(cur, collection='zzreq', record_id='req-a', status_field='status',
                      from_value='待评审', to_value='已通过',
                      old_data={'status': '待评审', 'title': '登录'},
                      new_data={'status': '已通过', 'title': '登录'}, operator='admin', role='admin')
        conn.commit()
        inst = repo.get_instance(cur, 'inst-adv')
        assert inst['current_stage_id'] == 's2'
        assert len(inst['chain']) == 2
        down_id = inst['chain'][1]['recordId']
        cur.execute("SELECT data FROM dynamic_data WHERE id=%s", (down_id,))
        d = cur.fetchone()[0]
        assert d['title'] == '登录' and d['srcReq'] == 'req-a'
        assert d['dcode'] == 'D-001'  # 下游 autoSequence 已分配
```

- [ ] **Step 2: 运行确认失败**

Run: `cd server ; $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ; python -m pytest tests/test_workflow_advance.py -v`
Expected: FAIL（`on_transition` 未定义）。

- [ ] **Step 3: 实现 `on_transition`（advance 路径，追加到 `workflow_engine.py`）**

```python
def _stage_index(stages, stage_id):
    for i, s in enumerate(stages):
        if s['id'] == stage_id:
            return i
    return -1


def _notify_roles(cur, roles, title, content, collection, record_id):
    """通知拥有 roles 之一的所有用户（create_notification 自开连接，独立事务）。"""
    if not roles:
        return
    from utils.notifier import create_notification
    cur.execute("SELECT id FROM users WHERE role = ANY(%s)", (list(roles),))
    for (uid,) in cur.fetchall():
        create_notification(uid, 'workflow', title, content, collection, record_id)


def on_transition(cur, collection, record_id, status_field, from_value, to_value,
                  old_data, new_data, operator, role, comment=None):
    """update_item 状态转换成功后调用。匹配当前实例阶段的 advance/reject 转换并编排。
    无匹配实例则静默返回（普通状态变更不受影响）。"""
    from utils import workflow_repo as repo
    inst = repo.find_running_instance_by_record(cur, collection, record_id, for_update=True)
    if not inst:
        return
    definition = repo.get_definition(cur, inst['workflow_id'])
    if not definition or not definition.get('enabled'):
        return
    stages = definition['stages']
    idx = _stage_index(stages, inst['current_stage_id'])
    if idx < 0:
        return
    stage = stages[idx]
    if stage.get('statusField') != status_field:
        return
    adv = stage.get('advanceTransition') or {}
    rej = stage.get('rejectTransition') or {}

    # 阶段办理角色门禁
    if stage.get('assignedRoles') and role not in stage['assignedRoles']:
        # 不属于本阶段办理角色 → 不编排（update_item 的转换本身另有 roles 门禁）
        return

    chain = inst['chain']; history = inst['history']
    if adv and from_value == adv.get('from') and to_value == adv.get('to'):
        chain[-1]['completedBy'] = operator
        history.append({'ts': repo._now(), 'action': 'advance', 'stageId': stage['id'],
                        'by': operator, 'comment': comment})
        if idx + 1 < len(stages):
            nxt = stages[idx + 1]
            spawn = stage.get('spawn') or {}
            down_id = spawn_record(cur, target_collection=nxt['collection'], branch_id='main',
                                   field_mapping=spawn.get('fieldMapping', {}),
                                   source_data=new_data, source_id=record_id, operator=operator)
            chain.append({'stageId': nxt['id'], 'collection': nxt['collection'], 'recordId': down_id,
                          'enteredAt': repo._now(), 'completedBy': None})
            repo.update_instance(cur, inst['id'], current_stage_id=nxt['id'], chain=chain, history=history)
            _notify_roles(cur, nxt.get('assignedRoles', []), f'工作流待办：{nxt["name"]}',
                          f'{operator} 推进了流程，请处理「{nxt["name"]}」', nxt['collection'], down_id)
        else:
            history.append({'ts': repo._now(), 'action': 'complete', 'stageId': stage['id'],
                            'by': operator, 'comment': None})
            repo.update_instance(cur, inst['id'], status='completed', chain=chain, history=history)
        return
    # reject 在 Task 5 实现
```

- [ ] **Step 4: 运行 + 提交**

Run: `cd server ; $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ; python -m pytest tests/test_workflow_advance.py tests/test_workflow_spawn.py -v` → PASS。
```bash
git add server/utils/workflow_engine.py server/tests/test_workflow_advance.py
git commit -m "feat(workflow): on_transition 推进——生成下游记录、前移实例、通知下阶段角色"
```

---

## Task 5: 回退 reject + 并发保护

**Files:**
- Modify: `server/utils/workflow_engine.py`
- Test: `server/tests/test_workflow_reject.py`

- [ ] **Step 1: 写失败测试**

`server/tests/test_workflow_reject.py`:
```python
import psycopg2.extras
from db import get_db
from utils import workflow_repo as repo
from utils.workflow_engine import on_transition
from tests.test_workflow_advance import _setup  # 复用两阶段 setup


def test_reject_moves_instance_back_and_resets_upstream():
    with get_db() as conn:
        cur = conn.cursor(); _setup(cur)
        # 给 s2 配 rejectTransition 并把实例推进到 s2
        d = repo.get_definition(cur, 'wf-adv')
        d['stages'][1]['rejectTransition'] = {'from': '设计中', 'to': '退回'}
        d['stages'][1]['statusField'] = 'dstatus'
        repo.save_definition(cur, d)
        # 先推进到 s2
        on_transition(cur, 'zzreq', 'req-a', 'status', '待评审', '已通过',
                      {'status': '待评审', 'title': '登录'}, {'status': '已通过', 'title': '登录'}, 'admin', 'admin')
        conn.commit()
        inst = repo.get_instance(cur, 'inst-adv'); down_id = inst['chain'][1]['recordId']
        # s2 驳回：dstatus 设计中→退回
        on_transition(cur, 'zzdesign', down_id, 'dstatus', '设计中', '退回',
                      {'dstatus': '设计中'}, {'dstatus': '退回'}, 'admin', 'admin', comment='需补充')
        conn.commit()
        inst2 = repo.get_instance(cur, 'inst-adv')
    assert inst2['current_stage_id'] == 's1'                      # 回到上一阶段
    assert any(h['action'] == 'reject' and h['comment'] == '需补充' for h in inst2['history'])
```

- [ ] **Step 2: 运行确认失败**

Run: `cd server ; $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ; python -m pytest tests/test_workflow_reject.py -v`
Expected: FAIL（reject 未实现，`current_stage_id` 仍是 s2）。

- [ ] **Step 3: 实现 reject 分支（替换 `on_transition` 末尾的 `# reject 在 Task 5 实现` 注释）**

```python
    if rej and from_value == rej.get('from') and to_value == rej.get('to'):
        if idx == 0:
            return  # 首阶段无可退回
        prev = stages[idx - 1]
        history.append({'ts': repo._now(), 'action': 'reject', 'stageId': stage['id'],
                        'by': operator, 'comment': comment})
        # 实例指针回退到 chain 中上一阶段项（复用既有上游记录）
        prev_entry = None
        for entry in reversed(chain[:-1]):
            if entry['stageId'] == prev['id']:
                prev_entry = entry
                break
        if prev_entry is None:
            return
        prev_entry['completedBy'] = None
        # 上游记录状态复位到其阶段的"待办值"（advanceTransition.from），可重新办理
        prev_adv = (prev.get('advanceTransition') or {})
        reset_to = prev_adv.get('from')
        if reset_to and prev.get('statusField'):
            cur.execute("SELECT data FROM dynamic_data WHERE collection=%s AND id=%s AND branch_id='main'",
                        (prev_entry['collection'], prev_entry['recordId']))
            row = cur.fetchone()
            if row:
                pdata = row[0] or {}
                pdata[prev['statusField']] = reset_to
                pdata['_rejectComment'] = comment
                cur.execute("UPDATE dynamic_data SET data=%s, updated_at=NOW() WHERE collection=%s AND id=%s AND branch_id='main'",
                            (psycopg2.extras.Json(pdata), prev_entry['collection'], prev_entry['recordId']))
        repo.update_instance(cur, inst['id'], current_stage_id=prev['id'], chain=chain, history=history)
        _notify_roles(cur, prev.get('assignedRoles', []), f'工作流驳回：{prev["name"]}',
                      f'{operator} 驳回到「{prev["name"]}」：{comment or ""}', prev_entry['collection'], prev_entry['recordId'])
        return
```
并发保护：`on_transition` 已用 `find_running_instance_by_record(..., for_update=True)` 对实例行 `FOR UPDATE`，同一实例并发推进/回退被串行化（后到者读到已变更的 `current_stage_id`，转换不再匹配 → 不重复编排）。

- [ ] **Step 4: 运行 + 提交**

Run: `cd server ; $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ; python -m pytest tests/test_workflow_reject.py tests/test_workflow_advance.py -v` → PASS。
```bash
git add server/utils/workflow_engine.py server/tests/test_workflow_reject.py
git commit -m "feat(workflow): on_transition 回退——退回上一阶段、复位上游、通知 + FOR UPDATE 并发保护"
```

---

## Task 6: 接入 update_item + 启动 API + REST + 权限

**Files:**
- Modify: `server/routes/dynamic.py`（update_item 调用 on_transition）
- Create: `server/routes/workflows.py`（蓝图）
- Modify: `server/app.py`（注册蓝图）
- Modify: `server/utils/permissions.py`（`admin.workflows`）
- Test: `server/tests/test_routes_workflows.py`

- [ ] **Step 1: 接入 update_item**

在 `update_item`（`routes/dynamic.py`）状态转换 + `execute_actions` 之后、UPDATE 成功之后、同一事务内，对每个发生变化的、带 `workflowConfig` 的状态字段调用引擎。在工作流校验循环（`for field_cfg in (fields or []): wf = field_cfg.get('workflowConfig') ...`）内，紧随 `execute_actions(...)` 之后加：
```python
                    try:
                        from utils.workflow_engine import on_transition
                        on_transition(cur, collection, item_id, field_cfg['fieldName'],
                                      old_val, new_val, old_data, merged_data, 
                                      getattr(flask_g, 'current_user', {}).get('username', ''),
                                      user_role, comment=body.get('_workflowComment'))
                    except Exception:
                        import logging; logging.exception('workflow on_transition failed')
```
（`user_role` 在该循环上文已取；`body` 是 `request.get_json`。引擎自身对无匹配实例静默返回，不影响普通更新。失败不阻断更新——记录日志。）

- [ ] **Step 2: 启动实例 + REST 蓝图 `server/routes/workflows.py`**

```python
"""工作流定义 CRUD + 实例（启动/列表/详情）+ 收件箱。"""
import uuid
from flask import Blueprint, request, jsonify, g as flask_g
from db import get_db
from auth import login_required, write_required, require_permission  # 装饰器在 server/auth.py（非 utils/auth.py）
from utils import workflow_repo as repo

workflows_bp = Blueprint('workflows', __name__)


@workflows_bp.route('/workflow/definitions', methods=['GET'])
@login_required
def list_defs():
    with get_db() as conn:
        return jsonify(repo.list_definitions(conn.cursor()))


@workflows_bp.route('/workflow/definitions', methods=['POST'])
@require_permission('admin.workflows')
def save_def():
    body = request.get_json(force=True)
    with get_db() as conn:
        cur = conn.cursor()
        wid = repo.save_definition(cur, body)
        conn.commit()
        return jsonify(repo.get_definition(cur, wid)), 200


@workflows_bp.route('/workflow/definitions/<wid>', methods=['DELETE'])
@require_permission('admin.workflows')
def delete_def(wid):
    with get_db() as conn:
        repo.delete_definition(conn.cursor(), wid); conn.commit()
    return jsonify({'ok': True})


@workflows_bp.route('/workflow/instances', methods=['POST'])
@write_required
def start_instance():
    body = request.get_json(force=True)
    workflow_id = body['workflowId']; collection = body['collection']; record_id = body['recordId']
    user = getattr(flask_g, 'current_user', {})
    with get_db() as conn:
        cur = conn.cursor()
        d = repo.get_definition(cur, workflow_id)
        if not d or not d.get('stages'):
            return jsonify({'error': '工作流不存在或无阶段'}), 404
        first = d['stages'][0]
        inst_id = f'wfi-{uuid.uuid4().hex[:12]}'
        inst = repo.create_instance(cur, inst_id, workflow_id, first['id'], collection, record_id,
                                    user.get('username', ''))
        conn.commit()
        from utils.workflow_engine import _notify_roles
        with get_db() as c2:
            _notify_roles(c2.cursor(), first.get('assignedRoles', []), f'工作流待办：{first["name"]}',
                          '新流程已启动', collection, record_id)
            c2.commit()
        return jsonify(inst), 201


@workflows_bp.route('/workflow/instances', methods=['GET'])
@login_required
def list_instances():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id,workflow_id,status,current_stage_id,chain,history,started_by "
                    "FROM workflow_instances ORDER BY updated_at DESC LIMIT 200")
        out = [{'id': r[0], 'workflowId': r[1], 'status': r[2], 'currentStageId': r[3],
                'chain': r[4] or [], 'history': r[5] or [], 'startedBy': r[6]} for r in cur.fetchall()]
    return jsonify(out)


@workflows_bp.route('/workflow/inbox', methods=['GET'])
@login_required
def inbox():
    """当前用户角色匹配的运行中实例当前阶段待办。"""
    user = getattr(flask_g, 'current_user', {})
    role = user.get('role', '')
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT i.id, i.workflow_id, i.current_stage_id, i.chain, d.name, d.stages "
                    "FROM workflow_instances i JOIN workflow_definitions d ON i.workflow_id=d.id "
                    "WHERE i.status='running'")
        items = []
        for iid, wid, csid, chain, wname, stages in cur.fetchall():
            stage = next((s for s in (stages or []) if s['id'] == csid), None)
            if not stage:
                continue
            roles = stage.get('assignedRoles', [])
            if roles and role not in roles:
                continue
            cur_entry = (chain or [])[-1] if chain else None
            items.append({'instanceId': iid, 'workflowName': wname, 'stageName': stage.get('name'),
                          'collection': cur_entry and cur_entry['collection'],
                          'recordId': cur_entry and cur_entry['recordId'],
                          'enteredAt': cur_entry and cur_entry.get('enteredAt')})
    return jsonify(items)
```

- [ ] **Step 3: 注册蓝图 + 权限 key**

`server/app.py`：在 `dynamic_bp` 注册之前，加入：
```python
from routes.workflows import workflows_bp
app.register_blueprint(workflows_bp)
```
`server/utils/permissions.py` `PERMISSION_CATALOG` 追加一项：
```python
    {'key': 'admin.workflows', 'label': '工作流', 'group': '平台管理'},
```

- [ ] **Step 4: 写测试 `server/tests/test_routes_workflows.py`**

```python
import psycopg2.extras
from db import get_db


def _admin_headers():
    from auth import create_token  # server/auth.py
    return {'Authorization': 'Bearer ' + create_token({'id': 'admin', 'username': 'admin', 'role': 'admin'})}


def test_definition_crud_via_api(client):
    h = _admin_headers()
    body = {'id': 'wf-api-1', 'name': '流A', 'enabled': True,
            'stages': [{'id': 's1', 'name': '评审', 'collection': 'req'}]}
    r = client.post('/workflow/definitions', json=body, headers=h)
    assert r.status_code == 200, r.get_data(as_text=True)
    r = client.get('/workflow/definitions', headers=h)
    assert any(x['id'] == 'wf-api-1' for x in r.get_json())
    r = client.delete('/workflow/definitions/wf-api-1', headers=h)
    assert r.status_code == 200


def test_inbox_filters_by_role(client):
    h = _admin_headers()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM workflow_definitions WHERE id='wf-inbox'")
        cur.execute("DELETE FROM workflow_instances WHERE id='wfi-inbox'")
        cur.execute("INSERT INTO workflow_definitions (id,name,stages) VALUES ('wf-inbox','x',%s)",
                    (psycopg2.extras.Json([{'id': 's1', 'name': '评审', 'collection': 'req', 'assignedRoles': ['admin']}]),))
        cur.execute("INSERT INTO workflow_instances (id,workflow_id,status,current_stage_id,chain) "
                    "VALUES ('wfi-inbox','wf-inbox','running','s1',%s)",
                    (psycopg2.extras.Json([{'stageId': 's1', 'collection': 'req', 'recordId': 'r9'}]),))
        conn.commit()
    r = client.get('/workflow/inbox', headers=h)
    assert any(x['instanceId'] == 'wfi-inbox' for x in r.get_json())
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM workflow_instances WHERE id='wfi-inbox'")
        cur.execute("DELETE FROM workflow_definitions WHERE id='wf-inbox'"); conn.commit()
```
（READ `server/tests/conftest.py` 确认 `client` fixture + `create_token` 用法，按既有 admin 测试范式对齐——若 `client` 的 app fixture mock 了 DB，参考 `test_bypass_reseed.py` 的 real-client 重绑定范式。）

- [ ] **Step 5: 运行 + 全量回归 + 提交**

Run: `cd server ; $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ; python -m pytest tests/test_routes_workflows.py -v` → PASS。
Run: `cd server ; $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ; python -m pytest -q` → 全绿。
```bash
git add server/routes/dynamic.py server/routes/workflows.py server/app.py server/utils/permissions.py server/tests/test_routes_workflows.py
git commit -m "feat(workflow): 接入 update_item + 启动/CRUD/收件箱 REST + admin.workflows 权限"
```

---

## Task 7: 前端——类型/API/Store + 流程设计器

**Files:**
- Create: `src/types/workflow.ts`, `src/api/workflow.ts`, `src/stores/workflow.ts`
- Create: `src/views/admin/WorkflowManager.vue`
- Modify: `src/views/admin/hub/settingsCatalog.ts` + `settingsComponents.ts`（接入设置中心 tab）
- Test: `src/stores/__tests__/workflow.test.ts`

- [ ] **Step 1: 类型 `src/types/workflow.ts`**
```ts
export interface WorkflowStage {
  id: string; name: string; collection: string; statusField?: string
  advanceTransition?: { from: string; to: string }
  rejectTransition?: { from: string; to: string }
  assignedRoles?: string[]
  spawn?: { fieldMapping: Record<string, string>; linkBackField?: string }
}
export interface WorkflowDefinition {
  id?: string; name: string; description?: string; enabled: boolean; stages: WorkflowStage[]
}
export interface WorkflowInboxItem {
  instanceId: string; workflowName: string; stageName: string
  collection: string | null; recordId: string | null; enteredAt: string | null
}
```

- [ ] **Step 2: API `src/api/workflow.ts`**
```ts
import { get, post, del } from '@/utils/request'
import type { WorkflowDefinition, WorkflowInboxItem } from '@/types/workflow'

export const listWorkflows = () => get<WorkflowDefinition[]>('/workflow/definitions')
export const saveWorkflow = (d: WorkflowDefinition) => post<WorkflowDefinition>('/workflow/definitions', d)
export const deleteWorkflow = (id: string) => del(`/workflow/definitions/${id}`)
export const getInbox = () => get<WorkflowInboxItem[]>('/workflow/inbox')
export const startWorkflow = (workflowId: string, collection: string, recordId: string) =>
  post('/workflow/instances', { workflowId, collection, recordId })
```
（确认 `src/utils/request.ts` 导出 `del`；若无则用 `request.delete` 等价封装——READ 该文件。）

- [ ] **Step 3: Store `src/stores/workflow.ts`** —— 保存/列表/删除 definitions、inbox 拉取。最小 Pinia store 包裹上面的 API（参照 `src/stores/` 既有 store 范式）。

- [ ] **Step 4: 设计器 `WorkflowManager.vue`**
列表 + 编辑对话框。编辑器：名称/启用 + **有序阶段列表**（`el-table` 或可拖拽列表，增删/上移下移），每阶段：
- `collection` 选择（从菜单/页面配置列表取数据页）；
- `statusField` 选择（拉该 collection 的 `page_configs.fields` 里 controlType∈select/radio 且有 workflowConfig 的字段；可复用 `pageConfigStore` 或新增一个 `GET page_configs` 读取）；
- 推进转换 from/to、可选回退转换 from/to（从该字段 `workflowConfig.transitions` 下拉）；
- 办理角色多选（从 `/roles` 或 auth store 角色列表）；
- spawn 字段映射（key=下一阶段字段，value=`$source.<上游字段>`/`$NOW`/字面量）的键值编辑器 + linkBack 字段。
保存调用 `saveWorkflow`。组件结构参照现有 admin 管理页（如 `TriggerRuleManager.vue`，它也是"规则 + 字段映射"编辑，结构高度相似——READ 它作为蓝本）。

- [ ] **Step 5: 接入设置中心**
`settingsCatalog.ts`：在 `structure`（结构配置）分类的 tabs 末尾加：
```ts
    { id: 'workflows', label: '工作流', perm: 'admin.workflows' },
```
`settingsComponents.ts`：加 `workflows: defineAsyncComponent(() => import('@/views/admin/WorkflowManager.vue')),`。
旧路径重定向非必需（新功能）。

- [ ] **Step 6: 测试 `src/stores/__tests__/workflow.test.ts`**
mock `@/api/workflow`，断言 store 的保存/列表/inbox action 调用对应 API 并更新 state。参照 `src/stores/__tests__/` 既有 store 测试范式。

- [ ] **Step 7: 运行 + 提交**
Run: `npx vitest run src/stores/__tests__/workflow.test.ts` → PASS。`npx vue-tsc --noEmit` → 干净。`npx vitest run src/` → 全绿。
```bash
git add src/types/workflow.ts src/api/workflow.ts src/stores/workflow.ts src/views/admin/WorkflowManager.vue src/views/admin/hub/settingsCatalog.ts src/views/admin/hub/settingsComponents.ts src/stores/__tests__/workflow.test.ts
git commit -m "feat(workflow): 前端设计器 + 类型/API/Store，接入设置中心"
```

---

## Task 8: 前端——待办收件箱 + 推进/驳回意见对话框 + 启动入口

**Files:**
- Create: `src/views/workflow/WorkflowInbox.vue`
- Modify: `src/router/index.ts`（`/workflow/inbox` 路由）
- Modify: `src/components/layout/AppLayout.vue` 或顶栏（收件箱入口 + 角标，可选）
- Modify: `src/views/dynamic/DynamicPage.vue`（`handleWorkflowTransition` 带意见 + 启动工作流入口）
- Test: `src/views/workflow/__tests__/WorkflowInbox.test.ts`

- [ ] **Step 1: 收件箱视图 `WorkflowInbox.vue`** —— 调 `getInbox()`，`el-table` 列出 workflowName/stageName/进入时间，操作列"去处理"按钮 → `router.push('/page/<pageId>?record=<recordId>')`（复用动态页;若动态页不支持 record 定位则跳到该数据页并在 URL 带 record id，由 DynamicPage 读取打开详情——READ DynamicPage 看是否已支持 jump-to-record，此前 `jumpSource` 机制存在，可复用）。

- [ ] **Step 2: 路由** `src/router/index.ts` 布局 children 加：
```ts
      { path: 'workflow/inbox', name: 'WorkflowInbox', component: () => import('@/views/workflow/WorkflowInbox.vue'), meta: { title: '我的待办' } },
```

- [ ] **Step 3: 推进/驳回意见对话框（DynamicPage.vue）**
扩展现有 `handleWorkflowTransition`：点击推进/驳回按钮时弹一个含可选"意见"输入框的对话框，确认后把 `_workflowComment` 一并放进 update 请求体（`updatePageData` 的 record 里加 `_workflowComment`，后端 `update_item` 读取并透传给 `on_transition`）。驳回按钮来自该状态字段 `workflowConfig` 中 to 值匹配某阶段 `rejectTransition` 的转换（沿用 `get_allowed_transitions` 返回的转换，UI 上把"退回/驳回"类转换标红）。

- [ ] **Step 4: 启动工作流入口（DynamicPage.vue）**
在数据页 `操作` 菜单加"启动工作流"项（仅当存在以本 collection 为首阶段的启用工作流）：选择工作流 → 对当前选中/查看的记录调 `startWorkflow(workflowId, collection, recordId)`。最小实现：查看记录详情时提供"启动工作流"按钮。

- [ ] **Step 5: 测试 + 回归 + 提交**
`WorkflowInbox.test.ts`：mock `getInbox` 返回两条，断言渲染行数 + "去处理"跳转。
Run: `npx vitest run src/views/workflow/__tests__/WorkflowInbox.test.ts` → PASS。`npx vue-tsc --noEmit` 干净。`npx vitest run src/` 全绿。
```bash
git add src/views/workflow/ src/router/index.ts src/views/dynamic/DynamicPage.vue src/components/layout/AppLayout.vue
git commit -m "feat(workflow): 待办收件箱 + 推进/驳回意见对话框 + 启动入口"
```

---

## Task 9: 集成验证 + 端到端 demo + 全量回归

**Files:** 无代码改动（除非验证发现问题）

- [ ] **Step 1: 应用迁移 + 起服务**
确认 Task 1 迁移已在 dev 库执行。`npm run dev:all`。

- [ ] **Step 2: 配一个 demo 流程并端到端走**
设置中心 → 工作流 → 新建"需求→设计"两阶段流程(绑两个有 autoSequence + 状态字段的数据页)。在需求页建记录 → 启动工作流 → 评审通过推进 → 确认设计页自动生成下游记录(带分配的编号 + 反向关联) + 设计角色收件箱出现待办 → 在设计页驳回 → 确认实例回到需求阶段、需求记录状态复位、需求角色收到驳回通知。Playwright 截图核对收件箱 + 递传链。

- [ ] **Step 3: 全量回归**
Run: `cd server ; $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ; python -m pytest -q` → 全绿。
Run: `npx vitest run src/` → 全绿。`npx vue-tsc --noEmit` → 干净。

- [ ] **Step 4: 提交(若有微调)**
```bash
git add -A
git commit -m "fix(workflow): 端到端验证微调"
```

---

## Self-Review（作者自检）

**Spec 覆盖：**
- 架构(workflow_engine 挂 update_item 后)→ Task 4/6，✓
- 两表数据模型 → Task 1，✓
- spawn(复用 allocate_sequence/pk lock，不绕过)→ Task 3，✓
- 推进(下游生成/前移/通知/末阶段完成)→ Task 4，✓
- 回退(退回上一阶段/复位上游/通知/FOR UPDATE 并发)→ Task 5，✓
- 收件箱/设计器/启动/REST/权限 → Task 6/7/8，✓
- 复用现有状态机/通知/RBAC/计数器 → 各 Task 明确复用，✓
- 测试策略(推进/回退/角色门禁/计数器一致/CRUD/inbox/并发)→ Task 3-6 + 9，✓

**占位符扫描：** 无 TBD/TODO；后端步骤含完整代码。前端 Task 7/8 的 Vue 组件以"结构 + 蓝本(TriggerRuleManager.vue)+ 关键 API 接线"指引，标注 READ 具体蓝本文件 —— 属"按既有范式落地"指令而非占位（与已验收的设置中心计划同款粒度）。`$src`→`$source` 对齐已注明。

**类型/命名一致性：** `workflow_repo`(save/get/list/delete_definition、create/get/find_running_instance_by_record/update_instance)、`workflow_engine`(spawn_record、on_transition、_notify_roles、_resolve)、表 `workflow_definitions/instances`、权限 `admin.workflows`、前端类型 `WorkflowStage/Definition/InboxItem`、API 名 在各 Task 间一致。`on_transition(cur, collection, record_id, status_field, from_value, to_value, old_data, new_data, operator, role, comment=None)` 签名在 Task 4 定义、Task 6 调用一致。

**潜在风险（实现留意）：**
- Task 6 接入点：`update_item` 工作流校验循环里 `old_data`/`merged_data`/`user_role`/`body` 的实际变量名需 READ 确认；引擎调用须在 UPDATE 成功之后、同一事务内。
- spawn 在 update_item 事务内创建下游记录：下游记录的 create 不再走 create_item 的 webhook/trigger（仅核心写入）——MVP 可接受；若需下游也触发 trigger/webhook，留 v2。
- `chain -> -1` 负索引需 PostgreSQL 12+（本仓库 JSONB 用法已依赖较新版本）。
- 设计器读取目标页字段/转换：复用 `page_configs` 读取(已有 `get_page_info` / 前端 pageConfig store)。
