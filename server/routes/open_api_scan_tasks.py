"""AI 定时扫描任务对外触发（/api/v1/ai-scan-tasks/*）。

内部 run-now（routes/ai_scan_tasks.py::run_now）完全是 admin.ai_scan JWT 权限门，
本蓝图给它加一套 API Key 归属过滤的等价物——按 ai_scan_tasks.owner_user_id 与
密钥的 ownerUserId 匹配来判定"这把密钥能不能碰这个任务"，同 open_api_batches.py
的归属模型。error 文案沿用 ai-batch-api.md 的中文惯例（这是同一个"AI 相关对外
端点"家族），不套用 open-api.md 的英文惯例。

不检查 task['enabled']——内部 run-now 路由本来就不检查，对外包装层不引入内部
没有的新规则，保持行为一致。
"""

from flask import Blueprint, g, jsonify

from auth import api_key_required, require_bound_key
from utils import ai_scan_repo
from utils.api_errors import INTERNAL_ERROR, NOT_FOUND, err, register_error_handlers
from utils.operation_log import log_api_operation

open_api_scan_tasks_bp = Blueprint(
    'open_api_scan_tasks', __name__, url_prefix='/v1/ai-scan-tasks')
register_error_handlers(open_api_scan_tasks_bp)


def _current_key() -> dict:
    return getattr(g, 'api_key_info', {}) or {}


def _task_out(t: dict) -> dict:
    """内部任务行 → 对外契约字段。不带 promptTemplate/fieldMapping/extraFilter/
    contextFields/statusField/各状态值/ownerUserId 这些内部配置细节。"""
    return {
        'id': t['id'],
        'name': t.get('name'),
        'enabled': t.get('enabled'),
        'collection': t.get('collection'),
        'branchId': t.get('branchId'),
        'scheduleIntervalMinutes': t.get('scheduleIntervalMinutes'),
        'maxRecordsPerScan': t.get('maxRecordsPerScan'),
        'lastRunAt': t.get('lastRunAt'),
        'lastScanCount': t.get('lastScanCount'),
        'lastError': t.get('lastError'),
    }


@open_api_scan_tasks_bp.get('')
@api_key_required
@require_bound_key
def list_tasks():
    owner = _current_key()['ownerUserId']
    tasks = ai_scan_repo.list_tasks_for_owner(owner)
    return jsonify({'tasks': [_task_out(t) for t in tasks]})


@open_api_scan_tasks_bp.get('/<task_id>')
@api_key_required
@require_bound_key
def get_task(task_id):
    owner = _current_key()['ownerUserId']
    t = ai_scan_repo.get_task_for_owner(task_id, owner)
    if not t:
        return err('任务不存在', NOT_FOUND, 404)
    return jsonify(_task_out(t))


@open_api_scan_tasks_bp.post('/<task_id>/run-now')
@api_key_required
@require_bound_key
def run_now(task_id):
    owner = _current_key()['ownerUserId']
    t = ai_scan_repo.get_task_for_owner(task_id, owner)
    if not t:
        return err('任务不存在', NOT_FOUND, 404)

    from utils.ai_scan_engine import run_task
    try:
        run_task(t)
    except Exception:
        import traceback
        traceback.print_exc()
        return err('运行失败，请查看服务端日志', INTERNAL_ERROR, 500)

    t2 = ai_scan_repo.get_task_for_owner(task_id, owner) or t
    log_api_operation('update', 'ai_scan_task', task_id, t.get('name'),
                      f'通过 API Key 立即运行扫描任务「{t.get("name")}」')
    return jsonify({
        'triggered': True,
        'claimedCount': t2.get('lastScanCount'),
        'lastError': t2.get('lastError'),
    })
