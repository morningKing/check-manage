# 第一方插件架构 + 智能写作挂入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立第一方插件规范（注册表单真相源 + 运行时开关 + 四挂载点门控），并把智能写作挂入为首插件——禁用即菜单隐藏/路由 404/API 404/WS 拒连/Node 进程停，数据全保留。

**Architecture:** 后端 `server/utils/plugin_registry.py` 为唯一真相源（manifest + TTL 缓存开关 + 蓝图门 + 菜单过滤）；状态存新表 `platform_plugins`（种子不覆盖管理员选择）；`PUT /api/plugins/<id>`（admin.plugins）即时生效。前端零镜像清单：路由 `meta.plugin` 标注 + Pinia store 从 `GET /api/plugins` 动态取启用集 + 守卫拦截（fail-open，后端门是硬墙）。生产 `proxy.py` 隧道拒连 + se 受管子进程按开关停启。

**Tech Stack:** Flask + PostgreSQL（psycopg2 裸 SQL，沿用 get_db）；Vue3 + Pinia + Element Plus；pytest（真库 casemanage_test）+ vitest + Playwright。

**Spec:** `docs/superpowers/specs/2026-10-25-first-party-plugin-architecture-design.md`（四决策 D1-D4 + 七节设计；本计划 §引用均指该 spec）。

## Global Constraints

- 迁移**双注册**：`server/migrations/` 文件 + `server/app.py` 显式 boot 块（照抄 2026_10_25_batch_takeover 块样式）
- 种子 `ON CONFLICT (plugin_id) DO NOTHING`——**永不重置管理员选择**（spec §4）；`role_permissions` 种子 `('admin','admin.plugins')` 行（同 init_db.py:1746 admin.roles 惯例，超管本就放行、行供权限目录展示）
- 门控统一返回：404 + `{'error': {'code': 'PLUGIN_DISABLED', 'plugin': '<id>'}}`
- `gate_plugin` 挂载时校验蓝图 `url_prefix ∈ manifest.api_prefixes`，不一致**启动即抛**（spec §10）
- 前端不维护插件镜像清单；守卫对 `/plugins` 加载失败 **fail-open**（放行导航，后端蓝图门是硬墙）
- 禁用**不删任何数据**；智能写作目录一行不动（D3 壳层）
- registry TTL 缓存 3s；PUT 后即时 `invalidate()`（spec §2 小默认）
- 每任务：先写失败测试 → 跑挂 → 实现 → 跑绿 → 独立 commit；不提交 `.superpowers/`
- 注意 conftest `app` 夹具的 `get_db` 绑定污染坑：**新建的 Flask client 测试文件必须带 `_heal_leaked_get_db_bindings` autouse 夹具**（照抄 `server/tests/test_open_api_takeover_link.py` 的既有实现）

---

### Task 1: plugin_registry + 迁移 + 种子

**Files:**
- Create: `server/utils/plugin_registry.py`
- Create: `server/migrations/2026_10_25b_platform_plugins.py`（避开同日已占的 `2026_10_25_batch_takeover.py` 名）
- Modify: `server/app.py`（boot 块，紧随 batch_takeover 块之后）
- Test: `server/tests/test_plugin_registry.py`

**Interfaces:**
- Produces（后续所有任务依赖，签名逐字）:
  - `PLUGINS: dict[str, PluginManifest]`；`PluginManifest(id, name, description, version, default_enabled, routes, menu_prefixes, api_prefixes, process)`（frozen dataclass）
  - `plugin_enabled(pid: str) -> bool`（TTL 3s 缓存；无 DB 行回落 `default_enabled`；未知 pid → False）
  - `invalidate(plugin_id: str | None = None) -> None`
  - `disabled_menu_prefixes() -> tuple[str, ...]`
  - `filter_menu_items(items: list[dict]) -> list[dict]`（扁平列表按 path 前缀过滤——GET /menus 返回的是扁平行不是树）
  - `gate_plugin(bp, plugin_id: str)`（before_request 门 + url_prefix 校验）

- [ ] **Step 1: 写失败测试**（真库 `db_conn` fixture + `monkeypatch`；照抄 `test_takeover_repo.py` 的种子/清理纪律，自造 `pp_<uuid>` 行清 tails）

