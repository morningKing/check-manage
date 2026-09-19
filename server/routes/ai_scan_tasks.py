from flask import Blueprint, request, jsonify, g
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
    except Exception:
        import traceback
        traceback.print_exc()
        return jsonify({'error': '运行失败，请查看服务端日志'}), 500
    log_operation('run', 'ai_scan_task', task_id, t['name'], f'手动触发 AI 定时任务「{t["name"]}」')
    return jsonify({'message': '已触发一次扫描'})


@ai_scan_tasks_bp.route('/ai-scan-tasks/<task_id>/sessions/<sid>/import-outputs',
                        methods=['POST'])
@require_permission('admin.ai_scan')
def import_scan_outputs(task_id, sid):
    """手动触发：把子会话工作区 outputs/ 下的产物导入数据行
    （execution-audit Spec「AI 产出文件→数据行呈现」）。幂等：重复调用
    只会重复导入新出现的文件。"""
    from utils.ai_scan_engine import _load_task, _import_child_outputs_to_record
    task = _load_task(task_id)
    if not task:
        return jsonify({'error': 'task not found'}), 404
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            # 子会话通过 scan_task_id 关联到定时任务（一个任务多次运行产生多个批次）
            cur.execute(
                "SELECT id, user_id, workspace_path FROM ai_chat_sessions "
                "WHERE id = %s AND scan_task_id = %s", (sid, task_id))
            row = cur.fetchone()
    if not row:
        return jsonify({'error': 'session not in this task'}), 404
    session_row = {'id': row[0], 'user_id': row[1], 'workspace_path': row[2]}
    if not task.get('output_file_field'):
        return jsonify({'error': '任务未配置 output_file_field'}), 400
    n = _import_child_outputs_to_record(task, row[2], session_row,
                                        task['output_file_field'])
    from utils.operation_log import log_operation
    log_operation('update', 'ai_scan_task', task_id, sid,
                  f'手动导入产物 {n} 个文件')
    return jsonify({'imported': n})


