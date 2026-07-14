"""Routes for data-page file/image field uploads + downloads.

Replaces the demo `mockUpload` in the frontend that stored `blob:` URLs —
those vanished on reload and were invisible to other browsers.

Storage layout: <DATA_FILES_ROOT>/<id[:2]>/<id>/<original_name>
  - Two-level dir keeps inode counts sane.
  - Original name is kept (after Unicode-preserving safe_filename, so 中文
    names survive) and downloads land with a sensible name.

JSONB shape stored in dynamic_data:
  [{ "uid": "<data_files.id>", "name": "...", "url": "/api/data-files/<id>/download",
     "size": <bytes>, "type": "<mime>" }]

Auth model: any logged-in user can download (matches data-page read
semantics, which are not row-level ACL'd). Upload requires non-guest
(`write_required`). `login_required_sse` is used on download so the URL
works inside <img src=...> / <a href=...> via ?access_token=.
"""
import os
import uuid
from pathlib import Path

from flask import Blueprint, request, jsonify, g, send_file

from auth import login_required, login_required_sse
from db import get_db
from config import DATA_FILES_ROOT, DATA_FILE_MAX_MB
from utils.filename import safe_filename
from utils.permissions import can_page

data_files_bp = Blueprint('data_files', __name__)

_MAX_BYTES = DATA_FILE_MAX_MB * 1024 * 1024


def _get_file_extension(filename: str) -> str:
    """Lowercase extension incl. leading dot, e.g. 'a.PDF' -> '.pdf'. No dot -> ''."""
    _, ext = os.path.splitext(filename)
    return ext.lower()


def _check_allowed_extension(collection, field_name, filename):
    """Look up the field's fileConfig.allowedExtensions on page_configs and
    validate filename's extension against it.

    Mirrors the frontend's accept/beforeUpload check — that one is UX-only
    and trivially bypassed by calling this endpoint directly, so the real
    enforcement has to live here. No collection/fieldName, no matching
    field, or an empty allowedExtensions list all mean "unrestricted"
    (backward compatible with fields that never configured this).

    Returns an error message string, or None if the upload is allowed.
    """
    if not collection or not field_name:
        return None
    page_id = f'page-{collection}'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT fields FROM page_configs WHERE id = %s', (page_id,))
        row = cur.fetchone()
    fields = row[0] if row and row[0] else []
    field = next((f for f in fields if f.get('fieldName') == field_name), None)
    if not field:
        return None
    allowed = (field.get('fileConfig') or {}).get('allowedExtensions') or []
    if not allowed:
        return None
    ext = _get_file_extension(filename)
    if ext not in allowed:
        return f'不支持 {ext or "该"} 类型的文件，仅支持 {"、".join(allowed)}'
    return None


def _storage_dir(file_id: str) -> Path:
    p = Path(DATA_FILES_ROOT) / file_id[:2] / file_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_data_file(f, uploaded_by=None):
    """Persist a werkzeug FileStorage to disk + the data_files table.

    Shared by the web upload route and the Open API upload route. Returns
    ``(meta, error)``: ``meta`` (dict) on success with ``error`` None, or
    ``(None, (response, status))`` on failure. ``uploaded_by`` is a users.id
    (the column is nullable — API-key uploads pass None).
    """
    safe_name = safe_filename(f.filename)  # preserves Unicode (e.g. 中文) names
    file_id = str(uuid.uuid4())
    dest_path = _storage_dir(file_id) / safe_name

    f.save(dest_path)
    size = os.path.getsize(dest_path)
    if size > _MAX_BYTES:
        try:
            os.remove(dest_path)
        except OSError:
            pass
        return None, (jsonify({'error': f'file too large (max {DATA_FILE_MAX_MB} MB)'}), 413)

    mime = f.mimetype or 'application/octet-stream'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO data_files (id, original_name, mime_type, size_bytes, "
            "storage_path, uploaded_by) VALUES (%s, %s, %s, %s, %s, %s)",
            (file_id, safe_name, mime, size, str(dest_path), uploaded_by),
        )
    return {
        'id': file_id,
        'name': safe_name,
        'size': size,
        'mimeType': mime,
        'url': f'/api/data-files/{file_id}/download',
    }, None


@data_files_bp.route('/data-files/upload', methods=['POST'])
@login_required
def upload_data_file():
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'file required'}), 400

    # 鉴权：文件字段属于某个数据页。若请求带上目标 collection，则按该数据页的
    # 「写」权限（新增或编辑任一）放行——这样被授予数据页写权限的自定义角色
    # （含访客）也能上传，与 routes/dynamic.py 的 require_page_action 一致。
    # 未带 collection 的旧调用退回「非访客」校验，保持向后兼容。
    role = (g.current_user or {}).get('role')
    collection = (request.form.get('collection') or '').strip()
    field_name = (request.form.get('fieldName') or '').strip()
    if collection:
        page_id = f'page-{collection}'
        if not (can_page(role, page_id, 'create') or can_page(role, page_id, 'update')):
            return jsonify({'error': '权限不足'}), 403
    elif role == 'guest':
        return jsonify({'error': '访客无操作权限'}), 403

    type_error = _check_allowed_extension(collection, field_name, f.filename)
    if type_error:
        return jsonify({'error': type_error}), 400

    meta, err = save_data_file(f, uploaded_by=g.current_user['userId'])
    if err:
        return err
    return jsonify(meta), 201


@data_files_bp.route('/data-files/<file_id>/download', methods=['GET'])
@login_required_sse  # JWT via Authorization OR ?access_token=
def download_data_file(file_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT original_name, mime_type, storage_path FROM data_files WHERE id = %s',
            (file_id,),
        )
        row = cur.fetchone()
    if not row:
        return jsonify({'error': 'not found'}), 404
    name, mime, path = row
    if not os.path.isfile(path):
        return jsonify({'error': 'file missing on disk', 'code': 'STORAGE_MISSING'}), 410
    # Inline display for images so <img> works; attachment otherwise.
    as_attachment = not (mime or '').startswith('image/')
    return send_file(
        path,
        mimetype=mime or 'application/octet-stream',
        download_name=name,
        as_attachment=as_attachment,
    )


@data_files_bp.route('/data-files/<file_id>', methods=['GET'])
@login_required
def get_data_file_metadata(file_id):
    """Metadata-only endpoint. Useful for "the JSONB row references this
    file id — what's its name/size now?" without re-streaming bytes."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT id, original_name, mime_type, size_bytes, uploaded_at '
            'FROM data_files WHERE id = %s',
            (file_id,),
        )
        row = cur.fetchone()
    if not row:
        return jsonify({'error': 'not found'}), 404
    return jsonify({
        'id': row[0],
        'name': row[1],
        'mimeType': row[2],
        'size': row[3],
        'uploadedAt': row[4].isoformat() if row[4] else None,
        'url': f'/api/data-files/{row[0]}/download',
    })
