"""REST endpoints for AI chat batch tasks (CRUD + staging upload).

Worker engine lives in utils.batch_engine; this module only owns the HTTP edge.
"""
import uuid
from pathlib import Path

from flask import Blueprint, current_app, g, jsonify, request
from utils.filename import safe_filename

from auth import login_required
from utils.workspace import (batch_staging_dir, batch_workspace_root,
                             cleanup_batch_workspaces, validate_staged_files,
                             WorkspacePathError)
from utils import agent_ledger
from utils.batch_repo import (
    append_to_batch,
    get_max_files_per_batch,
    cancel_batch,
    cancel_child,
    create_batch,
    delete_batch,
    get_batch_detail,
    list_batches,
    pause_batch,
    reexecute_child,
    reset_failed_to_pending,
    resume_batch,
    resume_child,
    update_batch_config,
)


ai_chat_batches_bp = Blueprint('ai_chat_batches', __name__,
                               url_prefix='/ai/chat/batches')


@ai_chat_batches_bp.post('/staging/upload')
@login_required
def staging_upload():
    f = request.files.get('file')
    upload_session_id = (request.form.get('upload_session_id') or '').strip()
    if not f or not upload_session_id:
        return jsonify({'error': 'file and upload_session_id required'}), 400

    filename = safe_filename(f.filename or '')  # preserves Unicode (e.g. 中文) names

    workspace_root = current_app.config.get('AI_CHAT_WORKSPACE_ROOT') \
        or batch_workspace_root()
    try:
        staging = batch_staging_dir(workspace_root,
                                    g.current_user['userId'],
                                    upload_session_id)
    except WorkspacePathError as e:
        return jsonify({'error': str(e)}), 400

    dest = staging / filename
    f.save(str(dest))

    rel = dest.relative_to(workspace_root).as_posix()
    return jsonify({'name': filename, 'path': rel}), 201


@ai_chat_batches_bp.post('')
@login_required
def create():
    body = request.get_json(silent=True) or {}
    name = (body.get('name') or '').strip()
    prompt = (body.get('prompt') or '').strip()
    template_id = body.get('template_id')
    files = body.get('files') or []
    if not name or not prompt:
        return jsonify({'error': 'name and prompt required'}), 400
    if not isinstance(files, list) or not files:
        return jsonify({'error': 'at least one file required'}), 400
    max_files = get_max_files_per_batch()
    if len(files) > max_files:
        return jsonify({'error': f'每个批任务最多 {max_files} 个子会话（文件）'}), 400
    for f in files:
        if not isinstance(f, dict) or not f.get('path') or not f.get('name'):
            return jsonify({'error': 'each file must have {name, path}'}), 400
    # Path containment: a hostile client used to be able to hand the worker an
    # absolute path or batch-staging/<other-user>/... and have it copied into
    # its own workspace (same check the external API always had).
    path_err = validate_staged_files(files, g.current_user['userId'])
    if path_err:
        return jsonify({'error': path_err}), 400

    agent = (body.get('agent') or '').strip() or None
    model = (body.get('model') or '').strip() or None
    provision_repo = (body.get('provision_repo') or '').strip() or None
    provision_ref = (body.get('provision_ref') or '').strip() or None
    # 入口 A(设计 §5.2):批定义上的动作门禁期望;正则可编译性等在此校验,
    # 非法直接 400,不带病入库。
    try:
        action_checks = agent_ledger.validate_checks(body.get('action_checks'))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    result = create_batch(g.current_user['userId'],
                          name=name, prompt=prompt,
                          template_id=template_id, files=files,
                          agent=agent, model=model,
                          provision_repo=provision_repo, provision_ref=provision_ref,
                          action_checks=action_checks or None)
    # Wake the worker so it picks up the new pending sessions immediately.
    from utils.batch_engine import get_worker
    get_worker().notify()
    return jsonify(result), 201


@ai_chat_batches_bp.get('')
@login_required
def list_():
    page = int(request.args.get('page', 1))
    page_size = min(int(request.args.get('pageSize', 20)), 100)
    result = list_batches(g.current_user['userId'],
                          page=page, page_size=page_size)
    # 可配置的子会话个数上限随列表下发：新建/追加对话框的客户端预检用
    # （/ai/settings 是管理员接口，普通用户读不到自己的可用上限）
    result['maxSessions'] = get_max_files_per_batch()
    return jsonify(result)


