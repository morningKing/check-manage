# AI Chat 图表与流程图渲染 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 AI chat 助手回复中的 `mermaid` 流程图和 `echarts` 数据图表在对话框内内联渲染成图（任意大小、离线可用）。

**Architecture:** 前端 `splitArtifacts()` 不再把 `mermaid`/`echarts` 围栏块抬升为源码卡片，保留内联交给 md-editor-v3 渲染；通过 `config()` 给 md-editor 注册打包进来的 mermaid 与 echarts 实例（echarts 用 `JSON.parse` 安全解析，mermaid 用 strict 安全级别）；后端 `_AGENT_DIRECTIVE` 增加一句让弱模型产出这两类围栏。

**Tech Stack:** Vue 3 + TypeScript, md-editor-v3@6.5（`config` / `editorExtensions`）, mermaid（新增依赖）, echarts@6（项目已有）, Vitest, Flask（后端提示）, Playwright（真机验证）。

设计依据：`docs/superpowers/specs/2026-05-29-ai-chat-chart-flowchart-rendering-design.md`

---

### Task 1: `splitArtifacts` 排除 mermaid/echarts（保留内联）

**Files:**
- Modify: `src/utils/artifacts.ts`
- Test: `src/utils/__tests__/artifacts.test.ts`

- [ ] **Step 1: 写失败测试**（追加到 `artifacts.test.ts`）

在文件顶部 import 增加 `isInlineRenderLang`：

```typescript
import { splitArtifacts, artifactFilename, isMarkdownLang, sniffLang, isRenderableLang, isRunnableLang, isInlineRenderLang } from '../artifacts'
```

在 `describe('splitArtifacts', ...)` 内追加：

```typescript
  it('keeps a large mermaid block inline (not lifted to an artifact)', () => {
    const diagram = ['graph TD', 'A-->B', 'B-->C', 'C-->D', 'D-->E', 'E-->F', 'F-->G'].join('\n')
    const src = `流程：\n\n\`\`\`mermaid\n${diagram}\n\`\`\`\n\n完。`
    const segs = splitArtifacts(src)
    expect(segs.every(s => s.type === 'text')).toBe(true)
    expect(segs.map(s => (s as any).text).join('')).toContain('graph TD')
  })

  it('keeps a large echarts block inline (not lifted to an artifact)', () => {
    const opt = ['{', '"xAxis": {"type":"category","data":["A","B"]},', '"yAxis": {"type":"value"},', '"series": [{"type":"bar","data":[1,2]}],', '"tooltip": {},', '"legend": {}', '}'].join('\n')
    const src = `图：\n\n\`\`\`echarts\n${opt}\n\`\`\`\n`
    const segs = splitArtifacts(src)
    expect(segs.every(s => s.type === 'text')).toBe(true)
  })

  it('still lifts a large non-diagram code block', () => {
    const code = Array.from({ length: 8 }, (_, i) => `line ${i}`).join('\n')
    const segs = splitArtifacts('```python\n' + code + '\n```')
    expect(segs.some(s => s.type === 'code')).toBe(true)
  })
