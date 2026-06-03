from flask import Blueprint, request, jsonify, g
from db import get_db
from auth import require_permission
from utils import ai_scan_repo
from utils.operation_log import log_operation

ai_scan_tasks_bp = Blueprint('ai_scan_tasks', __name__)


@ai_scan_tasks_bp.route('/ai-scan-tasks', methods=['GET'])
@require_permission('admin.ai_scan')
def list_tasks():
    return jsonify(ai_scan_repo.list_tasks())


@ai_scan_tasks_bp.route('/ai-scan-tasks/<task_id>', methods=['GET'])
@require_permission('admin.ai_scan')
def get_task(task_id):
    t = ai_scan_repo.get_task(task_id)
    return (jsonify(t), 200) if t else (jsonify({'error': '任务不存在'}), 404)


@ai_scan_tasks_bp.route('/ai-scan-tasks', methods=['POST'])
@require_permission('admin.ai_scan')
def create_task():
    body = request.get_json(force=True)
    for k in ('name', 'collection', 'statusField', 'promptTemplate'):
        if not body.get(k):
            return jsonify({'error': f'缺少必填项：{k}'}), 400
    t = ai_scan_repo.create_task(body, g.current_user['userId'])
    log_operation('create', 'ai_scan_task', t['id'], t['name'], f'新增 AI 定时任务「{t["name"]}」')
    return jsonify(t), 201


@ai_scan_tasks_bp.route('/ai-scan-tasks/<task_id>', methods=['PUT'])
@require_permission('admin.ai_scan')
def update_task(task_id):
    body = request.get_json(force=True)
    t = ai_scan_repo.update_task(task_id, body)
    if not t:
        return jsonify({'error': '任务不存在'}), 404
    log_operation('update', 'ai_scan_task', task_id, t['name'], f'更新 AI 定时任务「{t["name"]}」')
    return jsonify(t)


@ai_scan_tasks_bp.route('/ai-scan-tasks/<task_id>', methods=['DELETE'])
@require_permission('admin.ai_scan')
def delete_task(task_id):
    ok = ai_scan_repo.delete_task(task_id)
    if not ok:
        return jsonify({'error': '任务不存在'}), 404
    log_operation('delete', 'ai_scan_task', task_id, task_id, f'删除 AI 定时任务「{task_id}」')
    return jsonify({})


@ai_scan_tasks_bp.route('/ai-scan-tasks/<task_id>/run-now', methods=['POST'])
@require_permission('admin.ai_scan')
def run_now(task_id):
    t = ai_scan_repo.get_task(task_id)
    if not t:
        return jsonify({'error': '任务不存在'}), 404
    from utils.ai_scan_engine import run_task
    try:
        run_task(t)
    except Exception as e:
        return jsonify({'error': f'运行失败：{e}'}), 500
    return jsonify({'message': '已触发一次扫描'})
