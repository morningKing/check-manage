"""AI 会话撤销/重做核心（undo-redo spec §2.2）。

三层收敛在一处：①锚点解析/文件能力（纯函数）②stash/状态 SQL 助手
③撤销/重做/预览编排。路由只做守卫与 JSON 映射。

关键实证（spec §0.3）：E2 必须传 user 消息 id；E6 必须校验响应
revert.messageID；E10 快照 7 天 GC → stale 降级 rebuild。
"""
import json
import logging
import os
import time

import requests

from db import get_db
from utils.runtime import get_runtime

logger = logging.getLogger(__name__)


class UndoError(Exception):
    """撤销/重做失败：路由层映射为结构化错误响应（报错＝什么都没改）。"""

    def __init__(self, code: str, message: str, *, status: int = 409,
                 retryable: bool = False, operation: str = 'undo'):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        self.operation = operation


def _files_after(oc_messages, start_idx: int) -> list:
    """oc_messages[start_idx:] 里所有 patch part 的文件并集（保序去重）。"""
    files: list = []
    for m in (oc_messages or [])[max(start_idx, 0):]:
        for p in (m.get('parts') or []):
            if (p or {}).get('type') != 'patch':
                continue
            for f in p.get('files') or []:
                name = f.get('file') if isinstance(f, dict) else f
                if name and name not in files:
                    files.append(name)
    return files


def _oc_index(oc_messages, mid: str) -> int:
    return next(i for i, m in enumerate(oc_messages)
                if ((m.get('info') or {}).get('id')) == mid)


def resolve_undo_target(rows, target_seq: int, oc_messages) -> dict:
    """锚点解析（spec §2.2a）。

    rows：平台行 [{'id','role','seq'}]（seq 升序，全会话）；
    oc_messages：OpenCode REST 消息（client.get_messages 原始返回）。
    平台 assistant 行 id = 该回合第一条 OC assistant 消息 id
    （chat_persist.turn_msg_id 落库），是天然锚点；用户行 id 平台自生成、
    无 OC 对应——所以 OC 目标必须经锚「吸附」出其后第一条 OC user 消息。
    """
    oc_messages = oc_messages or []
    oc_ids: set = set()
    first_oc_user = None
    for m in oc_messages:
        info = (m or {}).get('info') or {}
        if info.get('id'):
            oc_ids.add(info['id'])
        if first_oc_user is None and info.get('role') == 'user' and info.get('id'):
            first_oc_user = info['id']
    anchor = None
    has_any_assistant_before = False
    for r in rows or []:
        if r['seq'] >= target_seq:
            break
        if r['role'] == 'assistant':
            has_any_assistant_before = True
            if r['id'] in oc_ids:
                anchor = r['id']          # 最近一条「活着」的锚
    if anchor is None:
        if has_any_assistant_before and oc_messages:
            # 锚全部不在 OC 列表但 OC 有会话：上下文在别的 OC 会话里
            #（_recover_session_and_resend 换过）→ 重建式降级
            return {'mode': 'rebuild', 'oc_user_message_id': None,
                    'anchor_row_id': None, 'files': [], 'reason': 'anchor-missing'}
        # 无锚：目标即会话首条 / 目标前全是平台自造行 / OC 空（失败轮）
        return {'mode': 'native', 'oc_user_message_id': first_oc_user,
                'anchor_row_id': None,
                'files': _files_after(oc_messages, 0) if first_oc_user else []}
    idx = _oc_index(oc_messages, anchor)
    oc_target = None
    for m in oc_messages[idx + 1:]:
        info = (m.get('info') or {})
        if info.get('role') == 'user' and info.get('id'):
            oc_target = info['id']
            break
    if oc_target is None:
        # 锚存在但锚与目标之间的轮从未到达 OC（dispatch 失败等）→ 纯平台
        # 撤销：OC 不动、只搬平台行、无文件还原（spec §2.2a 规则⑤）
        return {'mode': 'native', 'oc_user_message_id': None,
                'anchor_row_id': anchor, 'files': []}
    t_idx = _oc_index(oc_messages, oc_target)
    return {'mode': 'native', 'oc_user_message_id': oc_target,
            'anchor_row_id': anchor,
            'files': _files_after(oc_messages, t_idx + 1)}


