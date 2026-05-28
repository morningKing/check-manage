"""Execute Python code in a session workspace and capture stdout + result files.

Backs the user-triggered "运行脚本" button (deterministic, not model-driven):
the user runs an agent-produced script to generate the actual result file when
only a script was provided. Same execution model as the MCP run_python tool.

Safety: arbitrary code execution, mitigated by — user-triggered + non-guest
(route is write_required), cwd confined to the session workspace, a hard
timeout, and truncated output. Not a strong sandbox (no network/syscall
isolation). The Flask interpreter (sys.executable) carries pandas + openpyxl;
override per-deployment via RUN_PYTHON_EXECUTABLE.
"""

import os
import re
import sys
import subprocess
import tempfile

_TIMEOUT = 30
_MAX_OUT = 8000

# Models (e.g. MiMo) often put the bare filename as the first code line
# (`squares.py`), which is never valid Python. Strip such a leading line so the
# user-triggered run doesn't fail on the model's formatting quirk.
_BARE_FILENAME = re.compile(r'^[A-Za-z0-9_\-./]+\.(py|python)$')


def _sanitize(code: str) -> str:
    lines = code.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and _BARE_FILENAME.match(lines[i].strip()):
        del lines[i]
    return "\n".join(lines)


def _interpreter() -> str:
    return os.getenv("RUN_PYTHON_EXECUTABLE") or sys.executable


def _list_outputs(out_dir: str):
    if not os.path.isdir(out_dir):
        return {}
    return {
        f: os.path.getmtime(os.path.join(out_dir, f))
        for f in os.listdir(out_dir)
        if os.path.isfile(os.path.join(out_dir, f))
    }


def run_python_in_workspace(code: str, workspace_path: str, timeout: int = _TIMEOUT) -> dict:
    out_dir = os.path.join(workspace_path, "outputs")
    os.makedirs(out_dir, exist_ok=True)
    before = _list_outputs(out_dir)

    fd, script = tempfile.mkstemp(suffix=".py", dir=workspace_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(_sanitize(code))
        try:
            proc = subprocess.run(
                [_interpreter(), script],
                cwd=workspace_path, capture_output=True, text=True, timeout=timeout,
            )
            stdout, stderr, rc, timed_out = proc.stdout, proc.stderr, proc.returncode, False
        except subprocess.TimeoutExpired as e:
            stdout = e.stdout or ""
            stderr = (e.stderr or "") + f"\n[执行超时 {timeout}s，已终止]"
            rc, timed_out = -1, True
    finally:
        try:
            os.remove(script)
        except OSError:
            pass

    after = _list_outputs(out_dir)
    new_files = sorted(f for f, m in after.items() if before.get(f) != m)

    if isinstance(stdout, str) and len(stdout) > _MAX_OUT:
        stdout = stdout[:_MAX_OUT] + "\n…[输出已截断]"
    if isinstance(stderr, str) and len(stderr) > _MAX_OUT:
        stderr = stderr[:_MAX_OUT] + "\n…[输出已截断]"

    return {
        "exitCode": rc,
        "timedOut": timed_out,
        "stdout": stdout or "",
        "stderr": stderr or "",
        "outputFiles": [f"outputs/{f}" for f in new_files],
    }
