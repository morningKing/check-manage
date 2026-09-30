"""客服实例管理 API（需 admin.kefu）。"""
import re
import json
import threading
import queue as _queue
from flask import Blueprint, request, jsonify, g, Response, stream_with_context
from auth import require_permission
from utils import kefu_repo
from utils import kefu_event_bus
from utils import kefu_sse_ticket
from utils.operation_log import log_operation

kefu_admin_bp = Blueprint('kefu_admin', __name__, url_prefix='/admin/kefu')

_SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,63}$')
_BLOCK_TYPES = {'links', 'faq', 'richtext', 'contact'}

MAX_ADMIN_SSE_PER_USER = 3
ADMIN_SSE_HEARTBEAT = 20
_admin_sse_active: dict = {}
_admin_sse_lock = threading.Lock()


def _format_sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _admin_sse_acquire(uid: str) -> bool:
    with _admin_sse_lock:
        n = _admin_sse_active.get(uid, 0)
        if n >= MAX_ADMIN_SSE_PER_USER:
            return False
        _admin_sse_active[uid] = n + 1
        return True


def _admin_sse_release(uid: str) -> None:
    with _admin_sse_lock:
        n = _admin_sse_active.get(uid, 0)
        if n <= 1:
            _admin_sse_active.pop(uid, None)
        else:
            _admin_sse_active[uid] = n - 1


def _validate_panel_blocks(v):
    if not isinstance(v, list):
        return 'panel_blocks 必须是数组'
    for b in v:
        if not isinstance(b, dict) or b.get('type') not in _BLOCK_TYPES:
            return 'panel_blocks 每项需为对象且 type 合法'
    return None


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
    if 'panel_blocks' in body:
        err = _validate_panel_blocks(body['panel_blocks'])
        if err:
            return jsonify({'error': err}), 400
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


def _faq_owned(iid, fid):
    faq = kefu_repo.get_faq(fid)
    return faq if (faq and faq['instance_id'] == iid) else None


@kefu_admin_bp.route('/instances/<iid>/faq', methods=['GET'])
@require_permission('admin.kefu')
def list_faq(iid):
    return jsonify({'items': kefu_repo.list_faq_admin(iid)})


@kefu_admin_bp.route('/instances/<iid>/faq', methods=['POST'])
@require_permission('admin.kefu')
def create_faq(iid):
    if not kefu_repo.get_instance(iid):
        return jsonify({'error': 'instance not found'}), 404
    body = request.get_json(silent=True) or {}
    if not (body.get('question') or '').strip() or not (body.get('answer') or '').strip():
        return jsonify({'error': 'question 与 answer 必填'}), 400
    faq = kefu_repo.create_faq(iid, body)
    log_operation('create', 'kefu_faq_item', faq['id'], faq['question'][:50], '新建热问')
    return jsonify(faq), 201


@kefu_admin_bp.route('/instances/<iid>/faq/reorder', methods=['PATCH'])
@require_permission('admin.kefu')
def reorder_faq(iid):
    order = (request.get_json(silent=True) or {}).get('order')
    if not isinstance(order, list):
        return jsonify({'error': 'order must be a list'}), 400
    kefu_repo.reorder_faq(iid, order)
    return jsonify({'ok': True})


@kefu_admin_bp.route('/instances/<iid>/faq/<fid>', methods=['PATCH'])
@require_permission('admin.kefu')
def update_faq(iid, fid):
    if not _faq_owned(iid, fid):
        return jsonify({'error': 'not found'}), 404
    faq = kefu_repo.update_faq(fid, request.get_json(silent=True) or {})
    if not faq:
        return jsonify({'error': 'not found'}), 404
    log_operation('update', 'kefu_faq_item', fid, faq['question'][:50], '更新热问')
    return jsonify(faq)


@kefu_admin_bp.route('/instances/<iid>/faq/<fid>', methods=['DELETE'])
@require_permission('admin.kefu')
def delete_faq(iid, fid):
    if not _faq_owned(iid, fid):
        return jsonify({'error': 'not found'}), 404
    kefu_repo.delete_faq(fid)
    log_operation('delete', 'kefu_faq_item', fid, fid, '删除热问')
    return jsonify({'ok': True})


# ==================== 人工接管（会话） ====================

