# 内置 MCP 工具治理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给内置 MCP 的 27 个工具建立「角色×工具矩阵」治理：代码 manifest 分组/读写元数据为真相源，共享库表即时生效，Flask 管理端点+抽屉，会话侧分组展示。

**Architecture:** mcp-server 新增 `tools/registry.py`（分组 manifest，缺标注启动即抛）+ `rbac.py` 升级读矩阵（TTL 3s，DB 失败分层回落）；启动时 `sync_metadata.py` 把元数据快照进 `ai_mcp_tools` 并播种 `ai_mcp_tool_rules` 默认行（ON CONFLICT DO NOTHING 不覆盖管理员选择）。Flask `utils/mcp_tool_rules.py` 提供矩阵读/全量替换，`routes/ai.py` 出 GET `/ai/mcp-tools` + PUT `/ai/mcp-tool-rules`（`admin.ai_settings`，`log_operation` 审计）。前端治理抽屉 + 会话侧分组（`/tools` 端点扩展字段直接流经 `/sessions/:id/mcp` 透传，无需后端 join）。

**Tech Stack:** Python/FastAPI（mcp-server，独立 .venv）+ Flask/psycopg2（server，裸 SQL get_db）+ PostgreSQL 共享库；Vue3 + Element Plus + vitest；Playwright（真实栈）。

**Spec:** `docs/superpowers/specs/2026-10-25-mcp-tool-governance-design.md`（D1-D4 决策 + 十二节设计；本计划 §引用均指该 spec）。

## Global Constraints

- **源码修改必须走 Edit/Write 工具**——Mimosa 钩子拦截 Bash 直写源码/安全配置（sed/echo > file 会被拒）。
- **迁移双注册**：`server/migrations/2026_10_25c_mcp_tool_governance.py` 文件 + `server/app.py` 显式 boot 块（紧随 `2026_10_25b_platform_plugins` 块之后，照抄其样式）。
- **种子语义**：矩阵默认行由 mcp-server 启动同步播种（工具清单单一真相源在 manifest；迁移只建表）——对 spec §4.3 的实现位置说明。`ON CONFLICT DO NOTHING` **永不覆盖管理员选择**。
- **权限点**：新端点统一 `@require_permission('admin.ai_settings')`（与现有 MCP 端点一致，spec §6）。
- **即时生效语义**（spec §5.2/§10）：`call_tool` 每调用校验；`list_tools` 过滤；传播窗口 ≤ TTL 3s。
- **对 spec 的两处实现偏离（语义等价，此处显式记录）**：① §4.1 的 `sort_order`（组内排序）实现为 `group_order`（组排序，组内按名称序）——27 工具组内无自定义排序需求；② §7.2 会话侧分组经 `/tools` 扩展字段**透传**实现（`ai_chat.py list_mcp_services` 本就代理 mcp-server `/tools`，`ai_chat.py:1786-1796`），不做快照表 join。
- **计划期已核实的 spec 事实修正**：§9.1「27 工具中文描述补齐」经逐个检视，26/27 已达标（memory_* 与 data_* 系列均完整），仅 `list_collections` 为英文一行需重写——Task 9 只改这一个。
- 新建 Flask client 测试文件**必须带 `_heal_leaked_get_db_bindings` autouse 夹具**（conftest `app` 夹具的 get_db 绑定污染坑，实现照抄 `server/tests/test_open_api_takeover_link.py:37-59`，本计划 Task 3 给出全文）。
- server 侧真库测试用 `db_conn` 夹具 + 前缀行/快照恢复纪律（照抄 `server/tests/test_mcp_servers.py:10-16` 模式）；矩阵是全局状态，测试用例必须「快照→沙箱→恢复」。
- mcp-server 测试跑在其独立 venv：`cd mcp-server && .venv/Scripts/python.exe -m pytest tests/<file> -v`。
- 每任务：先写失败测试 → 跑挂 → 实现 → 跑绿 → 独立 commit；不提交 `.superpowers/` 与运行日志。
- 重启运维：后端 3002 无自动 reload（改 `server/` 代码后须重启）；mcp-server 3003 重启须**先杀旧进程再拉起**（防双实例）：`cd mcp-server && cmd //c "start /b .venv\Scripts\python.exe main.py > ../mcp-server-3003.log 2>&1"`；E2E 前确认 dev 栈在跑。
- 部署顺序：先重启 3002（跑迁移建表）再重启 3003（快照+播种）；两步之间矩阵为空，走代码默认（admin/developer 放行、guest/kefu-guest 全拒）——窗口内 kefu 受限属预期，spec §10 已定义。

---

### Task 1: 迁移建两表 + app.py 引导块

**Files:**
- Create: `server/migrations/2026_10_25c_mcp_tool_governance.py`
- Modify: `server/app.py`（platform_plugins boot 块之后，约 :624 插入新块）
- Test: `server/tests/test_mcp_tool_governance_migration.py`

**Interfaces:**
- Produces（后续任务依赖）: 表 `ai_mcp_tools(tool_name PK, group_id, group_name, group_order, description, access, updated_at)` 与 `ai_mcp_tool_rules(role, tool_name, allowed, updated_at, PK(role, tool_name))`

- [ ] **Step 1: 写失败测试**（真库幂等：跑两遍，两表都在）

```python
# -*- coding: utf-8 -*-
"""治理两表迁移幂等测试：run() 两遍后 information_schema 可见两表。"""
import importlib.util
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

_MIG = os.path.join(os.path.dirname(__file__), '..', 'migrations',
                    '2026_10_25c_mcp_tool_governance.py')


def _run_migration():
    spec = importlib.util.spec_from_file_location('_mtg_mig_test', _MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.run()


def test_migration_idempotent_creates_both_tables(db_conn):
    _run_migration()
    _run_migration()  # 第二遍不抛 = 幂等
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name IN ('ai_mcp_tools', 'ai_mcp_tool_rules')")
        names = {r[0] for r in cur.fetchall()}
    assert names == {'ai_mcp_tools', 'ai_mcp_tool_rules'}
```

- [ ] **Step 2: 跑挂**

Run: `cd server && python -m pytest tests/test_mcp_tool_governance_migration.py -v`
Expected: FAIL（FileNotFoundError：迁移文件不存在）

- [ ] **Step 3: 写迁移文件**

```python
# -*- coding: utf-8 -*-
"""幂等迁移：内置 MCP 工具治理两表（2026-10-25 spec §4）。

ai_mcp_tools —— 工具元数据快照（mcp-server 启动时 upsert + 清理消失行）；
ai_mcp_tool_rules —— 角色×工具全量矩阵（mcp-server 启动播种默认行，ON
CONFLICT DO NOTHING 不覆盖管理员选择；管理端 PUT 全量替换）。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db


def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS ai_mcp_tools ("
            " tool_name TEXT PRIMARY KEY,"
            " group_id TEXT NOT NULL,"
            " group_name TEXT NOT NULL,"
            " group_order INT NOT NULL DEFAULT 0,"
            " description TEXT NOT NULL DEFAULT '',"
            " access TEXT NOT NULL CHECK (access IN ('read','write')),"
            " updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")
        cur.execute(
            "CREATE TABLE IF NOT EXISTS ai_mcp_tool_rules ("
            " role TEXT NOT NULL,"
            " tool_name TEXT NOT NULL,"
            " allowed BOOLEAN NOT NULL,"
            " updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
            " PRIMARY KEY (role, tool_name))")
        conn.commit()
    print("mcp tool governance tables ready.")


if __name__ == "__main__":
    run()
```

- [ ] **Step 4: 跑绿**

Run: `cd server && python -m pytest tests/test_mcp_tool_governance_migration.py -v`
Expected: PASS（注意：该测试在 casemanage_test 库建表；生产建表走 Task 10 的后端重启）

- [ ] **Step 5: app.py 引导块**（Edit 工具，插在 `platform plugins migration on boot failed` 的 except 块之后、`builtin skills` 注释之前）

```python
# 内置 MCP 工具治理两表（2026-10-25 工具治理 spec）。幂等建表；矩阵默认行
# 由 mcp-server 启动同步播种（工具清单真相源在 mcp-server manifest）。
try:
    import importlib.util as _ilu_mtg
    _mp_mtg = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'migrations', '2026_10_25c_mcp_tool_governance.py')
    _spec_mtg = _ilu_mtg.spec_from_file_location('_mcp_tool_governance_boot', _mp_mtg)
    _m_mtg = _ilu_mtg.module_from_spec(_spec_mtg)
    _spec_mtg.loader.exec_module(_m_mtg)
    _m_mtg.run()
except Exception as _e:
    logging.warning('mcp tool governance migration on boot failed: %s', _e)
```

- [ ] **Step 6: 冒烟 app.py 可导入**

Run: `cd server && python -c "import app"`
Expected: 无异常退出（boot 块执行，迁移在测试库幂等重跑）

- [ ] **Step 7: Commit**

```bash
git add server/migrations/2026_10_25c_mcp_tool_governance.py server/app.py server/tests/test_mcp_tool_governance_migration.py
git commit -m "feat(mcp-gov): 治理两表迁移+app引导块（快照/矩阵，幂等）"
```

---

### Task 2: server/utils/mcp_tool_rules.py（矩阵数据层）

**Files:**
- Create: `server/utils/mcp_tool_rules.py`
- Test: `server/tests/test_mcp_tool_rules.py`

**Interfaces:**
- Consumes: Task 1 的两张表
- Produces（Task 3 路由依赖，签名逐字）:
  - `MCP_TOOL_ROLES: tuple = ('admin', 'developer', 'guest', 'kefu-guest')`
  - `ROLE_LABELS: dict`
  - `class ToolRulesError(ValueError)`
  - `list_tool_governance() -> dict`：`{'groups': [{'id','name','order','tools':[{'name','description','access','rules': {role: bool}}]}], 'roles': [{'role','label'}]}`
  - `replace_tool_rules(rules: list[dict]) -> dict`：入参项 `{'role','tool_name','allowed'}`；返回 `{'updated': int, 'diff': str}`；非法抛 `ToolRulesError`

- [ ] **Step 1: 写失败测试**（真库沙箱：快照恢复矩阵 + 前缀假工具行）

