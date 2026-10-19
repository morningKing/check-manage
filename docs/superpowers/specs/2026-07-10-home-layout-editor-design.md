# 首页自由布局编辑器 设计文档

日期：2026-07-10

## 背景与目标

当前首页区块（`home_widgets` 表 / `WidgetConfig`）只有一个 `order` 字段，`HomeView.vue` 永远按 `order` 单列纵向堆叠渲染，管理员在「系统设置 → 首页配置」也只能拖拽排序（改 `order`），无法让区块并排、无法调整区块宽高。

目标：让管理员可以在「系统设置 → 首页配置」里用**自由拖拽 + 缩放**的方式排布首页区块（类似仪表盘编辑器），保存后**全局生效**（所有用户看到同一份布局，仍受 `visibleRoles`/`enabled` 控制可见性）。移动端不做自由布局，自动降级为现有的单列纵向堆叠。

本设计不改变：区块的新增/删除/启用/停用/角色可见性/内容编辑（`WidgetEditDialog`）逻辑，也不引入"每用户个性化布局"。

## 数据模型

`home_widgets` 表新增 4 列（12 列网格坐标系，行高固定为 30px/行）：

| 列 | 类型 | 说明 |
|----|------|------|
| `layout_x` | INT NOT NULL DEFAULT 0 | 起始列（0-11） |
| `layout_y` | INT NOT NULL DEFAULT 0 | 起始行（从 0 开始，向下增长） |
| `layout_w` | INT NOT NULL DEFAULT 12 | 跨列数（1-12） |
| `layout_h` | INT NOT NULL DEFAULT 4 | 跨行数（≥1） |

`order` 列保留，但不再由前端直接维护——每次保存布局时，后端在同一事务里按 `(layout_y, layout_x)` 排序重算并写回 `order`。这样移动端兜底渲染（`visibleWidgets` 按 `order` 排序单列堆叠）完全复用现有代码，不用改。

### 迁移

新增一次性迁移脚本（`init_db.py` 里的 DDL + 一次性回填，参照现有 `home_widgets` 建表方式），对已存在的行按当前 `order` 升序回填：

```
x = 0, w = 12（保持现状：全宽单列）
h = 4（统一默认值）
y = 累计值：第一个区块 y=0，第二个 y=4，第三个 y=8，以此类推（即 y = 前面所有区块的 h 之和）
```

回填后首页视觉与升级前完全一致（仍是从上到下、每个区块占满宽度），管理员之后可自行拖拽调整。

## 后端 API（`server/routes/home_widgets.py`）

1. `_row_to_json` 返回体新增：
   ```json
   "layout": { "x": 0, "y": 0, "w": 12, "h": 4 }
   ```
2. 新增 `PUT /home-widgets/layout`（`@require_permission('admin.home_widgets')`）：
   - body: `{"layout": [{"id": "...", "x": 0, "y": 0, "w": 12, "h": 4}, ...]}`
   - 校验：`x∈[0,11]`、`w∈[1,12]`、`x+w<=12`、`y>=0`、`h>=1`，否则 400。
   - 同一事务内批量 `UPDATE`，随后按 `(y, x)` 排序重算并写回所有行的 `order`。
   - 返回更新后的完整区块列表（与 `GET ?all=true` 同构）。
3. `POST /home-widgets`（新增区块）：默认坐标从"追加到网格底部"计算——`x=0, y=MAX(layout_y+layout_h) OVER all rows (无则0), w=12, h=4`，不再用 `MAX(order)+1`。
4. 删除 `PUT /home-widgets/order` 端点（不再有纯排序 UI 调用它；`vuedraggable` 依赖一并移除，除非其他页面仍在用则保留依赖只删此处调用）。

## 前端 · 管理端编辑体验

`SystemSettings.vue` 的「首页配置」Tab：

