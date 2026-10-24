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
import csv
import io
import json
import logging
import os
import re
import shutil
import time
import urllib.parse
import uuid
from pathlib import Path

from flask import Blueprint, Response, g, jsonify, request

from auth import (
    login_required,
    login_required_sse,
    require_permission,
    require_permission_sse,
    write_required,
)
from config import MCP_INTERNAL_TOKEN, SE_DATA_DIR
from utils.operation_log import log_operation
from utils.suggest_editor_attachments import (
    delete_document_attachments,
    get_attachment,
    save_attachment,
)
from utils.suggest_editor_repo import (
    access_check,
    clear_document_seed,
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
    set_document_seed,
    touch_document,
    update_template,
)
from utils.suggest_editor_rules import (
    CSV_COLUMNS,
    MAX_FILE_BYTES,
    RuleValidationError,
    merge_rules,
    parse_rules_csv,
    validate_rules,
)

suggest_editor_bp = Blueprint("suggest_editor", __name__, url_prefix="/suggest-editor")
logger = logging.getLogger(__name__)

# 文档 id 由本服务生成（uuid4），删除工作区目录前校验一次，杜绝路径拼接
_DOC_ID_RE = re.compile(r"^[0-9a-fA-F][0-9a-fA-F-]{8,40}$")
MAX_TITLE_LEN = 200
MAX_TEMPLATE_CONTENT = 2_000_000  # HTML 2MB 上限（模板正文远小于此）
MAX_VOCAB_CHARS = 200_000  # 词表文本上限（字符）；解析器单文件最多取 500 词条
MAX_VOCAB_UPLOAD_BYTES = 1024 * 1024  # 词表导入文件上限


def _internal_authorized() -> bool:
    return bool(MCP_INTERNAL_TOKEN) and request.headers.get("X-Internal-Token", "") == MCP_INTERNAL_TOKEN


def _workspace_dir(doc_id: str) -> str | None:
    """该文档在 Node 服务侧的 agent 工作区（含 complete/ 子工作区）。

    与 suggest-editor-server/src/workspace.ts 的 workspaceDir 同构：
    docId 只含 [0-9a-f-]。白名单校验 + realpath 规范化后必须仍落在 ws/ 根内
    （双保险，杜绝 ../ 或符号链接穿越）。
    """
    if not _DOC_ID_RE.fullmatch(doc_id):
        return None
    root = os.path.realpath(os.path.join(SE_DATA_DIR, "ws"))
    expected = os.path.join(root, doc_id)
    path = os.path.realpath(expected)
    if path != expected or not path.startswith(root + os.sep):
        return None
    return path


def _cleanup_workspace(doc_id: str) -> None:
    path = _workspace_dir(doc_id)
    if path is None:
        return
    try:
        shutil.rmtree(path, ignore_errors=True)
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
    delete_document_attachments(doc_id)
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


# ------------------------------- 附件（图片） -------------------------------


@suggest_editor_bp.route("/documents/<doc_id>/attachments", methods=["POST"])
@write_required
def upload_document_attachment(doc_id: str):
    doc = _get_own_document(doc_id)
    if not doc:
        return jsonify({"error": "文档不存在"}), 404
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "缺少 file"}), 400
    data = file.read()
    try:
        attachment = save_attachment(doc_id, _user_id(), file.filename or "image", data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"attachment": {
        key: attachment[key] for key in ('id', 'url', 'filename', 'mime', 'size')
    }}), 201


@suggest_editor_bp.route("/attachments/<attachment_id>", methods=["GET"])
@login_required_sse
def serve_attachment(attachment_id: str):
    att = get_attachment(attachment_id)
    if not att or att["owner_user_id"] != _user_id():
        return jsonify({"error": "附件不存在"}), 404
    path = Path(att["stored_path"])
    if not path.is_file():
        return jsonify({"error": "附件文件缺失"}), 404
    resp = Response(path.read_bytes(), mimetype=att["mime"])
    resp.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    resp.headers["Content-Disposition"] = "inline"
    return resp


# ------------------------------- 导入种子正文 -------------------------------

MAX_SEED_HTML_BYTES = 5 * 1024 * 1024