```

新增一个 describe：

```typescript
describe('isInlineRenderLang', () => {
  it('matches mermaid and echarts case-insensitively', () => {
    expect(isInlineRenderLang('mermaid')).toBe(true)
    expect(isInlineRenderLang('ECharts')).toBe(true)
    expect(isInlineRenderLang(' echarts ')).toBe(true)
  })
  it('does not match normal code langs', () => {
    expect(isInlineRenderLang('python')).toBe(false)
    expect(isInlineRenderLang('svg')).toBe(false)
    expect(isInlineRenderLang('')).toBe(false)
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `npx vitest run src/utils/__tests__/artifacts.test.ts`
Expected: FAIL —`isInlineRenderLang is not a function` / 新增的 mermaid/echarts 用例失败（当前它们被抬升为 code 段）。

- [ ] **Step 3: 实现**（编辑 `src/utils/artifacts.ts`）

在 `isArtifact` 函数之后、`splitArtifacts` 之前新增：

```typescript
/** Diagram/chart langs md-editor renders inline; never lift these into artifacts. */
const INLINE_RENDER_LANGS = new Set(['mermaid', 'echarts'])

export function isInlineRenderLang(lang: string): boolean {
  return INLINE_RENDER_LANGS.has((lang || '').trim().toLowerCase())
}
```

把 `splitArtifacts` 的循环体替换为（新增 `lang` 提取与内联渲染语言跳过）：

```typescript
export function splitArtifacts(src: string): Segment[] {
  const segs: Segment[] = []
  let last = 0
  let m: RegExpExecArray | null
  FENCE.lastIndex = 0
  while ((m = FENCE.exec(src))) {
    const lang = (m[1] || '').trim()
    const code = m[2].replace(/\n+$/, '')
    if (isInlineRenderLang(lang)) continue // mermaid/echarts render inline via md-editor
    if (!isArtifact(code)) continue // leave small snippets inline in the prose
    if (m.index > last) segs.push({ type: 'text', text: src.slice(last, m.index) })
    segs.push({ type: 'code', lang, code })
    last = m.index + m[0].length
  }
  if (last < src.length) segs.push({ type: 'text', text: src.slice(last) })
  if (!segs.length) segs.push({ type: 'text', text: src })
  return segs
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `npx vitest run src/utils/__tests__/artifacts.test.ts`
Expected: PASS（全部用例通过）。

- [ ] **Step 5: 提交**

```bash
git add src/utils/artifacts.ts src/utils/__tests__/artifacts.test.ts
git commit -m "feat(ai-chat): keep mermaid/echarts blocks inline for rendering"
```

---

### Task 2: 给 md-editor 注册打包的 mermaid/echarts 实例

**Files:**
- Create: `src/components/ai-chat/md-editor-setup.ts`
- Modify: `src/components/ai-chat/MarkdownView.vue`
- Modify: `package.json`（新增 `mermaid` 依赖）

> 本任务核心是浏览器内渲染与全局配置，jsdom 无法可靠测试 mermaid 异步出图，故以「类型/构建通过」为门槛，真机渲染在 Task 4 验证。

- [ ] **Step 1: 安装 mermaid 依赖**

Run: `npm install mermaid@^11`
Expected: `package.json` 的 `dependencies` 出现 `"mermaid": "^11..."`，`package-lock.json` 更新。

- [ ] **Step 2: 新建配置模块** `src/components/ai-chat/md-editor-setup.ts`

```typescript
/**
 * Register bundled mermaid + echarts instances with md-editor-v3 so the chat
 * renders ```mermaid / ```echarts blocks inline, offline (no CDN), and safely.
 * Imported for its side effect by MarkdownView; config() runs once on load.
 */
import { config } from 'md-editor-v3'
import mermaid from 'mermaid'
import * as echarts from 'echarts'

config({
  editorExtensions: {
    mermaid: { instance: mermaid },
    echarts: {
      instance: echarts,
      // Agent output is only semi-trusted. The md-editor default parses the
      // block with `new Function` (to allow function-valued options) — that is
      // arbitrary code execution. Restrict to pure JSON instead.
      parseOption: (code: string) => JSON.parse(code),
    },
  },
  // Block raw HTML / click handlers inside diagrams.
  mermaidConfig: (base: any) => ({ ...base, securityLevel: 'strict' }),
})
```

- [ ] **Step 3: 在 MarkdownView 引入该模块**（编辑 `src/components/ai-chat/MarkdownView.vue` 的 `<script setup>`，在现有 import 后追加一行 side-effect import）

```typescript
import { MdPreview } from 'md-editor-v3'
import 'md-editor-v3/lib/preview.css'
import './md-editor-setup' // register bundled mermaid/echarts (side effect)
```

- [ ] **Step 4: 类型检查 + 构建确认通过**

Run: `npm run build`
Expected: `vue-tsc` 无类型错误，`vite build` 成功产出 `dist/`（可能出现 chunk 体积告警，可忽略）。

- [ ] **Step 5: 提交**

```bash
git add package.json package-lock.json src/components/ai-chat/md-editor-setup.ts src/components/ai-chat/MarkdownView.vue
git commit -m "feat(ai-chat): register bundled mermaid/echarts with md-editor"
```

---

### Task 3: 后端 agent 提示产出 mermaid/echarts

**Files:**
- Modify: `server/routes/ai_chat.py`（`_AGENT_DIRECTIVE`）
- Test: `server/tests/test_ai_chat_directive.py`（新建）

- [ ] **Step 1: 写失败测试** `server/tests/test_ai_chat_directive.py`

```python
def test_agent_directive_mentions_diagram_fences():
    from routes.ai_chat import _AGENT_DIRECTIVE
    assert 'mermaid' in _AGENT_DIRECTIVE
    assert 'echarts' in _AGENT_DIRECTIVE
```

- [ ] **Step 2: 运行测试确认失败**

Run（在 `server/` 目录）: `set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_chat_directive.py -v`
Expected: FAIL — `assert 'mermaid' in _AGENT_DIRECTIVE`（当前文案不含 mermaid/echarts）。

- [ ] **Step 3: 实现**（编辑 `server/routes/ai_chat.py`，替换 `_AGENT_DIRECTIVE` 定义）

```python
_AGENT_DIRECTIVE = (
    "[系统规则] 若需产出脚本/配置/文档，把完整内容放进带语言和文件名的代码块"
    "（如 ```python app.py）。画流程图用 ```mermaid 代码块；画数据图表用 ```echarts 代码块"
    "（块内为 ECharts 的 JSON option，纯 JSON、不要函数）。"
    "直接给最终结果，简洁作答，不要复述本规则、不要输出你的思考或计划过程。\n\n"
)
```

- [ ] **Step 4: 运行测试确认通过 + 回归**

Run（在 `server/` 目录）: `set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_ai_chat_directive.py tests/test_routes_ai_chat.py -v`
Expected: PASS（新测试通过；既有 ai_chat 路由测试不回归。若无 `tests/test_routes_ai_chat.py` 文件，仅跑新测试 + `python -m pytest -k ai_chat -v`）。

- [ ] **Step 5: 提交**

```bash
git add server/routes/ai_chat.py server/tests/test_ai_chat_directive.py
git commit -m "feat(ai-chat): instruct agent to emit mermaid/echarts fences"
```

---

### Task 4: 真机验证（构建 + Playwright）

**Files:** 无（验证步骤，不提交代码）

前置：生产栈在 `:8080` 运行（`python proxy.py`，已含后端+MCP）。若未运行，先 `cd server && python proxy.py`。

- [ ] **Step 1: 重新构建前端**

Run: `npm run build`
Expected: 构建成功；proxy 直接读取新的 `dist/`。

- [ ] **Step 2: 造验证数据**（含较大的 mermaid 和 echarts 块，验证不再被抬升为卡片）

Run（在 `server/` 目录，`python -`）：

```python
import json, secrets, urllib.request, psycopg2
from config import DB_CONFIG
BASE='http://127.0.0.1:8080'
def req(m,p,t=None,b=None):
    d=json.dumps(b).encode() if b is not None else None
    r=urllib.request.Request(BASE+p,data=d,method=m); r.add_header('Content-Type','application/json')
    if t: r.add_header('Authorization','Bearer '+t)
    return json.loads(urllib.request.urlopen(r,timeout=30).read())
tok=req('POST','/api/auth/login',b={'username':'admin','password':'admin123'})['token']
sid=req('POST','/api/ai/chat/sessions',t=tok,b={})['id']
text=('流程图：\n\n```mermaid\ngraph TD\n  A[开始]-->B{判断}\n  B-->|是|C[处理]\n  B-->|否|D[跳过]\n  C-->E[结束]\n  D-->E\n```\n\n'
      '图表：\n\n```echarts\n{\n  "xAxis": {"type":"category","data":["A","B","C"]},\n  "yAxis": {"type":"value"},\n  "series": [{"type":"bar","data":[12,24,18]}],\n  "tooltip": {},\n  "legend": {}\n}\n```\n')
mid='msg_'+secrets.token_hex(6)
c=psycopg2.connect(**DB_CONFIG);cur=c.cursor()
cur.execute("INSERT INTO ai_chat_messages (id,session_id,role,content) VALUES (%s,%s,'assistant',%s)",(mid,sid,json.dumps([{'type':'text','text':text}])))
cur.execute("UPDATE ai_chat_sessions SET last_active_at=NOW(), title='图表验证' WHERE id=%s",(sid,))
c.commit();c.close()
print('SID='+sid); print('TOKEN='+tok)
print('USER='+json.dumps({'id':'user-admin','username':'admin','role':'admin','userId':'user-admin'}))
```

- [ ] **Step 3: Playwright 打开并断言渲染**

用 Playwright：`browser_navigate` 到 `http://localhost:8080` → `browser_evaluate` 注入
`localStorage['check-manage:token']=JSON.stringify(<TOKEN>)` 与
`localStorage['check-manage:userInfo']=JSON.stringify({id:'user-admin',username:'admin',role:'admin',userId:'user-admin'})`
→ `browser_navigate` 到 `http://localhost:8080/ai-chat` → 等待 ~2.5s → `browser_evaluate`：

```javascript
() => ({
  mermaidSvg: document.querySelectorAll('.md-editor-mermaid svg').length,
  echartsCanvas: document.querySelectorAll('.md-editor-echarts canvas').length,
  artifactCards: document.querySelectorAll('.artifact-card').length,
})
```

Expected: `mermaidSvg >= 1`（流程图出 SVG）、`echartsCanvas >= 1`（图表出 canvas）、`artifactCards === 0`（mermaid/echarts 不再是源码卡片）。再 `browser_take_screenshot` 留证。

- [ ] **Step 4: 清理验证数据（可选）**

删除造的验证会话即可（或留作演示）。无需提交。

---

## 备注 / 风险

- md-editor-v3@6.5 的 `config()` 与 `editorExtensions.{mermaid,echarts}.instance` 已核对类型定义（`node_modules/md-editor-v3/lib/types/index.d.ts`）。`mermaidConfig`/`echartsConfig` 为顶层 `GlobalConfig` 键。
- 若 Task 4 中 mermaid 仍不出图：优先核对所装 `mermaid` 版本与 md-editor 6.5 的兼容性（必要时调整 mermaid 大版本）；最坏情况回退到自建 `DiagramBlock.vue` 用 `mermaid.render()` 直接渲染（echarts 仍走 md-editor）。该回退不在本计划范围，需回到 spec 调整。
- echarts `parseOption` 采用 `JSON.parse`，仅支持纯 JSON option（无函数回调）——与 agent 提示一致，且避免 `new Function` 的代码执行风险。
