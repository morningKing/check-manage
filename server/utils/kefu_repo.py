"""客服实例 + 客服会话数据层。

客服会话复用 ai_chat_sessions 表，user_id 指向只读 bot 用户
（MCP 经 JOIN users ON user_id 自动只读钳制）。
"""
import json
import secrets
from pathlib import Path

from werkzeug.security import generate_password_hash

from db import get_db
from utils.kefu_guardrail import assemble_system_prompt
from utils.workspace import create_session_workspace, write_opencode_config
from utils.session_token import generate_token
from utils.opencode_client import OpenCodeClient
from config import (
    AI_WORKSPACE_ROOT, OPENCODE_BASE_URL, MCP_SERVER_URL,
    AI_SESSION_TTL_HOURS, OPENCODE_MODEL,
)

MCP_NAME = 'check-manage'
_BOT_USER_ID = 'kefu-bot'


def ensure_bot_user(role_slug: str = 'kefu-guest') -> str:
    """幂等创建/更新固定 bot 用户。口令为随机不可登录值。"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET role = EXCLUDED.role",
            (
                _BOT_USER_ID,
                _BOT_USER_ID,
                generate_password_hash(secrets.token_hex(16)),
                '智能客服',
                role_slug,
            ),
        )
    return _BOT_USER_ID


def _row_to_instance(r) -> dict:
    return {
        'id': r[0],
        'slug': r[1],
        'name': r[2],
        'agent': r[3],
        'model': r[4],
        'system_prompt': r[5],
        'welcome_message': r[6],
        'guided_questions': r[7],
        'branding': r[8],
        'bot_user_id': r[9],
        'enabled': r[10],
        'rate_limit': r[11],
        'panel_blocks': r[12],
    }


_COLS = (
    "id, slug, name, agent, model, system_prompt, welcome_message, "
    "guided_questions, branding, bot_user_id, enabled, rate_limit, panel_blocks"
)


def create_instance(payload: dict) -> dict:
    bot_id = ensure_bot_user()
    iid = 'kf_' + secrets.token_hex(6)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO kefu_instances "
            "(id, slug, name, agent, model, system_prompt, welcome_message, "
            " guided_questions, branding, bot_user_id, enabled, rate_limit, panel_blocks) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            f"RETURNING {_COLS}",
            (
                iid,
                payload['slug'],
                payload['name'],
                payload.get('agent') or None,
                payload.get('model') or None,
                payload.get('system_prompt') or None,
                payload.get('welcome_message') or None,
                json.dumps(payload.get('guided_questions') or []),
                json.dumps(payload.get('branding') or {}),
                bot_id,
                payload.get('enabled', True),
                json.dumps(payload.get('rate_limit') or {}),
                json.dumps(payload.get('panel_blocks') or []),
            ),
        )
        return _row_to_instance(cur.fetchone())


def list_instances() -> list:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT {_COLS} FROM kefu_instances ORDER BY created_at DESC")
        return [_row_to_instance(r) for r in cur.fetchall()]


def get_instance(instance_id) -> dict | None:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT {_COLS} FROM kefu_instances WHERE id=%s", (instance_id,))
        r = cur.fetchone()
        return _row_to_instance(r) if r else None


def get_instance_by_slug(slug) -> dict | None:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT {_COLS} FROM kefu_instances WHERE slug=%s", (slug,))
        r = cur.fetchone()
        return _row_to_instance(r) if r else None


def update_instance(instance_id, payload) -> dict | None:
    fields, params = [], []
    for col in ('slug', 'name', 'agent', 'model', 'system_prompt',
                'welcome_message'):
        if col in payload:
            fields.append(f"{col}=%s")
            params.append(payload[col] if payload[col] != '' else None)
    for col in ('guided_questions', 'branding', 'rate_limit', 'panel_blocks'):
        if col in payload:
            fields.append(f"{col}=%s")
            params.append(json.dumps(payload[col]))
    if 'enabled' in payload:
        fields.append("enabled=%s")
        params.append(bool(payload['enabled']))
    if not fields:
        return get_instance(instance_id)
    fields.append("updated_at=now()")
    params.append(instance_id)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE kefu_instances SET {', '.join(fields)} WHERE id=%s "
            f"RETURNING {_COLS}",
            tuple(params),
        )
        r = cur.fetchone()
        return _row_to_instance(r) if r else None


def delete_instance(instance_id) -> bool:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM kefu_instances WHERE id=%s", (instance_id,))
        return cur.rowcount > 0


def _inject_system_prompt(workspace_path: str, instance: dict) -> None:
    """把护栏+实例人设追加进工作区 AGENTS.md，OpenCode 作为项目上下文读取。"""
    block = assemble_system_prompt(instance.get('system_prompt'))
    agents_md = Path(workspace_path) / 'AGENTS.md'
    with open(agents_md, 'a', encoding='utf-8') as f:
        f.write(f"\n\n## 客服角色与边界\n\n{block}\n")


def _new_session_id() -> str:
    return 'sess_' + secrets.token_hex(6)


def create_kefu_session(instance: dict, visitor_id: str) -> dict:
    """建工作区 + 注入护栏 + 生成 token + 写 opencode.json + 创建 OpenCode session + 插入 DB 行。

    Returns {'id': session_id, 'title': '客服会话'}.
    Re-asserts kefu-bot's role on every session creation so a manual DB edit
    cannot silently widen the bot's access (SPOT: single point of truth).
    """
    ensure_bot_user()  # idempotent — ON CONFLICT DO UPDATE re-pins role to kefu-guest
    bot_user_id = instance['bot_user_id']
    session_id = _new_session_id()
    workspace_path = create_session_workspace(AI_WORKSPACE_ROOT, bot_user_id, session_id)
    _inject_system_prompt(workspace_path, instance)

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ai_chat_sessions "
            "(id, user_id, title, workspace_path, session_token, token_expires_at, "
            " status, kefu_instance_id, visitor_id) "
            "VALUES (%s,%s,%s,%s,%s, NOW() + INTERVAL '1 hour', 'active', %s, %s)",
            (
                session_id,
                bot_user_id,
                '客服会话',
                workspace_path,
                '_pending_',
                instance['id'],
                visitor_id,
            ),
        )

    token = generate_token(session_id, AI_SESSION_TTL_HOURS)
    mcp_url = f"{MCP_SERVER_URL}/mcp?token={token}"
    write_opencode_config(
        workspace_path,
        mcp_name=MCP_NAME,
        mcp_url=mcp_url,
        model=(instance.get('model') or OPENCODE_MODEL),
    )

    client = OpenCodeClient(OPENCODE_BASE_URL)
    oc_sid = client.create_session(directory=workspace_path, title='客服会话')

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ai_chat_sessions SET opencode_session_id=%s WHERE id=%s",
            (oc_sid, session_id),
        )

    return {'id': session_id, 'title': '客服会话'}


def load_kefu_session(session_id: str, visitor_id: str):
    """返回 (id, user_id, opencode_session_id, status, workspace_path, kefu_instance_id, human_takeover)
    或 None（session 不存在或 visitor 不匹配）。"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, user_id, opencode_session_id, status, workspace_path, "
            "kefu_instance_id, human_takeover FROM ai_chat_sessions "
            "WHERE id=%s AND visitor_id=%s AND kefu_instance_id IS NOT NULL",
            (session_id, visitor_id),
        )
        return cur.fetchone()


