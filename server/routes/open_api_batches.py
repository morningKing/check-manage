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
from db import get_db
from utils.batch_engine import get_worker
from utils.batch_repo import (MAX_FILES_PER_BATCH, append_to_batch, create_batch,
                              delete_batch, get_batch_detail, get_batch_results,
                              list_batches, reset_failed_to_pending)
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


def _validate_files(files: list, owner_user_id: str):
    """校验 files[] 的形状、路径归属与文件是否还在。

    合法返回 None，否则返回可直接 `return` 的 (response, status) 二元组。
    `create` 与 `append` 共用 —— 两处各抄一份必然漂移，而这里每一条都是安全边界：
    漏掉路径归属校验就是横向越权，漏掉存在性校验就会留下一个注定空跑的子任务。
    """
    root = _workspace_root()
    for f in files:
        if not isinstance(f, dict) or not f.get('name') or not f.get('path'):
            return jsonify({'error': '每个文件需包含 name 与 path'}), 400
        if not _validate_staged_path(f['path'], owner_user_id):
            return jsonify({'error': '文件路径无效'}), 400
        # 形状合法 ≠ 文件还在。暂存目录超过 TTL 会被 _sweep_stale_staging 清掉；
        # 不在这里拦住的话，_prepare_workspace 会抛 FileNotFoundError 把子任务直接
        # 标成失败 —— 与其让调用方事后从结果里发现，不如在提交时就明确拒绝。
        if not os.path.isfile(os.path.join(root, str(f['path']).replace('\\', '/'))):
            return jsonify({
                'error': f'文件「{f["name"]}」已过期或不存在，'
                         f'请重新调用 /uploads 上传后再提交'
            }), 400
    return None


def _batch_out(b: dict) -> dict:
    """内部批任务行 → 对外契约字段。刻意只吐这几个，内部字段一律不外泄。

    `callbackSecret` 刻意不回显——调用方自己设置的密钥没必要在每次
    list/detail 轮询里明文回传；只回 `callbackUrl` 供确认当前配置。
    """
    return {
        'batchId': b['id'],
        'name': b.get('name'),
        'status': b.get('status'),
        'total': b.get('total'),
        'done': b.get('done'),
        'failed': b.get('failed'),
        'agent': b.get('agent'),
        'model': b.get('model'),
        'callbackUrl': b.get('callback_url'),
        'createdAt': b['created_at'].isoformat() if b.get('created_at') else None,
        'completedAt': b['completed_at'].isoformat() if b.get('completed_at') else None,
    }


def _validate_callback_url(url: str | None):
    """None/空字符串合法（表示不设置回调）。非空必须是 http(s) URL，否则返回
    可直接 `return` 的 (response, status) 二元组；合法返回 None。"""
    if not url:
        return None
    if not (url.startswith('http://') or url.startswith('https://')):
        return jsonify({'error': 'callbackUrl 必须是 http:// 或 https:// 开头的 URL'}), 400
    return None


@open_api_batches_bp.get('/agents')
@api_key_required
@require_bound_key
def list_agents():
    """列出可用的 OpenCode agent（创建批任务时可指定）。

    返回 primary（可用于批任务 agent 参数）和 subagent（仅供参考）两组。
    过滤掉 OpenCode 内部 agent（compaction/title/summary）。
    """
    from utils.opencode_client import OpenCodeClient
    from config import OPENCODE_BASE_URL
    try:
        raw = OpenCodeClient(OPENCODE_BASE_URL).list_agents()
    except Exception as e:
        return jsonify({'error': f'OpenCode 不可用: {e}',
                        'agents': [], 'default': None}), 502
    _INTERNAL = {'compaction', 'title', 'summary'}
    agents = [
        {'name': a.get('name'), 'description': a.get('description') or ''}
        for a in (raw or [])
        if a.get('mode') == 'primary' and a.get('name') not in _INTERNAL
    ]
    names = {a['name'] for a in agents}
    default = 'build' if 'build' in names else (agents[0]['name'] if agents else None)
    return jsonify({'agents': agents, 'default': default})


