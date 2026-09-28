"""内置工具超时门限——OpenCode 插件自动部署。

随 Flask 启动把 `plugin/baize-tool-timeout.js` 写入 OPENCODE_GLOBAL_DIR
（幂等；与 subagent_reuse_plugin / SkillOpt baize-trace 同模式）。插件在
OpenCode 进程内给每一次内置工具调用挂看门狗：

- tool.execute.before：按 (sessionID, callID) 记录开始时间并起 setTimeout；
- 计时器到点且工具仍未返回 → 调 client.session.abort({sessionID})
  中断该回合（OpenCode 没有"取消单个工具"的 API，abort 会话是唯一的
  外部断开手段——与批任务引擎 tool-stuck 看门狗同语义）；
- tool.execute.after：清计时器。

为什么需要：OC 内置工具只有 bash(默认 120s/上限 600s) 和 webfetch(30s/上限
120s) 有门限，read/grep/glob/edit/write/task 以及 MCP 调用没有任何超时——
卡死的工具在交互会话里会永远挂着（批任务有 900s tool-stuck 看门狗兜底，
交互会话没有）。本插件补齐交互侧的门限。

配置（serve 进程环境变量，opencode_launch.serve_env 透传）：
  OPENCODE_TOOL_TIMEOUT_MS      单工具门限，默认 900000（15 分钟）
  OPENCODE_TOOL_TIMEOUT_EXEMPT  豁免的工具名，逗号分隔，默认 "task"
                                （子代理委派合法耗时可能很长，批任务侧
                                已有带子代理存活检测的更聪明看门狗）

注意：OC serve 在启动时加载 plugin 目录——部署后需重启 serve 生效。
"""
import os
import logging

logger = logging.getLogger(__name__)

PLUGIN_NAME = 'baize-tool-timeout.js'

DEFAULT_TIMEOUT_MS = 900_000
DEFAULT_EXEMPT = 'task'

_PLUGIN_TEMPLATE = r"""// Baize tool timeout watchdog plugin (auto-installed by Baize server).
// Enforces a wall-clock threshold on EVERY built-in tool call: if a tool
// hasn't returned within OPENCODE_TOOL_TIMEOUT_MS, abort the session.
// OC has no per-tool cancel API — session abort is the only external
// cut-off, same semantics as the batch engine's tool-stuck watchdog.
const TIMEOUT_MS = Number(process.env.OPENCODE_TOOL_TIMEOUT_MS || '__TIMEOUT_MS__') || 900000
const EXEMPT = new Set(
  (process.env.OPENCODE_TOOL_TIMEOUT_EXEMPT || '__EXEMPT__')
    .split(',').map(s => s.trim()).filter(Boolean))

const pending = new Map() // `${sessionID}:${callID}` -> { timer, startedAt }

export const BaizeToolTimeoutPlugin = async ({ client }) => ({
  async 'tool.execute.before'(input) {
    try {
      if (!input || !input.sessionID || !input.callID) return
      if (EXEMPT.has(input.tool)) return
      const key = `${input.sessionID}:${input.callID}`
      const startedAt = Date.now()
      const timer = setTimeout(async () => {
        const entry = pending.get(key)
        if (!entry || entry.timer !== timer) return  // 已结束：after 已清理
        pending.delete(key)
        console.error(`[baize-tool-timeout] tool=${input.tool} ` +
          `session=${input.sessionID} exceeded ${Date.now() - startedAt}ms → abort`)
        try {
          await client.session.abort({ sessionID: input.sessionID })
        } catch (e) {
          console.error(`[baize-tool-timeout] abort failed: ${e && e.message}`)
        }
      }, TIMEOUT_MS)
      pending.set(key, { timer, startedAt })
    } catch { /* 看门狗自身故障不阻断工具执行 */ }
  },
  async 'tool.execute.after'(input) {
    try {
      if (!input || !input.sessionID || !input.callID) return
      const key = `${input.sessionID}:${input.callID}`
      const entry = pending.get(key)
      if (!entry) return
      clearTimeout(entry.timer)
      pending.delete(key)
    } catch { /* 清理失败无碍 */ }
  },
})
"""


def plugin_source(timeout_ms: int = DEFAULT_TIMEOUT_MS,
                  exempt: str = DEFAULT_EXEMPT) -> str:
    return (_PLUGIN_TEMPLATE
            .replace('__TIMEOUT_MS__', str(int(timeout_ms)))
            .replace('__EXEMPT__', exempt))


def ensure_tool_timeout_plugin(global_dir: str,
                               timeout_ms: int = DEFAULT_TIMEOUT_MS,
                               exempt: str = DEFAULT_EXEMPT) -> str | None:
    """写入 <OPENCODE_GLOBAL_DIR>/plugin/baize-tool-timeout.js（幂等）。

    内容无变化时不重写。部署后需重启 OC serve 才被加载。"""
    if not global_dir:
        return None
    try:
        root = os.path.realpath(global_dir)
        plugin_dir = os.path.join(root, 'plugin')
        os.makedirs(plugin_dir, exist_ok=True)
        dest = os.path.realpath(os.path.join(plugin_dir, PLUGIN_NAME))
        # 写点必须落在全局目录内（global_dir 来自环境配置，防止误配 ../ 逃逸）
        if os.path.commonpath([dest, root]) != root:
            raise ValueError('plugin path escapes global dir')
        source = plugin_source(timeout_ms, exempt)
        if os.path.isfile(dest):
            with open(dest, 'r', encoding='utf-8') as f:
                if f.read() == source:
                    return dest
        with open(dest, 'w', encoding='utf-8', newline='\n') as f:
            f.write(source)
        logger.info('tool timeout plugin installed: %s (timeout=%sms exempt=%s)',
                    dest, timeout_ms, exempt)
        return dest
    except Exception as e:  # noqa: BLE001 —— 插件部署失败不阻断启动
        logger.warning('tool timeout plugin deploy failed: %s', e)
        return None
