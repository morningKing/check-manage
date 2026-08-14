"""批任务的**管理员**视图：跨全部用户查看与重试。

为什么独立成文件而不是塞进 routes/ai_chat_batches.py 或 routes/ai_chat.py：
前者的 url_prefix 被钉死在 /ai/chat/batches，装不下 /ai/chat/admin/batches；
后者已逾千行。更要紧的是，这一层每个端点的安全假设与普通端点**相反**——跨用户查，
而不是按归属过滤。物理隔开能让这条边界一眼可见。

权限一律走 admin.ai_chat_admin（"AI 会话治理"）。不新建能力键：该能力现在已能
列出全部用户的 AI 会话，而批任务的子任务就是会话——能看会话就已经能看到这里的
全部内容，再拆一个键是假粒度。
"""
import os

from flask import Blueprint, jsonify, request, send_file

from auth import require_permission, require_permission_sse
from utils.batch_engine import get_worker
from utils.batch_repo import (MAX_ADMIN_MESSAGES, admin_get_batch_detail,
                              admin_get_batch_owner, admin_get_child_messages,
                              admin_get_child_session, admin_list_batches,
                              reexecute_child, reset_failed_to_pending)
from utils.operation_log import log_operation
from utils.session_file_import import MAX_IMPORT_PATHS, import_recorded_files
from utils.subtask_repo import get_subtask_messages
from utils.workspace import WorkspacePathError, safe_resolve
from utils.workspace_changes import file_diff, git_changes, read_file_preview
from utils.workspace_outputs import augment_with_data_file_id, list_session_files

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


# --- 写操作 -----------------------------------------------------------------
# 这里**不使用**任何"不按归属过滤的写函数"：先用 admin_get_batch_owner 查出批任务
# 的归属用户，再拿它调用既有的按归属过滤的写函数。读路径泄漏的是可见性，写路径泄漏
# 的是数据完整性，后者严重得多——所以系统里干脆不存在这种写函数。

@ai_batch_admin_bp.post('/<batch_id>/retry-failed')
@require_permission('admin.ai_chat_admin')
def retry_failed(batch_id):
    """重试该批任务中全部失败的子任务。

    刻意**不设终态门**（与对外 API 的 409 相反）：reset_failed_to_pending 只动
    已经 failed 的子任务，一个仍在跑的大批任务里已经挂掉的那几条，管理员应该能
    立刻重试而不必等整批结束。
    """
    owner = admin_get_batch_owner(batch_id)
    if owner is None:
        return jsonify({'error': '批任务不存在'}), 404

    n = reset_failed_to_pending(owner, batch_id)
    if n:
        get_worker().notify()

    # 取名只是为了让日志文案好读；这一步若抛异常（DB 抖动 / 连接池耗尽）不能让重置
    # 已生效但审计写不进去——那就是「管理员代改他人数据无痕」。取不到名字就退化用
    # batch_id，但 log_operation 必须执行到。
    try:
        d = admin_get_batch_detail(batch_id)
        name = (d or {}).get('batch', {}).get('name') or batch_id
    except Exception:
        name = batch_id
    log_operation('update', 'ai_chat_batch', batch_id, name,
                  f'管理员重试批任务「{name}」中 {n} 个失败的子任务')
    return jsonify({'retried': n})


@ai_batch_admin_bp.post('/<batch_id>/sessions/<sid>/reexecute')
@require_permission('admin.ai_chat_admin')
def reexecute(batch_id, sid):
    """重跑单个子任务（从头开始，清掉旧消息）。"""
    owner = admin_get_batch_owner(batch_id)
    if owner is None:
        return jsonify({'error': '批任务不存在'}), 404

    try:
        d = reexecute_child(owner, batch_id, sid)
    except ValueError as e:
        # repo 只在"子任务不是 completed/failed"时抛这个——它正在跑，重跑会与
        # 运行中的会话冲突。409 而不是 500。
        return jsonify({'error': str(e)}), 409
    if d is None:
        return jsonify({'error': '子任务不存在'}), 404

    get_worker().notify()
    log_operation('update', 'ai_chat_batch', batch_id, batch_id,
                  f'管理员重跑批任务 {batch_id} 的子任务 {sid}')
    return jsonify({'reexecuted': True})