def file_capability(project_info, workspace_path: str, created_ms: int,
                    files, *, now_ms=None, max_age_days=None) -> str:
    """文件还原能力分级（spec E8/E9/E10）：'full' | 'stale' | 'none'。"""
    if not files:
        return 'none'
    info = project_info or {}
    if info.get('vcs') != 'git':
        return 'none'
    wt = info.get('worktree')
    if not wt:
        return 'none'
    try:
        same = os.path.normcase(os.path.abspath(wt)) == \
            os.path.normcase(os.path.abspath(workspace_path))
    except Exception:
        same = False
    if not same:
        return 'none'
    if max_age_days is None:
        from config import AI_UNDO_FILE_SNAPSHOT_MAX_AGE_DAYS
        max_age_days = AI_UNDO_FILE_SNAPSHOT_MAX_AGE_DAYS
    now = now_ms if now_ms is not None else time.time() * 1000
    if created_ms and (now - created_ms) > max_age_days * 86400 * 1000:
        return 'stale'
    return 'full'


# ================================================================ stash / 状态
def stash_rows(conn, sid: str, from_seq: int) -> int:
    """把 seq >= from_seq 的消息搬进 stash 并从主表删除（rating 随行携带，
    feedback 行随消息 CASCADE 删除）。返回删除条数。"""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ai_chat_message_stash "
        "  (message_id, session_id, role, content, meta, created_at, seq, rating) "
        "SELECT m.id, m.session_id, m.role, m.content, m.meta, m.created_at, m.seq, f.rating "
        "FROM ai_chat_messages m "
        "LEFT JOIN ai_chat_message_feedback f ON f.message_id = m.id "
        "WHERE m.session_id = %s AND m.seq >= %s", (sid, from_seq))
    cur.execute(
        "DELETE FROM ai_chat_messages WHERE session_id = %s AND seq >= %s",
        (sid, from_seq))
    return cur.rowcount


def restore_stash(conn, sid: str) -> int:
    """stash 整体搬回主表（seq/created_at 保真——旧值必小于当前序列值，
    新消息 nextval 不会冲突）+ feedback 回插 + 清 stash。返回恢复条数。"""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ai_chat_messages (id, session_id, role, content, meta, "
        "  created_at, seq) "
        "SELECT message_id, session_id, role, content, meta, created_at, seq "
        "FROM ai_chat_message_stash WHERE session_id = %s", (sid,))
    cur.execute(
        "INSERT INTO ai_chat_message_feedback (message_id, session_id, rating) "
        "SELECT message_id, session_id, rating FROM ai_chat_message_stash "
        "WHERE session_id = %s AND rating IS NOT NULL "
        "ON CONFLICT (message_id) DO NOTHING", (sid,))
    cur.execute("DELETE FROM ai_chat_message_stash WHERE session_id = %s", (sid,))
    return cur.rowcount


def get_revert_state(conn, sid):
    cur = conn.cursor()
    cur.execute(
        "SELECT mode, opencode_session_id, oc_message_id, from_seq, "
        "       message_count, file_count, file_names, created_at, "
        "       prev_opencode_session_id "
        "FROM ai_chat_session_reverts WHERE session_id = %s", (sid,))
    r = cur.fetchone()
    if not r:
        return None
    return {'mode': r[0], 'opencodeSessionId': r[1],
            'prevOpencodeSessionId': r[8], 'ocMessageId': r[2],
            'fromSeq': r[3], 'messageCount': r[4], 'fileCount': r[5],
            'fileNames': r[6] or [],
            'createdAt': r[7].isoformat() if r[7] else None}