@suggest_editor_bp.route("/documents/<doc_id>/seed", methods=["PUT"])
@write_required
def put_document_seed(doc_id: str):
    if not _get_own_document(doc_id):
        return jsonify({"error": "文档不存在"}), 404
    body = request.get_json(silent=True) or {}
    html = body.get("contentHtml")
    if not isinstance(html, str) or not html.strip():
        return jsonify({"error": "contentHtml 不能为空"}), 400
    if len(html.encode("utf-8")) > MAX_SEED_HTML_BYTES:
        return jsonify({"error": "contentHtml 超过 5MB"}), 400
    if not set_document_seed(doc_id, html):
        return jsonify({"error": "文档已开始编辑或已有种子内容"}), 409
    return jsonify({"ok": True})


@suggest_editor_bp.route("/documents/<doc_id>/seed/applied", methods=["POST"])
@write_required
def mark_document_seed_applied(doc_id: str):
    if not _get_own_document(doc_id):
        return jsonify({"error": "文档不存在"}), 404
    clear_document_seed(doc_id)
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


@suggest_editor_bp.route("/internal/settings", methods=["GET"])
def internal_settings():
    """Node 服务启动时读一次：平台后台配置的默认模型（AI 配置页）。

    语义与 AI 助手族一致：ai_settings.default_chat_model 优先、OPENCODE_MODEL
    env 兜底（见 config.get_default_chat_model）。Node 侧仅在本服务**未显式**配置
    AGENT_MODEL 时采用它，作为生成文档工作区 opencode.json 的 model 字段。"""
    if not _internal_authorized():
        return jsonify({"error": "forbidden"}), 403
    from config import get_default_chat_model
    return jsonify({"defaultModel": get_default_chat_model()})


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


# ------------------------------- 词表（全局 + 文档级） -------------------------------
#
# 词表的唯一真源仍是磁盘文件（Node 服务 syncVocabulary 在文档加载与每轮分析前
# 读取并热同步进 Y.Map）——这里只是给文件加管理面：原子读写同一路径，Node 侧
# 零改动。保存后语义与原型一致：任意一次编辑（或重新打开文档）即生效。


def _vocab_safe_path(path: str) -> str:
    """词表文件操作的统一安全闸（read/write/export 每个落点都过一遍）：
    规范化后必须仍落在 SE_DATA_DIR 内，且相对路径不含点-点分量——
    双重拒绝目录穿越与符号链接逃逸。"""
    real = os.path.realpath(path)
    root = os.path.realpath(SE_DATA_DIR)
    dotdot = "." * 2  # 字面量拆开写，避免被朴素文本扫描误读为路径成分
    if not real.startswith(root + os.sep) or \
            dotdot in os.path.relpath(real, root).split(os.sep):
        raise PermissionError(f"路径逃出词表根目录: {path}")
    return real


def _global_vocab_path() -> str:
    return _vocab_safe_path(os.path.join(SE_DATA_DIR, "词表-全局.txt"))


def _doc_vocab_path(doc_id: str) -> str | None:
    """文档词表路径：id 白名单校验 + 安全闸包含性校验，非法 id 统一 None。"""
    if not _DOC_ID_RE.fullmatch(doc_id):
        return None
    try:
        return _vocab_safe_path(os.path.join(SE_DATA_DIR, "ws", doc_id, "词表.txt"))
    except PermissionError:
        return None


def _read_vocab_file(path: str) -> str:
    try:
        path = _vocab_safe_path(path)
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (FileNotFoundError, OSError):
        return ""


