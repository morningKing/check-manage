# 客服会话 图片/文件渲染 设计

**日期**: 2026-07-06
**分支**: feat/kefu-self-service-faq
**范围**: 后端 + 前端。让客服会话（访客气泡 + 管理端接管控制台）能**渲染**消息里的图片与文件——图片内联显示，其他文件给可点下载链接。加一个访客态文件 serve 端点供两侧共用。

## 背景

访客上传（`POST /kefu/sessions/<sid>/files`）已允许图片（`_ALLOWED_EXT` 含 `.png/.jpg/.jpeg/.gif`）+ pdf/docx/xlsx/txt/md/csv/json，文件存到会话工作区 `uploads/<name>`，消息里存 `{type:'file', name, path}`。但**渲染缺失**：

- 访客气泡（`KefuMessageBubble.vue`）把文件部件一律显示成「📎 文件名」chip，不显示图片、无可点链接。
- 管理端控制台（`KefuTakeoverPanel.vue`）的 `plainText()` 只取 `type==='text'`，**文件部件完全被忽略**——管理员看不到访客发的图/文件。
- 根本缺口：**没有回取文件的 URL**——只有上传端点，无 serve 端点。

## 目标

- 访客会话里，图片消息**内联显示**（缩略、点击看原图）；非图片文件显示为**可点下载链接**。
- 管理端控制台的对话区同样渲染图片/文件（当前完全不显示）。

## 非目标（YAGNI）

- 不改上传端点、不扩展允许类型、不做图片压缩/缩略图生成（直接 serve 原图，前端限宽显示）。
- 不做灯箱/画廊组件（点击图片用浏览器新标签打开原图即可）。
- 不为管理端另建带 JWT 的文件端点（复用访客态端点，见下）。

## 设计

### 1. 文件 serve 端点（`server/routes/kefu_public.py`，新增）

```
GET /kefu/sessions/<sid>/files/<name>?visitor_id=<vid>
```
- `name` 为**单段**路由参数（Flask 默认 `<name>` 不匹配斜杠 → 杜绝 `../` 跨目录）。
- `sess = kefu_repo.load_kefu_session(sid, vid)`；`None` → 404（归属校验，同现有访客接口）。
- `dest = safe_resolve(sess[4], f"uploads/{name}")`（`WorkspacePathError` → 404，防穿越，双保险）。
- 文件不存在 → 404；否则 `send_file(dest)`（inline，Flask 依文件名自动推断 MIME）。
- 访问凭据 = 不可猜的 `sid`（`sess_<hex>`）+ `visitor_id`（UUID），与访客 SSE/history 同一模型；仅暴露 `uploads/` 下文件。

### 2. 访客侧（`src/api/kefuPublic.ts` + `KefuMessageBubble.vue` + `KefuChatPage.vue`）

**`kefuPublic.ts`**：加
```ts
export function kefuFileUrl(sid: string, name: string): string {
  return `/api/kefu/sessions/${encodeURIComponent(sid)}/files/${encodeURIComponent(name)}?visitor_id=${encodeURIComponent(getVisitorId())}`
}
export function isKefuImage(name: string): boolean {
  return /\.(png|jpe?g|gif)$/i.test(name || '')
}
```

**`KefuMessageBubble.vue`**：
- 新增 prop `sessionId: string`。
- `import { kefuFileUrl, isKefuImage } from '@/api/kefuPublic'`；用 `sessionId` 拼访客 URL。（管理端**不复用**此组件——其对话区自渲染，见 §3——故气泡只管访客态，不加 URL-构造器 prop。）
- 文件部件渲染改为：
  - `isKefuImage(f.name)` → `<a :href="kefuFileUrl(sessionId, f.name)" target="_blank"><img class="kmb__img" :src="kefuFileUrl(sessionId, f.name)" :alt="f.name" /></a>`（`.kmb__img` 限宽如 `max-width: 220px`、圆角、`cursor: zoom-in`）。
  - 否则 → `<a class="kmb__file" :href="kefuFileUrl(sessionId, f.name)" target="_blank" download>📎 {{ f.name }}</a>`（现 chip 样式改成链接）。

**`KefuChatPage.vue`**：给两处 `<KefuMessageBubble>`（欢迎区那条是纯文本 welcome，不含文件，可不传；消息列表那处必传）加 `:session-id="sessionId"`。

### 3. 管理端（`KefuTakeoverPanel.vue`）

