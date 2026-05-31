"""REST endpoints for AI chat batch tasks (CRUD + staging upload).

Worker engine lives in utils.batch_engine; this module only owns the HTTP edge.
"""
import os
import uuid
from pathlib import Path

from flask import Blueprint, current_app, g, jsonify, request
from werkzeug.utils import secure_filename

from auth import login_required
from utils.workspace import batch_staging_dir, WorkspacePathError


ai_chat_batches_bp = Blueprint('ai_chat_batches', __name__,
                               url_prefix='/ai/chat/batches')


@ai_chat_batches_bp.post('/staging/upload')
@login_required
def staging_upload():
    f = request.files.get('file')
    upload_session_id = (request.form.get('upload_session_id') or '').strip()
    if not f or not upload_session_id:
        return jsonify({'error': 'file and upload_session_id required'}), 400

    filename = secure_filename(f.filename or '')
    if not filename:
        return jsonify({'error': 'invalid filename'}), 400

    workspace_root = current_app.config.get('AI_CHAT_WORKSPACE_ROOT') \
        or os.environ.get('AI_CHAT_WORKSPACE_ROOT', 'ai-workspaces')
    try:
        staging = batch_staging_dir(workspace_root,
                                    g.current_user['userId'],
                                    upload_session_id)
    except WorkspacePathError as e:
        return jsonify({'error': str(e)}), 400

    dest = staging / filename
    f.save(str(dest))

    rel = dest.relative_to(workspace_root).as_posix()
    return jsonify({'name': filename, 'path': rel}), 201
