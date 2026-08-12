"""AI 批任务对外 API（/api/v1/ai-batches）。

这是一层**对外契约层**：只调 batch_repo / batch_engine / workspace 的底层函数，
刻意不复用内部 routes/ai_chat_batches.py 的处理器。原因是内部返回体是给前端用的
（含 opencode_session_id / workspace_path 等实现细节），一旦对外就变成公开承诺，
以后为前端改字段会破坏外部集成。这里只吐出约定好的字段。
"""

import os
import time
import uuid
from pathlib import PurePosixPath

from flask import Blueprint, g, jsonify, request

from auth import api_key_required
from utils.batch_engine import get_worker
from utils.batch_repo import (MAX_FILES_PER_BATCH, create_batch, delete_batch,
                              get_batch_detail, get_batch_results, list_batches,
                              reset_failed_to_pending)
from utils.filename import safe_filename
# 请求体上限来自零依赖的共享模块：同一组数字 proxy.py 也要用（它在
# rfile.read() 之前拦截），两处各写一遍必然漂移。见 utils/upload_limits.py。
from utils.upload_limits import (MAX_JSON_BODY_BYTES, MAX_UPLOAD_REQUEST_BYTES,
                                 MAX_UPLOAD_TOTAL_BYTES, body_limit_for_path)
from utils.workspace import batch_staging_dir, cleanup_batch_workspaces, WorkspacePathError

open_api_batches_bp = Blueprint('open_api_batches', __name__,
                                url_prefix='/v1/ai-batches')

MAX_FILE_BYTES = 20 * 1024 * 1024          # 单个文件 20 MB
STAGING_TTL_SECONDS = 24 * 3600            # 暂存目录保留 24 小时
MAX_PROMPT_CHARS = 20000
TERMINAL_STATUSES = ('completed', 'partial', 'failed')  # 重试/结果读取允许的终态


@open_api_batches_bp.before_request
def _guard_request_body_size():
    """按 Content-Length 在 **Werkzeug 解析 body 之前** 卡住超大请求。

    视图里的 20 MB / 100 MB 判断发生在 `f.read()` 之后 —— 那时整个 multipart
    body 已经被解析（大 body 会落到临时文件），一个 10 GB 的请求体能在解析阶段
    就占满 waitress 的线程池并写爆临时目录，20 MB 那道门一次都跑不到。
    Werkzeug 的表单解析是惰性的（首次访问 request.files/form 才触发），本钩子在
    视图之前跑、且只读请求头，所以超限请求在 body 被解析前就被拒。

    刻意**不设**全应用的 `MAX_CONTENT_LENGTH`：备份还原端点
    (`/backups/upload-restore`) 接收的完整备份 ZIP 现在含 `vector_store/` 与
    `data_files/`，大小本质无上界，任何一个拍脑袋的全局值都会直接打断还原。
    另外 Flask 3.0 的 `request.max_content_length` 是只读属性（没有 setter，
    Flask 3.1 才支持逐请求覆盖），所以逐请求限制只能靠这里读 `content_length`。

    ⚠️ 这道门只覆盖直连后端 / 经 Vite 开发代理的请求。生产入口 `proxy.py` 在
    转发前就把整个 body 读进代理进程内存（`self.rfile.read(content_length)`），
    发生在本钩子之前 —— 所以 `proxy.py` 里有一道**同源**的前置门（共用
    `utils/upload_limits.py` 的数值），两处必须都在。
    """
    limit = body_limit_for_path(request.path) or MAX_JSON_BODY_BYTES
    length = request.content_length
    if length is None:
        # 没有 Content-Length 就无法预判大小。分块传输一律拒绝（生产入口
        # proxy.py 本来也只转发带 Content-Length 的请求体）；其余无 body 的
        # GET/DELETE 正常放行。
        if 'chunked' in (request.headers.get('Transfer-Encoding') or '').lower():
            return jsonify({'error': '请求必须携带 Content-Length，不支持分块传输'}), 411
        return None
    if length > limit:
        return jsonify({
            'error': f'请求体超过 {limit // 1024 // 1024} MB 的上限'
        }), 413
    return None


def _current_key() -> dict:
    """当前请求的密钥信息。抽成函数便于测试打桩。"""
    return getattr(g, 'api_key_info', {}) or {}


def _workspace_root() -> str:
    return os.environ.get('AI_CHAT_WORKSPACE_ROOT', 'ai-workspaces')


def require_bound_key(f):
    """挡住未绑定用户的密钥。

    存量密钥的 owner_user_id 是 NULL。放行它们会导致外部调用以某个人的名义
    跑 AI、烧他的额度、把任务塞进他的侧边栏 —— 所以一律拒绝，要求重建密钥。
    """
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if not _current_key().get('ownerUserId'):
            return jsonify({'error': '该密钥未绑定用户，请在密钥管理中重新创建'}), 403
        return f(*args, **kwargs)
    return decorated


