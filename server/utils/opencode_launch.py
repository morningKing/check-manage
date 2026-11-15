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
from pathlib import Path

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
    # 工具超时门限：插件 baize-tool-timeout.js 在 serve 进程内读这两个 env，
    # .env 只被 Python 侧读入所以必须显式透传（同 OPENCODE_GLOBAL_DIR 道理）。
    # bash 默认门限走 OC 自身的实验性 env（不设则 OC 默认 120s，上限 600s）。
    for _var in ('OPENCODE_TOOL_TIMEOUT_MS', 'OPENCODE_TOOL_TIMEOUT_EXEMPT',
                 'OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS'):
        _val = (os.environ.get(_var) or '').strip()
        if _val:
            env[_var] = _val
    return env


# ---------------------------------------------------------------------------
# OpenCode 2.x 双栈（OC2 适配阶段三：V2 serve 启动链）
#
# V2 没有 OPENCODE_GLOBAL_DIR（真机实测）：全局根改为 XDG 四件套
# （config/data/state/cache 各自拼 opencode/）。V2 serve 即使未设密码也会
# 自生成随机密码链：env → 状态文件 → 现场生成并持久化，serve 重启与 backend
# 重启都经状态文件找回，免刮日志。
#
# 注意 V1/V2 二进制同名（V2 安装会覆盖 PATH 上的 opencode）——生产部署必须
# 显式 OPENCODE2_BIN 指向 V2 二进制；默认值只适合已装 V2 的开发机。
# ---------------------------------------------------------------------------

DEFAULT_OC2_XDG_ROOT = os.path.join(os.path.expanduser('~'), '.check-manage', 'oc2')
DEFAULT_OC2_PORT = 4196
_OC2_PASSWORD_FILE = 'platform-password'


def oc2_bin() -> str:
    """V2 二进制。V1/V2 同名（V2 安装覆盖 PATH 上的 opencode）——生产部署
    必须显式 OPENCODE2_BIN 指向 V2 二进制；默认值仅适合已装 V2 的开发机。"""
    return (os.environ.get('OPENCODE2_BIN', '') or '').strip() or DEFAULT_BIN


def oc2_xdg_root() -> str:
    """V2 全局数据根（XDG 家目录父级）。env 可覆盖，但路径必须归一化且
    不含 '..' 段（防路径穿越——该目录下有密码状态文件）。"""
    raw = (os.environ.get('OPENCODE2_XDG_ROOT', '') or '').strip() \
        or DEFAULT_OC2_XDG_ROOT
    root = os.path.abspath(os.path.expanduser(raw))
    if '..' in root.split(os.sep):
        raise ValueError(f'invalid OPENCODE2_XDG_ROOT: {raw!r}')
    return root


def oc2_port() -> int:
    raw = (os.environ.get('OPENCODE2_PORT', '') or '').strip()
    try:
        return int(raw) if raw else DEFAULT_OC2_PORT
    except ValueError:
        return DEFAULT_OC2_PORT


def oc2_base_url() -> str:
    return (os.environ.get('OPENCODE2_BASE_URL', '') or '').strip() \
        or f'http://127.0.0.1:{oc2_port()}'


def oc2_log_file() -> str:
    return str(Path(oc2_xdg_root()) / 'serve.log')


def oc2_password_file() -> str:
    return str(Path(oc2_xdg_root()) / _OC2_PASSWORD_FILE)


def oc2_serve_env() -> tuple[dict, str]:
    """V2 serve 子进程环境：XDG 四件套整组钉死（只设 config 一项会让
    data/state 落到用户家目录默认位置，与手工安装冲突）+ 显式
    OPENCODE_PASSWORD。密码链：env → 状态文件 → 生成并写状态文件。"""
    env = child_env()
    root = oc2_xdg_root()
    env['XDG_CONFIG_HOME'] = os.path.join(root, 'config')
    env['XDG_DATA_HOME'] = os.path.join(root, 'data')
    env['XDG_STATE_HOME'] = os.path.join(root, 'state')
    env['XDG_CACHE_HOME'] = os.path.join(root, 'cache')
    password = (os.environ.get('OPENCODE2_PASSWORD', '') or '').strip()
    if not password:
        password = oc2_known_password()
    if not password:
        import secrets
        password = secrets.token_urlsafe(32)
    Path(root).mkdir(parents=True, exist_ok=True)
    Path(oc2_password_file()).write_text(password, encoding='utf-8')
    env['OPENCODE_PASSWORD'] = password
    return env, password


def oc2_known_password() -> str:
    """客户端侧取 V2 serve 密码（opencode_v2_local 构造 client_v2 用）：
    env → 状态文件 → ''（空密码时全部请求 401，health 探活显式报红——
    不静默假装健康）。"""
    password = (os.environ.get('OPENCODE2_PASSWORD', '') or '').strip()
    if password:
        return password
    try:
        return Path(oc2_password_file()).read_text(encoding='utf-8').strip()
    except OSError:
        return ''


def spawn_oc2_serve():
    """拉起 V2 serve（detached），返回 (Popen, password)。日志落
    <xdg_root>/serve.log（append；显式密码模式下 stdout 的 server password
    行仅作审计留痕，不再需要刮取）。"""
    import subprocess
    Path(oc2_xdg_root()).mkdir(parents=True, exist_ok=True)
    env, password = oc2_serve_env()
    argv = [oc2_bin(), 'serve', '--port', str(oc2_port()),
            '--hostname', '127.0.0.1']
    log_fh = open(Path(oc2_log_file()), 'ab')
    proc = subprocess.Popen(
        argv, env=env, cwd=serve_cwd(),
        stdout=log_fh, stderr=subprocess.STDOUT,
        creationflags=(getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
                       | getattr(subprocess, 'DETACHED_PROCESS', 0)),
        close_fds=True,
    )
    return proc, password


def oc2_serve_health(timeout: float = 5) -> dict:
    """GET /api/info（V2 唯一健康面——/global/health 在 V2 兜底到 web 页，
    V1 式探活会假阳性，真机实测）。返回 {'ok', 'version', 'error'}。"""
    import base64

    import requests
    try:
        headers = {}
        password = oc2_known_password()
        if password:
            token = base64.b64encode(f'opencode:{password}'.encode()).decode()
            headers['Authorization'] = f'Basic {token}'
        url = oc2_base_url() + '/api/info'
        from urllib.parse import urlsplit
        _u = urlsplit(url)
        assert _u.scheme == 'http', 'oc2 URL scheme boundary'
        assert _u.hostname == '127.0.0.1' and _u.port == 4196, \
            'oc2 URL host boundary violated'
        resp = requests.request('GET', url, timeout=timeout, headers=headers)
        if resp.status_code == 401:
            return {'ok': False, 'version': None,
                    'error': '401 auth required (password mismatch/missing)'}
        resp.raise_for_status()
        version = (resp.json() or {}).get('version') or ''
        # 栈指纹校验：/api/info 只存在于 V2——若对面是误指的 V1 serve，
        # 这里会 404/HTML（raise_for_status 或解析失败）；再验版本号双保险。
        if version and not version.startswith('2'):
            return {'ok': False, 'version': version,
                    'error': 'OPENCODE2_BASE_URL 指向的不是 OpenCode 2.x '
                             '(version=%s)' % version}
        return {'ok': True, 'version': version, 'error': None}
    except Exception as e:  # noqa: BLE001
        return {'ok': False, 'version': None, 'error': str(e)[:200]}