def upsert_revert_state(conn, sid, *, mode, opencode_session_id, oc_message_id,
                        from_seq, message_count, file_count=0, file_names=None,
                        prev_opencode_session_id=None):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ai_chat_session_reverts "
        "  (session_id, mode, opencode_session_id, oc_message_id, from_seq, "
        "   message_count, file_count, file_names, prev_opencode_session_id, "
        "   created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW()) "
        "ON CONFLICT (session_id) DO UPDATE SET "
        "  mode = EXCLUDED.mode, "
        "  opencode_session_id = EXCLUDED.opencode_session_id, "
        "  oc_message_id = EXCLUDED.oc_message_id, from_seq = EXCLUDED.from_seq, "
        "  message_count = EXCLUDED.message_count, "
        "  file_count = EXCLUDED.file_count, file_names = EXCLUDED.file_names, "
        "  prev_opencode_session_id = EXCLUDED.prev_opencode_session_id, "
        "  updated_at = NOW()",
        (sid, mode, opencode_session_id, oc_message_id, from_seq,
         message_count, file_count, json.dumps(file_names or []),
         prev_opencode_session_id))


def commit_revert(conn, sid) -> int:
    """提交撤销（不可重做）：清 stash + 状态行 + 被撤内容引用的孤儿子任务行。
    返回 stash 清理条数（0 = 无挂起撤销）。"""
    cur = conn.cursor()
    cur.execute(
        "SELECT content FROM ai_chat_message_stash WHERE session_id = %s", (sid,))
    subtask_ids = set()
    for (content,) in cur.fetchall():
        if not isinstance(content, list):
            continue
        for p in content:
            if isinstance(p, dict) and p.get('type') == 'subtask_use' \
                    and p.get('subtaskId'):
                subtask_ids.add(p['subtaskId'])
    if subtask_ids:
        cur.execute(
            "DELETE FROM ai_chat_subtasks WHERE root_session_id = %s "
            "AND id = ANY(%s)", (sid, sorted(subtask_ids)))
    cur.execute("DELETE FROM ai_chat_message_stash WHERE session_id = %s", (sid,))
    n = cur.rowcount
    cur.execute("DELETE FROM ai_chat_session_reverts WHERE session_id = %s", (sid,))
    return n


def clear_context_replay(conn, sid):
    cur = conn.cursor()
    cur.execute(
        "UPDATE ai_chat_sessions SET context_replay_pending = FALSE "
        "WHERE id = %s", (sid,))


# ================================================================ 编排器
def _fetch_oc_messages(client, oc_sid, workspace_path):
    if not oc_sid:
        return []
    try:
        return client.get_messages(oc_sid, directory=workspace_path) or []
    except requests.RequestException as e:
        raise UndoError('OPENCODE_UNAVAILABLE', 'AI 运行时暂时不可用，请稍后重试',
                        status=502, retryable=True, operation='undo') from e


def _load_undo_context_or_raise(sid: str, msg_id: str) -> dict:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT opencode_session_id, status, workspace_path "
            "FROM ai_chat_sessions WHERE id = %s", (sid,))
        row = cur.fetchone()
        if row is None:
            raise UndoError('SESSION_NOT_FOUND', 'session not found', status=404,
                            operation='undo')
        oc_sid, status, workspace_path = row
        if status != 'active':
            raise UndoError('INVALID_STATUS', '该状态会话不可撤销', operation='undo')
        cur.execute(
            "SELECT seq FROM ai_chat_messages WHERE id = %s AND session_id = %s",
            (msg_id, sid))
        trow = cur.fetchone()
        if trow is None:
            raise UndoError('MESSAGE_NOT_FOUND', 'message not found', status=404,
                            operation='undo')
        target_seq = trow[0]
        cur.execute(
            "SELECT id, role, seq FROM ai_chat_messages "
            "WHERE session_id = %s ORDER BY seq ASC", (sid,))
        rows = [{'id': r[0], 'role': r[1], 'seq': r[2]} for r in cur.fetchall()]
    return {'oc_sid': oc_sid, 'workspace_path': workspace_path,
            'target_seq': target_seq, 'rows': rows,
            'message_count': sum(1 for r in rows if r['seq'] >= target_seq)}


