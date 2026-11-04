"""Render a session's recent conversation as a plain-text summary block.

Used to re-inject context into a freshly recovered OpenCode session when the
old `opencode_session_id` has gone stale — see `routes/ai_chat.py::
_recover_session_and_resend` (interactive chat, M3) and `utils/batch_engine.py::
BatchWorker._recover_session` (batch/scan children). Both hit this when
`send_prompt_async`/`send_message` fails against the old id: a brand-new
OpenCode session has no memory of prior turns, so the new prompt is prefixed
with this summary instead.

Shared rather than duplicated: both call sites need byte-identical formatting
(the summary has to read the same way to the agent regardless of which path
recovered it), so drift here would silently produce inconsistent context
without anyone noticing.
"""
from db import get_db


def render_history_block(session_id: str, exclude_msg_id: str | None = None,
                         max_turns: int = 6) -> str:
    """Most recent `max_turns*2` messages (excluding `exclude_msg_id`, if
    given) rendered as plain text, oldest first. Returns '' on any DB error
    or when there's nothing usable to show."""
    try:
        with get_db() as conn:
            cur = conn.cursor()
            if exclude_msg_id is not None:
                cur.execute(
                    "SELECT id, role, content FROM ai_chat_messages "
                    "WHERE session_id=%s AND id != %s "
                    "ORDER BY created_at DESC, id DESC LIMIT %s",
                    (session_id, exclude_msg_id, max_turns * 2),
                )
            else:
                cur.execute(
                    "SELECT id, role, content FROM ai_chat_messages "
                    "WHERE session_id=%s "
                    "ORDER BY created_at DESC, id DESC LIMIT %s",
                    (session_id, max_turns * 2),
                )
            rows = cur.fetchall()
    except Exception:
        return ''
    if not rows:
        return ''
    rows = list(reversed(rows))
    lines = []
    for _id, role, content in rows:
        text = ''
        if isinstance(content, list):
            text = '\n'.join(p.get('text', '') for p in content
                             if isinstance(p, dict) and p.get('type') == 'text').strip()
        if not text:
            continue
        who = '用户' if role == 'user' else '助手'
        lines.append(f'{who}: {text}')
    if not lines:
        return ''
    return '[此前对话摘要（会话已恢复，供你延续上下文）]\n' + '\n'.join(lines) + '\n\n'
