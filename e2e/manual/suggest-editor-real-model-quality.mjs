/**
 * 智能写作「真模型」语义质量烟测（不进 CI，人工执行）。
 *
 * 只在 AGENT_KIND=opencode 的栈上跑；mock 栈请用
 *   npx playwright test e2e/suggest-editor-quality-ai.spec.ts
 * 因为 mock 的语义用例是固定契约，无法替代对真实模型判断力的评估。
 *
 * 用法：
 *   # 默认绝对化承诺
 *   node e2e/manual/suggest-editor-real-model-quality.mjs
 *   # 其它缺陷
 *   SE_QUALITY_CASE=pronoun|data|rollback|security node e2e/manual/suggest-editor-real-model-quality.mjs
 *   # 覆盖入口
 *   SE_CHECK_API=http://127.0.0.1:3002 SE_CHECK_WS=ws://127.0.0.1:1234
 *   SE_CHECK_TIMEOUT=240000
 *
 * 判定：至少 1 条建议的 quote 命中缺陷片段，且 reason 含规则/要点名 → PASS。
 * 真模型存在误判与措辞差异：这里只做「是否看见、是否定位」的烟测，不锁死文案。
 */
import assert from 'node:assert'
import { HocuspocusProvider } from '@hocuspocus/provider'
import * as Y from 'yjs'

const API = process.env.SE_CHECK_API ?? 'http://127.0.0.1:3002'
const WS = process.env.SE_CHECK_WS ?? 'ws://127.0.0.1:1234'
const TIMEOUT_MS = Number(process.env.SE_CHECK_TIMEOUT ?? 240_000)

const CASES = {
  absolute: {
    name: '绝对化表述',
    aiPrompt: '绝对化表述：不得承诺支持所有型号，须列出适用范围。',
    text: '本产品支持所有型号。',
    quoteHint: '支持所有型号',
  },
  pronoun: {
    name: '指代明确',
    aiPrompt: '指代明确：避免使用「该系统/前者/上述」等无明确指代的说法。',
    text: '该系统负责转发。',
    quoteHint: '该系统',
  },
  data: {
    name: '数据一致性',
    aiPrompt: '数据一致性：总数必须与明细表分项之和一致。',
    text: '总计 10 台。\n明细：9800M12 12 台。',
    quoteHint: '总计 10 台',
  },
  rollback: {
    name: '回滚方案',
    checklist: '回滚方案：上线步骤必须包含回滚步骤与触发条件',
    text: '上线步骤：第一步，重启设备。',
    quoteHint: '上线步骤',
  },
  security: {
    name: '权限与加密',
    checklist: '权限与加密：不得明文描述密码处理方式',
    text: '管理员密码写入配置文件。',
    quoteHint: '管理员密码',
  },
}

const caseKey = process.env.SE_QUALITY_CASE ?? 'absolute'
const scenario = CASES[caseKey]
if (!scenario) throw new Error(`未知 SE_QUALITY_CASE=${caseKey}（可选：${Object.keys(CASES).join('/')}）`)

const authHeaders = (token) => ({
  'Content-Type': 'application/json',
  Authorization: `Bearer ${token}`,
})

