/**
 * 智能写作「真 agent」链路检查（AGENT_KIND=opencode 时用）：
 *   登录 → 建文档 → WS 同步 → 写入带问题的文本 → 等真 opencode acp 分析出建议。
 * 首轮分析含 opencode 进程启动 + 模型调用，实测可到 ~1-3 分钟，超时 4 分钟。
 * 用法：SE_CHECK_API / SE_CHECK_WS 可覆盖入口（默认本机 3002/1234 直连）。
 */
import assert from 'node:assert'
import { HocuspocusProvider } from '@hocuspocus/provider'
import * as Y from 'yjs'

const API = process.env.SE_CHECK_API ?? 'http://127.0.0.1:3002'
const WS = process.env.SE_CHECK_WS ?? 'ws://127.0.0.1:1234'
const TIMEOUT_MS = Number(process.env.SE_CHECK_TIMEOUT ?? 240_000)

const TRIGGER =
  '系统会自动处理这个情况，但是 如果数据不对的话它也会自己重试，而且 不会  通知任何人。'

async function main() {
  const login = await fetch(`${API}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'admin', password: 'admin123' }),
  })
  assert.strictEqual(login.status, 200, `login failed: ${login.status}`)
  const { token } = await login.json()
  const auth = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }

  const created = await fetch(`${API}/suggest-editor/documents`, {
    method: 'POST',
    headers: auth,
    body: JSON.stringify({ title: `真agent检查-${Date.now()}` }),
  })
  if (created.status !== 201) throw new Error(`create failed: ${created.status}`)
  const doc = (await created.json()).document
  console.log('[real-check] doc:', doc.id)

  const ydoc = new Y.Doc()
  const provider = new HocuspocusProvider({ url: WS, name: doc.id, document: ydoc, token })
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('synced timeout')), 20_000)
    provider.on('synced', () => { clearTimeout(timer); resolve() })
  })
  console.log('[real-check] ws synced')

  const fragment = ydoc.getXmlFragment('default')
  const paragraph = new Y.XmlElement('paragraph')
  const text = new Y.XmlText()
  text.insert(0, TRIGGER)
  paragraph.insert(0, [text])
  fragment.insert(0, [paragraph])
  const started = Date.now()

  process.stdout.write('[real-check] waiting for real agent suggestions')
  const suggestions = ydoc.getMap('suggestions')
  while (Date.now() - started < TIMEOUT_MS) {
    await new Promise((r) => setTimeout(r, 3000))
    process.stdout.write('.')
    if (suggestions.size > 0) break
  }
  console.log()
  provider.destroy()
  ydoc.destroy()

  if (suggestions.size === 0) {
    throw new Error(`${Math.round((Date.now() - started) / 1000)}s 内未收到建议——查 agent 工作区日志与模型配置`)
  }
  console.log(`[real-check] PASS：${suggestions.size} 条建议，耗时 ${Math.round((Date.now() - started) / 1000)}s`)
  suggestions.forEach((raw) => {
    const v = raw
    console.log(`  - [${v.severity}] ${JSON.stringify(v.quote)} → ${JSON.stringify(v.replacement)} ｜ ${v.reason}`)
  })
  await fetch(`${API}/suggest-editor/documents/${doc.id}`, { method: 'DELETE', headers: auth })
}

main().catch((err) => {
  console.error('[real-check] FAIL:', err.message ?? err)
  process.exit(1)
})