```python
# -*- coding: utf-8 -*-
"""utils/mcp_tool_rules 数据层测试：分组视图/全量替换/校验拒绝/行缺失回默认。

矩阵与快照是全局状态：夹具先快照现有矩阵行 → 清空 → 插入 mtg-test-% 假
工具行构造沙箱；teardown 恢复原矩阵并删假行（casemanage_test 也别留脏）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture(autouse=True)
def _matrix_sandbox(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("SELECT role, tool_name, allowed FROM ai_mcp_tool_rules")
        saved = cur.fetchall()
        cur.execute("DELETE FROM ai_mcp_tool_rules")
        cur.execute("DELETE FROM ai_mcp_tools WHERE tool_name LIKE 'mtg-test-%'")
        cur.executemany(
            "INSERT INTO ai_mcp_tools (tool_name, group_id, group_name, group_order, description, access) "
            "VALUES (%s, 'data-query', '数据查询', 1, '测试工具', %s)",
            [('mtg-test-a', 'read'), ('mtg-test-b', 'write')])
    db_conn.commit()
    yield
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_mcp_tool_rules")
        if saved:
            cur.executemany(
                "INSERT INTO ai_mcp_tool_rules (role, tool_name, allowed) VALUES (%s, %s, %s)",
                saved)
        cur.execute("DELETE FROM ai_mcp_tools WHERE tool_name LIKE 'mtg-test-%'")
    db_conn.commit()


def test_list_groups_shape_and_defaults():
    from utils.mcp_tool_rules import list_tool_governance
    out = list_tool_governance()
    by_tool = {t['name']: t for g in out['groups'] for t in g['tools']}
    assert set(by_tool) == {'mtg-test-a', 'mtg-test-b'}
    g = out['groups'][0]
    assert g['id'] == 'data-query' and g['name'] == '数据查询'
    # 矩阵为空 → 行缺失回代码默认（spec §4.3）：admin/developer True，受限角色 False
    assert by_tool['mtg-test-a']['rules'] == {
        'admin': True, 'developer': True, 'guest': False, 'kefu-guest': False}
    assert by_tool['mtg-test-b']['access'] == 'write'
    assert {'role': 'kefu-guest', 'label': '客服'} in out['roles']


def test_replace_rules_roundtrip_and_diff():
    from utils.mcp_tool_rules import list_tool_governance, replace_tool_rules
    full = [{'role': r, 'tool_name': t, 'allowed': a}
            for t in ('mtg-test-a', 'mtg-test-b')
            for r, a in (('admin', True), ('developer', False),
                         ('guest', True), ('kefu-guest', False))]
    res = replace_tool_rules(full)
    assert res['updated'] == 3  # developer×2 off + guest×1 on（其余等于默认）
    assert 'developer:mtg-test-a off' in res['diff']
    out = list_tool_governance()
    by_tool = {t['name']: t for g in out['groups'] for t in g['tools']}
    assert by_tool['mtg-test-a']['rules']['developer'] is False
    assert by_tool['mtg-test-a']['rules']['guest'] is True


def test_replace_rules_atomic_on_unknown_tool():
    from utils.mcp_tool_rules import replace_tool_rules, ToolRulesError, list_tool_governance
    good = [{'role': 'admin', 'tool_name': 'mtg-test-a', 'allowed': True}]
    bad = good + [{'role': 'admin', 'tool_name': 'no-such-tool', 'allowed': True}]
    with pytest.raises(ToolRulesError):
        replace_tool_rules(bad)
    # 整体回滚：good 行也没落库（仍是空矩阵→默认视图）
    out = list_tool_governance()
    t = [t for g in out['groups'] for t in g['tools'] if t['name'] == 'mtg-test-a'][0]
    assert t['rules']['developer'] is True


def test_replace_rules_validation_errors():
    from utils.mcp_tool_rules import replace_tool_rules, ToolRulesError
    for bad in (
        [],
        [{'role': 'hacker', 'tool_name': 'mtg-test-a', 'allowed': True}],
        [{'role': 'admin', 'tool_name': '', 'allowed': True}],
        [{'role': 'admin', 'tool_name': 'mtg-test-a', 'allowed': 'yes'}],
        [{'role': 'admin', 'tool_name': 'mtg-test-a', 'allowed': True},
         {'role': 'admin', 'tool_name': 'mtg-test-a', 'allowed': False}],  # 重复
        'not-a-list',
    ):
        with pytest.raises(ToolRulesError):
            replace_tool_rules(bad)
```

（写文件时删除本段说明；测试文件以上述最终内容为准，`updated == 3` 的依据：与代码默认不同的行 = developer×2（默认 True 改 False）+ guest×1（默认 False 改 True）。）

- [ ] **Step 2: 跑挂**

Run: `cd server && python -m pytest tests/test_mcp_tool_rules.py -v`
Expected: FAIL（ModuleNotFoundError: utils.mcp_tool_rules）

- [ ] **Step 3: 实现 utils/mcp_tool_rules.py**

```python
# -*- coding: utf-8 -*-
"""内置 MCP 工具治理：矩阵读取/全量替换（Flask 管理端点的数据层）。

矩阵语义见 spec §4-§5：全量行、无例外式隐式语义；行缺失时视图端用代码
默认（admin/developer=True，guest/kefu-guest=False）补齐展示。替换为事务内
DELETE + bulk INSERT，非法载荷整体不落库，返回 diff 摘要供操作日志。"""
from psycopg2.extras import RealDictCursor

from db import get_db

# 与 init_db 内置角色种子对齐（动态自建角色不进矩阵——spec §6）
MCP_TOOL_ROLES = ('admin', 'developer', 'guest', 'kefu-guest')

ROLE_LABELS = {'admin': '管理员', 'developer': '开发', 'guest': '访客', 'kefu-guest': '客服'}

DEFAULT_ALLOWED = {'admin': True, 'developer': True, 'guest': False, 'kefu-guest': False}


class ToolRulesError(ValueError):
    """非法矩阵载荷（未知角色/工具、缺字段、allowed 非布尔、重复行）。"""


def list_tool_governance():
    """分组工具清单 + 矩阵现状（快照表 join 矩阵，行缺失回默认值）。"""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT tool_name, group_id, group_name, group_order, description, access "
                "FROM ai_mcp_tools ORDER BY group_order, group_id, tool_name")
            tools = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT role, tool_name, allowed FROM ai_mcp_tool_rules")
            rows = cur.fetchall()
    rules = {(r['role'], r['tool_name']): r['allowed'] for r in rows}
    groups = {}
    for t in tools:
        g = groups.setdefault(t['group_id'], {'id': t['group_id'], 'name': t['group_name'],
                                              'order': t['group_order'], 'tools': []})
        g['tools'].append({
            'name': t['tool_name'], 'description': t['description'],
            'access': t['access'],
            'rules': {role: rules.get((role, t['tool_name']), DEFAULT_ALLOWED[role])
                      for role in MCP_TOOL_ROLES},
        })
    ordered = sorted(groups.values(), key=lambda g: (g['order'], g['id']))
    return {'groups': ordered,
            'roles': [{'role': r, 'label': ROLE_LABELS[r]} for r in MCP_TOOL_ROLES]}


def replace_tool_rules(rules):
    """全量替换矩阵。rules: [{role, tool_name, allowed}]；未知角色/工具、缺字段、
    allowed 非布尔、重复行 → ToolRulesError（整体不落库）。返回 diff 摘要。"""
    if not isinstance(rules, list) or not rules:
        raise ToolRulesError('rules must be a non-empty list')
    seen = set()
    for item in rules:
        if not isinstance(item, dict):
            raise ToolRulesError('each rule must be an object')
        role, tool, allowed = item.get('role'), item.get('tool_name'), item.get('allowed')
        if role not in MCP_TOOL_ROLES:
            raise ToolRulesError(f'unknown role: {role!r}')
        if not isinstance(tool, str) or not tool:
            raise ToolRulesError(f'bad tool_name: {tool!r}')
        if not isinstance(allowed, bool):
            raise ToolRulesError(f'allowed must be boolean: {allowed!r}')
        if (role, tool) in seen:
            raise ToolRulesError(f'duplicate rule: {role}/{tool}')
        seen.add((role, tool))
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT tool_name FROM ai_mcp_tools")
            known = {r['tool_name'] for r in cur.fetchall()}
            unknown = {t for _, t in seen if t not in known}
            if unknown:
                raise ToolRulesError(
                    'unknown tools (mcp-server 未同步快照?): ' + ', '.join(sorted(unknown)))
            cur.execute("SELECT role, tool_name, allowed FROM ai_mcp_tool_rules")
            old = {(r['role'], r['tool_name']): r['allowed'] for r in cur.fetchall()}
            new = {(i['role'], i['tool_name']): i['allowed'] for i in rules}
            diff = [f'{r}:{t} {"on" if v else "off"}'
                    for k, v in sorted(new.items())
                    if old.get(k, DEFAULT_ALLOWED[k[0]]) != v]
            cur.execute("DELETE FROM ai_mcp_tool_rules")
            cur.executemany(
                "INSERT INTO ai_mcp_tool_rules (role, tool_name, allowed, updated_at) "
                "VALUES (%s, %s, %s, NOW())",
                [(i['role'], i['tool_name'], i['allowed']) for i in rules])
        conn.commit()
    summary = '无变更' if not diff else '; '.join(diff[:10]) + ('…' if len(diff) > 10 else '')
    return {'updated': len(diff), 'diff': summary}
```

注意：`replace_tool_rules` 的 unknown 校验与 DELETE 在同一事务，`get_db` 上下文异常时 rollback（`db.py` 惯例），原子性由其保证。

- [ ] **Step 4: 跑绿**

Run: `cd server && python -m pytest tests/test_mcp_tool_rules.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add server/utils/mcp_tool_rules.py server/tests/test_mcp_tool_rules.py
git commit -m "feat(mcp-gov): 矩阵数据层——分组视图/全量替换/校验原子性"
```

---

### Task 3: Flask 管理端点 GET/PUT + 审计

**Files:**
- Modify: `server/routes/ai.py`（现有 MCP 端点之后，`delete_mcp_server` 函数之后追加；import 区 `from utils.mcp_servers import ...` 附近加 `from utils.mcp_tool_rules import ...`）
- Test: `server/tests/test_routes_mcp_tool_governance.py`

**Interfaces:**
- Consumes: Task 2 的 `list_tool_governance / replace_tool_rules / ToolRulesError`
- Produces:
  - `GET /ai/mcp-tools` → `{'groups': [...], 'roles': [...]}`（200）
  - `PUT /ai/mcp-tool-rules` body `{'rules': [...]}` → `{'updated': n, 'diff': s}`（200）/ 400 `{'error': msg}`
  - 均挂 `admin.ai_settings`；PUT 成功 `log_operation('update', 'ai_mcp_tool_rules', 'matrix', '工具治理矩阵', diff 摘要)`

