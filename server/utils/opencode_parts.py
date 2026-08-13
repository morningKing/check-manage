"""共享的 OpenCode part → 存库内容映射，供实时聊天（chat_persist.py）与批任务
（batch_engine.py）两条持久化路径复用，避免同一套映射逻辑维护两份。

`map_part` 对 text/tool 两种既有类型的行为，是从这两处原有实现里逐字搬来的
（不是重新设计）——调用方各自决定要不要过滤空文本，这里保持"不过滤"的中立
契约，与两处原有实现"各自在 flatten 阶段过滤"的既有分工一致。

subtask 是本次新增的类型：OpenCode 委托子代理时产出的 SubtaskPart，携带子代理
自己的 OpenCode sessionID——它的完整对话轨迹在别处（ai_chat_subtasks /
ai_chat_subtask_messages）单独持久化，这里只映射成一个轻量占位（前端据此惰性
拉取），`status` 由调用方通过 `subtask_status` 传入当前已知状态（map_part 本身
不做数据库查询，保持纯函数）。
"""
from utils.ai_message_meta import tool_duration_ms


def map_part(part: dict, *, subtask_status: dict | None = None,
             subtask_id_map: dict | None = None) -> dict | None:
    """把一个 OpenCode message part 映射成持久化内容的形状。

    返回 None 表示这个 part 不需要持久化（未知类型，或 subtask 缺 sessionID
    这种畸形数据——没有 sessionID 就没法在前端拉取，占位没有意义）。

    `subtask_id_map` 把 subtask part 的 id 映射到正确的子会话 sessionID——
    SubtaskPart.sessionID 实际是 PartBase 的标准"所属会话"字段（等于父会话），
    真正的子会话 id 在配套的 tool:'task' part 的 state.metadata.sessionId 里。
    调用方在发现阶段已经算好了这个映射，传进来让占位气泡引用正确的子代理。
    """
    t = part.get('type')
    if t == 'text':
        return {'type': 'text', 'text': part.get('text', '')}
    if t == 'tool':
        st = part.get('state') or {}
        return {
            'type': 'tool_use',
            'name': part.get('tool') or 'tool',
            'title': st.get('title') or '',
            'status': st.get('status'),
            'input': st.get('input'),
            'result': st.get('output') if st.get('output') is not None else st.get('result'),
            'durationMs': tool_duration_ms(st),
        }
    if t == 'subtask':
        sid = (subtask_id_map or {}).get(part.get('id')) or part.get('sessionID')
        if not sid:
            return None
        return {
            'type': 'subtask_use',
            'subtaskId': sid,
            'agent': part.get('agent'),
            'description': part.get('description'),
            'status': (subtask_status or {}).get(sid, 'running'),
        }
    return None


# OpenCode 的 AssistantMessage.error 是七选一：ProviderAuthError / UnknownError /
# MessageOutputLengthError / MessageAbortedError / StructuredOutputError /
# ContextOverflowError / APIError，形状均为 {name, data: {providerID?, message?}}。
def format_opencode_error(error: dict | None) -> str:
    """把 error 格式化成人类可读的一句话。batch_engine._TurnFailed 与子代理的
    错误状态判定共用这一份，不各自拼一遍。"""
    error = error or {}
    name = error.get('name') or 'UnknownError'
    data = error.get('data') or {}
    detail = data.get('message') or ''
    provider = data.get('providerID') or ''
    head = f'OpenCode 报告本轮失败: {name}'
    if provider:
        head += f'（provider={provider}）'
    return f'{head}: {detail}' if detail else head
