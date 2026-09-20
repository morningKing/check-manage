"""Tool: register_action_check — 交互会话内登记动作门禁期望(设计 §5.2 入口 C)。

用例:主 agent 在派发子代理(或执行带"必经动作"的任务)之前,把"哪些动作必须
发生"登记为期望;回合收敛(session.idle)时服务端按账本机械核对,不过门记录
失败明细。模型可以登记,但**不能增删已登记的期望**——核对在服务端,登记后
执行侧无干预路径。

强度说明:登记本身依赖模型调用(与批任务入口 A/B 的确定性登记相比弱一档),
适用于临时/长尾需求;关键流程应走批定义或模板内嵌的 action_checks。
kefu-guest 公开身份不在本工具白名单内(rbac 自动排除)。
"""

import re

import mcp.types as types

from db import get_db
from context import ToolContext

NAME = "register_action_check"

TOOL = types.Tool(
    name=NAME,
    description=(
        "为本会话登记动作门禁期望:回合结束时服务端按工具调用账本逐条核对,"
        "不过门的项会记录失败明细(动作级'到位'验证,如:某脚本被执行、某仓库"
        "被克隆、某知识文件被读取)。请在派发子代理或开始执行关键动作**之前**"
        "登记。参数:checks 数组,每项 {name, tool, args_pattern, min_count?, "
        "scope?};tool 是工具名(bash/read/write/edit/grep/glob/task 等),"
        "args_pattern 是匹配工具入参文本的 POSIX 正则(如 'git clone\\\\s+\\\\S*"
        "acme/inspector'、'docs/knowledge/xxx\\\\.md'),scope 默认 tree(含全部"
        "子代理的动作)。登记后不可由会话撤销。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "checks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "简短中文名,如「克隆目标仓库」"},
                        "tool": {"type": "string",
                                 "description": "工具名,如 bash / read"},
                        "args_pattern": {"type": "string",
                                         "description": "匹配入参文本的正则"},
                        "min_count": {"type": "integer", "minimum": 1,
                                      "default": 1},
                        "scope": {"type": "string",
                                  "enum": ["session", "tree"],
                                  "default": "tree"},
                    },
                    "required": ["name", "tool", "args_pattern"],
                },
                "minItems": 1,
                "maxItems": 20,
                "description": "要登记的期望数组",
            },
        },
        "required": ["checks"],
        "additionalProperties": False,
    },
)


class RegisterActionCheckError(Exception):
    pass


def _validate(checks):
    if not isinstance(checks, list) or not checks:
        raise RegisterActionCheckError("checks 必须是非空数组")
    normalized = []
    for i, c in enumerate(checks):
        if not isinstance(c, dict):
            raise RegisterActionCheckError(f"checks[{i}] 必须是对象")
        name = (c.get("name") or "").strip()
        tool = (c.get("tool") or "").strip()
        pattern = (c.get("args_pattern") or "").strip()
        if not name or len(name) > 100:
            raise RegisterActionCheckError(f"checks[{i}].name 必填且不超过 100 字")
        if not tool or len(tool) > 50:
            raise RegisterActionCheckError(f"checks[{i}].tool 必填且不超过 50 字")
        if not pattern:
            raise RegisterActionCheckError(f"checks[{i}].args_pattern 必填")
        try:
            re.compile(pattern)
        except re.error as e:
            raise RegisterActionCheckError(
                f"checks[{i}].args_pattern 不是合法正则: {e}")
        scope = (c.get("scope") or "tree").strip()
        if scope not in ("session", "tree"):
            raise RegisterActionCheckError(f"checks[{i}].scope 只支持 session/tree")
        try:
            min_count = int(c.get("min_count", 1) or 1)
        except (TypeError, ValueError):
            raise RegisterActionCheckError(f"checks[{i}].min_count 必须是整数")
        if min_count < 1:
            raise RegisterActionCheckError(f"checks[{i}].min_count 至少为 1")
        normalized.append((name, tool, pattern, min_count, scope))
    return normalized


def handle(input: dict, ctx: ToolContext) -> dict:
    normalized = _validate((input or {}).get("checks"))
    registered = 0
    with get_db() as conn:
        cur = conn.cursor()
        for (name, tool, pattern, min_count, scope) in normalized:
            cur.execute(
                """
                INSERT INTO action_expectations
                    (scope_type, scope_id, name, tool, args_pattern,
                     require_state, min_count, source, last_status)
                VALUES (%s, %s, %s, %s, %s, 'completed', %s, 'mcp', 'pending')
                ON CONFLICT (scope_type, scope_id, name) DO UPDATE SET
                    tool = EXCLUDED.tool,
                    args_pattern = EXCLUDED.args_pattern,
                    min_count = EXCLUDED.min_count,
                    last_status = 'pending',
                    last_checked_at = NULL,
                    last_evidence = NULL
                """,
                (scope, ctx.session_id, name, tool, pattern, min_count),
            )
            registered += 1
    return {
        "registered": registered,
        "session_id": ctx.session_id,
        "note": "已登记;回合结束(idle)时服务端自动核对,结果不可由本会话修改",
    }