- [ ] **Step 1: 写失败测试**（app 夹具 + monkeypatch 数据层；含 heal 夹具全文）

```python
# -*- coding: utf-8 -*-
"""治理端点路由测试：权限/200 形状/400 校验/审计落账。数据层整体 monkeypatch
（真库语义归 test_mcp_tool_rules.py）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# 合跑隔离：conftest app 夹具的 get_db 绑定污染（照抄 test_open_api_takeover_link.py）
@pytest.fixture(autouse=True)
def _heal_leaked_get_db_bindings():
    import db as _db

    def _heal():
        real = _db.get_db
        for name, mod in list(sys.modules.items()):
            if mod is None or not (name.startswith(('routes.', 'utils.'))
                                   or name in ('auth', 'app')):
                continue
            for attr in ('get_db', '_default', '_default_get_db'):
                cur = getattr(mod, attr, None)
                if cur is not None and cur is not real and callable(cur):
                    try:
                        setattr(mod, attr, real)
                    except (AttributeError, TypeError):
                        pass
        if hasattr(_db.pool, '_mock_name'):
            _db.pool = None

    _heal()
    yield
    _heal()


FAKE_VIEW = {'groups': [{'id': 'data-query', 'name': '数据查询', 'order': 1, 'tools': [
    {'name': 'mtg-test-a', 'description': 'd', 'access': 'read',
     'rules': {'admin': True, 'developer': True, 'guest': False, 'kefu-guest': False}}]}],
    'roles': [{'role': 'admin', 'label': '管理员'}]}


@pytest.fixture
def patched_repo(monkeypatch):
    import routes.ai as ai_mod
    monkeypatch.setattr(ai_mod, 'list_tool_governance', lambda: FAKE_VIEW)
    monkeypatch.setattr(ai_mod, 'replace_tool_rules',
                        lambda rules: {'updated': 1, 'diff': 'developer:mtg-test-a off'})


def test_get_requires_admin(app, dev_headers):
    resp = app.test_client().get('/ai/mcp-tools', headers=dev_headers)
    assert resp.status_code == 403


def test_get_ok(app, patched_repo, admin_headers):
    resp = app.test_client().get('/ai/mcp-tools', headers=admin_headers)
    assert resp.status_code == 200
    assert resp.get_json()['groups'][0]['id'] == 'data-query'


def test_put_ok_logs_operation(app, patched_repo, admin_headers, monkeypatch):
    logged = {}
    import routes.ai as ai_mod
    monkeypatch.setattr(ai_mod, 'log_operation',
                        lambda *a, **k: logged.update(args=a))
    resp = app.test_client().put(
        '/ai/mcp-tool-rules', headers=admin_headers,
        json={'rules': [{'role': 'developer', 'tool_name': 'mtg-test-a', 'allowed': False}]})
    assert resp.status_code == 200
    assert resp.get_json() == {'updated': 1, 'diff': 'developer:mtg-test-a off'}
    assert logged['args'][0] == 'update'
    assert logged['args'][1] == 'ai_mcp_tool_rules'


def test_put_invalid_rules_400(app, patched_repo, admin_headers, monkeypatch):
    import routes.ai as ai_mod
    from utils.mcp_tool_rules import ToolRulesError
    monkeypatch.setattr(ai_mod, 'replace_tool_rules',
                        lambda rules: (_ for _ in ()).throw(ToolRulesError('unknown role')))
    resp = app.test_client().put(
        '/ai/mcp-tool-rules', headers=admin_headers, json={'rules': [{'role': 'x'}]})
    assert resp.status_code == 400
    assert 'unknown role' in resp.get_json()['error']
```

（`dev_headers` 403 断言依赖 conftest `_reset_and_prime_permission_cache` 已把 developer 初始化为无 admin 权限——既有约定。）

- [ ] **Step 2: 跑挂**

Run: `cd server && python -m pytest tests/test_routes_mcp_tool_governance.py -v`
Expected: FAIL（404：路由不存在）

- [ ] **Step 3: 实现路由**（Edit `server/routes/ai.py`：import 区加一行，`delete_mcp_server` 后加两个函数）

import 区（`from utils.mcp_servers import (...)` 之后）：

```python
from utils.mcp_tool_rules import (
    list_tool_governance, replace_tool_rules, ToolRulesError,
)
```

路由（追加在 `delete_mcp_server` 之后）：

```python
@ai_bp.route('/mcp-tools', methods=['GET'])
@require_permission('admin.ai_settings')
def list_mcp_tools():
    """工具治理视图：分组清单 + 角色×工具矩阵（spec §6）。数据来自快照表
    join 矩阵；mcp-server 未同步时 groups 为空（前端提示未同步）。"""
    return jsonify(list_tool_governance())


@ai_bp.route('/mcp-tool-rules', methods=['PUT'])
@require_permission('admin.ai_settings')
def update_mcp_tool_rules():
    """全量替换矩阵（即时生效：mcp-server TTL 3s 内传播，call 时拒绝）。"""
    body = request.get_json(silent=True) or {}
    try:
        result = replace_tool_rules(body.get('rules'))
    except ToolRulesError as e:
        return jsonify({'error': str(e)}), 400
    log_operation('update', 'ai_mcp_tool_rules', 'matrix', '工具治理矩阵',
                  f"{result['updated']} 项变更: {result['diff']}")
    return jsonify(result)
```

- [ ] **Step 4: 跑绿**

Run: `cd server && python -m pytest tests/test_routes_mcp_tool_governance.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add server/routes/ai.py server/tests/test_routes_mcp_tool_governance.py
git commit -m "feat(mcp-gov): GET /ai/mcp-tools + PUT /ai/mcp-tool-rules（admin.ai_settings+审计）"
```

---

### Task 4: mcp-server registry manifest + 25 模块 META 标注 + 启动校验

**Files:**
- Create: `mcp-server/tools/registry.py`
- Modify: `mcp-server/tools/__init__.py`（校验调用 + `tool_meta()`）
- Modify: 25 个工具模块（各加 import + 一行 register_meta；memory.py 三行）
- Test: `mcp-server/tests/test_registry.py`

**Interfaces:**
- Produces:
  - `GROUPS: dict[str, GroupDef]`，`GroupDef(id, name, order)`（frozen dataclass）
  - `register_meta(name: str, *, group: str, access: str) -> None`（未知 group/access/重复注册 → RuntimeError）
  - `meta_of(name: str) -> tuple[str, str] | None`（(group_id, access)）
  - `validate_registry(tool_names: set[str]) -> None`（缺/多标注 → RuntimeError）
  - `tools.tool_meta() -> list[dict]`：`[{'name','description','group','group_name','group_order','access'}]` 按 (group_order, name) 排序——Task 6 同步与 /tools 端点共用

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""registry manifest 测试：注册校验/全集一致/视图形状。"""
import pytest


def test_every_registered_tool_has_meta():
    from tools import _TOOLS
    from tools.registry import validate_registry
    validate_registry(set(_TOOLS))          # 不抛 = 全集一致
    assert len(_TOOLS) == 27                 # 25 模块，memory 出 3 个（spec §5.1）


def test_register_meta_rejects_bad_group():
    from tools.registry import register_meta
    with pytest.raises(RuntimeError, match='unknown group'):
        register_meta('mtg-x', group='no-such-group', access='read')


def test_register_meta_rejects_bad_access_and_dup():
    from tools.registry import register_meta
    with pytest.raises(RuntimeError, match='bad access'):
        register_meta('mtg-x', group='exec', access='admin')
    register_meta('mtg-y', group='exec', access='read')
    with pytest.raises(RuntimeError, match='twice'):
        register_meta('mtg-y', group='exec', access='write')


def test_validate_mismatch_raises():
    from tools.registry import validate_registry
    with pytest.raises(RuntimeError, match='missing META'):
        validate_registry({'nonexistent-tool'})


def test_tool_meta_shape_and_order():
    from tools import tool_meta
    items = {d['name']: d for d in tool_meta()}
    assert items['query_collection']['group'] == 'data-query'
    assert items['query_collection']['group_name'] == '数据查询'
    assert items['run_python']['access'] == 'write'
    assert items['memory_search']['access'] == 'read'
    orders = [d['group_order'] for d in tool_meta()]
    assert orders == sorted(orders)          # 组序单调
    assert isinstance(items['query_collection']['description'], str)
```

注意：`test_register_meta_rejects_bad_access_and_dup` 会污染全局 `_META`（mtg-y）——在文件末尾补清理夹具：

```python
@pytest.fixture(autouse=True)
def _clean_test_meta():
    yield
    from tools import registry
    registry._META.pop('mtg-y', None)
```

- [ ] **Step 2: 跑挂**

Run: `cd mcp-server && .venv/Scripts/python.exe -m pytest tests/test_registry.py -v`
Expected: FAIL（ModuleNotFoundError: tools.registry）

- [ ] **Step 3: 写 registry.py**

```python
# -*- coding: utf-8 -*-
"""工具元数据 manifest——分组/读写属性的唯一真相源（spec §5.1）。