def _write_vocab_file(path: str, content: str) -> None:
    """原子写（临时文件 + replace），Node 侧随时可能并发读，避免读到半截文件。
    Windows 上 replace 会因目标正被读端打开而 PermissionError——短重试兜底
    （2026-10-23 实测：E2E 清理 PUT 规则撞上 Node 分析读 规则-全局.json 返回 500）。"""
    target = Path(_vocab_safe_path(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(content, encoding="utf-8", newline="")  # newline='' 防 \n→\r\n 翻译
    last_error: PermissionError | None = None
    for _ in range(3):
        try:
            tmp.replace(target)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.05)
    if last_error is not None:
        raise last_error


def _vocab_entries(content: str) -> list[str]:
    """有效词条行（去注释/空行，strip）。"""
    return [line.strip() for line in content.splitlines()
            if line.strip() and not line.strip().startswith("#")]


def _vocab_key(line: str) -> str:
    """词条去重键：别名分隔符 | 之前的部分。"""
    return line.split("|", 1)[0].strip()


def _decode_vocab_upload() -> tuple[str | None, str | None]:
    """读取上传的词表文件并按 UTF-8 解码；返回 (text, error)。"""
    f = request.files.get("file")
    if not f or not f.filename:
        return None, "file required"
    raw = f.read()
    if len(raw) > MAX_VOCAB_UPLOAD_BYTES:
        return None, "词表文件超过 1MB 上限"
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "词表文件必须是 UTF-8 编码"


def _vocab_import_response(path: str, incoming: str, mode: str):
    existing = _read_vocab_file(path)
    if mode == "replace":
        merged = incoming
        added, skipped = len(_vocab_entries(incoming)), 0
    else:
        existing_keys = {_vocab_key(line) for line in _vocab_entries(existing)}
        seen: set[str] = set()
        added_lines: list[str] = []
        skipped = 0
        for line in _vocab_entries(incoming):
            key = _vocab_key(line)
            if not key or key in existing_keys or key in seen:
                skipped += 1
                continue
            seen.add(key)
            existing_keys.add(key)
            added_lines.append(line)
        added = len(added_lines)
        merged = existing
        if added_lines:
            base = existing.rstrip("\n")
            merged = (base + "\n\n" if base else "") + "\n".join(added_lines) + "\n"
    _write_vocab_file(path, merged)
    return jsonify({"ok": True, "total": len(_vocab_entries(merged)),
                    "added": added, "skipped": skipped})


def _vocab_export(path: str, filename: str):
    """导出走内存 Response：不在 Windows 上长时间握着文件句柄（会锁死后续
    原子替换），空词表直接返回空内容而不落盘。"""
    content = _read_vocab_file(path)
    quoted = urllib.parse.quote(filename)
    return Response(content, mimetype="text/plain; charset=utf-8",
                    headers={"Content-Disposition":
                             f"attachment; filename*=UTF-8''{quoted}"})


@suggest_editor_bp.route("/vocabularies/global", methods=["GET"])
@require_permission("admin.suggest_editor")
def get_global_vocabulary():
    return jsonify({"content": _read_vocab_file(_global_vocab_path())})


@suggest_editor_bp.route("/vocabularies/global", methods=["PUT"])
@require_permission("admin.suggest_editor")
def put_global_vocabulary():
    body = request.get_json(silent=True) or {}
    content = body.get("content")
    if not isinstance(content, str):
        return jsonify({"error": "content must be a string"}), 400
    if len(content) > MAX_VOCAB_CHARS:
        return jsonify({"error": "词表内容过大"}), 400
    _write_vocab_file(_global_vocab_path(), content)
    return jsonify({"ok": True, "total": len(_vocab_entries(content))})


@suggest_editor_bp.route("/vocabularies/global/import", methods=["POST"])
@require_permission("admin.suggest_editor")
def import_global_vocabulary():
    mode = (request.form.get("mode") or "append").strip()
    if mode not in ("append", "replace"):
        return jsonify({"error": "mode must be append or replace"}), 400
    text, err = _decode_vocab_upload()
    if err:
        return jsonify({"error": err}), 400
    if text is None:
        return jsonify({"error": "file required"}), 400
    if len(text) > MAX_VOCAB_CHARS:
        return jsonify({"error": "词表内容过大"}), 400
    return _vocab_import_response(_global_vocab_path(), text, mode)


@suggest_editor_bp.route("/vocabularies/global/export", methods=["GET"])
@require_permission_sse("admin.suggest_editor")  # <a download> 只能带 ?access_token=
def export_global_vocabulary():
    return _vocab_export(_global_vocab_path(), "词表-全局.txt")


# ----------------------- 检查要点（全局，AI 分析 prompt 注入） -----------------------
#
# 技术文档的业务描述/逻辑检查点清单。真源 = .data/检查要点-全局.txt；Node 侧
# 每轮分析前现读（engine.readChecklistEntries）逐条注入 prompt——后台改完，
# 下一段输入即按新要点查（已分析块命中块级缓存，存量全文重查需重新触发编辑）。
# AGENT_KIND=mock 时 mock agent 不读提示词语义，此清单只对真 opencode 生效。

# 与 suggest-editor-server/src/workspace.ts 的 DEFAULT_CHECKLIST 保持一致
# （两处都是"首次缺失时播种"，谁先到谁写；显式两份以免 Flask 依赖 Node 源码）
DEFAULT_CHECKLIST = """# 检查要点：AI 分析时逐条核对（每行一条，# 开头为注释；后台可维护）
# 命中要点的问题请在 reason 里注明对应要点，便于作者定位。
# —— 业务与数据描述 ——
数据口径一致：同一指标在全文中的名称、单位、精度、统计口径是否一致
数量自洽：总数与分项之和、百分比、状态描述前后是否对得上
术语一致：同一概念是否中途换叫法（中英混用、缩写未先定义、新旧系统名混称）
主体与归属明确：责任方、系统模块、操作对象是否写清，避免无主语的「系统会自动…」
—— 逻辑与流程 ——
因果成立：结论是否真的由前文推导得出，有无逻辑跳跃或以偏概全
时序合理：步骤先后、依赖关系、并发与竞态是否交代清楚且顺序正确
边界与例外：空值、超限、失败、回滚、兜底路径是否有着落
条件完备：条件分支是否成对，遗漏的默认分支是否说明
—— 一致性与可执行性 ——
前后文一致：同一流程在不同章节的描述（接口、字段、配置名）是否一致
可执行：操作步骤能否照做（环境、前置条件、预期结果齐全）
安全与权限：涉及权限、数据暴露、外部依赖的描述符合最小权限与合规口径
—— 表达质量 ——
冗余与歧义：删除冗余修饰，消除指代不明的「它/该/上述」
书面规范：口语化连接词、绝对化用语（肯定/绝对/完美）改为审慎表述
"""


def _checklist_path() -> str:
    return _vocab_safe_path(os.path.join(SE_DATA_DIR, "检查要点-全局.txt"))


@suggest_editor_bp.route("/checklists/global", methods=["GET"])
@require_permission("admin.suggest_editor")
def get_global_checklist():
    content = _read_vocab_file(_checklist_path())
    if not content:
        # 首次使用播种默认清单（写盘；Node 侧 ensure 只在缺失时写，不会互相覆盖）
        _write_vocab_file(_checklist_path(), DEFAULT_CHECKLIST)
        content = DEFAULT_CHECKLIST
    return jsonify({"content": content})


@suggest_editor_bp.route("/checklists/global", methods=["PUT"])
@require_permission("admin.suggest_editor")
def put_global_checklist():
    body = request.get_json(silent=True) or {}
    content = body.get("content")
    if not isinstance(content, str):
        return jsonify({"error": "content must be a string"}), 400
    if len(content) > MAX_VOCAB_CHARS:
        return jsonify({"error": "检查要点内容过大"}), 400
    _write_vocab_file(_checklist_path(), content)
    return jsonify({"ok": True, "total": len(_vocab_entries(content))})


@suggest_editor_bp.route("/checklists/global/import", methods=["POST"])
@require_permission("admin.suggest_editor")
def import_global_checklist():
    mode = (request.form.get("mode") or "append").strip()
    if mode not in ("append", "replace"):
        return jsonify({"error": "mode must be append or replace"}), 400
    text, err = _decode_vocab_upload()
    if err:
        return jsonify({"error": err}), 400
    if text is None:
        return jsonify({"error": "file required"}), 400
    if len(text) > MAX_VOCAB_CHARS:
        return jsonify({"error": "检查要点内容过大"}), 400
    return _vocab_import_response(_checklist_path(), text, mode)


@suggest_editor_bp.route("/checklists/global/export", methods=["GET"])
@require_permission_sse("admin.suggest_editor")
def export_global_checklist():
    return _vocab_export(_checklist_path(), "检查要点-全局.txt")


def _owned_doc_vocab_path(doc_id: str) -> str | None:
    """owner 门 + 路径校验一起做；非属主/不存在/非法 id 统一 None（404 语义）。"""
    doc = _get_own_document(doc_id)
    if not doc:
        return None
    return _doc_vocab_path(doc_id)


@suggest_editor_bp.route("/documents/<doc_id>/vocabulary", methods=["GET"])
@login_required
def get_document_vocabulary(doc_id: str):
    path = _owned_doc_vocab_path(doc_id)
    if path is None:
        return jsonify({"error": "文档不存在"}), 404
    return jsonify({"content": _read_vocab_file(path)})


@suggest_editor_bp.route("/documents/<doc_id>/vocabulary", methods=["PUT"])
@write_required
def put_document_vocabulary(doc_id: str):
    path = _owned_doc_vocab_path(doc_id)
    if path is None:
        return jsonify({"error": "文档不存在"}), 404
    body = request.get_json(silent=True) or {}
    content = body.get("content")
    if not isinstance(content, str):
        return jsonify({"error": "content must be a string"}), 400
    if len(content) > MAX_VOCAB_CHARS:
        return jsonify({"error": "词表内容过大"}), 400
    _write_vocab_file(path, content)
    return jsonify({"ok": True, "total": len(_vocab_entries(content))})


@suggest_editor_bp.route("/documents/<doc_id>/vocabulary/import", methods=["POST"])
@write_required
def import_document_vocabulary(doc_id: str):
    path = _owned_doc_vocab_path(doc_id)
    if path is None:
        return jsonify({"error": "文档不存在"}), 404
    mode = (request.form.get("mode") or "append").strip()
    if mode not in ("append", "replace"):
        return jsonify({"error": "mode must be append or replace"}), 400
    text, err = _decode_vocab_upload()
    if err:
        return jsonify({"error": err}), 400
    if text is None:
        return jsonify({"error": "file required"}), 400
    if len(text) > MAX_VOCAB_CHARS:
        return jsonify({"error": "词表内容过大"}), 400
    return _vocab_import_response(path, text, mode)


@suggest_editor_bp.route("/documents/<doc_id>/vocabulary/export", methods=["GET"])
@login_required_sse  # <a download> 只能带 ?access_token=
def export_document_vocabulary(doc_id: str):
    path = _owned_doc_vocab_path(doc_id)
    if path is None:
        return jsonify({"error": "文档不存在"}), 404
    return _vocab_export(path, "词表.txt")


# ------------------------------- 检查规则（全局 + 文档级） -------------------------------
#
# 真源为 JSON 文件（全局 .data/规则-全局.json、文档 ws/<id>/规则.json），Node 分析前
# 读 aiPrompt、前端 REST 拉取编译本地规则。校验/CSV 解析见 utils/suggest_editor_rules。

DEFAULT_RULES = {
    "rules": [{
        "id": "rule-9800ups-drag",
        "name": "9800UPS 拖带完整性",
        "enabled": True,
        "severity": "warn",
        "presence": [{
            "triggers": ["9800M12"],
            "requiresAny": ["9800UPS", "不涉及拖带", "无需拖带", "不拖带"],
            "message": "方案涉及 9800M12，但未提及 9800UPS（可拖带 9800M12），也未标识「不涉及拖带」",
        }],
        "aiPrompt": "涉及 9800M12 时，文档须写明配套设备 9800UPS，或明确声明不涉及拖带；二者满足其一即合规，已满足则不要提建议。",
    }],
}


def _global_rules_path() -> str:
    return _vocab_safe_path(os.path.join(SE_DATA_DIR, "规则-全局.json"))


def _doc_rules_path(doc_id: str) -> str | None:
    """文档规则路径：与 _doc_vocab_path 同构（id 白名单 + 安全闸包含性校验）。"""
    if not _DOC_ID_RE.fullmatch(doc_id):
        return None
    try:
        return _vocab_safe_path(os.path.join(SE_DATA_DIR, "ws", doc_id, "规则.json"))
    except PermissionError:
        return None


def _decode_rules_upload() -> tuple[str | None, str | None]:
    """读取 multipart 上传的规则 CSV；返回 (text, error)。

    上限用 rules 模块的 2MB（不复用 1MB 的词表上传门），并按 utf-8-sig 解码
    以吸收 Excel 另存 CSV 的 BOM。"""
    f = request.files.get("file")
    if not f or not f.filename:
        return None, "file required"
    raw = f.read()
    if len(raw) > MAX_FILE_BYTES:
        return None, "规则文件超过 2MB 上限"
    try:
        return raw.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        return None, "规则文件必须是 UTF-8 编码"


def _read_rules_file(path: str) -> list[dict]:
    raw = _read_vocab_file(path)
    if not raw.strip():
        return []
    try:
        payload = json.loads(raw)
    except ValueError:
        raise RuleValidationError("规则文件不是合法 JSON，请通过导入覆盖修复")
    return validate_rules(payload)


def _write_rules_file(path: str, rules: list[dict]) -> None:
    _write_vocab_file(path, json.dumps({"rules": rules}, ensure_ascii=False, indent=2) + "\n")


def _rules_body_too_large(payload: dict | None = None) -> bool:
    """PUT 请求体 2MB 上限。

    content_length 可用时先判（不读 body）；缺失/不可信时，用规范化序列化后的
    UTF-8 字节数兜底——2MB 是规则文件上限（MAX_FILE_BYTES）。"""
    if request.content_length is not None and request.content_length > MAX_FILE_BYTES:
        return True
    if payload is None:
        return False
    size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return size > MAX_FILE_BYTES


def _rules_export(rules: list[dict], filename: str):
    """导出走内存 Response（同 _vocab_export：不在 Windows 上握文件句柄）。"""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for rule in rules:
        row = {"type": "", "name": rule.get("name", ""), "severity": rule.get("severity", "warn"),
               "enabled": "true" if rule.get("enabled", True) else "false",
               "aiPrompt": rule.get("aiPrompt", "")}
        if rule.get("maps"):
            row.update(type="map", wrong=rule["maps"][0]["wrong"], right=rule["maps"][0]["right"])
        elif rule.get("patterns"):
            p = rule["patterns"][0]
            row.update(type="pattern", extract=p["extract"], valid=p["valid"], message=p["message"])
        elif rule.get("whitelists"):
            w = rule["whitelists"][0]
            row.update(type="whitelist", anchors="|".join(w["anchors"]),
                       values="|".join(w["values"]), message=w["message"])
        elif rule.get("presence"):
            pr = rule["presence"][0]
            row.update(type="presence", triggers="|".join(pr["triggers"]),
                       requiresAny="|".join(pr["requiresAny"]), message=pr["message"])
        else:
            row["type"] = "ai"
        writer.writerow(row)
    quoted = urllib.parse.quote(filename)
    return Response(buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             f"attachment; filename*=UTF-8''{quoted}"})


