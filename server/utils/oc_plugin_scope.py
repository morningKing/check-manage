"""OC 插件作用域（2026-11-24）——平台插件只在平台管理的会话里生效。

baize-trace / baize-tool-timeout / baize-subagent-reuse 装在 OpenCode **全局**
目录（<global>/plugin/），随本机每一个 OpenCode 实例加载：同一个 oc serve
既服务平台的会话工作区，也服务用户自己的其它项目目录（TUI / 桌面端 / 其它
serve 进程同理）。修复前钩子对**所有**会话生效——每次工具调用前后各打一个
后端请求（pre-check / post-check）、task 委派查后端、每次工具调用挂 abort
看门狗；后端没开时这些请求全部拒连，错误行刷进用户会话的 stderr。

实测事实（OpenCode 1.19.3，隔离实例探针）：V1 插件按「目录实例」各初始化
一份——init 入参的 directory 就是该实例会话自己的目录（同进程三个目录 →
三次 init）；V2 是 ctx.location.directory（官方 plugin 文档）。所以作用域
在初始化时即可判定：实例目录不在任何平台工作区根下 → 返回空钩子，不发
请求、不挂看门狗、不打日志。

本模块是根清单与作用域 JS 片段的唯一真相源；六个插件模板只放占位符
__BAIZE_SCOPE__，渲染时替换——作用域 JS 含反斜杠，经占位符注入可避开
skillopt.PLUGIN_JS（非 raw 字符串）的转义坑。

逃生口（serve 进程 env）：
  BAIZE_PLUGIN_SCOPE=off     关闭作用域，恢复「对所有会话生效」的旧行为
  BAIZE_PLUGIN_EXTRA_ROOTS   追加平台根（os.pathsep 分隔；自定义布局用）
"""
import json
import os

SCOPE_FLAG_ENV = 'BAIZE_PLUGIN_SCOPE'
EXTRA_ROOTS_ENV = 'BAIZE_PLUGIN_EXTRA_ROOTS'
# 占位符**连注释定界符一起**占位：模板里写 /*__BAIZE_SCOPE__*/，渲染时整块
# 换成作用域 JS。（只替换内层标识符会把注入的代码留在块注释里——Python 层
# 字符串断言照样通过，JS 实际不可用。）
SCOPE_PLACEHOLDER = '/*__BAIZE_SCOPE__*/'

_SCOPE_JS = r"""// ── 作用域（2026-11-24）──────────────────────────────────────────────
// 本插件装在 OpenCode 全局目录，随本机**每一个** OpenCode 实例加载——包括
// 用户自己的工作会话。平台会话的工作区全部位于下列根之下；实例目录不在
// 任何根下 = 非平台会话 → 插件初始化返回空钩子，完全不动作（不发请求、
// 不挂看门狗、不打日志）。BAIZE_PLUGIN_SCOPE=off 恢复全局生效（逃生口）。
const WORKSPACE_ROOTS = __WORKSPACE_ROOTS__
const _scopeNorm = (p) => {
  let s = String(p || '').replace(/\\/g, '/')
  if (s.length > 1) s = s.replace(/\/+$/, '')
  return (process.platform === 'win32' || process.platform === 'darwin')
    ? s.toLowerCase() : s
}
const inPlatformWorkspace = (directory) => {
  if (process.env.BAIZE_PLUGIN_SCOPE === 'off') return true
  const d = _scopeNorm(directory)
  if (!d) return false
  return WORKSPACE_ROOTS.some((root) => {
    const r = _scopeNorm(root)
    // 必须落在根的路径段边界内：`<root>-evil` 这类前缀相近的兄弟目录不算
    return !!r && (d === r || d.startsWith(r.endsWith('/') ? r : r + '/'))
  })
}
"""


def platform_workspace_roots() -> list[str]:
    """平台管理的 OpenCode 会话所在的工作区根（绝对路径，去重）。

    - AI_WORKSPACE_ROOT：交互 / 批任务子会话 / 独立会话 / 巡检会话的工作区
      （utils.workspace.create_session_workspace）
    - AI_CHAT_WORKSPACE_ROOT：老部署显式配置的批任务根
      （utils.workspace.batch_workspace_root；未配置时与上一项重合）
    - 根统一之前的批任务工作区（legacy_batch_workspace_root，存量会话）
    - 智能写作 agent 工作区（opencode acp 子进程的 cwd，
      suggest-editor-server 的 DATA_DIR/ws = config.SE_DATA_DIR/ws）
    - BAIZE_PLUGIN_EXTRA_ROOTS：部署侧自定义布局

    每个根同时收录字面绝对路径与 realpath 两种形态（符号链接 / 目录联接
    下的根，两种写法都认）。调用时读取配置，便于测试与运行期改 env。
    """
    import config
    from utils.workspace import batch_workspace_root, legacy_batch_workspace_root
    raw = [
        getattr(config, 'AI_WORKSPACE_ROOT', '') or '',
        batch_workspace_root(),
        legacy_batch_workspace_root(),
        os.path.join(getattr(config, 'SE_DATA_DIR', '') or '', 'ws'),
    ]
    raw.extend((os.environ.get(EXTRA_ROOTS_ENV) or '').split(os.pathsep))

    out: list[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        key = os.path.normcase(path)
        if key in seen:
            return
        seen.add(key)
        out.append(path)

    for item in raw:
        item = (item or '').strip()
        if not item:
            continue
        absolute = os.path.abspath(item)
        _add(absolute)
        try:
            real = os.path.realpath(item)
        except OSError:
            real = absolute
        _add(real)
    return out


def scope_js(roots: list[str]) -> str:
    """注入插件的作用域 JS 片段（V1 / V2 共用；V2 模板以
    ctx.location.directory 调 inPlatformWorkspace）。

    根路径以 JSON 字面量嵌入：JSON 字符串是合法 JS 字面量，且非 ASCII
    路径转义成 \\uXXXX——插件文件保持纯 ASCII。"""
    return _SCOPE_JS.replace('__WORKSPACE_ROOTS__', json.dumps(list(roots)))


def resolve_roots(workspace_roots) -> list[str]:
    """插件部署函数的 workspace_roots 形参归一：None → 平台根清单。"""
    if workspace_roots is None:
        return platform_workspace_roots()
    return [os.path.abspath(r) for r in workspace_roots if (r or '').strip()]


def inject_scope(template: str, workspace_roots=None) -> str:
    """把模板里的占位符注释整块换成作用域 JS。

    模板必须恰好含一个占位符，否则直接抛：渲染出「引用了
    inPlatformWorkspace 却没有其定义」的插件 = 每个目录实例初始化时
    ReferenceError。部署函数都包在 try/except 里，抛出表现为启动告警 +
    不覆盖已部署的旧文件，而不是把坏插件写进 OpenCode 全局目录。"""
    count = template.count(SCOPE_PLACEHOLDER)
    if count != 1:
        raise ValueError(
            f'plugin template must contain exactly one {SCOPE_PLACEHOLDER}, '
            f'found {count}')
    return template.replace(SCOPE_PLACEHOLDER, scope_js(resolve_roots(workspace_roots)))
