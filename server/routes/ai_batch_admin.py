"""批任务的**管理员**视图：跨全部用户查看与重试。

为什么独立成文件而不是塞进 routes/ai_chat_batches.py 或 routes/ai_chat.py：
前者的 url_prefix 被钉死在 /ai/chat/batches，装不下 /ai/chat/admin/batches；
后者已逾千行。更要紧的是，这一层每个端点的安全假设与普通端点**相反**——跨用户查，
而不是按归属过滤。物理隔开能让这条边界一眼可见。

权限一律走 admin.ai_chat_admin（"AI 会话治理"）。不新建能力键：该能力现在已能
列出全部用户的 AI 会话，而批任务的子任务就是会话——能看会话就已经能看到这里的
全部内容，再拆一个键是假粒度。
"""
from flask import Blueprint, jsonify, request

from auth import require_permission
from utils.batch_repo import (MAX_ADMIN_MESSAGES, admin_get_batch_detail,
                              admin_get_child_messages, admin_list_batches)

ai_batch_admin_bp = Blueprint('ai_batch_admin', __name__,
                              url_prefix='/ai/chat/admin/batches')


def _iso(v):
    return v.isoformat() if v else None


def _batch_out(b: dict) -> dict:
    """出参白名单。用固定键集合而不是"删掉几个字段"——后者在数据层新增列时会
    悄悄把内部字段泄出去。"""
    return {
        'batchId': b.get('id'),
        'name': b.get('name'),
        'status': b.get('status'),
        'total': b.get('total'),
        'done': b.get('done'),
        'failed': b.get('failed'),
        'agent': b.get('agent'),
        'model': b.get('model'),
        'createdAt': _iso(b.get('created_at')),
        'completedAt': _iso(b.get('completed_at')),
        'ownerUsername': b.get('owner_username'),
        'source': b.get('source'),
    }


def _session_out(s: dict) -> dict:
    return {
        'sessionId': s.get('id'),
        'seq': s.get('batch_seq'),
        'name': (s.get('batch_input_file') or '').replace('\\', '/').rsplit('/', 1)[-1],
        'status': s.get('status'),
        'error': s.get('error_message'),
        'preview': s.get('last_message_preview'),
    }


@ai_batch_admin_bp.get('')
@require_permission('admin.ai_chat_admin')
def list_all():
    try:
        page = max(1, int(request.args.get('page', 1)))
        page_size = min(max(1, int(request.args.get('pageSize', 20))), 100)
    except (TypeError, ValueError):
        return jsonify({'error': 'page 与 pageSize 必须是整数'}), 400

    data = admin_list_batches(
        page=page, page_size=page_size,
        status=(request.args.get('status') or '').strip() or None,
        owner_keyword=(request.args.get('owner') or '').strip() or None,
        source=(request.args.get('source') or '').strip() or None,
        name_keyword=(request.args.get('keyword') or '').strip() or None,
    )
    return jsonify({'items': [_batch_out(b) for b in data['items']],
                    'total': data['total']})


@ai_batch_admin_bp.get('/<batch_id>')
@require_permission('admin.ai_chat_admin')
def detail(batch_id):
    d = admin_get_batch_detail(batch_id)
    if not d:
        return jsonify({'error': '批任务不存在'}), 404
    return jsonify({'batch': _batch_out(d['batch']),
                    'sessions': [_session_out(s) for s in d['sessions']]})


@ai_batch_admin_bp.get('/<batch_id>/sessions/<sid>/messages')
@require_permission('admin.ai_chat_admin')
def child_messages(batch_id, sid):
    """子任务的完整对话，只读。sid 必须属于该 batch_id，否则 404 ——
    不做这层校验的话，这个路径就成了"用任意 batchId 读任意会话"的通道。"""
    data = admin_get_child_messages(batch_id, sid, limit=MAX_ADMIN_MESSAGES)
    if data is None:
        return jsonify({'error': '子任务不存在'}), 404
    return jsonify({
        'messages': [{'id': m['id'], 'role': m['role'], 'content': m['content'],
                      'createdAt': _iso(m.get('created_at')), 'meta': m.get('meta')}
                     for m in data['messages']],
        'truncated': data['truncated'],
        'total': data['total'],
    })
