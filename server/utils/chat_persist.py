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
    meta_from_info, aggregate_metas, public_meta)
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
    """Fresh per-turn accumulator. `subtasks` maps a child OpenCode sessionID
    to its own nested accumulator (see `_new_subtask_scope`), discovered
    recursively during this turn — see the subagent-trace-visibility design
    for why this is safe to scope per-turn: delegating to a subagent is a
    *blocking* tool call from the parent's perspective, so the parent's own
    session.idle cannot fire before every subtask under it has already
    idled/failed."""
    return {'assistant_msg_ids': set(), 'parts_by_id': {}, 'part_order': [],
            'turn_msg_id': None, 'meta_by_msg': {}, 'subtasks': {}}


def _new_subtask_scope(parent_id, depth, agent, description):
    return {'assistant_msg_ids': set(), 'parts_by_id': {}, 'part_order': [],
            'parent_id': parent_id, 'depth': depth, 'agent': agent,
            'description': description, 'status': 'running', 'error': None}


MAX_SUBTASK_DEPTH = 5


def apply_event(state, evt, opencode_session_id):
    """Consume one subscribe_events() item ({'event','data'}). Accumulate
    assistant text/tool parts into `state`. Returns 'idle' on session.idle,
    'changed' when an assistant text/tool part was (re)captured, 'subtask'
    when a subtask-scoped event was processed (see `state['subtasks']` for
    which one / what changed — this file's caller inspects the dict, the
    return value only signals "go look"), else None. Events for genuinely
    unrelated sessions are ignored — unchanged from before subtask support."""
    etype = evt.get('event', '')
    props = (evt.get('data') or {}).get('properties') or {}
    ev_sid = event_session_id(props)

    scope, is_subtask = _resolve_scope(state, ev_sid, opencode_session_id)
    scope_sid = ev_sid if is_subtask else opencode_session_id
    if scope is None and etype == 'message.part.updated':
        # For a `subtask`-typed part, `part.sessionID` (which is what
        # event_session_id() reads off `props.part.sessionID` above) names
        # the *delegate target* session, not the session that owns this
        # event — unlike every other part type, where sessionID == owning
        # session (see opencode_parts.map_part's subtask branch: it reads
        # that same field as the child's own id). That makes `ev_sid`
        # unusable for scope resolution on exactly the event that's
        # supposed to *discover* the subtask (its target session isn't
        # tracked yet). Fall back to resolving by which known scope's own
        # message this part belongs to — message.updated/session.idle don't
        # have this ambiguity, so they never need this fallback.
        mid = (props.get('part') or {}).get('messageID')
        scope_sid, scope, is_subtask = _scope_owning_message(state, mid, opencode_session_id)
    if scope is None:
        return None

    if etype == 'message.updated':
        info = props.get('info') or {}
        if info.get('role') == 'assistant' and info.get('id'):
            scope['assistant_msg_ids'].add(info['id'])
            if not is_subtask and state['turn_msg_id'] is None:
                state['turn_msg_id'] = info['id']
            m = meta_from_info(info)
            if m:
                scope['meta_by_msg'] = scope.get('meta_by_msg') or {}
                scope['meta_by_msg'][info['id']] = m
            if is_subtask and info.get('error'):
                from utils.opencode_parts import format_opencode_error
                scope['status'] = 'failed'
                scope['error'] = format_opencode_error(info['error'])
                return 'subtask'
    elif etype == 'message.part.updated':
        part = props.get('part') or {}
        pid = part.get('id')
        if pid and part.get('messageID') in scope['assistant_msg_ids']:
            ptype = part.get('type')
            if ptype in ('text', 'tool'):
                from utils.opencode_parts import map_part
                mapped = map_part(part)
                if pid not in scope['parts_by_id']:
                    scope['part_order'].append(pid)
                scope['parts_by_id'][pid] = mapped
                return 'subtask' if is_subtask else 'changed'
            if ptype == 'subtask':
                # 占位存**原始 part**，不是已经映射好的 dict——状态在 flatten
                # 时（_flatten_scope）才现查现填，这样子代理跑完之后，父级
                # 下次 flatten 自然会拿到最新状态，不需要反向去更新这一条。
                if pid not in scope['parts_by_id']:
                    scope['part_order'].append(pid)
                scope['parts_by_id'][pid] = {'_subtask_part': part}
                _discover_subtask(state, scope, scope_sid, is_subtask, part)
                return 'subtask'
    elif etype == 'session.idle':
        if is_subtask:
            if scope['status'] == 'running':
                scope['status'] = 'completed'
            return 'subtask'
        return 'idle'
    return None


