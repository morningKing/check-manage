# 客服会话 图片/文件渲染 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 客服会话（访客气泡 + 管理端控制台）渲染消息里的图片（内联）与文件（下载链接），通过一个访客态文件 serve 端点取图。

**Architecture:** 新增 `GET /kefu/sessions/<sid>/files/<name>?visitor_id=`（归属校验 + safe_resolve）；访客 `KefuMessageBubble` 与管理端 `KefuTakeoverPanel` 各自按扩展名把文件部件渲染成 `<img>` 或下载 `<a>`，共用同一端点。

**Tech Stack:** Python Flask `send_file`；Vue 3；Vitest / Pytest。

## Global Constraints

- 端点：`GET /kefu/sessions/<sid>/files/<name>?visitor_id=`；`name` 为**单段**路由参数（不匹配斜杠）；`load_kefu_session(sid, vid)` 归属校验（失败 404）；`safe_resolve(sess[4], "uploads/"+name)`（穿越/不存在 → 404）；`send_file(dest)`（inline，MIME 自动）。
- 图片扩展名判定：`png|jpg|jpeg|gif`（`isKefuImage`）。其余允许类型为下载链接。
- 访问凭据 = 不可猜的 `sid`+`visitor_id`（同访客 SSE 模型）。管理端复用**该会话的 `visitor_id`**（队列数据含）拼同一端点——**不**另建带 JWT 的 admin 端点。
- 该 GET 是普通（有限）响应，`proxy.py._is_sse()` 对它返回 False = 正确（缓冲转发）；**无需**改 proxy.py。但按 CLAUDE.md「proxy.py Verification」，新 `/api` 路由**必须**经 `:8080` 验证。
- 后端测试：`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest <file> -v`。前端：repo 根 `npx vitest run <file>`。构建 `npm run build`。提交只 `git add` 该任务确切文件。

---

### Task 1: 文件 serve 端点

**Files:**
- Modify: `server/routes/kefu_public.py`（flask import 加 `send_file`；新增 GET 路由）
- Modify: `server/tests/test_kefu_public_chat.py`（3 用例）

**Interfaces:**
- Produces: `GET /kefu/sessions/<sid>/files/<name>?visitor_id=` → 200 文件 / 404。

- [ ] **Step 1: 写失败测试**

在 `server/tests/test_kefu_public_chat.py` 追加（`SESS`/`INST`/`patch` 已在文件内；用 pytest 内置 `tmp_path`）：

```python
def test_get_file_404_not_owner(client):
    with patch('routes.kefu_public.kefu_repo.load_kefu_session', return_value=None):
        resp = client.get('/kefu/sessions/sess_1/files/a.png?visitor_id=v1')
    assert resp.status_code == 404


def test_get_file_serves_image(client, tmp_path):
    up = tmp_path / 'uploads'
    up.mkdir()
    (up / 'a.png').write_bytes(b'\x89PNG\r\n\x1a\n' + b'0' * 64)
    sess = ('sess_1', 'kefu-bot', 'oc_1', 'active', str(tmp_path), 'kf_1', False)
    with patch('routes.kefu_public.kefu_repo.load_kefu_session', return_value=sess):
        resp = client.get('/kefu/sessions/sess_1/files/a.png?visitor_id=v1')
    assert resp.status_code == 200
    assert resp.headers['Content-Type'].startswith('image/png')


def test_get_file_404_when_missing(client, tmp_path):
    sess = ('sess_1', 'kefu-bot', 'oc_1', 'active', str(tmp_path), 'kf_1', False)
    with patch('routes.kefu_public.kefu_repo.load_kefu_session', return_value=sess):
        resp = client.get('/kefu/sessions/sess_1/files/nope.png?visitor_id=v1')
    assert resp.status_code == 404
```