@open_api_batches_bp.get('/models')
@api_key_required
@require_bound_key
def list_models():
    """列出可用的 LLM 模型（创建批任务时可指定 model 参数）。

    只返回已连接的 provider 下的模型。id 格式为 `<providerID>/<modelID>`，
    可直接作为创建批任务时的 model 参数。
    """
    from utils.opencode_client import OpenCodeClient
    from config import OPENCODE_BASE_URL, OPENCODE_MODEL
    try:
        provider_info = OpenCodeClient(OPENCODE_BASE_URL).list_providers()
    except Exception as e:
        return jsonify({'error': f'OpenCode 不可用: {e}',
                        'models': [], 'default': ''}), 502
    connected = provider_info.get('connected') or {}
    if isinstance(connected, list):
        connected_set = set(connected)
    else:
        connected_set = {pid for pid, ok in connected.items() if ok}
    models = []
    for prov in provider_info.get('all') or []:
        pid = prov.get('id')
        pname = prov.get('name') or pid
        if pid not in connected_set:
            continue
        for mid, mdef in (prov.get('models') or {}).items():
            models.append({
                'id': f'{pid}/{mid}',
                'label': f'{pname} / {mdef.get("name") or mid}',
                'providerID': pid,
                'modelID': mid,
            })
    models.sort(key=lambda m: (m['label'].lower(), m['id']))
    return jsonify({
        'models': models,
        'default': OPENCODE_MODEL or '',
    })


