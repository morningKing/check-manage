"""Sessions with a NULL workspace_path (e.g. batch child sessions) must not 500
the background /files and /changes loaders — they should report empty."""
import sys, os
from unittest.mock import patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from auth import create_token


def _client():
    from app import app
    app.config['TESTING'] = True
    return app.test_client()


def _h(uid='u1', role='developer'):
    return {'Authorization': 'Bearer ' + create_token({'id': uid, 'username': uid, 'role': role})}


# _load_session_for_user returns (id, user_id, opencode_session_id, status, workspace_path)
_NULL_WS_SESSION = ('s1', 'u1', 'oc1', 'active', None)


def test_list_files_null_workspace_returns_empty():
    import routes.ai_chat as ac
    with patch.object(ac, '_load_session_for_user', return_value=_NULL_WS_SESSION):
        r = _client().get('/ai/chat/sessions/s1/files', headers=_h())
    assert r.status_code == 200
    assert r.get_json() == {'files': []}


def test_list_changes_null_workspace_returns_empty():
    import routes.ai_chat as ac
    with patch.object(ac, '_load_session_for_user', return_value=_NULL_WS_SESSION):
        r = _client().get('/ai/chat/sessions/s1/changes', headers=_h())
    assert r.status_code == 200
    j = r.get_json()
    assert j['changes'] == [] and j['truncated'] is False and j['ok'] is True
