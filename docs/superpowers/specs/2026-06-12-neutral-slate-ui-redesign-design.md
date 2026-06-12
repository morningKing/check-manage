# 中性灰阶 UI 全局重设计 — 设计文档

> 目标：把前端从 Element Plus 原版默认主题（糖果蓝、低密度）改造为**企业级、紧凑、克制**的「中性灰阶」风格（类 Linear / Notion / Vercel 后台），全局生效。
>
> 视觉基准：已用 Playwright 渲染并经用户确认的 mock B（中性灰阶型）。
> - 当前基线：[`assets/2026-06-12-ui-redesign/current-datapage-baseline.png`](./assets/2026-06-12-ui-redesign/current-datapage-baseline.png)
> - 确认方向 mock B：[`assets/2026-06-12-ui-redesign/mock-b-shot.png`](./assets/2026-06-12-ui-redesign/mock-b-shot.png)
> - 备选 mock A：[`assets/2026-06-12-ui-redesign/mock-a-shot.png`](./assets/2026-06-12-ui-redesign/mock-a-shot.png)

## 1. 背景与问题

当前数据页（`DynamicPage.vue`）直接使用 Element Plus 默认主题，存在两类问题：

- **不紧凑**：页面头部由「标题行 + 描述行 + 搜索行 + 工具栏行」四行堆叠；表格行高 40px+、表头粗大；同一视口仅显示约 2 行数据，大量留白。
- **非企业级气质**：高饱和糖果蓝主色、大圆角卡片 + 重阴影、默认控件密度，整体偏消费级而非专业后台。

应用**已内置外观系统**（`stores/app.ts`）：通过 `html` 上的 `.dark` class、`.compact-mode` class 与根 `font-size`（13/14/16px）三个钩子控制主题模式 / 紧凑模式 / 字号，设置入口在 `AppLayout.vue` 的「外观设置」面板。重设计必须**对接并增强**这套系统，而非另起炉灶。

## 2. 目标与非目标

**目标**
- 全局换肤为中性灰阶风格；所有页面（数据页、管理页等）自动继承。
- 提升信息密度：头部精简、行高与控件收紧，同视口可舒适显示约 6 行。
- 状态语义化：优先级用彩色胶囊、类型用小标签，提升扫读效率。
- 暗色模式继续可用（提供灰阶暗色变体）；`.compact-mode` 在新主题上更紧凑；字号设置继续生效。

**非目标（YAGNI）**
- 不更换组件库（Element Plus 深度集成，保留）。
- 不改业务逻辑、路由、数据流、组件 API。
- 不逐页重写样式——通过覆盖 EP CSS 变量让多数页面自动继承。
- 不引入新的设计系统依赖或图标库。

## 3. 技术方案

### 3.1 总体策略：覆盖 Element Plus CSS 变量

Element Plus 的视觉由一组 `--el-*` CSS 自定义属性驱动（颜色、尺寸、圆角、填充等）。新建**单一主题层** `src/assets/styles/theme.scss`，在 `:root` 与 `html.dark` 作用域内覆盖这些变量为中性灰阶令牌，并在 `main.ts` 中**于 element-plus 默认样式之后**引入，确保优先级胜出。多数组件（按钮、表格、分页、标签、表单、对话框…）会自动应用新令牌，无需逐个改写。

引入顺序（`main.ts`）：
```
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import './assets/styles/global.scss'
import './assets/styles/theme.scss'   // 新增：最后引入，覆盖前面的 EP 变量
```

### 3.2 设计令牌（中性灰阶 · 浅色）

| 类别 | 令牌 / 选择器 | 值 | 说明 |
|------|--------------|----|------|
| 主色 | `--el-color-primary` | `#1a1d21`（近黑） | 主按钮、激活态、开关、复选；衍生 light/dark 变体按比例生成 |
| 危险 | `--el-color-danger` | `#dc2626` | 删除等 |
| 成功/警告/信息 | 对应 `--el-color-*` | 收敛饱和度的绿/琥珀/灰 | 语义状态 |
| 文本主 | `--el-text-color-primary` | `#1a1d21` | |
| 文本常规 | `--el-text-color-regular` | `#3a3d44` | |
| 文本次要 | `--el-text-color-secondary` | `#8b8f99` | 表头、占位、计数 |
| 边框 | `--el-border-color` / `-light` / `-lighter` | `#e3e4e8` / `#ebecf0` / `#f2f3f5` | 发丝级描边 |
| 填充/背景 | `--el-fill-color-*`、`--el-bg-color-page` | `#fbfbfc` / `#f7f8fa` | 浅中性 |
| 圆角 | `--el-border-radius-base` | `6px`（小元素），卡片/面板 `8px` | 比现状克制 |
| 控件尺寸 | `--el-component-size`（及 small/large） | 默认 30px、小 26px | 全局收紧 |

**操作链接**（表格内 查看/编辑/复制/删除）：单独把 `el-button.is-link` / 文本按钮的默认色覆盖为中性灰 `#5c606b`，hover→主色；删除项 hover→危险色。保留"可点击"的克制感（避免全黑链接像普通文本）。

