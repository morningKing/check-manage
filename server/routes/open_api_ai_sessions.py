"""AI 单会话对外 API（/api/v1/ai-sessions）。

跟批任务共用同一套执行引擎（server/utils/batch_engine.py 里 `batch_id IS NULL`
的分支——见该文件 `_run_one` 的注释），区别是只产生一个会话，不按文件拆分子任务。
error 文案沿用中文惯例，跟 open_api_batches.py 同一个"AI 相关对外端点"家族，
不是 open-api.md 的英文惯例。

刻意只有两个端点——创建 + 查状态，没有 list/delete/continue/retry，见
docs/user-guide/integration/ai-session-api.md「范围」一节。agent/model 的
可选值发现复用批任务已有的 `GET /v1/ai-batches/agents`/`/models`，不重复建。

可选支持文件（body.files，形状同批任务）：暂存复用批任务已有的
`POST /v1/ai-batches/uploads`，不新建专属上传端点——文件落在哪个用户的暂存
目录下才是校验的边界，跟消费方是批任务还是单会话无关。所有随附文件进入
**同一个** AI 会话的 uploads/（不像批任务按文件拆分子任务），校验复用
open_api_batches.py 的 `_validate_files`/`MAX_FILES_PER_BATCH`，一处实现两处调用。
"""
from flask import Blueprint, g, jsonify, request

from auth import api_key_required, require_bound_key
from routes.open_api_batches import MAX_FILES_PER_BATCH, MAX_PROMPT_CHARS, _validate_files
from utils.ai_session_repo import create_session, get_session_for_owner
from utils.batch_engine import get_worker

open_api_ai_sessions_bp = Blueprint(
    'open_api_ai_sessions', __name__, url_prefix='/v1/ai-sessions')


def _current_key() -> dict:
    return getattr(g, 'api_key_info', {}) or {}


def _session_out(row: dict) -> dict:
    return {
        'sessionId': row['id'],
        'status': row['status'],
        'title': row.get('title'),
        'agent': row.get('agent'),
        'model': row.get('model'),
        'createdAt': row['createdAt'].isoformat() if row.get('createdAt') else None,
        'lastActiveAt': row['lastActiveAt'].isoformat() if row.get('lastActiveAt') else None,
        'output': row.get('output'),
        'error': row.get('error'),
        'files': row.get('files') or [],
    }


@open_api_ai_sessions_bp.post('')
@api_key_required
@require_bound_key
def create():
    """创建一个单会话：给一句 prompt，不需要文件。立即返回 pending，真正的
    执行由批任务 worker 异步接手（跟批任务同一个 dispatcher/并发上限）。"""
    key = _current_key()
    body = request.get_json(silent=True) or {}
    prompt = (body.get('prompt') or '').strip()
    if not prompt:
        return jsonify({'error': 'prompt 必填'}), 400
    if len(prompt) > MAX_PROMPT_CHARS:
        return jsonify({'error': f'prompt 超过 {MAX_PROMPT_CHARS} 字符的上限'}), 400
    title = (body.get('title') or '').strip() or None
    agent = (body.get('agent') or '').strip() or None
    model = (body.get('model') or '').strip() or None

    files = body.get('files') or []
    if not isinstance(files, list):
        return jsonify({'error': 'files 必须是数组'}), 400
    if len(files) > MAX_FILES_PER_BATCH:
        return jsonify({'error': f'files 最多 {MAX_FILES_PER_BATCH} 个'}), 400
    if files:
        err = _validate_files(files, key['ownerUserId'])
        if err:
            return err

    row = create_session(
        key['ownerUserId'], prompt=prompt, agent=agent, model=model,
        title=title, api_key_id=key['id'], files=files or None,
    )
    get_worker().notify()
    return jsonify({'sessionId': row['id'], 'status': row['status']}), 201


@open_api_ai_sessions_bp.get('/<session_id>')
@api_key_required
@require_bound_key
def detail(session_id):
    """查询单会话状态。`status` 为 pending/running/completed/failed；`output`
    只在 completed 时非 null，`error` 只在 failed 时非 null。没有 SSE——轮询
    是唯一的完成信号获取方式。"""
    key = _current_key()
    row = get_session_for_owner(session_id, key['ownerUserId'], key['id'])
    if not row:
        return jsonify({'error': '会话不存在'}), 404
    return jsonify(_session_out(row))
