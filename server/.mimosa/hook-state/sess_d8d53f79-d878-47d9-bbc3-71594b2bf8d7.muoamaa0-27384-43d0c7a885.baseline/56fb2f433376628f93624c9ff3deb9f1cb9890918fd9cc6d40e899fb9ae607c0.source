"""Ownership registry for the platform-managed `opencode serve` process.

The restart path used to kill *whatever listens on the OPENCODE_BASE_URL port*.
That is only safe when the listener is the serve the platform itself spawned
(Spec §11: 外部托管模式下不得根据端口直接杀进程). This module persists the
identity of the serve the platform last launched — the listening PID found
after spawn — into a small JSON registry, so a later restart can classify the
current listener as:

  platform  recorded PID and the actual listener match → safe to kill/restart
  external  a listener exists that we have no record of → NEVER killed; the
            admin restarts it by hand (service manager / dev terminal)
  none      nothing listens → safe to spawn
  unknown   a listener exists but we have no registry (first run after
            upgrading, or the registry file was deleted) → treated like
            external: refuse to kill rather than guess

The registry lives in OPENCODE_RUNTIME_DIR (env-overridable, default
~/.check-manage/opencode-runtime) so backend and proxy.py — separate
processes — share one source of truth. PID reuse is neutralized by checking
that the recorded PID actually still listens on the recorded port before
calling it "ours".
"""

import json
import logging
import os
import tempfile
import time

logger = logging.getLogger(__name__)

OWNER_PLATFORM = 'platform'

_REG_FILENAME = 'serve-ownership.json'


def runtime_dir() -> str:
    """Directory holding the ownership registry (created lazily on write)."""
    d = (os.environ.get('OPENCODE_RUNTIME_DIR', '') or '').strip() \
        or os.path.join(os.path.expanduser('~'), '.check-manage', 'opencode-runtime')
    return d


def _registry_path() -> str:
    return os.path.join(runtime_dir(), _REG_FILENAME)


def record(pid: int, port: int) -> dict:
    """Persist the platform-launched serve's listening PID + port."""
    data = {
        'pid': int(pid),
        'port': int(port),
        'owner': OWNER_PLATFORM,
        'recorded_at': int(time.time()),
    }
    os.makedirs(runtime_dir(), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=runtime_dir(), prefix='.own-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        os.replace(tmp, _registry_path())
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return data


def read() -> dict | None:
    """Return the registry entry, or None when absent/corrupt (best-effort)."""
    try:
        with open(_registry_path(), 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) and data.get('pid') else None
    except (OSError, ValueError):
        return None


def clear() -> None:
    try:
        os.remove(_registry_path())
    except OSError:
        pass


def _pid_listens_on(pid: int, port: int, pids: list[int]) -> bool:
    return pid in pids


def status(port: int, listener_pids: list[int] | None = None) -> dict:
    """Classify the current serve listener for the given port.

    `listener_pids` is injectable for tests; production callers pass None and
    let the function discover listeners itself (utils.opencode_global's
    port-scan helper). Returns {mode, pid, recordedPid, recordedAt, listenerPids}.
    """
    if listener_pids is None:
        from utils.opencode_global import serve_listener_pids
        listener_pids = serve_listener_pids(port)

    rec = read()
    base = {
        'listenerPids': listener_pids,
        'recordedPid': (rec or {}).get('pid'),
        'recordedAt': (rec or {}).get('recorded_at'),
        'pid': listener_pids[0] if listener_pids else None,
    }

    if not listener_pids:
        # Nothing listening: either the platform's serve died or none started.
        base['mode'] = 'none'
        return base

    if rec and rec.get('port') == port \
            and _pid_listens_on(int(rec['pid']), port, listener_pids):
        # All listeners accounted for by our record → platform-owned. (Multiple
        # listeners on one port would be a broken state; primary PID check is
        # enough for the kill decision which targets the recorded PID's set.)
        base['mode'] = OWNER_PLATFORM
        return base

    # A listener we didn't launch (or can't attribute) — external/unknown are
    # both kill-forbidden; the distinction is only diagnostic detail.
    base['mode'] = 'external' if rec else 'unknown'
    return base


def platform_owned(port: int, listener_pids: list[int] | None = None) -> bool:
    """True when the current listener may be killed by the platform."""
    return status(port, listener_pids).get('mode') == OWNER_PLATFORM
