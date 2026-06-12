# 中性灰阶 UI 全局重设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把前端从 Element Plus 默认糖果蓝主题改造为企业级「中性灰阶」风格（类 Linear/Notion），全局生效，提升数据页信息密度。

**Architecture:** 单一主题层 `theme.scss` 覆盖 Element Plus 的 `--el-*` CSS 变量（浅色 + 暗色变体 + 密度令牌 + 语义类），在 element-plus 默认样式之后引入；应用壳（AppLayout/SideMenu）改浅色；数据页（DynamicPage）头部两行化与密度收紧。对接现有 `.dark`/`.compact-mode`/根字号外观钩子，不换组件库、不改业务逻辑。

**Tech Stack:** Vue 3 + Element Plus + SCSS + Vite。视觉验收用 Playwright 截图；逻辑回归用现有 Vitest。

设计依据：[`docs/superpowers/specs/2026-06-12-neutral-slate-ui-redesign-design.md`](../specs/2026-06-12-neutral-slate-ui-redesign-design.md)

---

## 说明：本计划的"测试"方式

UI 主题是视觉工作，无单元测试可断言"好看"。每个任务的验收 = **Playwright 截图核对** + **现有 Vitest 全绿（确保未破坏逻辑）**。前置条件：前端 `npm run dev`（:5173）、后端在线；用 admin/admin123 登录。

每任务通用收尾（除非另述）：
```bash
npx vitest run src/ 2>&1 | tail -4    # 期望：全绿（仅确认逻辑未破坏）
npx vue-tsc --noEmit 2>&1 | grep "error TS" | head    # 期望：无输出
```

---

## File Structure

| 文件 | 责任 |
|------|------|
| `src/assets/styles/theme.scss`（新增） | 主题核心：EP 变量覆盖（浅/暗）、密度令牌、语义类（pill/tag/操作链接）、紧凑模式加强 |
| `src/main.ts`（改） | 在 global.scss 后引入 theme.scss |
| `src/components/layout/SideMenu.vue`（改） | 侧栏菜单浅色化 |
| `src/components/layout/AppLayout.vue`（改） | 侧栏容器/顶栏浅色化、暗色变体 |
| `src/views/dynamic/DynamicPage.vue`（改） | 头部两行化、工具栏/表格/分页密度 |

---

## Task 1: 主题层 theme.scss（浅色令牌）+ 引入

**Files:**
- Create: `src/assets/styles/theme.scss`
- Modify: `src/main.ts`（在 `import './assets/styles/global.scss'` 之后加一行）

- [ ] **Step 1: 创建 `src/assets/styles/theme.scss`（浅色部分）**

