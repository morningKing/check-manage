"""智能写作（suggest-editor）路由。

两组端点：
- 用户端 `/suggest-editor/*`：文档 CRUD（owner 范围）、open 心跳、模板
  只读列表 + 管理员模板 CRUD（admin.suggest_editor）。
- 内部端 `/suggest-editor/internal/*`：Hocuspocus Node 服务在 WS 握手
  （access）与文档存取（state）时调用，X-Internal-Token 鉴权（与 MCP
  内部通道同一把 MCP_INTERNAL_TOKEN；空 token = 一律 403，同
  ai_memory_internal 先例）。文档归属的最终裁决在这里——Node 侧只做
  JWT 验签，不直接读库。
"""
import base64
import binascii
import re
import shutil
import uuid

from flask import Blueprint, g, jsonify, request

from auth import login_required, require_permission, write_required
from config import MCP_INTERNAL_TOKEN, SE_DATA_DIR
from utils.operation_log import log_operation
from utils.suggest_editor_repo import (
    access_check,
    create_document,
    create_template,
    delete_document,
    delete_template,
    get_document,
    get_template,
    list_documents,
    list_templates,
    rename_document,
    save_state,
    touch_document,
    update_template,
)

suggest_editor_bp = Blueprint("suggest_editor", __name__, url_prefix="/suggest-editor")

# 文档 id 由本服务生成（uuid4），删除工作区目录前校验一次，杜绝路径拼接
_DOC_ID_RE = re.compile(r"^[0-9a-fA-F][0-9a-fA-F-]{8,40}$")
MAX_TITLE_LEN = 200
MAX_TEMPLATE_CONTENT = 2_000_000  # HTML 2MB 上限（模板正文远小于此）


def _internal_authorized() -> bool:
    return bool(MCP_INTERNAL_TOKEN) and request.headers.get("X-Internal-Token", "") == MCP_INTERNAL_TOKEN


def _workspace_dir(doc_id: str) -> str:
    """该文档在 Node 服务侧的 agent 工作区（含 complete/ 子工作区）。

    与 suggest-editor-server/src/workspace.ts 的 workspaceDir 同构：
    docId 只含 [a-zA-Z0-9._-]，safeName 不改写。
    """
    import os
    return os.path.join(SE_DATA_DIR, "ws", doc_id)


def _cleanup_workspace(doc_id: str) -> None:
    if not _DOC_ID_RE.fullmatch(doc_id):
        return
    try:
        shutil.rmtree(_workspace_dir(doc_id), ignore_errors=True)
    except Exception:
        pass


def _user_id() -> str:
    return g.current_user["userId"]


def _get_own_document(doc_id: str) -> dict | None:
    """严格属主唯一：写作文档是私有内容，admin 也无跨人旁路（与 WS access 核对
    同一把尺子）；非属主一律 404，不泄露存在性。"""
    doc = get_document(doc_id)
    if not doc or doc["status"] != "active":
        return None
    if doc["ownerUserId"] != _user_id():
        return None
    return doc


# ------------------------------- 文档（用户端） -------------------------------


@suggest_editor_bp.route("/documents", methods=["GET"])
@login_required
def list_my_documents():
    return jsonify({"documents": list_documents(_user_id())})


@suggest_editor_bp.route("/documents", methods=["POST"])
@write_required
def create_my_document():
    body = request.get_json(silent=True) or {}
    title = str(body.get("title") or "").strip() or "未命名文档"
    if len(title) > MAX_TITLE_LEN:
        return jsonify({"error": "标题过长"}), 400
    template_id = body.get("templateId") or None
    if template_id and not get_template(template_id):
        return jsonify({"error": "模板不存在"}), 400
    doc = create_document(str(uuid.uuid4()), title, _user_id(), template_id)
    log_operation("create", "suggest_editor_document", doc["id"], title, f"创建智能写作文档「{title}」")
    return jsonify({"document": doc}), 201


@suggest_editor_bp.route("/documents/<doc_id>", methods=["GET"])
@login_required
def get_my_document(doc_id: str):
    doc = _get_own_document(doc_id)
    if not doc:
        return jsonify({"error": "文档不存在"}), 404
    return jsonify({"document": doc})


@suggest_editor_bp.route("/documents/<doc_id>", methods=["PATCH"])
@write_required
def rename_my_document(doc_id: str):
    doc = _get_own_document(doc_id)
    if not doc:
        return jsonify({"error": "文档不存在"}), 404
    body = request.get_json(silent=True) or {}
    title = str(body.get("title") or "").strip()
    if not title:
        return jsonify({"error": "标题不能为空"}), 400
    if len(title) > MAX_TITLE_LEN:
        return jsonify({"error": "标题过长"}), 400
    updated = rename_document(doc_id, title)
    log_operation("update", "suggest_editor_document", doc_id, title,
                  f"重命名智能写作文档「{doc['title']}」→「{title}」")
    return jsonify({"document": updated})


