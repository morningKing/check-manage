# 人工接管（Phase 2）子项目 B1：访客转人工 + 轮询 设计

**日期**: 2026-07-04
**分支**: feat/kefu-self-service-faq
**范围**: 前端。给访客页 `src/views/kefu/KefuChatPage.vue` 加「转人工」入口，接管期间靠**轮询**接收人工回复。消费子项目 A 已交付的后端接口。管理端接管控制台属子项目 B2；SSE 实时合并属后续。

## 背景

子项目 A（后端）已上线：访客 `POST /kefu/sessions/<sid>/request-human` 置 `needs_human`；接管中访客 `POST .../messages` 存消息但不派 AI、响应带 `humanTakeover:true`；`GET .../messages` 取历史。访客页目前没有转人工入口，也不会在接管态轮询——人工回复到不了访客。本子项目补上访客侧。

## 目标

- 访客可在对话页**发起转人工**。
- 接管期间访客能**收到人工回复**（用轮询，不依赖 SSE——接管态 OpenCode 空转、SSE 不出事件）。
- 接管态有清晰的视觉提示（「人工客服接入中」+ 一条系统提示）。
- 访客侧发送在接管态**不卡「正在输入」**（不派 AI，故立即结束发送态）。

## 非目标（YAGNI）

- 不改后端、不拆 SSE（接管期它自然空转）。
- 访客侧**不做**「转回 AI / 结束人工」入口（释放由管理员决定）。
- 不做 SSE 实时合并（属后续）。
- 管理端接管控制台属 **B2**。

## 设计

### 1. API（`src/api/kefuPublic.ts`）

- 新增 `requestHuman(sid: string)` → `POST /kefu/sessions/<sid>/request-human`（带 `X-Visitor-Id`）。
- `sendKefuMessage` 返回类型补 `humanTakeover?: boolean`（后端已返回）：`Promise<{ messageId: string; humanTakeover?: boolean }>`。
- `getKefuHistory` 已存在，轮询复用。

### 2. `KefuChatPage.vue`

**新增状态**：
- `humanMode = ref(false)`：是否处于人工接管态（客户端视角）。
- `pollTimer`（`number | null`，模块级 `let` 或 ref）：轮询句柄。

**「转人工」按钮**（header 右侧，与「🗂 自助服务」并列）：
- `<el-button size="small" v-if="!humanMode" @click="requestHuman">🙋 转人工</el-button>`。
- 进入 `humanMode` 后隐藏（`v-if="!humanMode"`）。

**`requestHuman()`**：
```
await api.requestHuman(sessionId)   // 失败 ElMessage.error
enterHumanMode()
```

**`enterHumanMode()`**（幂等）：
- 若已 `humanMode` 则返回。
- `humanMode.value = true`；`sending.value = false`（清掉可能悬着的 AI 输入态）。
- `startPolling()`。

> ⚠️ 提示文案用**独立提示条**（`v-if="humanMode"` 的 DOM 元素，见下），**不**塞进 `messages` 数组——因为 `reload()` 轮询会用后端历史整体覆盖 `messages`，本地消息会被冲掉。

**人工态提示条**（消息区顶部，独立元素，不属于 `messages`）：
- `<div v-if="humanMode" class="kefu-human-banner">已为你转接人工客服，请稍候…</div>`，置于 `.kefu-messages` 内、消息列表之前（或紧贴 header 下方）。轮询覆盖 `messages` 不影响它。

**进入 humanMode 的两条路径**：
1. 点「转人工」→ `requestHuman` → `enterHumanMode`。
2. `send()` 收到响应 `humanTakeover:true`（管理员已先接管）→ `enterHumanMode`。

**`send()` 调整**（最小改动，保留失败恢复逻辑）：
- 捕获 `const resp = await api.sendKefuMessage(...)`。
- 成功后：`if (resp?.humanTakeover || humanMode.value) { sending.value = false; enterHumanMode() }`——接管态立即结束发送态，不等 SSE `onIdle`；否则维持现状（等 SSE）。

**轮询**：
- `startPolling()`：若无 timer，`setInterval(() => reload(), 3000)`。
- `stopPolling()`：`clearInterval` 并置空。
- `onBeforeUnmount` 调 `stopPolling()`（与现有 `closeStream?.()` 并列）。
- `reload()`（现成）拉取历史并覆盖 `messages`，人工回复即显示为客服气泡。

**header 状态提示**：
- `<span class="kefu-header__status">` 内容随 `humanMode` 切换：`humanMode ? '人工客服接入中' : '在线'`（圆点颜色可保持绿色，或人工态用主色，实现时定；文案为准）。

### 3. 数据流

`requestHuman` → 后端置 needs_human → 管理员（B2 或 curl）接管 + 人工回复 → 访客 3s 轮询 `getKefuHistory` 覆盖 `messages` → 人工回复气泡出现。发送在接管态：`sendKefuMessage` 存消息（后端不派 AI）→ 前端立即清 sending、靠轮询取回复。

## 错误处理

- `requestHuman` 失败：`ElMessage.error('转人工失败，请稍后重试')`，不进入 humanMode。
- 轮询 `reload()` 失败：静默（沿用现有 reload 的容错；不中断 interval）。
- 现有 `send()` 失败恢复（草稿/附件/乐观气泡）逻辑保持不变。

## 组件边界与文件

| 文件 | 职责 | 变更 |
|---|---|---|
| `src/api/kefuPublic.ts` | 加 `requestHuman`；`sendKefuMessage` 返回类型补 `humanTakeover` | 改 |
| `src/views/kefu/KefuChatPage.vue` | 转人工按钮 + humanMode + 轮询 + header 提示 + send 调整 | 改 |

## 测试

`src/views/kefu/__tests__/KefuChatPage.test.ts` 增补（mock `@/api/kefuPublic` 已有）：
- `requestHuman()` 调 `api.requestHuman(sessionId)`，置 `humanMode=true`（据此隐藏按钮 + 渲染提示条），且启动轮询（用 `vi.useFakeTimers()` 断言 3s 后 `getKefuHistory` 被再次调用）。
- `send()` 当 `sendKefuMessage` 解析为 `{humanTakeover:true}` → `sending` 变 false 且 `humanMode=true`（不依赖 SSE onIdle）。
- `send()` 正常（无 humanTakeover）→ 行为不变（沿用现有断言，仍走 SSE 清 sending 路径）。
- `onBeforeUnmount` 停轮询（卸载后推进定时器，`getKefuHistory` 不再增调）。
- 现有用例（bootstrap / escalate / askBubble / 上传 / 发送 / 失败恢复）保持全绿。
- `npm run build`（vue-tsc）无错误。

## 验证（MANDATORY）

Playwright（前端 :5173 + 后端 :3002 + OpenCode :4096）：
1. 访客 `/kefu/demo` → 点 header「🙋 转人工」→ 断言出现系统提示「已为你转接人工客服…」+ header 变「人工客服接入中」+ 按钮消失。
2. 用 curl/API（admin JWT）对该会话 `takeover` + 人工 `messages` 回复。
3. 等待 ≤3s 轮询 → 断言访客页出现该人工回复气泡。
4. 访客在接管态发一条 → 断言不卡「正在输入」、消息入库（历史含之）。
5. 截图存 `.playwright-mcp/`。事后清理该测试会话。

## 文档同步

`docs/user-guide/ai/smart-customer-service.md` 第 10 节「人工接管」补访客侧说明：对话页「转人工」按钮 → 进入人工接入态 → 轮询接收人工回复；标注管理端控制台属 B2。
