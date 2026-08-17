"""Prompt 模板对外 CRUD（/api/v1/prompt-templates/*）。

直接复用 utils/prompt_template.py 的五个函数——它们本来就以 user_id 为第一
参数、SQL 层面按 user_id 过滤，只需把内部路由（routes/ai_chat_prompt_templates.py）
里的 g.current_user['userId'] 换成 API Key 的 ownerUserId。校验逻辑和错误文案
（英文）照抄内部路由——那个文件本身用的就是英文 error，不是"AI 相关对外端点"
那个中文惯例家族（那是因为 RowActionError/ai_scan_tasks 的内部错误消息本身就是
硬编码中文，这里没有那个约束，直接复用内部的英文文案更省事也更一致）。
"""

from flask import Blueprint, g, jsonify, request

from auth import api_key_required, require_bound_key
from utils.prompt_template import (
    DuplicateTemplateName,
    create_template,
    delete_template,
    get_template,
    list_templates,
    update_template,
)

open_api_prompt_templates_bp = Blueprint(
    'open_api_prompt_templates', __name__, url_prefix='/v1/prompt-templates')


def _current_key() -> dict:
    return getattr(g, 'api_key_info', {}) or {}


def _payload():
    body = request.get_json(silent=True) or {}
    name = (body.get('name') or '').strip()
    content = (body.get('content') or '').strip()
    if not name or not content:
        return None, ('name and content required', 400)
    if len(name) > 200:
        return None, ('name too long', 400)
    return (name, content), None


def _template_out(row: dict) -> dict:
    """内部行 → 对外契约字段。不带 user_id——那是内部归属细节。"""
    return {
        'id': row['id'],
        'name': row['name'],
        'content': row['content'],
        'createdAt': row['created_at'].isoformat() if row.get('created_at') else None,
        'updatedAt': row['updated_at'].isoformat() if row.get('updated_at') else None,
    }


@open_api_prompt_templates_bp.get('')
@api_key_required
@require_bound_key
def list_():
    owner = _current_key()['ownerUserId']
    return jsonify({'templates': [_template_out(r) for r in list_templates(owner)]})


@open_api_prompt_templates_bp.post('')
@api_key_required
@require_bound_key
def create():
    owner = _current_key()['ownerUserId']
    parsed, err = _payload()
    if err:
        msg, code = err
        return jsonify({'error': msg}), code
    name, content = parsed
    try:
        row = create_template(owner, name=name, content=content)
    except DuplicateTemplateName:
        return jsonify({'error': 'name already in use'}), 409
    return jsonify(_template_out(row)), 201


@open_api_prompt_templates_bp.get('/<template_id>')
@api_key_required
@require_bound_key
def get(template_id):
    owner = _current_key()['ownerUserId']
    row = get_template(owner, template_id)
    if not row:
        return jsonify({'error': 'not found'}), 404
    return jsonify(_template_out(row))


@open_api_prompt_templates_bp.put('/<template_id>')
@api_key_required
@require_bound_key
def update(template_id):
    owner = _current_key()['ownerUserId']
    parsed, err = _payload()
    if err:
        msg, code = err
        return jsonify({'error': msg}), code
    name, content = parsed
    try:
        row = update_template(owner, template_id, name=name, content=content)
    except DuplicateTemplateName:
        return jsonify({'error': 'name already in use'}), 409
    if not row:
        return jsonify({'error': 'not found'}), 404
    return jsonify(_template_out(row))


@open_api_prompt_templates_bp.delete('/<template_id>')
@api_key_required
@require_bound_key
def delete(template_id):
    owner = _current_key()['ownerUserId']
    if not delete_template(owner, template_id):
        return jsonify({'error': 'not found'}), 404
    return '', 204