```python
# -*- coding: utf-8 -*-
"""plugin_registry 真库测试：TTL 缓存/失效回落/种子不覆盖/门校验/菜单过滤。"""
import os, sys, uuid
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils import plugin_registry as pr  # noqa: E402


@pytest.fixture
def db_rows(db_conn):
    ids = [f'pp_{uuid.uuid4().hex[:10]}' for _ in range(2)]
    with db_conn.cursor() as cur:
        for i in (0, 1):
            cur.execute("INSERT INTO platform_plugins (plugin_id, enabled) VALUES (%s, %s)",
                        (ids[i], i == 0))   # 第一行 enabled=true，第二行 false
    db_conn.commit()
    yield ids
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM platform_plugins WHERE plugin_id = ANY(%s)", (ids,))
    db_conn.commit()
    pr.invalidate()


def test_manifest_singleton_shape():
    m = pr.PLUGINS['smart-writing']
    assert m.process == 'se-node'
    assert m.menu_prefixes == ('/suggest-editor',)
    assert m.default_enabled is True


def test_enabled_reads_db_and_falls_back(db_rows):
    pr.invalidate()
    assert pr.plugin_enabled(db_rows[0]) is True
    assert pr.plugin_enabled(db_rows[1]) is False
    # 无 DB 行 → manifest default；未知 pid → False
    assert pr.plugin_enabled('smart-writing') in (True, False)  # 取决于种子行，单独断言：
    assert pr.plugin_enabled('no-such') is False


def test_unknown_plugin_defaults_false_without_row():
    pr.invalidate()
    assert pr.plugin_enabled('no-such') is False


def test_invalidate_rereads(db_conn, db_rows):
    pr.invalidate()
    assert pr.plugin_enabled(db_rows[1]) is False
    with db_conn.cursor() as cur:
        cur.execute("UPDATE platform_plugins SET enabled = TRUE WHERE plugin_id = %s", (db_rows[1],))
    db_conn.commit()
    assert pr.plugin_enabled(db_rows[1]) is False        # 缓存仍命中
    pr.invalidate(db_rows[1])
    assert pr.plugin_enabled(db_rows[1]) is True         # 失效后重读


def test_gate_validates_url_prefix():
    from flask import Blueprint
    bp = Blueprint('x', __name__, url_prefix='/wrong')
    with pytest.raises(RuntimeError, match='api_prefixes'):
        pr.gate_plugin(bp, 'smart-writing')


def test_filter_menu_items_by_disabled_prefix(db_conn, monkeypatch):
    monkeypatch.setattr(pr, 'plugin_enabled',
                        lambda pid: {'smart-writing': False}.get(pid, True))
    items = [{'path': '/suggest-editor'}, {'path': '/suggest-editor/x'},
             {'path': '/home'}, {'path': None}]
    out = pr.filter_menu_items(items)
    assert [i['path'] for i in out] == ['/home', None]


def test_seed_never_resets_admin_choice(db_rows):
    """种子幂等：先禁用再重跑迁移 run()，选择保留（spec §4 硬约束）。"""
    import importlib.util as ilu
    mp = os.path.join(os.path.dirname(__file__), '..', 'migrations',
                      '2026_10_25b_platform_plugins.py')
    spec = ilu.spec_from_file_location('_pp_mig', mp)
    mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with db_conn.cursor() as cur:  # smart-writing 行强制禁用（模拟管理员选择）
        cur.execute("INSERT INTO platform_plugins (plugin_id, enabled) VALUES ('smart-writing', FALSE)"
                    " ON CONFLICT (plugin_id) DO UPDATE SET enabled = FALSE")
    db_conn.commit()
    mod.run()
    mod.run()  # 跑两遍证幂等
    with db_conn.cursor() as cur:
        cur.execute("SELECT enabled FROM platform_plugins WHERE plugin_id = 'smart-writing'")
        assert cur.fetchone()[0] is False
```

> 注：`test_enabled_reads_db_and_falls_back` 中 smart-writing 行断言在种子存在时恒为 True——实现时直接写死 `assert pr.plugin_enabled('smart-writing') is True`（迁移种子已跑，Task 1 内完成迁移后成立）。

- [ ] **Step 2: 跑挂** → `cd server && python -m pytest tests/test_plugin_registry.py -q` → FAIL（模块不存在）
- [ ] **Step 3: 实现** `server/utils/plugin_registry.py`：

