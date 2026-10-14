"""运行日志查看端点（spec 2026-10-14-log-viewer-design §4）。

GET /ai/logs/tail?file=chat|serve&offset=&limit= —— 只读跟尾，双文件白名单，
路径不接受用户输入；权限 admin.ai_chat_admin（日志含聊天内容与提示词）。
前端 1.5s 增量轮询消费；跟尾语义细节见 utils/log_tail.py。
"""
import logging
import os

from flask import Blueprint, jsonify, request

from auth import require_permission
from utils.log_tail import DEFAULT_LIMIT, MAX_LIMIT, read_tail

log = logging.getLogger(__name__)

ai_logs_bp = Blueprint('ai_logs', __name__, url_prefix='/ai/logs')

# server/ 目录（routes/ 的上一级）——与 logging_setup / opencode_global 落盘同口径
_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 白名单：逻辑名 → 落盘路径。公开展示给确认页的 ID 集合唯一入口。
LOG_FILES = {
    'chat': os.path.join(_SERVER_DIR, 'ai-chat.log'),
    'serve': os.path.join(_SERVER_DIR, 'oc-serve.log'),
}


@ai_logs_bp.get('/tail')
@require_permission('admin.ai_chat_admin')
def tail():
    name = (request.args.get('file') or '').strip()
    if name not in LOG_FILES:
        return jsonify({'error': 'file 只支持 chat / serve'}), 400
    try:
        offset = int(request.args.get('offset', -1))
        limit = int(request.args.get('limit', DEFAULT_LIMIT))
    except (TypeError, ValueError):
        return jsonify({'error': 'offset/limit 必须是整数'}), 400
    if offset < -1 or limit < 1 or limit > MAX_LIMIT:
        return jsonify({'error': f'offset≥-1；limit 1..{MAX_LIMIT}'}), 400
    try:
        result = read_tail(LOG_FILES[name], offset=offset, limit=limit)
    except Exception as e:  # noqa: BLE001 —— 读失败按不可用上报，留痕
        log.warning('log tail failed file=%s: %s', name, e)
        return jsonify({'error': '读取日志失败'}), 500
    result['file'] = name
    return jsonify(result), 200