def _resolve_scope(state, ev_sid, opencode_session_id):
    """Which accumulator does this event belong to: the top-level `state`
    itself, a known subtask's nested scope, or neither (unrelated session,
    ignored — matches the pre-subtask behavior exactly)."""
    if not ev_sid or ev_sid == opencode_session_id:
        return state, False
    sub = state['subtasks'].get(ev_sid)
    if sub is not None:
        return sub, True
    return None, False


def _scope_owning_message(state, message_id, opencode_session_id):
    """Fallback scope resolution for message.part.updated events whose part's
    own `sessionID` can't be trusted (subtask-typed parts overload it — see
    apply_event's caller comment). Finds the scope by which known
    accumulator (top-level, or a tracked subtask) already has `message_id`
    in its own `assistant_msg_ids`, and also returns that scope's own
    identifying sessionID (needed by `_discover_subtask`'s `parent_id`
    bookkeeping when the owning scope is itself a subtask)."""
    if not message_id:
        return None, None, False
    if message_id in state['assistant_msg_ids']:
        return opencode_session_id, state, False
    for sid, sub in state.get('subtasks', {}).items():
        if message_id in sub['assistant_msg_ids']:
            return sid, sub, True
    return None, None, False


def _discover_subtask(state, current_scope, current_sid, current_is_subtask, part):
    """A `subtask` part was seen inside `current_scope`'s own messages —
    register its child sessionID for tracking, unless we're already at the
    depth cap. `current_scope`'s own depth (0 for the top-level) determines
    the new entry's depth."""
    sid = part.get('sessionID')
    if not sid or sid in state['subtasks']:
        return
    parent_depth = current_scope['depth'] if current_is_subtask else 0
    if parent_depth + 1 > MAX_SUBTASK_DEPTH:
        return
    parent_id = current_sid if current_is_subtask else None
    state['subtasks'][sid] = _new_subtask_scope(
        parent_id, parent_depth + 1, part.get('agent'), part.get('description'))


def _status_snapshot(state):
    """{子代理 sessionID: 当前状态}，供 flatten 时给 subtask_use 占位填最新值。"""
    return {sid: sub['status'] for sid, sub in state.get('subtasks', {}).items()}


def _flatten_scope(scope, subtask_status):
    """把一个累加器（顶层 state 本身，或某个子代理的嵌套 scope）的
    part_order/parts_by_id 展开成持久化内容。text 分支保留既有的"空文本
    过滤"（build_content 原有行为，见 Task 2 的行为不变约束）；subtask 分支
    每次都用当前的 `subtask_status` 重新调 map_part，状态不会定型不变。"""
    from utils.opencode_parts import map_part
    content = []
    for pid in scope['part_order']:
        p = scope['parts_by_id'].get(pid)
        if not p:
            continue
        if '_subtask_part' in p:
            mapped = map_part(p['_subtask_part'], subtask_status=subtask_status)
            if mapped:
                content.append(mapped)
            continue
        if p['type'] == 'text':
            if (p.get('text') or '').strip():
                content.append({'type': 'text', 'text': p['text']})
        elif p['type'] == 'tool_use':
            content.append(p)
    return content