def _finalize_plan(client, plan: dict, workspace_path: str, oc_messages) -> dict:
    """补文件能力分级并把 stale 促销为 rebuild（spec §2.2a(5)）。"""
    if plan['mode'] != 'native' or not plan['oc_user_message_id']:
        plan['fileCapability'] = 'none'
        return plan
    created_ms = 0
    for m in oc_messages:
        info = (m.get('info') or {})
        if info.get('id') == plan['oc_user_message_id']:
            created_ms = ((info.get('time') or {}).get('created')) or 0
            break
    try:
        project_info = client.get_project(directory=workspace_path)
    except requests.RequestException:
        project_info = None
    cap = file_capability(project_info, workspace_path, created_ms, plan['files'])
    plan['fileCapability'] = cap
    if cap == 'stale':
        plan['mode'] = 'rebuild'
        plan['reason'] = 'file-snapshot-stale'
    return plan


def preview_undo(sid: str, msg_id: str) -> dict:
    """撤销预览（只读，1 次 OC get_messages + 1 次 get_project）。"""
    ctx = _load_undo_context_or_raise(sid, msg_id)
    client = get_runtime().get_client()
    oc_messages = _fetch_oc_messages(client, ctx['oc_sid'], ctx['workspace_path'])
    plan = resolve_undo_target(ctx['rows'], ctx['target_seq'], oc_messages)
    plan = _finalize_plan(client, plan, ctx['workspace_path'], oc_messages)
    return {'messageCount': ctx['message_count'], 'files': plan['files'],
            'fileCapability': plan['fileCapability'], 'mode': plan['mode'],
            'reason': plan.get('reason')}


def _compensate_clear(client, oc_sid, workspace_path):
    """OC 已撤销但平台侧失败 → 反向补偿（尽力而为并留痕）。"""
    try:
        client.revert_clear(oc_sid, directory=workspace_path)
    except Exception:
        logger.error('revert 补偿失败 oc=%s（平台已回滚，OC 侧处于撤销态，'
                     '下次发送将被提交）', oc_sid, exc_info=True)


def _undo_native(sid, ctx, plan, client):
    from utils import chat_persist
    oc_reverted = False
    try:
        with get_db() as conn:
            conn.cursor().execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", (sid,))
            chat_persist.stop_listener(sid)   # 防监听器复活（clear_session 先例）
            stash_rows(conn, sid, ctx['target_seq'])
            if plan['oc_user_message_id']:
                try:
                    info = client.revert_stage(ctx['oc_sid'],
                                               plan['oc_user_message_id'],
                                               directory=ctx['workspace_path'])
                except requests.HTTPError as e:
                    if getattr(e, 'response', None) is not None \
                            and e.response.status_code == 409:
                        raise UndoError('SESSION_BUSY',
                                        '回合进行中，请先停止当前回复再撤销',
                                        operation='undo') from e
                    raise
                if ((info or {}).get('revert') or {}).get('messageID') \
                        != plan['oc_user_message_id']:
                    raise UndoError('REVERT_MISMATCH',
                                    '撤销点校验失败（OpenCode 未按请求回退）')
                oc_reverted = True
            upsert_revert_state(
                conn, sid, mode='native', opencode_session_id=ctx['oc_sid'],
                oc_message_id=plan['oc_user_message_id'],
                from_seq=ctx['target_seq'], message_count=ctx['message_count'],
                file_count=len(plan['files']), file_names=plan['files'])
    except Exception:
        if oc_reverted:
            _compensate_clear(client, ctx['oc_sid'], ctx['workspace_path'])
        raise
    with get_db() as conn:
        state = get_revert_state(conn, sid)
    logger.info('undo committed session=%s oc=%s mode=native rows=%d files=%d',
                sid, ctx['oc_sid'], ctx['message_count'], len(plan['files']))
    broadcast_revert(sid)
    return state


