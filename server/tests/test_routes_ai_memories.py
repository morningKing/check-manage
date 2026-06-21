import sys, os
from unittest.mock import patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from auth import create_token

def _h():
    return {'Authorization': 'Bearer ' + create_token({'id': 'u1', 'username': 'bob', 'role': 'developer'})}

def _client():
    from app import app
    app.config['TESTING'] = True
    return app.test_client()

def test_list_memories_returns_user_memories():
    with patch('routes.ai.list_memories', return_value=[{'id': '1', 'memory': '喜欢 Python'}]) as lm:
        r = _client().get('/ai/memories', headers=_h())
    assert r.status_code == 200
    assert r.get_json()['memories'][0]['memory'] == '喜欢 Python'
    lm.assert_called_once_with('u1')

def test_delete_memory_checks_ownership():
    with patch('routes.ai.list_memories', return_value=[{'id': 'mine', 'memory': 'x'}]), \
         patch('routes.ai.delete_memory') as dm:
        ok = _client().delete('/ai/memories/mine', headers=_h())
        nope = _client().delete('/ai/memories/someone-else', headers=_h())
    assert ok.status_code == 200
    dm.assert_called_once_with('mine')
    assert nope.status_code == 404
