# 首页区块面板拖拽新增 设计文档

日期：2026-07-11

## 背景与目标

`2026-07-10-home-layout-editor-design.md`（已合并到 `main`）实现了首页区块的自由拖拽/缩放网格布局，但「新增区块」仍然是老的下拉菜单流程：选类型 → 后端自动把新区块追加到网格底部（`x=0,w=12,h=4`）→ 管理员再手动拖到想要的位置。用户反馈希望更接近 WordPress 区块编辑器的体验：有一个常驻的「区块面板」，可以直接把某个类型的卡片拖到网格里任意位置，一步创建到目标位置。

目标：在「系统设置 → 首页配置」新增一个区块面板组件，支持"拖拽创建到指定位置"，同时保留"点击创建到底部"作为不想精确定位时的快捷方式。**不新增区块类型**——面板里仍是现有 7 种可新增类型（`custom-markdown`/`data-card`/`quick-form`/`chart`/`todo`/`activity`/`announcement`，对应后端 `create_home_widget` 的 `allowed_types` 白名单）。

## 交互设计

「首页配置」Tab 布局改为左右两栏：

```
┌─────────────┬──────────────────────────────────┐
│  区块面板    │  [保存布局]  有未保存的修改        │
│  ┌───────┐  │  ┌─────────────┬─────────────┐   │
│  │Markdown│  │  │  欢迎卡片    │  快捷入口    │   │
│  ├───────┤  │  └─────────────┴─────────────┘   │
│  │数据卡片 │  │  ┌───────────────────────────┐   │
│  ├───────┤  │  │        统计概览              │   │
│  │快速录入 │  │  └───────────────────────────┘   │
│  │  ...   │  │                                  │
│  └───────┘  │                                  │
└─────────────┴──────────────────────────────────┘
```

- 左侧面板卡片：图标 + 类型名称，与现有下拉菜单选项一一对应（复用 `WIDGET_TYPE_LABELS`/图标）。
- **点击卡片**：行为与现状完全一致——调用 `POST /home-widgets`（不带 `layout`），后端按"追加到网格底部"计算坐标（`x=0, y=maxBottom, w=12, h=4`），前端 `HomeLayoutEditor` 的 id-集合变化侦测会自动把新区块纳入网格。
- **拖拽卡片到网格**：松手位置换算成网格坐标 `(x, y)`，创建时用 `w=6, h=4`（半行宽——如果还给满宽 12，则 `x` 只能是 0，拖拽定位失去意义）。创建请求 `POST /home-widgets` 带上 `layout: {x, y, w: 6, h: 4}`。
- 拖到的位置与现有区块**允许重叠**，不做校验/自动避让——与已上线的 `PUT /home-widgets/layout` 端点行为一致（该端点当初的审查已确认"重叠交给管理员事后手动拖开，client 端 `grid-layout-plus` 在拖拽时有实时碰撞检测"是既定设计），保持一致，不重复造轮子。

## 拖拽坐标计算的技术选型（已验证）

写实施计划前已直接查过已装的 `grid-layout-plus@1.1.1` 真实类型声明（`node_modules/grid-layout-plus/dist/components/types.d.ts`）：`GridLayoutProps` **没有** `isDroppable`/`droppingItem` 之类"外部拖入"机制，公开导出（`index.d.ts`）也只有 `GridLayout`/`GridItem` 两个组件，没有可复用的坐标换算工具函数。因此确定走**兜底方案**：原生 HTML5 Drag and Drop API（面板卡片 `draggable="true"`，`dragstart` 写入 `dataTransfer`），网格容器监听 `dragover.prevent` + `drop`，drop 时用 `getBoundingClientRect()` 换算相对像素坐标，再手动转成网格坐标。

换算公式**镜像自 `grid-layout-plus` 内部真实实现**（`node_modules/grid-layout-plus/es/components/grid-item.vue.mjs` 约 264-270 行的 `calcXY`/`colWidth` 计算），保证换算结果与库自身拖拽定位完全一致：

```
colWidth = (containerWidth - marginX * (colNum + 1)) / colNum
x = round((left - marginX) / (colWidth + marginX))
y = round((top - marginY) / (rowHeight + marginY))
```