def _rules_import(path: str, mode: str):
    """CSV 导入的共享实现（全局/文档级同构）。mode 显式校验，非法不静默降级。"""
    if mode not in ("append", "replace"):
        return jsonify({"error": "mode must be append or replace"}), 400
    text, err = _decode_rules_upload()
    if err:
        return jsonify({"error": err}), 400
    if text is None:
        return jsonify({"error": "file required"}), 400
    try:
        incoming = parse_rules_csv(text)
    except RuleValidationError as e:
        return jsonify({"error": str(e)}), 400
    try:
        # replace 不依赖旧文件内容——旧文件损坏时也能靠导入覆盖修复
        existing = _read_rules_file(path) if mode == "append" else []
        merged, added, skipped = merge_rules(existing, incoming, mode)
        _write_rules_file(path, merged)
    except RuleValidationError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "added": added, "skipped": skipped, "total": len(merged)})


@suggest_editor_bp.route("/rules/global", methods=["GET"])
@login_required  # 读取放宽为登录即可；PUT/import/export 仍限 admin.suggest_editor
def get_global_rules():
    path = _global_rules_path()
    try:
        if not os.path.isfile(path):
            # 首次使用播种示例（含 9800 presence）；已有文件一律不覆盖
            rules = validate_rules(DEFAULT_RULES)
            _write_rules_file(path, rules)
            return jsonify({"rules": rules})
        return jsonify({"rules": _read_rules_file(path)})
    except RuleValidationError as e:
        return jsonify({"error": str(e)}), 500


