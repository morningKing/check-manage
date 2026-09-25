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
const ENDPOINT = process.env.BAIZE_SUBAGENT_REUSE_URL || '__ENDPOINT__'
const TOKEN = process.env.BAIZE_INTERNAL_TOKEN || '__TOKEN__'
const HEADERS = { 'content-type': 'application/json', 'x-internal-token': TOKEN }

async function lookup(sessionID, agent, callID) {
  const res = await fetch(
    `${ENDPOINT}/reuse?session=${encodeURIComponent(sessionID)}&agent=${encodeURIComponent(agent)}&callId=${encodeURIComponent(callID || '')}`,
    { headers: HEADERS })
  if (!res.ok) return null
  return res.json()
}

async function pinByCall(sessionID, callID, taskId) {
  await fetch(`${ENDPOINT}/pins`, {
    method: 'POST',
    headers: HEADERS,
    body: JSON.stringify({ session: sessionID, callId: callID, taskId }),
  })
}

export const BaizeSubagentReusePlugin = async () => ({
  async 'tool.execute.before'(input, output) {
    try {
      if (!input || input.tool !== 'task') return
      const args = output && output.args ? output.args : null
      if (!args) return
      const agent = args.subagent_type || args.subagentType || ''
      if (!agent || args.task_id) return  // 模型已显式指定 task_id 则不覆盖
      // callId 随 lookup 上报：平台登记意图（callID → agent），使 after
      // 阶段的 pin 不依赖子代理行的持久化时序（复核竞态修复）
      const data = await lookup(input.sessionID, agent, input.callID || '')
      if (data && data.enabled && data.taskId) args.task_id = data.taskId
    } catch { /* 平台不可达 → 放行新建，不阻断委派 */ }
  },
  async 'tool.execute.after'(input, output) {
    try {
      if (!input || input.tool !== 'task' || !input.sessionID) return
      const out = output && output.output != null ? output.output : ''
      const text = typeof out === 'string' ? out : (out && out.text) || ''
      const m = /task_id:\s*(\S+)/.exec(String(text))
      if (!m) return
      await pinByCall(input.sessionID, input.callID || '', m[1])
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
