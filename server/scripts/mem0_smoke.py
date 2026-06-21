"""M1 连通性 spike：证明 mem0 + Chroma + DashScope 能 add/search 一条记忆。
Run: cd server && python scripts/mem0_smoke.py
依赖 ai_settings 已配 api_key/endpoint/model；用 --embed 覆盖 embedding 模型。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.ai_query import get_ai_settings

CHROMA_PATH = os.path.join(os.path.expanduser('~'), '.check-manage', 'mem0_smoke')
EMBED = sys.argv[sys.argv.index('--embed') + 1] if '--embed' in sys.argv else 'text-embedding-v3'


def base_url(endpoint):
    return endpoint.rsplit('/chat/completions', 1)[0] if endpoint else ''


def main():
    cfg = get_ai_settings()
    if not cfg.get('apiKey'):
        print('FAIL: ai_settings 未配 api_key'); return 1
    os.makedirs(CHROMA_PATH, exist_ok=True)
    from mem0 import Memory
    m = Memory.from_config({
        'vector_store': {'provider': 'chroma',
                         'config': {'collection_name': 'smoke', 'path': CHROMA_PATH}},
        'llm': {'provider': 'openai',
                'config': {'model': cfg['model'], 'openai_base_url': base_url(cfg['endpoint']),
                           'api_key': cfg['apiKey'], 'temperature': 0.1}},
        'embedder': {'provider': 'openai',
                     'config': {'model': EMBED, 'openai_base_url': base_url(cfg['endpoint']),
                                'api_key': cfg['apiKey'], 'embedding_dims': 1024}},
    })
    add_res = m.add([{'role': 'user', 'content': '我喜欢用 Python 和 PostgreSQL'},
                     {'role': 'assistant', 'content': '好的，记住你的技术偏好。'}],
                    user_id='smoke-user')
    print('ADD result:', add_res)
    res = m.search(query='我熟悉哪些技术', filters={'user_id': 'smoke-user'}, limit=5)
    print('SEARCH result:', res)
    ok = bool((res or {}).get('results') if isinstance(res, dict) else res)
    print('PASS' if ok else 'WARN: 无检索结果（抽取/维度可能需调整）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
