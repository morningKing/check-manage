# AI 助手对话框内联渲染图片/SVG 文件 — 设计

- 日期: 2026-05-29
- 状态: 已批准（待实现）
- 范围: 纯前端（新组件 + 工具 + AiChatView 接线）。后端不动。

## 背景

消息气泡里的 `file` part（`{ type:'file'; name; path }`）目前一律渲染成「图标 + 文件名」的 chip（`AiChatView.vue:256-258`）。当文件是图片（尤其 SVG）时，用户看不到内容、只能下载。希望图片/SVG 直接在对话框里内联渲染。

### 关键发现（已验证）

- `file` part 带 `path`（workspace 相对路径，后端 `server/routes/ai_chat.py:188` 写入），下载接口 `GET /sessions/:id/files/download?path=<rel>` 能服务 workspace 内任意文件 → 前端可用 `fileUrl(p.path)`（= `downloadFileUrl(activeId, path)`）拿到图片 URL。**无需后端改动**。
- `src/utils/artifacts.ts` 已有文件类型判断辅助（`extLang`、`isRenderableArtifact`），`isImageFile` 放这里一致。
- **安全**：SVG 经 `<img src>` 加载时浏览器禁用其中脚本 → 无 XSS 风险，无需 DOMPurify。比 `v-html` 注入 SVG 标记（需消毒）更安全。

## 目标 / 非目标

**目标**：消息气泡内的图片类 `file` part（`.svg/.png/.jpg/.jpeg/.gif/.webp`）内联渲染为缩略图，可点开原图、加载失败回退为 chip。

**非目标**：
- 不改输入框下方「待发送附件」chip（YAGNI）。
- 不改后端 / 下载接口。
- 不把 SVG 标记直接注入 DOM（坚持 `<img>`，避免消毒复杂度）。
- 不做图片编辑/缩放控件、不做画廊（YAGNI）。

## 设计

### 1. 工具 `isImageFile`

`src/utils/artifacts.ts` 新增并导出：

```ts
const IMAGE_EXTS = new Set(['svg', 'png', 'jpg', 'jpeg', 'gif', 'webp'])
/** True if a filename looks like an inline-renderable image (by extension). */
export function isImageFile(name: string): boolean {
  const ext = (name.split('.').pop() || '').trim().toLowerCase()
  return IMAGE_EXTS.has(ext)
}
```

### 2. 组件 `ChatFile.vue`

`src/components/ai-chat/ChatFile.vue`（新）——渲染单个 `file` part：

- props：`name: string`、`src: string`（下载 URL）。
- 本地状态 `failed = ref(false)`（`<img>` 加载失败时置 true）。
- `const isImage = computed(() => isImageFile(props.name))`。
- 模板：
  - `isImage && !failed` → `<a :href="src" target="_blank" rel="noopener"><img :src="src" :alt="name" @error="failed = true" /></a>` + 文件名小字 caption。img 样式 `max-width:100%; max-height:360px; border-radius:6px; border:1px solid var(--el-border-color)`。
  - 否则 → 现有 chip：`<div class="file-chip"><ElIcon><Document/></ElIcon><span>{{ name }}</span></div>`（从 AiChatView 平移过来，样式 `.file-chip` 复用现有；组件内自带 scoped 版本以自包含）。
- scoped SCSS：图片包裹 + caption + chip。

### 3. AiChatView 接线

`src/views/ai-chat/AiChatView.vue`：
- import：`import ChatFile from '@/components/ai-chat/ChatFile.vue'`。
- 把第 256-258 行：
  ```html
  <div v-if="p.type === 'file'" class="file-chip">
    <ElIcon><Document /></ElIcon><span>{{ p.name }}</span>
  </div>
  ```
  换成（保持 `v-if` 在 part-type 链首位）：
  ```html
  <ChatFile v-if="p.type === 'file'" :name="p.name" :src="fileUrl(p.path)" />
  ```

## 错误处理

| 情况 | 行为 |
|------|------|
| 非图片扩展名 | 渲染 chip（同现状） |
| 图片但加载失败（路径失效/坏文件） | `@error` → 回退 chip |
| `path` 缺失（理论上不会） | `fileUrl` 产出的 URL 无效 → `<img>` error → 回退 chip |

## 数据流

1. 消息含 `file` part（用户上传或 agent 产出，`path` 指向 workspace 内文件）。
2. AiChatView 渲染该 part → `<ChatFile :src="fileUrl(p.path)">`。
3. `ChatFile` 判断扩展名：图片 → `<img>` 内联（点开看原图）；否则 chip。
4. `<img>` 直接走下载接口取文件；SVG 脚本被浏览器禁用。

## 测试

**前端（Vitest，stub Element Plus）**
- `src/utils/__tests__/artifacts.image.test.ts`（或并入既有 artifacts 测试）：`isImageFile` 对 `a.svg/a.PNG/a.jpeg/a.webp` → true；`a.txt/a.py/a`（无扩展名）→ false。
- `src/components/ai-chat/__tests__/ChatFile.test.ts`：
  - 图片名 → 渲染 `<img>`，`src` 等于传入值，外层有 `<a href>`。
  - 非图片名 → 不渲染 `<img>`，渲染 chip（含文件名文本）。
  - 触发 `<img>` 的 `error` 事件后 → `<img>` 消失，回退 chip。

**真机**：在 AI 助手让 agent 产出（或上传）一个 SVG/PNG → 消息气泡内直接显示图，点击在新标签打开原图；坏路径回退为 chip。

## 影响文件清单

- 改：`src/utils/artifacts.ts`（新增 `isImageFile`）
- 新增：`src/components/ai-chat/ChatFile.vue`
- 改：`src/views/ai-chat/AiChatView.vue`（import + 替换 file part 渲染）
- 测试：`src/utils/__tests__/...`（isImageFile）、`src/components/ai-chat/__tests__/ChatFile.test.ts`