# ---- FAQ (热门问题) ----
_FAQ_COLS = ("id, instance_id, question, answer, category, "
             "sort_order, click_count, enabled")


def _row_to_faq(r) -> dict:
    return {
        'id': r[0], 'instance_id': r[1], 'question': r[2], 'answer': r[3],
        'category': r[4], 'sort_order': r[5], 'click_count': r[6], 'enabled': r[7],
    }


def create_faq(instance_id: str, payload: dict) -> dict:
    fid = 'faq_' + secrets.token_hex(6)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO kefu_faq_items "
            "(id, instance_id, question, answer, category, sort_order, enabled) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) "
            f"RETURNING {_FAQ_COLS}",
            (fid, instance_id, payload['question'], payload['answer'],
             payload.get('category') or None, int(payload.get('sort_order') or 0),
             payload.get('enabled', True)),
        )
        return _row_to_faq(cur.fetchone())


def list_faq_admin(instance_id: str) -> list:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT {_FAQ_COLS} FROM kefu_faq_items WHERE instance_id=%s "
            "ORDER BY sort_order ASC, created_at ASC", (instance_id,))
        return [_row_to_faq(r) for r in cur.fetchall()]


def list_faq_public(instance_id: str) -> list:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, question, answer, category FROM kefu_faq_items "
            "WHERE instance_id=%s AND enabled=true "
            "ORDER BY sort_order ASC, created_at ASC", (instance_id,))
        return [{'id': r[0], 'question': r[1], 'answer': r[2], 'category': r[3]}
                for r in cur.fetchall()]


def get_faq(faq_id: str):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT {_FAQ_COLS} FROM kefu_faq_items WHERE id=%s", (faq_id,))
        r = cur.fetchone()
        return _row_to_faq(r) if r else None


def update_faq(faq_id: str, payload: dict):
    fields, params = [], []
    for col in ('question', 'answer', 'category', 'sort_order', 'enabled'):
        if col in payload:
            fields.append(f"{col}=%s")
            val = payload[col]
            if col == 'category' and val == '':
                val = None
            params.append(val)
    if not fields:
        return get_faq(faq_id)
    fields.append("updated_at=now()")
    params.append(faq_id)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE kefu_faq_items SET {', '.join(fields)} WHERE id=%s "
                    f"RETURNING {_FAQ_COLS}", tuple(params))
        r = cur.fetchone()
        return _row_to_faq(r) if r else None