- 原有 `draggable` 纵向列表替换为新组件 `src/components/admin/HomeLayoutEditor.vue`，使用 **`grid-layout-plus`**（npm 包，Vue3 原生、TypeScript 类型、MIT 协议，是 `vue-grid-layout` 的维护中 Vue3 重写版，支持拖拽/缩放/碰撞检测，~30KB gzip）渲染 12 列网格，行高 30px。
- 每个 `grid-item` 内容是一张紧凑卡片：区块标题 + 类型标签 + 启用开关 + 编辑/删除图标按钮——功能与现在列表行完全一致，只是外壳从"列表行"换成"网格块"。编辑按钮仍打开现有 `WidgetEditDialog`（内容编辑逻辑不变）。
- 拖拽/缩放中仅更新本地网格状态（`@layout-updated` 回调），不逐动作发请求；顶部新增「保存布局」按钮，点击后统一提交 `PUT /home-widgets/layout`。未保存时离开 Tab 需二次确认（沿用 Element Plus `ElMessageBox.confirm`）。
- 新增区块（下拉菜单，逻辑不变）后自动出现在网格底部，管理员可立即拖拽调整。
- 该组件与 `grid-layout-plus` 依赖只在 `SystemSettings.vue` 路由的懒加载 chunk 里引入，不影响访客首页 bundle 体积。

## 前端 · 首页渲染（`HomeView.vue`）

- 用 `window.matchMedia('(min-width: 768px)')`（配合 `resize` 监听）计算 `isDesktopLayout`。
- 桌面态：容器设 `display: grid; grid-template-columns: repeat(12, 1fr); gap: 16px`；每个 `.widget-wrapper` 内联样式 `grid-column: ${x+1} / span ${w}; grid-row: ${y+1} / span ${h}`（由 `visibleWidgets` 提供的 `layout` 字段计算，纯函数、可单测）。不引入 `grid-layout-plus`，因为运行时只需静态定位，没有交互。
- 移动态：不设置 grid-column/grid-row 内联样式，容器退回现在的 `display: block` 纵向堆叠，顺序沿用 `visibleWidgets` 现有的按 `order` 排序（该排序已在保存布局时由后端按 (y,x) 重算，因此和桌面视觉顺序天然一致）。

## 错误处理

- 布局保存请求失败：`ElMessage.error` 提示，网格状态保持本地未保存值，管理员可重试保存或刷新页面丢弃改动。
- 坐标校验失败（后端 400）：提示具体原因（如"区块宽度超出网格范围"），不落库、不影响其他区块。
- `grid-layout-plus` 自带碰撞检测会自动把重叠区块推开，避免管理员手动拖出重叠布局。

## 测试

- 后端 pytest（`server/tests/test_routes_home_widgets.py`）：新增 `PUT /home-widgets/layout` 的批量更新、坐标越界校验、权限校验（非 admin.home_widgets 返回 403）、`order` 按 (y,x) 重算回填的断言；`POST` 新增区块默认坐标追加到底部的断言。
- 前端 vitest：坐标 → CSS Grid 内联样式转换函数的单测；`systemConfig` store 新增的 `updateLayout` action 单测（含请求失败回滚场景）。
- Playwright（遵循 CLAUDE.md 强制要求，因为这是纯前端可视化交互变更）：
  1. 管理员登录，进入「设置中心 → 首页配置」，拖拽一个区块到新位置并缩放，点击「保存布局」。
  2. 打开首页，验证区块实际渲染位置与保存的坐标一致（截图存 `.playwright-mcp/`）。
  3. 缩小浏览器窗口到 <768px，验证首页退化为单列纵向堆叠。
- `proxy.py` 验证：本功能不涉及 SSE/流式/新请求头，`_is_sse()`/请求头白名单均无需改动，可跳过 8080 验证。

## 文档同步

`docs/user-guide/admin/system-settings.md` 的「首页配置」章节需更新：把"支持拖拽排序"改为"支持自由拖拽位置与缩放大小"，补充新的网格编辑器截图（存 `docs/user-guide/_images/`）。

## 范围之外（明确不做）

- 每用户个性化布局（本设计布局是全局的）。
- 移动端自由拖拽布局（移动端始终是单列堆叠，非本设计目标）。
- 迁移/复用仪表盘（`dashboards` 表）的 `w`/`h` 编辑器组件——两者数据模型相似但是独立表、独立编辑入口，本设计不做代码层面的合并抽象（YAGNI，避免过早耦合两个概念上不同的功能）。
