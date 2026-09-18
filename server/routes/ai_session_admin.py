"""Admin session management API — unified cross-type session view.

Endpoints (all require admin.ai_chat_admin):
  GET  /ai/chat/admin/sessions/v2          — paginated list with filters
  GET  /ai/chat/admin/sessions/v2/<sid>    — session detail
  GET  /ai/chat/admin/sessions/v2/<sid>/messages — conversation history
  GET  /ai/chat/admin/sessions/v2/<sid>/files    — workspace file list
  GET  /ai/chat/admin/sessions/v2/<sid>/files/download — download file
  POST /ai/chat/admin/sessions/v2/<sid>/analyze  — trigger trace analysis
"""
import os
import secrets
import logging

from flask import Blueprint, g as flask_g, jsonify, request
from db import get_db
from auth import require_permission, require_permission_sse
from utils.session_admin_repo import (
    admin_get_session_detail,
    admin_get_session_messages,
    admin_list_sessions_v2,
    VALID_STATUSES,
    VALID_SOURCE_TYPES,
)

ai_session_admin_bp = Blueprint(
    'ai_session_admin', __name__,
    url_prefix='/ai/chat/admin/sessions/v2')


def _row_to_session(r: dict) -> dict:
    """Convert a DB row (snake_case) to the API contract (camelCase)."""
    return {
        'id': r['id'],
        'kind': r.get('kind') or 'chat',
        'userId': r.get('user_id'),
        'username': r.get('username'),
        'title': r.get('title'),
        'status': r.get('status'),
        'sourceType': r.get('source_type'),
        'batchId': r.get('batch_id'),
        'batchName': r.get('batch_name'),
        'batchSeq': r.get('batch_seq'),
        'inputFile': r.get('batch_input_file'),
        'scanTaskId': r.get('scan_task_id'),
        'lastMessagePreview': r.get('last_message_preview'),
        'errorMessage': r.get('error_message'),
        'createdAt': r['created_at'].isoformat() if r.get('created_at') else None,
        'lastActiveAt': r['last_active_at'].isoformat() if r.get('last_active_at') else None,
        'opencodeSessionId': r.get('opencode_session_id'),
    }


def _row_to_detail(r: dict) -> dict:
    """Convert a detail DB row to the API contract."""
    d = _row_to_session(r)
    d['workspacePath'] = r.get('workspace_path')
    d['batchApiKeyId'] = r.get('batch_api_key_id')
    return d


@ai_session_admin_bp.get('')
@require_permission('admin.ai_chat_admin')
def list_sessions():
    """Unified paginated session list with optional filters."""
    try:
        page = max(1, int(request.args.get('page', 1)))
        page_size = min(max(1, int(request.args.get('pageSize', 20))), 100)
    except (TypeError, ValueError):
        return jsonify({'error': 'page 与 pageSize 必须是整数'}), 400

    status = request.args.get('status', '').strip() or None
    source_type = request.args.get('sourceType', '').strip() or None
    owner = request.args.get('owner', '').strip() or None
    keyword = request.args.get('keyword', '').strip() or None
    batch_id = request.args.get('batchId', '').strip() or None

    if status and status not in VALID_STATUSES:
        return jsonify({'error': f'无效状态: {status}'}), 400
    if source_type and source_type not in VALID_SOURCE_TYPES:
        return jsonify({'error': f'无效来源类型: {source_type}'}), 400

    result = admin_list_sessions_v2(
        page=page, page_size=page_size,
        status=status, source_type=source_type,
        owner=owner, keyword=keyword, batch_id=batch_id,
        kind=(request.args.get('kind') or None))
    result['items'] = [_row_to_session(r) for r in result['items']]
    return jsonify(result)


@ai_session_admin_bp.get('/<session_id>')
@require_permission('admin.ai_chat_admin')
def session_detail(session_id):
    """Single session detail with all columns."""
    detail = admin_get_session_detail(session_id)
    if not detail:
        return jsonify({'error': '会话不存在'}), 404
    return jsonify(_row_to_detail(detail))