@kefu_admin_bp.route('/sessions', methods=['GET'])
@require_permission('admin.kefu')
def list_sessions():
    def _b(v):
        return None if v is None else v in ('1', 'true', 'True')
    sessions = kefu_repo.list_kefu_sessions_admin(
        instance_id=request.args.get('instance') or None,
        needs_human=_b(request.args.get('needs_human')),
        takeover=_b(request.args.get('takeover')),
        status=request.args.get('status', 'active') or None,
    )
    return jsonify({'sessions': sessions})


@kefu_admin_bp.route('/sessions/<sid>/messages', methods=['GET'])
@require_permission('admin.kefu')
def get_session_messages(sid):
    if not kefu_repo.get_kefu_session_admin(sid):
        return jsonify({'error': 'not found'}), 404
    return jsonify({'messages': kefu_repo.get_messages(sid)})


@kefu_admin_bp.route('/sessions/<sid>/takeover', methods=['POST'])
@require_permission('admin.kefu')
def takeover_session_route(sid):
    sess = kefu_repo.get_kefu_session_admin(sid)
    if not sess:
        return jsonify({'error': 'not found'}), 404
    kefu_repo.takeover_session(sid, g.current_user['userId'])
    log_operation('takeover', 'kefu_session', sid, None, '接管客服会话')
    kefu_event_bus.publish(sid, {'type': 'takeover'})
    kefu_event_bus.publish(f"inst:{sess['kefu_instance_id']}", {'sid': sid, 'type': 'takeover'})
    return jsonify({'humanTakeover': True})


@kefu_admin_bp.route('/sessions/<sid>/release', methods=['POST'])
@require_permission('admin.kefu')
def release_session_route(sid):
    sess = kefu_repo.get_kefu_session_admin(sid)
    if not sess:
        return jsonify({'error': 'not found'}), 404
    kefu_repo.release_session(sid)
    log_operation('release', 'kefu_session', sid, None, '释放客服会话')
    kefu_event_bus.publish(sid, {'type': 'release'})
    kefu_event_bus.publish(f"inst:{sess['kefu_instance_id']}", {'sid': sid, 'type': 'release'})
    return jsonify({'humanTakeover': False})


@kefu_admin_bp.route('/sessions/<sid>/messages', methods=['POST'])
@require_permission('admin.kefu')
def human_reply(sid):
    sess = kefu_repo.get_kefu_session_admin(sid)
    if not sess:
        return jsonify({'error': 'not found'}), 404
    if not sess['human_takeover']:
        return jsonify({'error': '会话未处于人工接管状态'}), 409
    content = ((request.get_json(silent=True) or {}).get('content') or '').strip()
    if not content:
        return jsonify({'error': 'content required'}), 400
    mid = kefu_repo.insert_human_message(sid, content, g.current_user['userId'])
    kefu_event_bus.publish(sid, {'type': 'human_message'})
    kefu_event_bus.publish(f"inst:{sess['kefu_instance_id']}", {'sid': sid, 'type': 'human_message'})
    return jsonify({'messageId': mid}), 201


# ==================== 管理端实例事件 SSE ====================

@kefu_admin_bp.route('/events/ticket', methods=['POST'])
@require_permission('admin.kefu')
def events_ticket():
    return jsonify({'ticket': kefu_sse_ticket.issue(g.current_user['userId'])})


@kefu_admin_bp.route('/events', methods=['GET'])
def admin_events():
    uid = kefu_sse_ticket.consume(request.args.get('ticket') or '')
    if not uid:
        return jsonify({'error': 'invalid ticket'}), 401
    iid = (request.args.get('instance') or '').strip()
    if not iid:
        return jsonify({'error': 'instance required'}), 400
    if not _admin_sse_acquire(uid):
        return jsonify({'error': '并发连接过多，请稍后再试'}), 429

    q = kefu_event_bus.subscribe(f"inst:{iid}")

    def generate():
        try:
            yield _format_sse('ready', {})
            while True:
                try:
                    evt = q.get(timeout=ADMIN_SSE_HEARTBEAT)
                    yield _format_sse(evt.get('type', 'message'), evt)
                except _queue.Empty:
                    yield ': ping\n\n'
        except GeneratorExit:
            return
        except Exception:
            return
        finally:
            kefu_event_bus.unsubscribe(f"inst:{iid}", q)
            _admin_sse_release(uid)

    return Response(stream_with_context(generate()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
