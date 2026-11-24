// OC 插件作用域执行器（test_oc_plugin_scope.py 的 Node 侧）。
//
// 把 Python 模板渲染出的插件 JS 当真 ES 模块 import 进来，按 OpenCode 的
// 调用形态驱动：V1 = 导出的插件函数 init({client, directory}) → 钩子对象；
// V2 = default.setup(ctx)（ctx.location.directory = 插件实例所属 location）。
// 断言：非平台目录 → 不注册任何钩子、不打任何 [baize- 日志；平台目录 →
// 钩子就位且真的会打后端。
//
// 后端用本地 sink 顶替：插件读 BAIZE_*_URL env 优先于内嵌值，故在 import 前
// 把 env 指到 sink，sink 记录收到的每个请求路径。
//
// 用法：node oc_plugin_scope_runner.mjs <config.json> <report.json>
import fs from 'node:fs'
import http from 'node:http'
import { pathToFileURL } from 'node:url'

const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))
const reportPath = process.argv[3]

const hits = []
const sink = http.createServer((req, res) => {
  req.on('data', () => {})
  req.on('end', () => {
    hits.push(req.method + ' ' + req.url)
    const url = req.url || ''
    const body = url.includes('post-check') ? { rules: 0, inject: false }
      : url.includes('pre-check') ? { rules: 0, allow: true }
        : url.includes('/reuse') ? { enabled: false, reason: 'no_agent' }
          : { ok: true }
    res.writeHead(200, { 'content-type': 'application/json' })
    res.end(JSON.stringify(body))
  })
})
await new Promise((resolve) => sink.listen(0, '127.0.0.1', resolve))
const base = 'http://127.0.0.1:' + sink.address().port

// 必须在 import 之前设好：插件在模块求值时读 env
process.env.BAIZE_RUNTIME_EVENT_URL = base + '/ai/memory/internal/runtime-events'
process.env.BAIZE_GATE_PRECHECK_URL = base + '/ai/gate/internal/pre-check'
process.env.BAIZE_GATE_POSTCHECK_URL = base + '/ai/gate/internal/post-check'
process.env.BAIZE_SUBAGENT_REUSE_URL = base + '/ai/subagent-internal'
process.env.BAIZE_INTERNAL_TOKEN = 'scope-test-token'
delete process.env.BAIZE_PLUGIN_SCOPE

const cases = []
const check = (name, ok, detail) => cases.push({ name, ok: !!ok, detail: detail || '' })

// 包住一段执行，收集期间 console.error 的 [baize- 行（插件惰性 = 零日志）
const capture = async (fn) => {
  const lines = []
  const orig = console.error
  console.error = (...a) => { lines.push(a.map(String).join(' ')) }
  try {
    return { result: await fn(), lines: lines.filter((l) => l.includes('[baize-')) }
  } finally {
    console.error = orig
  }
}

const stubClient = { session: { abort: async () => {} } }
const emptyStream = () => ({
  [Symbol.asyncIterator]() {
    return { next: async () => ({ done: true, value: undefined }) }
  },
})
const v2Ctx = (directory) => {
  const registered = []
  return {
    registered,
    location: { directory },
    tool: { hook: async (name) => { registered.push(name) } },
    event: { subscribe: emptyStream },
    session: { interrupt: async () => {} },
  }
}
const ownHooks = (obj) => Object.keys(obj || {}).filter((k) => typeof obj[k] === 'function')
const otherDirs = cfg.otherDirs || [cfg.otherDir]