- [ ] **Step 2: 运行确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_kefu_public_chat.py -v -k get_file`
Expected: FAIL（路由不存在 → 404 的路由缺失表现为 Flask 404，但 `test_get_file_serves_image` 会拿到 404 而非 200 → FAIL）。

- [ ] **Step 3: 实现端点**

`server/routes/kefu_public.py`：

(a) 顶部 flask import 加 `send_file`——把
```python
from flask import Blueprint, request, jsonify, Response, stream_with_context
```
改为
```python
from flask import Blueprint, request, jsonify, Response, stream_with_context, send_file
```
（若现有 import 行内容略有出入，只需确保 `send_file` 在其中。）

(b) 在 `upload()` 端点（`POST .../files`）之后加：
```python
@kefu_public_bp.route('/sessions/<sid>/files/<name>', methods=['GET'])
def get_file(sid, name):
    vid = (request.args.get('visitor_id') or '').strip()
    sess = kefu_repo.load_kefu_session(sid, vid)
    if not sess:
        return jsonify({'error': 'session not found'}), 404
    try:
        dest = safe_resolve(sess[4], f"uploads/{name}")
    except WorkspacePathError:
        return jsonify({'error': 'not found'}), 404
    if not os.path.isfile(dest):
        return jsonify({'error': 'not found'}), 404
    return send_file(dest)
```
（`safe_resolve`/`WorkspacePathError`/`os` 已在文件内 import。）

- [ ] **Step 4: 运行测试通过 + 回归**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_kefu_public_chat.py -v -k get_file`
Expected: PASS（3 用例）。

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/ -q -k kefu`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add server/routes/kefu_public.py server/tests/test_kefu_public_chat.py
git commit -m "feat(kefu): 访客态文件 serve 端点 GET /kefu/sessions/<sid>/files/<name>"
```

---

### Task 2: 访客气泡 图片/文件渲染

**Files:**
- Modify: `src/api/kefuPublic.ts`（`kefuFileUrl` + `isKefuImage`）
- Modify: `src/components/kefu/KefuMessageBubble.vue`（sessionId prop + 渲染）
- Modify: `src/views/kefu/KefuChatPage.vue`（传 `:session-id`）
- Modify: `src/components/kefu/__tests__/KefuMessageBubble.test.ts`（2 用例）

**Interfaces:**
- Consumes: Task 1 端点。
- Produces: `kefuFileUrl(sid, name): string`、`isKefuImage(name): boolean`；`KefuMessageBubble` 新 prop `sessionId?: string`。

- [ ] **Step 1: 写失败测试**

在 `src/components/kefu/__tests__/KefuMessageBubble.test.ts` 追加（沿用其既有 mount 方式；下方 `mountBubble` 按文件现有写法微调）：

```typescript
  it('renders an image file part as an inline <img> pointing at the files endpoint', () => {
    const w = mount(KefuMessageBubble, { props: {
      sessionId: 'sess_1',
      message: { id: 'm1', role: 'user', content: [{ type: 'file', name: 'a.png', path: 'uploads/a.png' }], createdAt: null },
    } })
    const img = w.find('img.kmb__img')
    expect(img.exists()).toBe(true)
    expect(img.attributes('src')).toContain('/kefu/sessions/sess_1/files/a.png')
    expect(w.find('a.kmb__file').exists()).toBe(false)
  })

  it('renders a non-image file part as a download link, not an <img>', () => {
    const w = mount(KefuMessageBubble, { props: {
      sessionId: 'sess_1',
      message: { id: 'm2', role: 'user', content: [{ type: 'file', name: 'b.pdf', path: 'uploads/b.pdf' }], createdAt: null },
    } })
    expect(w.find('img.kmb__img').exists()).toBe(false)
    const a = w.find('a.kmb__file')
    expect(a.exists()).toBe(true)
    expect(a.attributes('href')).toContain('/kefu/sessions/sess_1/files/b.pdf')
    expect(a.attributes('download')).toBeDefined()
  })
```

