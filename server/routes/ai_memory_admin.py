"""记忆管理管理面（/ai/chat/admin/memory，AI 能力组「记忆管理」页数据源）。

mem0 按 user_id 隔离；管理员通过「用户名或 userId」定位目标用户后操作。条目
删除沿用 open_api_memories.py 的 owned-guard 防线：utils.memory.delete_memory
(memory_id) 本身不按 user_id 过滤，任何删除必须先 list_memories(owner) 确认
目标 id 在集合里——否则拿到/猜到任意 memoryId 即可删别人记忆（IDOR）。

配置更新刻意走 read-merge：先 get_ai_settings() 取当前值，仅覆盖请求里出现的
记忆字段再整体落库——模型页（AI 配置）与记忆页各自保存都不会误清对方的字段。
"""
import os

from flask import Blueprint, jsonify, request

from auth import require_permission
from db import get_db
from utils.ai_query import get_ai_settings, update_ai_settings
from utils.memory import (MEM0_STORE_ROOT, add_memory_text, delete_memory,
                          get_memory, list_memories, render_memory_block,
                          reset_memory_singleton, search_memory)
from utils.operation_log import log_operation

ai_memory_admin_bp = Blueprint('ai_memory_admin', __name__,
                               url_prefix='/ai/chat/admin/memory')


@ai_memory_admin_bp.after_request
def _no_store(resp):
    # 管理面数据必须即读即新：条目删除/配置保存后紧邻的列表查询绝不能吃到
    # 浏览器启发式缓存（Flask JSON 无缓存头时同 URL 会被复用，E2E 实测踩过）。
    # 同款先例：external_takeover.py::_no_store。
    resp.headers.setdefault('Cache-Control', 'no-store')
    return resp

DEFAULT_EMBEDDING_MODEL = 'text-embedding-v3'
DEFAULT_OLLAMA_URL = 'http://localhost:11434'


def _mask_key(key: str) -> str:
    return ('*' * (len(key) - 4) + key[-4:]) if len(key) > 4 else key