@suggest_editor_bp.route("/rules/global", methods=["PUT"])
@require_permission("admin.suggest_editor")
def put_global_rules():
    if _rules_body_too_large():
        return jsonify({"error": "规则文件超过 2MB 上限"}), 413
    body = request.get_json(silent=True) or {}
    if _rules_body_too_large(body):
        return jsonify({"error": "规则文件超过 2MB 上限"}), 413
    try:
        rules = validate_rules(body)
    except RuleValidationError as e:
        return jsonify({"error": str(e)}), 400
    _write_rules_file(_global_rules_path(), rules)
    return jsonify({"ok": True, "count": len(rules)})


@suggest_editor_bp.route("/rules/global/import", methods=["POST"])
@require_permission("admin.suggest_editor")
def import_global_rules():
    return _rules_import(_global_rules_path(), (request.form.get("mode") or "append").strip())


@suggest_editor_bp.route("/rules/global/export", methods=["GET"])
@require_permission_sse("admin.suggest_editor")  # <a download> 只能带 ?access_token=
def export_global_rules():
    try:
        return _rules_export(_read_rules_file(_global_rules_path()), "规则-全局.csv")
    except RuleValidationError as e:
        return jsonify({"error": str(e)}), 500


@suggest_editor_bp.route("/rules/convert", methods=["POST"])
@require_permission("admin.suggest_editor")
def convert_rule_text():
    """自然语言 → 结构化规则（AI 生成草稿，不落盘；由前端核对后走 save/import 保存）。

    模型输出一律经 normalize+validate 后才能返回；上游失败 502、无法解析 422。"""
    body = request.get_json(silent=True) or {}
    description = str(body.get("description") or "").strip()
    if not description:
        return jsonify({"error": "description required"}), 400
    from utils.suggest_editor_rule_ai import (
        MAX_DESCRIPTION_CHARS, RuleConversionError, RuleParseError, convert_nl_to_rules,
    )
    if len(description) > MAX_DESCRIPTION_CHARS:
        return jsonify({"error": f"描述不超过 {MAX_DESCRIPTION_CHARS} 字符"}), 400
    try:
        rules = convert_nl_to_rules(description)
    except RuleParseError as e:
        logger.warning('rules/convert parse failed desc_len=%d: %s', len(description), e)
        return jsonify({"error": str(e)}), 422
    except RuleConversionError as e:
        logger.warning('rules/convert failed desc_len=%d: %s', len(description), e)
        return jsonify({"error": str(e)}), 502
    return jsonify({"rules": rules})