def _undo_rebuild(sid, ctx, plan, client):
    from utils import chat_persist
    new_oc = client.create_session(directory=ctx['workspace_path'],
                                   title='撤销重建')
    try:
        with get_db() as conn:
            conn.cursor().execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", (sid,))
            chat_persist.stop_listener(sid)
            stash_rows(conn, sid, ctx['target_seq'])
            conn.cursor().execute(
                "UPDATE ai_chat_sessions SET opencode_session_id = %s, "
                "context_replay_pending = TRUE WHERE id = %s", (new_oc, sid))
            upsert_revert_state(
                conn, sid, mode='rebuild', opencode_session_id=new_oc,
                oc_message_id=None, from_seq=ctx['target_seq'],
                message_count=ctx['message_count'], file_count=0, file_names=[],
                prev_opencode_session_id=ctx['oc_sid'])
    except Exception:
        try:
            client.delete_session(new_oc)
        except Exception:
            pass
        raise
    with get_db() as conn:
        state = get_revert_state(conn, sid)
    logger.info('undo committed session=%s oc=%s mode=rebuild rows=%d files=%d',
                sid, new_oc, ctx['message_count'], 0)
    broadcast_revert(sid)
    return state


def undo_from_message(sid: str, msg_id: str) -> dict:
    from utils import chat_persist
    if chat_persist.has_listener(sid):
        raise UndoError('SESSION_BUSY', '回合进行中，请先停止当前回复再撤销',
                        operation='undo')
    ctx = _load_undo_context_or_raise(sid, msg_id)
    client = get_runtime().get_client()
    oc_messages = _fetch_oc_messages(client, ctx['oc_sid'], ctx['workspace_path'])
    plan = resolve_undo_target(ctx['rows'], ctx['target_seq'], oc_messages)
    plan = _finalize_plan(client, plan, ctx['workspace_path'], oc_messages)
    if plan['mode'] == 'rebuild':
        state = _undo_rebuild(sid, ctx, plan, client)
    else:
        state = _undo_native(sid, ctx, plan, client)
    return {'deleted': ctx['message_count'], 'revert': state,
            'messages': _message_window(sid)}