**状态展示**（在 `DataTable` 列渲染或字段控件层，按需）：
- 优先级：胶囊徽章 `high`=红底红字、`mid`=琥珀、`low`=绿。
- 类型：小标签 + 色块圆点。
> 注：优先级/类型样式是**通用语义类**（如 `.pill.high`、`.tag`），由列渲染按字段值套用；不写死"巡检用例"业务，其它数据页同样可用。

### 3.3 密度令牌

| 项 | 基础态 | 紧凑模式（`.compact-mode`） |
|----|--------|-----------------------------|
| 表格行高（cell padding） | ~7px 上下（行高 ~34px） | ~5px（行高 ~30px） |
| 表头 | 8px padding、12px 小字、浅灰底 | 6px |
| 工具栏/头部 行高 | 控件 30px | 控件 28px |
| 卡片/面板内边距 | 12–16px | 10–12px |

基础态本身已比现状紧凑；`.compact-mode` 在此之上进一步压缩，作为可选的"超紧凑"档。

### 3.4 应用壳（`AppLayout.vue`）

- **侧栏**：深藏蓝 → 浅中性灰 `#f7f8fa` + 右侧 1px 描边；菜单项激活态为浅底深字（`#eceef5` / `#1a1d21`）、非激活灰字 hover 浅底；分组小标题灰字。高度收紧（菜单项 ~32px）。
- **顶栏**：白底 + 1px 底边；面包屑灰字、当前节点深字；右侧工具按钮（AI 助手/通知/设置/用户）统一为低对比图标按钮，角色徽章改描边式。
- **暗色变体**：侧栏/顶栏在 `html.dark` 下走灰阶暗色（深灰而非纯黑），见 3.6。

### 3.5 数据页（`DynamicPage.vue`）头部精简

- 头部从四行压缩为两行：
  - 第 1 行：标题 + 分支标签 + （右对齐）视图切换段控件 / 视图选择 / 列设置 / 刷新 / 更多 / 新增。
  - 第 2 行：搜索框 + 筛选/排序 chip + （右对齐）记录计数。
  - 页面描述（原单独一行）并入标题行右侧作次要小字（不单独占行、不移除），省一行空间又保留信息。
- 面板：边框 + 8px 圆角替代重阴影；表格无外框、发丝行线。
- 工具栏、分页脚均收紧到新密度令牌。

### 3.6 暗色与紧凑模式兼容

- **暗色**：在 `theme.scss` 的 `html.dark` 作用域提供灰阶暗色变体（背景 `#16181d`/`#1c1f26`、文本浅灰、边框深灰、主色近白或浅靛），覆盖 EP 的 `dark/css-vars.css`，使暗色也呈"中性灰阶"而非默认蓝。**不改动** `stores/app.ts` 的切换逻辑——只换它落地的视觉。
- **紧凑模式**：`html.compact-mode` 作用域内进一步收紧 3.3 的密度令牌。
- **字号**：沿用根 `font-size`（13/14/16px），主题令牌用相对/像素值不与之冲突。

## 4. 改动清单

| 文件 | 改动 |
|------|------|
| `src/assets/styles/theme.scss` | **新增**：EP CSS 变量覆盖（light/dark）、密度令牌、语义类（pill/tag/操作链接）、`.compact-mode` 加强 |
| `src/main.ts` | 在 global.scss 后引入 theme.scss |
| `src/components/layout/AppLayout.vue` | 侧栏/顶栏样式重构（浅色壳 + 暗色变体） |
| `src/views/dynamic/DynamicPage.vue` | 头部两行化、工具栏/表格/分页密度收紧、状态语义类接入 |
| `src/components/common/DataTable.vue`（按需） | 行高/表头密度、操作列链接样式、优先级/类型语义渲染挂钩 |

**自动继承（不单独改）**：其它管理页、对话框、表单等因覆盖 EP 变量自动换肤。

## 5. 风险与回归策略

- **全局覆盖 EP 变量**可能影响个别已有局部样式（重点排查：AI 对话 `ai-chat/*`、仪表盘图表、关系图谱、Excel/看板/日历/甘特视图）。
- **回归策略**：落地后用 Playwright 逐屏截图核对关键页面（数据页表格/看板/Excel、首页区块、AI 助手、管理页若干、暗色 + 紧凑两档），发现冲突即加局部修正（scoped 覆盖），不回退全局主题。
- **测试**：现有前端 Vitest 不针对视觉，保持全绿即可（确保未破坏逻辑）；视觉验收以 Playwright 截图为准。

## 6. 验收标准

- 数据页同视口舒适显示 ≥6 行数据；头部不超过 2 行。
- 整体呈中性灰阶（无糖果蓝），主按钮近黑、状态语义化。
- 浅色 / 暗色 / 紧凑模式三态均正常且风格一致。
- 关键页面 Playwright 截图无明显样式破损。
