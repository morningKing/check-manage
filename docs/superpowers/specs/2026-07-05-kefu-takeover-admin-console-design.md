# 人工接管（Phase 2）子项目 B2：管理端接管控制台 设计

**日期**: 2026-07-05
**分支**: feat/kefu-self-service-faq
**范围**: 前端。给客服设置页加第 3 个 tab「人工接管」——一个左右主从的会话控制台：左侧会话队列，右侧对话 + 接管/释放 + 人工回复。消费子项目 A 已交付的管理端接口。

## 背景

子项目 A（后端）已上线管理端接口：`GET /admin/kefu/sessions`（队列，支持 `instance/needs_human/takeover/status` 过滤，含 `last_message`/`human_agent_id`）、`GET /admin/kefu/sessions/<sid>/messages`（对话，含 `meta`）、`POST .../takeover`、`POST .../release`、`POST .../messages`（人工回复）。B1（访客侧转人工 + 轮询）已上线。目前管理员只能用 curl 操作接管——本子项目补上可视化控制台。

## 目标

- 客服设置页「智能客服」下新增 tab「人工接管」，管理员可：看待人工/接管中会话队列、接管、看对话、人工回复、释放。
- 轮询自动刷新队列与当前对话（约 5s），无需手动。

## 非目标（YAGNI）

- 不改后端、不改 B1、不动「热门问题」「主页配置」两 tab。
- 不做 SSE 实时（沿用轮询）、不做多客服分配/抢占、不做跨实例聚合队列（队列限当前选中实例）。

## 设计

### 结构

- **`KefuManager.vue`**：`<el-tabs>` 增第 3 个 pane：
  ```html
  <el-tab-pane label="人工接管">
    <div class="km-tab-body"><KefuTakeoverPanel :instance-id="activeIid" /></div>
  </el-tab-pane>
  ```
- **新组件 `src/components/kefu/KefuTakeoverPanel.vue`**（props `{ instanceId: string }`）——左右主从：

```
.ktp（display:flex）
├─ 左 .ktp__queue（宽 ~320px，右侧分隔线）
│   ├─ 工具条：过滤 <el-radio-group>（全部 / 待人工 / 接管中）+「🔄 刷新」
│   └─ 会话列表：每项 = 状态徽标 + 访客id(截断) + 最后消息预览 + 接管人(有则显) + 时间；点选高亮；空态「暂无会话」
└─ 右 .ktp__detail（flex:1）
    ├─ 未选会话：占位「从左侧选择一个会话」
    └─ 选中会话：
        ├─ 操作条：接管人 + <el-button>接管(未接管时)/释放(接管中时)
        ├─ 对话区（自适应高度、可滚）：消息列表（访客右/客服左；meta.author==='human' 显「人工」小徽标；时间）
        └─ 回复条：<el-input> + 发送（**仅接管中启用**，否则禁用 + 占位「先接管再回复」）
```

### 状态（`<script setup>`）

- `sessions = ref<any[]>([])`、`filter = ref<'all'|'needs_human'|'takeover'>('all')`
- `selectedSid = ref('')`、`messages = ref<any[]>([])`、`replyDraft = ref('')`
- `selected = computed(() => sessions.value.find(s => s.id === selectedSid.value) || null)`（读 `human_takeover` 决定回复启用/接管按钮）
- `let queueTimer / convTimer`（轮询句柄）

### 行为

- `loadQueue()`：按 `filter` 映射查询参数调 `api.listSessions({ instance: props.instanceId, ...})`：
  - `all` → `{ status: 'active' }`；`needs_human` → `{ needs_human: true }`；`takeover` → `{ takeover: true }`。（均带 `instance`。）
  - 覆盖 `sessions`。
- `selectSession(sid)`：设 `selectedSid`、`loadConversation()`。
- `loadConversation()`：若有 `selectedSid` → `api.getSessionMessages(sid)` 覆盖 `messages`。
- `takeover()` / `release()`：调对应 API → `await loadQueue()` + `await loadConversation()`（刷新状态）。
- `sendReply()`：`replyDraft` 非空且 `selected.human_takeover` 才发；`api.humanReply(sid, content)` → 清 draft + `loadConversation()`。
- `filter` 变化 → `loadQueue()`。
- **轮询**：挂载 `loadQueue()` + 启 `queueTimer = setInterval(() => loadQueue().catch(()=>{}), 5000)`；`convTimer = setInterval(() => { if (selectedSid.value) loadConversation().catch(()=>{}) }, 5000)`。`onBeforeUnmount` 清两个 timer。
- `instanceId` 变化（父切换实例）→ 清空 `selectedSid/messages` + `loadQueue()`（`watch`）。

### API（`src/api/kefu.ts` 新增）

