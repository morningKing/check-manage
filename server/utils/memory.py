"""User-scoped long-term memory via mem0 (Chroma + DashScope-compatible LLM/embedder).

Flask is the SOLE owner of the mem0 instance (Chroma is a single-writer store). Every
operation degrades to no-op/empty when disabled or on error, so chat never breaks
because of the memory layer.
"""
import os
import threading
import logging

from db import get_db
from utils.ai_query import get_ai_settings

logger = logging.getLogger(__name__)

MEM0_STORE_ROOT = os.environ.get(
    'MEM0_STORE_ROOT', os.path.join(os.path.expanduser('~'), '.check-manage', 'mem0'))

_memory = None
_init_attempted = False
_lock = threading.Lock()

# ── Why every mem0 call is pinned to ONE dedicated thread ─────────────────────
# mem0's vector store (chromadb) computes embeddings via onnxruntime — a native
# C++ runtime. Such native libs hold THREAD-AFFINE state: handles, the onnxruntime
# session/memory arena, and BLAS thread context all bind to the thread that
# created them. Touching that state from a DIFFERENT thread is an illegal native
# memory access → SIGSEGV.
#
# This app is multi-threaded: waitress/werkzeug request workers, APScheduler
# threads (backup / dependency / AI-scan), plus mem0's own background extract
# thread. If the singleton is lazily created on request thread A and then used
# from scheduler thread B (or another worker), it crashes. Symptom we actually
# hit: the 1st call (same thread) succeeds, the 2nd (different thread) segfaults.
#
# A segfault is PROCESS-level — it kills the interpreter outright, so it can NOT
# be caught by the try/except no-op fallbacks below; the crash happens before any
# Python handler can run. The only fix is to never cross threads.
#
# So: a single-worker pool acts as the "native thread". Because max_workers=1,
# init AND every later add/search/get_all/delete run on that SAME long-lived
# thread, satisfying chromadb/onnxruntime's affinity. _on_mem_thread() submits
# the work there and blocks on .result(), so callers keep normal sync semantics.
# (Tests don't init the native store — they mock/short-circuit — so this path is
# only exercised by the running server with mem0_enabled=True.)
import concurrent.futures as _futures
_executor = _futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='mem0')


def _on_mem_thread(fn):
    # Run `fn` on the single mem0 thread and wait for it (keeps the chromadb/
    # onnxruntime native state thread-affine; see the block above for why).
    return _executor.submit(fn).result()


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
            _memory = _on_mem_thread(lambda: Memory.from_config(_build_config(cfg)))
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
        _on_mem_thread(lambda: m.add(messages, user_id=user_id))
    except Exception as e:
        logger.warning('mem0 add failed: %s', e)


def add_memory_text(user_id, text, infer=True):
    """手动补写一条记忆。infer=False 为 verbatim（原样、不提炼，仍嵌入）。
    返回是否写入（mem0 不可用/降级时 False）。"""
    m = get_memory()
    if m is None or not user_id or not text:
        return False
    try:
        _on_mem_thread(lambda: m.add([{'role': 'user', 'content': text}],
                                     user_id=user_id, infer=infer))
        return True
    except Exception as e:
        logger.warning('mem0 manual add failed: %s', e)
        return False


def search_memory(user_id, query, limit=5):
    m = get_memory()
    if m is None or not user_id or not query:
        return []
    try:
        return _unwrap(_on_mem_thread(lambda: m.search(query=query, filters={'user_id': user_id}, limit=limit)))
    except Exception as e:
        logger.warning('mem0 search failed: %s', e)
        return []


def list_memories(user_id):
    m = get_memory()
    if m is None or not user_id:
        return []
    try:
        return _unwrap(_on_mem_thread(lambda: m.get_all(filters={'user_id': user_id})))
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
        _on_mem_thread(lambda: m.delete(memory_id=memory_id))
    except Exception as e:
        logger.warning('mem0 delete failed: %s', e)


def _turn_text(state):
    """从 listener 的累积 state 取助手纯文本。复用 chat_persist.build_content。"""
    try:
        from utils.chat_persist import build_content
        parts = build_content(state) or []
        return '\n'.join(p.get('text', '') for p in parts if p.get('type') == 'text').strip()
    except Exception:
        return ''


def _extract_user_text(content):
    """ai_chat_messages.content 是 [{'type':'text','text':...}, ...] 的 JSONB。"""
    if isinstance(content, list):
        return '\n'.join(p.get('text', '') for p in content if isinstance(p, dict) and p.get('type') == 'text').strip()
    return ''


def extract_from_turn(session_id, state, _sync=False):
    """每轮 idle、persist_turn 之后调用：抽取这一轮 [user, assistant] 进长期记忆。
    仅限真人交互会话（batch_id/scan_task_id 为空）。后台线程执行，绝不抛出。"""
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT user_id, batch_id, scan_task_id FROM ai_chat_sessions WHERE id=%s",
                        (session_id,))
            row = cur.fetchone()
        if not row:
            return
        user_id, batch_id, scan_task_id = row[0], row[1], row[2]
        if not user_id or batch_id or scan_task_id:
            return
        assistant_text = _turn_text(state)
        if not assistant_text:
            return
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT content FROM ai_chat_messages WHERE session_id=%s AND role='user' "
                        "ORDER BY created_at DESC, id DESC LIMIT 1", (session_id,))
            r = cur.fetchone()
        user_text = _extract_user_text(r[0]) if r else ''
        messages = []
        if user_text:
            messages.append({'role': 'user', 'content': user_text})
        messages.append({'role': 'assistant', 'content': assistant_text})
        if _sync:
            add_memory(user_id, messages)
        else:
            threading.Thread(target=add_memory, args=(user_id, messages), daemon=True).start()
    except Exception as e:
        logger.warning('extract_from_turn failed: %s', e)
