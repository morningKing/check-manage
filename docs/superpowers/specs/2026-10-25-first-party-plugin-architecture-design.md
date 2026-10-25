# 第一方插件架构 + 智能写作首个挂入 设计

- 日期：2026-10-25
- 状态：设计已获用户确认（四项承重决策 + 七节设计 + 两项小默认），待 spec 评审
- 主诉：智能写作（suggest-editor）作为特色功能当前是一体化实现——路由静态注册、
  蓝图无条件挂载、Node 子进程随栈必启，没有任何「关掉它」的机制。按预先设想，
  这类功能应是**插件式的，可动态开启和关闭**；未来集成其它插件式功能也要遵循
  同一规范。本设计定义第一方插件规范，并把智能写作重构为首个挂入者。

## 1. 现状事实（2026-10-25 盘点）

**智能写作足迹**（物理隔离度已很高，缺的只是开关语义）：
- 前端：`src/router/index.ts:88-90` 两条静态路由（`/suggest-editor` 列表 +
  `/:docId` 编辑，lazy import）；`src/components/suggest-editor/`（35 文件，
  含 `core/` 引擎层）+ `src/views/suggest/`（2 视图）+ `src/api/suggestEditor.ts`。
  **系统其它部分对它的引用仅 router 一处**；其自身引用全局设施（request/
  store/storage）仅 4 处。
- 后端：`server/routes/suggest_editor.py` 独立蓝图（**41 端点**，
  `url_prefix='/suggest-editor'`，`app.py:70` 注册）；2 个独立迁移
  （`2026_10_18_suggest_editor_tables.py`、`2026_10_21_suggest_editor_attachments.py`）；
  数据表全 `se_` 前缀、无外部外键。
- Node 协作服务：`suggest-editor-server/`（Hocuspocus/Yjs WS，`:1234`，
  `SE_WS_PORT`）。生产由 `server/proxy.py:67-71` 作**受管子进程**拉起（与
  backend/mcp 同生命周期），WS 隧道在 `proxy.py:305-326`；开发由
  `package.json` `dev:all` 的 concurrently 拉起（`se:mock`）。
- 菜单入口：DB 菜单数据配置（init_db 无 suggest 种子）——数据驱动，非代码。
- 测试：9 个 vitest 文件（core/__tests__ + api）+ 9 个 e2e spec。
- **无任何整体关闭开关**（现状确认）。

**平台可复用先例**：DB 菜单显隐（数据驱动）、`ai_settings`/`mcp_servers` 的
`enabled` 列（DB 开关）、env 旋钮族、`require_permission` 权限键体系。

## 2. 关键决策（用户已确认）

| # | 决策 | 选择 |
|---|---|---|
| D1 | 动态语义 | **运行时即时开关**（管理页一键 on/off，无需重启；禁用=菜单隐藏+路由拦截+API 拒绝+WS 拒连+Node 进程停） |
| D2 | 规范野心 | **第一方模块化**（同仓同构建，无第三方安装/上传/沙箱概念） |
| D3 | 重构深度 | **壳层重构**（智能写作目录一行不动；注册表为唯一真相源 + 四挂载点门控） |
| D4 | 状态载体 | **独立表 `platform_plugins`**（不复用 ai_settings 加列） |

小默认（设计确认时明示，可推翻）：门控返回码 **404 + `code:'PLUGIN_DISABLED'`**
（对用户呈现「不存在」，管理员从 code 区分；451/503 语义不正、403 与权限不足混淆）；
开关生效窗口容忍 **≤3 秒** registry 缓存延迟（「即时」按秒级定义）。

## 3. 插件规范（manifest 契约）

`server/utils/plugin_registry.py` 为**唯一真相源**：

```python
@dataclass(frozen=True)
class PluginManifest:
    id: str                    # kebab-case，全系统唯一
    name: str                  # 展示名
    description: str
    version: str               # '1.0.0' 起步
    default_enabled: bool      # 种子缺省（不覆盖管理员既有选择）
    routes: tuple[str, ...]    # 前端路由 name（文档性声明）
    menu_prefixes: tuple[str, ...]   # DB 菜单 path 前缀（过滤依据）
    api_prefixes: tuple[str, ...]    # 后端门控前缀（须与蓝图 url_prefix 一致）
    process: str | None        # proxy.py 受管子进程名（无则 None）
```

核心函数：`plugin_enabled(pid) -> bool`（进程内 TTL 3s 缓存 + 库读）、
`invalidate()`（PUT 即时失效）、`gate_plugin(bp, pid)`（蓝图 before_request 门）、
`filter_menu_items(items)`（按禁用插件的 menu_prefixes 过滤菜单出参）。

前端**不维护镜像清单**：路由在 `meta.plugin = '<id>'` 标注所属插件；菜单过滤
在后端 menus API 返回时做（前端零改动）；插件元数据（启用集）由前端 store 从
`GET /api/plugins` 动态获取。单一真相源，杜绝两份登记漂移。

## 4. 状态存储与 API

