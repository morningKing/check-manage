"""AI 编排 REST 端点（/v1/ai-orchestrations，JWT；ai-harness-p2 spec §6.3）。

对外契约独立于 /v1/ai-batches；批任务继续独立使用（不强制升级为 DAG）。
定义发布与管理需要 admin；run 创建任何登录用户可用（归属 requested_by）。
"""
from flask import Blueprint, g as flask_g, jsonify, request

from auth import login_required, require_permission

from utils import orchestration_defs, orchestration_engine

ai_orchestrations_bp = Blueprint('ai_orchestrations', __name__,
                                 url_prefix='/v1/ai-orchestrations')


@ai_orchestrations_bp.get('/definitions')
@login_required
def list_definitions():
    return jsonify({'definitions': orchestration_defs.list_definitions()})


@ai_orchestrations_bp.post('/definitions')
@login_required
@require_permission('admin.ai_orchestration_admin')
def publish_definition():
    """发布（或升版）编排定义。校验失败 400；发布后不可变（编辑=新版本）。"""
    body = request.get_json(silent=True) or {}
    name = (body.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name required'}), 400
    try:
        out = orchestration_defs.publish_definition(
            name,
            description=(body.get('description') or '').strip() or None,
            nodes=body.get('nodes'),
            edges=body.get('edges') or [],
            owner_user_id=flask_g.current_user['userId'],
            retry_policy=body.get('retry_policy'),
            timeout_policy=body.get('timeout_policy'),
            budget_policy=body.get('budget_policy'),
            approval_policy=body.get('approval_policy'),
            compensation_policy=body.get('compensation_policy'))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify(out), 201


@ai_orchestrations_bp.post('/runs')
@login_required
def create_run():
    body = request.get_json(silent=True) or {}
    def_id = (body.get('definitionId') or body.get('definition_id') or '').strip()
    if not def_id:
        return jsonify({'error': 'definitionId required'}), 400
    try:
        run = orchestration_engine.create_run(
            def_id, flask_g.current_user['userId'],
            run_input=body.get('input') or {})
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    # 立即推进一次（scheduler 5s 内也会兜底）
    try:
        orchestration_engine._advance_run(run['id'])
        run = orchestration_engine.get_run(run['id'])
    except Exception:
        pass
    return jsonify(run), 201


@ai_orchestrations_bp.get('/runs')
@login_required
def list_runs():
    return jsonify({'runs': orchestration_engine.list_runs()})


@ai_orchestrations_bp.get('/runs/<run_id>')
@login_required
def get_run(run_id):
    run = orchestration_engine.get_run(run_id)
    if not run:
        return jsonify({'error': 'not found'}), 404
    return jsonify(run)


@ai_orchestrations_bp.get('/runs/<run_id>/events')
@login_required
def run_events(run_id):
    """run 的事件时间线（事实源与批任务同为 ai_batch_events，batch_id=run id）。"""
    from utils import batch_events
    if not orchestration_engine.get_run(run_id):
        return jsonify({'error': 'not found'}), 404
    try:
        after_seq = max(0, int(request.args.get('afterSeq', 0)))
    except (TypeError, ValueError):
        return jsonify({'error': 'afterSeq 必须是整数'}), 400
    rows = batch_events.read_events(run_id, after_seq=after_seq, limit=200)
    return jsonify({'runId': run_id, 'events': rows,
                    'nextAfterSeq': rows[-1]['event_seq'] if rows else after_seq})
