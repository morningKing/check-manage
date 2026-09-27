import sys, os
from unittest.mock import patch, MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from auth import create_token


def _client():
    from app import app
    app.config['TESTING'] = True
    return app.test_client()


def _h(uid='u1', role='developer'):
    return {'Authorization': 'Bearer ' + create_token({'id': uid, 'username': uid, 'role': role})}


# ---- add_memory_text unit ----
def test_add_memory_text_default_infer_true():
    import utils.memory as mem
    m = MagicMock()
    with patch.object(mem, 'get_memory', return_value=m), \
         patch.object(mem, '_on_mem_thread', side_effect=lambda fn: fn()):
        ok = mem.add_memory_text('u1', '负责 PostgreSQL 运维')
    assert ok is True
    assert m.add.call_args.kwargs['infer'] is True
    assert m.add.call_args.kwargs['user_id'] == 'u1'


def test_add_memory_text_verbatim_infer_false():
    import utils.memory as mem
    m = MagicMock()
    with patch.object(mem, 'get_memory', return_value=m), \
         patch.object(mem, '_on_mem_thread', side_effect=lambda fn: fn()):
        ok = mem.add_memory_text('u1', '原样一句话', infer=False)
    assert ok is True
    assert m.add.call_args.kwargs['infer'] is False


def test_add_memory_text_no_memory_returns_false():
    import utils.memory as mem
    with patch.object(mem, 'get_memory', return_value=None):
        assert mem.add_memory_text('u1', 'x') is False


# ---- POST /ai/memories ----
def test_post_memory_empty_400():
    import routes.ai as ai
    with patch.object(ai, 'get_memory', return_value=MagicMock()):
        r = _client().post('/ai/memories', json={'text': '   '}, headers=_h())
    assert r.status_code == 400


def test_post_memory_too_long_400():
    import routes.ai as ai
    with patch.object(ai, 'get_memory', return_value=MagicMock()):
        r = _client().post('/ai/memories', json={'text': 'a' * 2001}, headers=_h())
    assert r.status_code == 400


def test_post_memory_unavailable_409():
    import routes.ai as ai
    with patch.object(ai, 'get_memory', return_value=None):
        r = _client().post('/ai/memories', json={'text': 'hi'}, headers=_h())
    assert r.status_code == 409


def test_post_memory_ok_writes_current_user_infer_true():
    import routes.ai as ai
    with patch.object(ai, 'get_memory', return_value=MagicMock()), \
         patch.object(ai, 'add_memory_text', return_value=True) as add, \
         patch.object(ai, 'list_memories', return_value=[{'id': '1', 'memory': 'hi'}]):
        r = _client().post('/ai/memories', json={'text': 'hi'}, headers=_h(uid='u9'))
    assert r.status_code == 200
    assert r.get_json()['memories'] == [{'id': '1', 'memory': 'hi'}]
    assert add.call_args.args[0] == 'u9'           # current user id
    assert add.call_args.kwargs['infer'] is True


def test_post_memory_verbatim_infer_false():
    import routes.ai as ai
    with patch.object(ai, 'get_memory', return_value=MagicMock()), \
         patch.object(ai, 'add_memory_text', return_value=True) as add, \
         patch.object(ai, 'list_memories', return_value=[]):
        r = _client().post('/ai/memories', json={'text': 'hi', 'verbatim': True}, headers=_h())
    assert r.status_code == 200
    assert add.call_args.kwargs['infer'] is False