- 表 `platform_plugins(plugin_id TEXT PK, enabled BOOLEAN NOT NULL,
  config JSONB NOT NULL DEFAULT '{}', updated_at TIMESTAMPTZ,
  updated_by TEXT)`。迁移**双注册**（`server/migrations/` 文件 +
  `app.py` 显式 boot 块）；种子按 registry 逐插件 `INSERT ... ON CONFLICT
  (plugin_id) DO NOTHING`——**重启不重置管理员选择**。`config` 为未来
  per-plugin 配置留位，本期恒 `'{}'`。
- `GET /api/plugins`（login_required）：返回全部插件
  `[{id, name, description, version, enabled}]`（元数据不敏感，前端 store
  与管理页共用）。
- `PUT /api/plugins/<id>`（`require_permission('admin.plugins')` + oplog
  `plugin.update`）：body `{enabled: bool}`；registry 外 id → 404；写
  `updated_by`；成功后 `invalidate()`。
- registry 单进程假设：当前部署为 proxy 受管单 Flask 实例，进程内缓存安全；
  多实例部署需换集中缓存（文档注明，非本期）。

## 5. 四挂载点门控

| 挂载点 | 机制 | 禁用表现 |
|---|---|---|
| 菜单 | menus API 返回时按禁用插件 `menu_prefixes` 过滤（**不改 DB 菜单数据**） | 入口消失 |
| 前端路由 | 守卫在 public 检查后查 `to.meta.plugin` + pluginStore；store 未就绪先 `await ensureLoaded()`（防首屏竞态） | 直达 URL → 404 页 |
| 后端蓝图 | `gate_plugin(suggest_editor_bp, 'smart-writing')`：before_request 查 `plugin_enabled` | 404 + `{'error': {'code': 'PLUGIN_DISABLED', 'plugin': '<id>'}}` |
| WS 隧道+Node 进程 | 生产 `proxy.py`：隧道握手时直查 DB（带短 TTL 缓存）拒连禁用插件；轮询线程（~10s）发现禁用 → terminate 受管子进程，启用 → spawn（复用既有受管机制） | 协作服务不可达且进程不占资源 |

已知边界：
- 开发态（`dev:all`）：se 进程由 concurrently 拉起不受 proxy 管，**不杀进程**；
  蓝图门 + 前端守卫已挡住全部正常路径（页面进不去自然无 WS）。生产态才有隧道
  门 + 进程停启。
- 前端 localStorage 菜单缓存（`check-manage:menus`）可能短暂残留已禁用入口：
  点击后被路由守卫拦到 404，可接受。
- TTL 3s 窗口内旧请求仍放行（秒级「即时」语义）。

## 6. 智能写作挂入（首插件）

- registry 登记：`id='smart-writing'`，name 智能写作，
  `menu_prefixes=('/suggest-editor',)`，`api_prefixes=('/suggest-editor',)`，
  `process='se-node'`，`default_enabled=True`（现状零破坏）。
- 前端两条路由加 `meta.plugin='smart-writing'`。
- `app.py` 蓝图注册处挂 `gate_plugin(...)` 一行。
- `proxy.py`：隧道门 + se-node 进程按开关停启。
- **禁用不删任何数据**：`se_` 表与文档全保留，只是入口与 API 关闭；再启用
  数据原样回来。

## 7. 管理界面

系统设置页新增「功能插件」tab：el-table（名称/描述/版本/el-switch 开关），
权限 `admin.plugins`；切换即 `PUT`，乐观更新 + 失败回滚 + 错误提示。

## 8. 测试策略（真实链路）

- pytest：registry TTL 缓存与失效；gate 禁用 404+code / 启用放行；menus 过滤；
  PUT 权限边界（guest 403 / admin 200 + oplog 行）；种子幂等（先 PUT 禁用再
  重跑种子，enabled 仍为 false）。
- vitest：pluginStore（ensureLoaded 并发单飞/启用集）；路由守卫 meta.plugin
  拦截；设置 tab 组件（开关交互/失败回滚）。
- Playwright E2E 验收（核心场景一条龙）：种子一篇 se 文档 → 管理页关闭
  smart-writing → 菜单入口消失 + 直达 `/suggest-editor` 404 页 + API
  `PLUGIN_DISABLED` → 再开启 → 菜单恢复 + 列表加载 + 文档数据仍在。
  WS 隧道/进程停启属生产拓扑（proxy），开发栈不覆盖，由 pytest 对 proxy 门
  逻辑的单测 + 代码评审覆盖。

## 9. 非目标

- 不做第三方插件安装/上传/沙箱/热插包（D2）。
- 不做 per-user / per-role 粒度开关（全局）。
- 不做插件依赖图、插件排序。
- 不动智能写作内部结构（D3 壳层）；不迁移目录。
- 不做多实例部署的集中式开关缓存（单进程假设，文档注明）。

## 10. 未来插件接入指南（规范速查）

1. `plugin_registry.py` 登记一行 manifest（含 `default_enabled`）。
2. 前端路由打 `meta.plugin='<id>'`；菜单数据 path 落在 `menu_prefixes` 内。
3. 后端蓝图在 `app.py` 注册处 `gate_plugin(bp, '<id>')`；若有受管子进程，
   `proxy.py` 进程表登记。

约束：id 全局唯一 kebab-case；`api_prefixes` 必须与蓝图 url_prefix 一致
（gate 挂载时校验，不一致启动即抛）；禁用永远不删数据。
