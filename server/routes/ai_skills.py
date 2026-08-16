"""Admin API for global skill management.

Endpoints (all require admin.ai_settings):
  GET    /ai/skills              — list all global skills
  GET    /ai/skills/<id>         — get single skill detail
  POST   /ai/skills              — upload new skill (zip)
  PUT    /ai/skills/<id>         — update description / enabled
  DELETE /ai/skills/<id>         — delete skill
  GET    /ai/skills/<id>/files   — list files inside skill
  GET    /ai/skills/<id>/files/<path> — read file content
"""
import os

from flask import Blueprint, g, jsonify, request
from auth import require_permission
from utils.global_skills import (
    delete_global_skill,
    get_global_skill,
    install_skill_from_zip,
    list_global_skills,
    list_skill_files,
    read_skill_file,
    update_global_skill,
    MAX_SKILL_ZIP_BYTES,
)

ai_skills_bp = Blueprint('ai_skills', __name__, url_prefix='/ai/skills')


def _row_out(r: dict) -> dict:
    """Convert DB row to API contract."""
    return {
        'id': r['id'],
        'name': r['name'],
        'description': r.get('description', ''),
        'enabled': r.get('enabled', True),
        'uploadedBy': r.get('uploader_name') or r.get('uploaded_by'),
        'fileSize': r.get('file_size', 0),
        'createdAt': r['created_at'].isoformat() if r.get('created_at') else None,
        'updatedAt': r['updated_at'].isoformat() if r.get('updated_at') else None,
    }


@ai_skills_bp.get('')
@require_permission('admin.ai_settings')
def list_skills():
    skills = list_global_skills()
    return jsonify({'skills': [_row_out(s) for s in skills]})


@ai_skills_bp.get('/<skill_id>')
@require_permission('admin.ai_settings')
def get_skill(skill_id):
    skill = get_global_skill(skill_id)
    if not skill:
        return jsonify({'error': '技能不存在'}), 404
    return jsonify(_row_out(skill))


@ai_skills_bp.post('')
@require_permission('admin.ai_settings')
def create_skill():
    if 'file' not in request.files:
        return jsonify({'error': '请上传 zip 文件'}), 400
    f = request.files['file']
    if not f.filename:
        return jsonify({'error': '文件名为空'}), 400

    description = (request.form.get('description') or '').strip()

    # Save to temp file
    import tempfile
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    try:
        f.save(tmp)
        tmp.close()
        user_id = g.current_user.get('userId') or g.current_user.get('id')
        skill = install_skill_from_zip(tmp.name, description, user_id,
                                       os.environ.get('AI_CHAT_WORKSPACE_ROOT', 'ai-workspaces'))
        return jsonify(_row_out(skill)), 201
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


@ai_skills_bp.put('/<skill_id>')
@require_permission('admin.ai_settings')
def update_skill(skill_id):
    body = request.get_json(silent=True) or {}
    description = body.get('description')
    enabled = body.get('enabled')
    if description is not None:
        description = str(description).strip()
    if enabled is not None:
        enabled = bool(enabled)
    skill = update_global_skill(skill_id, description=description, enabled=enabled)
    if not skill:
        return jsonify({'error': '技能不存在'}), 404
    return jsonify(_row_out(skill))


@ai_skills_bp.delete('/<skill_id>')
@require_permission('admin.ai_settings')
def delete_skill(skill_id):
    ws = os.environ.get('AI_CHAT_WORKSPACE_ROOT', 'ai-workspaces')
    if not delete_global_skill(skill_id, ws):
        return jsonify({'error': '技能不存在'}), 404
    return jsonify({'deleted': True})


@ai_skills_bp.get('/<skill_id>/files')
@require_permission('admin.ai_settings')
def skill_files(skill_id):
    ws = os.environ.get('AI_CHAT_WORKSPACE_ROOT', 'ai-workspaces')
    skill = get_global_skill(skill_id)
    if not skill:
        return jsonify({'error': '技能不存在'}), 404
    files = list_skill_files(skill_id, ws)
    return jsonify({'files': files})


@ai_skills_bp.get('/<skill_id>/files/<path:file_path>')
@require_permission('admin.ai_settings')
def skill_file_content(skill_id, file_path):
    ws = os.environ.get('AI_CHAT_WORKSPACE_ROOT', 'ai-workspaces')
    result = read_skill_file(skill_id, file_path, ws)
    if result is None:
        return jsonify({'error': '文件不存在'}), 404
    return jsonify(result)
