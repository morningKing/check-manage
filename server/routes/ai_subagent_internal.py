"""子代理会话复用——OC 插件契约端点（NOT for browsers）。

OpenCode 插件 baize-subagent-reuse.js（由 utils.subagent_reuse_plugin 随启动
部署）在 task 工具执行前后回调这里：

- GET  /reuse?session=<父OC会话id>&agent=<subagent_type>
    返回 {enabled, taskId}。enabled 判定：该父会话属于某个批任务、且批级
    subagent_reuse 配置包含该 agent。taskId 为当前钉住的子会话 id
    （(root_session_id, agent) 唯一），无则 null（首次委派，由 OC 新建）。
- POST /pins  body {session, agent, taskId}
    把 OC 新建/续跑的子会话 id 登记为该 (root_session_id, agent) 的钉住值。
    未启用时静默 no-op（返回 {enabled: false}）。

鉴权：X-Internal-Token（与 ai_memory_internal 同一把 MCP_INTERNAL_TOKEN）。
"""
import secrets
import threading
import time

from flask import Blueprint, request, jsonify

from config import MCP_INTERNAL_TOKEN
from db import get_db

ai_subagent_internal_bp = Blueprint('ai_subagent_internal', __name__,
                                    url_prefix='/ai/subagent-internal')

# 复用竞态修复（复核 e2e 实测）：委派 after 回调登记 pin 时依赖
# ai_chat_subtasks 行已被周期持久化——第二次委派可能先于持久化发生，
# resolve-agent 查不到行 → pin 缺失 → 复用落空。改为 before 钩子登记
# 意图（callID → root+agent，进程内存、10 分钟 TTL），after 直接按
# callID 取 agent 写 pin，不再依赖持久化时序。
_INTENT: dict = {}
_INTENT_LOCK = threading.Lock()
_INTENT_TTL_SEC = 600


def _intent_remember(call_id: str, root_session_id: str, agent: str):
    with _INTENT_LOCK:
        _INTENT[call_id] = (root_session_id, agent, time.time())
        stale = [k for k, (_, _, ts) in _INTENT.items()
                 if time.time() - ts > _INTENT_TTL_SEC]
        for k in stale:
            _INTENT.pop(k, None)


def _intent_take(call_id: str):
    with _INTENT_LOCK:
        return _INTENT.pop(call_id, None)


def _authorized():
    token = request.headers.get('X-Internal-Token', '')
    return bool(MCP_INTERNAL_TOKEN) and token == MCP_INTERNAL_TOKEN


def _resolve_root(session_oc_id: str):
    """父 OC 会话 id → 平台 ai_chat_sessions 行 (id, batch_id) 或 None。"""
    if not session_oc_id:
        return None
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, batch_id FROM ai_chat_sessions "
                "WHERE opencode_session_id = %s "
                "ORDER BY created_at DESC LIMIT 1",
                (session_oc_id,),
            )
            return cur.fetchone()


def _reuse_agents(batch_id) -> list:
    if not batch_id:
        return []
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT subagent_reuse FROM ai_chat_batches WHERE id = %s",
                        (batch_id,))
            row = cur.fetchone()
    agents = row[0] if row else None
    return [a for a in (agents or []) if a]


@ai_subagent_internal_bp.get('/reuse')
def lookup_reuse():
    if not _authorized():
        return jsonify({'error': 'forbidden'}), 403
    session_oc_id = (request.args.get('session') or '').strip()
    agent = (request.args.get('agent') or '').strip()
    row = _resolve_root(session_oc_id)
    if not row or not agent:
        return jsonify({'enabled': False, 'taskId': None})
    root_session_id, batch_id = row[0], row[1]
    if agent not in _reuse_agents(batch_id):
        return jsonify({'enabled': False, 'taskId': None})
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT task_id FROM ai_subagent_pins "
                "WHERE root_session_id = %s AND agent = %s",
                (root_session_id, agent),
            )
            hit = cur.fetchone()
    call_id = (request.args.get('callId') or '').strip()
    if call_id:
        _intent_remember(call_id, root_session_id, agent)
    return jsonify({'enabled': True, 'taskId': hit[0] if hit else None})


@ai_subagent_internal_bp.get('/resolve-agent')
def resolve_agent():
    """tool.execute.after 回调用：工具输出只知道 taskId（子会话 id），agent
    名由平台反查（ai_chat_subtasks.id 即子会话 id，进度落库时已带 agent）。"""
    if not _authorized():
        return jsonify({'error': 'forbidden'}), 403
    session_oc_id = (request.args.get('session') or '').strip()
    task_id = (request.args.get('taskId') or '').strip()
    row = _resolve_root(session_oc_id)
    if not row or not task_id:
        return jsonify({'enabled': False, 'agent': None})
    root_session_id, batch_id = row[0], row[1]
    agents = _reuse_agents(batch_id)
    if not agents:
        return jsonify({'enabled': False, 'agent': None})
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT agent FROM ai_chat_subtasks "
                "WHERE id = %s AND root_session_id = %s",
                (task_id, root_session_id),
            )
            hit = cur.fetchone()
    agent = hit[0] if hit and hit[0] else None
    return jsonify({'enabled': agent in agents if agent else False,
                    'agent': agent})


@ai_subagent_internal_bp.post('/pins')
def upsert_pin():
    if not _authorized():
        return jsonify({'error': 'forbidden'}), 403
    body = request.get_json(silent=True) or {}
    session_oc_id = (body.get('session') or '').strip()
    agent = (body.get('agent') or '').strip()[:200]
    task_id = (body.get('taskId') or '').strip()[:100]
    call_id = (body.get('callId') or '').strip()
    # callId 路径（复核竞态修复）：agent 来自 before 登记的 intent，
    # 不依赖 ai_chat_subtasks 行是否已持久化
    if call_id:
        intent = _intent_take(call_id)
        if intent:
            root_session_id, agent = intent[0], intent[1]
            if task_id:
                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO ai_subagent_pins "
                            "  (id, root_session_id, batch_id, agent, task_id) "
                            "VALUES (%s, %s, (SELECT batch_id FROM ai_chat_sessions "
                            "          WHERE id = %s), %s, %s) "
                            "ON CONFLICT (root_session_id, agent) DO UPDATE SET "
                            "  task_id = EXCLUDED.task_id, updated_at = NOW()",
                            ('spin_' + secrets.token_hex(8), root_session_id,
                             root_session_id, agent, task_id),
                        )
                    conn.commit()
                return jsonify({'enabled': True, 'pinned': True})
            return jsonify({'enabled': False, 'pinned': False})
    row = _resolve_root(session_oc_id)
    if not row or not agent or not task_id:
        return jsonify({'enabled': False, 'pinned': False})
    root_session_id, batch_id = row[0], row[1]
    if agent not in _reuse_agents(batch_id):
        return jsonify({'enabled': False, 'pinned': False})
    pin_id = 'spin_' + secrets.token_hex(6)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ai_subagent_pins
                    (id, root_session_id, batch_id, agent, task_id)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (root_session_id, agent) DO UPDATE SET
                    task_id = EXCLUDED.task_id,
                    batch_id = EXCLUDED.batch_id,
                    updated_at = NOW()
                """,
                (pin_id, root_session_id, batch_id, agent, task_id),
            )
        conn.commit()
    return jsonify({'enabled': True, 'pinned': True})