- 对话渲染不再只 `plainText`，改为遍历 `m.content` 部件：文本部件显示文本；文件部件按 `isKefuImage` 分流为 `<img>` / 下载链接。
- URL 用**该会话的 visitor_id**（`selected.value.visitor_id`，队列数据含）拼同一访客端点：
  ```ts
  function adminFileUrl(name: string) {
    return `/api/kefu/sessions/${selectedSid.value}/files/${encodeURIComponent(name)}?visitor_id=${encodeURIComponent(selected.value?.visitor_id || '')}`
  }
  ```
- `import { isKefuImage } from '@/api/kefuPublic'`（复用，不重复定义）。
- 图片 `<img class="ktp__img">`（限宽），非图片 `<a download>` 链接。保留人工消息「人工」徽标、访客右/客服左布局。

### 4. 图片/文件识别

图片扩展名：`png|jpg|jpeg|gif`（`isKefuImage`，与上传允许的图片类型一致；`.svg` 未在允许列表，无需处理）。其余允许类型（pdf/docx/xlsx/txt/md/csv/json）为下载链接。

## 错误处理

- serve：归属不符 / 穿越 / 文件不存在 → 404（不泄漏路径）。
- 前端图片加载失败（如文件被清理）：`<img>` 自然显示 broken——可选加 `@error` 兜底为文件名文本（实现时加，最小）。
- 管理端 `selected.visitor_id` 缺失（理论不会，队列必带）→ URL 带空 vid → 404，图片 broken，不崩。

## 组件边界与文件

| 文件 | 职责 | 变更 |
|---|---|---|
| `server/routes/kefu_public.py` | 文件 serve 端点 | 改（+1 路由） |
| `src/api/kefuPublic.ts` | `kefuFileUrl` + `isKefuImage` | 改 |
| `src/components/kefu/KefuMessageBubble.vue` | 图片内联 / 文件下载链接 + `sessionId` prop | 改 |
| `src/views/kefu/KefuChatPage.vue` | 传 `:session-id` | 改（+1 属性） |
| `src/components/kefu/KefuTakeoverPanel.vue` | 对话区渲染文件/图片部件 | 改 |

## 测试

**后端**（`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`）— `server/tests/test_kefu_public_chat.py` 增：
- 非归属（`load_kefu_session` → None）→ 404。
- 正常：patch `load_kefu_session` 返回 SESS + 在临时工作区放一个 `uploads/a.png`，GET → 200 且 `Content-Type` 为 `image/png`。（用 `tmp_path` 作 `sess[4]`；或 patch `safe_resolve` + `send_file`——实现时用真实 tmp 工作区更实。）
- 文件不存在 → 404。
- （Flask `<name>` 不匹配斜杠，穿越天然被路由挡；safe_resolve 为双保险，可加一条 `..%2F` 断言非 200。）

**前端**：
- `KefuMessageBubble.test`（新增或并入现有）：给含 `{type:'file',name:'a.png'}` 的 user 消息 + `sessionId` → 渲染 `img.kmb__img`，`src` 含 `/kefu/sessions/<sid>/files/a.png`；含 `{type:'file',name:'b.pdf'}` → 渲染 `a.kmb__file[download]` 而非 img。
- `KefuTakeoverPanel.test`：`messages` 含图片文件部件 + 选中会话（含 visitor_id）→ 渲染 `img.ktp__img`；含 pdf → 下载链接。
- 现有用例保持绿；`npm run build`（vue-tsc）无错误。

## 验证（MANDATORY）

Playwright（前端 :5173 + 后端 :3002 + OpenCode :4096）：
1. 访客 `/kefu/demo` → 用 📎 上传一张 png → 发送 → 断言气泡内**内联显示该图**（`img.kmb__img` 可见、src 指向 files 端点、加载成功即 naturalWidth>0）。
2. 上传一个 pdf → 发送 → 断言显示为**可点下载链接**（非 img）。
3. 管理端控制台接管该会话 → 断言对话区**看到该图片**（`img.ktp__img`）。
4. 截图存 `.playwright-mcp/`。

**并经 proxy.py（:8080）验证**（新 `/api` 路由，按 CLAUDE.md「proxy.py Verification」强制条款）：`npm run build && python proxy.py`，经 `:8080` 访客发图→气泡显示 + `curl -s -o /tmp/x.png -w "%{http_code} %{content_type}" http://localhost:8080/api/kefu/sessions/<sid>/files/<name>?visitor_id=<vid>` 确认图片经生产代理正确返回（200 + image/png，非缓冲损坏）。事后清理测试会话与上传文件。

## 文档同步

`docs/user-guide/ai/smart-customer-service.md`：在访客上传/会话说明处补「图片消息内联显示、其他文件为下载链接；管理端控制台同样可见」。
