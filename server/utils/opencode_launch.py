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


def opencode_bin() -> str:
    return (os.environ.get('OPENCODE_BIN', '') or '').strip() or DEFAULT_BIN


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