def _validate_staged_path(path: str, owner_user_id: str) -> bool:
    """校验 files[].path 确实落在本密钥属主的暂存目录下。

    内部创建接口不做这个校验（它在 JWT 之后，路径由前端从上传响应原样带回）。
    对外必须做：否则调用方可以构造 batch-staging/<别人的userId>/... 或用 ..
    往上跳，把别人工作区的文件塞进自己的批任务让 AI 读出来。
    """
    if not path or not owner_user_id:
        return False
    normalized = str(path).replace('\\', '/')
    if normalized.startswith('/'):
        return False
    parts = PurePosixPath(normalized).parts
    if any(p == '..' for p in parts):
        return False
    if len(parts) < 3:
        return False
    return parts[0] == 'batch-staging' and parts[1] == owner_user_id


def _sweep_stale_staging(root: str, owner_user_id: str) -> None:
    """清理该属主下超过 TTL 未被动过的暂存目录。

    上传后从不创建批任务的话，暂存目录会永久堆积。对外后任何持 key 的人都能
    无限制刷，所以在上传时顺带清 —— 零新增组件，且触发频率与滥用程度天然正相关。

    ⚠️ 两点必须讲清，别照着「已用过的目录不受影响」这种直觉理解：
    1. **创建批任务不会消费掉暂存目录**：create_batch 只写 DB 行（batch_input_file
       存的就是这条暂存路径），真正读取发生在 batch_engine._prepare_workspace，
       用的是 shutil.copy2（复制，不是移动）。所以暂存目录在批任务整个生命周期内
       一直躺在原地，超过 TTL 一样会被这里删掉。已经跑完的批任务不受影响（文件早
       复制进各自工作区了），但**尚未开始跑**的批任务会因此丢掉输入 —— 这正是
       create() 里加文件存在性校验的原因：超期的暂存路径在创建时就被 400 挡下，
       不会留下一个注定空跑的批任务。
    2. **按用户维度扫全目录**：batch-staging/<userId>/ 这棵树是内外共用的（界面
       上传走 routes/ai_chat_batches.py::staging_upload，落的是同一个用户目录），
       所以一次 API 上传会顺带清掉该用户在**界面上**传了超过 TTL 还没用的暂存
       文件。已在对外文档「限制 / 注意事项」里写明。
    """
    import shutil
    base = os.path.join(root, 'batch-staging', owner_user_id)
    if not os.path.isdir(base):
        return
    cutoff = time.time() - STAGING_TTL_SECONDS
    for entry in os.scandir(base):
        try:
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry.path, ignore_errors=True)
        except OSError:
            pass


@open_api_batches_bp.post('/uploads')
@api_key_required
@require_bound_key
def upload_files():
    owner = _current_key()['ownerUserId']
    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': '请通过 files 字段上传至少一个文件'}), 400

    root = _workspace_root()
    _sweep_stale_staging(root, owner)

    upload_session_id = uuid.uuid4().hex[:16]
    try:
        staging = batch_staging_dir(root, owner, upload_session_id)
    except WorkspacePathError as e:
        return jsonify({'error': str(e)}), 400

    total = 0
    saved = []
    for f in files:
        name = safe_filename(f.filename or '')
        if not name:
            return jsonify({'error': '文件名无效'}), 400
        blob = f.read()
        if len(blob) > MAX_FILE_BYTES:
            return jsonify({
                'error': f'文件「{name}」超过单个文件 '
                         f'{MAX_FILE_BYTES // 1024 // 1024} MB 的上限'
            }), 400
        total += len(blob)
        if total > MAX_UPLOAD_TOTAL_BYTES:
            return jsonify({
                'error': f'单次上传总大小超过 '
                         f'{MAX_UPLOAD_TOTAL_BYTES // 1024 // 1024} MB 的上限'
            }), 400
        dest = staging / name
        dest.write_bytes(blob)
        saved.append({'name': name,
                      'path': dest.relative_to(root).as_posix()})

    return jsonify({'files': saved}), 201


def _batch_out(b: dict) -> dict:
    """内部批任务行 → 对外契约字段。刻意只吐这几个，内部字段一律不外泄。"""
    return {
        'batchId': b['id'],
        'name': b.get('name'),
        'status': b.get('status'),
        'total': b.get('total'),
        'done': b.get('done'),
        'failed': b.get('failed'),
        'agent': b.get('agent'),
        'model': b.get('model'),
        'createdAt': b['created_at'].isoformat() if b.get('created_at') else None,
        'completedAt': b['completed_at'].isoformat() if b.get('completed_at') else None,
    }