@ai_chat_batches_bp.get('/<batch_id>')
@login_required
def detail(batch_id):
    body = get_batch_detail(g.current_user['userId'], batch_id)
    if not body:
        return jsonify({'error': 'not found'}), 404
    return jsonify(body)


def _authorize_child(batch_id: str, sid: str) -> str | None:
    """批任务归属 + 子会话归属校验;通过返回子会话的 oc_session_id,否则 None。"""
    from db import get_db as _get_db
    user_id = g.current_user['userId']
    if not get_batch_detail(user_id, batch_id):
        return None
    with _get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT opencode_session_id FROM ai_chat_sessions "
                "WHERE id = %s AND batch_id = %s",
                (sid, batch_id),
            )
            row = cur.fetchone()
    return row[0] if row else None


@ai_chat_batches_bp.get('/<batch_id>/children/<sid>/tool-calls')
@login_required
def child_tool_calls(batch_id, sid):
    """动作账本查询(设计 §5.2 编写辅助):该子会话及其子代理树实际发生的
    工具调用,按时间序;写正则时从这里取材。"""
    oc_sid = _authorize_child(batch_id, sid)
    if oc_sid is None:
        return jsonify({'error': 'not found'}), 404
    from db import get_db as _get_db
    with _get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM ai_chat_subtasks WHERE root_session_id = %s",
                (sid,),
            )
            ids = [oc_sid] + [r[0] for r in cur.fetchall()]
            cur.execute(
                """
                SELECT oc_session_id, subtask_id, tool, args_text, state, occurred_at
                FROM agent_tool_calls
                WHERE oc_session_id = ANY(%s)
                ORDER BY occurred_at, id
                """,
                (ids,),
            )
            calls = [
                {'ocSessionId': r[0], 'subtaskId': r[1], 'tool': r[2],
                 'args': (r[3] or '')[:2000], 'state': r[4],
                 'occurredAt': r[5].isoformat() if r[5] else None}
                for r in cur.fetchall()
            ]
    return jsonify({'calls': calls})


@ai_chat_batches_bp.post('/<batch_id>/children/<sid>/gate/dry-run')
@login_required
def gate_dry_run(batch_id, sid):
    """试跑核对(设计 §5.2 编写辅助):保存期望前按 tree 作用域对现有账本
    跑一次匹配,返回命中数与样例,验证正则写得对不对。"""
    oc_sid = _authorize_child(batch_id, sid)
    if oc_sid is None:
        return jsonify({'error': 'not found'}), 404
    body = request.get_json(silent=True) or {}
    tool = (body.get('tool') or '').strip()
    pattern = (body.get('args_pattern') or '').strip()
    require_state = (body.get('require_state') or 'completed').strip()
    if not tool or not pattern:
        return jsonify({'error': 'tool and args_pattern required'}), 400
    import re as _re
    try:
        _re.compile(pattern)
    except _re.error as e:
        return jsonify({'error': f'args_pattern 不是合法正则: {e}'}), 400
    return jsonify(agent_ledger.count_tree_tool_calls(
        sid, tool, pattern, require_state))


@ai_chat_batches_bp.post('/action-checks/extract')
@login_required
def extract_action_checks():
    """AI 自动提炼(设计 §5.2,入口 A/B 的预填器):分析任务文本 + Agent +
    Skill 步骤,产出门禁期望建议。只产出建议,登记仍走创建/模板保存的人工确认。"""
    body = request.get_json(silent=True) or {}
    task_text = (body.get('task_text') or '').strip()
    if not task_text:
        return jsonify({'error': 'task_text required'}), 400
    from utils.action_check_extractor import extract_action_checks
    try:
        checks = extract_action_checks(task_text,
                                       agent=body.get('agent'),
                                       skills=body.get('skills'))
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 502
    except ValueError as e:
        return jsonify({'error': f'提炼结果未通过校验: {e}'}), 502
    return jsonify({'checks': checks})


