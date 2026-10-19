# 客服设置页（KefuManager）重设计 设计

**日期**: 2026-07-04
**分支**: feat/kefu-self-service-faq
**范围**: 前端。把「设置中心 → 集成对接 → 智能客服」的管理页 `src/views/admin/KefuManager.vue` 从一路裸滚的单页重构为「顶部实例栏 + 子标签」结构。**纯模板/结构重构 + 样式**，`<script>` 逻辑与所有后端接口零改动。

## 背景与问题

`KefuManager.vue` 现状（模板）把三类东西堆在一页、无分区：
- 顶部 `el-page-header content="智能客服 · 热门问题"`（标题误导：页面已不只是热问）。
- 一行里挤了：实例下拉 + 「新建/编辑/删除客服」+「新增热问」（实例操作与 FAQ 操作混在一起）。
- 紧接 FAQ 表格，再 `el-divider` 后是「主页配置」（提示气泡 + 区块编辑器 + 保存按钮）。
- 无卡片/分区/留白，垂直堆叠、需一路滚动，观感混乱。

## 目标

- 结构清晰：顶部**实例栏**（选择/新建/编辑/删除实例）+ 下方**子标签**（热门问题 / 主页配置）分离两类配置。
- 消除误导标题与按钮拥挤。
- **行为零变**：所有现有交互（FAQ 增删改/排序/启用、气泡编辑、区块编辑、保存主页配置、实例增删改）保持不变。

## 非目标（YAGNI）

- 不改任何后端接口、`api/kefu.ts`、`KefuInstanceDialog.vue`、`KefuBlocksEditor.vue`。
- 不动 `<script setup>` 的数据/方法逻辑（`loadInstances`/`loadFaq`/`loadHome`/`onInstanceChange`/FAQ 的 `openCreate`/`openEdit`/`save`/`remove`/`toggle`/`move`、`addBubble`/`removeBubble`/`moveBubble`/`saveHome`、实例的 `openCreateInstance`/`openEditInstance`/`removeInstance`/`onInstanceSaved`）。
- **不拆组件**（保持 `KefuManager.vue` 单文件；两个 tab 的内容内联在模板里）。
- 不加「人工接管」tab（后端已交付，前端属子项目 B；本次仅在结构上预留位置）。

## 设计

### 结构（`<template>` 重排）

```
.kefu-manager
├─ 顶部实例栏 .km-topbar
│   ├─ 「客服实例」label + <el-select>（activeIid, @change=onInstanceChange）
│   └─ <el-button> + 新建客服 / 编辑客服(v-if activeIid) / 删除客服(v-if activeIid)
├─ 空状态（v-if !activeIid）.km-empty
│   └─ 文案：instances.length ? '请选择上方的客服实例开始配置' : '还没有客服实例，点「+ 新建客服」创建一个'
└─ <el-tabs>（v-if activeIid）
    ├─ <el-tab-pane label="热门问题">
    │   ├─ 工具条：<el-button>+ 新增热问（openCreate）
    │   └─ FAQ <el-table>（排序/问题/分类/点击量/启用/操作，交互不变）
    └─ <el-tab-pane label="主页配置">
        ├─ 提示气泡编辑（bubbles：输入 + ↑↓ + 删除 + 添加气泡）
        ├─ <KefuBlocksEditor v-model="blocks" />
        └─ <el-button>保存主页配置（saveHome）

（对话框保持原样：FAQ 编辑 <el-dialog> + <KefuInstanceDialog>）
```

- 去掉 `el-page-header content="智能客服 · 热门问题"`；页面标题由设置中心 hub 的 tab 名「智能客服」承载，KefuManager 顶部直接是实例栏（如需页面小标题，用一行朴素标题「智能客服」，不带「· 热门问题」）。
- 「新增热问」从实例栏移入「热门问题」tab 的工具条。
- 现有的 `home-config-section` / `bubble-row` / `section-title` 结构与 class 复用于「主页配置」tab 内。

### 样式（`<style scoped>`）

- `.km-topbar`：flex、`gap`、`align-items:center`、下方 `margin-bottom`；实例操作按钮成组。
- `.km-empty`：居中、次要色文案、适度留白。
- 标签内容区留白（`padding`）；FAQ 工具条与表格间距。
- 全部用 Element Plus 主题变量（`--el-text-color-*`/`--el-border-color-*`），随主题适配。

### 状态与数据流

无变化。`activeIid` 驱动：空 → 空状态；有值 → `el-tabs` 显示，`onInstanceChange` 已在选择时加载 FAQ + 主页配置。tab 切换是纯前端展示，不触发额外请求（数据在选实例时已加载）。`el-tabs` **不绑 `v-model`（非受控）**，首个 pane「热门问题」即默认激活——因此**无需新增任何 script 状态**，`<script setup>` 保持零改动。

## 错误处理

展示层无新增路径；现有各操作的 `ElMessage` 提示与失败处理不变。

## 组件边界与文件

| 文件 | 职责 | 变更 |
|---|---|---|
| `src/views/admin/KefuManager.vue` | 客服设置页：实例栏 + 子标签 + 编排 | **模板 + 样式重构；script 不动** |
| `src/views/admin/__tests__/KefuManager.faq.test.ts` | 现有 FAQ 逻辑单测 | 在 stubs 补 `el-tabs`/`el-tab-pane`，保持全绿 |

## 测试

- **现有** `KefuManager.faq.test.ts`：断言基于 `vm` 的方法/状态（`move`/`save`/`listInstances`/`form`），不查具体 DOM 结构。在 `stubs` 补极简 `el-tabs`/`el-tab-pane`（渲染 `<div><slot/></div>`），使模板含 `el-tabs` 时仍能挂载、8 用例保持通过。
- **构建**：`npm run build`（vue-tsc）无错误。

## 验证（MANDATORY）

Playwright（admin 登录）：设置中心 → 集成对接 → 智能客服：
1. 断言顶部实例栏（下拉 + 新建/编辑/删除）；未选实例时显示空状态文案。
2. 选中「演示客服」→ 出现 `el-tabs`，默认「热门问题」tab：显示「+ 新增热问」+ FAQ 表格。
3. 切到「主页配置」tab：显示提示气泡编辑 + 区块编辑器 + 保存按钮。
4. 抽查一次操作仍生效（如切 FAQ 启用开关 / 保存主页配置 → 成功提示）。
5. 截图存 `.playwright-mcp/`。

## 文档同步

`docs/user-guide/ai/smart-customer-service.md` §9.2「访问管理页面」附近，更新管理页结构描述（顶部实例栏 + 「热门问题 / 主页配置」子标签）；若有旧的页面截图则替换为新结构截图。