def redo(sid: str) -> dict:
    from utils import chat_persist
    if chat_persist.has_listener(sid):
        raise UndoError('SESSION_BUSY', '回合进行中，请先停止当前回复再重做',
                        operation='redo')
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT opencode_session_id, status, workspace_path "
            "FROM ai_chat_sessions WHERE id = %s", (sid,))
        row = cur.fetchone()
        if row is None:
            raise UndoError('SESSION_NOT_FOUND', 'session not found', status=404,
                            operation='redo')
        oc_now, status, workspace_path = row
        if status != 'active':
            raise UndoError('INVALID_STATUS', '该状态会话不可重做', operation='redo')
        state = get_revert_state(conn, sid)
        if not state:
            raise UndoError('NOTHING_TO_REDO', '没有可重做的撤销', operation='redo')
        if state['opencodeSessionId'] != oc_now:
            # 会话被恢复/清空换过：旧 OC 上下文不可达，只收口不搬回（spec §2.2c）。
            # 收口必须独立事务：若与下面的 raise 同处一个 get_db() 块，
            # db.py 异常路径会把写入一并回滚——「撤销已收口」名存实亡，
            # 此后每次 redo 都重复失败。
            # 注：校验发生在切回（rebuild 分支的 UPDATE）之前——rebuild 状态行
            # 的 opencodeSessionId 是撤销时新建的空会话，与切回前 sessions 行
            # 一致，此处不会误触发。
            with get_db() as commit_conn:
                commit_revert(commit_conn, sid)
                clear_context_replay(commit_conn, sid)
            raise UndoError('REDO_STALE', '会话上下文已变化，无法重做（撤销已收口）',
                            operation='redo')
    # rebuild（spec §2.2d）：重做切回旧 OC 会话；切回后 sessions 行的
    # opencode_session_id 变为 prev，仅影响本事务之后的读取方。
    switch_back_prev = state['prevOpencodeSessionId'] \
        if state['mode'] == 'rebuild' and state.get('prevOpencodeSessionId') \
        else None
    client = get_runtime().get_client()
    try:
        with get_db() as conn:
            conn.cursor().execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", (sid,))
            if state['mode'] == 'native' and state['ocMessageId']:
                try:
                    client.revert_clear(state['opencodeSessionId'],
                                        directory=workspace_path)
                except requests.HTTPError as e:
                    if getattr(e, 'response', None) is not None \
                            and e.response.status_code == 409:
                        raise UndoError('SESSION_BUSY',
                                        '回合进行中，请先停止当前回复再重做',
                                        operation='redo') from e
                    raise
            chat_persist.stop_listener(sid)
            restore_stash(conn, sid)               # 先搬回 stash，再切回 OC 指针
            clear_context_replay(conn, sid)
            if switch_back_prev:
                conn.cursor().execute(
                    "UPDATE ai_chat_sessions SET opencode_session_id = %s, "
                    "context_replay_pending = FALSE WHERE id = %s",
                    (switch_back_prev, sid))
            conn.cursor().execute(
                "DELETE FROM ai_chat_session_reverts WHERE session_id = %s", (sid,))
    except Exception:
        # OC clear 已发出而 DB 回滚：平台仍处撤销态，再次 redo 幂等重试
        logger.error('redo 失败 session=%s（OC clear 可能已发出）', sid, exc_info=True)
        raise
    if switch_back_prev:
        # 切回已提交：撤销时新建的空会话尽力删除（与 _undo_rebuild 补偿同风格）
        try:
            client.delete_session(state['opencodeSessionId'])
        except Exception:
            pass
    logger.info('redo committed session=%s oc=%s mode=%s rows=%d files=%d',
                sid, switch_back_prev or state['opencodeSessionId'] or '-',
                state['mode'], state['messageCount'], state['fileCount'] or 0)
    broadcast_revert(sid)
    return {'deleted': state['messageCount'], 'messages': _message_window(sid)}


def commit_after_dispatch(sid: str) -> None:
    """提交挂起的撤销（新回合已被接受——OC 侧 prompt 开头自动 commit，E3，
    平台侧同步收口）。无撤销态时零开销（单 SELECT）。"""
    with get_db() as conn:
        state = get_revert_state(conn, sid)
        if not state:
            return
        commit_revert(conn, sid)
        clear_context_replay(conn, sid)
    logger.info('revert committed session=%s oc=%s mode=%s rows=%d files=%d',
                sid, state.get('opencodeSessionId') or '-',
                state.get('mode') or '-', state.get('messageCount') or 0,
                state.get('fileCount') or 0)
    broadcast_revert(sid)


def broadcast_revert(sid: str) -> None:
    """向该会话全部 SSE 连接广播撤销态（无连接时 no-op）。"""
    try:
        with get_db() as conn:
            state = get_revert_state(conn, sid)
        from utils.session_event_bus import broadcast
        broadcast(sid, 'session.revert', {'sessionId': sid, 'revert': state})
    except Exception:
        logger.warning('revert 广播失败 session=%s', sid, exc_info=True)


def _message_window(sid: str, limit: int = 200) -> list:
    """与 GET /messages 默认分支同形（seq DESC 取 limit 再反转）。"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT m.id, m.role, m.content, m.created_at, m.meta, m.seq, f.rating "
            "FROM ai_chat_messages m "
            "LEFT JOIN ai_chat_message_feedback f ON f.message_id = m.id "
            "WHERE m.session_id = %s ORDER BY m.seq DESC LIMIT %s", (sid, limit))
        rows = cur.fetchall()
    rows = list(reversed(rows))
    return [{'id': r[0], 'role': r[1], 'content': r[2],
             'createdAt': r[3].isoformat() if r[3] else None,
             'meta': r[4], 'seq': r[5], 'rating': r[6]} for r in rows]