@ai_chat_batches_bp.post('/<batch_id>/action-checks/attach')
@login_required
def attach_action_checks(batch_id):
    """入口 D(设计 §5.2):对批任务下指定子会话补挂期望(运行中/排队中均可,
    终态核对时生效)。管理员专用;批量补挂逐会话登记,幂等。"""
    user_id = g.current_user['userId']
    if g.current_user.get('role') != 'admin':
        return jsonify({'error': 'admin only'}), 403
    if not get_batch_detail(user_id, batch_id):
        return jsonify({'error': 'not found'}), 404
    body = request.get_json(silent=True) or {}
    session_ids = body.get('session_ids') or []
    try:
        checks = agent_ledger.validate_checks(body.get('checks'))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if not session_ids or not checks:
        return jsonify({'error': 'session_ids and checks required'}), 400
    from db import get_db as _get_db
    with _get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM ai_chat_sessions "
                "WHERE batch_id = %s AND id = ANY(%s)",
                (batch_id, session_ids),
            )
            valid = [r[0] for r in cur.fetchall()]
    registered = 0
    for sid in valid:
        registered += agent_ledger.register_session_expectations(
            sid, checks, source='batch')
    return jsonify({'registered': registered, 'sessions': len(valid)})


@ai_chat_batches_bp.patch('/<batch_id>')
@login_required
def update_config(batch_id):
    body = request.get_json(silent=True) or {}
    agent = (body.get('agent') or '').strip() or None
    model = (body.get('model') or '').strip() or None
    provision_repo = (body.get('provision_repo') or '').strip() or None
    provision_ref = (body.get('provision_ref') or '').strip() or None
    result = update_batch_config(g.current_user['userId'], batch_id, agent=agent, model=model,
                                 provision_repo=provision_repo, provision_ref=provision_ref)
    if result is None:
        return jsonify({'error': 'not found'}), 404
    # 编辑动作门禁(设计 §5.2 入口 A):显式传入 action_checks 才更新;
    # 未终态子任务同步替换期望,已终态子任务的历史核对结果保持原样。
    if 'action_checks' in body:
        try:
            checks = agent_ledger.validate_checks(body.get('action_checks'))
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        agent_ledger.sync_batch_expectations(batch_id, checks or None)
    return jsonify(result)


@ai_chat_batches_bp.delete('/<batch_id>')
@login_required
def remove(batch_id):
    """Delete a batch. P0 spec 10.4: a non-terminal batch (pending/running/
    paused) cannot be deleted directly — the caller must pass stop=1 to run
    "stop then delete", which cancels every child first and waits (bounded)
    for running children to land before tearing workspaces down."""
    user_id = g.current_user['userId']
    body = get_batch_detail(user_id, batch_id)
    if not body:
        return jsonify({'error': 'not found'}), 404
    status = body['batch']['status']
    stop_first = request.args.get('stop', '').lower() in ('1', 'true') \
        or bool((request.get_json(silent=True) or {}).get('stop'))
    if status not in ('completed', 'partial', 'failed'):
        if not stop_first:
            return jsonify({'error': {
                'code': 'BATCH_NOT_TERMINAL',
                'message': '运行中的批任务不能直接删除，请先停止任务',
                'retryable': False,
                'operation': 'delete_batch',
            }}), 409
        try:
            cancel_batch(user_id, batch_id)
        except ValueError:
            pass  # became terminal concurrently
        from utils.batch_engine import get_worker
        get_worker().notify()
        # Bounded wait so running children actually stop before their
        # workspaces/DB rows vanish; workers' post-delete write-backs are
        # guarded (0-row updates / warning-logged), so a timeout is not fatal.
        import time as _time
        deadline = _time.time() + 10
        while _time.time() < deadline:
            d = get_batch_detail(user_id, batch_id)
            if not d or not any(s['status'] == 'running' for s in d['sessions']):
                break
            _time.sleep(0.3)
        body = get_batch_detail(user_id, batch_id) or body
    # Tear down per-child workspaces before DB cascade
    workspace_root = current_app.config.get('AI_CHAT_WORKSPACE_ROOT') \
        or batch_workspace_root()
    # cleanup_batch_workspaces sweeps both the unified and the legacy root
    cleanup_batch_workspaces(workspace_root, user_id, body['sessions'])
    delete_batch(user_id, batch_id)
    return '', 204


@ai_chat_batches_bp.post('/<batch_id>/cancel')
@login_required
def cancel(batch_id):
    """中断整个批次：排队中的子任务直接取消，运行中的协作式中断，已暂停的
    同步落成 cancelled。中断不是终局 —— /resume 可把 cancelled 子任务在原
    工作上继续执行。"""
    try:
        result = cancel_batch(g.current_user['userId'], batch_id)
    except ValueError as e:
        return jsonify({'error': str(e)}), 409
    if result is None:
        return jsonify({'error': 'not found'}), 404
    from utils.batch_engine import get_worker
    get_worker().notify()
    return jsonify(result)