for (const p of cfg.plugins) {
  try {
    const imported = await capture(() => import(pathToFileURL(p.file).href))
    check(p.name + ': import 无副作用日志', imported.lines.length === 0,
      imported.lines.join(' | '))

    if (p.kind === 'v1') {
      const fns = Object.values(imported.result).filter((v) => typeof v === 'function')
      check(p.name + ': 导出恰好一个插件函数', fns.length === 1, 'n=' + fns.length)
      const init = fns[0]

      for (const dir of otherDirs) {
        const tag = dir === cfg.otherDir ? '非平台目录' : '前缀相近的兄弟目录'
        const inert = await capture(() => init({ client: stubClient, directory: dir }))
        check(p.name + ': ' + tag + ' → 不注册任何钩子', ownHooks(inert.result).length === 0,
          'hooks=' + ownHooks(inert.result).join(',') + ' dir=' + dir)
        check(p.name + ': ' + tag + ' → 零日志', inert.lines.length === 0,
          inert.lines.join(' | '))
      }

      const active = await capture(() => init({ client: stubClient, directory: cfg.managedDir }))
      check(p.name + ': 平台目录 → 钩子就位', ownHooks(active.result).length > 0,
        'hooks=' + ownHooks(active.result).join(','))

      if (p.name === 'baize-trace') {
        const before = hits.length
        await active.result['tool.execute.before'](
          { tool: 'bash', sessionID: 'ses_scope', callID: 'call_1' },
          { args: { command: 'echo hi' } })
        await active.result['tool.execute.after'](
          { tool: 'bash', sessionID: 'ses_scope', callID: 'call_1', args: { command: 'echo hi' } },
          { output: 'hi' })
        check(p.name + ': 平台目录 → pre/post-check 真的打后端',
          hits.slice(before).some((h) => h.includes('pre-check'))
          && hits.slice(before).some((h) => h.includes('post-check')),
          hits.slice(before).join(' | '))
        // 放行语义不变：sink 返回 allow → 钩子不抛
        let threw = false
        try {
          await active.result['tool.execute.before'](
            { tool: 'bash', sessionID: 'ses_scope', callID: 'call_2' }, { args: {} })
        } catch { threw = true }
        check(p.name + ': 后端放行时 before 钩子不抛', !threw)
      }
      if (p.name === 'baize-subagent-reuse') {
        const before = hits.length
        await active.result['tool.execute.before'](
          { tool: 'task', sessionID: 'ses_scope', callID: 'call_1' },
          { args: { subagent_type: 'explore' } })
        check(p.name + ': 平台目录 → task 委派真的查后端',
          hits.slice(before).some((h) => h.includes('/reuse')),
          hits.slice(before).join(' | '))
      }
      if (p.name === 'baize-tool-timeout') {
        // before/after 成对 → 计时器清掉，不抛
        let threw = false
        try {
          await active.result['tool.execute.before'](
            { tool: 'bash', sessionID: 'ses_scope', callID: 'call_1' }, { args: {} })
          await active.result['tool.execute.after'](
            { tool: 'bash', sessionID: 'ses_scope', callID: 'call_1' }, { output: {} })
        } catch { threw = true }
        check(p.name + ': 平台目录 → 看门狗 before/after 成对不抛', !threw)
      }

      process.env.BAIZE_PLUGIN_SCOPE = 'off'
      const escaped = await init({ client: stubClient, directory: cfg.otherDir })
      check(p.name + ': BAIZE_PLUGIN_SCOPE=off → 非平台目录也生效（逃生口）',
        ownHooks(escaped).length > 0)
      delete process.env.BAIZE_PLUGIN_SCOPE
    } else {
      const plugin = imported.result.default
      check(p.name + ': V2 default 导出带 id/setup', !!plugin && typeof plugin.setup === 'function')

      for (const dir of otherDirs) {
        const tag = dir === cfg.otherDir ? '非平台 location' : '前缀相近的兄弟 location'
        const inertCtx = v2Ctx(dir)
        const inert = await capture(() => plugin.setup(inertCtx))
        check(p.name + ': ' + tag + ' → 不注册任何钩子', inertCtx.registered.length === 0,
          'hooks=' + inertCtx.registered.join(',') + ' dir=' + dir)
        check(p.name + ': ' + tag + ' → 零日志', inert.lines.length === 0,
          inert.lines.join(' | '))
      }

      const activeCtx = v2Ctx(cfg.managedDir)
      await plugin.setup(activeCtx)
      check(p.name + ': 平台 location → 钩子就位', activeCtx.registered.length > 0,
        'hooks=' + activeCtx.registered.join(','))
    }
  } catch (e) {
    check(p.name + ': 执行不抛异常', false, String((e && e.stack) || e).slice(0, 400))
  }
}

// 不用 process.exit()：Windows 上句柄尚在关闭时强退会撞 libuv 断言
// （UV_HANDLE_CLOSING，退出码变成 0xC0000409）。让事件循环自然排空。
const ok = cases.every((c) => c.ok)
sink.closeAllConnections()
await new Promise((resolve) => sink.close(resolve))
process.exitCode = ok ? 0 : 1
const report = JSON.stringify({ ok, cases, hits: hits.length }, null, 2)
fs.writeFileSync(reportPath, report)
console.log(`[scope-runner] cases=${cases.length} failed=${cases.filter((c) => !c.ok).length} hits=${hits.length}`)