@open_api_batches_bp.get('/skills')
@api_key_required
@require_bound_key
def list_skills():
    """列出可用的全局 skill（创建批任务时可指定 skills 参数）。

    只返回 enabled 的 skill。
    """
    from utils.global_skills import list_global_skills
    skills = [
        {'name': s['name'], 'description': s.get('description', '')}
        for s in list_global_skills()
        if s.get('enabled')
    ]
    return jsonify({'skills': skills})


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
    callback_url = (body.get('callbackUrl') or '').strip() or None
    callback_secret = (body.get('callbackSecret') or '').strip() or None

    if not name or not prompt:
        return jsonify({'error': 'name 与 prompt 均为必填'}), 400
    if len(prompt) > MAX_PROMPT_CHARS:
        return jsonify({'error': f'prompt 超过 {MAX_PROMPT_CHARS} 字符的上限'}), 400
    if not isinstance(files, list) or not files:
        return jsonify({'error': '请至少提供一个文件'}), 400
    if len(files) > MAX_FILES_PER_BATCH:
        return jsonify({'error': f'单批最多 {MAX_FILES_PER_BATCH} 个文件'}), 400
    err = _validate_callback_url(callback_url)
    if err:
        return err

    err = _validate_files(files, owner)
    if err:
        return err

    result = create_batch(
        owner,
        name=name, prompt=prompt, template_id=None, files=files,
        agent=(body.get('agent') or '').strip() or None,
        model=(body.get('model') or '').strip() or None,
        api_key_id=key['id'],
        callback_url=callback_url,
        callback_secret=callback_secret,
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


def _child_name(s) -> str:
    """与 /results 的 name 同口径：batch_input_file 的 basename。"""
    return (s.get('batch_input_file') or '').replace('\\', '/').rsplit('/', 1)[-1]


@open_api_batches_bp.get('/<batch_id>/file-records')
@api_key_required
@require_bound_key
def file_records(batch_id):
    """每个子会话被自动记录的新增/修改文件（ai_chat_session_files）。
    `name` 与 /results 同口径，`seq` 是稳定序号——POST /import 用二者定位子会话。"""
    from utils.workspace_changes import get_session_files
    key = _current_key()
    d = get_batch_detail(key['ownerUserId'], batch_id, api_key_id=key['id'])
    if not d:
        return jsonify({'error': '批任务不存在'}), 404
    out = []
    for s in d['sessions']:
        out.append({
            'name': _child_name(s),
            'seq': s.get('batch_seq'),
            'status': s.get('status'),
            'files': get_session_files(s['id']),
        })
    return jsonify({'batchId': batch_id, 'status': d['batch']['status'],
                    'results': out})


@open_api_batches_bp.post('/<batch_id>/import')
@api_key_required
@require_bound_key
def import_files(batch_id):
    """把子会话工作区里被记录过的文件按需导入系统 data_files，返回文件 id。
    body: {'name' 或 'seq'（二选一定位子会话）, 'paths': [...]}。幂等——
    已导入过的路径返回原 id。uploaded_by 为 None（API key 来源）。"""
    from utils.session_file_import import import_recorded_files, MAX_IMPORT_PATHS
    key = _current_key()
    d = get_batch_detail(key['ownerUserId'], batch_id, api_key_id=key['id'])
    if not d:
        return jsonify({'error': '批任务不存在'}), 404
    body = request.get_json(silent=True) or {}
    paths = body.get('paths')
    if not isinstance(paths, list) or not paths:
        return jsonify({'error': 'paths required'}), 400
    if len(paths) > MAX_IMPORT_PATHS:
        return jsonify({'error': f'单次最多导入 {MAX_IMPORT_PATHS} 个文件'}), 400
    name, seq = body.get('name'), body.get('seq')
    child = None
    if seq is not None:
        child = next((s for s in d['sessions'] if s.get('batch_seq') == seq), None)
    elif name:
        child = next((s for s in d['sessions'] if _child_name(s) == name), None)
    else:
        return jsonify({'error': '需提供 name 或 seq 定位子会话'}), 400
    if child is None:
        return jsonify({'error': '子会话不存在'}), 404
    if not child.get('workspace_path'):
        return jsonify({'error': '该子会话没有工作区'}), 400
    results = import_recorded_files(child['id'], child['workspace_path'], paths,
                                    uploaded_by=None)
    return jsonify({'batchId': batch_id, 'name': _child_name(child),
                    'seq': child.get('batch_seq'), 'results': results})


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


@open_api_batches_bp.post('/<batch_id>/append')
@api_key_required
@require_bound_key
def append(batch_id):
    """向已有批任务追加文件。

    刻意**不限终态**（与 retry-failed 不同）：追加的语义是「这批还没做完，再加几个」，
    对一个已经 completed 的批任务追加是合法用法。追加后批任务重回 running，
    total += N，worker 自动捡起新子任务。已完成的子任务不受影响、不会重跑。
    """
    key = _current_key()
    owner = key['ownerUserId']
    body = request.get_json(silent=True) or {}
    files = body.get('files') or []

    if not isinstance(files, list) or not files:
        return jsonify({'error': '请至少提供一个文件'}), 400
    if len(files) > MAX_FILES_PER_BATCH:
        return jsonify({'error': f'单次最多追加 {MAX_FILES_PER_BATCH} 个文件'}), 400

    err = _validate_files(files, owner)
    if err:
        return err

    try:
        d = append_to_batch(owner, batch_id, files, api_key_id=key['id'])
    except ValueError as e:
        # 唯一的 ValueError 来源是「追加后超过单批文件数上限」（空 files 已在上面挡掉）。
        return jsonify({'error': str(e)}), 400
    if not d:
        return jsonify({'error': '批任务不存在'}), 404

    get_worker().notify()
    b = d['batch']
    return jsonify({'batchId': b['id'], 'status': b['status'],
                    'total': b['total'], 'appended': len(files)})


# ---------------------------------------------------------------------------
# 子会话级别端点：对话 / 文件 / 继续对话
# ---------------------------------------------------------------------------

def _resolve_child(d: dict, child_id: str):
    """从批任务详情中定位子会话。

    child_id 为纯数字时按 batch_seq 匹配，否则按 batch_input_file 的 basename 匹配。
    返回 (child_dict, error_response) 二元组——找到时 error_response 为 None。
    """
    sessions = d.get('sessions') or []
    child = None
    if child_id.isdigit():
        child = next((s for s in sessions if s.get('batch_seq') == int(child_id)), None)
    else:
        child = next((s for s in sessions if _child_name(s) == child_id), None)
    if child is None:
        return None, (jsonify({'error': '子会话不存在'}), 404)
    return child, None


@open_api_batches_bp.get('/<batch_id>/sessions/<child_id>/messages')
@api_key_required
@require_bound_key
def session_messages(batch_id, child_id):
    """获取子会话的完整对话历史。"""
    from utils.batch_repo import get_child_messages
    key = _current_key()
    d = get_batch_detail(key['ownerUserId'], batch_id, api_key_id=key['id'])
    if not d:
        return jsonify({'error': '批任务不存在'}), 404
    child, err = _resolve_child(d, child_id)
    if err:
        return err
    messages = get_child_messages(child['id'])
    return jsonify({
        'batchId': batch_id,
        'child': {'name': _child_name(child), 'seq': child.get('batch_seq'),
                   'status': child.get('status')},
        'messages': messages['messages'],
        'total': messages['total'],
        'truncated': messages['truncated'],
    })


@open_api_batches_bp.get('/<batch_id>/sessions/<child_id>/files')
@api_key_required
@require_bound_key
def session_files(batch_id, child_id):
    """实时扫描子会话工作区，返回当前文件列表。"""
    from utils.workspace_outputs import list_session_files
    key = _current_key()
    d = get_batch_detail(key['ownerUserId'], batch_id, api_key_id=key['id'])
    if not d:
        return jsonify({'error': '批任务不存在'}), 404
    child, err = _resolve_child(d, child_id)
    if err:
        return err
    ws = child.get('workspace_path')
    if not ws:
        return jsonify({
            'batchId': batch_id,
            'child': {'name': _child_name(child), 'seq': child.get('batch_seq'),
                       'status': child.get('status')},
            'files': [],
        })
    files, _truncated = list_session_files(ws)
    return jsonify({
        'batchId': batch_id,
        'child': {'name': _child_name(child), 'seq': child.get('batch_seq'),
                   'status': child.get('status')},
        'files': files,
    })


@open_api_batches_bp.get('/<batch_id>/sessions/<child_id>/files/download')
@api_key_required
@require_bound_key
def session_file_download(batch_id, child_id):
    """下载子会话工作区中的单个文件。"""
    from flask import send_file
    key = _current_key()
    d = get_batch_detail(key['ownerUserId'], batch_id, api_key_id=key['id'])
    if not d:
        return jsonify({'error': '批任务不存在'}), 404
    child, err = _resolve_child(d, child_id)
    if err:
        return err
    ws = child.get('workspace_path')
    if not ws:
        return jsonify({'error': '该子会话没有工作区'}), 400
    rel_path = request.args.get('path', '').strip()
    if not rel_path:
        return jsonify({'error': 'path 参数必填'}), 400
    # 安全校验：禁止 .. 穿越
    normalized = os.path.normpath(rel_path)
    if '..' in normalized.split(os.sep):
        return jsonify({'error': '路径非法'}), 400
    abs_path = os.path.join(ws, normalized)
    # 确保路径落在工作区内
    if not os.path.commonpath([ws, abs_path]).startswith(ws):
        return jsonify({'error': '路径非法'}), 400
    if not os.path.isfile(abs_path):
        return jsonify({'error': '文件不存在'}), 404
    return send_file(abs_path, as_attachment=True)


@open_api_batches_bp.get('/<batch_id>/sessions/<child_id>/files/download-all')
@api_key_required
@require_bound_key
def session_files_download_all(batch_id, child_id):
    """将子会话工作区中所有新增和修改的文件打包为 ZIP 下载。"""
    import io
    import zipfile
    from utils.workspace_changes import get_session_files as get_recorded_files
    key = _current_key()
    d = get_batch_detail(key['ownerUserId'], batch_id, api_key_id=key['id'])
    if not d:
        return jsonify({'error': '批任务不存在'}), 404
    child, err = _resolve_child(d, child_id)
    if err:
        return err
    ws = child.get('workspace_path')
    if not ws:
        return jsonify({'error': '该子会话没有工作区'}), 400
    # 筛选参数
    include_str = request.args.get('include', 'added,modified')
    include_statuses = {s.strip() for s in include_str.split(',') if s.strip()}
    # 获取变更记录
    records = get_recorded_files(child['id'])
    # 构建 ZIP
    child_name = _child_name(child)
    # 去掉扩展名，截断至 50 字符
    zip_root = os.path.splitext(child_name)[0][:50] or 'session'
    buf = io.BytesIO()
    skipped = []
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for rec in records:
            if rec.get('status') not in include_statuses:
                continue
            rel_path = rec.get('path', '')
            if not rel_path:
                continue
            # 安全校验
            normalized = os.path.normpath(rel_path)
            if '..' in normalized.split(os.sep):
                continue
            abs_path = os.path.join(ws, normalized)
            if not os.path.commonpath([ws, abs_path]).startswith(ws):
                continue
            if not os.path.isfile(abs_path):
                skipped.append(rel_path)
                continue
            # 单文件过大跳过
            try:
                if os.path.getsize(abs_path) > 50 * 1024 * 1024:
                    skipped.append(rel_path)
                    continue
            except OSError:
                skipped.append(rel_path)
                continue
            arc_name = f'{zip_root}/{rel_path}'
            try:
                zf.write(abs_path, arc_name)
            except Exception:
                skipped.append(rel_path)
    buf.seek(0)
    from flask import send_file
    return send_file(
        buf,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'{zip_root}.zip',
    )


@open_api_batches_bp.post('/<batch_id>/sessions/<child_id>/continue')
@api_key_required
@require_bound_key
def session_continue(batch_id, child_id):
    """在已完成/失败的子会话上追加一轮对话，保留历史上下文。"""
    from utils.batch_repo import continue_child
    key = _current_key()
    d = get_batch_detail(key['ownerUserId'], batch_id, api_key_id=key['id'])
    if not d:
        return jsonify({'error': '批任务不存在'}), 404
    child, err = _resolve_child(d, child_id)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    prompt = (body.get('prompt') or '').strip()
    if not prompt:
        return jsonify({'error': 'prompt 必填'}), 400
    if len(prompt) > MAX_PROMPT_CHARS:
        return jsonify({'error': f'prompt 超过 {MAX_PROMPT_CHARS} 字符的上限'}), 400
    try:
        continue_child(key['ownerUserId'], batch_id, child['id'], prompt)
    except ValueError as e:
        return jsonify({'error': str(e)}), 409
    get_worker().notify()
    return jsonify({
        'batchId': batch_id,
        'child': {'name': _child_name(child), 'seq': child.get('batch_seq')},
        'status': 'running',
    }), 202


@open_api_batches_bp.post('/<batch_id>/sessions/<child_id>/reexecute')
@api_key_required
@require_bound_key
def session_reexecute(batch_id, child_id):
    """重新执行单个子会话（清掉历史，从头开始）。

    与 continue 不同：删除旧对话、新建 OpenCode 会话、用批任务原始 prompt 重跑。
    与 retry-failed 不同：可对已完成的子会话重执行，不限于 failed。
    仅限终态（completed/failed）的子会话。
    """
    from utils.batch_repo import reexecute_child
    key = _current_key()
    d = get_batch_detail(key['ownerUserId'], batch_id, api_key_id=key['id'])
    if not d:
        return jsonify({'error': '批任务不存在'}), 404
    child, err = _resolve_child(d, child_id)
    if err:
        return err
    try:
        reexecute_child(key['ownerUserId'], batch_id, child['id'])
    except ValueError as e:
        return jsonify({'error': str(e)}), 409
    get_worker().notify()
    return jsonify({
        'batchId': batch_id,
        'child': {'name': _child_name(child), 'seq': child.get('batch_seq')},
        'status': 'running',
    })


@open_api_batches_bp.patch('/<batch_id>')
@api_key_required
@require_bound_key
def update_config(batch_id):
    """修改批任务的 agent/model/callback 配置。

    整体替换语义，不是局部 patch：省略的字段会被清空为默认值（空字符串同样
    视为清空），所以每次调用都要把想保留的字段一起带上——包括 callbackUrl/
    callbackSecret，只改 agent/model 而不重传它们会把已配置的回调清掉。
    修改在下一次 worker 拾取时生效（retry / reexecute / continue / pending）。
    """
    from utils.batch_repo import update_batch_config
    key = _current_key()
    body = request.get_json(silent=True) or {}
    agent = (body.get('agent') or '').strip() or None
    model = (body.get('model') or '').strip() or None
    callback_url = (body.get('callbackUrl') or '').strip() or None
    callback_secret = (body.get('callbackSecret') or '').strip() or None
    err = _validate_callback_url(callback_url)
    if err:
        return err
    result = update_batch_config(
        key['ownerUserId'], batch_id,
        agent=agent, model=model,
        api_key_id=key['id'],
        callback_url=callback_url,
        callback_secret=callback_secret,
    )
    if result is None:
        return jsonify({'error': '批任务不存在'}), 404
    return jsonify(_batch_out(result['batch']))


@open_api_batches_bp.post('/query')
@api_key_required
@require_bound_key
def ai_query():
    """自然语言查询翻译：将中文/英文问题转换为 MongoDB 风格的过滤器 JSON。

    不执行查询本身，只返回 filter。调用方可用返回的 filter 调用
    GET /v1/collections/<collection>?q=<filter> 获取实际数据。
    """
    from utils.ai_query import nl_to_mongo_filter
    from utils.mongo_query import translate as mongo_translate, remap_labels, MongoQueryError
    body = request.get_json(force=True)
    collection = (body.get('collection') or '').strip()
    question = (body.get('question') or '').strip()

    if not collection or not question:
        return jsonify({'error': 'collection 和 question 均为必填'}), 400

    # 获取字段 schema
    page_id = f'page-{collection}'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT fields FROM page_configs WHERE id = %s', (page_id,))
        row = cur.fetchone()

    if not row or not row[0]:
        return jsonify({'error': f'未找到页面配置: {collection}'}), 404

    fields = row[0]

    # 调用 LLM 翻译
    try:
        raw_filter = nl_to_mongo_filter(question, fields, collection)
    except RuntimeError as e:
        status = 503 if 'API Key' in str(e) else 500
        return jsonify({'error': str(e)}), status

    # 安全回落：修正 LLM 可能使用的中文标签
    safe_filter = remap_labels(raw_filter, fields)

    # 通过 mongo_translate 做语法校验（dry run）
    try:
        mongo_translate(safe_filter)
    except MongoQueryError as e:
        return jsonify({
            'error': f'AI 生成的查询语法无效: {e}',
            'raw_filter': safe_filter,
        }), 422

    return jsonify({'filter': safe_filter})