```scss
/**
 * 中性灰阶主题层 —— 覆盖 Element Plus 的 --el-* CSS 变量。
 * 必须在 element-plus 默认样式之后引入（见 main.ts）。
 * 设计依据：docs/superpowers/specs/2026-06-12-neutral-slate-ui-redesign-design.md
 */

:root {
  /* —— 主色：近黑（Vercel/Linear 风），按比例生成 light/dark 衍生 —— */
  --el-color-primary: #1a1d21;
  --el-color-primary-light-3: #45484e;
  --el-color-primary-light-5: #6b6e74;
  --el-color-primary-light-7: #a9abb0;
  --el-color-primary-light-8: #c7c8cc;
  --el-color-primary-light-9: #eceef0;
  --el-color-primary-dark-2: #000000;

  /* —— 语义色（收敛饱和度） —— */
  --el-color-danger: #dc2626;
  --el-color-danger-light-9: #fef2f2;
  --el-color-success: #16a34a;
  --el-color-success-light-9: #f0fdf4;
  --el-color-warning: #d97706;
  --el-color-warning-light-9: #fffbeb;
  --el-color-info: #8b8f99;

  /* —— 文本 —— */
  --el-text-color-primary: #1a1d21;
  --el-text-color-regular: #3a3d44;
  --el-text-color-secondary: #8b8f99;
  --el-text-color-placeholder: #b4b7bf;

  /* —— 边框（发丝级） —— */
  --el-border-color: #e3e4e8;
  --el-border-color-light: #ebecf0;
  --el-border-color-lighter: #f2f3f5;
  --el-border-color-extra-light: #f7f8fa;

  /* —— 填充 / 背景 —— */
  --el-bg-color: #ffffff;
  --el-bg-color-page: #fbfbfc;
  --el-bg-color-overlay: #ffffff;
  --el-fill-color: #f4f5f7;
  --el-fill-color-light: #f7f8fa;
  --el-fill-color-lighter: #fafbfc;
  --el-fill-color-blank: #ffffff;

  /* —— 圆角（更克制） —— */
  --el-border-radius-base: 6px;
  --el-border-radius-small: 5px;

  /* —— 控件密度（全局收紧） —— */
  --el-component-size: 30px;
  --el-component-size-small: 26px;
  --el-component-size-large: 36px;

  /* —— 自定义令牌（本主题内部用） —— */
  --app-shell-bg: #f7f8fa;          /* 侧栏底 */
  --app-shell-border: #ebecf0;
  --app-shell-active-bg: #eceef5;   /* 菜单激活底 */
  --app-table-row-py: 7px;          /* 表格单元上下内边距 */
  --app-control-h: 30px;
}

/* —— 表格：无外框、发丝行线、表头浅灰小字 —— */
.el-table {
  --el-table-border-color: var(--el-border-color-lighter);
  --el-table-header-bg-color: #f7f9fc;
  --el-table-header-text-color: var(--el-text-color-secondary);
  --el-table-row-hover-bg-color: #fafbfc;
  font-size: 13px;
}
.el-table th.el-table__cell {
  padding: 8px 0;
  font-weight: 550;
  font-size: 12px;
}
.el-table td.el-table__cell {
  padding: var(--app-table-row-py) 0;
}
.el-table--border, .el-table--group { border-radius: 0; }

/* —— 操作列文本/链接按钮：中性灰，hover 主色；删除 hover 危险色 —— */
.el-table .el-button.is-link,
.el-table .el-button--text {
  color: var(--el-text-color-secondary);
  font-weight: 400;
}
.el-table .el-button.is-link:hover,
.el-table .el-button--text:hover { color: var(--el-text-color-primary); }
.el-table .el-button.is-link.is-danger:hover { color: var(--el-color-danger); }

/* —— 卡片：边框替代重阴影 —— */
.el-card { box-shadow: none; border: 1px solid var(--el-border-color-light); border-radius: 8px; }

/* —— 语义类：优先级胶囊 / 类型标签（列渲染按值套用） —— */
.app-pill {
  display: inline-block; font-size: 11.5px; line-height: 18px;
  padding: 1px 9px; border-radius: 11px; font-weight: 500;
}
.app-pill--high { background: var(--el-color-danger-light-9); color: var(--el-color-danger); }
.app-pill--mid  { background: var(--el-color-warning-light-9); color: var(--el-color-warning); }
.app-pill--low  { background: var(--el-color-success-light-9); color: var(--el-color-success); }
```

- [ ] **Step 2: 在 `src/main.ts` 引入主题层（在 global.scss 之后）**

找到（约 28 行）：
```ts
import './assets/styles/global.scss'
```
改为：
```ts
import './assets/styles/global.scss'
import './assets/styles/theme.scss'   // 中性灰阶主题，覆盖 EP 变量（须在 EP css 之后）
```

- [ ] **Step 3: 类型检查 + 逻辑回归**

Run:
```bash
npx vue-tsc --noEmit 2>&1 | grep "error TS" | head
npx vitest run src/ 2>&1 | tail -4
```
Expected: 无 TS error；Vitest 全绿。

- [ ] **Step 4: Playwright 截图核对**

登录后访问 `http://localhost:5173/inspection/case`，截图 `t1-theme.png`。
Expected: 主按钮变近黑、表格表头浅灰小字、整体去糖果蓝（侧栏此时仍是深色，下个任务处理）。

- [ ] **Step 5: Commit**

```bash
git add src/assets/styles/theme.scss src/main.ts
git commit -m "feat(theme): 新增中性灰阶主题层，覆盖 Element Plus 浅色变量与表格密度"
```

---

## Task 2: 应用壳浅色化（SideMenu + AppLayout）

**Files:**
- Modify: `src/components/layout/SideMenu.vue`（el-menu 配色属性 + scoped 样式）
- Modify: `src/components/layout/AppLayout.vue:409`（`.app-aside` 背景）

- [ ] **Step 1: SideMenu —— el-menu 配色属性改浅色**

在 `src/components/layout/SideMenu.vue` 找到 el-menu（约 33-35 行）：
```html
        background-color="#304156"
        text-color="#bfcbd9"
        active-text-color="#409eff"
```
改为：
```html
        background-color="#f7f8fa"
        text-color="#5c606b"
        active-text-color="#1a1d21"
```

- [ ] **Step 2: SideMenu —— scoped 样式改浅色**