每个工具模块在 import 时调用 register_meta 自注册；tools/__init__ 构建完
_TOOLS 后调用 validate_registry 校验「工具集 == META 集」，缺标注/多余标注
都拒绝启动。分组与读写属性同时服务于：
- 管理展示（快照表 → GET /ai/mcp-tools）
- rbac 的 DB 失败分层回落（guest → 仅 read 工具，spec §5.2）
"""
from dataclasses import dataclass

VALID_ACCESS = ('read', 'write')


@dataclass(frozen=True)
class GroupDef:
    id: str
    name: str          # 中文显示名
    order: int         # 组排序（快照表 group_order 来源）


GROUPS = {
    'data-query': GroupDef('data-query', '数据查询', 1),
    'data-write': GroupDef('data-write', '数据写入', 2),
    'memory':     GroupDef('memory', '长期记忆', 3),
    'trace':      GroupDef('trace', '轨迹与会话分析', 4),
    'batch':      GroupDef('batch', '批任务观测', 5),
    'graph':      GroupDef('graph', '图谱查询', 6),
    'exec':       GroupDef('exec', '通用执行与产物', 7),
}

# tool_name -> (group_id, access)
_META: dict = {}


def register_meta(name: str, *, group: str, access: str) -> None:
    if group not in GROUPS:
        raise RuntimeError(
            f'tool {name!r} declares unknown group {group!r}; known: {sorted(GROUPS)}')
    if access not in VALID_ACCESS:
        raise RuntimeError(f'tool {name!r} declares bad access {access!r}; expected read|write')
    if name in _META:
        raise RuntimeError(f'tool {name!r} registered meta twice')
    _META[name] = (group, access)


def meta_of(name: str):
    return _META.get(name)


def validate_registry(tool_names: set) -> None:
    missing = tool_names - set(_META)
    extra = set(_META) - tool_names
    if missing or extra:
        raise RuntimeError(
            f'tool/meta registry mismatch — missing META for {sorted(missing)}, '
            f'meta without tool {sorted(extra)}')
```

- [ ] **Step 4: 25 模块标注**（每模块两处 Edit：import 区加 `from tools.registry import register_meta`；`NAME = "..."` 行后加 register_meta 行。分组/读写映射逐字如下——spec §5.1 分组草案）

| 模块文件 | register_meta 调用 |
|---|---|
| list_collections.py | `register_meta(NAME, group='data-query', access='read')` |
| query_collection.py | `register_meta(NAME, group='data-query', access='read')` |
| read_upload.py | `register_meta(NAME, group='data-query', access='read')` |
| read_data_file.py | `register_meta(NAME, group='data-query', access='read')` |
| export_collection_excel.py | `register_meta(NAME, group='data-query', access='read')` |
| download_field_files.py | `register_meta(NAME, group='data-query', access='read')` |
| data_create_records.py | `register_meta(NAME, group='data-write', access='write')` |
| data_update_record.py | `register_meta(NAME, group='data-write', access='write')` |
| data_delete_record.py | `register_meta(NAME, group='data-write', access='write')` |
| data_attach_menu.py | `register_meta(NAME, group='data-write', access='write')` |
| ai_create_data_page.py | `register_meta(NAME, group='data-write', access='write')` |
| memory.py | `register_meta("memory_search", group='memory', access='read')` + `register_meta("memory_add", group='memory', access='write')` + `register_meta("memory_delete", group='memory', access='write')`（字面量名——该模块无单 NAME） |
| analyze_trace.py | `register_meta(NAME, group='trace', access='read')` |
| query_sessions.py | `register_meta(NAME, group='trace', access='read')` |
| batch_children_status.py | `register_meta(NAME, group='batch', access='read')` |
| batch_child_changes.py | `register_meta(NAME, group='batch', access='read')` |
| batch_children_search.py | `register_meta(NAME, group='batch', access='read')` |
| batch_tool_audit.py | `register_meta(NAME, group='batch', access='read')` |
| graph_neighbors.py | `register_meta(NAME, group='graph', access='read')` |
| graph_traverse.py | `register_meta(NAME, group='graph', access='read')` |
| run_python.py | `register_meta(NAME, group='exec', access='write')` |
| save_artifact.py | `register_meta(NAME, group='exec', access='write')` |
| list_export_scripts.py | `register_meta(NAME, group='exec', access='read')` |
| run_export_script.py | `register_meta(NAME, group='exec', access='write')` |
| register_action_check.py | `register_meta(NAME, group='exec', access='write')` |

- [ ] **Step 5: tools/__init__.py 接校验 + tool_meta()**（Edit：文件末尾追加；import 区顶部已有 `from tools import (...)` 子模块导入，`_TOOLS` 构建后追加校验调用）

在 `_TOOLS = {...}` 字典闭合后紧接：

```python
from .registry import validate_registry
validate_registry(set(_TOOLS))
```

文件末尾追加：

```python
def tool_meta() -> list:
    """名称+描述（TOOL spec）+分组/读写元数据（registry）——/tools 端点与
    启动快照同步共用，按 (group_order, name) 排序。"""
    from .registry import GROUPS, meta_of
    out = []
    for spec, _ in _TOOLS.values():
        m = meta_of(spec.name)
        if m is None:
            continue  # validate_registry 已确保不会发生
        g = GROUPS[m[0]]
        out.append({'name': spec.name, 'description': spec.description or '',
                    'group': g.id, 'group_name': g.name,
                    'group_order': g.order, 'access': m[1]})
    out.sort(key=lambda d: (d['group_order'], d['name']))
    return out
```

- [ ] **Step 6: 跑绿 + 全量回归**

Run: `cd mcp-server && .venv/Scripts/python.exe -m pytest tests/test_registry.py -v && .venv/Scripts/python.exe -m pytest tests/ -v`
Expected: test_registry 5 PASS；全量无回归（启动校验对现有套件透明——所有工具已标注）

- [ ] **Step 7: Commit**

```bash
git add mcp-server/tools/registry.py mcp-server/tools/__init__.py mcp-server/tools/*.py mcp-server/tests/test_registry.py
git commit -m "feat(mcp-gov): registry manifest+25模块META标注+启动校验（缺标注拒绝启动）"
```

---

### Task 5: mcp-server rbac.py 矩阵升级

**Files:**
- Modify: `mcp-server/rbac.py`（整文件重写，保留旧公开名）
- Modify: `mcp-server/tests/test_rbac.py`（重写）
- Modify: `mcp-server/tests/test_tool_dispatch_allowlist.py`（加确定性夹具 + guest 断言更新）

**Interfaces:**
- Produces（Task 6/7 依赖）:
  - 既有名保留：`PUBLIC_KEFU_ROLE / READONLY_ROLES / KEFU_TOOL_ALLOWLIST / is_readonly / is_public_kefu / tool_allowed(name, role) -> bool`
  - 新增：`ROLES = ("admin","developer","guest","kefu-guest")`、`RULES_TTL_SECONDS = 3.0`、`_fetch_rules() -> dict`（可 monkeypatch）、`_reset_cache() -> None`

- [ ] **Step 1: 重写 test_rbac.py**（旧 `test_tool_allowed_others_unrestricted` 的 guest 全放行断言**按 spec §8 Q1 有意废弃**——guest 收紧）

```python
# -*- coding: utf-8 -*-
"""rbac 矩阵语义：行命中/行缺失默认/DB失败分层回落/TTL 缓存。"""
import pytest

import rbac


@pytest.fixture(autouse=True)
def _fresh_cache():
    rbac._reset_cache()
    yield
    rbac._reset_cache()


def test_legacy_predicates():
    assert rbac.is_readonly("guest") and rbac.is_readonly("kefu-guest")
    assert not rbac.is_readonly("developer")
    assert rbac.is_public_kefu("kefu-guest") and not rbac.is_public_kefu("guest")


def test_matrix_row_hit(monkeypatch):
    monkeypatch.setattr(rbac, "_fetch_rules", lambda: {
        ("developer", "run_python"): False,
        ("kefu-guest", "query_collection"): True,
    })
    assert rbac.tool_allowed("run_python", "developer") is False
    assert rbac.tool_allowed("query_collection", "kefu-guest") is True
    # 未播种行 → 代码默认
    assert rbac.tool_allowed("run_python", "admin") is True


def test_missing_row_defaults(monkeypatch):
    monkeypatch.setattr(rbac, "_fetch_rules", lambda: {})
    assert rbac.tool_allowed("run_python", "admin") is True
    assert rbac.tool_allowed("run_python", "developer") is True
    assert rbac.tool_allowed("run_python", "guest") is False        # spec §8 Q1 收紧
    assert rbac.tool_allowed("anything", "kefu-guest") is False
    assert rbac.tool_allowed("whatever", "custom-role") is False    # spec §8 Q2


def test_db_failure_layered_fallback(monkeypatch):
    def _boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(rbac, "_fetch_rules", _boom)
    assert rbac.tool_allowed("query_collection", "kefu-guest") is True   # 白名单兜底
    assert rbac.tool_allowed("run_python", "kefu-guest") is False
    assert rbac.tool_allowed("query_collection", "guest") is True        # manifest read 工具
    assert rbac.tool_allowed("run_python", "guest") is False             # write 工具拒绝
    assert rbac.tool_allowed("run_python", "admin") is True              # fail-open
    assert rbac.tool_allowed("run_python", "custom-role") is True        # fail-open


def test_ttl_cache_reuse(monkeypatch):
    calls = {"n": 0}

    def _counting():
        calls["n"] += 1
        return {}
    monkeypatch.setattr(rbac, "_fetch_rules", _counting)
    rbac.tool_allowed("run_python", "admin")
    rbac.tool_allowed("run_python", "admin")
    assert calls["n"] == 1                # TTL 内不重读
```

- [ ] **Step 2: 跑挂**

Run: `cd mcp-server && .venv/Scripts/python.exe -m pytest tests/test_rbac.py -v`
Expected: FAIL（`rbac._reset_cache` 不存在）

- [ ] **Step 3: 重写 rbac.py**

```python
# -*- coding: utf-8 -*-
"""工具访问钳制：角色×工具矩阵（DB）+ 代码默认/分层回落（spec §5.2）。