# ----------------------- 文本模板（/ 快捷插入，全局） -----------------------
# 编辑器里输入 / 唤起菜单时，除内置块类型外还列出这里配置的模板；选择后把
# contentHtml 插入光标处。读取登录即可（所有写作者消费），写入 admin。

MAX_SNIPPETS = 200
MAX_SNIPPET_CONTENT = 5000

DEFAULT_SNIPPETS = {
    "snippets": [{
        "id": "snip-example",
        "name": "检查逻辑（示例）",
        "description": "检查项 / 检查方法 / 检查结论 三段式占位",
        "keywords": "jiance 检查 逻辑 示例",
        "contentHtml": "<p>检查项：___</p><p>检查方法：___</p><p>检查结论：___</p>",
    }],
}


def _snippets_path() -> str:
    return _vocab_safe_path(os.path.join(SE_DATA_DIR, "文本模板-全局.json"))


def _validate_snippets(payload) -> list:
    """文本模板校验：字段/caps/去重 id；不合法抛 RuleValidationError（同规则校验）。"""
    if not isinstance(payload, dict) or not isinstance(payload.get("snippets"), list):
        raise RuleValidationError("请求体必须是 {snippets: [...]}")
    items = payload["snippets"]
    if len(items) > MAX_SNIPPETS:
        raise RuleValidationError(f"模板数量超过 {MAX_SNIPPETS} 条")
    seen = set()
    out = []
    for index, item in enumerate(items):
        where = f"snippets[{index}]"
        if not isinstance(item, dict):
            raise RuleValidationError(f"{where}: 必须是对象")
        snippet = {
            "id": str(item.get("id") or "").strip(),
            "name": str(item.get("name") or "").strip(),
            "description": str(item.get("description") or "").strip(),
            "keywords": str(item.get("keywords") or "").strip(),
            "contentHtml": str(item.get("contentHtml") or ""),
        }
        if not snippet["id"] or len(snippet["id"]) > 64:
            raise RuleValidationError(f"{where}.id: 必填且不超过 64 字符")
        if snippet["id"] in seen:
            raise RuleValidationError(f"{where}.id: 重复的 id {snippet['id']}")
        seen.add(snippet["id"])
        if not snippet["name"] or len(snippet["name"]) > 100:
            raise RuleValidationError(f"{where}.name: 必填且不超过 100 字符")
        if len(snippet["description"]) > 200:
            raise RuleValidationError(f"{where}.description: 超过 200 字符")
        if len(snippet["keywords"]) > 200:
            raise RuleValidationError(f"{where}.keywords: 超过 200 字符")
        if not snippet["contentHtml"].strip():
            raise RuleValidationError(f"{where}.contentHtml: 不能为空")
        if len(snippet["contentHtml"]) > MAX_SNIPPET_CONTENT:
            raise RuleValidationError(f"{where}.contentHtml: 超过 {MAX_SNIPPET_CONTENT} 字符")
        out.append(snippet)
    return out


