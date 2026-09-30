"""ai_memory_internal /search limit 参数健壮性（修复回归：非数字此前 500）。"""
import sys, os
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from app import app

TOKEN = 'secret'
HDR = {'X-Internal-Token': TOKEN}


def _client():
    app.config['TESTING'] = True
    return app.test_client()


def test_non_numeric_limit_returns_400():
    with patch('routes.ai_memory_internal.MCP_INTERNAL_TOKEN', TOKEN), \
         patch('routes.ai_memory_internal.search_memory', return_value=[]) as s:
        r = _client().post('/ai/memory/internal/search', headers=HDR,
                           json={'userId': 'u', 'query': 'q', 'limit': 'abc'})
    assert r.status_code == 400
    assert '整数' in r.get_json()['error']
    s.assert_not_called()


def test_null_limit_falls_back_default():
    with patch('routes.ai_memory_internal.MCP_INTERNAL_TOKEN', TOKEN), \
         patch('routes.ai_memory_internal.search_memory',
               return_value=[{'id': '1'}]) as s:
        r = _client().post('/ai/memory/internal/search', headers=HDR,
                           json={'userId': 'u', 'query': 'q', 'limit': None})
    assert r.status_code == 200
    s.assert_called_once_with('u', 'q', 5)


def test_non_positive_limit_clamped_to_default():
    with patch('routes.ai_memory_internal.MCP_INTERNAL_TOKEN', TOKEN), \
         patch('routes.ai_memory_internal.search_memory', return_value=[]) as s:
        r = _client().post('/ai/memory/internal/search', headers=HDR,
                           json={'userId': 'u', 'query': 'q', 'limit': 0})
    assert r.status_code == 200
    s.assert_called_once_with('u', 'q', 5)


def test_valid_limit_passthrough():
    with patch('routes.ai_memory_internal.MCP_INTERNAL_TOKEN', TOKEN), \
         patch('routes.ai_memory_internal.search_memory', return_value=[]) as s:
        r = _client().post('/ai/memory/internal/search', headers=HDR,
                           json={'userId': 'u', 'query': 'q', 'limit': 20})
    assert r.status_code == 200
    s.assert_called_once_with('u', 'q', 20)
