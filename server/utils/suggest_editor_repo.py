"""智能写作（suggest-editor）数据访问层。

文档元数据 / 模板 / Yjs 二进制状态的唯一读写入口。routes/suggest_editor.py
（用户 REST + Node 内部端点）都只调这里，测试可以直接对 repo 打桩或打真库。

列名到前端驼峰的映射统一在序列化函数里做（_doc_row / _template_row），
SQL 里保持 PostgreSQL 的下划线原样。
"""
import json
from psycopg2.extras import RealDictCursor

from db import get_db


def _doc_row(row) -> dict:
    if not row:
        return {}
    return {
        "id": row["id"],
        "title": row["title"],
        "ownerUserId": row["owner_user_id"],
        "templateId": row["template_id"],
        "status": row["status"],
        "createdAt": row["created_at"].isoformat() if row.get("created_at") else None,
        "updatedAt": row["updated_at"].isoformat() if row.get("updated_at") else None,
        "lastOpenedAt": row["last_opened_at"].isoformat() if row.get("last_opened_at") else None,
    }


def _template_row(row) -> dict:
    if not row:
        return {}
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "contentHtml": row["content_html"],
        "isBuiltin": row["is_builtin"],
        "sortOrder": row["sort_order"],
        "updatedAt": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


# ------------------------------- 文档 -------------------------------


def list_documents(owner_user_id: str) -> list[dict]:
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, title, owner_user_id, template_id, status, created_at, updated_at, last_opened_at "
                "FROM se_documents WHERE owner_user_id = %s AND status = 'active' "
                "ORDER BY updated_at DESC",
                (owner_user_id,),
            )
            return [_doc_row(r) for r in cur.fetchall()]


def get_document(doc_id: str) -> dict | None:
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, title, owner_user_id, template_id, status, created_at, updated_at, last_opened_at "
                "FROM se_documents WHERE id = %s",
                (doc_id,),
            )
            row = cur.fetchone()
            return _doc_row(row) if row else None


def create_document(doc_id: str, title: str, owner_user_id: str, template_id: str | None) -> dict:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO se_documents (id, title, owner_user_id, template_id) VALUES (%s,%s,%s,%s)",
                (doc_id, title, owner_user_id, template_id),
            )
        conn.commit()
    return get_document(doc_id)


def rename_document(doc_id: str, title: str) -> dict | None:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE se_documents SET title = %s, updated_at = NOW() WHERE id = %s",
                (title, doc_id),
            )
        conn.commit()
    return get_document(doc_id)


def delete_document(doc_id: str) -> bool:
    """硬删：文档行 + Yjs 状态行一起删（agent 工作区目录由路由层负责清理）。"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM se_document_states WHERE document_id = %s", (doc_id,))
            cur.execute("DELETE FROM se_documents WHERE id = %s", (doc_id,))
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted


def touch_document(doc_id: str, field: str) -> None:
    """更新 last_opened_at（open）等时间戳列；field 只接受白名单值。"""
    if field not in ("last_opened_at",):
        raise ValueError(f"unexpected touch field: {field}")
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE se_documents SET {field} = NOW() WHERE id = %s", (doc_id,))
        conn.commit()


# --------------------------- Yjs 状态（内部） ---------------------------


def load_state(doc_id: str) -> dict:
    """返回 {'state': bytes|None, 'analyzedHashes': list}；文档不存在时 state 为 None。"""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT ds.state, ds.analyzed_hashes FROM se_document_states ds "
                "WHERE ds.document_id = %s",
                (doc_id,),
            )
            row = cur.fetchone()
            if not row:
                return {"state": None, "analyzedHashes": []}
            hashes = row["analyzed_hashes"]
            return {
                "state": bytes(row["state"]) if row["state"] is not None else None,
                "analyzedHashes": hashes if isinstance(hashes, list) else [],
            }


def save_state(doc_id: str, state: bytes, analyzed_hashes: list) -> bool:
    """upsert Yjs 状态；文档必须存在（返回 False = 文档没了，丢弃写入）。"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM se_documents WHERE id = %s", (doc_id,))
            if cur.fetchone() is None:
                return False
            cur.execute(
                "INSERT INTO se_document_states (document_id, state, analyzed_hashes, updated_at) "
                "VALUES (%s,%s,%s,NOW()) "
                "ON CONFLICT (document_id) DO UPDATE SET state = EXCLUDED.state, "
                "analyzed_hashes = EXCLUDED.analyzed_hashes, updated_at = NOW()",
                (doc_id, state, json.dumps(analyzed_hashes)),
            )
            cur.execute("UPDATE se_documents SET updated_at = NOW() WHERE id = %s", (doc_id,))
        conn.commit()
    return True


def access_check(doc_id: str, user_id: str) -> dict:
    """Node WS 握手时的归属核对：文档存在且 active，且用户是属主。

    刻意不设 admin 旁路——写作文档是私有内容，管理面只覆盖模板；
    与 REST 侧 _get_own_document 的 404 语义保持同一把尺子。
    """
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT owner_user_id, status FROM se_documents WHERE id = %s", (doc_id,)
            )
            doc = cur.fetchone()
            if not doc or doc["status"] != "active":
                return {"exists": False, "allowed": False}
            return {"exists": True, "allowed": doc["owner_user_id"] == user_id}


# ------------------------------- 模板 -------------------------------


def list_templates() -> list[dict]:
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, name, description, content_html, is_builtin, sort_order, updated_at "
                "FROM se_templates ORDER BY sort_order ASC, created_at ASC"
            )
            return [_template_row(r) for r in cur.fetchall()]


def get_template(template_id: str) -> dict | None:
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, name, description, content_html, is_builtin, sort_order, updated_at "
                "FROM se_templates WHERE id = %s",
                (template_id,),
            )
            row = cur.fetchone()
            return _template_row(row) if row else None


def create_template(template_id: str, name: str, description: str,
                    content_html: str, owner_user_id: str | None) -> dict:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO se_templates (id, name, description, content_html, is_builtin, owner_user_id) "
                "VALUES (%s,%s,%s,%s,FALSE,%s)",
                (template_id, name, description, content_html, owner_user_id),
            )
        conn.commit()
    return get_template(template_id)


def update_template(template_id: str, name: str, description: str, content_html: str) -> dict | None:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE se_templates SET name = %s, description = %s, content_html = %s, updated_at = NOW() "
                "WHERE id = %s",
                (name, description, content_html, template_id),
            )
        conn.commit()
    return get_template(template_id)


def delete_template(template_id: str) -> bool:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM se_templates WHERE id = %s", (template_id,))
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted
