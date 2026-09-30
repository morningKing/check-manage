from unittest.mock import patch, MagicMock

_H = {'X-Visitor-Id': 'v1'}
# load_kefu_session 7-tuple: (id,user,oc,status,ws,kefu_instance_id,human_takeover)
def _sess(takeover):
    return ('sess_1', 'user-bot', 'oc_1', 'active', '/ws', 'kf_1', takeover)


def test_send_message_takeover_skips_opencode(client):
    with patch('routes.kefu_public.kefu_repo.load_kefu_session', return_value=_sess(True)), \
         patch('routes.kefu_public.kefu_repo.get_instance',
               return_value={'id': 'kf_1', 'model': None, 'agent': None, 'rate_limit': {}}), \
         patch('routes.kefu_public.OpenCodeClient') as OC, \
         patch('routes.kefu_public.ensure_listener') as EL:
        resp = client.post('/kefu/sessions/sess_1/messages',
                           json={'content': '你好'}, headers=_H)
    assert resp.status_code == 202
    OC.return_value.send_prompt_async.assert_not_called()
    EL.assert_not_called()


def test_send_message_normal_calls_opencode(client):
    with patch('routes.kefu_public.kefu_repo.load_kefu_session', return_value=_sess(False)), \
         patch('routes.kefu_public.kefu_repo.get_instance',
               return_value={'id': 'kf_1', 'model': None, 'agent': None, 'rate_limit': {}}), \
         patch('routes.kefu_public.OpenCodeClient') as OC, \
         patch('routes.kefu_public.ensure_listener'):
        resp = client.post('/kefu/sessions/sess_1/messages',
                           json={'content': '你好'}, headers=_H)
    assert resp.status_code == 202
    OC.return_value.send_prompt_async.assert_called_once()


def test_request_human_sets_flag(client):
    with patch('routes.kefu_public.kefu_repo.load_kefu_session', return_value=_sess(False)), \
         patch('routes.kefu_public.kefu_repo.set_needs_human', return_value=True) as SN:
        resp = client.post('/kefu/sessions/sess_1/request-human', headers=_H)
    assert resp.status_code == 200
    assert resp.get_json()['needsHuman'] is True
    SN.assert_called_once_with('sess_1', True)


def test_request_human_wrong_visitor_404(client):
    with patch('routes.kefu_public.kefu_repo.load_kefu_session', return_value=None):
        resp = client.post('/kefu/sessions/sess_1/request-human', headers=_H)
    assert resp.status_code == 404
