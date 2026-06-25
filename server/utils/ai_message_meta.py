"""Extract per-message timing / token / cost metadata from OpenCode messages.

OpenCode message `info` carries `time {created, completed}` (epoch ms),
`tokens {input, output, ...}` and `cost`; tool parts carry `state.time
{start, end}`. We surface these so the chat UI can show execution time, tokens
and cost per turn (and per tool call). Shared by the interactive persist path
(chat_persist) and the batch persist path (batch_engine).
"""


def meta_from_info(info):
    """Per-message meta from an assistant message `info`, or None if it never
    completed (no duration to report). Shape:
        {durationMs, tokensInput, tokensOutput, cost}
    """
    if not isinstance(info, dict):
        return None
    t = info.get('time') or {}
    created, completed = t.get('created'), t.get('completed')
    if not created or not completed:
        return None
    tok = info.get('tokens') or {}
    return {
        'durationMs': max(0, int(completed) - int(created)),
        'tokensInput': int(tok.get('input') or 0),
        'tokensOutput': int(tok.get('output') or 0),
        'cost': float(info.get('cost') or 0),
        '_created': int(created),
        '_completed': int(completed),
    }


def aggregate_metas(metas):
    """Combine several per-message metas into one turn-level meta.

    duration spans first start → last finish; output tokens + cost sum across
    messages; input tokens take the max (OpenCode resends the growing context
    each step, so summing would double-count — the last/largest is the real
    context size). Returns None if no usable metas.
    """
    usable = [m for m in metas if m]
    if not usable:
        return None
    created = min(m['_created'] for m in usable)
    completed = max(m['_completed'] for m in usable)
    return {
        'durationMs': max(0, completed - created),
        'tokensInput': max(m['tokensInput'] for m in usable),
        'tokensOutput': sum(m['tokensOutput'] for m in usable),
        'cost': round(sum(m['cost'] for m in usable), 6),
    }


def public_meta(meta):
    """Strip internal `_created`/`_completed` keys before persisting/returning."""
    if not meta:
        return None
    return {k: v for k, v in meta.items() if not k.startswith('_')}


def tool_duration_ms(state):
    """Duration (ms) of a tool call from its part `state.time {start, end}`,
    or None when unavailable."""
    if not isinstance(state, dict):
        return None
    t = state.get('time') or {}
    start, end = t.get('start'), t.get('end')
    if not start or not end:
        return None
    return max(0, int(end) - int(start))
