import logging

logger = logging.getLogger(__name__)

MAX_SUBTASK_DEPTH = 5


def discover_subtasks(messages: list, known: dict, parent_depth: int,
                      parent_sid: str | None) -> dict:
    """扫描一批（同一个会话拉到的）原始 OpenCode 消息，找出其中携带的 subtask
    part、且还不在 `known` 里的子代理，返回新发现的
    {sessionID: {'depth', 'parent_id', 'agent', 'description'}}。不递归深入
    已知子代理自己的消息——调用方对每个已知子代理的消息列表各自再调一次本
    函数，`parent_depth`/`parent_sid` 传该子代理自己的 depth/id（顶层扫描
    传 depth=0, parent_sid=None）。

    子代理的真实 sessionID 不在 SubtaskPart.sessionID（那是 PartBase 的标准
    "所属会话"字段，等于父/根会话自己），而在同一消息里配套产出的
    tool:'task' part 的 state.metadata.sessionId——按出现顺序与 subtask part
    一一配对。"""
    found = {}
    for m in (messages or []):
        parts = m.get('parts') or []
        task_tools = []
        subtask_parts = []
        for p in parts:
            if p.get('type') == 'tool' and p.get('tool') == 'task':
                sid = ((p.get('state') or {}).get('metadata') or {}).get('sessionId')
                if sid:
                    task_tools.append((sid, p))
            elif p.get('type') == 'subtask':
                subtask_parts.append(p)
        # subtask part 按出现顺序与 tool:'task' 一一配对（/command 路径两者并存）；
        # 配不上对的 tool:'task'（自然语言委托，只有 tool part）单独发现。
        paired = 0
        for i, sp in enumerate(subtask_parts):
            if i >= len(task_tools):
                break
            paired += 1
            sid, _tp = task_tools[i]
            if sid in known or sid in found:
                continue
            if parent_depth + 1 > MAX_SUBTASK_DEPTH:
                continue
            found[sid] = {'depth': parent_depth + 1, 'parent_id': parent_sid,
                          'agent': sp.get('agent'), 'description': sp.get('description'),
                          '_parent_session_id': sp.get('sessionID'),
                          '_part_id': sp.get('id')}
        for sid, tp in task_tools[paired:]:
            if sid in known or sid in found:
                continue
            if parent_depth + 1 > MAX_SUBTASK_DEPTH:
                continue
            inp = ((tp.get('state') or {}).get('input') or {})
            found[sid] = {'depth': parent_depth + 1, 'parent_id': parent_sid,
                          'agent': inp.get('subagent_type'),
                          'description': inp.get('description'),
                          '_parent_session_id': m.get('info', {}).get('sessionID'),
                          '_part_id': tp.get('id')}
    return found


def subtask_status_from_messages(messages: list) -> tuple[str, str | None]:
    """从子代理自己的原始消息列表判定当前状态：('failed', 原因) / ('completed', None)
    / ('running', None)。跟批任务顶层的完成/报错判定同源（复用
    opencode_parts.format_opencode_error），不是另起一套。"""
    from ai.opencode.opencode_parts import format_opencode_error
    for m in reversed(messages or []):
        info = m.get('info') or {}
        if info.get('role') != 'assistant':
            continue
        if info.get('error'):
            return 'failed', format_opencode_error(info['error'])
        finish = info.get('finish')
        completed = (info.get('time') or {}).get('completed')
        if completed and finish not in (None, '', 'tool-calls', 'tool_use'):
            return 'completed', None
        return 'running', None
    return 'running', None


def _raw_has_open_delegation(raw) -> bool:
    """根会话快照里是否存在未终态的 task part（=委派正在执行）。

    快照短路的豁免开关：委派执行期根会话静止（sig 恒等），但子代理树在
    持续产出消息——此时必须照常全量拉取，否则子代理内容无实时性。"""
    for m in raw or []:
        for p in (m.get('parts') or []):
            if p.get('tool') == 'task':
                if (p.get('state') or {}).get('status') not in ('completed', 'error'):
                    return True
    return False


def gate_participates(gate: dict, applicable: int) -> bool:
    """门禁判定信号（H3，单一事实来源——引擎与测试共用，10 号 §7-2）。

    任一生效信号 > 0 即参与判定：evaluated（实际核对了的期望条数）、
    applicable（批级登记条数）、expected（已登记但无法核对的条数，入口 D
    补挂/账本不健康场景）。三者皆 0 才允许 skipped 放行。"""
    evaluated = len(gate.get('results') or [])
    expected = (gate.get('expected') or 0)
    return evaluated > 0 or applicable > 0 or expected > 0


def _require_fencing(token):
    """M6（10 号 §3.3）：终态写包装方法必须显式携带 fencing token——
    worker 传 claim 捕获值，对账/重定向等权威路径传决策查询刚读出的
    当前值。未传即 TypeError（fail-fast），杜绝"未传=免检"回归。"""
    if token is None:
        raise TypeError(
            "fencing_token is required for terminal writes (M6): "
            "pass the claimed token or the freshly-read current value")



__all__ = [
    'MAX_SUBTASK_DEPTH',
    'discover_subtasks',
    'subtask_status_from_messages',
    '_raw_has_open_delegation',
    'gate_participates',
    '_require_fencing',
    'logger',
]
