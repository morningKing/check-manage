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


def persist_turn(session_id, state):
    """Idempotent upsert of the accumulated assistant message. No-op if the
    content is empty. Keyed on the turn's OpenCode message id so the browser
    SSE proxy and the background listener converge on the same row (no dupes)."""
    content = build_content(state)
    if not content:
        return
    row_id = state.get('turn_msg_id') or ('msg_' + secrets.token_hex(6))
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO ai_chat_messages (id, session_id, role, content) "
                "VALUES (%s, %s, 'assistant', %s) "
                "ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content",
                (row_id, session_id, json.dumps(content)),
            )
    except Exception:
        pass  # don't break the listener/stream on a DB hiccup


def _run_listener(sid, opencode_session_id, event_source):
    """Consume events, persisting the assistant message on each session.idle.
    Returns when the source is exhausted/raises (inactivity read-timeout or
    stream end). Pure loop — `event_source` is injectable for tests."""
    state = new_state()
    for evt in event_source:
        if apply_event(state, evt, opencode_session_id) == 'idle':
            persist_turn(sid, state)
            state = new_state()


def _listener_thread(sid, opencode_session_id, directory):
    """Thread target: subscribe to OpenCode events for this session's workspace
    and run the persist loop. Exits on inactivity read-timeout or any error;
    removes itself from the registry so a later turn can start a fresh one."""
    try:
        source = OpenCodeClient(OPENCODE_BASE_URL).subscribe_events(
            directory=directory, read_timeout=INACTIVITY_TIMEOUT,
        )
        _run_listener(sid, opencode_session_id, source)
    except Exception:
        pass
    finally:
        with _lock:
            _listeners.pop(sid, None)


def ensure_listener(sid, opencode_session_id, directory):
    """Start a background persistence listener for `sid` if none is running.
    Called when a turn begins, so persistence happens even with no browser
    connected."""
    with _lock:
        existing = _listeners.get(sid)
        if existing and existing.is_alive():
            return
        t = threading.Thread(
            target=_listener_thread, args=(sid, opencode_session_id, directory), daemon=True,
        )
        _listeners[sid] = t
        t.start()


def stop_listener(sid):
    """Drop a session's listener from the registry (on session delete). The
    daemon thread itself exits once its OpenCode stream ends/errors."""
    with _lock:
        _listeners.pop(sid, None)
