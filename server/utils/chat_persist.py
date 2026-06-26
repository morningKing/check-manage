"""Server-side persistence of AI chat assistant turns, decoupled from the
browser SSE connection.

A per-session daemon thread subscribes to OpenCode's event stream and persists
the assistant message on `session.idle`, so switching sessions mid-stream no
longer loses tool calls / partial output. The accumulation helpers are also
used by the browser SSE proxy so both share one tested implementation."""
import json
import logging
import secrets
import threading
import time

from db import get_db
from utils.opencode_client import OpenCodeClient
from utils.ai_message_meta import (
    meta_from_info, aggregate_metas, public_meta, tool_duration_ms)
from config import OPENCODE_BASE_URL

logger = logging.getLogger(__name__)

# Debounce for mid-turn (incremental) persistence: at most one DB upsert this
# often while a turn streams, so switching sessions mid-stream recovers the
# partial answer (incl. tool calls) instead of only the on-idle final snapshot.
INCREMENTAL_PERSIST_INTERVAL = 1.0  # seconds

INACTIVITY_TIMEOUT = 30 * 60  # seconds; read timeout on the event stream. The
# listener exits after this much *silence* on the scoped stream (no bytes). If
# OpenCode emits keepalives the thread may live longer, until a real stream end.

_listeners = {}          # sid -> threading.Thread
_lock = threading.Lock()


def event_session_id(props):
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
    return {'assistant_msg_ids': set(), 'parts_by_id': {}, 'part_order': [],
            'turn_msg_id': None, 'meta_by_msg': {}}


def apply_event(state, evt, opencode_session_id):
    """Consume one subscribe_events() item ({'event','data'}). Accumulate
    assistant text/tool parts into `state`. Returns 'idle' on session.idle,
    'changed' when an assistant text/tool part was (re)captured, else None.
    Events for other sessions are ignored."""
    etype = evt.get('event', '')
    props = (evt.get('data') or {}).get('properties') or {}
    ev_sid = event_session_id(props)
    if ev_sid and ev_sid != opencode_session_id:
        return None
    if etype == 'message.updated':
        info = props.get('info') or {}
        if info.get('role') == 'assistant' and info.get('id'):
            state['assistant_msg_ids'].add(info['id'])
            if state['turn_msg_id'] is None:
                state['turn_msg_id'] = info['id']
            # Capture timing/tokens/cost; overwrite so the final (completed)
            # update wins. None until the message completes.
            m = meta_from_info(info)
            if m:
                state['meta_by_msg'][info['id']] = m
    elif etype == 'message.part.updated':
        part = props.get('part') or {}
        pid = part.get('id')
        if pid and part.get('messageID') in state['assistant_msg_ids']:
            ptype = part.get('type')
            if ptype == 'text':
                if pid not in state['parts_by_id']:
                    state['part_order'].append(pid)
                state['parts_by_id'][pid] = {'type': 'text', 'text': part.get('text', '')}
                return 'changed'
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
                    'durationMs': tool_duration_ms(st),
                }
                return 'changed'
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
    meta = public_meta(aggregate_metas(list(state.get('meta_by_msg', {}).values())))
    meta_json = json.dumps(meta) if meta else None
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO ai_chat_messages (id, session_id, role, content, meta) "
                "VALUES (%s, %s, 'assistant', %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content, "
                "  meta = COALESCE(EXCLUDED.meta, ai_chat_messages.meta)",
                (row_id, session_id, json.dumps(content), meta_json),
            )
    except Exception as e:
        # Don't break the listener/stream on a DB hiccup — but no longer silent.
        logger.warning('persist_turn DB error session=%s row=%s: %s', session_id, row_id, e)


def _run_listener(sid, opencode_session_id, event_source):
    """Consume events, persisting the assistant message on each session.idle and
    incrementally (time-debounced) while a turn streams, so switching sessions
    mid-stream recovers the partial answer. Returns when the source is
    exhausted/raises. Pure loop — `event_source` is injectable for tests."""
    state = new_state()
    last_persist = time.monotonic()
    for evt in event_source:
        sig = apply_event(state, evt, opencode_session_id)
        if sig == 'idle':
            persist_turn(sid, state)
            logger.debug('persist listener idle->persisted session=%s parts=%d',
                         sid, len(state.get('part_order', [])))
            try:
                from utils.memory import extract_from_turn
                extract_from_turn(sid, state)
            except Exception as e:
                logger.warning('memory extract_from_turn failed session=%s: %s', sid, e)
            state = new_state()
            last_persist = time.monotonic()
        elif sig == 'changed' and state['turn_msg_id']:
            now = time.monotonic()
            if now - last_persist >= INCREMENTAL_PERSIST_INTERVAL:
                persist_turn(sid, state)
                last_persist = now


def _listener_thread(sid, opencode_session_id, directory):
    """Thread target: subscribe to OpenCode events for this session's workspace
    and run the persist loop. Exits on inactivity read-timeout or any error;
    removes itself from the registry so a later turn can start a fresh one."""
    try:
        logger.info('persist listener start session=%s oc=%s', sid, opencode_session_id)
        source = OpenCodeClient(OPENCODE_BASE_URL).subscribe_events(
            directory=directory, read_timeout=INACTIVITY_TIMEOUT,
        )
        _run_listener(sid, opencode_session_id, source)
        logger.info('persist listener stream ended session=%s', sid)
    except Exception:
        # Previously swallowed — the #1 reason a "stuck" session left no trace.
        logger.exception('persist listener crashed session=%s', sid)
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


def has_listener(sid):
    """True if a live background persistence listener owns this session. The SSE
    proxy uses this to AVOID persisting itself — otherwise a browser that opens a
    turn mid-stream captures a different turn_msg_id than the listener and writes
    a duplicate partial row (seen when opening a running batch child)."""
    with _lock:
        t = _listeners.get(sid)
        return bool(t and t.is_alive())
