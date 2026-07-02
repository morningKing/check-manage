# 客服访客页 整页视觉重设计

**日期**: 2026-07-02
**分支**: feat/kefu-self-service-faq
**范围**: `/kefu/:slug` 访客页整页展示层重构（外壳/头部/消息区/输入框/右侧自助区）。**不改任何业务逻辑**（发送/上传/SSE/失败恢复/FAQ 埋点全部保留）。

## 背景与问题

`src/views/kefu/KefuChatPage.vue` 的页面骨架**几乎无样式**：`.kefu-header` / `.kefu-messages` / `.kefu-input` 无背景、无内边距、无限宽、无框架，内容全幅贴边，输入框是裸 `el-input` + 灰按钮。整页读起来像未完成的原型。上一轮只重做了消息气泡；本轮重做**整页**。

## 目标视觉方向（已与用户确认）

- **现代 SaaS 精致卡片风**。
- **柔和品牌蓝/靛强调色**，仅作用于本页（不动全局近黑 `--el-color-primary`）。
- **居中悬浮卡片**布局：浅背景 + 中央大圆角阴影卡片装整个客服界面。
- 顶部栏**白底克制**（强调色只用在头像/名字），发送按钮用**纸飞机图标**，抽出 **`KefuComposer.vue`** 组件。

## 非目标（YAGNI）

- 不改后端、不加字段、不动 SSE/上传/发送/失败恢复逻辑。
- 不做主题切换开关、不做可嵌入 widget（Phase 3）。
- 不新增自助区块类型。

## 设计

### 1. 强调色（作用域限定）

在 `.kefu-page` 根元素上定义局部 CSS 变量，**只影响本页**：

```css
.kefu-page {
  --kefu-accent: #4f6ef2;          /* 柔和靛蓝 */
  --kefu-accent-hover: #3f5fe0;    /* 悬停/按下 */
  --kefu-accent-soft: #eef1fe;     /* 浅蓝底：pill / hover / 聚焦环底 */
  --kefu-accent-contrast: #ffffff; /* 蓝底上的文字 */
}
```

用处：顶部头像圆标底色、发送按钮、访客气泡、引导问题 pill、输入框聚焦描边。全局 `--el-color-primary`（近黑）不变，后台其它页面不受影响。

### 2. 页面外壳（`KefuChatPage.vue`）

```
.kefu-page            → 100vh；浅背景 var(--el-bg-color-page)；flex 居中（align/justify center）
  .kefu-card          → width min(1080px, 94vw)；height min(880px, 92vh)；
                        border-radius 16px；box-shadow 柔和；background var(--el-bg-color)（白）；
                        overflow hidden；display flex（左对话列 + 右自助列）
    .kefu-main        → flex:1；display flex column（header / messages / composer）
    .kefu-column      → 320px；仅 hasBlocks 时渲染；border-left 细线；overflow-y auto
```

**响应式**：
- `≥992px`：如上，居中卡片 + 右列常驻。
- `<992px`：`.kefu-card` 变全屏（`width:100vw;height:100vh;border-radius:0;box-shadow:none`）；`.kefu-column` 隐藏，顶部出现「🗂 自助服务」按钮 → `el-drawer`（沿用现有 `drawer` 逻辑与 `KefuServiceColumn` 复用）。

### 3. 顶部栏（`KefuChatPage.vue` 内，`.kefu-header`）

白底、`padding 14px 20px`、底部 `1px solid var(--el-border-color-lighter)`。左：头像（40px 圆，`--kefu-accent` 底 + 客服名首字白字，或 `branding.logo` 图片）+ 竖排「客服名 / ● 在线」（绿点 `#22c55e` + 「在线」次要色小字）。右：`<992px` 显示「🗂 自助服务」按钮。头像逻辑复用 `KefuMessageBubble` 的同款（首字/哈希色/logo 优先），故抽出共享工具（见 §7）。

### 4. 消息区（`.kefu-messages`）

`flex:1; overflow-y:auto; padding: 20px 24px`；纤细自定义滚动条（`::-webkit-scrollbar` 6px，`--el-border-color` 拇指）。欢迎语=带头像客服气泡（现状保留）。引导问题 pill 重排：浅蓝底 `--kefu-accent-soft`、蓝字 `--kefu-accent`、`border-radius 16px`、hover 加深。

### 5. 组合输入框（新组件 `KefuComposer.vue`）

抽出为受控组件，`KefuChatPage` 传状态、收事件。

**Props**：`{ draft: string; pending: { name: string; path: string }[]; sending: boolean }`
**Emits**：
- `update:draft(value: string)`
- `pickFiles(files: File[])`
- `removePending(index: number)`
- `send()`

**结构与样式**：
- 外层 `.composer`：一体化圆角容器（`border-radius 12px; border 1px solid var(--el-border-color)`；聚焦时 `border-color: var(--kefu-accent)` + 浅蓝外发光）。内含：左附件按钮（`Paperclip` 图标，幽灵圆形）、中自增高 `el-input type=textarea autosize`、右发送按钮（`Promotion` 纸飞机图标，圆形，`--kefu-accent` 底、白图标；`disabled` 态灰化）。
- 待发附件 chip 显示在容器**上方**（`.composer__pending`），沿用现有 chip 视觉。
- Enter 发送逻辑（IME 安全：`isComposing`/`shiftKey` 判断）迁入本组件，触发 `send`。
- 图标：`import { Paperclip, Promotion } from '@element-plus/icons-vue'`，用法与 `AiChatView.vue` 一致（`<el-icon>` / `:icon`）。