```python
"""第一方插件注册表（plugin-architecture spec §3/§4）——唯一真相源。

manifest 声明挂载点；开关状态存 platform_plugins（迁移种子 ON CONFLICT DO
NOTHING，不覆盖管理员选择）。plugin_enabled 进程内 TTL 缓存 3s——当前部署
为 proxy 受管单 Flask 实例，安全；多实例需换集中缓存（spec §4 注）。
"""
import time
from dataclasses import dataclass

from flask import jsonify

from db import get_db

CACHE_TTL_S = 3.0


@dataclass(frozen=True)
class PluginManifest:
    id: str
    name: str
    description: str
    version: str
    default_enabled: bool = False
    routes: tuple[str, ...] = ()
    menu_prefixes: tuple[str, ...] = ()
    api_prefixes: tuple[str, ...] = ()
    process: str | None = None


PLUGINS: dict[str, PluginManifest] = {
    'smart-writing': PluginManifest(
        id='smart-writing', name='智能写作',
        description='协作式智能文档编辑（词表/规则/检查/导入导出）',
        version='1.0.0', default_enabled=True,
        routes=('SuggestEditorList', 'SuggestEditorDoc'),
        menu_prefixes=('/suggest-editor',),
        api_prefixes=('/suggest-editor',),
        process='se-node',
    ),
}

_cache: dict[str, tuple[float, bool]] = {}


def invalidate(plugin_id: str | None = None) -> None:
    if plugin_id is None:
        _cache.clear()
    else:
        _cache.pop(plugin_id, None)


def plugin_enabled(plugin_id: str) -> bool:
    m = PLUGINS.get(plugin_id)
    if not m:
        return False
    now = time.monotonic()
    hit = _cache.get(plugin_id)
    if hit and now - hit[0] < CACHE_TTL_S:
        return hit[1]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT enabled FROM platform_plugins WHERE plugin_id = %s",
                        (plugin_id,))
            row = cur.fetchone()
    enabled = bool(row[0]) if row else m.default_enabled
    _cache[plugin_id] = (now, enabled)
    return enabled


def disabled_menu_prefixes() -> tuple[str, ...]:
    out: list[str] = []
    for m in PLUGINS.values():
        if not plugin_enabled(m.id):
            out.extend(m.menu_prefixes)
    return tuple(out)


def filter_menu_items(items: list[dict]) -> list[dict]:
    """GET /menus 出参过滤（扁平行）：禁用插件的 path 前缀命中项整行剔除。"""
    prefixes = disabled_menu_prefixes()
    if not prefixes:
        return items
    return [i for i in items
            if not any((i.get('path') or '').startswith(p) for p in prefixes)]


def gate_plugin(bp, plugin_id: str):
    """蓝图 before_request 门：禁用 → 404 PLUGIN_DISABLED（spec §2 小默认）。
    挂载即校验 url_prefix ∈ api_prefixes，不一致启动即抛（spec §10）。"""
    m = PLUGINS.get(plugin_id)
    if not m:
        raise RuntimeError(f'unknown plugin: {plugin_id}')
    prefix = getattr(bp, 'url_prefix', None)
    if prefix and prefix not in m.api_prefixes:
        raise RuntimeError(
            f'plugin {plugin_id}: blueprint url_prefix {prefix} '
            f'不在 api_prefixes {m.api_prefixes}')

    @bp.before_request
    def _plugin_gate():
        if not plugin_enabled(plugin_id):
            return jsonify({'error': {'code': 'PLUGIN_DISABLED',
                                      'plugin': plugin_id}}), 404
        return None
```

迁移 `2026_10_25b_platform_plugins.py`：

```python
# -*- coding: utf-8 -*-
"""幂等迁移：platform_plugins 表 + 按注册表种子 + admin.plugins 权限目录行。

种子 ON CONFLICT (plugin_id) DO NOTHING——重启/重跑永不重置管理员选择
（plugin-architecture spec §4）。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db


def run():
    from utils.plugin_registry import PLUGINS
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS platform_plugins ("
            " plugin_id TEXT PRIMARY KEY,"
            " enabled BOOLEAN NOT NULL DEFAULT FALSE,"
            " config JSONB NOT NULL DEFAULT '{}',"
            " updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
            " updated_by TEXT)")
        for m in PLUGINS.values():
            cur.execute(
                "INSERT INTO platform_plugins (plugin_id, enabled) VALUES (%s, %s)"
                " ON CONFLICT (plugin_id) DO NOTHING",
                (m.id, m.default_enabled))
        cur.execute(
            "INSERT INTO role_permissions (role_id, permission_key)"
            " VALUES ('admin', 'admin.plugins') ON CONFLICT DO NOTHING")
        conn.commit()
    print("platform plugins migration ready.")


if __name__ == "__main__":
    run()
```

