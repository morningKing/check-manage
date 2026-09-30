"""编排能力对外契约（/v1/ai-orchestrations/*，P2 §6.3 / 缺口补齐计划 3.8）。

只读：definitions（已发布版本）与 runs 状态/事件。create_run 允许持有
API Key 的调用方以其 owner 身份发起编排（归属隔离与批任务同模型）。
密钥隔离模型与 open_api_batches.py 一致：requested_by=密钥 owner，
requested_by_kind='api_key'；API Key 创建的 run 不能被 JWT 用户侧越权查看。
"""
import json

from flask import Blueprint, g, jsonify, request

from auth import api_key_required
from utils import orchestration_defs
from utils import orchestration_engine
from utils.api_errors import NOT_FOUND, err, register_error_handlers
from utils.operation_log import log_api_operation

open_api_orchestrations_bp = Blueprint(
    'open_api_orchestrations', __name__, url_prefix='/v1/ai-orchestrations')

register_error_handlers(open_api_orchestrations_bp)


def _current_key() -> dict:
    return getattr(g, 'api_key_info', {}) or {}


def _out_definition(d: dict) -> dict:
    """白名单输出：不泄漏内部校验细节以外的字段。"""
    return {
        'definitionId': d.get('id'),
        'name': d.get('name'),
        'description': d.get('description'),
        'version': d.get('version'),
        'nodes': d.get('nodes') or [],
        'edges': d.get('edges') or [],
    }


def _out_run(r: dict) -> dict:
    return {
        'runId': r.get('id'),
        'definitionId': r.get('definition_id'),
        'definitionVersion': r.get('definition_version'),
        'status': r.get('status'),
        'createdAt': (r.get('created_at').isoformat()
                      if r.get('created_at') else None),
        'finishedAt': (r.get('finished_at').isoformat()
                       if r.get('finished_at') else None),
        'errorCode': r.get('error_code'),
        'errorMessage': r.get('error_message'),
    }


@open_api_orchestrations_bp.get('/definitions')
@api_key_required
def list_definitions():
    ds = orchestration_defs.list_definitions()
    return jsonify({'definitions': [_out_definition(d) for d in ds]})


@open_api_orchestrations_bp.get('/definitions/<def_id>')
@api_key_required
def get_definition(def_id):
    d = orchestration_defs.get_definition(def_id)
    if not d:
        return err('编排定义不存在', NOT_FOUND, 404)
    return jsonify({'definition': _out_definition(d)})


@open_api_orchestrations_bp.post('/runs')
@api_key_required
def create_run():
    """以密钥 owner 身份发起一次编排 run。归属：requested_by=密钥 owner，
    requested_by_kind='api_key'（engine 侧归属性语义与批任务同源）。"""
    body = request.get_json(silent=True) or {}
    def_id = (body.get('definitionId') or '').strip()
    if not def_id:
        return err('definitionId 必填', 'INVALID_ARGUMENT', 400)
    key = _current_key()
    try:
        run = orchestration_engine.create_run(
            def_id, key['ownerUserId'], run_input=body.get('input') or {},
            requested_by_kind='api_key')
    except ValueError as e:
        return err(str(e), 'INVALID_ARGUMENT', 400)
    from utils.batch_engine import get_worker
    try:
        get_worker().notify()
    except Exception:
        pass  # 通知失败不阻断创建（scheduler 周期会拾取）
    log_api_operation('create', 'ai_orchestration_run', run['id'], run['id'],
                      f'通过 API Key 创建编排 run（定义 {def_id}）')
    return jsonify(_out_run(run)), 201


@open_api_orchestrations_bp.get('/runs/<run_id>')
@api_key_required
def get_run(run_id):
    run = orchestration_engine.get_run(run_id)
    if not run:
        return err('run 不存在', NOT_FOUND, 404)
    if run.get('requested_by') != _current_key()['ownerUserId']:
        return err('run 不存在', NOT_FOUND, 404)  # 归属隔离：404 不泄漏存在性
    return jsonify({'run': _out_run(run)})


@open_api_orchestrations_bp.get('/runs/<run_id>/events')
@api_key_required
def run_events(run_id):
    """事件增量读取（afterSeq 语义同批任务；事实源 ai_batch_events）。"""
    run = orchestration_engine.get_run(run_id)
    if not run:
        return err('run 不存在', NOT_FOUND, 404)
    if run.get('requested_by') != _current_key()['ownerUserId']:
        return err('run 不存在', NOT_FOUND, 404)
    try:
        after_seq = max(0, int(request.args.get('afterSeq', 0)))
    except (TypeError, ValueError):
        return err('afterSeq 必须是整数', 'INVALID_ARGUMENT', 400)
    from utils import batch_events
    rows = batch_events.read_events(run_id, after_seq=after_seq, limit=200)
    out = [{'eventId': r['event_id'], 'eventSeq': r['event_seq'],
            'type': r['event_type'], 'at': r.get('createdAt'),
            'data': r.get('payload') or {}} for r in rows]
    return jsonify({'runId': run_id, 'events': out,
                    'nextAfterSeq': rows[-1]['event_seq'] if rows else after_seq,
                    'hasMore': len(rows) >= 200})
