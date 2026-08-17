"""长期记忆（mem0）对外管理（/api/v1/memories/*）。

直接复用 utils/memory.py 的 list_memories/add_memory_text/get_memory/delete_memory，
**完整照抄** routes/ai.py::delete_my_memory 的鉴权模式——这是本文件唯一的安全
关键点：utils/memory.py::delete_memory(memory_id) 不按 user_id 过滤（直接调
mem0 的 m.delete(memory_id=...)），所以删除前必须先 list_memories(owner) 建一个
"这把密钥能看到的记忆 id 集合"，确认目标 id 在集合里才能删——否则外部调用方
只要拿到/猜到任意一个 memory_id（mem0 的不透明 UUID），就能删除别的用户的记忆
（IDOR）。内部端点已经用这道路由层闸门堵住了，这里必须原样照抄，不能因为是
新代码就漏掉。

响应形状（GET /memories 的 {'memories': [...]}）原样透传 mem0 返回的 dict，
不做字段裁剪——内部端点也是这么做的，mem0 的返回形状不是这个仓库能控制的，
没必要另起一套契约。
"""

from flask import Blueprint, g, jsonify, request

from auth import api_key_required, require_bound_key
from utils.memory import add_memory_text, delete_memory, get_memory, list_memories

open_api_memories_bp = Blueprint(
    'open_api_memories', __name__, url_prefix='/v1/memories')


def _current_key() -> dict:
    return getattr(g, 'api_key_info', {}) or {}


@open_api_memories_bp.get('')
@api_key_required
@require_bound_key
def list_my_memories():
    owner = _current_key()['ownerUserId']
    return jsonify({'memories': list_memories(owner)})


@open_api_memories_bp.post('')
@api_key_required
@require_bound_key
def add_my_memory():
    owner = _current_key()['ownerUserId']
    body = request.get_json(silent=True) or {}
    text = (body.get('text') or '').strip()
    verbatim = bool(body.get('verbatim'))
    if not text:
        return jsonify({'error': '内容不能为空'}), 400
    if len(text) > 2000:
        return jsonify({'error': '内容过长（上限 2000 字符）'}), 400
    if get_memory() is None:
        return jsonify({'error': '记忆功能未配置（缺少 API Key 或未启用底层）',
                        'code': 'MEMORY_UNAVAILABLE'}), 409
    if not add_memory_text(owner, text, infer=not verbatim):
        return jsonify({'error': '写入失败'}), 500
    return jsonify({'ok': True, 'memories': list_memories(owner)})


@open_api_memories_bp.delete('/<memory_id>')
@api_key_required
@require_bound_key
def delete_my_memory(memory_id):
    owner = _current_key()['ownerUserId']
    # 安全关键：删除前必须先确认这条记忆确实属于这把密钥的所属用户——
    # delete_memory 本身不做这个校验，见文件头注释。
    owned = {m.get('id') for m in list_memories(owner)}
    if memory_id not in owned:
        return jsonify({'error': 'not found'}), 404
    delete_memory(memory_id)
    return jsonify({'ok': True})
