"""REST endpoints for AI chat prompt templates (per-user CRUD)."""
from flask import Blueprint, g, jsonify, request

from auth import login_required
from utils.agent_ledger import validate_checks
from utils.operation_log import log_operation
from utils.prompt_template import (
    DuplicateTemplateName,
    create_template,
    delete_template,
    get_template,
    list_templates,
    update_template,
)

ai_chat_prompt_templates_bp = Blueprint(
    'ai_chat_prompt_templates', __name__,
    url_prefix='/ai/chat/prompt-templates',
)


def _payload():
    body = request.get_json(silent=True) or {}
    name = (body.get('name') or '').strip()
    content = (body.get('content') or '').strip()
    if not name or not content:
        return None, ('name and content required', 400)
    if len(name) > 200:
        return None, ('name too long', 400)
    # 入口 B(设计 §5.2):模板可携带动作门禁期望,从模板创建的批任务自动继承
    try:
        checks = validate_checks(body.get('action_checks'))
    except ValueError as e:
        return None, (str(e), 400)
    return (name, content, checks or None), None


@ai_chat_prompt_templates_bp.get('')
@login_required
def list_():
    return jsonify(list_templates(g.current_user['userId']))


@ai_chat_prompt_templates_bp.post('')
@login_required
def create():
    parsed, err = _payload()
    if err:
        msg, code = err
        return jsonify({'error': msg}), code
    name, content, checks = parsed
    try:
        row = create_template(g.current_user['userId'], name=name,
                              content=content, action_checks=checks)
    except DuplicateTemplateName:
        return jsonify({'error': 'name already in use'}), 409
    log_operation('create', 'ai_prompt_template', row.get('id'), name,
                  '新建提示词模板')
    return jsonify(row), 201


@ai_chat_prompt_templates_bp.get('/<template_id>')
@login_required
def get(template_id):
    row = get_template(g.current_user['userId'], template_id)
    if not row:
        return jsonify({'error': 'not found'}), 404
    return jsonify(row)


@ai_chat_prompt_templates_bp.put('/<template_id>')
@login_required
def update(template_id):
    parsed, err = _payload()
    if err:
        msg, code = err
        return jsonify({'error': msg}), code
    name, content, checks = parsed
    try:
        row = update_template(g.current_user['userId'], template_id,
                              name=name, content=content, action_checks=checks)
    except DuplicateTemplateName:
        return jsonify({'error': 'name already in use'}), 409
    if not row:
        return jsonify({'error': 'not found'}), 404
    log_operation('update', 'ai_prompt_template', template_id, name,
                  '更新提示词模板')
    return jsonify(row)


@ai_chat_prompt_templates_bp.delete('/<template_id>')
@login_required
def delete(template_id):
    if not delete_template(g.current_user['userId'], template_id):
        return jsonify({'error': 'not found'}), 404
    log_operation('delete', 'ai_prompt_template', template_id, template_id,
                  '删除提示词模板')
    return '', 204