def build_content(state):
    """Build the persisted assistant content (arrival order). Delegates to
    _flatten_scope so the top-level's own subtask_use stubs (if any) get the
    same "status re-derived fresh at flatten time" treatment as everything
    else — see _flatten_scope's docstring."""
    return _flatten_scope(state, _status_snapshot(state))


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


def persist_subtasks(root_session_id, state):
    """把 state['subtasks'] 里每一个已知子代理的当前内容落库：
    ai_chat_subtasks 一行（首次插入，之后只更新 status/error/completed_at），
    ai_chat_subtask_messages 一行持续更新的 assistant 消息（跟 persist_turn
    对顶层的处理方式一致——单行按 id 幂等 upsert，不是每次都插新行）。
    子代理自己的 content 里如果又有更深一层的占位，同样交给 _flatten_scope
    用当前状态现查现填。Best-effort；不抛异常，不打断调用方的事件循环。"""
    snapshot = _status_snapshot(state)
    for sid, sub in state.get('subtasks', {}).items():
        try:
            content = _flatten_scope(sub, snapshot)
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO ai_chat_subtasks "
                    "  (id, root_session_id, parent_subtask_id, agent, description, status, "
                    "   error_message, completed_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, "
                    "        CASE WHEN %s IN ('completed','failed') THEN now() ELSE NULL END) "
                    "ON CONFLICT (id) DO UPDATE SET "
                    "  status = EXCLUDED.status, error_message = EXCLUDED.error_message, "
                    "  completed_at = COALESCE(ai_chat_subtasks.completed_at, EXCLUDED.completed_at)",
                    (sid, root_session_id, sub['parent_id'], sub['agent'],
                     sub['description'], sub['status'], sub.get('error'), sub['status']),
                )
                if content:
                    cur.execute(
                        "INSERT INTO ai_chat_subtask_messages (id, subtask_id, role, content) "
                        "VALUES (%s, %s, 'assistant', %s) "
                        "ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content",
                        (f'{sid}:a', sid, json.dumps(content)),
                    )
                conn.commit()
        except Exception as e:
            logger.warning('persist_subtasks DB error root=%s subtask=%s: %s',
                           root_session_id, sid, e)


def _run_listener(sid, opencode_session_id, event_source):
    """Consume events, persisting the assistant message on session.idle and
    incrementally (time-debounced) while the turn streams, so switching sessions
    mid-stream recovers the partial answer. Returns after the turn's idle (or
    when the source is exhausted/raises).

    One listener per turn, not per session: on idle we persist and RETURN,
    releasing the OpenCode /event subscription instead of holding it open for up
    to 30 min waiting for the next turn. OpenCode adds an internal event listener
    per open /event connection (Node EventEmitter, default cap 10); a long-lived
    listener per idle session piled these up until OpenCode hit "max listeners
    exceeded" and dropped events. The next turn re-creates a fresh listener via
    ensure_listener(). The pre-idle read timeout (INACTIVITY_TIMEOUT) still
    tolerates long silent tool calls *within* a turn. `event_source` is
    injectable for tests."""
    state = new_state()
    last_persist = time.monotonic()
    for evt in event_source:
        sig = apply_event(state, evt, opencode_session_id)
        if sig == 'subtask':
            # 子代理相关信号可能只改了它自己的内容/状态，也可能是顶层第一次
            # 发现委托——两种情况都要把顶层重新 flatten 一遍：占位气泡的
            # status 现查现填（_flatten_scope），子代理状态变了但顶层没有
            # "新事件"时，只有重新持久化顶层才会带出刷新后的状态。
            persist_turn(sid, state)
            persist_subtasks(sid, state)
            continue
        if sig == 'idle':
            persist_turn(sid, state)
            logger.debug('persist listener idle->persisted+exit session=%s parts=%d',
                         sid, len(state.get('part_order', [])))
            try:
                from utils.memory import extract_from_turn
                extract_from_turn(sid, state)
            except Exception as e:
                logger.warning('memory extract_from_turn failed session=%s: %s', sid, e)
            return
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
