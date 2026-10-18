/**
 * 智能写作 WS 链路冒烟（手动探针，不进常规套件）：
 *   登录 → 建文档（选模板）→ Hocuspocus 握手（JWT 鉴权）→ 写入正文 →
 *   等待 onStoreDocument 落库 → 错误 token 被拒。
 * 用法：先起齐 backend(3002) + suggest-editor-server(1234)，然后
 *   node e2e/manual/suggest-editor-ws-smoke.mjs
 */
import assert from 'node:assert'
import { HocuspocusProvider } from '@hocuspocus/provider'
import * as Y from 'yjs'

const API = process.env.SE_SMOKE_API ?? 'http://127.0.0.1:3002'
const WS = process.env.SE_SMOKE_WS ?? 'ws://127.0.0.1:1234'

async function main() {
  // 1) 登录
  const login = await fetch(`${API}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'admin', password: 'admin123' }),
  })
  assert.strictEqual(login.status, 200, `login failed: ${login.status}`)
  const { token } = await login.json()
  const auth = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }

  // 2) 建文档（带模板）
  const created = await fetch(`${API}/suggest-editor/documents`, {
    method: 'POST',
    headers: auth,
    body: JSON.stringify({ title: 'WS冒烟-可删除', templateId: 'se-tpl-weekly' }),
  })
  if (created.status !== 201) {
    throw new Error(`create doc failed: ${created.status} ${await created.text()}`)
  }
  const doc = (await created.json()).document
  console.log('[smoke] doc created:', doc.id)

  // 3) WS 握手 + 写入
  const ydoc = new Y.Doc()
  const provider = new HocuspocusProvider({ url: WS, name: doc.id, document: ydoc, token })
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('synced timeout (15s)')), 15_000)
    provider.on('synced', () => {
      clearTimeout(timer)
      resolve()
    })
    provider.on('disconnect', (e) => reject(new Error(`disconnected before sync: ${e}`)))
  })
  console.log('[smoke] ws synced (JWT accepted)')

  const fragment = ydoc.getXmlFragment('default')
  const paragraph = new Y.XmlElement('paragraph')
  const text = new Y.XmlText()
  text.insert(0, '链路冒烟：这个方案非常 不错，但是 需要验证落库。')
  paragraph.insert(0, [text])
  fragment.insert(0, [paragraph])
  console.log('[smoke] text inserted into Y.Doc')

  // onStoreDocument 防抖（hocuspocus 默认 ~2s）后落库
  await new Promise((r) => setTimeout(r, 4500))
  provider.destroy()
  ydoc.destroy()

  // 4) 错误 token 必须被拒：服务端拒绝后 provider 会自动重连重试，
  //    可观测语义是「永远到不了 synced」（服务端日志同步留痕）。
  const bad = new Y.Doc()
  let badSynced = false
  const badProvider = new HocuspocusProvider({ url: WS, name: doc.id, document: bad, token: 'not-a-jwt' })
  badProvider.on('synced', () => {
    badSynced = true
  })
  await new Promise((r) => setTimeout(r, 3000))
  badProvider.destroy()
  bad.destroy()
  assert.ok(!badSynced, 'bad token should never reach synced')
  console.log('[smoke] bad token rejected ✓')

  console.log(`[smoke] PASS — 清理：DELETE ${API}/suggest-editor/documents/${doc.id}`)
  await fetch(`${API}/suggest-editor/documents/${doc.id}`, { method: 'DELETE', headers: auth })
}

main().catch((err) => {
  console.error('[smoke] FAIL:', err)
  process.exit(1)
})