@ai_session_admin_bp.get('/<session_id>/messages')
@require_permission('admin.ai_chat_admin')
def session_messages(session_id):
    """Conversation history for any session (admin context, no ownership check)."""
    detail = admin_get_session_detail(session_id)
    if not detail:
        return jsonify({'error': '会话不存在'}), 404
    result = admin_get_session_messages(session_id)
    return jsonify(result)


@ai_session_admin_bp.get('/<session_id>/files')
@require_permission_sse('admin.ai_chat_admin')
def session_files(session_id):
    """Workspace file list for any session."""
    from utils.workspace_outputs import list_session_files, augment_with_data_file_id
    detail = admin_get_session_detail(session_id)
    if not detail:
        return jsonify({'error': '会话不存在'}), 404
    ws = detail.get('workspace_path')
    if not ws:
        return jsonify({'files': [], 'truncated': False})
    files, truncated = list_session_files(ws)
    augment_with_data_file_id(session_id, files)
    return jsonify({'files': files, 'truncated': truncated})


@ai_session_admin_bp.get('/<session_id>/files/download')
@require_permission_sse('admin.ai_chat_admin')
def session_file_download(session_id):
    """Download a single file from session workspace."""
    from flask import send_file
    detail = admin_get_session_detail(session_id)
    if not detail:
        return jsonify({'error': '会话不存在'}), 404
    ws = detail.get('workspace_path')
    if not ws:
        return jsonify({'error': '该会话没有工作区'}), 400
    rel_path = request.args.get('path', '').strip()
    if not rel_path:
        return jsonify({'error': 'path 参数必填'}), 400
    normalized = os.path.normpath(rel_path)
    if '..' in normalized.split(os.sep):
        return jsonify({'error': '路径非法'}), 400
    abs_path = os.path.join(ws, normalized)
    if not os.path.commonpath([ws, abs_path]).startswith(ws):
        return jsonify({'error': '路径非法'}), 400
    if not os.path.isfile(abs_path):
        return jsonify({'error': '文件不存在'}), 404
    return send_file(abs_path, as_attachment=True)


