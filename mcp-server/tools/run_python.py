"""Tool: run_python — execute Python code in the current session's workspace and
return stdout + any result files written to outputs/. Lets the agent actually
RUN a script (e.g. produce a file) instead of only emitting source.

Caveats / safety: this is arbitrary code execution. It is mitigated by — guest
blocked, cwd confined to the session workspace, a hard timeout, and truncated
output. It is NOT a strong sandbox (no network/syscall isolation); it runs with
the MCP server's interpreter and privileges. Acceptable here because OpenCode's
built-in bash tool already permits arbitrary execution; this just lands result
files in the session's outputs/ so the user can download them."""

import os
import sys
import subprocess
import tempfile

import mcp.types as types
from db import get_db
from context import ToolContext
from rbac import is_readonly


NAME = "run_python"

_TIMEOUT = 30          # seconds
_MAX_OUT = 8000        # chars of stdout/stderr returned


def _interpreter() -> str:
    """Python used to run user code. Defaults to this server's interpreter
    (the mcp venv, which carries pandas + openpyxl), overridable per-deployment
    via RUN_PYTHON_EXECUTABLE to point at an interpreter with more libraries."""
    return os.getenv("RUN_PYTHON_EXECUTABLE") or sys.executable

TOOL = types.Tool(
    name=NAME,
    description=(
        "在本次会话的工作目录中执行 Python 代码,并返回标准输出以及写入 outputs/ 的结果文件"
        "(用户可下载)。当你需要真正运行脚本产出文件(如生成 Excel/图表/报告)时调用。"
        "运行环境已内置 pandas 与 openpyxl。请把结果文件写入相对路径 outputs/ 下。"
        "参数:code=完整 Python 代码。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "要执行的完整 Python 代码"},
        },
        "required": ["code"],
        "additionalProperties": False,
    },
)


class RunPythonError(Exception):
    pass


def _workspace_for_session(session_id: str):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT workspace_path FROM ai_chat_sessions WHERE id = %s AND status = 'active'",
            (session_id,),
        )
        row = cur.fetchone()
    return row[0] if row else None


def _list_outputs(out_dir: str):
    if not os.path.isdir(out_dir):
        return {}
    return {
        f: os.path.getmtime(os.path.join(out_dir, f))
        for f in os.listdir(out_dir)
        if os.path.isfile(os.path.join(out_dir, f))
    }


def handle(input: dict, ctx: ToolContext) -> dict:
    if is_readonly(ctx.role):
        raise RunPythonError("read-only role is not allowed to run code")
    code = (input or {}).get("code")
    if not code or not isinstance(code, str):
        raise RunPythonError("code is required")

    ws = _workspace_for_session(ctx.session_id)
    if not ws:
        raise RunPythonError("session workspace not found")
    out_dir = os.path.join(ws, "outputs")
    os.makedirs(out_dir, exist_ok=True)

    before = _list_outputs(out_dir)

    # write code to a temp script inside the workspace, run with cwd=workspace
    fd, script = tempfile.mkstemp(suffix=".py", dir=ws)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(code)
        try:
            proc = subprocess.run(
                [_interpreter(), script],
                cwd=ws, capture_output=True, text=True, timeout=_TIMEOUT,
            )
            stdout, stderr, rc = proc.stdout, proc.stderr, proc.returncode
            timed_out = False
        except subprocess.TimeoutExpired as e:
            stdout = e.stdout or ""
            stderr = (e.stderr or "") + f"\n[执行超时 {_TIMEOUT}s,已终止]"
            rc = -1
            timed_out = True
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
        "exit_code": rc,
        "timed_out": timed_out,
        "stdout": stdout or "",
        "stderr": stderr or "",
        "output_files": [f"outputs/{f}" for f in new_files],
    }