def _owner_row(owner: str):
    """用户名或 userId → (id, username)；不存在返回 None。"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username FROM users WHERE id = %s OR username = %s",
                (owner, owner))
            row = cur.fetchone()
            return row


def _resolve_owner_or_404(owner: str):
    if not owner:
        return None
    return _owner_row(owner)


@ai_memory_admin_bp.get('')
@require_permission('admin.ai_settings')
def get_config():
    cfg = get_ai_settings()
    mem = get_memory()   # 一次性懒初始化探测；未启用/失败为 None
    return jsonify({
        'mem0Enabled': bool(cfg.get('mem0Enabled')),
        'embeddingProvider': cfg.get('embeddingProvider') or 'api',
        'embeddingModel': cfg.get('embeddingModel') or DEFAULT_EMBEDDING_MODEL,
        'embeddingOllamaUrl': cfg.get('embeddingOllamaUrl') or DEFAULT_OLLAMA_URL,
        'embeddingDims': cfg.get('embeddingDims') or 1024,
        'endpoint': cfg.get('endpoint') or '',
        'model': cfg.get('model') or '',
        'apiKey': _mask_key(cfg.get('apiKey') or ''),
        'status': {
            'ready': mem is not None,
            'storePath': MEM0_STORE_ROOT,
            'storeExists': os.path.isdir(MEM0_STORE_ROOT),
        },
    })


@ai_memory_admin_bp.put('')
@require_permission('admin.ai_settings')
def put_config():
    """read-merge 更新记忆配置：模型页字段（enabled/apiKey/endpoint/model 等）
    一律回填当前值，只有请求里出现的记忆字段被覆盖；落库后重置 mem0 单例。"""
    body = request.get_json(silent=True) or {}
    current = get_ai_settings()

    provider = (body.get('embeddingProvider') or current.get('embeddingProvider')
                or 'api').strip()
    if provider not in ('api', 'ollama'):
        return jsonify({'error': "embeddingProvider 只能是 'api' 或 'ollama'"}), 400
    dims = body.get('embeddingDims', current.get('embeddingDims', 1024))
    if not isinstance(dims, int) or isinstance(dims, bool) \
            or not (64 <= dims <= 4096):
        return jsonify({'error': '向量维度必须是 64-4096 的整数'}), 400
    ollama_url = (body.get('embeddingOllamaUrl') or current.get('embeddingOllamaUrl')
                  or DEFAULT_OLLAMA_URL).strip()
    if provider == 'ollama' and not ollama_url:
        return jsonify({'error': 'Ollama 服务地址不能为空'}), 400
    model = (body.get('embeddingModel') or current.get('embeddingModel')
             or DEFAULT_EMBEDDING_MODEL).strip()
    mem0_enabled = bool(body.get('mem0Enabled', current.get('mem0Enabled', False)))

    settings = update_ai_settings(
        enabled=current.get('enabled', False),
        api_key=current.get('apiKey') or '',
        endpoint=current.get('endpoint') or '',
        model=current.get('model') or '',
        timeout=current.get('timeout', 30),
        max_tokens=current.get('maxTokens', 1024),
        mem0_enabled=mem0_enabled,
        embedding_model=model,
        default_chat_model=current.get('defaultChatModel') or '',
        max_batch_sessions=current.get('maxBatchSessions', 50),
        embedding_provider=provider,
        embedding_ollama_url=ollama_url,
        embedding_dims=dims)
    reset_memory_singleton()
    log_operation('update', 'ai_settings', 'ai-settings', 'AI 配置',
                  f'记忆管理页更新 mem0 配置(enabled={mem0_enabled}, '
                  f'provider={provider}, model={model}, dims={dims})')
    out = dict(settings)
    out['apiKey'] = _mask_key(out.get('apiKey') or '')
    return jsonify(out)


@ai_memory_admin_bp.get('/users')
@require_permission('admin.ai_settings')
def search_users():
    """用户选择器数据：按用户名模糊或精确 id。空 q 返回空列表（不全量吐）。"""
    q = (request.args.get('q') or '').strip()
    if not q:
        return jsonify({'users': []})
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username FROM users "
                "WHERE username ILIKE %s OR id = %s ORDER BY username LIMIT 20",
                (f'%{q}%', q))
            rows = cur.fetchall()
    return jsonify({'users': [{'id': r[0], 'username': r[1]} for r in rows]})


@ai_memory_admin_bp.get('/entries')
@require_permission('admin.ai_settings')
def list_entries():
    owner = (request.args.get('owner') or '').strip()
    row = _resolve_owner_or_404(owner)
    if row is None:
        return jsonify({'error': '用户不存在', 'code': 'OWNER_NOT_FOUND'}), 404
    user_id, username = row
    query = (request.args.get('query') or '').strip()
    try:
        limit = int(request.args.get('limit', 50))
    except (TypeError, ValueError):
        limit = 50
    limit = min(max(1, limit), 100)
    if query:
        mems = search_memory(user_id, query, limit)
    else:
        mems = list_memories(user_id)
    return jsonify({'ownerUserId': user_id, 'ownerUsername': username,
                    'memories': mems})


@ai_memory_admin_bp.delete('/entries/<memory_id>')
@require_permission('admin.ai_settings')
def delete_entry(memory_id):
    owner = (request.args.get('owner') or '').strip()
    row = _resolve_owner_or_404(owner)
    if row is None:
        return jsonify({'error': '用户不存在', 'code': 'OWNER_NOT_FOUND'}), 404
    user_id, username = row
    # owned-guard（安全关键）：delete_memory 不做归属校验，见文件头注释
    owned = {m.get('id') for m in list_memories(user_id)}
    if memory_id not in owned:
        return jsonify({'error': 'not found', 'code': 'NOT_FOUND'}), 404
    delete_memory(memory_id)
    log_operation('delete', 'ai_memory', memory_id, username,
                  f'管理员删除用户 {username} 的长期记忆 {memory_id}')
    return jsonify({'ok': True})


@ai_memory_admin_bp.post('/test/search')
@require_permission('admin.ai_settings')
def test_search():
    body = request.get_json(silent=True) or {}
    query = (body.get('query') or '').strip()
    if not query:
        return jsonify({'error': 'query 不能为空'}), 400
    row = _resolve_owner_or_404((body.get('owner') or '').strip())
    if row is None:
        return jsonify({'error': '用户不存在', 'code': 'OWNER_NOT_FOUND'}), 404
    user_id, username = row
    try:
        limit = int(body.get('limit', 5))
    except (TypeError, ValueError):
        limit = 5
    limit = min(max(1, limit), 20)
    results = search_memory(user_id, query, limit)
    return jsonify({'ownerUserId': user_id, 'ownerUsername': username,
                    'results': results,
                    # 注入提示词原文：页面所见 = 模型所见
                    'rendered': render_memory_block(results, limit)})


@ai_memory_admin_bp.post('/test/add')
@require_permission('admin.ai_settings')
def test_add():
    body = request.get_json(silent=True) or {}
    text = (body.get('text') or '').strip()
    if not text:
        return jsonify({'error': '内容不能为空'}), 400
    if len(text) > 2000:
        return jsonify({'error': '内容过长（上限 2000 字符）'}), 400
    row = _resolve_owner_or_404((body.get('owner') or '').strip())
    if row is None:
        return jsonify({'error': '用户不存在', 'code': 'OWNER_NOT_FOUND'}), 404
    user_id, username = row
    verbatim = bool(body.get('verbatim'))
    if get_memory() is None:
        return jsonify({'error': '记忆功能未配置（缺少 API Key 或未启用底层）',
                        'code': 'MEMORY_UNAVAILABLE'}), 409
    if not add_memory_text(user_id, text, infer=not verbatim, source='manual'):
        # add_memory_text False = mem0 不可用/降级（utils.memory docstring 口径），
        # 与上面 get_memory None 同一契约，不当作 500
        return jsonify({'error': '记忆功能未配置（缺少 API Key 或未启用底层）',
                        'code': 'MEMORY_UNAVAILABLE'}), 409
    log_operation('create', 'ai_memory', None, username,
                  f'管理员为用户 {username} 补写长期记忆'
                  + ('（原样保存）' if verbatim else ''))
    return jsonify({'ok': True, 'memories': list_memories(user_id)})
