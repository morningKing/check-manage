"""User-scoped long-term memory via mem0 (Chroma + DashScope-compatible LLM/embedder).

Flask is the SOLE owner of the mem0 instance (Chroma is a single-writer store). Every
operation degrades to no-op/empty when disabled or on error, so chat never breaks
because of the memory layer.
"""
import os
import threading
import logging

from utils.ai_query import get_ai_settings

logger = logging.getLogger(__name__)

MEM0_STORE_ROOT = os.environ.get(
    'MEM0_STORE_ROOT', os.path.join(os.path.expanduser('~'), '.check-manage', 'mem0'))

_memory = None
_init_attempted = False
_lock = threading.Lock()


def _base_url(endpoint):
    return endpoint.rsplit('/chat/completions', 1)[0] if endpoint else ''


def _build_config(cfg):
    base = _base_url(cfg['endpoint'])
    key = cfg['apiKey']
    return {
        'vector_store': {'provider': 'chroma',
                         'config': {'collection_name': 'memories', 'path': MEM0_STORE_ROOT}},
        'llm': {'provider': 'openai',
                'config': {'model': cfg['model'], 'openai_base_url': base,
                           'api_key': key, 'temperature': 0.1}},
        'embedder': {'provider': 'openai',
                     'config': {'model': cfg.get('embeddingModel') or 'text-embedding-v3',
                                'openai_base_url': base, 'api_key': key, 'embedding_dims': 1024}},
    }


def get_memory():
    """mem0 Memory 单例；未启用/初始化失败返回 None（只尝试一次，不每次重试）。"""
    global _memory, _init_attempted
    if _memory is not None:
        return _memory
    with _lock:
        if _memory is not None:
            return _memory
        if _init_attempted:
            return None
        _init_attempted = True
        cfg = get_ai_settings()
        if not cfg.get('mem0Enabled') or not cfg.get('apiKey'):
            return None
        try:
            os.makedirs(MEM0_STORE_ROOT, exist_ok=True)
            from mem0 import Memory
            _memory = Memory.from_config(_build_config(cfg))
            return _memory
        except Exception as e:
            logger.warning('mem0 init failed, memory disabled: %s', e)
            return None


def reset_memory_singleton():
    """配置变更后丢弃缓存实例，使下次调用按新配置重建。"""
    global _memory, _init_attempted
    with _lock:
        _memory = None
        _init_attempted = False


def _unwrap(res):
    if isinstance(res, dict):
        return res.get('results', [])
    return res or []


def add_memory(user_id, messages):
    m = get_memory()
    if m is None or not user_id or not messages:
        return
    try:
        m.add(messages, user_id=user_id)
    except Exception as e:
        logger.warning('mem0 add failed: %s', e)


def search_memory(user_id, query, limit=5):
    m = get_memory()
    if m is None or not user_id or not query:
        return []
    try:
        return _unwrap(m.search(query=query, filters={'user_id': user_id}, limit=limit))
    except Exception as e:
        logger.warning('mem0 search failed: %s', e)
        return []


def list_memories(user_id):
    m = get_memory()
    if m is None or not user_id:
        return []
    try:
        return _unwrap(m.get_all(filters={'user_id': user_id}))
    except Exception as e:
        logger.warning('mem0 get_all failed: %s', e)
        return []


def render_memory_block(mems, limit=5):
    lines = [str(x.get('memory', '')).strip() for x in (mems or [])]
    lines = [l for l in lines if l][:limit]
    if not lines:
        return ''
    body = '\n'.join(f'- {l}' for l in lines)
    return f'[关于当前用户的长期记忆（供参考，不必逐条复述）]\n{body}\n\n'


def delete_memory(memory_id):
    m = get_memory()
    if m is None or not memory_id:
        return
    try:
        m.delete(memory_id=memory_id)
    except Exception as e:
        logger.warning('mem0 delete failed: %s', e)