在 `src/components/layout/SideMenu.vue` 的 `<style scoped>` 中，替换硬编码深色为浅色（保持选择器不变，仅改色值）：
```scss
.menu-logo {
  height: 52px;
  background-color: #f7f8fa;
  border-bottom: 1px solid #ebecf0;

  .logo-icon { color: #1a1d21; }
  .logo-text { color: #1a1d21; }
}

:deep(.el-menu-item),
:deep(.el-sub-menu__title) {
  height: 40px;
  line-height: 40px;
}
:deep(.el-menu-item):hover,
:deep(.el-sub-menu__title):hover {
  background-color: #f0f1f4 !important;
  color: #1a1d21 !important;
}
:deep(.el-menu-item.is-active) {
  background-color: #eceef5 !important;
  color: #1a1d21 !important;
  font-weight: 500;
  &::before { content: ''; }
}
:deep(.el-sub-menu .el-menu-item) {
  background-color: #f7f8fa !important;
  &:hover { background-color: #f0f1f4 !important; }
  &.is-active { background-color: #eceef5 !important; color: #1a1d21 !important; }
}
```
> 注：若原文件有 `#304156`/`#263445`/`#409eff`/`#1f2d3d`/`#001528` 等其它深色引用，一并按上述浅色体系替换（菜单底 `#f7f8fa`、hover `#f0f1f4`、激活 `#eceef5`、主文字 `#1a1d21`、次文字 `#5c606b`、描边 `#ebecf0`）。右侧 1px 描边由 AppLayout 的 `.app-aside` 提供。

- [ ] **Step 3: AppLayout —— `.app-aside` 背景与描边**

在 `src/components/layout/AppLayout.vue` 找到（约 408-412 行）：
```scss
.app-aside {
  background-color: #304156;
  transition: width 0.3s ease;
  overflow: hidden;
}
```
改为：
```scss
.app-aside {
  background-color: #f7f8fa;
  border-right: 1px solid #ebecf0;
  transition: width 0.3s ease;
  overflow: hidden;
}
```

- [ ] **Step 4: AppLayout —— 顶栏阴影改描边**

找到 `.app-header`（约 420-428 行）中的：
```scss
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
```
删除该行（仅保留已有的 `border-bottom: 1px solid var(--el-border-color-lighter);`）。

- [ ] **Step 5: 回归 + 截图**

Run:
```bash
npx vitest run src/ 2>&1 | tail -4
```
Playwright 访问 `/inspection/case` 截图 `t2-shell.png`。
Expected: 侧栏变浅中性灰、激活项浅底深字、顶栏无投影仅描边；Vitest 全绿。

- [ ] **Step 6: Commit**

```bash
git add src/components/layout/SideMenu.vue src/components/layout/AppLayout.vue
git commit -m "feat(shell): 侧栏与顶栏改中性浅色，去深藏蓝与投影"
```

---

## Task 3: 数据页头部两行化 + 工具栏/表格密度

**Files:**
- Modify: `src/views/dynamic/DynamicPage.vue`（`.page-header` 模板结构与 scoped 样式）

- [ ] **Step 1: 把页面描述并入标题行（去掉单独描述行）**

在 `src/views/dynamic/DynamicPage.vue` 找到标题行内的页面描述块（约 50-52 行）：
```html
        <!-- 页面描述（单独一行） -->
        <span v-if="pageConfig?.description" class="page-description">
          {{ pageConfig.description }}
        </span>
```
把它从 `.page-title` 末尾**移动到 `.title-row` 内、紧跟 `<h2>` 之后**（与标题、分支标签同行），并改用次要小字类：
```html
        <h2>{{ pageConfig?.name || '数据页面' }}</h2>
        <span v-if="pageConfig?.description" class="page-subtitle">{{ pageConfig.description }}</span>
```
确保原 `.page-title` 下不再单独渲染 `.page-description`。

- [ ] **Step 2: DynamicPage —— scoped 样式：头部两行 + 子标题**

在 `src/views/dynamic/DynamicPage.vue` 的 `<style scoped>` 中新增/调整：
```scss
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}
.title-row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 30px;
}
.title-row h2 {
  font-size: 17px;
  font-weight: 650;
  color: var(--el-text-color-primary);
  letter-spacing: -0.01em;
}
.page-subtitle {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.page-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
```

- [ ] **Step 3: DynamicPage —— 工具栏与表格卡片密度收紧**

在 `<style scoped>` 中收紧工具栏/卡片间距（按文件现有类名调整；若类名不同则套用到对应容器）：
```scss
.toolbar-row, .search-row { gap: 10px; margin-bottom: 10px; }
.el-card :deep(.el-card__body) { padding: 0; }   /* 表格卡片去内边距，由表格自身控制密度 */
```
> 若 DynamicPage 未用 `.toolbar-row`/`.search-row`，把上述间距套到搜索框+计数所在的那个容器上（保持 ~10px 间距、控件高 30px）。

