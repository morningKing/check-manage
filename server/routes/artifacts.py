"""Artifact REST 端点（ai-harness-p2 spec §9.1）。

下载/预览一律按 artifactId 鉴权：owner 或 admin；不再依赖 workspace 路径。
"""
from flask import Blueprint, g as flask_g, jsonify, send_file

from auth import login_required
from db import get_db

from utils import artifact_store

artifacts_bp = Blueprint('artifacts', __name__, url_prefix='/v1/artifacts')


@artifacts_bp.get('/<artifact_id>')
@login_required
def download(artifact_id):
    art = artifact_store.get_artifact(artifact_id)
    if not art:
        return jsonify({'error': 'not found'}), 404
    user = flask_g.current_user
    if user.get('role') != 'admin' and art['ownerUserId'] != user['userId']:
        return jsonify({'error': 'forbidden'}), 403
    path = artifact_store.artifact_path(art)
    if not path:
        return jsonify({'error': 'artifact content missing'}), 410
    return send_file(path, as_attachment=True, download_name=art['name'])


@artifacts_bp.get('')
@login_required
def list_artifacts():
    user = flask_g.current_user
    with get_db() as conn:
        with conn.cursor() as cur:
            if user.get('role') == 'admin':
                cur.execute(
                    "SELECT id, name, media_type, size_bytes, sha256, "
                    "created_at FROM artifacts WHERE status='active' "
                    "ORDER BY created_at DESC LIMIT 200")
            else:
                cur.execute(
                    "SELECT id, name, media_type, size_bytes, sha256, "
                    "created_at FROM artifacts WHERE status='active' "
                    "  AND owner_user_id = %s "
                    "ORDER BY created_at DESC LIMIT 200", (user['userId'],))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        if r.get('created_at') is not None:
            r['createdAt'] = r.pop('created_at').isoformat()
    return jsonify({'artifacts': rows})