@ai_session_admin_bp.post('/<session_id>/analyze')
@require_permission('admin.ai_chat_admin')
def analyze_session(session_id):
    """Trigger trace analysis for a session.

    Creates a new analysis session with the trace-analyzer skill injected,
    sends an analysis prompt, and returns the new session ID.
    """
    from config import AI_WORKSPACE_ROOT, OPENCODE_BASE_URL, MCP_SERVER_URL, get_default_chat_model
    from utils.opencode_client import OpenCodeClient
    from utils.workspace import create_session_workspace, write_opencode_config
    from utils.session_token import generate_token
    from utils.mcp_servers import enabled_mcp_config, internal_mcp_enabled

    MCP_NAME = 'check-manage'
    logger = logging.getLogger('ai_session_admin')

    # 0. Pre-flight: the analysis agent's core capability is the check-manage
    #    MCP toolset (analyze_trace / query_sessions). If the MCP server is
    #    down the turn would still "run" but silently degrade to wandering the
    #    workspace filesystem (observed live: the agent loads the skill, finds
    #    the tools missing, and starts bash/glob-ing around). Fail fast with an
    #    actionable error instead.
    import urllib.request
    try:
        with urllib.request.urlopen(f'{MCP_SERVER_URL}/health', timeout=3) as resp:
            if resp.status != 200:
                raise OSError(f'HTTP {resp.status}')
    except Exception as e:
        logger.warning('analyze: MCP server unreachable at %s: %s', MCP_SERVER_URL, e)
        return jsonify({
            'error': (f'MCP 服务不可用（{MCP_SERVER_URL}）：轨迹分析依赖 '
                      f'analyze_trace / query_sessions MCP 工具。'
                      f'请先启动 MCP 服务器（npm run mcp 或 npm run dev:all）后重试。'),
        }), 502
    if not internal_mcp_enabled():
        # The analysis agent is built on the internal MCP tools
        # (analyze_trace / query_sessions) — without the internal entry in its
        # opencode.json the session would run blind, so refuse up front (before
        # creating any session row / workspace) rather than degrade silently.
        return jsonify({
            'error': ('平台内置 MCP 已被禁用：轨迹分析依赖 analyze_trace / query_sessions '
                      '内部工具。请在 AI 设置中重新启用内置 MCP 后重试。'),
        }), 409

    # 1. Verify target session exists
    detail = admin_get_session_detail(session_id)
    if not detail:
        return jsonify({'error': '会话不存在'}), 404

    user = flask_g.current_user
    user_id = user['userId']

    # ── Execution audit (Spec §7.1): the analysis is a first-class job row
    # (analysis_id → target_session_id) and an execution attempt with its own
    # model / skill manifest — the old route had none of these and silently
    # blanket-injected every enabled skill.
    import json as _json
    from utils.workspace import cleanup_session_workspace, create_session_workspace
    from config import AI_WORKSPACE_ROOT
    from utils.opencode_client import OpenCodeClient as _OC  # noqa: F401 (alias below)
    from utils import execution_audit
    from utils.opencode_client import OpenCodeClient

    analysis_model = get_default_chat_model()
    analysis_skill = 'trace-analyzer'

    user = flask_g.current_user
    user_id = user['userId']

    # Locate the platform trace-analyzer skill BEFORE creating anything —
    # a missing analyzer must fail the request (fail-closed), not spawn a
    # blind session that wanders the filesystem.
    from utils.global_skills import inject_single_skill

    analysis_sid = 'sess_' + secrets.token_hex(6)
    analysis_id = 'ana_' + secrets.token_hex(6)
    workspace_path = create_session_workspace(AI_WORKSPACE_ROOT, user_id, analysis_sid)

    def _fail(status_code: int, message: str, code: str | None = None):
        """Converge a failed trigger: no orphan session row / workspace /
        listener / diagnosis row (Spec §22 P0-8)."""
        try:
            from utils.chat_persist import stop_listener
            stop_listener(analysis_sid)
        except Exception:
            pass
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM ai_chat_messages WHERE session_id = %s",
                                (analysis_sid,))
                    cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s",
                                (analysis_sid,))
                    cur.execute(
                        "UPDATE ai_execution_diagnoses SET status='failed', "
                        " error_message=%s, completed_at=NOW() WHERE id=%s",
                        (message[:1000], analysis_id))
        except Exception:
            pass
        try:
            cleanup_session_workspace(AI_WORKSPACE_ROOT, user_id, analysis_sid)
        except Exception:
            pass
        return jsonify({'error': message, 'code': code}), status_code

    # 2. Insert session + diagnosis rows
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ai_chat_sessions "
            "(id, user_id, title, workspace_path, session_token, "
            " token_expires_at, status, kind) "
            "VALUES (%s, %s, %s, %s, %s, NOW() + INTERVAL '1 hour', 'active', "
            " 'trace_analysis')",
            (analysis_sid, user_id, f'轨迹分析: {session_id}', workspace_path,
             '_pending_'),
        )
        cur.execute(
            "INSERT INTO ai_execution_diagnoses "
            "(id, target_session_id, analysis_session_id, status, "
            " requested_model, requested_by) "
            "VALUES (%s, %s, %s, 'pending', %s, %s)",
            (analysis_id, session_id, analysis_sid,
             analysis_model or None, user_id),
        )

    # 3. Token + opencode.json
    token = generate_token(analysis_sid, 24)
    mcp_url = f"{MCP_SERVER_URL}/mcp?token={token}"
    extra_mcp = enabled_mcp_config(reserved_names=[MCP_NAME])
    write_opencode_config(
        workspace_path, mcp_name=MCP_NAME, mcp_url=mcp_url,
        model=analysis_model, extra_mcp=extra_mcp,
        include_internal=internal_mcp_enabled(),
    )

    # 4. Inject ONLY the analyzer skill — fail-closed (Spec §7.1).
    try:
        skill_src = inject_single_skill(workspace_path, analysis_skill,
                                        AI_WORKSPACE_ROOT)
    except FileNotFoundError as e:
        return _fail(409, f'{e}；轨迹分析依赖该技能，已取消本次分析',
                     code='TRACE_SKILL_MISSING')
    except OSError as e:
        return _fail(502, f'技能 {analysis_skill} 注入失败: {e}',
                     code='TRACE_SKILL_INJECT_FAILED')
    skill_hash = execution_audit.sha256_file(
        os.path.join(skill_src, 'SKILL.md'))

    # 5. Create OpenCode session
    client = OpenCodeClient(OPENCODE_BASE_URL)
    try:
        oc_sid = client.create_session(
            directory=workspace_path,
            title=f'轨迹分析: {session_id}',
        )
    except Exception as e:
        logger.warning('analyze: OpenCode create_session failed: %s', e)
        return _fail(502, f'OpenCode 会话创建失败: {e}')

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ai_chat_sessions SET opencode_session_id = %s WHERE id = %s",
            (oc_sid, analysis_sid),
        )

    # 6. Analysis prompt
    source_info = ''
    if detail.get('scan_task_id'):
        source_info = f'扫描任务 {detail.get("scan_task_name") or detail["scan_task_id"]}'
    elif detail.get('batch_id'):
        source_info = f'批任务 {detail.get("batch_name") or detail["batch_id"]}'
    else:
        source_info = '交互会话'

    analysis_prompt = (
        f'使用 `trace-analyzer` 技能: '
        f'分析会话 {session_id} 的执行轨迹。\n\n'
        f'## 会话概况\n'
        f'- 来源: {source_info}\n'
        f'- 状态: {detail.get("status", "未知")}\n'
        f'- Agent: {detail.get("agent") or detail.get("batch_agent") or "默认"}\n'
    )
    if detail.get('error_message'):
        analysis_prompt += f'- 错误信息: {detail["error_message"]}\n'

    # 7. Persist the analysis user message
    msg_id = 'msg_' + secrets.token_hex(6)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ai_chat_messages (id, session_id, role, content) "
            "VALUES (%s, %s, 'user', %s)",
            (msg_id, analysis_sid,
             _json.dumps([{"type": "text", "text": analysis_prompt}])),
        )

    # 8. Execution attempt + manifests for the analysis itself
    audit_attempt_id = execution_audit.create_attempt(
        session_id=analysis_sid, source_type='trace_analysis',
        source_id=analysis_id, operation='analysis',
        effective_model=analysis_model or None,
        model_resolution='session_default' if analysis_model else 'runtime_default',
        raw_user_content=analysis_prompt,
        effective_prompt=analysis_prompt,
        prompt_version='trace-analysis-v1',
        augmentations={'skill': analysis_skill, 'skill_hash': skill_hash},
        context_snapshot={'analysis_id': analysis_id,
                          'target_session_id': session_id},
        workspace_path=workspace_path,
    )
    if audit_attempt_id:
        execution_audit.save_manifests(audit_attempt_id, [
            {'kind': 'skill', 'name': analysis_skill,
             'source': 'platform_global', 'path': os.path.join(skill_src, 'SKILL.md'),
             'content_hash': skill_hash, 'injected': True,
             'injection_status': 'success', 'selected': 'requested'},
        ] + execution_audit.scan_workspace_manifests(workspace_path))

    # 9. Listener BEFORE dispatch (same ordering rationale as before).
    from utils.chat_persist import ensure_listener
    ensure_listener(analysis_sid, oc_sid, workspace_path)

    # 10. Send — explicitly pass the analysis model (OpenCode does NOT pick up
    # opencode.json's model for prompt_async; the old route silently analyzed
    # with OpenCode's own default).
    try:
        client.send_prompt_async(oc_sid, analysis_prompt, directory=workspace_path,
                                 model=analysis_model or None)
    except Exception as e:
        logger.warning('analyze: send_prompt_async failed: %s', e)
        execution_audit.finish_latest_running(
            analysis_sid, 'failed', error_code='ANALYSIS_DISPATCH_FAILED',
            error_message=str(e)[:500])
        return _fail(502, f'发送分析请求失败: {e}')

    if audit_attempt_id:
        execution_audit.record_event(
            audit_attempt_id, 'dispatch.ok', session_id=analysis_sid,
            parent_session_id=oc_sid,
            payload={'model': analysis_model, 'analysis_id': analysis_id,
                     'target_session_id': session_id})

    logger.info('analyze: triggered for %s -> analysis session %s (oc=%s, '
                'analysis_id=%s)', session_id, analysis_sid, oc_sid, analysis_id)

    return jsonify({
        'analysisId': analysis_id,
        'analysisSessionId': analysis_sid,
        'message': f'已触发轨迹分析，分析会话: {analysis_sid}',
    })


