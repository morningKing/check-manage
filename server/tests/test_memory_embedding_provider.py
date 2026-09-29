"""嵌入模型提供方（api / ollama）配置测试。

覆盖：_build_config 两种 embedder 形态（api 回归 + ollama 新形态 + 缺省
回退）、PUT /ai/settings 的 provider 校验与参数转发。
"""
import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.memory import _build_config
from auth import create_token


# ---------------------------------------------------------------- _build_config

def test_build_config_api_provider_regression():
    cfg = {'endpoint': 'https://dashscope/v1/chat/completions', 'apiKey': 'sk',
           'model': 'qwen-plus', 'embeddingModel': 'text-embedding-v3',
           'embeddingProvider': 'api'}
    c = _build_config(cfg)
    assert c['embedder']['provider'] == 'openai'
    assert c['embedder']['config']['model'] == 'text-embedding-v3'
    assert c['embedder']['config']['openai_base_url'] == 'https://dashscope/v1'
    assert c['embedder']['config']['embedding_dims'] == 1024
    # llm（摘要）不受 embedder 提供方影响，始终走原 API 端点
    assert c['llm']['config']['openai_base_url'] == 'https://dashscope/v1'
    assert c['llm']['config']['model'] == 'qwen-plus'


def test_build_config_ollama_provider():
    cfg = {'endpoint': 'https://dashscope/v1/chat/completions', 'apiKey': 'sk',
           'model': 'qwen-plus', 'embeddingModel': 'nomic-embed-text',
           'embeddingProvider': 'ollama',
           'embeddingOllamaUrl': 'http://localhost:11434', 'embeddingDims': 768}
    c = _build_config(cfg)
    assert c['embedder']['provider'] == 'ollama'
    ec = c['embedder']['config']
    assert ec['model'] == 'nomic-embed-text'
    assert ec['ollama_base_url'] == 'http://localhost:11434'
    assert ec['embedding_dims'] == 768
    # llm（摘要）仍是原 API 端点
    assert c['llm']['config']['openai_base_url'] == 'https://dashscope/v1'


def test_build_config_defaults_when_fields_missing():
    """缺省字段回退：provider 缺省=api；ollama 模型/dims 有内置默认。"""
    c = _build_config({'endpoint': 'https://x/v1/chat/completions', 'apiKey': 'k',
                       'model': 'm'})
    assert c['embedder']['provider'] == 'openai'
    cfg = {'endpoint': 'https://x/v1/chat/completions', 'apiKey': 'k', 'model': 'm',
           'embeddingProvider': 'ollama'}
    c2 = _build_config(cfg)
    assert c2['embedder']['config']['model'] == 'nomic-embed-text'
    assert c2['embedder']['config']['ollama_base_url'] == 'http://localhost:11434'
    assert c2['embedder']['config']['embedding_dims'] == 1024


# ---------------------------------------------------------------- PUT 校验

def _put_settings(payload):
    from app import app
    app.config['TESTING'] = True
    tok = create_token({'id': 'u', 'username': 'admin', 'role': 'admin'})
    return app.test_client().put('/ai/settings',
        headers={'Authorization': f'Bearer {tok}'}, json=payload)


def test_put_settings_forwards_embedding_provider_kwargs():
    import routes.ai as ai
    captured = {}
    def fake_update(*args, **kwargs):
        captured['kwargs'] = kwargs
        return {'enabled': True, 'apiKey': 'sk', 'endpoint': 'https://x/v1/chat/completions',
                'model': 'qwen-plus', 'timeout': 30, 'maxTokens': 1024,
                'mem0Enabled': True, 'embeddingModel': 'nomic-embed-text',
                'embeddingProvider': 'ollama'}
    with patch.object(ai, 'update_ai_settings', fake_update), \
         patch.object(ai, 'get_ai_settings', return_value={'apiKey': 'sk'}), \
         patch.object(ai, 'reset_memory_singleton'):
        resp = _put_settings({
            'enabled': True, 'apiKey': 'sk', 'endpoint': 'https://x/v1/chat/completions',
            'model': 'qwen-plus', 'timeout': 30, 'maxTokens': 1024,
            'mem0Enabled': True, 'embeddingModel': 'nomic-embed-text',
            'embeddingProvider': 'ollama',
            'embeddingOllamaUrl': 'http://192.168.1.10:11434', 'embeddingDims': 768})
    assert resp.status_code == 200
    assert captured['kwargs']['embedding_provider'] == 'ollama'
    assert captured['kwargs']['embedding_ollama_url'] == 'http://192.168.1.10:11434'
    assert captured['kwargs']['embedding_dims'] == 768


def test_put_settings_rejects_bad_provider_and_dims():
    base = {'enabled': True, 'apiKey': 'sk', 'endpoint': 'https://x/v1/chat/completions',
            'model': 'qwen-plus', 'timeout': 30, 'maxTokens': 1024}
    r1 = _put_settings({**base, 'embeddingProvider': 'bogus'})
    assert r1.status_code == 400 and 'embeddingProvider' in r1.get_json()['error']
    # 空 Ollama URL 回退默认（宽容语义，不 400）
    r2 = _put_settings({**base, 'embeddingProvider': 'ollama', 'embeddingOllamaUrl': ''})
    assert r2.status_code == 200
    r3 = _put_settings({**base, 'embeddingDims': 12})
    assert r3.status_code == 400 and '向量维度' in r3.get_json()['error']