```ts
export function listSessions(params: { instance?: string; needs_human?: boolean; takeover?: boolean; status?: string }) {
  return get<{ sessions: any[] }>('/admin/kefu/sessions', params)
}
export function getSessionMessages(sid: string) { return get<{ messages: any[] }>(`/admin/kefu/sessions/${sid}/messages`) }
export function takeoverSession(sid: string) { return post<{ humanTakeover: boolean }>(`/admin/kefu/sessions/${sid}/takeover`, {}) }
export function releaseSession(sid: string) { return post<{ humanTakeover: boolean }>(`/admin/kefu/sessions/${sid}/release`, {}) }
export function humanReply(sid: string, content: string) { return post<{ messageId: string }>(`/admin/kefu/sessions/${sid}/messages`, { content }) }
```
（已确认 `@/utils/request` 的 `get<T>(url, params?, config?)` 把第二参作为 axios query params 发送；`listSessions` 直接 `get('/admin/kefu/sessions', params)` 即可。布尔以 `true` 传，后端 `_b()` 认 `'1'/'true'/'True'`——axios 序列化为 `true` 字符串，匹配。`all` 过滤只传 `{instance, status:'active'}`，不传 needs_human/takeover → 后端不过滤。）

### 消息渲染

面板内**自渲染**简单消息列表（不复用 `KefuMessageBubble`，避免耦合其访客态样式/`--kefu-accent` 作用域与 config 依赖）：从每条 `content`（parts 数组）提取纯文本；`role==='user'` 右对齐访客气泡、`role==='assistant'` 左对齐客服气泡；`meta?.author==='human'` 在客服气泡上加「人工」徽标；显 `createdAt` 的 `HH:mm`。样式用 Element Plus 主题变量。

## 错误处理

- 队列/对话轮询失败：静默（`.catch(()=>{})`，不中断 interval）。
- 接管/释放/回复失败：`ElMessage.error`（优先后端 `error` 文案）。
- 非接管态点发送：按钮禁用；`sendReply` 内再校验一次（`selected?.human_takeover` 为假直接返回）。

## 组件边界与文件

| 文件 | 职责 | 变更 |
|---|---|---|
| `src/api/kefu.ts` | 加会话/接管/回复接口 | 改 |
| `src/components/kefu/KefuTakeoverPanel.vue` | 接管控制台（队列 + 对话 + 操作） | **新增** |
| `src/views/admin/KefuManager.vue` | `<el-tabs>` 加「人工接管」pane | 改（+几行模板） |
| `src/views/admin/__tests__/KefuManager.faq.test.ts` | stubs 补 `KefuTakeoverPanel` | 改 |

## 测试

- **新增** `src/components/kefu/__tests__/KefuTakeoverPanel.test.ts`（mock `@/api/kefu`）：
  - 挂载 → `listSessions` 被调，带 `instance=<instanceId>`；渲染队列项。
  - 过滤切到「待人工」→ `listSessions` 以 `needs_human:true` 调。
  - `selectSession(sid)` → `getSessionMessages(sid)` 调、`messages` 填充。
  - `takeover()` → `takeoverSession(sid)` 调 + 之后 `listSessions`/`getSessionMessages` 再调（刷新）。
  - `release()` → `releaseSession(sid)` 调。
  - `sendReply()`：`selected.human_takeover=true` + draft 非空 → `humanReply(sid, content)` 调、清 draft；`human_takeover=false` → 不调。
  - 轮询：`vi.useFakeTimers()`，5s 后 `listSessions` 再调；卸载后不再调。
  - （用 `defineExpose` 暴露 `filter/selectSession/takeover/release/sendReply/selectedSid/replyDraft` 等供断言。）
- **现有** `KefuManager.faq.test.ts`：`stubs` 加 `KefuTakeoverPanel: { template: '<div />', props: ['instanceId'] }`，保持 6 用例绿。
- `npm run build`（vue-tsc）无错误。

## 验证（MANDATORY）

Playwright（前端 :5173 + 后端 :3002 + OpenCode :4096，admin 登录）：
1. 先制造一条待人工会话：另一浏览器上下文/curl 让访客 `/kefu/demo` 建会话 + `request-human`（或用 B1：点转人工）。
2. 管理端设置中心 → 集成对接 → 智能客服 → 选「演示客服」→ 切「人工接管」tab：断言左侧队列出现该会话（状态「待人工」）。
3. 点该会话 → 右侧显示对话 + 「接管」按钮 → 点接管 → 断言变「释放」+ 回复框启用。
4. 输入人工回复发送 → 断言对话区出现该消息（带「人工」标记）；（可选）访客页轮询收到。
5. 点「释放」→ 断言回到未接管态。
6. 截图存 `.playwright-mcp/`。事后清理测试会话。

## 文档同步

`docs/user-guide/ai/smart-customer-service.md` 第 10 节「人工接管」补「管理端控制台」小节：设置中心→集成对接→智能客服→「人工接管」tab 的队列/接管/释放/回复用法 + 截图；把该功能从「后续」标注为「已交付」。