@ai_batch_admin_bp.get('/<batch_id>/sessions/<sid>/subtasks/<subtask_id>/messages')
@require_permission('admin.ai_chat_admin')
def child_subtask_messages(batch_id, sid, subtask_id):
    """子代理的完整对话（管理员视角，只读）。不传 owner_user_id——鉴权已经
    在 require_permission 做过，跨用户可见正是管理员视角存在的意义。"""
    data = get_subtask_messages(subtask_id)
    if data is None:
        return jsonify({'error': '子代理不存在'}), 404
    st = data['subtask']
    return jsonify({
        'subtask': {
            'id': st['id'], 'agent': st.get('agent'), 'description': st.get('description'),
            'status': st['status'], 'error': st.get('error_message'),
        },
        'messages': [
            {'id': m['id'], 'role': m['role'], 'content': m['content'],
             'createdAt': m['created_at'].isoformat() if m.get('created_at') else None,
             'meta': m.get('meta')}
            for m in data['messages']
        ],
        'truncated': data['truncated'],
        'total': data['total'],
    })


# --- 产出文件端点（管理员跨用户视角） -----------------------------------------
# 6 个端点全部挂 @require_permission_sse('admin.ai_chat_admin')：download 需要
# ?access_token= 兼容浏览器 <a download> 链接（无法带 Authorization 头），其余
# 5 个走同一装饰器保持一致；该装饰器对 Bearer 头的接受与 require_permission
# 完全同源，普通 admin 客户端调用不受影响。

@ai_batch_admin_bp.get('/<batch_id>/sessions/<sid>/files')
@require_permission_sse('admin.ai_chat_admin')
def list_child_files(batch_id, sid):
    """列子任务产出文件：live scan + LEFT JOIN ai_chat_session_files.data_file_id。

    刻意**只读不写**（spec §1.2）：live-scan 不回写 ai_chat_session_files。
    DB 记录的累积维护由 list_child_changes 端点里的 record_session_files 负责
    （与 owner 端 routes/ai_chat.py::list_changes 同口径）——这里只拿当下能下/
    能导入的文件清单给前端。
    """
    sess = admin_get_child_session(batch_id, sid)
    if sess is None:
        return jsonify({'error': '子任务不存在', 'code': 'NOT_FOUND'}), 404
    if not sess['workspace_path']:
        return jsonify({'files': [], 'truncated': False})
    files, truncated = list_session_files(sess['workspace_path'])
    augment_with_data_file_id(sid, files)
    return jsonify({'files': files, 'truncated': truncated})


@ai_batch_admin_bp.get('/<batch_id>/sessions/<sid>/files/preview')
@require_permission_sse('admin.ai_chat_admin')
def preview_child_file(batch_id, sid):
    """单文件文本预览（封顶），与 routes/ai_chat.py::preview_file 同实现。"""
    sess = admin_get_child_session(batch_id, sid)
    if sess is None:
        return jsonify({'error': '子任务不存在', 'code': 'NOT_FOUND'}), 404
    if not sess['workspace_path']:
        return jsonify({'error': '该子会话没有工作区', 'code': 'NO_WORKSPACE'}), 400
    rel = (request.args.get('path') or '').strip()
    if not rel:
        return jsonify({'error': 'path required', 'code': 'PATH_REQUIRED'}), 400
    try:
        abs_path = safe_resolve(sess['workspace_path'], rel)
    except WorkspacePathError:
        return jsonify({'error': 'bad path', 'code': 'BAD_PATH'}), 400
    if not os.path.isfile(abs_path):
        return jsonify({'error': 'not found', 'code': 'FILE_NOT_FOUND'}), 404
    return jsonify(read_file_preview(abs_path))


@ai_batch_admin_bp.get('/<batch_id>/sessions/<sid>/files/download')
@require_permission_sse('admin.ai_chat_admin')
def download_child_file(batch_id, sid):
    """下载工作区文件。

    `require_permission_sse` 已支持 ?access_token= 透传 JWT，浏览器
    `<a href=...?access_token=>` 下载链接专用。
    """
    sess = admin_get_child_session(batch_id, sid)
    if sess is None:
        return jsonify({'error': '子任务不存在', 'code': 'NOT_FOUND'}), 404
    if not sess['workspace_path']:
        return jsonify({'error': '该子会话没有工作区', 'code': 'NO_WORKSPACE'}), 400
    rel = (request.args.get('path') or '').strip()
    if not rel:
        return jsonify({'error': 'path required', 'code': 'PATH_REQUIRED'}), 400
    try:
        abs_path = safe_resolve(sess['workspace_path'], rel)
    except WorkspacePathError:
        return jsonify({'error': 'bad path', 'code': 'BAD_PATH'}), 400
    if not os.path.isfile(abs_path):
        return jsonify({'error': 'not found', 'code': 'FILE_NOT_FOUND'}), 404
    return send_file(abs_path, as_attachment=True,
                     download_name=os.path.basename(abs_path))


