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

from auth import login_required, login_required_sse, write_required
from db import get_db
from config import DATA_FILES_ROOT, DATA_FILE_MAX_MB
from utils.filename import safe_filename

data_files_bp = Blueprint('data_files', __name__)

_MAX_BYTES = DATA_FILE_MAX_MB * 1024 * 1024


def _storage_dir(file_id: str) -> Path:
    p = Path(DATA_FILES_ROOT) / file_id[:2] / file_id
    p.mkdir(parents=True, exist_ok=True)
    return p


@data_files_bp.route('/data-files/upload', methods=['POST'])
@write_required
def upload_data_file():
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'file required'}), 400

    safe_name = safe_filename(f.filename)  # preserves Unicode (e.g. 中文) names
    file_id = str(uuid.uuid4())
    dest_dir = _storage_dir(file_id)
    dest_path = dest_dir / safe_name

    f.save(dest_path)
    size = os.path.getsize(dest_path)
    if size > _MAX_BYTES:
        try:
            os.remove(dest_path)
        except OSError:
            pass
        return jsonify({'error': f'file too large (max {DATA_FILE_MAX_MB} MB)'}), 413

    mime = f.mimetype or 'application/octet-stream'
    user = g.current_user
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO data_files (id, original_name, mime_type, size_bytes, "
            "storage_path, uploaded_by) VALUES (%s, %s, %s, %s, %s, %s)",
            (file_id, safe_name, mime, size, str(dest_path), user['userId']),
        )

    return jsonify({
        'id': file_id,
        'name': safe_name,
        'size': size,
        'mimeType': mime,
        'url': f'/api/data-files/{file_id}/download',
    }), 201


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