@suggest_editor_bp.route("/documents/<doc_id>", methods=["DELETE"])
@write_required
def delete_my_document(doc_id: str):
    doc = _get_own_document(doc_id)
    if not doc:
        return jsonify({"error": "文档不存在"}), 404
    delete_document(doc_id)
    _cleanup_workspace(doc_id)
    log_operation("delete", "suggest_editor_document", doc_id, doc["title"],
                  f"删除智能写作文档「{doc['title']}」")
    return jsonify({"ok": True})


@suggest_editor_bp.route("/documents/<doc_id>/open", methods=["POST"])
@login_required
def open_my_document(doc_id: str):
    doc = _get_own_document(doc_id)
    if not doc:
        return jsonify({"error": "文档不存在"}), 404
    touch_document(doc_id, "last_opened_at")
    return jsonify({"ok": True})


# ------------------------------- 模板 -------------------------------


@suggest_editor_bp.route("/templates", methods=["GET"])
@login_required
def list_all_templates():
    return jsonify({"templates": list_templates()})


@suggest_editor_bp.route("/templates", methods=["POST"])
@require_permission("admin.suggest_editor")
def create_new_template():
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "模板名称不能为空"}), 400
    content = str(body.get("contentHtml") or "")
    if len(content) > MAX_TEMPLATE_CONTENT:
        return jsonify({"error": "模板内容过大"}), 400
    description = str(body.get("description") or "").strip()
    tpl = create_template(f"se-tpl-{uuid.uuid4().hex[:12]}", name, description,
                          content, _user_id())
    log_operation("create", "suggest_editor_template", tpl["id"], name, f"创建写作模板「{name}」")
    return jsonify({"template": tpl}), 201


@suggest_editor_bp.route("/templates/<template_id>", methods=["PATCH"])
@require_permission("admin.suggest_editor")
def update_existing_template(template_id: str):
    existing = get_template(template_id)
    if not existing:
        return jsonify({"error": "模板不存在"}), 404
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip() or existing["name"]
    content = str(body.get("contentHtml") or "")
    if len(content) > MAX_TEMPLATE_CONTENT:
        return jsonify({"error": "模板内容过大"}), 400
    description = str(body.get("description") if body.get("description") is not None
                      else existing["description"] or "").strip()
    tpl = update_template(template_id, name, description, content)
    log_operation("update", "suggest_editor_template", template_id, name, f"更新写作模板「{name}」")
    return jsonify({"template": tpl})


@suggest_editor_bp.route("/templates/<template_id>", methods=["DELETE"])
@require_permission("admin.suggest_editor")
def delete_existing_template(template_id: str):
    existing = get_template(template_id)
    if not existing:
        return jsonify({"error": "模板不存在"}), 404
    delete_template(template_id)
    log_operation("delete", "suggest_editor_template", template_id, existing["name"],
                  f"删除写作模板「{existing['name']}」")
    return jsonify({"ok": True})


# --------------------------- 内部端点（Node 服务专用） ---------------------------


@suggest_editor_bp.route("/internal/documents/<doc_id>/access", methods=["GET"])
def internal_document_access(doc_id: str):
    if not _internal_authorized():
        return jsonify({"error": "forbidden"}), 403
    user_id = request.args.get("userId", "")
    if not user_id:
        return jsonify({"exists": False, "allowed": False})
    return jsonify(access_check(doc_id, user_id))


@suggest_editor_bp.route("/internal/documents/<doc_id>/state", methods=["GET"])
def internal_document_state_get(doc_id: str):
    if not _internal_authorized():
        return jsonify({"error": "forbidden"}), 403
    from utils.suggest_editor_repo import load_state

    state = load_state(doc_id)
    return jsonify({
        "state": base64.b64encode(state["state"]).decode("ascii") if state["state"] else None,
        "analyzedHashes": state["analyzedHashes"],
    })


@suggest_editor_bp.route("/internal/documents/<doc_id>/state", methods=["PUT"])
def internal_document_state_put(doc_id: str):
    if not _internal_authorized():
        return jsonify({"error": "forbidden"}), 403
    body = request.get_json(silent=True) or {}
    raw_state = body.get("state")
    hashes = body.get("analyzedHashes")
    if raw_state is None:
        return jsonify({"error": "state is required"}), 400
    if not isinstance(hashes, list) or not all(isinstance(h, str) for h in hashes):
        return jsonify({"error": "analyzedHashes must be a list of strings"}), 400
    try:
        state = base64.b64decode(raw_state, validate=True)
    except (binascii.Error, ValueError):
        return jsonify({"error": "state must be base64"}), 400
    if len(state) > 64 * 1024 * 1024:
        return jsonify({"error": "state too large"}), 413
    saved = save_state(doc_id, state, hashes)
    if not saved:
        return jsonify({"error": "document not found"}), 404
    return jsonify({"ok": True})