矩阵表 ai_mcp_tool_rules（全量行）由管理端 PUT 全量替换；本模块 TTL 3s
缓存读取。行缺失（新工具上线）→ 代码默认：admin/developer 放行，其余拒绝。
DB 读取失败 → 分层回落：kefu-guest 用代码白名单（fail-closed，公开身份
最后防线），guest 用 manifest 的 read 工具集，其余角色放行（fail-open，
DB 抖动不打瘫核心会话）。白名单同时是矩阵种子的 kefu 默认值。"""
import time

PUBLIC_KEFU_ROLE = "kefu-guest"
READONLY_ROLES = frozenset({"guest", "kefu-guest"})
# 公开匿名客服只允许的只读工具：数据查询 + 会话级上传读取。
KEFU_TOOL_ALLOWLIST = frozenset({"query_collection", "list_collections", "read_upload"})

# 与 server/init_db.py 内置角色种子对齐；动态自建角色不进矩阵（spec §6）
ROLES = ("admin", "developer", "guest", "kefu-guest")

RULES_TTL_SECONDS = 3.0

_rules_cache = None        # (role, tool_name) -> allowed
_rules_cached_at = 0.0


def is_readonly(role: str) -> bool:
    return role in READONLY_ROLES


def is_public_kefu(role: str) -> bool:
    return role == PUBLIC_KEFU_ROLE


def _fetch_rules() -> dict:
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT role, tool_name, allowed FROM ai_mcp_tool_rules")
            return {(r, t): a for r, t, a in cur.fetchall()}


def _reset_cache() -> None:
    """测试与手动失效入口：清缓存，下次调用重读 DB。"""
    global _rules_cache, _rules_cached_at
    _rules_cache = None
    _rules_cached_at = 0.0


def _rules():
    """矩阵快照；DB 失败返回 None（调用方走分层回落）。TTL 3s。"""
    global _rules_cache, _rules_cached_at
    now = time.monotonic()
    if _rules_cache is None or now - _rules_cached_at > RULES_TTL_SECONDS:
        try:
            _rules_cache = _fetch_rules()
        except Exception:
            _rules_cache = None
        _rules_cached_at = now
    return _rules_cache


def _fallback_allowed(name: str, role: str) -> bool:
    """DB 失败时的分层回落（spec §5.2）。"""
    if is_public_kefu(role):
        return name in KEFU_TOOL_ALLOWLIST
    if role == "guest":
        from tools.registry import meta_of
        m = meta_of(name)
        return bool(m) and m[1] == "read"
    # admin/developer/未知角色：fail-open
    return True


def tool_allowed(name: str, role: str) -> bool:
    rules = _rules()
    if rules is None:                       # DB 失败 → 分层回落
        return _fallback_allowed(name, role)
    hit = rules.get((role, name))
    if hit is not None:
        return bool(hit)
    # 行缺失（新工具/行被删）→ 代码默认：受限身份一律拒绝（spec §4.3）
    return role in ("admin", "developer")
```

- [ ] **Step 4: 跑绿 + 修 dispatch 套件确定性**

Run: `cd mcp-server && .venv/Scripts/python.exe -m pytest tests/test_rbac.py tests/test_tool_dispatch_allowlist.py -v`

`test_tool_dispatch_allowlist.py` 若因真 DB 矩阵不确定性挂：文件顶部加 autouse 夹具固定矩阵（kefu 三工具开）：

```python
@pytest.fixture(autouse=True)
def _deterministic_matrix(monkeypatch):
    import rbac
    rbac._reset_cache()
    monkeypatch.setattr(rbac, "_fetch_rules", lambda: {
        ("kefu-guest", "query_collection"): True,
        ("kefu-guest", "list_collections"): True,
        ("kefu-guest", "read_upload"): True,
    })
    yield
    rbac._reset_cache()
```

并检查该文件若有 `for role in ("developer", "admin", "guest")` 形态的全放行断言，改为 `("developer", "admin")` 并补 `assert not tool_allowed("run_python", "guest")`（spec §8 Q1 收紧后的新语义）。

Expected: 全 PASS

- [ ] **Step 5: 全量回归**

Run: `cd mcp-server && .venv/Scripts/python.exe -m pytest tests/ -v`
Expected: 无回归（`test_integration_db.py` 若连真库且矩阵未播种，行为=代码默认，与旧 kefu 断言一致：kefu 三工具… 注意旧断言 kefu run_python=False ✓、query=True ✓；guest 若有全放行断言按上一步同样修正）

- [ ] **Step 6: Commit**

```bash
git add mcp-server/rbac.py mcp-server/tests/test_rbac.py mcp-server/tests/test_tool_dispatch_allowlist.py
git commit -m "feat(mcp-gov): rbac 矩阵化——TTL缓存/行缺失默认/DB失败分层回落（guest收紧为Q1语义）"
```

---

### Task 6: 快照同步 + 矩阵播种 + /tools 扩展

**Files:**
- Create: `mcp-server/sync_metadata.py`
- Modify: `mcp-server/main.py`（register_all 后接线 sync；/tools 换 tool_meta()）
- Test: `mcp-server/tests/test_sync_metadata.py`、`mcp-server/tests/test_tools_endpoint.py`（追加断言）

**Interfaces:**
- Consumes: Task 4 `tools.tool_meta()`、Task 5 `rbac.KEFU_TOOL_ALLOWLIST/ROLES`、Task 1 两表
- Produces:
  - `sync_metadata_to_db() -> dict`（`{'tools': 27, 'rules_seeded': 108}`；失败抛异常由调用方 warning 吞掉）
  - `/tools` 端点条目扩展为 `{name, description, group, group_name, group_order, access}`

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""sync_metadata：快照 upsert 参数/消失行清理/矩阵播种默认值（fake_db 录制）。"""
from unittest.mock import MagicMock
from contextlib import contextmanager


def _make_fake_db():
    cur = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value = cur

    @contextmanager
    def _fake():
        yield conn
    return _fake, cur


def test_sync_upserts_snapshot_and_seeds_defaults(monkeypatch):
    import sync_metadata
    fake_db, cur = _make_fake_db()
    monkeypatch.setattr(sync_metadata, 'get_db', fake_db)
    out = sync_metadata.sync_metadata_to_db()
    assert out['tools'] == 27
    assert out['rules_seeded'] == 27 * 4

    # 第一次 executemany = 快照 upsert（27 行，含分组元数据）
    snap = cur.executemany.call_args_list[0]
    rows = snap.args[1]
    by_name = {r[0]: r for r in rows}
    assert set(by_name) == {r[0] for r in rows}
    assert len(rows) == 27
    q = by_name['query_collection']
    assert q[1] == 'data-query' and q[2] == '数据查询' and q[5] == 'read'

    # DELETE 消失行：ANY(当前名单)
    del_sql = cur.execute.call_args_list[0].args[0]
    assert 'DELETE FROM ai_mcp_tools' in del_sql and 'ANY' in del_sql

    # 第二次 executemany = 矩阵播种（ON CONFLICT DO NOTHING）
    seed = cur.executemany.call_args_list[1]
    seed_sql = seed.args[0]
    assert 'ON CONFLICT (role, tool_name) DO NOTHING' in seed_sql
    seed_rows = {(r[0], r[1]): r[2] for r in seed.args[1]}
    assert seed_rows[('admin', 'run_python')] is True
    assert seed_rows[('developer', 'run_python')] is True
    assert seed_rows[('guest', 'run_python')] is False          # write 拒绝
    assert seed_rows[('guest', 'query_collection')] is True     # read 放行
    assert seed_rows[('kefu-guest', 'read_upload')] is True     # 白名单平移
    assert seed_rows[('kefu-guest', 'read_data_file')] is False
```

- [ ] **Step 2: 跑挂**

Run: `cd mcp-server && .venv/Scripts/python.exe -m pytest tests/test_sync_metadata.py -v`
Expected: FAIL（ModuleNotFoundError: sync_metadata）

- [ ] **Step 3: 写 sync_metadata.py**

```python
# -*- coding: utf-8 -*-
"""启动时把 registry 元数据快照进 ai_mcp_tools，并播种矩阵默认行（spec §4.3）。

快照 = upsert 全量 + 删除注册集中已消失的行（工具改名/下架不留尾巴）；失败
由 main.py 记 warning 不阻断启动（快照只服务管理展示，执法走 rbac 默认语义）。
矩阵播种 ON CONFLICT DO NOTHING——永不覆盖管理员选择。"""
from db import get_db
from rbac import KEFU_TOOL_ALLOWLIST, ROLES
from tools import tool_meta


def _default_allowed(role: str, m: dict) -> bool:
    if role in ('admin', 'developer'):
        return True
    if role == 'guest':
        return m['access'] == 'read'
    if role == 'kefu-guest':
        return m['name'] in KEFU_TOOL_ALLOWLIST
    return False


def sync_metadata_to_db() -> dict:
    meta = tool_meta()
    names = [m['name'] for m in meta]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO ai_mcp_tools (tool_name, group_id, group_name, group_order, "
                "description, access, updated_at) VALUES (%s,%s,%s,%s,%s,%s,NOW()) "
                "ON CONFLICT (tool_name) DO UPDATE SET group_id=EXCLUDED.group_id, "
                "group_name=EXCLUDED.group_name, group_order=EXCLUDED.group_order, "
                "description=EXCLUDED.description, access=EXCLUDED.access, "
                "updated_at=NOW()",
                [(m['name'], m['group'], m['group_name'], m['group_order'],
                  m['description'], m['access']) for m in meta])
            cur.execute("DELETE FROM ai_mcp_tools WHERE NOT (tool_name = ANY(%s))", (names,))
            cur.executemany(
                "INSERT INTO ai_mcp_tool_rules (role, tool_name, allowed, updated_at) "
                "VALUES (%s,%s,%s,NOW()) ON CONFLICT (role, tool_name) DO NOTHING",
                [(role, m['name'], _default_allowed(role, m))
                 for m in meta for role in ROLES])
        conn.commit()
    return {'tools': len(meta), 'rules_seeded': len(meta) * len(ROLES)}
```

- [ ] **Step 4: main.py 接线 + /tools 扩展**（Edit 两处）

`register_all(mcp_server)` 行（main.py:46）之后插：

```python
# 元数据快照 + 矩阵默认行播种（spec §5.1/§4.3）；失败不阻断——执法走
# rbac 代码默认语义，管理页可能显示旧快照。
try:
    from sync_metadata import sync_metadata_to_db
    print("mcp tool metadata synced:", sync_metadata_to_db())
except Exception as _e:
    print(f"WARNING: mcp tool metadata sync failed: {_e}")
```

`/tools` 端点（main.py:86-88）替换为：

```python
@app.get("/tools")
def tools():
    from tools import tool_meta
    return tool_meta()
```

- [ ] **Step 5: test_tools_endpoint.py 追加断言**（文件末尾追加）

```python
def test_tools_endpoint_returns_group_meta():
    from main import app
    with TestClient(app) as c:
        resp = c.get("/tools")
    by_name = {t["name"]: t for t in resp.json()}
    qc = by_name["query_collection"]
    assert qc["group"] == "data-query"
    assert qc["group_name"] == "数据查询"
    assert qc["access"] == "read"
    assert qc["group_order"] == 1
    rp = by_name["run_python"]
    assert rp["group"] == "exec" and rp["access"] == "write"
