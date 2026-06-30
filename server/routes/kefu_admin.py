"""客服实例管理 API（需 admin.kefu）。"""
import re
from flask import Blueprint, request, jsonify
from auth import require_permission
from utils import kefu_repo
from utils.operation_log import log_operation

kefu_admin_bp = Blueprint('kefu_admin', __name__, url_prefix='/admin/kefu')

_SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,63}$')


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
