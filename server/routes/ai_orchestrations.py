"""AI 编排 REST 端点（/ai/orchestrations 内部域，JWT；ai-harness-p2 spec §6.3；/v1/ai-orchestrations 为对外 API Key 契约——前缀冲突修复）。

对外契约独立于 /v1/ai-batches；批任务继续独立使用（不强制升级为 DAG）。
定义发布与管理需要 admin；run 创建任何登录用户可用（归属 requested_by）。
"""
from flask import Blueprint, g as flask_g, jsonify, request

from auth import login_required, require_permission

from utils import orchestration_defs, orchestration_engine

ai_orchestrations_bp = Blueprint('ai_orchestrations', __name__,
                                 url_prefix='/ai/orchestrations')


@ai_orchestrations_bp.get('/definitions')
@login_required
def list_definitions():
    return jsonify({'definitions': orchestration_defs.list_definitions()})


@ai_orchestrations_bp.get('/definitions/<def_id>')
@login_required
def get_definition(def_id):
    """定义详情（P3 DAG 编辑器加载 nodes/edges；缺省最新已发布版本）。"""
    d = orchestration_defs.get_definition(def_id)
    if not d:
        return jsonify({'error': 'not found'}), 404
    return jsonify(d)


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


@ai_orchestrations_bp.get('/runs/<run_id>/graph')
@login_required
@require_permission('admin.ai_orchestration_admin')
def run_graph(run_id):
    """run graph（P2 §10/§13，缺口补齐 9）：节点状态/耗时/聚合可观测一次返回。"""
    g_ = orchestration_engine.run_graph(run_id)
    if not g_:
        return jsonify({'error': 'not found'}), 404
    return jsonify(g_)


@ai_orchestrations_bp.post('/runs/<run_id>/suspend')
@login_required
@require_permission('admin.ai_orchestration_admin')
def suspend_step(run_id):
    """人工挂起（P2 §7.1，缺口补齐 3）：运行中的 step 挂起进审批——
    暂停其子会话（pause_requested）并建人工审批请求；approve 后继续。"""
    run = orchestration_engine.get_run(run_id)
    if not run or run['status'] not in ('running', 'waiting_approval'):
        return jsonify({'error': 'run not active'}), 409
    body = request.get_json(silent=True) or {}
    step_id = (body.get('stepId') or '').strip()
    step = next((s for s in run['steps'] if s['id'] == step_id), None)
    if not step or step['status'] != 'running':
        return jsonify({'error': 'step not running'}), 409
    if not step.get('session_id'):
        return jsonify({'error': 'step has no child session'}), 409
    from utils.batch_repo import pause_batch
    pause_batch(run['requested_by'], step.get('batch_id'))         if step.get('batch_id') else None
    from utils import approval_repo
    aid = approval_repo.create_approval(
        run_id=run_id, step_id=step_id, risk_level='high',
        effect_summary=f'人工挂起: {step.get("name") or step["node_id"]}')
    return jsonify({'approvalId': aid}), 201


@ai_orchestrations_bp.post('/runs/<run_id>/mode')
@login_required
def set_execution_mode(run_id):
    """P3 单步调试：切换 run 执行模式（auto=自动推进 / single_step=单步）。"""
    run = orchestration_engine.get_run(run_id)
    if not run or run['status'] not in ('running', 'waiting_approval', 'pending'):
        return jsonify({'error': 'run not active'}), 409
    body = request.get_json(silent=True) or {}
    mode = body.get('mode')
    if mode not in ('auto', 'single_step'):
        return jsonify({'error': "mode must be 'auto' or 'single_step'"}), 400
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ai_orchestration_runs SET execution_mode = %s "
                "WHERE id = %s", (mode, run_id))
        conn.commit()
    return jsonify({'runId': run_id, 'executionMode': mode})


@ai_orchestrations_bp.post('/runs/<run_id>/advance')
@login_required
def advance_step(run_id):
    """P3 单步调试：手动推进一个 runnable step（仅 single_step 模式有意义）。"""
    run = orchestration_engine.get_run(run_id)
    if not run or run['status'] not in ('running', 'waiting_approval', 'pending'):
        return jsonify({'error': 'run not active'}), 409
    # 临时切 auto → _advance_run 派发 1 个 step → 切回 single_step
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ai_orchestration_runs SET execution_mode = 'auto' WHERE id = %s",
                (run_id,))
        conn.commit()
    try:
        orchestration_engine._advance_run(run_id)
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_orchestration_runs SET execution_mode = 'single_step' "
                    "WHERE id = %s AND execution_mode = 'auto'", (run_id,))
            conn.commit()
    fresh = orchestration_engine.get_run(run_id)
    return jsonify(fresh)