`KefuChatPage` 用法：
```html
<KefuComposer
  :draft="draft" :pending="pending" :sending="sending"
  @update:draft="draft = $event"
  @pick-files="onPickFiles"
  @remove-pending="removePending"
  @send="send" />
```
`KefuChatPage` 保留 `draft`/`pending`/`sending`/`send`/`onPickFiles`/`removePending`（`send()` 的空判与失败恢复不变）；删除页面内 `.input-row`/`.attach-btn`/`.pending` 及隐藏 file input 相关模板（迁入 composer）。

### 6. 访客气泡改用强调色（`KefuMessageBubble.vue`）

`.kmb--user .kmb__bubble` 背景由 `var(--el-color-primary, #409eff)`（当前解析为近黑）改为 `var(--kefu-accent, #4f6ef2)`，文字仍白。因变量定义在 `.kefu-page` 根、气泡是其后代，作用域自然继承。客服白卡气泡与头像逻辑不变。`.kmb--user .kmb__file` 半透明白覆盖层在蓝底上同样可读，不改。

### 7. 头像工具复用

当前首字/哈希取色/logo 逻辑内嵌在 `KefuMessageBubble.vue`。顶部栏也需要同款头像。抽出纯函数到 `src/components/kefu/avatar.ts`：`avatarInitial(name?: string): string`、`avatarColor(name?: string): string`（现有哈希+`AVATAR_COLORS` 调色板）。`KefuMessageBubble.vue` 与顶部栏共用，避免重复。`AVATAR_COLORS` 移入该文件（保留「主题无关身份色板」注释）。

### 8. 右侧自助区卡片化（`KefuServiceColumn.vue` + 区块组件）

右列每个区块统一为干净小卡片：白底、`border 1px solid var(--el-border-color-lighter)`、`border-radius 12px`、`padding 14px 16px`、区块间 `gap 12px`；标题小号加粗次要色。仅调整 `KefuServiceColumn.vue`（及必要的 `KefuBlock*.vue`）的容器/间距样式，**不改其数据与交互**（faqClick/escalate/链接行为原样）。抽屉内复用同组件，样式一致。

### 9. 状态样式

- **加载中**：`onMounted` 完成前显示居中卡片骨架（头像+两三条灰条占位）。
- **错误态**（`loadError`）：居中卡片内友好插画位/图标 + 「客服暂不可用，请稍后再试」，不再是裸文字。
- **空状态**：即欢迎语+引导问题，已覆盖。

## 组件边界与文件

| 文件 | 职责 | 变更 |
|---|---|---|
| `src/views/kefu/KefuChatPage.vue` | 外壳/头部/消息区/编排/响应式/状态/accent 作用域 | 重构展示层 |
| `src/components/kefu/KefuComposer.vue` | 输入/附件/发送/待发 chip（受控） | **新增** |
| `src/components/kefu/KefuMessageBubble.vue` | 访客气泡改用 `--kefu-accent`；头像逻辑改从 `avatar.ts` 导入 | 小改 |
| `src/components/kefu/avatar.ts` | `avatarInitial`/`avatarColor` + `AVATAR_COLORS` | **新增** |
| `src/components/kefu/KefuServiceColumn.vue`（+ `KefuBlock*.vue` 必要时） | 右列区块卡片化 | 样式改 |

## 数据流

无变化。`KefuChatPage` 仍持有 `config/sessionId/faq/messages/draft/pending/sending`，SSE `onIdle→reload` 不变。`KefuComposer` 纯受控（props in / emits out，无副作用）。`avatar.ts` 是纯函数。

## 错误处理

展示层无新增错误路径；`loadError` 分支改为样式化卡片。`normalize()` 等容错逻辑保持。

## 测试

- **新增** `src/components/kefu/__tests__/KefuComposer.test.ts`：
  - 输入触发 `update:draft`；Enter（非 IME、非 shift）触发 `send`，IME 组字/Shift+Enter 不触发；
  - 选文件触发 `pickFiles(File[])`；
  - `pending` 渲染 chip，点 ✕ 触发 `removePending(i)`；
  - `sending`/空内容时发送按钮 `disabled`。
- **新增** `src/components/kefu/__tests__/avatar.test.ts`：`avatarInitial`（首字/缺省「客」）、`avatarColor`（同名同色、取自调色板）。
- **现有** `KefuMessageBubble.test.ts`：改从 `avatar.ts` 导入后仍应 5/5（断言不变）。
- **现有** `KefuChatPage.test.ts`：`defineExpose` 暴露项保持（`draft/pending/sending/send/onPickFiles/askBubble/...`）；断言全部基于 `defineExpose` 的方法/状态（`(vm as any).send()` 等），不查 DOM 结构。在该测试的 `stubs` 中**加入 `KefuComposer` 与 `KefuMessageBubble` 的极简 stub**（避免子组件图标/渲染副作用），使 8/8 保持通过、且与展示层重构解耦。

## 验证（MANDATORY）

Playwright 打开 `/kefu/demo`（需 Flask :3002 + MCP :3003 + OpenCode :4096 + vite）：确认居中卡片外壳、白底顶部栏+头像+在线点、蓝色访客气泡、纸飞机发送按钮、组合输入框聚焦描边、引导 pill、右列卡片化、移动端全屏+抽屉；走一轮真实对话 + 附件发送。截图存 `.playwright-mcp/`。桌面(≥992)与移动(<992)各一张。

## 文档同步

更新 `docs/user-guide/ai/smart-customer-service.md` §9.6（整页外观描述 + 新截图替换 `_images/kefu-message-bubbles.png` 或新增整页图）。