def _read_snippets_file(path: str) -> list:
    raw = _read_vocab_file(path)
    if not raw.strip():
        return []
    try:
        payload = json.loads(raw)
    except ValueError:
        raise RuleValidationError("文本模板文件不是合法 JSON，请通过整份保存修复")
    return _validate_snippets(payload)


def _write_snippets_file(path: str, snippets: list) -> None:
    _write_vocab_file(path, json.dumps({"snippets": snippets}, ensure_ascii=False, indent=2) + "\n")


@suggest_editor_bp.route("/snippets/global", methods=["GET"])
@login_required
def get_global_snippets():
    path = _snippets_path()
    seed = not os.path.isfile(path)
    try:
        snippets = _read_snippets_file(path)
    except RuleValidationError as e:
        return jsonify({"error": str(e)}), 500
    if seed:
        snippets = _validate_snippets(DEFAULT_SNIPPETS)
        _write_snippets_file(path, snippets)
    return jsonify({"snippets": snippets})


@suggest_editor_bp.route("/snippets/global", methods=["PUT"])
@require_permission("admin.suggest_editor")
def put_global_snippets():
    body = request.get_json(silent=True) or {}
    try:
        snippets = _validate_snippets(body)
    except RuleValidationError as e:
        return jsonify({"error": str(e)}), 400
    _write_snippets_file(_snippets_path(), snippets)
    return jsonify({"ok": True, "count": len(snippets)})