app.py boot 块（照抄 batch_takeover 块样式，变量名 `_ilu_pp/_mp_pp/_spec_pp/_m_pp`，warning 文案 `'platform plugins migration on boot failed: %s'`）。

- [ ] **Step 4: 跑绿**：单文件 + 真库验证迁移两遍幂等（`cd server && python migrations/2026_10_25b_platform_plugins.py` ×2）+ `python -c "import app"` boot 块干净
- [ ] **Step 5: Commit**：`feat(plugins): 插件注册表单真相源 + platform_plugins 迁移种子（不覆盖管理员选择）`

---

### Task 2: 插件管理 API

**Files:**
- Create: `server/routes/plugins_admin.py`
- Modify: `server/app.py`（import + register，对齐相邻行）
- Test: `server/tests/test_plugins_admin_api.py`（**带 heal 夹具**，照抄 test_open_api_takeover_link.py）

**Interfaces:**
- Consumes: Task 1 的 `PLUGINS/plugin_enabled/invalidate`。
- Produces（Task 4/6/8 依赖）:
  - `GET /api/plugins` → 200 `[{id,name,description,version,enabled}]`（login_required）
  - `PUT /api/plugins/<id>` body `{enabled: bool}` → 200 `{id, enabled}`；未知 id 404；非布尔 400；`require_permission('admin.plugins')` + oplog `plugin.update` + `invalidate(id)`

- [ ] **Step 1: 失败测试**（真实 JWT 走 `auth.create_token`——模式照抄 test_batch_execution_safety_p0.py 的 token 构造；DB 直写开关行 + `pr.invalidate()` 控制状态；PUT 用例断言 oplog 行与缓存即时失效）

核心用例（断言精确）：
1. `GET` 未登录 401；admin token 200 返回含 smart-writing 且 `enabled` 为真（种子后）。
2. `PUT` guest token（developer/guest 角色）403。
3. `PUT` admin `{enabled:false}` → 200；随后 `GET` 该插件 `enabled=false`（同进程缓存已被 invalidate 证即时生效）；`operation_logs` 出现 `(plugin.update, platform_plugin, smart-writing)` 行。
4. `PUT` 未知 id → 404；`{}` / `{enabled:'yes'}` → 400。
5. （heal 夹具 autouse 全文件生效。）

- [ ] **Step 2: 跑挂** → 404（路由不存在）
- [ ] **Step 3: 实现**：

```python
"""第一方插件管理 API（plugin-architecture spec §4）。"""
from flask import Blueprint, jsonify, g, request

from auth import login_required, require_permission
from db import get_db
from utils import plugin_registry
from utils.operation_log import log_operation

plugins_bp = Blueprint('plugins', __name__, url_prefix='/plugins')


@plugins_bp.route('', methods=['GET'])
@login_required
def list_plugins():
    return jsonify([{
        'id': m.id, 'name': m.name, 'description': m.description,
        'version': m.version, 'enabled': plugin_registry.plugin_enabled(m.id),
    } for m in plugin_registry.PLUGINS.values()])


@plugins_bp.route('/<plugin_id>', methods=['PUT'])
@require_permission('admin.plugins')
def set_plugin_enabled(plugin_id):
    m = plugin_registry.PLUGINS.get(plugin_id)
    if not m:
        return jsonify({'error': '插件不存在'}), 404
    body = request.get_json(silent=True) or {}
    enabled = body.get('enabled')
    if not isinstance(enabled, bool):
        return jsonify({'error': 'enabled 必须为布尔值'}), 400
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO platform_plugins (plugin_id, enabled, updated_at, updated_by)"
                " VALUES (%s, %s, NOW(), %s)"
                " ON CONFLICT (plugin_id) DO UPDATE SET enabled = EXCLUDED.enabled,"
                " updated_at = NOW(), updated_by = EXCLUDED.updated_by",
                (plugin_id, enabled, g.current_user.get('userId')))
        conn.commit()
    plugin_registry.invalidate(plugin_id)
    log_operation('plugin.update', 'platform_plugin', plugin_id, m.name,
                  f'{"启用" if enabled else "禁用"}插件 {m.name}')
    return jsonify({'id': plugin_id, 'enabled': enabled})
```