（若测试文件顶部没有 `import { mount } from '@vue/test-utils'` 与 `import KefuMessageBubble from '../KefuMessageBubble.vue'`，它本就有——沿用。`getVisitorId` 走真实实现，localStorage 由 test setup 提供。）

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/components/kefu/__tests__/KefuMessageBubble.test.ts`
Expected: FAIL（当前渲染 chip、无 `img.kmb__img`）。

- [ ] **Step 3: `kefuPublic.ts` 加 helper**

在 `src/api/kefuPublic.ts`（`getVisitorId` 之后任意处）追加：
```typescript
export function kefuFileUrl(sid: string, name: string): string {
  return `/api/kefu/sessions/${encodeURIComponent(sid)}/files/${encodeURIComponent(name)}?visitor_id=${encodeURIComponent(getVisitorId())}`
}
export function isKefuImage(name: string): boolean {
  return /\.(png|jpe?g|gif)$/i.test(name || '')
}
```

- [ ] **Step 4: `KefuMessageBubble.vue` 渲染**

(a) 模板——把
```html
        <span v-for="(f, i) in files" :key="i" class="kmb__file">📎 {{ f.name }}</span>
```
替换为
```html
        <template v-for="(f, i) in files" :key="i">
          <a v-if="isKefuImage(f.name)" :href="kefuFileUrl(sessionId, f.name)" target="_blank" rel="noopener">
            <img class="kmb__img" :src="kefuFileUrl(sessionId, f.name)" :alt="f.name" />
          </a>
          <a v-else class="kmb__file" :href="kefuFileUrl(sessionId, f.name)" target="_blank" rel="noopener" download>📎 {{ f.name }}</a>
        </template>
```

(b) 脚本——import 段加：
```typescript
import { kefuFileUrl, isKefuImage } from '@/api/kefuPublic'
```
`defineProps` 加 `sessionId`（可选、默认空串，避免破坏无 sessionId 的既有挂载）——把
```typescript
const props = defineProps<{ message: KefuMessage; agentName?: string; agentLogo?: string }>()
```
改为
```typescript
const props = withDefaults(defineProps<{ message: KefuMessage; agentName?: string; agentLogo?: string; sessionId?: string }>(), { sessionId: '' })
```

(c) 样式——`<style scoped>` 内，把 `.kmb__file { ... }` 起始声明补上链接复位并加图片样式。在 `.kmb__file` 规则里追加 `text-decoration: none; cursor: pointer;`，并新增：
```css
.kmb__img { max-width: 220px; max-height: 220px; border-radius: 8px; display: block; margin-top: 4px; cursor: zoom-in; }
```

- [ ] **Step 5: `KefuChatPage.vue` 传 sessionId**

给消息列表的 `<KefuMessageBubble>`（约第 43 行起，`v-for="m in messages"` 那个）加属性 `:session-id="sessionId"`。欢迎区那个（第 34 行，纯 welcome 文本）也加 `:session-id="sessionId"` 以保持一致（无 files，不影响）。

- [ ] **Step 6: 运行测试通过 + 构建**

Run: `npx vitest run src/components/kefu/__tests__/KefuMessageBubble.test.ts`
Expected: PASS（新 2 + 既有全绿）。

Run: `npm run build`
Expected: vue-tsc 无错误。

- [ ] **Step 7: Commit**

```bash
git add src/api/kefuPublic.ts src/components/kefu/KefuMessageBubble.vue src/views/kefu/KefuChatPage.vue src/components/kefu/__tests__/KefuMessageBubble.test.ts
git commit -m "feat(kefu): 访客气泡内联渲染图片 + 文件下载链接"
```

---

### Task 3: 管理端控制台 图片/文件渲染

**Files:**
- Modify: `src/components/kefu/KefuTakeoverPanel.vue`（对话区渲染文件部件）
- Modify: `src/components/kefu/__tests__/KefuTakeoverPanel.test.ts`（1 用例）

**Interfaces:**
- Consumes: Task 1 端点、`isKefuImage`（`@/api/kefuPublic`）。

- [ ] **Step 1: 写失败测试**

在 `src/components/kefu/__tests__/KefuTakeoverPanel.test.ts` 追加（沿用其 `mountP`/`mockMsgs`/`flushPromises`）：

```typescript
  it('renders an image file part in the conversation as <img>', async () => {
    mockMsgs.mockResolvedValue({ messages: [
      { id: 'm1', role: 'user', content: [{ type: 'file', name: 'a.png', path: 'uploads/a.png' }], meta: null, createdAt: null },
    ] })
    const w = mountP(); await flushPromises()
    await (w.vm as any).selectSession('sess_1')
    const img = w.find('img.ktp__img')
    expect(img.exists()).toBe(true)
    expect(img.attributes('src')).toContain('/kefu/sessions/sess_1/files/a.png')
    expect(img.attributes('src')).toContain('visitor_id=v-abcdef12')
  })