@ai_chat_batches_bp.post('/<batch_id>/pause')
@login_required
def pause(batch_id):
    """暂停整批排队/运行中的子任务：运行中的回合被协作式 abort 后落在非终态
    'paused'（不占 failed 计数）。之后 /resume 从原 OpenCode 会话续跑。与
    /cancel（中断）的区别：暂停是"温和的停"，语义上是执行中的歇脚。"""
    try:
        result = pause_batch(g.current_user['userId'], batch_id)
    except ValueError as e:
        return jsonify({'error': str(e)}), 409
    if result is None:
        return jsonify({'error': 'not found'}), 404
    from utils.batch_engine import get_worker
    get_worker().notify()
    return jsonify(result)


@ai_chat_batches_bp.post('/<batch_id>/resume')
@login_required
def resume(batch_id):
    """继续执行已暂停/已中断的批任务：paused/cancelled 子任务恢复为 pending——
    已开跑过的在原 OpenCode 会话/工作区上续跑（保留历史），排队中被停的正常
    执行。与 retry-failed（重试 failed）互补，各自动各自的状态集合。"""
    try:
        result = resume_batch(g.current_user['userId'], batch_id)
    except ValueError as e:
        return jsonify({'error': str(e)}), 409
    if result is None:
        return jsonify({'error': 'not found'}), 404
    from utils.batch_engine import get_worker
    get_worker().notify()
    return jsonify(result)


@ai_chat_batches_bp.post('/<batch_id>/retry-failed')
@login_required
def retry_failed(batch_id):
    count = reset_failed_to_pending(g.current_user['userId'], batch_id)
    if count:
        from utils.batch_engine import get_worker
        get_worker().notify()
    return jsonify({'retried': count})


@ai_chat_batches_bp.post('/<batch_id>/sessions/<session_id>/cancel')
@login_required
def cancel_single_child(batch_id, session_id):
    """Cancel a single child session within a batch."""
    try:
        result = cancel_child(g.current_user['userId'], batch_id, session_id)
    except ValueError as e:
        return jsonify({'error': str(e)}), 409
    if result is None:
        return jsonify({'error': 'not found'}), 404
    # Notify worker to pick up the cancellation
    from utils.batch_engine import get_worker
    get_worker().notify()
    return jsonify(result)


@ai_chat_batches_bp.post('/<batch_id>/append')
@login_required
def append(batch_id):
    body = request.get_json(silent=True) or {}
    files = body.get('files') or []
    if not isinstance(files, list) or not files:
        return jsonify({'error': 'at least one file required'}), 400
    for f in files:
        if not isinstance(f, dict) or not f.get('path') or not f.get('name'):
            return jsonify({'error': 'each file must have {name, path}'}), 400
    path_err = validate_staged_files(files, g.current_user['userId'])
    if path_err:
        return jsonify({'error': path_err}), 400
    try:
        result = append_to_batch(g.current_user['userId'], batch_id, files)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if result is None:
        return jsonify({'error': 'not found'}), 404
    from utils.batch_engine import get_worker
    get_worker().notify()
    return jsonify(result)


@ai_chat_batches_bp.post('/<batch_id>/sessions/<session_id>/resume')
@login_required
def resume_single_child(batch_id, session_id):
    """Continue one PAUSED child in place (from where it stopped). Unlike the
    batch-level /resume, other paused/cancelled children stay untouched."""
    try:
        result = resume_child(g.current_user['userId'], batch_id, session_id)
    except ValueError as e:
        return jsonify({'error': str(e)}), 409
    if result is None:
        return jsonify({'error': 'not found'}), 404
    from utils.batch_engine import get_worker
    get_worker().notify()
    return jsonify(result)


@ai_chat_batches_bp.post('/<batch_id>/sessions/<session_id>/reexecute')
@login_required
def reexecute(batch_id, session_id):
    try:
        result = reexecute_child(g.current_user['userId'], batch_id, session_id)
    except ValueError as e:
        return jsonify({'error': str(e)}), 409
    if result is None:
        return jsonify({'error': 'not found'}), 404
    from utils.batch_engine import get_worker
    get_worker().notify()
    return jsonify(result)
