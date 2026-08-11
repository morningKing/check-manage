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
from utils.filename import safe_filename
from utils.workspace import batch_staging_dir, WorkspacePathError

open_api_batches_bp = Blueprint('open_api_batches', __name__,
                                url_prefix='/api/v1/ai-batches')

MAX_FILE_BYTES = 20 * 1024 * 1024          # 单个文件 20 MB
MAX_UPLOAD_TOTAL_BYTES = 100 * 1024 * 1024  # 单次上传总计 100 MB
STAGING_TTL_SECONDS = 24 * 3600            # 暂存目录保留 24 小时


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
    """清理该属主下超过 TTL 未被使用的暂存目录。

    上传后从不创建批任务的话，暂存目录会永久堆积。对外后任何持 key 的人都能
    无限制刷，所以在上传时顺带清 —— 零新增组件，且触发频率与滥用程度天然正相关。
    已被 create_batch 消费掉的目录本就不在了，不受影响。
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