```

（`SAMPLE` 会话 `visitor_id: 'v-abcdef12'`，`selectSession('sess_1')` 后 `selected` 命中它。）

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/components/kefu/__tests__/KefuTakeoverPanel.test.ts`
Expected: FAIL（当前只 `plainText`，无 img）。

- [ ] **Step 3: `KefuTakeoverPanel.vue` 渲染**

(a) 脚本——import 段加：
```typescript
import { isKefuImage } from '@/api/kefuPublic'
```
在 `plainText` 函数附近加：
```typescript
function fileParts(content: any) {
  return (Array.isArray(content) ? content : []).filter((p: any) => p && p.type === 'file')
}
function adminFileUrl(name: string) {
  const vid = selected.value?.visitor_id || ''
  return `/api/kefu/sessions/${selectedSid.value}/files/${encodeURIComponent(name)}?visitor_id=${encodeURIComponent(vid)}`
}
```

(b) 模板——把对话气泡
```html
            <div class="ktp__bubble">
              <span v-if="m.meta && m.meta.author === 'human'" class="ktp__human-tag">人工</span>
              {{ plainText(m.content) }}
            </div>
```
替换为
```html
            <div class="ktp__bubble">
              <span v-if="m.meta && m.meta.author === 'human'" class="ktp__human-tag">人工</span>
              <span v-if="plainText(m.content)">{{ plainText(m.content) }}</span>
              <template v-for="(f, i) in fileParts(m.content)" :key="i">
                <a v-if="isKefuImage(f.name)" :href="adminFileUrl(f.name)" target="_blank" rel="noopener">
                  <img class="ktp__img" :src="adminFileUrl(f.name)" :alt="f.name" />
                </a>
                <a v-else class="ktp__filelink" :href="adminFileUrl(f.name)" target="_blank" rel="noopener" download>📎 {{ f.name }}</a>
              </template>
            </div>
```

(c) 样式——`<style scoped>` 内追加：
```css
.ktp__img { max-width: 200px; max-height: 200px; border-radius: 8px; display: block; margin-top: 4px; cursor: zoom-in; }
.ktp__filelink { display: inline-block; margin-top: 4px; font-size: 12px; text-decoration: none; color: var(--el-color-primary, #4f6ef2); }
```

- [ ] **Step 4: 运行测试通过 + 构建**

Run: `npx vitest run src/components/kefu/__tests__/KefuTakeoverPanel.test.ts`
Expected: PASS（新 1 + 既有全绿）。

Run: `npm run build`
Expected: vue-tsc 无错误。

- [ ] **Step 5: Commit**

```bash
git add src/components/kefu/KefuTakeoverPanel.vue src/components/kefu/__tests__/KefuTakeoverPanel.test.ts
git commit -m "feat(kefu): 管理端控制台对话区渲染图片/文件"
```

---

### Task 4: 文档 + Playwright + proxy.py 验证