- [ ] **Step 4: 跑绿**（含 `tests/test_plugin_registry.py` 邻居回归）
- [ ] **Step 5: Commit**：`feat(plugins): 插件管理 API（GET/PUT + admin.plugins 权限 + oplog + 即时失效）`

---

### Task 3: 智能写作蓝图挂门（后端门落地）

**Files:**
- Modify: `server/app.py`（`app.register_blueprint(suggest_editor_bp)` 之后一行 `plugin_registry.gate_plugin(suggest_editor_bp, 'smart-writing')` + import）
- Test: `server/tests/test_plugin_gate_se.py`（真库 + 真实 app client + JWT）

**Interfaces:**
- Consumes: Task 1 `gate_plugin`。
- Produces: `/api/suggest-editor/*` 全部 41 端点受门控（含附件服务）。

- [ ] **Step 1: 失败测试**（admin token，DB 直写开关 + `pr.invalidate('smart-writing')`）：

```python
def test_se_gate_blocks_when_disabled(client, db_conn, admin_token):
    _set_enabled(db_conn, False)
    r = client.get('/api/suggest-editor/documents', headers=auth_headers(admin_token))
    assert r.status_code == 404
    assert r.get_json() == {'error': {'code': 'PLUGIN_DISABLED', 'plugin': 'smart-writing'}}

def test_se_gate_passes_when_enabled(client, db_conn, admin_token):
    _set_enabled(db_conn, True)
    r = client.get('/api/suggest-editor/documents', headers=auth_headers(admin_token))
    assert r.status_code != 404 or r.get_json().get('error', {}).get('code') != 'PLUGIN_DISABLED'
    # 启用态下正常业务响应（200/空列表），绝不能是 PLUGIN_DISABLED
```

（`_set_enabled` = UPSERT 行 + `pr.invalidate('smart-writing')`；测试尾巴恢复 enabled=true + invalidate——**必须恢复**，否则污染同进程后续套件。加 autouse fixture 兜底恢复。）

- [ ] **Step 2: 跑挂** → 禁用用例失败（无门，返回 200）
- [ ] **Step 3: 实现**（app.py 一行挂门）
- [ ] **Step 4: 跑绿** + 回归 `tests/test_plugin_registry.py tests/test_plugins_admin_api.py` + 既有 se 相关后端测试（若有：`rg -l suggest server/tests` 先查，有则全跑）
- [ ] **Step 5: Commit**：`feat(plugins): 智能写作蓝图挂门——禁用即 41 端点 404 PLUGIN_DISABLED`

---

### Task 4: 前端插件门（store + 守卫 + meta.plugin）

**Files:**
- Create: `src/api/plugins.ts`、`src/stores/plugins.ts`、`src/router/pluginGuard.ts`
- Modify: `src/router/index.ts`（两条 suggest 路由加 `meta.plugin`；守卫第 4/5 步之间接线）
- Test: `src/stores/__tests__/plugins.test.ts`、`src/router/__tests__/pluginGuard.test.ts`

**Interfaces:**
- Consumes: Task 2 的 `GET /api/plugins`。
- Produces（Task 6/8 依赖）: `usePluginsStore()`（`ensureLoaded(): Promise<void>` 单飞、`enabled(id): boolean`、`refresh()`）；`pluginRouteAllowed(to): Promise<boolean>`（fail-open）。

- [ ] **Step 1: 失败测试**
  - store：mock `@/api/plugins`——`ensureLoaded` 并发两次只发一次请求（单飞）；`enabled` 按 list 过滤；`refresh` 强制重拉。
  - pluginGuard：无 `meta.plugin` 恒放行；有且 store enabled → true；有且 disabled → false；`ensureLoaded` reject → **放行**（fail-open）。
- [ ] **Step 2: 跑挂** → 模块不存在
- [ ] **Step 3: 实现**

