"""REST endpoints for AI chat batch tasks (CRUD + staging upload).

Worker engine lives in utils.batch_engine; this module only owns the HTTP edge.
"""
import os
import uuid
from pathlib import Path

from flask import Blueprint, current_app, g, jsonify, request
from utils.filename import safe_filename

from auth import login_required
from utils.workspace import batch_staging_dir, WorkspacePathError
from utils.batch_repo import (
    MAX_FILES_PER_BATCH,
    append_to_batch,
    create_batch,
    delete_batch,
    get_batch_detail,
    list_batches,
    reexecute_child,
    reset_failed_to_pending,
)


ai_chat_batches_bp = Blueprint('ai_chat_batches', __name__,
                               url_prefix='/ai/chat/batches')


@ai_chat_batches_bp.post('/staging/upload')
@login_required
def staging_upload():
    f = request.files.get('file')
    upload_session_id = (request.form.get('upload_session_id') or '').strip()
    if not f or not upload_session_id:
        return jsonify({'error': 'file and upload_session_id required'}), 400

    filename = safe_filename(f.filename or '')  # preserves Unicode (e.g. 中文) names

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


@ai_chat_batches_bp.post('')
@login_required
def create():
    body = request.get_json(silent=True) or {}
    name = (body.get('name') or '').strip()
    prompt = (body.get('prompt') or '').strip()
    template_id = body.get('template_id')
    files = body.get('files') or []
    if not name or not prompt:
        return jsonify({'error': 'name and prompt required'}), 400
    if not isinstance(files, list) or not files:
        return jsonify({'error': 'at least one file required'}), 400
    if len(files) > MAX_FILES_PER_BATCH:
        return jsonify({'error': f'max {MAX_FILES_PER_BATCH} files'}), 400
    for f in files:
        if not isinstance(f, dict) or not f.get('path') or not f.get('name'):
            return jsonify({'error': 'each file must have {name, path}'}), 400

    agent = (body.get('agent') or '').strip() or None
    model = (body.get('model') or '').strip() or None
    result = create_batch(g.current_user['userId'],
                          name=name, prompt=prompt,
                          template_id=template_id, files=files,
                          agent=agent, model=model)
    # Wake the worker so it picks up the new pending sessions immediately.
    from utils.batch_engine import get_worker
    get_worker().notify()
    return jsonify(result), 201


@ai_chat_batches_bp.get('')
@login_required
def list_():
    page = int(request.args.get('page', 1))
    page_size = min(int(request.args.get('pageSize', 20)), 100)
    return jsonify(list_batches(g.current_user['userId'],
                                page=page, page_size=page_size))


@ai_chat_batches_bp.get('/<batch_id>')
@login_required
def detail(batch_id):
    body = get_batch_detail(g.current_user['userId'], batch_id)
    if not body:
        return jsonify({'error': 'not found'}), 404
    return jsonify(body)


@ai_chat_batches_bp.delete('/<batch_id>')
@login_required
def remove(batch_id):
    # Tear down per-child workspaces before DB cascade
    body = get_batch_detail(g.current_user['userId'], batch_id)
    if not body:
        return jsonify({'error': 'not found'}), 404
    from utils.workspace import cleanup_session_workspace
    workspace_root = current_app.config.get('AI_CHAT_WORKSPACE_ROOT') \
        or os.environ.get('AI_CHAT_WORKSPACE_ROOT', 'ai-workspaces')
    for s in body['sessions']:
        try:
            cleanup_session_workspace(workspace_root,
                                      g.current_user['userId'], s['id'])
        except Exception:
            pass  # best-effort
    delete_batch(g.current_user['userId'], batch_id)
    return '', 204


@ai_chat_batches_bp.post('/<batch_id>/retry-failed')
@login_required
def retry_failed(batch_id):
    count = reset_failed_to_pending(g.current_user['userId'], batch_id)
    if count:
        from utils.batch_engine import get_worker
        get_worker().notify()
    return jsonify({'retried': count})


@ai_chat_batches_bp.post('/<batch_id>/append')
@login_required
def append(batch_id):
    body = request.get_json(silent=True) or {}
    files = body.get('files') or []
    if not isinstance(files, list) or not files:
        return jsonify({'error': 'at least one file required'}), 400
    for f in files:
        if not isinstance(f, dict) or not f.get('path') or not f.get('name'):
            return jsonify({'error': 'each file must have {name, path}'}), 400
    try:
        result = append_to_batch(g.current_user['userId'], batch_id, files)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if result is None:
        return jsonify({'error': 'not found'}), 404
    from utils.batch_engine import get_worker
    get_worker().notify()
    return jsonify(result)


@ai_chat_batches_bp.post('/<batch_id>/sessions/<session_id>/reexecute')
@login_required
def reexecute(batch_id, session_id):
    try:
        result = reexecute_child(g.current_user['userId'], batch_id, session_id)
    except ValueError as e:
        return jsonify({'error': str(e)}), 409
    if result is None:
        return jsonify({'error': 'not found'}), 404
    from utils.batch_engine import get_worker
    get_worker().notify()
    return jsonify(result)
