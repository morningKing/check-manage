"""客服实例管理 API（需 admin.kefu）。"""
import re
from flask import Blueprint, request, jsonify
from auth import require_permission
from utils import kefu_repo
from utils.operation_log import log_operation

kefu_admin_bp = Blueprint('kefu_admin', __name__, url_prefix='/admin/kefu')

_SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,63}$')
_BLOCK_TYPES = {'links', 'faq', 'richtext', 'contact'}


def _validate_panel_blocks(v):
    if not isinstance(v, list):
        return 'panel_blocks 必须是数组'
    for b in v:
        if not isinstance(b, dict) or b.get('type') not in _BLOCK_TYPES:
            return 'panel_blocks 每项需为对象且 type 合法'
    return None


@kefu_admin_bp.route('/instances', methods=['GET'])
@require_permission('admin.kefu')
def list_instances():
    return jsonify({'instances': kefu_repo.list_instances()})


@kefu_admin_bp.route('/instances', methods=['POST'])
@require_permission('admin.kefu')
def create_instance():
    body = request.get_json(silent=True) or {}
    slug = (body.get('slug') or '').strip()
    name = (body.get('name') or '').strip()
    if not _SLUG_RE.match(slug) or not name:
        return jsonify({'error': 'slug 需为小写字母/数字/连字符，name 必填'}), 400
    if kefu_repo.get_instance_by_slug(slug):
        return jsonify({'error': 'slug 已存在'}), 409
    inst = kefu_repo.create_instance(body)
    log_operation('create', 'kefu_instance', inst['id'], inst['name'], '创建客服实例')
    return jsonify(inst), 201


@kefu_admin_bp.route('/instances/<iid>', methods=['GET'])
@require_permission('admin.kefu')
def get_instance(iid):
    inst = kefu_repo.get_instance(iid)
    if not inst:
        return jsonify({'error': 'not found'}), 404
    return jsonify(inst)


@kefu_admin_bp.route('/instances/<iid>', methods=['PATCH'])
@require_permission('admin.kefu')
def update_instance(iid):
    body = request.get_json(silent=True) or {}
    if 'slug' in body and not _SLUG_RE.match((body.get('slug') or '').strip()):
        return jsonify({'error': 'slug 非法'}), 400
    if 'panel_blocks' in body:
        err = _validate_panel_blocks(body['panel_blocks'])
        if err:
            return jsonify({'error': err}), 400
    inst = kefu_repo.update_instance(iid, body)
    if not inst:
        return jsonify({'error': 'not found'}), 404
    log_operation('update', 'kefu_instance', iid, inst['name'], '更新客服实例')
    return jsonify(inst)


@kefu_admin_bp.route('/instances/<iid>', methods=['DELETE'])
@require_permission('admin.kefu')
def delete_instance(iid):
    ok = kefu_repo.delete_instance(iid)
    if not ok:
        return jsonify({'error': 'not found'}), 404
    log_operation('delete', 'kefu_instance', iid, iid, '删除客服实例')
    return jsonify({'ok': True})


def _faq_owned(iid, fid):
    faq = kefu_repo.get_faq(fid)
    return faq if (faq and faq['instance_id'] == iid) else None


@kefu_admin_bp.route('/instances/<iid>/faq', methods=['GET'])
@require_permission('admin.kefu')
def list_faq(iid):
    return jsonify({'items': kefu_repo.list_faq_admin(iid)})


@kefu_admin_bp.route('/instances/<iid>/faq', methods=['POST'])
@require_permission('admin.kefu')
def create_faq(iid):
    if not kefu_repo.get_instance(iid):
        return jsonify({'error': 'instance not found'}), 404
    body = request.get_json(silent=True) or {}
    if not (body.get('question') or '').strip() or not (body.get('answer') or '').strip():
        return jsonify({'error': 'question 与 answer 必填'}), 400
    faq = kefu_repo.create_faq(iid, body)
    log_operation('create', 'kefu_faq_item', faq['id'], faq['question'][:50], '新建热问')
    return jsonify(faq), 201


@kefu_admin_bp.route('/instances/<iid>/faq/reorder', methods=['PATCH'])
@require_permission('admin.kefu')
def reorder_faq(iid):
    order = (request.get_json(silent=True) or {}).get('order')
    if not isinstance(order, list):
        return jsonify({'error': 'order must be a list'}), 400
    kefu_repo.reorder_faq(iid, order)
    return jsonify({'ok': True})


@kefu_admin_bp.route('/instances/<iid>/faq/<fid>', methods=['PATCH'])
@require_permission('admin.kefu')
def update_faq(iid, fid):
    if not _faq_owned(iid, fid):
        return jsonify({'error': 'not found'}), 404
    faq = kefu_repo.update_faq(fid, request.get_json(silent=True) or {})
    if not faq:
        return jsonify({'error': 'not found'}), 404
    log_operation('update', 'kefu_faq_item', fid, faq['question'][:50], '更新热问')
    return jsonify(faq)


@kefu_admin_bp.route('/instances/<iid>/faq/<fid>', methods=['DELETE'])
@require_permission('admin.kefu')
def delete_faq(iid, fid):
    if not _faq_owned(iid, fid):
        return jsonify({'error': 'not found'}), 404
    kefu_repo.delete_faq(fid)
    log_operation('delete', 'kefu_faq_item', fid, fid, '删除热问')
    return jsonify({'ok': True})
