"""Launch configuration for `opencode serve` — the single place that knows HOW
to start the agent runtime.

Deliberately ZERO-dependency (no flask/db imports) so both the standalone
proxy.py process (same pattern as utils.upload_limits) and the Flask-side
restart orchestration (utils.opencode_global) share one source of truth.

Config (server/.env or environment):
  OPENCODE_BIN        opencode executable. Default 'opencode' (PATH lookup).
                      On Windows set the full .exe path — argv launching can't
                      resolve npm's `opencode.cmd` wrapper without a shell, and
                      a shell would break paths containing spaces.
  OPENCODE_SERVE_CMD  Full override command, executed via shell, for wrapper
                      scripts / service managers. Wins over OPENCODE_BIN.
  OPENCODE_SERVE_CWD  Working directory for the spawned serve. Default '~' —
                      neutral ground so no project-level .opencode/ is picked up.
  OPENCODE_AUTOSTART  proxy.py: start serve automatically when it's not
                      reachable at startup. Default on; '0' restores the old
                      probe-and-warn-only behavior.
"""

import os

DEFAULT_BIN = 'opencode'

# Mirrors config.OPENCODE_GLOBAL_DIR's fallback (duplicated here because this
# module is deliberately zero-dependency — proxy.py and the Flask side must
# agree on the serve's global dir without importing config.py). Pinned
# explicitly into the child env (Spec P0: 自定义 OPENCODE_GLOBAL_DIR 必须与
# serve 实际读取目录一致，两侧永不靠默认值推断对齐).
DEFAULT_GLOBAL_DIR = os.path.join(os.path.expanduser('~'), '.config', 'opencode')


def opencode_bin() -> str:
    return (os.environ.get('OPENCODE_BIN', '') or '').strip() or DEFAULT_BIN


def global_dir() -> str:
    """The global config dir the spawned serve must read."""
    return (os.environ.get('OPENCODE_GLOBAL_DIR', '') or '').strip() or DEFAULT_GLOBAL_DIR


def serve_cwd() -> str:
    return (os.environ.get('OPENCODE_SERVE_CWD', '') or '').strip() \
        or os.path.expanduser('~')


def serve_launch() -> tuple[list[str] | str, bool]:
    """Return (target, use_shell) for subprocess.Popen.

    argv form by default (safe for paths containing spaces); the raw string +
    shell only when OPENCODE_SERVE_CMD explicitly overrides the whole command.
    """
    override = (os.environ.get('OPENCODE_SERVE_CMD', '') or '').strip()
    if override:
        return override, True
    return [opencode_bin(), 'serve'], False


def serve_cmd_display() -> str:
    """Human-readable form of the command that will run (UI / failure hints)."""
    target, use_shell = serve_launch()
    return target if use_shell else ' '.join(target)


def autostart_enabled() -> bool:
    return (os.environ.get('OPENCODE_AUTOSTART', '1') or '1').strip() not in ('0', 'false', 'no')


_UTF8_MARKERS = ('utf-8', 'utf8')


def _is_utf8_locale(value: str) -> bool:
    return any(marker in value.lower() for marker in _UTF8_MARKERS)


def child_env(base: dict | None = None) -> dict:
    """Child-process environment with encoding pinned to UTF-8.

    Windows 中文环境下子进程默认继承 GBK/cp936：OpenCode（bun）自己输出
    UTF-8，但它拉起的 bash/git 等工具按控制台代码页输出 GBK，中文路径/内容
    到 OpenCode 手里就成了乱码；Python 子进程（backend/MCP）的 open() 与
    管道也默认跟随 locale。统一在启动前钉死：

      PYTHONUTF8=1 / PYTHONIOENCODING=utf-8   Python 子进程全量 UTF-8 模式
      LANG / LC_ALL = en_US.UTF-8             msys/git-bash 子进程输出编码
                                              （已有 UTF-8 值则尊重不覆盖）

    proxy.py 与 opencode_global.restart_serve 共用，保证无论谁拉起 serve，
    执行环境编码一致。
    """
    env = dict(base if base is not None else os.environ)
    env['PYTHONUTF8'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    for var in ('LANG', 'LC_ALL'):
        value = (env.get(var) or '').strip()
        if not _is_utf8_locale(value):
            env[var] = 'en_US.UTF-8'
    return env


def serve_env(base: dict | None = None) -> dict:
    """child_env + explicitly pinned OPENCODE_GLOBAL_DIR.

    serve 子进程的 OPENCODE_GLOBAL_DIR 一律显式传入（继承值不可靠：.env 只被
    Python 侧 load_dotenv 读入、不会自动出现在真实进程环境里），保证 serve
    读取的全局目录与管理界面操作的目录永远相同。BAIZE_INTERNAL_TOKEN 同理：
    SkillOpt 运行时插件（baize-trace.js）优先读该 env 作为 x-internal-token，
    未配置时回落到插件文件里嵌入的 token（ensure_runtime_plugin 安装时写入）。"""
    env = child_env(base)
    env['OPENCODE_GLOBAL_DIR'] = global_dir()
    env['BAIZE_INTERNAL_TOKEN'] = (os.environ.get('MCP_INTERNAL_TOKEN', '') or '').strip()
    return env
