"""Server-side persistence of AI chat assistant turns, decoupled from the
browser SSE connection.

A per-session daemon thread subscribes to OpenCode's event stream and persists
the assistant message on `session.idle`, so switching sessions mid-stream no
longer loses tool calls / partial output. The accumulation helpers are also
used by the browser SSE proxy so both share one tested implementation."""
import json
import secrets
import threading

from db import get_db
from utils.opencode_client import OpenCodeClient
from config import OPENCODE_BASE_URL

INACTIVITY_TIMEOUT = 30 * 60  # seconds; listener exits after this much silence

_listeners = {}          # sid -> threading.Thread
_lock = threading.Lock()


def _event_session_id(props):
    """OpenCode puts the session id in different nested spots per event type."""
    if not isinstance(props, dict):
        return None
    if props.get('sessionID'):
        return props['sessionID']
    for k in ('part', 'info'):
        v = props.get(k)
        if isinstance(v, dict) and v.get('sessionID'):
            return v['sessionID']
    return None


def new_state():
    """Fresh per-turn accumulator."""
    return {'assistant_msg_ids': set(), 'parts_by_id': {}, 'part_order': [], 'turn_msg_id': None}


def apply_event(state, evt, opencode_session_id):
    """Consume one subscribe_events() item ({'event','data'}). Accumulate
    assistant text/tool parts into `state`. Return 'idle' on session.idle.
    Events for other sessions are ignored."""
    etype = evt.get('event', '')
    props = (evt.get('data') or {}).get('properties') or {}
    ev_sid = _event_session_id(props)
    if ev_sid and ev_sid != opencode_session_id:
        return None
    if etype == 'message.updated':
        info = props.get('info') or {}
        if info.get('role') == 'assistant' and info.get('id'):
            state['assistant_msg_ids'].add(info['id'])
            if state['turn_msg_id'] is None:
                state['turn_msg_id'] = info['id']
    elif etype == 'message.part.updated':
        part = props.get('part') or {}
        pid = part.get('id')
        if pid and part.get('messageID') in state['assistant_msg_ids']:
            ptype = part.get('type')
            if ptype == 'text':
                if pid not in state['parts_by_id']:
                    state['part_order'].append(pid)
                state['parts_by_id'][pid] = {'type': 'text', 'text': part.get('text', '')}
            elif ptype == 'tool':
                if pid not in state['parts_by_id']:
                    state['part_order'].append(pid)
                st = part.get('state') or {}
                state['parts_by_id'][pid] = {
                    'type': 'tool_use',
                    'name': part.get('tool') or 'tool',
                    'title': st.get('title'),
                    'status': st.get('status'),
                    'input': st.get('input'),
                    'result': st.get('output') if st.get('output') is not None else st.get('result'),
                }
    elif etype == 'session.idle':
        return 'idle'
    return None


def build_content(state):
    """Build the persisted assistant content (arrival order). Empty text parts
    dropped; tool_use parts kept so rendered results survive a reload."""
    content = []
    for pid in state['part_order']:
        p = state['parts_by_id'].get(pid)
        if not p:
            continue
        if p['type'] == 'text':
            if (p.get('text') or '').strip():
                content.append({'type': 'text', 'text': p['text']})
        elif p['type'] == 'tool_use':
            content.append(p)
    return content