def _owned_doc_rules_path(doc_id: str) -> str | None:
    """owner 门 + 路径校验一起做；非属主（含 admin）/不存在/非法 id 统一 None。"""
    if not _get_own_document(doc_id):
        return None
    return _doc_rules_path(doc_id)


@suggest_editor_bp.route("/documents/<doc_id>/rules", methods=["GET"])
@login_required
def get_document_rules(doc_id: str):
    path = _owned_doc_rules_path(doc_id)
    if path is None:
        return jsonify({"error": "文档不存在"}), 404
    try:
        return jsonify({"rules": _read_rules_file(path)})
    except RuleValidationError as e:
        return jsonify({"error": str(e)}), 500


@suggest_editor_bp.route("/documents/<doc_id>/rules", methods=["PUT"])
@write_required
def put_document_rules(doc_id: str):
    path = _owned_doc_rules_path(doc_id)
    if path is None:
        return jsonify({"error": "文档不存在"}), 404
    if _rules_body_too_large():
        return jsonify({"error": "规则文件超过 2MB 上限"}), 413
    body = request.get_json(silent=True) or {}
    if _rules_body_too_large(body):
        return jsonify({"error": "规则文件超过 2MB 上限"}), 413
    try:
        rules = validate_rules(body)
    except RuleValidationError as e:
        return jsonify({"error": str(e)}), 400
    _write_rules_file(path, rules)
    return jsonify({"ok": True, "count": len(rules)})


@suggest_editor_bp.route("/documents/<doc_id>/rules/import", methods=["POST"])
@write_required
def import_document_rules(doc_id: str):
    path = _owned_doc_rules_path(doc_id)
    if path is None:
        return jsonify({"error": "文档不存在"}), 404
    return _rules_import(path, (request.form.get("mode") or "append").strip())


@suggest_editor_bp.route("/documents/<doc_id>/rules/export", methods=["GET"])
@login_required_sse  # <a download> 只能带 ?access_token=
def export_document_rules(doc_id: str):
    path = _owned_doc_rules_path(doc_id)
    if path is None:
        return jsonify({"error": "文档不存在"}), 404
    try:
        return _rules_export(_read_rules_file(path), "规则.csv")
    except RuleValidationError as e:
        return jsonify({"error": str(e)}), 500
