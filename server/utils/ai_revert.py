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
        "       message_count, file_count, file_names, created_at "
        "FROM ai_chat_session_reverts WHERE session_id = %s", (sid,))
    r = cur.fetchone()
    if not r:
        return None
    return {'mode': r[0], 'opencodeSessionId': r[1], 'ocMessageId': r[2],
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