`src/api/plugins.ts`（对齐 `@/utils/request` 既有导出 get/put）：

```ts
import { get, put } from '@/utils/request'

export interface PluginInfo {
  id: string; name: string; description: string; version: string; enabled: boolean
}

export function getPlugins() { return get<PluginInfo[]>('/plugins') }
export function setPluginEnabled(id: string, enabled: boolean) {
  return put<{ id: string; enabled: boolean }>(`/plugins/${encodeURIComponent(id)}`, { enabled })
}
```

`src/stores/plugins.ts`：

```ts
import { defineStore } from 'pinia'
import { getPlugins } from '@/api/plugins'

/** 第一方插件启用集（plugin-architecture spec §5 前端挂载点）。
 * 数据源唯一 GET /plugins；ensureLoaded 单飞；导航守卫 fail-open（后端门是硬墙）。 */
export const usePluginsStore = defineStore('plugins', {
  state: () => ({
    enabledSet: new Set<string>(),
    loaded: false,
    _pending: null as Promise<void> | null,
  }),
  actions: {
    async ensureLoaded(): Promise<void> {
      if (this.loaded) return
      if (!this._pending) {
        this._pending = getPlugins().then((list) => {
          this.enabledSet = new Set((list ?? []).filter((p) => p.enabled).map((p) => p.id))
          this.loaded = true
        }).finally(() => { this._pending = null })
      }
      return this._pending
    },
    enabled(id: string): boolean { return this.enabledSet.has(id) },
    async refresh(): Promise<void> { this.loaded = false; await this.ensureLoaded() },
  },
})
```

`src/router/pluginGuard.ts`：

```ts
import type { RouteLocationNormalized } from 'vue-router'

/** 插件路由门（spec §5）：meta.plugin 的路由在所属插件禁用时拦到 NotFound。
 * store 加载失败放行（fail-open）——后端蓝图门才是硬墙，/plugins 抖动不应锁死导航。 */
export async function pluginRouteAllowed(to: RouteLocationNormalized): Promise<boolean> {
  const pid = to.meta.plugin as string | undefined
  if (!pid) return true
  const { usePluginsStore } = await import('@/stores/plugins')
  const store = usePluginsStore()
  try {
    await store.ensureLoaded()
  } catch {
    return true
  }
  return store.enabled(pid)
}
```

守卫接线（index.ts，`return to.fullPath` 的第 4 步之后、第 5 步权限检查之前）：

```ts
  // 4.5 插件门：meta.plugin 所属插件被禁用 → NotFound（后端蓝图门兜底硬墙）
  if (!(await pluginRouteAllowed(to))) {
    return { name: 'NotFound' }
  }
```

两条 suggest 路由 meta 追加 `plugin: 'smart-writing'`。

- [ ] **Step 4: 跑绿** + 全量 `npx vitest run src/router src/stores` 回归 + `npx vue-tsc --noEmit`
- [ ] **Step 5: Commit**：`feat(plugins): 前端插件门——store 单飞加载 + 守卫 fail-open + suggest 路由打标`

---

### Task 5: 菜单过滤

**Files:**
- Modify: `server/routes/menus.py`（`list_menus` :108 出参过滤一行 + import）
- Test: `server/tests/test_plugin_menu_filter.py`（heal 夹具）

- [ ] **Step 1: 失败测试**：admin token GET `/api/menus`——DB 直写禁用 + invalidate 后，`path` 以 `/suggest-editor` 开头的行**不出现在响应**；启用恢复后出现（测试尾巴恢复）。用例自带自造菜单行（`ppm_<uuid>` path `/suggest-editor-e2e-probe`… 不行——前缀必须真命中 `/suggest-editor`；用真实存在的 suggest 菜单行（查 DB `SELECT ... WHERE path LIKE '/suggest-editor%'`，无则 INSERT 自造 `path='/suggest-editor-ppm-<uuid>'`——前缀 `/suggest-editor` 命中即可，尾巴删除）。
- [ ] **Step 2: 跑挂** → 禁用态行仍在响应
- [ ] **Step 3: 实现**：`return jsonify(plugin_registry.filter_menu_items(result))`
- [ ] **Step 4: 跑绿** + 既有 menus 相关测试回归（`rg -l "menus" server/tests` 有则跑）
- [ ] **Step 5: Commit**：`feat(plugins): 菜单出参按禁用插件前缀过滤（导航入口消失，DB 数据不动）`