async function main() {
  const login = await fetch(`${API}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'admin', password: 'admin123' }),
  })
  assert.strictEqual(login.status, 200, `login failed: ${login.status}`)
  const { token } = await login.json()
  const headers = authHeaders(token)

  // 快照全局规则/检查要点（脚本失败也会在 finally 里恢复）
  const readGlobal = async (url) => {
    const res = await fetch(`${API}${url}`, { headers })
    assert.strictEqual(res.status, 200, `GET ${url} -> ${res.status()}`)
    return await res.json()
  }
  const originalRules = (await readGlobal('/suggest-editor/rules/global')).rules ?? []
  const originalChecklist = (await readGlobal('/suggest-editor/checklists/global')).content ?? ''

  let docId = ''
  let provider = null
  try {
    const now = Date.now()
    const created = await fetch(`${API}/suggest-editor/documents`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ title: `真模型质量烟测-${caseKey}-${now}` }),
    })
    assert.strictEqual(created.status, 201, `create failed: ${created.status}`)
    docId = (await created.json()).document.id
    console.log(`[quality] case=${caseKey} doc=${docId}`)

    if (scenario.aiPrompt) {
      // 文档级 AI 规则：只对本文档生效，随文档删除
      const put = await fetch(`${API}/suggest-editor/documents/${docId}/rules`, {
        method: 'PUT',
        headers,
        body: JSON.stringify({
          rules: [{
            id: `real-${caseKey}`,
            name: scenario.name,
            enabled: true,
            severity: 'warn',
            aiPrompt: scenario.aiPrompt,
          }],
        }),
      })
      assert.strictEqual(put.status, 200, `PUT doc rules -> ${put.status()}`)
    }
    if (scenario.checklist) {
      const put = await fetch(`${API}/suggest-editor/checklists/global`, {
        method: 'PUT',
        headers,
        body: JSON.stringify({ content: `# 真模型烟测\n${scenario.checklist}\n` }),
      })
      assert.strictEqual(put.status, 200, `PUT checklist -> ${put.status()}`)
    }

    const ydoc = new Y.Doc()
    provider = new HocuspocusProvider({ url: WS, name: docId, document: ydoc, token })
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('synced timeout')), 20_000)
      provider.on('synced', () => { clearTimeout(timer); resolve() })
    })
    console.log('[quality] ws synced')

    const fragment = ydoc.getXmlFragment('default')
    const paragraphs = scenario.text.split('\n')
    // 一次性写入所有段落：同一轮脏块，快照包含全部上下文
    ydoc.transact(() => {
      paragraphs.forEach((line, index) => {
        const paragraph = new Y.XmlElement('paragraph')
        const text = new Y.XmlText()
        text.insert(0, line)
        paragraph.insert(0, [text])
        fragment.insert(index, [paragraph])
      })
    })

    const started = Date.now()
    const suggestions = ydoc.getMap('suggestions')
    process.stdout.write('[quality] waiting for real-agent suggestions')
    while (Date.now() - started < TIMEOUT_MS && suggestions.size === 0) {
      await new Promise((resolve) => setTimeout(resolve, 3000))
      process.stdout.write('.')
    }
    console.log()

    const items = []
    suggestions.forEach((raw) => {
      items.push({
        quote: String(raw.quote ?? ''),
        replacement: String(raw.replacement ?? ''),
        reason: String(raw.reason ?? ''),
        severity: String(raw.severity ?? ''),
      })
    })

    if (items.length === 0) {
      throw new Error(`FAIL：${TIMEOUT_MS / 1000}s 内没有建议（模型未发现问题，或 AGENT_KIND 不是 opencode）`)
    }
    console.log(`[quality] ${items.length} 条建议，耗时 ${Math.round((Date.now() - started) / 1000)}s`)
    for (const item of items) {
      console.log(`  - [${item.severity}] ${JSON.stringify(item.quote)} → ${JSON.stringify(item.replacement)} ｜ ${item.reason}`)
    }

    const anchored = items.find((item) => item.quote.includes(scenario.quoteHint))
    assert(anchored, `FAIL：没有建议锚定到「${scenario.quoteHint}」`)
    const named = items.find(
      (item) => item.reason.includes(scenario.name) && item.quote.includes(scenario.quoteHint),
    )
    assert(named, `FAIL：锚定建议的 reason 未注明「${scenario.name}」`)
    console.log(`[quality] PASS（quote=${JSON.stringify(named.quote)}，reason=${named.reason}）`)
  } finally {
    // 恢复全局状态（规则/检查要点）+ 删除测试文档
    await fetch(`${API}/suggest-editor/checklists/global`, {
      method: 'PUT',
      headers,
      body: JSON.stringify({ content: originalChecklist }),
    }).catch(() => {})
    await fetch(`${API}/suggest-editor/rules/global`, {
      method: 'PUT',
      headers,
      body: JSON.stringify({ rules: originalRules }),
    }).catch(() => {})
    provider?.destroy()
    if (docId) {
      await fetch(`${API}/suggest-editor/documents/${docId}`, {
        method: 'DELETE',
        headers,
      }).catch(() => {})
    }
  }
}

main().catch((error) => {
  console.error('[quality] FAIL:', error.message ?? error)
  process.exit(1)
})