```

- [ ] **Step 6: 跑绿 + 全量回归**

Run: `cd mcp-server && .venv/Scripts/python.exe -m pytest tests/test_sync_metadata.py tests/test_tools_endpoint.py -v && .venv/Scripts/python.exe -m pytest tests/ -v`
Expected: 全 PASS（`/tools` 旧断言兼容：仍是 name+description 超集）

- [ ] **Step 7: Commit**

```bash
git add mcp-server/sync_metadata.py mcp-server/main.py mcp-server/tests/test_sync_metadata.py mcp-server/tests/test_tools_endpoint.py
git commit -m "feat(mcp-gov): 启动快照+矩阵播种+/tools 分组元数据扩展"
```

---

### Task 7: 前端 API + 工具治理抽屉

**Files:**
- Modify: `src/api/aiMcpServers.ts`（文件末尾追加）
- Create: `src/components/admin/McpToolGovernanceDrawer.vue`
- Test: `src/components/admin/__tests__/McpToolGovernanceDrawer.test.ts`

**Interfaces:**
- Consumes: Task 3 的 GET `/ai/mcp-tools` / PUT `/ai/mcp-tool-rules`
- Produces:
  - `getMcpTools(): Promise<McpGovernance>`、`putMcpToolRules(rules: McpToolRuleInput[]): Promise<{updated: number; diff: string}>`
  - 类型 `McpGovernance { groups: McpGovernanceGroup[]; roles: {role,label}[] }`；`McpToolRuleInput { role: string; tool_name: string; allowed: boolean }`（PUT 载荷用 snake_case `tool_name`，与后端一致）
  - `McpToolGovernanceDrawer.vue`：`props: { modelValue: boolean }`，`emits: ['update:modelValue']`（Task 8 的 McpServersCard 挂它）

- [ ] **Step 1: aiMcpServers.ts 追加**（文件末尾）

```ts
/** 工具治理（内置 MCP 工具的角色×矩阵）。 */
export interface McpToolRuleInput { role: string; tool_name: string; allowed: boolean }

export interface McpGovernanceTool {
  name: string
  description: string
  access: 'read' | 'write'
  rules: Record<string, boolean>
}

export interface McpGovernanceGroup {
  id: string
  name: string
  order: number
  tools: McpGovernanceTool[]
}

export interface McpGovernance {
  groups: McpGovernanceGroup[]
  roles: { role: string; label: string }[]
}

export function getMcpTools(): Promise<McpGovernance> {
  return get<McpGovernance>('/ai/mcp-tools')
}

export async function putMcpToolRules(
  rules: McpToolRuleInput[],
): Promise<{ updated: number; diff: string }> {
  const { data } = await service.put('/ai/mcp-tool-rules', { rules })
  return data
}
```

- [ ] **Step 2: 写失败测试**（真实 EP + `$emit` 直驱惯例）

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import McpToolGovernanceDrawer from '@/components/admin/McpToolGovernanceDrawer.vue'
import { getMcpTools, putMcpToolRules } from '@/api/aiMcpServers'

vi.mock('@/api/aiMcpServers', () => ({
  getMcpTools: vi.fn(),
  putMcpToolRules: vi.fn(),
}))

const FIXTURE = {
  groups: [
    { id: 'data-query', name: '数据查询', order: 1, tools: [
      { name: 'query_collection', description: '按条件查询集合', access: 'read',
        rules: { admin: true, developer: true, guest: false, 'kefu-guest': true } },
      { name: 'export_collection_excel', description: '导出 Excel', access: 'read',
        rules: { admin: true, developer: true, guest: false, 'kefu-guest': false } },
    ] },
    { id: 'exec', name: '通用执行与产物', order: 7, tools: [
      { name: 'run_python', description: '执行 Python', access: 'write',
        rules: { admin: true, developer: true, guest: false, 'kefu-guest': false } },
    ] },
  ],
  roles: [
    { role: 'admin', label: '管理员' }, { role: 'developer', label: '开发' },
    { role: 'guest', label: '访客' }, { role: 'kefu-guest', label: '客服' },
  ],
}

describe('McpToolGovernanceDrawer', () => {
  beforeEach(() => {
    vi.mocked(getMcpTools).mockResolvedValue(FIXTURE)
    vi.mocked(putMcpToolRules).mockResolvedValue({ updated: 1, diff: 'x' })
  })

  it('按组渲染工具 + 角色列头 + 开关初值', async () => {
    const w = mount(McpToolGovernanceDrawer, { props: { modelValue: true } })
    await flushPromises()
    const text = document.body.textContent || ''
    expect(text).toContain('数据查询')
    expect(text).toContain('通用执行与产物')
    expect(text).toContain('query_collection')
    expect(text).toContain('run_python')
    const switches = w.findAllComponents({ name: 'ElSwitch' })
    // 组批量开关（2 组×4 角色）+ 工具开关（3 工具×4 角色）= 20
    expect(switches.length).toBe(2 * 4 + 3 * 4)
  })

  it('切换开关 → 脏标记 → 保存发全量矩阵', async () => {
    const w = mount(McpToolGovernanceDrawer, { props: { modelValue: true } })
    await flushPromises()
    // DOM 序：组1批量(0-3) 工具1(4-7) 工具2(8-11) 组2批量(12-15) run_python(16-19)
    // run_python × developer = 17
    const dev = w.findAllComponents({ name: 'ElSwitch' })[17]
    dev.vm.$emit('change', false)
    await flushPromises()
    expect(w.text()).toContain('保存')
    const saveBtn = w.findAll('button').find(b => b.text().includes('保存'))!
    await saveBtn.trigger('click')
    await flushPromises()
    const payload = vi.mocked(putMcpToolRules).mock.calls[0][0]
    expect(payload.length).toBe(3 * 4)
    expect(payload).toContainEqual(
      { role: 'developer', tool_name: 'run_python', allowed: false })
  })

  it('空 groups 显示未同步提示', async () => {
    vi.mocked(getMcpTools).mockResolvedValue({ groups: [], roles: [] })
    const w = mount(McpToolGovernanceDrawer, { props: { modelValue: true } })
    await flushPromises()
    expect((document.body.textContent || '')).toContain('未同步')
  })
})
```

- [ ] **Step 3: 跑挂**

Run: `npx vitest run src/components/admin/__tests__/McpToolGovernanceDrawer.test.ts`
Expected: FAIL（组件不存在）

- [ ] **Step 4: 写 McpToolGovernanceDrawer.vue**

```vue
<script setup lang="ts">
/** 内置 MCP 工具治理抽屉：分组清单 + 角色×工具矩阵开关（spec §7.1）。
 *  改动即时生效（≤3s）；保存=全量 PUT；组标题行 4 个角色微开关批量切整组。 */
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getMcpTools, putMcpToolRules,
  type McpGovernance, type McpToolRuleInput,
} from '@/api/aiMcpServers'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

const loading = ref(false)
const saving = ref(false)
const data = ref<McpGovernance | null>(null)
/** 本地可编辑副本：groups → tools[i].rules[role]（直接改，保存时扁平化） */
const editable = ref<McpGovernance | null>(null)
let baseline = ''

const dirty = computed(() =>
  !!editable.value && JSON.stringify(editable.value.groups) !== baseline)

watch(() => props.modelValue, async (open) => {
  if (!open || editable.value) return
  loading.value = true
  try {
    data.value = await getMcpTools()
    editable.value = JSON.parse(JSON.stringify(data.value))
    baseline = JSON.stringify(editable.value.groups)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || '加载工具清单失败')
  } finally { loading.value = false }
})

function setToolRole(tool: { rules: Record<string, boolean> }, role: string, v: boolean) {
  tool.rules[role] = v
}

/** 组×角色批量：目标值取「该组该角色当前值取反」，整组一起切。 */
function toggleGroupRole(group: McpGovernance['groups'][number], role: string) {
  const target = !group.tools.every(t => t.rules[role])
  for (const t of group.tools) t.rules[role] = target
}

function groupAllOn(group: McpGovernance['groups'][number], role: string) {
  return group.tools.length > 0 && group.tools.every(t => t.rules[role])
}

function flatten(): McpToolRuleInput[] {
  const out: McpToolRuleInput[] = []
  for (const g of editable.value?.groups || [])
    for (const t of g.tools)
      for (const r of editable.value?.roles || [])
        out.push({ role: r.role, tool_name: t.name, allowed: !!t.rules[r.role] })
  return out
}

async function save() {
  if (!editable.value) return
  saving.value = true
  try {
    const res = await putMcpToolRules(flatten())
    ElMessage.success(`已保存（${res.updated} 项变更，≤3s 内对新调用生效）`)
    baseline = JSON.stringify(editable.value.groups)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || '保存失败')
  } finally { saving.value = false }
}

async function beforeClose(done: () => void) {
  if (!dirty.value) { done(); return }
  try {
    await ElMessageBox.confirm('有未保存的改动，确认放弃？', '未保存改动',
      { confirmButtonText: '放弃', cancelButtonText: '继续编辑', type: 'warning' })
    done()
  } catch { /* 继续编辑 */ }
}
</script>

<template>
  <el-drawer :model-value="props.modelValue" title="工具治理（角色 × 工具）" size="760px"
             :before-close="beforeClose" @update:model-value="emit('update:modelValue', $event)">
    <div v-loading="loading">
      <p class="hint">
        开关按角色控制工具可用性，保存后<b>即时生效</b>（≤3s 内对新调用拒绝）；
        已打开会话的工具列表要等其重连后刷新展示。
      </p>
      <el-alert v-if="editable && !editable.groups.length" type="info" :closable="false"
                title="暂无工具清单（mcp-server 未同步快照）——请确认 3003 已随新版本重启。" />
      <template v-for="g in editable?.groups || []" :key="g.id">
        <div class="grp">
          <div class="grp__head">
            <span class="grp__name">{{ g.name }}</span>
            <div class="grp__batch">
              <template v-for="r in editable!.roles" :key="r.role">
                <el-tooltip :content="`整组 × ${r.label}`" placement="top">
                  <el-switch size="small" :model-value="groupAllOn(g, r.role)"
                             @change="toggleGroupRole(g, r.role)" />
                </el-tooltip>
                <span class="grp__batch-label">{{ r.label }}</span>
              </template>
            </div>
          </div>
          <el-table :data="g.tools" size="small">
            <el-table-column label="工具" min-width="220">
              <template #default="{ row }">
                <span class="mono">{{ row.name }}</span>
                <div class="tool-desc">{{ row.description }}</div>
              </template>
            </el-table-column>
            <el-table-column label="读写" width="70">
              <template #default="{ row }">
                <el-tag :type="row.access === 'write' ? 'danger' : 'info'" size="small">
                  {{ row.access === 'write' ? '写' : '读' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column v-for="r in editable!.roles" :key="r.role"
                             :label="r.label" width="72" align="center">
              <template #default="{ row }">
                <el-switch :model-value="!!row.rules[r.role]"
                           @change="(v: string | number | boolean) => setToolRole(row, r.role, !!v)" />
              </template>
            </el-table-column>
          </el-table>
        </div>
      </template>
      <div v-if="editable?.groups.length" class="footer">
        <span class="footer__dirty">{{ dirty ? '有未保存改动' : '' }}</span>
        <el-button :disabled="!dirty" :loading="saving" type="primary" @click="save">
          保存（全量提交）
        </el-button>
      </div>
    </div>
  </el-drawer>
</template>

<style scoped>
.hint { margin: 0 0 12px; font-size: 13px; color: var(--el-text-color-secondary); }
.grp { margin-bottom: 18px; }
.grp__head { display: flex; align-items: center; gap: 12px; margin-bottom: 6px; }
.grp__name { font-weight: 600; }
.grp__batch { display: flex; align-items: center; gap: 4px; margin-left: auto; }
.grp__batch-label { font-size: 12px; color: var(--el-text-color-secondary); margin-right: 4px; }
.mono { font-family: var(--el-font-family-mono, monospace); font-size: 12px; }
.tool-desc { font-size: 12px; color: var(--el-text-color-secondary); white-space: normal; }
.footer { display: flex; align-items: center; gap: 12px; margin-top: 8px; }
.footer__dirty { color: var(--el-color-warning); font-size: 12px; }
</style>
```