---

### Task 6: 设置页「功能插件」tab

**Files:**
- Create: `src/components/admin/PluginToggleTable.vue`
- Modify: `src/views/admin/SystemSettings.vue`（el-tabs 第三格 `<el-tab-pane label="功能插件" name="plugins">`）
- Test: `src/components/admin/__tests__/PluginToggleTable.test.ts`

**Interfaces:**
- Consumes: Task 2 API（`getPlugins/setPluginEnabled`）+ Task 4 store（切换成功后 `refresh()`——同页其它导航的守卫下次取新值）。
- Produces: 管理入口 UI（E2E 用 API 直调，UI 由 vitest 覆盖）。

- [ ] **Step 1: 失败测试**：mock `@/api/plugins` + stub plugins store——挂载拉列表渲染行（名称/版本/开关态）；点 switch → `setPluginEnabled(id,false)` 以正确参数调用、UI 乐观翻转；请求 reject → 翻转回滚 + 错误提示（ElMessage stub 断言）。
- [ ] **Step 2: 跑挂** → 组件不存在
- [ ] **Step 3: 实现**：el-table（列：名称+描述 / 版本 / el-switch）；loading 态；switch change → 乐观置位 → API 失败回滚 + `ElMessage.error`（沿用项目 EP 组件测试惯例：真实 Element Plus 组件挂载）。
- [ ] **Step 4: 跑绿** + `npx vue-tsc --noEmit`
- [ ] **Step 5: Commit**：`feat(plugins): 系统设置新增功能插件 tab（开关乐观更新+失败回滚）`

---

### Task 7: proxy.py 生产门（WS 隧道 + se 进程停启）

**Files:**
- Modify: `server/proxy.py`（三处：`se_plugin_enabled()` 决策函数、`_tunnel_websocket` 入口门、主循环 se 进程按开关 reconcile）
- Test: `server/tests/test_proxy_plugin_gate.py`（纯函数级，不启 proxy）

**Interfaces:**
- Consumes: Task 1 `plugin_enabled('smart-writing')`。
- Produces: 生产拓扑的 WS/进程挂载点（开发态 dev:all 无 proxy，蓝图门+守卫已兜底——spec §5 边界）。

- [ ] **Step 1: 失败测试**：对决策函数直测——

```python
def test_se_process_action():
    from proxy import _se_process_action
    assert _se_process_action(enabled=True, running=False) == 'start'
    assert _se_process_action(enabled=True, running=True) == 'noop'
    assert _se_process_action(enabled=False, running=True) == 'stop'
    assert _se_process_action(enabled=False, running=False) == 'noop'

def test_se_plugin_enabled_fails_open(monkeypatch):
    import proxy
    def boom(pid): raise RuntimeError('db down')
    monkeypatch.setattr('utils.plugin_registry.plugin_enabled', boom)
    assert proxy.se_plugin_enabled() is True   # fail-open：开关面故障不掐在线协作
```

（`proxy.py` 可导入性：`sys.path` 指向 server/ 即可，`import proxy` 顶层只做常量定义不启动服务——若顶层有副作用则把可测函数下沉到模块顶部、main 逻辑守 `if __name__`，现状已如此。）

- [ ] **Step 2: 跑挂** → 函数不存在
- [ ] **Step 3: 实现**（proxy.py）：

```python
def se_plugin_enabled() -> bool:
    """智能写作插件开关（隧道门与进程停启共用）。直查库（经 plugin_registry
    TTL 缓存）。库不可达 fail-open：开关面故障不应立刻掐断在线协作，
    进程轮询下一轮自愈。"""
    try:
        from utils.plugin_registry import plugin_enabled
        return plugin_enabled('smart-writing')
    except Exception:
        return True


def _se_process_action(enabled: bool, running: bool) -> str:
    """纯决策函数（可单测）：'start' / 'stop' / 'noop'。"""
    if enabled and not running:
        return 'start'
    if not enabled and running:
        return 'stop'
    return 'noop'
```