其中 `colNum=12`、`rowHeight=30`、`marginX=marginY=12` 与 `HomeLayoutEditor.vue` 里 `GridLayout` 已经在用的固定值一致。`x` 最终 clamp 到 `[0, 12-w]`，`y` clamp 到 `>=0`（网格允许向下无限增长，不设上限）。

## 后端 API（`server/routes/home_widgets.py`）

`POST /home-widgets`（`create_home_widget`）新增可选请求体字段 `layout: {x, y, w, h}`：

- **传了 `layout`**：校验规则与 `PUT /home-widgets/layout` 完全一致（`x∈[0,11]`、`1<=w<=12` 且 `x+w<=12`、`y>=0`、`h>=1`，否则 400），校验通过后直接用传入的坐标插入，不再计算"追加到底部"的默认值；插入成功后，**复用 `PUT /home-widgets/layout` 已有的"按 `(layout_y, layout_x)` 重算 `order`"逻辑**，对全部区块（含新插入的这个）重新计算并写回 `order`——理由：拖到网格中间某处创建的区块，如果 `order` 仍然只是简单的"当前最大值 +1"，会导致移动端单列回退顺序和桌面网格阅读顺序不一致，这与已上线的 `PUT /home-widgets/layout` 端点保证的"顺序一致性"矛盾。两处校验+重算逻辑应提成同一个内部辅助函数复用，避免重复实现。
- **不传 `layout`**（现状的点击创建路径）：完全保持现有行为——`x=0, y=maxBottom, w=12, h=4`，`order` 仍是简单的"当前最大值 +1"（因为永远追加在最底部，本来就与阅读顺序一致，不需要重算）。

不新增端点，不改动其他现有端点；`GET`/`PUT /home-widgets`/`PUT /home-widgets/layout`/`DELETE` 均不受影响。

## 前端

- 新组件 `src/components/admin/HomeBlockPalette.vue`：渲染 7 张可拖拽/可点击的类型卡片；`emit('add-at-bottom', widgetType)`（点击）与 `emit('drop-at', { widgetType, x, y })`（拖拽落点，坐标已经过网格换算和 clamp）。
- `src/components/admin/HomeLayoutEditor.vue`：网格容器新增 drop 处理（技术选型见上一节），负责把落点像素坐标换算为网格坐标（如走兜底方案）；不负责发起创建请求，只负责计算坐标后把事件继续往上抛给 `SystemSettings.vue`。
- `src/views/admin/SystemSettings.vue`："首页配置" Tab 模板改为左右两栏（`HomeBlockPalette` + 现有 `HomeLayoutEditor`），新增 `handleAddAtBottom`（复用现有 `handleAddWidget` 逻辑，只是触发源从下拉菜单变成面板点击）和 `handleDropAt(widgetType, x, y)`（调用扩展后的 `systemConfigStore.createWidget()`，多传一个 `layout` 参数）。
- `src/stores/systemConfig.ts` 的 `createWidget` action、`src/api/systemConfig.ts` 的 `createHomeWidget` 函数：`data` 参数新增可选 `layout?: WidgetLayout` 字段，透传给后端；不传时行为不变。
- 原来的「新增区块」下拉菜单（`el-dropdown` + `el-dropdown-menu`）整体移除，被面板取代。

## 测试

- 后端 pytest：`POST /home-widgets` 带合法 `layout` 创建成功、坐标越界返回 400、不带 `layout` 时保持现有"追加到底部"行为不变（回归）。
- 前端 vitest：拖拽落点像素→网格坐标换算的纯函数单测（如走兜底方案）；`SystemSettings.vue` 的 `handleDropAt`/`handleAddAtBottom` 调用 store action 时传参正确。
- Playwright（遵循 CLAUDE.md 强制要求）：从面板拖一张卡片到网格空白处，确认新区块出现在预期位置（而不是底部）；点击面板卡片，确认仍然追加到底部（回归验证点击路径不受影响）。

## 范围之外（明确不做）

- 不新增区块类型（仍是现有 7 种可创建类型）。
- 不做拖拽创建时的自动避让/碰撞检测（与已上线的 `PUT /home-widgets/layout` 保持一致的"允许重叠、事后手动调开"设计）。
- 不做面板的可折叠/可隐藏（保持常驻，YAGNI）。
- 不改动内置四个区块（欢迎/统计/快捷入口/系统说明）的创建限制——它们本来就不在 `allowed_types` 白名单里，面板不展示它们。