- [ ] **Step 5: 跑绿**

Run: `npx vitest run src/components/admin/__tests__/McpToolGovernanceDrawer.test.ts`
Expected: 3 PASS（若 el-drawer teleport 导致 `w.text()` 拿不到内容，统一改断言 `document.body.textContent`——测试代码已按此写）

- [ ] **Step 6: Commit**

```bash
git add src/api/aiMcpServers.ts src/components/admin/McpToolGovernanceDrawer.vue src/components/admin/__tests__/McpToolGovernanceDrawer.test.ts
git commit -m "feat(mcp-gov): 工具治理抽屉——分组矩阵开关+组级批量+全量保存"
```

---

### Task 8: McpServersCard 接入 + 会话侧分组展示

**Files:**
- Modify: `src/components/admin/McpServersCard.vue`（内置区加按钮/抽屉/文案）
- Modify: `src/components/ai-chat/McpServicesBlock.vue`（按组渲染）
- Modify: `src/api/aiChat.ts:120`（McpTool 加可选分组字段）
- Test: `src/components/ai-chat/__tests__/McpServicesBlock.test.ts`（追加分组用例）

**Interfaces:**
- Consumes: Task 7 的 `McpToolGovernanceDrawer`；mcp-server `/tools` 扩展字段（Task 6）经 `ai_chat.py list_mcp_services` **原样透传**（无需改后端）

- [ ] **Step 1: 写失败测试**（McpServicesBlock.test.ts 追加）

```ts
  it('按 group_name 分组渲染，缺失归「其他」', () => {
    const w = mount(McpServicesBlock, {
      props: {
        servers: [
          { name: 'check-manage', status: 'connected', tools: [
            { name: 'query_collection', description: '查', group: 'data-query', group_name: '数据查询' },
            { name: 'run_python', description: '跑', group: 'exec', group_name: '通用执行与产物' },
            { name: 'mystery_tool', description: '无组' },
          ] },
        ],
      },
    })
    const text = w.text()
    expect(text).toContain('数据查询')
    expect(text).toContain('通用执行与产物')
    expect(text).toContain('其他')            // 无 group_name 的工具归「其他」
    expect(text).toContain('mystery_tool')
  })
```

- [ ] **Step 2: 跑挂**

Run: `npx vitest run src/components/ai-chat/__tests__/McpServicesBlock.test.ts`
Expected: 新用例 FAIL（无分组渲染），旧两用例仍 PASS（向后兼容断言不破）

- [ ] **Step 3: aiChat.ts 类型**（:120 替换）

```ts
export interface McpTool {
  name: string
  description: string
  /** mcp-server /tools 扩展的分组元数据（旧快照/外部源缺省时无此二字段） */
  group?: string
  group_name?: string
}
```

- [ ] **Step 4: 重写 McpServicesBlock.vue**（分组渲染，无分组字段时保持旧行为平铺）

```vue
<script setup lang="ts">
import { computed } from 'vue'
import type { McpServer, McpTool } from '@/api/aiChat'

const props = defineProps<{ servers: McpServer[] }>()

/** 工具带 group_name 时按组分区（首现顺序）；缺组归「其他」。 */
function groupTools(tools: McpTool[]): { title: string; tools: McpTool[] }[] {
  const map = new Map<string, { title: string; tools: McpTool[] }>()
  for (const t of tools) {
    const title = t.group_name || '其他'
    if (!map.has(title)) map.set(title, { title, tools: [] })
    map.get(title)!.tools.push(t)
  }
  return [...map.values()]
}

const anyGrouped = computed(() =>
  props.servers.some(s => s.tools.some(t => t.group_name)))
</script>

<template>
  <div class="mcp-services">
    <template v-if="servers.length">
      <div class="mcp-services__title">MCP 服务 ({{ servers.length }})</div>
      <div v-for="s in servers" :key="s.name" class="mcp-server">
        <div class="mcp-server__head">
          <span class="mcp-dot" :class="{ on: s.status === 'connected' }" />
          <span class="mcp-server__name">{{ s.name }}</span>
          <span class="mcp-server__status">{{ s.status }}</span>
        </div>
        <template v-if="anyGrouped">
          <div v-for="g in groupTools(s.tools)" :key="g.title" class="mcp-tools__group">
            <div class="mcp-tools__group-title">{{ g.title }}（{{ g.tools.length }}）</div>
            <ul class="mcp-tools">
              <li v-for="t in g.tools" :key="t.name">
                <code>{{ t.name }}</code><span v-if="t.description"> — {{ t.description }}</span>
              </li>
            </ul>
          </div>
        </template>
        <ul v-else class="mcp-tools">
          <li v-for="t in s.tools" :key="t.name">
            <code>{{ t.name }}</code><span v-if="t.description"> — {{ t.description }}</span>
          </li>
          <li v-if="!s.tools.length" class="mcp-tools__empty">（无可用工具信息）</li>
        </ul>
      </div>
    </template>
    <div v-else class="mcp-services__empty">无法获取 MCP 服务（OpenCode 不可用）</div>
  </div>
</template>

<style scoped lang="scss">
.mcp-services {
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  padding: 10px 12px;
  background: var(--el-fill-color-light);
  font-size: 13px;
}
.mcp-services__title { font-weight: 600; margin-bottom: 8px; }
.mcp-server { margin-bottom: 8px; }
.mcp-server__head { display: flex; align-items: center; gap: 6px; }
.mcp-dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--el-color-info);
}
.mcp-dot.on { background: var(--el-color-success); }
.mcp-server__name { font-weight: 600; }
.mcp-server__status { color: var(--el-text-color-secondary); font-size: 12px; }
.mcp-tools { margin: 4px 0 0; padding-left: 18px; }
.mcp-tools li { line-height: 1.7; }
.mcp-tools code { font-family: var(--el-font-family-mono, monospace); }
.mcp-tools__empty { color: var(--el-text-color-secondary); list-style: none; }
.mcp-tools__group { margin-top: 4px; }
.mcp-tools__group-title {
  font-weight: 600; font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 6px;
}
</style>
```

- [ ] **Step 5: McpServersCard 接入抽屉**（Edit 三处）

`internal-mcp__row` 里「检测连接」按钮后加：

```html
        <el-button size="small" text @click="govOpen = true">工具治理</el-button>
```

`internal-mcp__desc` 段末追加一句：

```html
      工具级开关（角色×矩阵，即时生效）见「工具治理」。
```

script 区：`import` 块后加 `import McpToolGovernanceDrawer from './McpToolGovernanceDrawer.vue'`；`internalHealth` 定义后加 `const govOpen = ref(false)`；template `</el-dialog>` 之后加：

```html
  <McpToolGovernanceDrawer v-model="govOpen" />
```

- [ ] **Step 6: 跑绿 + 类型检查**

Run: `npx vitest run src/components/ai-chat/__tests__/McpServicesBlock.test.ts && npx vue-tsc --noEmit -p tsconfig.json`
Expected: 3 PASS；类型无错

- [ ] **Step 7: Commit**

```bash
git add src/components/admin/McpServersCard.vue src/components/ai-chat/McpServicesBlock.vue src/api/aiChat.ts src/components/ai-chat/__tests__/McpServicesBlock.test.ts
git commit -m "feat(mcp-gov): 管理卡接入治理抽屉+会话侧工具按组展示"
```

---

### Task 9: MCP_NAME 收敛 + list_collections 描述中文化

**Files:**
- Modify: `server/utils/mcp_servers.py`（顶部加常量）
- Modify: `server/routes/ai_chat.py:81`、`server/utils/kefu_repo.py:23`、`server/utils/batch_engine.py:352`、`server/routes/ai.py:207,246`（字面量改引用）
- Modify: `mcp-server/tools/list_collections.py`（description 重写）

**Interfaces:**
- Produces: `utils.mcp_servers.INTERNAL_MCP_NAME = 'check-manage'`（唯一字面量定义点；注入名不可改——改名破坏存量会话 opencode.json，spec §9.2）

- [ ] **Step 1: mcp_servers.py 常量**（`VALID_TYPES = ...` 行之前插入）

```python
# opencode.json 注入名（对外单一呈现名）。改名会破坏存量会话工作区，勿动。
INTERNAL_MCP_NAME = 'check-manage'
```

- [ ] **Step 2: 四处引用替换**

- `server/routes/ai_chat.py:59` 的 import 行追加 `INTERNAL_MCP_NAME as MCP_NAME`；删除 `:81` 的 `MCP_NAME = 'check-manage'`：

```python
from utils.mcp_servers import enabled_mcp_config, internal_mcp_enabled, \
    INTERNAL_MCP_NAME as MCP_NAME
```

- `server/utils/kefu_repo.py:15` 的 import 行同样追加 `INTERNAL_MCP_NAME as MCP_NAME`；删除 `:23` 的定义行。
- `server/utils/batch_engine.py:349` 的 import 行追加 `INTERNAL_MCP_NAME`；`:352` 改为 `_mcp_name = INTERNAL_MCP_NAME`。
- `server/routes/ai.py:19-21` 的 import 行追加 `INTERNAL_MCP_NAME`；`:207`（`'name': 'check-manage'`）与 `:246`（log 文案处 `'check-manage'`、`'name': 'check-manage'`）替换为 `INTERNAL_MCP_NAME`（f-string 或拼接处同步）。

- [ ] **Step 3: 全量字面量核查**

Run: `cd E:/wsl/check/check-manage && grep -rn "'check-manage'" server/ --include='*.py' | grep -v '.mimosa' | grep -v backups`
Expected: 仅 `server/utils/mcp_servers.py` 常量定义一行（其余全是引用）