- `_tunnel_websocket`（:307）入口加：`if not se_plugin_enabled():` 按该 handler 既有早退习惯回 403 并 return（实现时对照相邻分支的响应写法）。
- 主循环（:824 `se_proc = start_se()`）：改为 `se_proc = start_se() if se_plugin_enabled() else None`；新增守护线程每 10s 执行 reconcile——`_se_process_action(se_plugin_enabled(), se_proc 活着)` → start 则拉起并纳入 procs 管理 / stop 则 terminate 置 None（se_proc 用可变容器持有，实现时对齐主循环既有的进程表结构）。
- [ ] **Step 4: 跑绿** + `python -c "import sys; sys.path.insert(0,'server'); import proxy"` 导入干净
- [ ] **Step 5: Commit**：`feat(plugins): proxy 生产门——WS 隧道拒连禁用插件 + se 受管进程按开关停启（fail-open）`

---

### Task 8: E2E 真实链路

**Files:**
- Create: `e2e/plugin-toggle.spec.ts`

**Interfaces:**
- Consumes: `e2e/ai-full/helpers.ts`（`api/gotoWithAuth`）；Task 2/3/4/5 全链路。
- 确定性：全程真实栈、无 LLM（se 文档 CRUD 是纯 DB/文件操作；不打开编辑器 WS 页——开关断言不需要）。

- [ ] **Step 1: 写 spec**（单 test 主链路 + finally 恢复）：

1. `api PUT /plugins/smart-writing {enabled:true}` 前置恢复；`api POST /suggest-editor/documents`（payload 按 `server/routes/suggest_editor.py:134` 实际字段——title 必填，实现时读端点钉死）种 AITEST 文档拿 id。
2. `gotoWithAuth(page, '/home')` → 侧栏出现智能写作菜单项（选择器：先 `dbSeed` 查 `SELECT name FROM menus WHERE path LIKE '/suggest-editor%'` 拿真实菜单名再 `getByText`——菜单是 DB 数据，名字不硬编码）。
3. `api PUT {enabled:false}` → `page.reload()`（菜单与插件 store 均随 app init 重建）→ 菜单项 count 0。
4. `page.goto('/suggest-editor')` → NotFound 视图可见（`src/views/error/NotFound.vue` 的标志性文案，实现时钉死选择器）。
5. `api GET /suggest-editor/documents` → 404 + `error.code === 'PLUGIN_DISABLED'`。
6. `dbSeed SELECT count(*) FROM se_documents WHERE id='<docId>'` → ≥1（数据保留证据）。
7. `api PUT {enabled:true}` → `page.reload()` → 菜单恢复、`gotoWithAuth('/suggest-editor')` 列表页加载且 AITEST 文档可见。
8. 截图 `plugin-toggle-off`（步骤 4）与 `plugin-toggle-on`（步骤 7）；`finally`：恢复 enabled=true + 删 AITEST 文档。

- [ ] **Step 2: 跑绿**：`npx playwright test e2e/plugin-toggle.spec.ts`（栈已在跑；dev 栈 vite+3002）
- [ ] **Step 3: Commit**：`test(plugins): E2E 真实链路——开关全链路（菜单/路由/API/数据保留/恢复）`

---

### Task 9: 全量回归 + 上库

- [ ] **Step 1: 后端**：`cd server && python -m pytest tests/test_plugin_registry.py tests/test_plugins_admin_api.py tests/test_plugin_gate_se.py tests/test_plugin_menu_filter.py tests/test_proxy_plugin_gate.py tests/test_batch_execution_safety_p0.py tests/test_open_api_takeover_link.py tests/test_external_takeover_routes.py -q` 全绿
- [ ] **Step 2: 前端**：`npx vitest run`（全量绿）+ `npx vue-tsc --noEmit`
- [ ] **Step 3: E2E 相邻回归**：`npx playwright test e2e/suggest-editor.spec.ts e2e/ai-chat-plantuml.spec.ts`（se 主链路未回归即验收；plantuml 是本会话基线）
- [ ] **Step 4: Mimosa normal 扫描** + 与上轮封印 `94b11ec3` 基线 anchor-diff（新增须 triage）；公开面清单复核（本特性无新公开路由）
- [ ] **Step 5: `git push origin main` + 记忆更新**（新记忆：插件架构落地事实——registry 单真相源/heal 夹具复用/菜单 DB 名动态取/E2E 选择器纪律；更新 suggest-editor 记忆挂「已插件化」）