- [ ] **Step 4: 回归 + 截图**

Run:
```bash
npx vitest run src/views/dynamic/__tests__/DynamicPage.test.ts 2>&1 | tail -4
```
Playwright 访问 `/inspection/case` 截图 `t3-datapage.png`。
Expected: 头部 ≤2 行、描述并入标题行小字、同视口可舒适显示约 6 行；DynamicPage 测试全绿。

- [ ] **Step 5: Commit**

```bash
git add src/views/dynamic/DynamicPage.vue
git commit -m "feat(datapage): 头部两行化、描述并入标题、工具栏与表格密度收紧"
```

---

## Task 4: 暗色灰阶变体

**Files:**
- Modify: `src/assets/styles/theme.scss`（追加 `html.dark` 作用域）

- [ ] **Step 1: theme.scss 追加暗色覆盖**

在 `src/assets/styles/theme.scss` 末尾追加：
```scss
/* ==================== 暗色：中性灰阶变体（覆盖 EP dark/css-vars） ==================== */
html.dark {
  --el-color-primary: #e6e8eb;        /* 暗色下主色近白 */
  --el-color-primary-light-3: #b9bcc1;
  --el-color-primary-light-5: #8b8f96;
  --el-color-primary-light-9: #2a2e35;
  --el-color-primary-dark-2: #ffffff;

  --el-text-color-primary: #e6e8eb;
  --el-text-color-regular: #c2c6cd;
  --el-text-color-secondary: #8b8f99;

  --el-bg-color: #1c1f26;
  --el-bg-color-page: #16181d;
  --el-bg-color-overlay: #21242c;
  --el-border-color: #2e323b;
  --el-border-color-light: #282c34;
  --el-border-color-lighter: #23262e;
  --el-fill-color: #23262e;
  --el-fill-color-light: #1f222a;
  --el-fill-color-lighter: #1c1f26;

  --app-shell-bg: #1a1d23;
  --app-shell-border: #282c34;
  --app-shell-active-bg: #2a2e36;

  .el-table {
    --el-table-header-bg-color: #1f222a;
    --el-table-row-hover-bg-color: #21242c;
    --el-table-border-color: #23262e;
  }
}
```

- [ ] **Step 2: AppLayout/SideMenu 暗色壳对齐**

在 `src/components/layout/AppLayout.vue` 的暗色块（约 519-523 行 `:global(html.dark) { .app-aside { ... } }`）改为：
```scss
:global(html.dark) {
  .app-aside { background-color: #1a1d23; border-right-color: #282c34; }
  .app-header { background-color: #1c1f26; }
}
```
在 `src/components/layout/SideMenu.vue` 末尾 `<style scoped>` 追加暗色覆盖：
```scss
:global(html.dark) {
  .menu-logo { background-color: #1a1d23; border-bottom-color: #282c34;
    .logo-icon, .logo-text { color: #e6e8eb; } }
  :deep(.el-menu) { background-color: #1a1d23 !important; }
  :deep(.el-menu-item), :deep(.el-sub-menu__title) { color: #c2c6cd !important; }
  :deep(.el-menu-item):hover, :deep(.el-sub-menu__title):hover { background-color: #23262e !important; color: #fff !important; }
  :deep(.el-menu-item.is-active) { background-color: #2a2e36 !important; color: #fff !important; }
  :deep(.el-sub-menu .el-menu-item) { background-color: #1a1d23 !important; }
}
```

- [ ] **Step 3: 回归 + 截图**

通过顶栏「外观设置 → 主题模式 → 深色」切换（或 Playwright `page.evaluate(() => document.documentElement.classList.add('dark'))`）。截图 `t4-dark.png`。
Expected: 暗色呈中性深灰（非默认蓝），文本/边框对比适中，侧栏壳一致。

- [ ] **Step 4: Commit**

```bash
git add src/assets/styles/theme.scss src/components/layout/AppLayout.vue src/components/layout/SideMenu.vue
git commit -m "feat(theme): 暗色中性灰阶变体，覆盖 EP dark 变量与应用壳"
```

---

## Task 5: 紧凑模式加强

**Files:**
- Modify: `src/assets/styles/theme.scss`（追加 `html.compact-mode` 作用域）

- [ ] **Step 1: theme.scss 追加紧凑模式密度**

