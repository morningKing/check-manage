import sys, os, json
from unittest.mock import MagicMock, patch
from contextlib import contextmanager
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from auth import create_token

def test_put_settings_persists_mem0_fields():
    import routes.ai as ai
    captured = {}
    def fake_update(*args, **kwargs):
        captured['args'] = args
        captured['kwargs'] = kwargs
        return {'enabled': True, 'apiKey': 'sk', 'endpoint': 'https://x/v1/chat/completions',
                'model': 'qwen-plus', 'timeout': 30, 'maxTokens': 1024,
                'mem0Enabled': True, 'embeddingModel': 'text-embedding-v3'}
    with patch.object(ai, 'update_ai_settings', fake_update), \
         patch.object(ai, 'get_ai_settings', return_value={'apiKey': 'sk'}):
        from app import app
        app.config['TESTING'] = True
        tok = create_token({'id': 'u', 'username': 'admin', 'role': 'admin'})
        resp = app.test_client().put('/ai/settings',
            headers={'Authorization': f'Bearer {tok}'},
            json={'enabled': True, 'apiKey': 'sk', 'endpoint': 'https://x/v1/chat/completions',
                  'model': 'qwen-plus', 'timeout': 30, 'maxTokens': 1024,
                  'mem0Enabled': True, 'embeddingModel': 'text-embedding-v3'})
    assert resp.status_code == 200
    assert resp.get_json()['mem0Enabled'] is True
    assert resp.get_json()['embeddingModel'] == 'text-embedding-v3'
    # Verify the new kwargs were forwarded to update_ai_settings
    assert captured['kwargs'].get('mem0_enabled') is True
    assert captured['kwargs'].get('embedding_model') == 'text-embedding-v3'

def test_put_settings_resets_memory_singleton():
    import routes.ai as ai
    def fake_update(*a, **k):
        return {'enabled': True, 'apiKey': 'sk', 'endpoint': 'https://x/v1/chat/completions',
                'model': 'qwen-plus', 'timeout': 30, 'maxTokens': 1024,
                'mem0Enabled': False, 'embeddingModel': 'text-embedding-v3'}
    with patch.object(ai, 'update_ai_settings', fake_update), \
         patch.object(ai, 'get_ai_settings', return_value={'apiKey': 'sk'}), \
         patch.object(ai, 'reset_memory_singleton') as reset:
        from app import app
        app.config['TESTING'] = True
        tok = create_token({'id': 'u', 'username': 'admin', 'role': 'admin'})
        app.test_client().put('/ai/settings', headers={'Authorization': f'Bearer {tok}'},
            json={'enabled': True, 'apiKey': 'sk', 'endpoint': 'https://x/v1/chat/completions',
                  'model': 'qwen-plus', 'timeout': 30, 'maxTokens': 1024,
                  'mem0Enabled': False, 'embeddingModel': 'text-embedding-v3'})
    reset.assert_called_once()