def delete_faq(faq_id: str) -> bool:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM kefu_faq_items WHERE id=%s", (faq_id,))
        return cur.rowcount > 0


def reorder_faq(instance_id: str, id_list: list) -> None:
    with get_db() as conn:
        cur = conn.cursor()
        for idx, fid in enumerate(id_list):
            cur.execute(
                "UPDATE kefu_faq_items SET sort_order=%s, updated_at=now() "
                "WHERE id=%s AND instance_id=%s",
                (idx, fid, instance_id))


def increment_faq_click(instance_id: str, faq_id: str) -> bool:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE kefu_faq_items SET click_count = click_count + 1 "
            "WHERE id=%s AND instance_id=%s AND enabled=true",
            (faq_id, instance_id))
        return cur.rowcount > 0


def _msg_preview(content, n: int = 60) -> str:
    """从消息 content（parts 数组）提取纯文本预览。"""
    if not isinstance(content, list):
        return ''
    text = ''.join(p.get('text', '') for p in content
                   if isinstance(p, dict) and p.get('type') == 'text')
    return text[:n]


def _iso(ts):
    return ts.isoformat() if ts else None


def set_needs_human(session_id: str, value: bool = True) -> bool:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ai_chat_sessions SET needs_human=%s "
            "WHERE id=%s AND kefu_instance_id IS NOT NULL",
            (value, session_id))
        return cur.rowcount > 0


def get_kefu_session_admin(session_id: str):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, kefu_instance_id, visitor_id, needs_human, human_takeover, "
            "human_agent_id, status, opencode_session_id, workspace_path "
            "FROM ai_chat_sessions WHERE id=%s AND kefu_instance_id IS NOT NULL",
            (session_id,))
        r = cur.fetchone()
    if not r:
        return None
    return {'id': r[0], 'kefu_instance_id': r[1], 'visitor_id': r[2],
            'needs_human': r[3], 'human_takeover': r[4], 'human_agent_id': r[5],
            'status': r[6], 'opencode_session_id': r[7], 'workspace_path': r[8]}


def takeover_session(session_id: str, agent_id: str) -> bool:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ai_chat_sessions SET human_takeover=true, human_agent_id=%s, "
            "needs_human=false WHERE id=%s AND kefu_instance_id IS NOT NULL",
            (agent_id, session_id))
        return cur.rowcount > 0


def release_session(session_id: str) -> bool:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ai_chat_sessions SET human_takeover=false, human_agent_id=NULL "
            "WHERE id=%s AND kefu_instance_id IS NOT NULL",
            (session_id,))
        return cur.rowcount > 0


def insert_human_message(session_id: str, text: str, agent_id: str) -> str:
    msg_id = 'msg_' + secrets.token_hex(6)
    content = json.dumps([{'type': 'text', 'text': text}])
    meta = json.dumps({'author': 'human', 'agent_user_id': agent_id})
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ai_chat_messages (id, session_id, role, content, meta) "
            "VALUES (%s,%s,'assistant',%s,%s)",
            (msg_id, session_id, content, meta))
    return msg_id


def list_kefu_sessions_admin(instance_id=None, needs_human=None,
                             takeover=None, status='active') -> list:
    clauses = ["kefu_instance_id IS NOT NULL"]
    params = []
    if instance_id:
        clauses.append("kefu_instance_id=%s"); params.append(instance_id)
    if needs_human is not None:
        clauses.append("needs_human=%s"); params.append(needs_human)
    if takeover is not None:
        clauses.append("human_takeover=%s"); params.append(takeover)
    if status:
        clauses.append("status=%s"); params.append(status)
    where = " AND ".join(clauses)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, kefu_instance_id, visitor_id, needs_human, human_takeover, "
            "human_agent_id, status, created_at, last_active_at, "
            "(SELECT content FROM ai_chat_messages m WHERE m.session_id=s.id "
            " ORDER BY created_at DESC LIMIT 1) "
            "FROM ai_chat_sessions s WHERE " + where +
            " ORDER BY last_active_at DESC NULLS LAST",
            tuple(params))
        rows = cur.fetchall()
    return [{'id': r[0], 'kefu_instance_id': r[1], 'visitor_id': r[2],
             'needs_human': r[3], 'human_takeover': r[4], 'human_agent_id': r[5],
             'status': r[6], 'created_at': _iso(r[7]), 'last_active_at': _iso(r[8]),
             'last_message': _msg_preview(r[9])} for r in rows]


def get_messages(session_id: str) -> list:
    """管理员视角取会话全部消息（含 meta，用于区分人工/AI）。"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, role, content, meta, created_at FROM ai_chat_messages "
            "WHERE session_id=%s ORDER BY created_at ASC", (session_id,))
        rows = cur.fetchall()
    return [{'id': r[0], 'role': r[1], 'content': r[2], 'meta': r[3],
             'createdAt': _iso(r[4])} for r in rows]