在 `src/assets/styles/theme.scss` 末尾追加：
```scss
/* ==================== 紧凑模式：在基础态之上进一步收紧 ==================== */
html.compact-mode {
  --el-component-size: 28px;
  --el-component-size-small: 24px;
  --app-table-row-py: 5px;

  .el-table th.el-table__cell { padding: 6px 0; }
  .el-table td.el-table__cell { padding: 5px 0; }
  .el-card :deep(.el-card__body) { padding: 0; }
  .page-header { margin-bottom: 8px; }
}
```

- [ ] **Step 2: 回归 + 截图**

通过「外观设置 → 紧凑模式」开启（或 Playwright `classList.add('compact-mode')`）。截图 `t5-compact.png`。
Expected: 行高更小（~30px），同视口显示更多行，风格不变。

- [ ] **Step 3: Commit**

```bash
git add src/assets/styles/theme.scss
git commit -m "feat(theme): 紧凑模式在中性灰阶基础上进一步收紧密度"
```

---

## Task 6: 跨屏回归与冲突修正

**Files:**
- 视发现而定（局部 scoped 修正）：可能涉及 `src/components/ai-chat/*`、仪表盘、关系图谱、Excel/看板/日历/甘特视图等

- [ ] **Step 1: 逐屏 Playwright 截图核对**

依次登录并截图核对以下关键页面（浅色 + 暗色各一遍），记录任何明显样式破损（对比度过低、控件错位、深色残留）：
- 数据页 表格视图 `/inspection/case`
- 数据页 Excel 视图、看板视图（切换视图按钮）
- 首页 `/home`（含快速录入区块、统计卡片）
- AI 助手 `/ai-chat`（消息区、制品卡、输入框）
- 管理页若干：`/admin/page-config`、`/admin/menu-export`、`/admin/system-settings`
- 数据导出 `/admin/menu-export`

- [ ] **Step 2: 对每处冲突加局部 scoped 修正**

原则：**不回退全局主题**；在受影响组件的 `<style scoped>` 内用 `var(--el-*)` 令牌或最小覆盖修正（例如某图表硬编码了旧蓝色，则改用 `var(--el-color-primary)` 或该模块自有色）。逐个修复并各自 commit：
```bash
git add <被修组件>
git commit -m "fix(theme): <页面> 适配中性灰阶主题（局部修正）"
```

- [ ] **Step 3: 最终验收**

Run:
```bash
npx vitest run src/ 2>&1 | tail -4
npx vue-tsc --noEmit 2>&1 | grep "error TS" | head
```
Expected: Vitest 全绿、无 TS error。对照验收标准（spec §6）逐项确认：数据页 ≥6 行、头部 ≤2 行、整体中性灰阶、三态正常、关键页无破损。

- [ ] **Step 4: 清理临时截图**

```bash
rm -f t1-theme.png t2-shell.png t3-datapage.png t4-dark.png t5-compact.png
```
（Playwright 截图默认落 `.playwright-mcp/`，已被 .gitignore 忽略；如有落到根目录的临时 PNG 一并删除。）

---

## Self-Review

**Spec 覆盖核对**（spec 各节 → 任务）：
- §3.1 覆盖 EP 变量 + main.ts 引入顺序 → Task 1 ✅
- §3.2 设计令牌（浅色） → Task 1 theme.scss ✅
- §3.3 密度令牌（基础/紧凑） → Task 1（基础）+ Task 5（紧凑）✅
- §3.4 应用壳浅色化 → Task 2（浅色）+ Task 4（暗色壳）✅
- §3.5 数据页头部两行化 → Task 3 ✅
- §3.6 暗色/紧凑/字号兼容 → Task 4 + Task 5；字号沿用根 font-size，未与令牌冲突 ✅
- §4 改动清单 → 各任务文件一致 ✅
- §5 风险与回归 → Task 6 跨屏回归 ✅
- §6 验收标准 → Task 6 Step 3 ✅

**Placeholder 扫描**：无 TBD/TODO；Task 2 Step 2 与 Task 3 Step 3 含"若类名不同则…"的条件指引，因 SideMenu/DynamicPage 既有类名可能与示例略异——已给出明确色值/间距体系兜底，非占位。

**类型/命名一致**：自定义令牌 `--app-shell-bg`/`--app-shell-active-bg`/`--app-table-row-py`/`--app-control-h` 与语义类 `.app-pill--high/mid/low` 在浅/暗/紧凑各 Task 中拼写一致 ✅。颜色体系（侧栏 `#f7f8fa`、hover `#f0f1f4`、激活 `#eceef5`、主文字 `#1a1d21`、次文字 `#5c606b`、描边 `#ebecf0`）跨 Task 2/4 一致 ✅。
