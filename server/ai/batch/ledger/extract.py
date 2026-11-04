import json
from datetime import datetime, timezone

from ai.sessions.ai_message_meta import tool_duration_ms


MAX_ARGS_LEN = 8192


# ---------------------------------------------------------------------------
# 1. 落账
# ---------------------------------------------------------------------------

def args_to_text(inp) -> str:
    """把工具入参压成可检索文本:字符串直接用;对象按 key=value 拼接;
    其余类型 JSON 序列化。只服务于正则匹配,不追求还原原始结构。"""
    if inp is None:
        return ''
    if isinstance(inp, str):
        return inp
    if isinstance(inp, dict):
        parts = []
        for k, v in inp.items():
            if isinstance(v, str):
                parts.append(f'{k}={v}')
            else:
                try:
                    parts.append(f'{k}={json.dumps(v, ensure_ascii=False)}')
                except (TypeError, ValueError):
                    parts.append(f'{k}={v}')
        return '\n'.join(parts)
    try:
        return json.dumps(inp, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(inp)


def _start_dt(time_obj):
    """state.time.start（epoch ms）→ tz-aware UTC datetime；缺省 None。"""
    start = (time_obj or {}).get('start')
    if not start:
        return None
    return datetime.fromtimestamp(int(start) / 1000, tz=timezone.utc)


def extract_from_parts(parts) -> list:
    """从 part 字典集合抽取 tool part 记录(REST 消息 parts 原始形状)。
    返回 [(part_id, tool, args_text, state, started_at, duration_ms, meta_session_id)]——
    started_at 由 state.time.start(epoch ms)换算(tz-aware UTC),
    duration_ms 由 tool_duration_ms(state) 得出;无 time 时两者为 None。"""
    out: dict = {}
    for p in parts or []:
        if not isinstance(p, dict) or p.get('type') != 'tool':
            continue
        pid = p.get('id')
        tool = p.get('tool')
        if not pid or not tool:
            continue
        state_obj = p.get('state') or {}
        state = (state_obj.get('status') or 'pending')
        args_text = args_to_text(state_obj.get('input'))[:MAX_ARGS_LEN]
        started = _start_dt(state_obj.get('time'))
        out[pid] = (str(pid), str(tool), args_text, str(state)[:20],
                    started, tool_duration_ms(state_obj),
                    (state_obj.get('metadata') or {}).get('sessionId'))
    return list(out.values())


def extract_from_part_map(part_map) -> list:
    """从 chat_persist 累积态 parts_by_id 抽取 tool 调用记录。

    累积态的值是 map_part 映射后的形状（'tool_use'：name/status/input，
    无 'id' 字段——part id 只在 dict key 上）；批路径 REST 的原始形状
    （'tool'：tool/state.input）由 extract_from_parts 负责。两形状此处都收
    ——2026-10-05 S0 能力压测发现：修复前 record_state 只认原始形状，
    交互路径账本恒 0 行、交互工具型门禁恒 failed。
    返回 [(part_id, tool, args_text, state, started_at, duration_ms)]，
    started_at/duration_ms 口径同 extract_from_parts。"""
    out: dict = {}
    for pid, p in (part_map or {}).items():
        if not isinstance(p, dict):
            continue
        t = p.get('type')
        if t == 'tool':                      # 原始 OpenCode part 形状
            key = str(p.get('id') or pid)
            tool = p.get('tool')
            state_obj = p.get('state') or {}
            args_text = args_to_text(state_obj.get('input'))
            state = (state_obj.get('status') or 'pending')
            started = _start_dt(state_obj.get('time'))
            dur = tool_duration_ms(state_obj)
            meta_sid = (state_obj.get('metadata') or {}).get('sessionId')
        elif t == 'tool_use':                # map_part 映射形状（已透传 time）
            key = str(pid)
            tool = p.get('name')
            args_text = args_to_text(p.get('input'))
            state = (p.get('status') or 'pending')
            started = _start_dt(p.get('time'))
            dur = p.get('durationMs')
            dur = int(dur) if isinstance(dur, (int, float)) else None
            meta_sid = None   # map 形状不携带 metadata（子代理 id 在发现期已提取）
        elif t == 'subtask_use':             # 委托占位（map_part 对 tool:'task' 的映射）
            # 交互路径的委派行此前从未落账：map_part 把 tool:'task' 映射成
            # subtask_use 占位（前端气泡形状），提取器只认 tool/tool_use →
            # 整行被跳过 → 性能分解画不出「开子代理」段与嵌套分解。这里把
            # 占位还原成 task 行：描述当参数、子代理 status 当状态、子会话 id
            # 当 subtask_id；time 由 map_part 透传（缺时靠子代理生命周期兜底）。
            key = str(pid)
            tool = 'task'
            args_text = str(p.get('description') or '')
            state = (p.get('status') or 'pending')
            started = _start_dt(p.get('time'))
            dur = tool_duration_ms({'time': p.get('time')})
            meta_sid = p.get('subtaskId')
        else:
            continue
        if not tool:
            continue
        out[key] = (key, str(tool), args_text[:MAX_ARGS_LEN], str(state)[:20],
                    started, dur, meta_sid)
    return list(out.values())


def extract_tool_parts(messages) -> list:
    """从一批 OpenCode 消息抽取 tool part 记录,按 part_id 去重(同 part 取
    最后一次出现的状态——轮询/快照重复投递时,后到的状态更新)。"""
    parts = []
    for m in messages or []:
        parts.extend(m.get('parts') or [])
    return extract_from_parts(parts)



__all__ = [
    'MAX_ARGS_LEN',
    'args_to_text',
    'datetime',
    '_start_dt',
    'extract_from_parts',
    'extract_from_part_map',
    'extract_tool_parts',
    'timezone',
    'tool_duration_ms',
]
