import sys, os
from unittest.mock import patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from app import app

def _client():
    app.config['TESTING'] = True
    return app.test_client()

def test_search_requires_internal_token():
    with patch('routes.ai_memory_internal.MCP_INTERNAL_TOKEN', 'secret'):
        r = _client().post('/ai/memory/internal/search', json={'userId': 'u', 'query': 'q'})
    assert r.status_code == 403

def test_search_forwards_to_memory():
    with patch('routes.ai_memory_internal.MCP_INTERNAL_TOKEN', 'secret'), \
         patch('routes.ai_memory_internal.search_memory', return_value=[{'id': '1', 'memory': '喜欢 Python'}]) as s:
        r = _client().post('/ai/memory/internal/search',
                           headers={'X-Internal-Token': 'secret'},
                           json={'userId': 'alice', 'query': '技术', 'limit': 3})
    assert r.status_code == 200
    assert r.get_json()['results'][0]['memory'] == '喜欢 Python'
    s.assert_called_once_with('alice', '技术', 3)

def test_add_forwards_messages():
    with patch('routes.ai_memory_internal.MCP_INTERNAL_TOKEN', 'secret'), \
         patch('routes.ai_memory_internal.add_memory') as a:
        r = _client().post('/ai/memory/internal/add',
                           headers={'X-Internal-Token': 'secret'},
                           json={'userId': 'alice', 'messages': [{'role': 'user', 'content': '记住X'}]})
    assert r.status_code == 200
    a.assert_called_once_with('alice', [{'role': 'user', 'content': '记住X'}])

def test_delete_forwards():
    with patch('routes.ai_memory_internal.MCP_INTERNAL_TOKEN', 'secret'), \
         patch('routes.ai_memory_internal.delete_memory') as d:
        r = _client().post('/ai/memory/internal/delete',
                           headers={'X-Internal-Token': 'secret'},
                           json={'memoryId': 'm1'})
    assert r.status_code == 200
    d.assert_called_once_with('m1')

def test_disabled_when_token_empty():
    with patch('routes.ai_memory_internal.MCP_INTERNAL_TOKEN', ''):
        r = _client().post('/ai/memory/internal/search',
                           headers={'X-Internal-Token': ''}, json={'userId': 'u', 'query': 'q'})
    assert r.status_code == 403
