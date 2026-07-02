# 客服访客页 消息气泡重新设计

**日期**: 2026-07-02
**分支**: feat/kefu-self-service-faq
**范围**: 纯展示层重构，客服访客聊天页（`/kefu/:slug`）的消息气泡渲染。

## 背景与问题

`src/views/kefu/KefuChatPage.vue` 的消息区当前**几乎没有气泡样式**：消息渲染为无对齐、无背景、访客/客服无视觉区分的纯文本块（`.msg` / `.msg.user` / `.msg.assistant` 在 scoped 样式里根本不存在）。用户不满意，要求重新设计。

对比参照：产品内部 AI 聊天 `src/views/ai-chat/AiChatView.vue` 走极简文档风（用户右侧灰块、助手左侧无边框全宽）。但客服页是**对外访客**界面，选用更亲和的「双向气泡」消费级客服组件风。

## 目标

- 访客/客服消息用左右对齐 + 气泡背景清晰区分。
- 客服侧带头像，访客侧无头像（组件惯例）。
- 每条消息显示时间戳。
- 保持现有发送/上传/SSE/失败恢复逻辑**完全不变**，只重构展示层。
- 深浅主题自适应（用 Element Plus CSS 变量）。

## 非目标（YAGNI）

- 不改发送逻辑、附件上传、SSE 流、失败恢复。
- 不加后端字段（头像颜色前端从名字哈希派生；logo 复用既有 `branding.logo`，无则回退首字）。
- 不做已读回执、消息分组按日期分隔等。

## 方案（Approach B：抽出组件）

### 组件结构

新增 `src/components/kefu/KefuMessageBubble.vue`。

Props:
```ts
{
  message: KefuMessage          // { id, role: 'user'|'assistant', content, createdAt }
  agentName: string             // 客服名，用于首字头像与色相；缺省 '在线客服'
  agentLogo?: string            // branding.logo，存在则头像用图片
}
```

`KefuChatPage.vue` 消息循环改为：
```html
<KefuMessageBubble
  v-for="m in messages" :key="m.id"
  :message="m"
  :agent-name="config?.name || '在线客服'"
  :agent-logo="config?.branding?.logo" />
```
同时把 `normalize` / `plainText` / `fileParts` 三个纯函数从 `KefuChatPage.vue` 迁入 `KefuMessageBubble.vue`（这三者当前只服务于消息渲染），并从 `KefuChatPage` 的 `defineExpose` 中移除 `fileParts`（现有测试不引用它，`bubbles`/其余暴露项保持不变）。

### 布局与视觉（全部用 Element Plus CSS 变量，自动适配主题）

**访客消息（role==='user'，右对齐）**
- 主色气泡：背景 `--el-color-primary`，文字白色。
- 无头像。
- 圆角 12px，右下角收窄为 4px（气泡尾巴感）。
- `max-width: 76%`，`word-break: break-word` 自动换行。
- 文字下方渲染附件 chip（见下）。

**客服消息（role==='assistant'，左对齐）**
- 左侧圆形头像（40px），右侧浅色气泡。
- 气泡：背景 `--el-fill-color-light`，常规文字色 `--el-text-color-primary`。
- 圆角 12px，左上角收窄为 4px。
- 内容用现有 `MarkdownView` 渲染（保留 Markdown 能力）。

**头像逻辑**
- 若 `agentLogo` 存在 → `<img>`。
- 否则取 `agentName` 首个字符，配一个由名字哈希取模选出的柔和背景色（固定色板，保证同名同色、稳定）。

**时间戳**
- 气泡下方小号次要色文字，格式 `HH:mm`（由 `createdAt` 解析）。
- 本地乐观消息 `createdAt === null / 空` → 不显示时间。

**附件 chip**
- 沿用现有 `.file-chip` 视觉（`📎 文件名`），在主色气泡上调整为半透明白底以保证可读。

### 其它元素统一新风格（在 `KefuChatPage.vue` 内）

- **欢迎语**：作为客服首条消息样式展示（带头像的浅色气泡）。引导问题保留为可点圆角 pill，视觉与新气泡协调。
- **正在输入**：客服侧左对齐的三点动画气泡，替换当前纯文字「正在输入…」。

## 数据流

无变化。`messages` 仍是 `KefuMessage[]`，`send()` 推乐观消息（`createdAt: null`）→ SSE `onIdle` 触发 `reload()` 用后端历史替换。bubble 组件是纯展示，输入即 `message`，无副作用、无对外依赖（除 `MarkdownView`）。

## 错误处理

展示层无新增错误路径。`content` 经 `normalize()` 容错（非数组转 text part），沿用现有逻辑迁入组件。

## 测试

**新增** `src/components/kefu/__tests__/KefuMessageBubble.test.ts`：
- 访客消息 → 根元素带 `user`/右对齐 class，无头像。
- 客服消息 → 带头像；`agentLogo` 存在时渲染 `<img>`，否则渲染首字。
- `createdAt` 有值 → 显示 `HH:mm`；为 null → 不显示时间戳。
- 含 file part → 渲染 `📎 文件名` chip。
- 纯文本 / 文本+附件混合渲染正确。

**现有** `src/views/kefu/__tests__/KefuChatPage.test.ts`：断言基于 `defineExpose` 暴露的方法/状态，不受模板重构影响，应保持全绿。

## 验证（MANDATORY）

按 CLAUDE.md，UI 改动需 Playwright 实机验证：打开 `/kefu/demo`，走一轮真实对话（发消息、收客服回复、带附件），确认气泡左右对齐、头像、时间戳、附件 chip 正确渲染，截图存 `.playwright-mcp/`。（需先重连 Playwright MCP。）

## 文档同步

更新客服相关用户指南（`docs/user-guide/` 下对应客服页文档），补充新气泡外观说明/截图。