@ai_batch_admin_bp.post('/<batch_id>/sessions/<sid>/files/import')
@require_permission_sse('admin.ai_chat_admin')
def import_child_files(batch_id, sid):
    """导入子任务产出文件到系统 data_files。

    - extra_whitelist = live-scan 所有 file paths（admin 视角放行 outputs/ +
      workspace + uploads 混合，避免 outputs/ 被 .gitignore 屏蔽不入 DB 时拒掉）
    - uploaded_by = 批任务原 owner（不是管理员本人）——守住数据归属
    - log_operation 触发审计：operator 自动是当前管理员（g.current_user），
      target_id 是导入成功的 data_file id 用 ';' 拼接
    """
    sess = admin_get_child_session(batch_id, sid)
    if sess is None:
        return jsonify({'error': '子任务不存在', 'code': 'NOT_FOUND'}), 404
    if not sess['workspace_path']:
        return jsonify({'error': '该子会话没有工作区', 'code': 'NO_WORKSPACE'}), 400
    body = request.get_json(silent=True) or {}
    paths = body.get('paths')
    if not isinstance(paths, list) or not paths:
        return jsonify({'error': 'paths required', 'code': 'PATHS_REQUIRED'}), 400
    if len(paths) > MAX_IMPORT_PATHS:
        return jsonify({'error': f'单次最多导入 {MAX_IMPORT_PATHS} 个文件',
                        'code': 'TOO_MANY'}), 400
    ws = sess['workspace_path']
    files_live, _tr = list_session_files(ws)
    live_paths = {f['path'] for f in files_live}
    results = import_recorded_files(
        sess['id'], ws, paths,
        uploaded_by=sess['ownerUserId'],
        extra_whitelist=live_paths,
    )
    imported_ids = [r['file']['id'] for r in results
                    if r.get('status') in ('imported', 'existing') and r.get('file')]
    # 位置参数而非关键字：与同文件 retry_failed/reexecute 的 log_operation 调用
    # 风格一致，也让审计测试可以 call_args.args[2..4] 直接读 target_id/name/desc。
    log_operation(
        'create', 'data_file',
        ';'.join(imported_ids) if imported_ids else None,
        f'批任务 {batch_id} 子任务 {sid}',
        f'管理员代导入 {len(imported_ids)} 个产出文件（归属 {sess["ownerUserId"]}）',
    )
    return jsonify({'results': results})


@ai_batch_admin_bp.get('/<batch_id>/sessions/<sid>/changes')
@require_permission_sse('admin.ai_chat_admin')
def list_child_changes(batch_id, sid):
    """列 git 变更文件（routes/ai_chat.py::list_changes 的管理员跨用户版）。

    镜像 owner 端语义：ok=True 时顺手 record_session_files 维护
    ai_chat_session_files 记录（这是 git-changes 历史表的维护入口，不是 live
    scan）。list_child_files 端点刻意不写 DB，写 DB 责任全在这里。
    """
    sess = admin_get_child_session(batch_id, sid)
    if sess is None:
        return jsonify({'error': '子任务不存在', 'code': 'NOT_FOUND'}), 404
    if not sess['workspace_path']:
        return jsonify({'changes': [], 'truncated': False, 'ok': True})
    changes, truncated, ok = git_changes(sess['workspace_path'])
    if ok:
        # 延迟导入：与 routes/ai_chat.py:752 同风格，避免顶层循环依赖
        from utils.workspace_changes import record_session_files
        record_session_files(sid, changes)
    return jsonify({'changes': changes, 'truncated': truncated, 'ok': ok})


@ai_batch_admin_bp.get('/<batch_id>/sessions/<sid>/files/diff')
@require_permission_sse('admin.ai_chat_admin')
def child_file_diff(batch_id, sid):
    """单文件 diff（modified）/封顶内容（added）。"""
    sess = admin_get_child_session(batch_id, sid)
    if sess is None:
        return jsonify({'error': '子任务不存在', 'code': 'NOT_FOUND'}), 404
    if not sess['workspace_path']:
        return jsonify({'error': '该子会话没有工作区', 'code': 'NO_WORKSPACE'}), 400
    rel = (request.args.get('path') or '').strip()
    if not rel:
        return jsonify({'error': 'path required', 'code': 'PATH_REQUIRED'}), 400
    try:
        safe_resolve(sess['workspace_path'], rel)  # 校验越界；结果弃用
    except WorkspacePathError:
        return jsonify({'error': 'bad path', 'code': 'BAD_PATH'}), 400
    return jsonify(file_diff(sess['workspace_path'], rel))
