"""子代理会话复用——OpenCode 插件自动部署。

随 Flask 启动把 `plugin/baize-subagent-reuse.js` 写入 OPENCODE_GLOBAL_DIR
（幂等；仿 skillopt.ensure_runtime_plugin 的 baize-trace 模式）。插件在
OpenCode 进程内拦截 task 工具：

- tool.execute.before：按 (父会话, subagent_type) 查平台登记表，命中则把
  钉住的 task_id 强制写进工具入参——子代理会话复用不依赖模型自觉；
- tool.execute.after：从工具输出解析 `task_id: ses_xxx`，经平台反查 agent
  名后回写平台登记（首次委派新建子会话后固化锚点）。

所有 HTTP 回调 best-effort：平台不可达时插件静默放行，委派照常执行。
注意：OC serve 进程在启动时加载 plugin 目录——部署后需重启 serve 生效。
"""
import logging

logger = logging.getLogger(__name__)

PLUGIN_NAME = 'baize-subagent-reuse.js'

_PLUGIN_TEMPLATE = r"""// Baize subagent session reuse plugin (auto-installed by Baize server).
// Forces task-tool delegation to REUSE the pinned subagent session per
// (parent session, subagent_type): injects task_id before execution and
// registers the session id from the tool output after execution.
// Endpoint 与 token 由服务端安装时嵌入；同名 env 变量存在时优先。
// 可靠性（生产"概率性不复用"修复，2026-09-29）：回调 fetch 带 3s 超时 +
// 1 次重试 + 进程内缓存兜底——后端重启窗口/瞬时拒连时用缓存继续注入，
// 不再静默退化为新建；失败打 stderr（serve 日志可见）。
const ENDPOINT = process.env.BAIZE_SUBAGENT_REUSE_URL || '__ENDPOINT__'
const TOKEN = process.env.BAIZE_INTERNAL_TOKEN || '__TOKEN__'
const HEADERS = { 'content-type': 'application/json', 'x-internal-token': TOKEN }
const FETCH_TIMEOUT_MS = 3000
// (sessionID) → { agents:Set, pins:Map(agent→taskId), at } —— 后端瞬断时的兜底判定
const _cache = new Map()
const CACHE_TTL_MS = 120000
// (callID → { agent, at }) —— 双保险（2026-10-08 生产「概率性新开 task_id」）：
// before 阶段进程内记住委派目标，after 阶段随 POST /pins 带回 agent——
// 平台侧 intent 被剪枝/未登记（before 瞬断走缓存兜底时 /reuse 未被调到）
// 时，body.agent 是 pin 写成的最后兜底。
const _callAgents = new Map()
const CALL_AGENT_TTL_MS = 2 * 60 * 60 * 1000

function _rememberCallAgent(callID, agent) {
  if (!callID || !agent) return
  const now = Date.now()
  for (const [k, v] of _callAgents)
    if (now - v.at > CALL_AGENT_TTL_MS) _callAgents.delete(k)
  _callAgents.set(callID, { agent, at: now })
}

function _takeCallAgent(callID) {
  const v = callID ? _callAgents.get(callID) : null
  if (v) _callAgents.delete(callID)
  return v ? v.agent : ''
}

function _log(...a) { console.error('[baize-subagent-reuse]', ...a) }

async function _fetchJson(url, opts) {
  for (let attempt = 0; attempt < 2; attempt++) {
    const ctrl = new AbortController()
    const timer = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS)
    try {
      const res = await fetch(url, { ...opts, signal: ctrl.signal, headers: HEADERS })
      clearTimeout(timer)
      if (!res.ok) return { ok: false, status: res.status }
      return { ok: true, body: await res.json() }
    } catch (e) {
      clearTimeout(timer)
      if (attempt === 1) { _log('fetch failed', url.replace(/\?.*/, ''), e && e.message); return { ok: false, error: String(e && e.message) } }
      await new Promise(r => setTimeout(r, 300))
    }
  }
}

function _cacheGet(sessionID) {
  const c = _cache.get(sessionID)
  if (!c || Date.now() - c.at > CACHE_TTL_MS) { _cache.delete(sessionID); return null }
  return c
}

async function lookup(sessionID, agent, callID) {
  const cached = _cacheGet(sessionID)
  // 缓存负判定只认「确定性拒绝」（not_enabled/no_agent——名单没配这类稳定
  // 结论）；unresolved（父会话反查失败，DB 瞬断/落库窗口）不缓存——固化它
  // 会把瞬断窗口内的强制注入停掉 120s（生产"概率性不复用"根因之一）。
  if (cached && !cached.agents.has(agent)) {
    if (cached.negative && cached.negative.has(agent)) return { enabled: false }
  }
  const r = await _fetchJson(
    `${ENDPOINT}/reuse?session=${encodeURIComponent(sessionID)}&agent=${encodeURIComponent(agent)}&callId=${encodeURIComponent(callID || '')}`)
  if (!r.ok) {
    // 后端瞬断：缓存兜底——知道 pin 就继续注入，否则只能放行新建
    if (cached) {
      const taskId = cached.pins.get(agent) || null
      if (taskId || cached.agents.has(agent)) return { enabled: cached.agents.has(agent), taskId }
    }
    return null
  }
  const body = r.body || {}
  const c = _cacheGet(sessionID) || { agents: new Set(), negative: new Set(), pins: new Map(), at: 0 }
  if (body.enabled) {
    c.agents.add(agent); c.at = Date.now()
    c.negative && c.negative.delete(agent)
    if (body.taskId) c.pins.set(agent, body.taskId)
  } else if (body.reason === 'not_enabled' || body.reason === 'no_agent') {
    c.agents.add(agent); c.negative.add(agent); c.at = Date.now()   // 确定性拒绝才固化
  } else {
    // unresolved：不写缓存——下次委派重新查询平台
    return body
  }
  _cache.set(sessionID, c)
  return body
}

async function pinByCall(sessionID, callID, taskId, agent) {
  const body = { session: sessionID, callId: callID, taskId }
  if (agent) body.agent = agent   // intent 兜底：平台 callId 未命中时按 body.agent 写 pin
  const r = await _fetchJson(`${ENDPOINT}/pins`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  // pin 失败不本地兜底（本地无 callID→agent 映射，乱记会让 A 复用到 B 的
  // 会话）；后果有界：本次委派的 pin 缺失，下一次委派新建一次，后端恢复
  // 后自愈。stderr 留痕供排查。
  if (!r.ok) _log('pin FAILED (本次 pin 缺失，下次委派将新建；后端恢复后自愈)',
                  sessionID, taskId)
  return r.ok
}

export const BaizeSubagentReusePlugin = async () => ({
  async 'tool.execute.before'(input, output) {
    try {
      if (!input || input.tool !== 'task') return
      const args = output && output.args ? output.args : null
      if (!args) return
      const agent = args.subagent_type || args.subagentType || ''
      if (!agent) return
      // 先记 callID→agent（与 lookup 结果无关——lookup 失败走缓存兜底时
      // 平台收不到 callId intent，这里就是 after 阶段唯一的 agent 来源）
      _rememberCallAgent(input.callID || '', agent)
      // callId 随 lookup 上报：平台登记意图（callID → agent），使 after
      // 阶段的 pin 不依赖子代理行的持久化时序（复核竞态修复）
      const data = await lookup(input.sessionID, agent, input.callID || '')
      // 复用可观测性（2026-09-30）：每次 task 委派都留一行结果日志——注入了
      // 什么/为什么没注入，serve 日志可直接回答"这次委派是否复用"。
      const desc = `parent=${input.sessionID} agent=${agent} call=${(input.callID || '').slice(0, 12)}`
      // pin 权威（生产"概率性不复用"修复 2026-09-29）：平台有 pin 时覆盖
      // 模型自带的 task_id——模型常会回显/编造上一轮 id，原"模型指定即放行"
      // 会绕过复用；平台无 pin（首次委派）才保留模型的值。
      if (data && data.enabled && data.taskId) {
        const via = args.task_id && args.task_id !== data.taskId
          ? 'override(model-specified)' : 'injected'
        _log(`reuse ${via}: task_id=${data.taskId} ${desc}`)
        args.task_id = data.taskId
        return
      }
      if (args.task_id) {
        _log(`reuse model-specified (no platform pin): task_id=${args.task_id} ${desc}`)
        return
      }
      _log(`reuse MISS → new session ${desc} reason=${(data && data.reason) || (data ? 'no-pin' : 'lookup-failed')}`)
    } catch { /* 平台不可达 → 放行新建，不阻断委派 */ }
  },
  async 'tool.execute.after'(input, output) {
    try {
      if (!input || input.tool !== 'task' || !input.sessionID) return
      const out = output && output.output != null ? output.output : ''
      const text = typeof out === 'string' ? out : (out && out.text) || ''
      let taskId = null
      const m = /task_id:\s*(\S+)/.exec(String(text))
      if (m) {
        taskId = m[1]
      } else {
        // 兜底（2026-09-30）：输出截断/格式变化导致正则不命中时，task part 的
        // metadata.sessionId 是 OC 侧可靠的子会话 id 来源（与 chat_persist 的
        // 子代理发现同源）。拿不到就只能放弃本次登记（下次委派新建一次）。
        const meta = output && output.metadata ? output.metadata
          : (output && output.part && output.part.metadata) || null
        const msid = meta && (meta.sessionId || meta.sessionID)
        if (msid && /^ses_/.test(String(msid))) taskId = String(msid)
      }
      if (!taskId) return
      await pinByCall(input.sessionID, input.callID || '', taskId,
                      _takeCallAgent(input.callID || ''))
    } catch { /* 登记失败不影响委派 */ }
  },
})
"""


def plugin_source(endpoint: str, token: str) -> str:
    return _PLUGIN_TEMPLATE.replace('__ENDPOINT__', endpoint) \
                           .replace('__TOKEN__', token or '')


def ensure_subagent_reuse_plugin(global_dir: str, endpoint: str,
                                 token: str = '') -> str | None:
    """写入 <OPENCODE_GLOBAL_DIR>/plugin/baize-subagent-reuse.js（幂等）。

    内容无变化时不重写（避免无谓 mtime 抖动）。部署后需重启 OC serve
    才被加载。"""
    import os
    if not global_dir or not endpoint:
        return None
    try:
        plugin_dir = os.path.join(global_dir, 'plugin')
        os.makedirs(plugin_dir, exist_ok=True)
        path = os.path.join(plugin_dir, PLUGIN_NAME)
        source = plugin_source(endpoint, token)
        if os.path.isfile(path):
            with open(path, 'r', encoding='utf-8') as f:
                if f.read() == source:
                    return path
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(source)
        logger.info('subagent reuse plugin installed: %s', path)
        return path
    except Exception as e:  # noqa: BLE001 —— 插件部署失败不阻断启动
        logger.warning('subagent reuse plugin deploy failed: %s', e)
        return None