**Files:**
- Modify: `docs/user-guide/ai/smart-customer-service.md`
- Evidence: `.playwright-mcp/` 截图

- [ ] **Step 1: 备一张测试图 + 确认服务**

准备一张小 png 供上传（scratchpad）：
```bash
python -c "import base64,os; d=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQBHDFHfAAAAAElFTkSuQmCC'); p=os.path.expandvars(r'%TEMP%\\kefu-test.png'); open(p,'wb').write(d); print(p)"
```
确认前端 `:5173`、后端 `:3002`、OpenCode `:4096`（后端本轮改了代码 → 若未重启则 `python app.py` 重启）。

- [ ] **Step 2: Playwright 走渲染闭环**

1. 访客 `/kefu/demo` → 点 📎（`browser_file_upload` 选上一步的 png）→ 发送 → 断言气泡内出现 `img.kmb__img`，且 `naturalWidth > 0`（图片真的加载成功，不是 broken）。
2. 再上传一个非图片（如任意 `.txt`/`.pdf`）→ 发送 → 断言出现 `a.kmb__file[download]`、无对应 img。
3. 管理端登录 → 智能客服 → 演示客服 → 人工接管 → 选该会话 → 断言对话区出现 `img.ktp__img`（`naturalWidth > 0`）。
4. 截图存 `.playwright-mcp/kefu-image-render.png`。

- [ ] **Step 3: proxy.py 生产验证（MANDATORY，新 /api 路由）**

`npm run build && (cd server && python proxy.py)`；经 `:8080`：
- 用 curl 建会话 + 上传一张 png（`POST :8080/api/kefu/i/demo/sessions` 拿 sid；`POST :8080/api/kefu/sessions/<sid>/files` 传 png，X-Visitor-Id）→ 拿 `name`。
- `curl -s -o /dev/null -w "%{http_code} %{content_type}\n" "http://localhost:8080/api/kefu/sessions/<sid>/files/<name>?visitor_id=<vid>"` → 断言 `200 image/png`（经生产反向代理正确返回，非缓冲损坏/404）。
- 事后停 proxy 栈（:8080/:3001/:3003 按 PID kill）、清理测试会话与其工作区上传文件。

- [ ] **Step 4: 更新用户指南 + Commit**

在 `docs/user-guide/ai/smart-customer-service.md` 访客上传/会话说明处补：「图片消息在会话中**内联显示**（点击看原图），其他文件显示为**下载链接**；管理端接管控制台同样可见访客发送的图片/文件。」

```bash
git add docs/user-guide/ai/smart-customer-service.md
git commit -m "docs(kefu): 会话图片内联/文件下载链接说明"
```

---

## Self-Review

**Spec coverage:**
- 文件 serve 端点（归属/穿越/不存在 404、send_file inline）→ Task 1 ✓
- `kefuFileUrl`/`isKefuImage` + 访客气泡图片内联/文件下载 + `sessionId` 传递 → Task 2 ✓
- 管理端控制台渲染文件部件（复用会话 visitor_id）→ Task 3 ✓
- 文档 + Playwright + **proxy.py 验证** → Task 4 ✓

**Placeholder scan:** 无 TBD/TODO；每步含完整代码；测试含具体断言。✓

**Type consistency:**
- `kefuFileUrl(sid,name)`/`isKefuImage(name)`（Task 2 定义）与气泡（Task 2）、控制台（Task 3 import isKefuImage + 自拼 adminFileUrl）用法一致 ✓
- `KefuMessageBubble` prop `sessionId?`（Task 2）与 `KefuChatPage` `:session-id`（Task 2 Step 5）一致 ✓
- 端点路径 `/kefu/sessions/<sid>/files/<name>`（Task 1）与前端 `kefuFileUrl`/`adminFileUrl`、测试断言一致 ✓
- CSS 类 `.kmb__img`/`.kmb__file`（Task 2）、`.ktp__img`/`.ktp__filelink`（Task 3）与各自模板一致 ✓