- [ ] **Step 4: list_collections 描述重写**（`mcp-server/tools/list_collections.py` TOOL 块 description 替换）

```python
TOOL = types.Tool(
    name=NAME,
    description=(
        "列出当前用户角色可见的业务数据集合（返回集合标识与数据页显示名）。"
        "当不确定平台有哪些数据可查、或需要拿集合标识/显示名去调其它数据工具时，先调它。"
    ),
    inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
)
```

- [ ] **Step 5: 回归**

Run: `cd server && python -m pytest tests/test_mcp_servers.py tests/test_routes_ai_settings.py -v && cd ../mcp-server && .venv/Scripts/python.exe -m pytest tests/test_list_collections.py tests/test_registry.py tests/test_tools_endpoint.py -v`
Expected: 全 PASS（描述变更不破坏现有断言；若有断言旧英文文案则同步更新该断言）

- [ ] **Step 6: Commit**

```bash
git add server/utils/mcp_servers.py server/routes/ai_chat.py server/utils/kefu_repo.py server/utils/batch_engine.py server/routes/ai.py mcp-server/tools/list_collections.py
git commit -m "refactor(mcp): MCP_NAME 收敛单点常量；list_collections 描述中文化"
```

---

### Task 10: E2E 真实链路 + 真机验证收口

**Files:**
- Create: `e2e/ai-full/ai-mcp-governance.spec.ts`

**Interfaces:**
- Consumes: 全部前置任务（dev 栈须在跑：3002 后端已带迁移与端点、3003 已带 sync/rbac、前端已带抽屉）；helpers 的 `api(request, method, path, data?)`（自动 admin token，返回 `{status, json}`）

- [ ] **Step 1: 先重启真机栈**（顺序：3002 → 3003；3003 重启会执行快照+播种，日志可验证）

Run（Windows Git Bash，杀旧再拉新；3003 防双实例）:
```bash
# 3002 后端（无自动 reload，须重启跑迁移）：
powershell -Command "Get-NetTCPConnection -LocalPort 3002 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"
cd server && cmd //c "start /b python app.py > ../backend-mtgv-restart.log 2>&1"
sleep 8 && grep -m1 "mcp tool governance tables ready" ../backend-mtgv-restart.log
# 3003 mcp-server：
powershell -Command "Get-NetTCPConnection -LocalPort 3003 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"
cd ../mcp-server && cmd //c "start /b .venv\Scripts\python.exe main.py > ../mcp-server-3003.log 2>&1"
sleep 5 && tail -3 ../mcp-server-3003.log    # 期望出现 mcp tool metadata synced: {'tools': 27, 'rules_seeded': 108}
```

- [ ] **Step 2: 写 E2E spec**

```ts
/**
 * 内置 MCP 工具治理 E2E（2026-10-25 spec，真实栈 3002+3003+前端）。
 * 覆盖：矩阵 API roundtrip（PUT→GET 反映/非法 400/恢复原矩阵）、管理抽屉 UI
 * （分组渲染+开关+保存）、会话侧 /sessions/:id/mcp 工具带分组字段。
 * 不做「禁用后真调用被拒」的重链路断言——dispatch 拒绝语义归 mcp-server 单测
 * （test_rbac/test_tool_dispatch_allowlist），真调用链路已由 ai-mcp-tools.spec.ts 覆盖。
 */
import { test, expect } from '@playwright/test'
import { api, screenshot } from './helpers'

test.setTimeout(180_000)

type Gov = { groups: any[]; roles: { role: string; label: string }[] }

function flatRules(gov: Gov) {
  return gov.groups.flatMap(g => g.tools).flatMap((t: any) =>
    Object.entries(t.rules).map(([role, allowed]) =>
      ({ role, tool_name: t.name, allowed })))
}

test('矩阵 API：GET 分组结构 / PUT 全量替换 / 非法 400 / 恢复', async ({ request }) => {
  const before = await api(request, 'GET', '/ai/mcp-tools')
  expect(before.status).toBe(200)
  const gov: Gov = before.json
  expect(gov.groups.length).toBeGreaterThanOrEqual(7)
  expect(gov.roles.map(r => r.role)).toEqual(
    ['admin', 'developer', 'guest', 'kefu-guest'])
  const all = gov.groups.flatMap(g => g.tools)
  expect(all.length).toBe(27)
  const runPython = all.find(t => t.name === 'run_python')
  expect(runPython.access).toBe('write')

  const bad = await api(request, 'PUT', '/ai/mcp-tool-rules',
    { rules: [{ role: 'hacker', tool_name: 'run_python', allowed: true }] })
  expect(bad.status).toBe(400)

  const orig = flatRules(gov)
  // 取当前值取反——对「此前已被管理员禁用」的状态也稳健
  const cur = orig.find(r => r.tool_name === 'run_python' && r.role === 'developer')!.allowed
  const mutated = orig.map(r =>
    r.tool_name === 'run_python' && r.role === 'developer' ? { ...r, allowed: !cur } : r)
  const put = await api(request, 'PUT', '/ai/mcp-tool-rules', { rules: mutated })
  expect(put.status).toBe(200)
  expect(put.json.updated).toBe(1)

  const after = await api(request, 'GET', '/ai/mcp-tools')
  const rp2 = (after.json as Gov).groups.flatMap(g => g.tools)
    .find(t => t.name === 'run_python')
  expect(rp2.rules.developer).toBe(!cur)

  const restore = await api(request, 'PUT', '/ai/mcp-tool-rules', { rules: orig })
  expect(restore.status).toBe(200)
})

test('会话侧：/sessions/:id/mcp 的 check-manage 工具带分组字段', async ({ request }) => {
  const created = await api(request, 'POST', '/ai/chat/sessions', {})
  const sid = created.json.id
  const r = await api(request, 'GET', `/ai/chat/sessions/${sid}/mcp`)
  expect(r.status).toBe(200)
  const ours = r.json.servers.find((s: any) => s.name === 'check-manage')
  expect(ours).toBeTruthy()
  const qc = ours.tools.find((t: any) => t.name === 'query_collection')
  expect(qc.group_name).toBe('数据查询')
  await api(request, 'POST', `/ai/chat/sessions/${sid}/close`, {})
})

test('管理抽屉 UI：分组渲染 + 开关 + 保存', async ({ page, request }) => {
  await page.goto('/admin/ai-settings')
  // 壳层「运行时」tab → 页内 .oc-runtime 的「MCP 服务」tab（嵌套 el-tabs 选择器坑：
  // 照抄 ai-admin-nav.spec.ts 的限定模式）
  await page.locator('.el-tabs__nav', { hasText: '运行时' }).first().click()
  await page.locator('.oc-runtime .el-tabs__nav').filter({ hasText: 'MCP 服务' }).click()
  await page.getByRole('button', { name: '工具治理' }).click()
  const drawer = page.locator('.el-drawer')
  await expect(drawer).toBeVisible()
  await expect(drawer.getByText('数据查询')).toBeVisible()
  await expect(drawer.getByText('通用执行与产物')).toBeVisible()
  await expect(drawer.getByText('run_python')).toBeVisible()
  await screenshot(page, 'mcp-governance-drawer')

  // 快照矩阵 → 切 run_python×developer → 保存 → 断言落库翻转 → 恢复
  const before = await api(request, 'GET', '/ai/mcp-tools')
  const orig = flatRules(before.json as Gov)
  const cur = orig.find(r => r.tool_name === 'run_python' && r.role === 'developer')!.allowed
  const row = drawer.locator('.el-table__row', { hasText: 'run_python' })
  const switches = row.locator('.el-switch')
  await switches.nth(1).click()                    // developer 列（取反当前值）
  await drawer.getByRole('button', { name: /保存/ }).click()
  await expect(page.locator('.el-message--success').or(page.getByText('已保存'))).toBeVisible()
  const after = await api(request, 'GET', '/ai/mcp-tools')
  const rp = (after.json as Gov).groups.flatMap(g => g.tools)
    .find(t => t.name === 'run_python')
  expect(rp.rules.developer).toBe(!cur)
  await api(request, 'PUT', '/ai/mcp-tool-rules', { rules: orig })
})
```

- [ ] **Step 3: 跑 E2E**

Run: `cd E:/wsl/check/check-manage && npx playwright test e2e/ai-full/ai-mcp-governance.spec.ts`
Expected: 3 PASS（若 UI 选择器因 EP 渲染差异挂，按既有套件的 hoverClick/重试惯例修选择器，不改语义）

- [ ] **Step 4: 全量回归**

Run:
```bash
cd server && python -m pytest tests/ -m "not slow and not data_full" -q
cd ../mcp-server && .venv/Scripts/python.exe -m pytest tests/ -q
cd .. && npx vitest run
```
Expected: server / mcp-server / vitest 全绿（vitest 全量含既有 McpServicesBlock 旧两用例——向后兼容已保证）

- [ ] **Step 5: 真机即时生效冒烟**（可选人工验证，spec D3 证据）

在抽屉里临时禁 `query_collection`×developer → 3 秒内在任一已打开的 developer 会话里让 AI 调 `query_collection` → 期望调用被拒（PermissionError 工具气泡）→ 恢复开关。不做进自动化（需 developer 身份真会话，成本高；语义已由单测+API E2E 覆盖）。

- [ ] **Step 6: Commit**

```bash
git add e2e/ai-full/ai-mcp-governance.spec.ts
git commit -m "test(e2e): MCP 工具治理——矩阵roundtrip+抽屉UI+会话侧分组（真实栈）"
```

---

## 验收对照（spec → 任务）

| spec 节 | 任务 |
|---|---|
| §4 数据模型/迁移 | Task 1（`group_order` 偏离见 Global Constraints） |
| §5.1 registry/分组/启动校验 | Task 4 |
| §5.2 rbac 矩阵/默认/分层回落 | Task 5 |
| §5.3 /tools 扩展 | Task 6 |
| §4.3 种子（mcp-server 播种，偏离已记） | Task 6 |
| §6 Flask 端点/权限/审计 | Task 2+3 |
| §7.1 管理抽屉 | Task 7+8 |
| §7.2 会话侧分组（透传实现，偏离已记） | Task 6+8 |
| §9.1 描述修复（收窄为 list_collections） | Task 9 |
| §9.2 MCP_NAME 收敛 | Task 9 |
| §11 测试策略 | 各任务 Step 1 + Task 10 |
| §8 Q1/Q2 语义落点 | Task 5 测试显式断言（guest/未知角色拒绝） |
