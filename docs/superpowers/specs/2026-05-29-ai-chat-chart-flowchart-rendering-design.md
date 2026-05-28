# AI Chat 图表与流程图渲染 — 设计

- 日期: 2026-05-29
- 状态: 已批准（待实现）
- 范围: 前端 AI chat 渲染 + 后端 agent 提示

## 背景与问题

AI chat 助手回复目前的渲染管线（`src/views/ai-chat/AiChatView.vue`）：
助手文本 → `splitArtifacts()`（`src/utils/artifacts.ts`）拆成「文本段」与「代码段」。
文本段交给 `MarkdownView`（md-editor-v3 `MdPreview`）渲染；代码段（≥6 行或 ≥240 字符）
被「抬升」为可下载/预览的源码卡片 `ArtifactCard`（预览在 `ArtifactPreview` 抽屉里，仅
`html`/`svg` 走 iframe 实时渲染）。

### 构造数据验证结论（已在 :8080 真机确认）

- **ECharts 图表**：md-editor-v3 原生支持 ` ```echarts ` 代码块，内联时能渲染成 `<canvas>`
  （`.md-editor-echarts`）。已验证小的柱状图、饼图正常出图。
- **Mermaid 流程图**：内联时 md-editor 生成 `.md-editor-mermaid` 容器，但**只显示原始文本、
  不出 SVG**（无控制台报错；`window.mermaid` 已加载）。即默认的 CDN 懒加载在本环境未真正渲染。
- **共同问题**：两类块只要 ≥6 行/≥240 字符就被 `splitArtifacts` 抬升为源码卡片，
  根本到不了 md-editor 渲染——真实世界的流程图/图表几乎都超过该阈值，于是显示为源码卡片。

结论：**部分支持**（小的内联 echarts 可渲染；mermaid 不出图；两者较大时都不渲染）。

## 目标 / 非目标

**目标**
- 助手回复中的 `mermaid` 流程图与 `echarts` 图表在对话框内**内联渲染成图**，不受块大小限制。
- 离线可用（不依赖 CDN 懒加载）。
- 弱模型（MiMo）被要求画图/流程图时能产出可渲染的围栏格式。

**非目标**
- 不为图表/流程图提供版本历史、单独「运行」或「下载为文件」入口（普通代码仍走 `ArtifactCard`）。
- 不新增除 mermaid 外的图形库；echarts 复用项目已有依赖。
- 不改动非 AI-chat 场景的 markdown 渲染逻辑（仅做全局 md-editor 扩展注册，行为向后兼容）。

## 设计

### 1. 制品抽取改造 — `src/utils/artifacts.ts`

新增「内联渲染语言」集合 `INLINE_RENDER_LANGS = { mermaid, echarts }`（小写比较）。
在 `splitArtifacts()` 的围栏遍历中，当围栏信息串语言属于该集合时，**跳过抬升**（与
小片段同样 `continue`），从而把该块留在所属文本段里，交给 `MarkdownView` 渲染。
其余语言（代码、svg、html、markdown 等）的 artifact 行为完全不变。

判定基于围栏原始语言标记（`m[1]`，trim 后小写），不走 `sniffLang`（mermaid/echarts 均为
显式标注，无需嗅探）。

### 2. md-editor 扩展配置 — 新增 `src/components/ai-chat/md-editor-setup.ts`

模块顶层调用一次 md-editor-v3 的 `config({ editorExtensions: { ... } })`：
- `mermaid.instance`：注册**打包进来的 mermaid 实例**；mermaid `securityLevel: 'strict'`
  （防止恶意 mermaid 通过点击/HTML 注入）。
- `echarts.instance`：注册项目的 echarts 实例（按需 `echarts.use([...])` 注册所需图表/组件，
  至少 Bar/Line/Pie + 常用坐标轴/提示/图例/Canvas 渲染器），保证离线可用、不走 CDN。

由 `MarkdownView.vue` `import` 该模块以确保在渲染前执行一次（全局生效）。
新增 `mermaid` 到 `dependencies`。

> 备注：md-editor-v3 注册外部实例的确切 API 形态（键名/实例形状）在实现时以所装版本
> （`md-editor-v3@^6.5`）为准核对；echarts 需提供已注册所需图表类型的实例。

### 3. 后端 agent 提示 — `server/routes/ai_chat.py`

在 `_AGENT_DIRECTIVE` 追加一句简短规则（保持 terse、延续「不要复述本规则」的风格）：
> 画流程图用 ` ```mermaid ` 代码块；画数据图表用 ` ```echarts ` 代码块（块内为 ECharts 的
> JSON option）。

让端到端真正可用，尤其针对易跑偏的 MiMo。

## 数据流（端到端）

1. 用户在对话框请求「画个流程图/图表」。
2. 后端 `send_message` 给 OpenCode 的 prompt 前置 `_AGENT_DIRECTIVE`（含新规则）。
3. 助手回复包含 ` ```mermaid ` / ` ```echarts ` 围栏块，经 SSE 流式回传、持久化。
4. 前端 `AiChatView` 对助手文本调用 `splitArtifacts()`：mermaid/echarts 块**保留在文本段**。
5. `MarkdownView`（md-editor-v3，已注册 mermaid/echarts 实例）把这些块**渲染成图**。

## 测试

- **前端单测**（`src/utils/__tests__/artifacts.test.ts` 或新增）：
  - mermaid 块（含 ≥6 行）→ `splitArtifacts` 结果中保留为 `text` 段，不产生 `code` 段。
  - echarts 块（含 ≥6 行）→ 同上。
  - 普通代码块（≥6 行）→ 仍抬升为 `code` 段（回归保护）。
- **真机验证**（Playwright，复用本次种子会话流程）：重新 `npm run build` 后，
  - 小/大 mermaid 块都渲染出 SVG（`.md-editor-mermaid svg` 存在）。
  - 小/大 echarts 块都渲染出 `<canvas>`（`.md-editor-echarts`）。
- 后端：现有 `ai_chat` 测试不回归；`_AGENT_DIRECTIVE` 文案变更不破坏既有断言。

## 风险与缓解

- **md-editor 外部实例 API 形态不确定**：实现时按已装版本核对；若注册接口与预期不符，
  在该模块内适配（最坏情况：退化为自建 `DiagramBlock` 渲染 mermaid，echarts 仍走 md-editor）。
- **echarts 实例图表类型注册不全**：先注册常用类型；后续按需补充。
- **mermaid 安全**：`securityLevel: 'strict'`，不放开 HTML/点击。
- **bundle 体积**：新增 mermaid（较大）；可接受，换取离线可靠渲染。

## 影响文件清单

- `src/utils/artifacts.ts`（改）
- `src/components/ai-chat/md-editor-setup.ts`（新增）+ `MarkdownView.vue` 引入（改）
- `package.json`（新增 mermaid 依赖）
- `server/routes/ai_chat.py`（`_AGENT_DIRECTIVE` 文案）
- 测试：`src/utils/__tests__/artifacts.test.ts`（改/增）