# ═══════════════════════════════════════════════════════════════════════════

@ai_session_admin_bp.get('/<sid>/analyses')
@require_permission('admin.ai_chat_admin')
def list_session_analyses(sid):
    """该会话的轨迹分析历史（execution-audit：分析与原会话关联入口）。
    每行含 analysis_session_id，可直开分析会话。"""
    from utils.session_admin_repo import admin_get_session_detail
    if not admin_get_session_detail(sid):
        return jsonify({'error': '会话不存在'}), 404
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, analysis_session_id, status, data_completeness,
                       error_message, created_at, completed_at
                FROM ai_execution_diagnoses
                WHERE target_session_id = %s
                ORDER BY created_at DESC
                LIMIT 20
                """, (sid,))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        for k in ('created_at', 'completed_at'):
            r[k] = r[k].isoformat() if r[k] else None
    return jsonify({'analyses': rows})


# Execution audit APIs (execution-audit Spec §15/§16).
# Separate blueprint so the paths match the Spec (/ai/chat/admin/...) while
# keeping the session v2 routes untouched.

from flask import Blueprint as _Blueprint

ai_execution_admin_bp = _Blueprint(
    'ai_execution_admin', __name__, url_prefix='/ai/chat/admin')


def _attempt_camel(a: dict) -> dict:
    from datetime import datetime as _dt
    out = {
        'id': a.get('id'), 'sessionId': a.get('session_id'),
        'sourceType': a.get('source_type'), 'sourceId': a.get('source_id'),
        'parentAttemptId': a.get('parent_attempt_id'),
        'attemptNo': a.get('attempt_no'), 'operation': a.get('operation'),
        'requestedAgent': a.get('requested_agent'),
        'effectiveAgent': a.get('effective_agent'),
        'agentResolution': a.get('agent_resolution'),
        'requestedModel': a.get('requested_model'),
        'effectiveModel': a.get('effective_model'),
        'modelResolution': a.get('model_resolution'),
        'rawPromptHash': a.get('raw_prompt_hash'),
        'effectivePromptHash': a.get('effective_prompt_hash'),
        'effectivePromptLen': a.get('effective_prompt_len'),
        'status': a.get('status'), 'errorCode': a.get('error_code'),
        'errorMessage': a.get('error_message'),
        'startedAt': a.get('started_at').isoformat()
        if isinstance(a.get('started_at'), _dt) else a.get('started_at'),
        'finishedAt': a.get('finished_at').isoformat()
        if isinstance(a.get('finished_at'), _dt) else a.get('finished_at'),
    }
    return out


def _manifest_camel(m: dict) -> dict:
    return {
        'kind': m.get('kind'), 'name': m.get('name'), 'source': m.get('source'),
        'path': m.get('path'), 'contentHash': m.get('content_hash'),
        'injected': m.get('injected'), 'injectionStatus': m.get('injection_status'),
        'runtimeLoaded': m.get('runtime_loaded'), 'selected': m.get('selected'),
        'invoked': m.get('invoked'),
    }


def _attempt_block(sid: str, attempt_id: str | None = None) -> dict:
    from utils import execution_audit
    from utils import trace_auditor
    attempts = execution_audit.get_attempts(sid, limit=20)
    if attempt_id:
        target = next((a for a in attempts if a['id'] == attempt_id), None)
    else:
        target = attempts[0] if attempts else None
    manifests = execution_audit.get_manifests(target['id']) if target else []
    events = execution_audit.get_events(target['id'], limit=500) if target else []
    events_camel = [
        {'eventSeq': e.get('event_seq'), 'eventType': e.get('event_type'),
         'occurredAt': e.get('occurred_at').isoformat()
         if hasattr(e.get('occurred_at'), 'isoformat') else e.get('occurred_at'),
         'status': e.get('status'), 'toolCallId': e.get('tool_call_id'),
         'messageId': e.get('message_id'), 'partId': e.get('part_id')}
        for e in events
    ]
    report = None
    if target and target.get('status') in ('completed', 'failed', 'stopped'):
        try:
            report = trace_auditor.ensure_diagnosis_for_session(sid)
        except Exception as e:
            logger.warning('execution report build failed sid=%s: %s', sid, e)
            report = {'status': 'failed', 'error': str(e)[:300]}
    attempts_camel = [_attempt_camel(a) for a in attempts]
    target_camel = next((a for a in attempts_camel
                         if target and a['id'] == target['id']), None)
    return {
        'attempts': attempts_camel,
        'currentAttempt': target_camel,
        'manifests': [_manifest_camel(m) for m in manifests],
        'events': events_camel,
        'report': report,
    }


@ai_execution_admin_bp.get('/sessions/<sid>/execution-audit')
@require_permission('admin.ai_chat_admin')
def execution_audit_overview(sid):
    """One-stop payload for the admin audit drawer: attempts + manifests +
    events + the deterministic diagnosis report (built on demand)."""
    from utils.session_admin_repo import admin_get_session_detail
    if not admin_get_session_detail(sid):
        return jsonify({'error': '会话不存在'}), 404
    return jsonify(_attempt_block(sid))


@ai_execution_admin_bp.get('/sessions/<sid>/execution-events')
@require_permission('admin.ai_chat_admin')
def execution_audit_events(sid):
    from utils import execution_audit
    from utils.session_admin_repo import admin_get_session_detail
    if not admin_get_session_detail(sid):
        return jsonify({'error': '会话不存在'}), 404
    attempt_id = (request.args.get('attemptId') or '').strip() or None
    attempts = execution_audit.get_attempts(sid, limit=20)
    target = (next((a for a in attempts if a['id'] == attempt_id), None)
              if attempt_id else (attempts[0] if attempts else None))
    events = execution_audit.get_events(target['id'], limit=1000) if target else []
    return jsonify({'attempt': target, 'events': events})


@ai_execution_admin_bp.get('/sessions/<sid>/execution-prompt')
@require_permission('admin.ai_execution_prompt_read')
def execution_audit_prompt(sid):
    """Effective-prompt snapshot. Hashes/augmentations always; plaintext only
    when EXECUTION_AUDIT_PROMPT_PLAINTEXT is on (redaction_status='plaintext')."""
    from utils import execution_audit
    from utils.session_admin_repo import admin_get_session_detail
    if not admin_get_session_detail(sid):
        return jsonify({'error': '会话不存在'}), 404
    attempt_id = (request.args.get('attemptId') or '').strip()
    attempts = execution_audit.get_attempts(sid, limit=20)
    target = (next((a for a in attempts if a['id'] == attempt_id), None)
              if attempt_id else (attempts[0] if attempts else None))
    if not target:
        return jsonify({'error': '该会话没有执行尝试记录'}), 404
    from db import get_db as _gdb
    with _gdb() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT raw_user_content, effective_prompt, raw_user_content_hash,"
                " effective_prompt_hash, effective_prompt_len, augmentations,"
                " context_snapshot, redaction_status"
                " FROM ai_execution_prompt_snapshots WHERE attempt_id = %s"
                " ORDER BY created_at DESC LIMIT 1", (target['id'],))
            row = cur.fetchone()
    if not row:
        return jsonify({'error': '没有 Prompt 快照'}), 404
    plaintext = row[7] == 'plaintext'
    from utils.operation_log import log_operation
    log_operation('read', 'ai_execution_prompt', target['id'], sid,
                  '查看执行 Prompt 快照')
    return jsonify({
        'attemptId': target['id'],
        'rawUserContent': row[0] if plaintext else None,
        'effectivePrompt': row[1] if plaintext else None,
        'rawHash': row[2],
        'effectiveHash': row[3],
        'effectiveLen': row[4],
        'augmentations': row[5],
        'context': row[6],
        'plaintextAvailable': plaintext,
    })


@ai_execution_admin_bp.get('/analyses/<analysis_id>')
@require_permission('admin.ai_chat_admin')
def analysis_status(analysis_id):
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, target_session_id, analysis_session_id, status,"
                " data_completeness, error_message, created_at, completed_at"
                " FROM ai_execution_diagnoses WHERE id = %s", (analysis_id,))
            row = cur.fetchone()
    if not row:
        return jsonify({'error': '分析任务不存在'}), 404
    cols = ['analysisId', 'targetSessionId', 'analysisSessionId', 'status',
            'dataCompleteness', 'error', 'createdAt', 'completedAt']
    out = dict(zip(cols, row))
    for k in ('createdAt', 'completedAt'):
        out[k] = out[k].isoformat() if out[k] else None
    return jsonify(out)


@ai_execution_admin_bp.get('/analyses/<analysis_id>/report')
@require_permission('admin.ai_chat_admin')
def analysis_report(analysis_id):
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, target_session_id, status, report,"
                " data_completeness FROM ai_execution_diagnoses WHERE id = %s",
                (analysis_id,))
            row = cur.fetchone()
    if not row:
        return jsonify({'error': '分析任务不存在'}), 404
    report = row[3] if isinstance(row[3], dict) else {}
    # live-compile the deterministic audit for the target attempt on demand
    try:
        from utils import trace_auditor
        fresh = trace_auditor.ensure_diagnosis_for_session(row[1])
        if isinstance(fresh, dict) and fresh.get('execution'):
            report = fresh
    except Exception as e:
        logger.warning('analysis report compile failed %s: %s', analysis_id, e)
    return jsonify({'analysisId': row[0], 'targetSessionId': row[1],
                    'status': row[2], 'dataCompleteness': row[4],
                    'report': report})


@ai_execution_admin_bp.get('/skill-analytics')
@require_permission('admin.ai_chat_admin')
def skill_analytics():
    """SkillOpt aggregation v0 (Spec §14): per skill name+version over the
    attempts that carried it. Sample-starved buckets surface as-is — the
    caller decides confidence, this endpoint never inflates it."""
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.name, m.content_hash,
                       COUNT(DISTINCT a.id)                                          AS invocations,
                       COUNT(DISTINCT CASE WHEN a.status = 'completed'
                                           THEN a.id END)                            AS completed,
                       COUNT(DISTINCT CASE WHEN a.status = 'failed'
                                           THEN a.id END)                            AS failed,
                       ROUND(AVG(EXTRACT(EPOCH FROM (a.finished_at - a.started_at))
                                 * 1000))                                            AS avg_duration_ms
                FROM ai_execution_manifests m
                JOIN ai_execution_attempts a ON a.id = m.attempt_id
                WHERE m.kind = 'skill' AND m.content_hash IS NOT NULL
                GROUP BY m.name, m.content_hash
                ORDER BY invocations DESC
                LIMIT 50
                """)
            cols = [d[0] for d in cur.description]
            items = [dict(zip(cols, r)) for r in cur.fetchall()]
            for it in items:
                inv = it['invocations'] or 0
                it['completion_rate'] = round((it['completed'] or 0) / inv, 2) if inv else None
                it['failure_rate'] = round((it['failed'] or 0) / inv, 2) if inv else None
                it['sample_size'] = inv
            cur.execute(
                """
                SELECT s.step_id,
                       COUNT(*)                                              AS total,
                       COUNT(*) FILTER (WHERE s.status = 'completed_confirmed') AS confirmed,
                       COUNT(*) FILTER (WHERE s.status = 'missing')             AS missing,
                       COUNT(*) FILTER (WHERE s.status = 'failed')              AS failed
                FROM ai_execution_step_results s
                GROUP BY s.step_id
                ORDER BY total DESC
                LIMIT 20
                """)
            scols = [d[0] for d in cur.description]
            step_stats = [dict(zip(scols, r)) for r in cur.fetchall()]
    return jsonify({'skills': items, 'stepStats': step_stats})