@open_api_batches_bp.post('')
@api_key_required
@require_bound_key
def create():
    key = _current_key()
    owner = key['ownerUserId']
    body = request.get_json(silent=True) or {}

    name = (body.get('name') or '').strip()
    prompt = (body.get('prompt') or '').strip()
    files = body.get('files') or []

    if not name or not prompt:
        return jsonify({'error': 'name 与 prompt 均为必填'}), 400
    if len(prompt) > MAX_PROMPT_CHARS:
        return jsonify({'error': f'prompt 超过 {MAX_PROMPT_CHARS} 字符的上限'}), 400
    if not isinstance(files, list) or not files:
        return jsonify({'error': '请至少提供一个文件'}), 400
    if len(files) > MAX_FILES_PER_BATCH:
        return jsonify({'error': f'单批最多 {MAX_FILES_PER_BATCH} 个文件'}), 400

    root = _workspace_root()
    for f in files:
        if not isinstance(f, dict) or not f.get('name') or not f.get('path'):
            return jsonify({'error': '每个文件需包含 name 与 path'}), 400
        if not _validate_staged_path(f['path'], owner):
            return jsonify({'error': '文件路径无效'}), 400
        # 形状合法 ≠ 文件还在。暂存目录超过 TTL 会被 _sweep_stale_staging 清掉；
        # 不在这里拦住的话，_prepare_workspace 会建出一个空的 uploads/，AI 拿着
        # 「请先读取 uploads/xxx」的提示在空工作区里跑完，子任务被标成 completed、
        # output 是一段「我读不到文件」的垃圾，集成方毫无察觉。
        if not os.path.isfile(os.path.join(root, str(f['path']).replace('\\', '/'))):
            return jsonify({
                'error': f'文件「{f["name"]}」已过期或不存在，'
                         f'请重新调用 /uploads 上传后再创建批任务'
            }), 400

    result = create_batch(
        owner,
        name=name, prompt=prompt, template_id=None, files=files,
        agent=(body.get('agent') or '').strip() or None,
        model=(body.get('model') or '').strip() or None,
        api_key_id=key['id'],
    )
    get_worker().notify()
    b = result['batch']
    return jsonify({'batchId': b['id'], 'status': b['status'], 'total': b['total']}), 201


@open_api_batches_bp.get('')
@api_key_required
@require_bound_key
def list_():
    key = _current_key()
    try:
        page = max(1, int(request.args.get('page', 1)))
        page_size = min(max(1, int(request.args.get('pageSize', 20))), 100)
    except (TypeError, ValueError):
        return jsonify({'error': 'page 与 pageSize 必须是整数'}), 400

    data = list_batches(key['ownerUserId'], page=page, page_size=page_size,
                        api_key_id=key['id'])
    return jsonify({'items': [_batch_out(b) for b in data['items']],
                    'total': data['total']})


@open_api_batches_bp.get('/<batch_id>')
@api_key_required
@require_bound_key
def detail(batch_id):
    key = _current_key()
    d = get_batch_detail(key['ownerUserId'], batch_id, api_key_id=key['id'])
    if not d:
        return jsonify({'error': '批任务不存在'}), 404
    return jsonify(_batch_out(d['batch']))


@open_api_batches_bp.get('/<batch_id>/results')
@api_key_required
@require_bound_key
def results(batch_id):
    key = _current_key()
    d = get_batch_detail(key['ownerUserId'], batch_id, api_key_id=key['id'])
    if not d:
        return jsonify({'error': '批任务不存在'}), 404
    return jsonify({
        'batchId': batch_id,
        'status': d['batch']['status'],
        'results': get_batch_results(batch_id),
    })


@open_api_batches_bp.delete('/<batch_id>')
@api_key_required
@require_bound_key
def remove(batch_id):
    key = _current_key()
    owner = key['ownerUserId']
    # Best-effort workspace teardown before the DB delete — same shared helper
    # routes/ai_chat_batches.py::remove uses, see utils/workspace.py::
    # cleanup_batch_workspaces. Skipped (not fatal) if the batch can't be found
    # under this key: delete_batch below is the actual 404 authority.
    d = get_batch_detail(owner, batch_id, api_key_id=key['id'])
    if d:
        cleanup_batch_workspaces(_workspace_root(), owner, d['sessions'])
    ok = delete_batch(owner, batch_id, api_key_id=key['id'])
    if not ok:
        return jsonify({'error': '批任务不存在'}), 404
    return jsonify({'deleted': True})


@open_api_batches_bp.post('/<batch_id>/retry-failed')
@api_key_required
@require_bound_key
def retry_failed(batch_id):
    key = _current_key()
    d = get_batch_detail(key['ownerUserId'], batch_id, api_key_id=key['id'])
    if not d:
        return jsonify({'error': '批任务不存在'}), 404
    if d['batch']['status'] not in TERMINAL_STATUSES:
        return jsonify({'error': '该批任务仍在执行中'}), 409

    n = reset_failed_to_pending(key['ownerUserId'], batch_id, api_key_id=key['id'])
    if n:
        get_worker().notify()
    return jsonify({'retried': n})
