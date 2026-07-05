from unittest.mock import patch

_SESS = {'id': 'sess_1', 'kefu_instance_id': 'kf_1', 'visitor_id': 'v1',
         'needs_human': True, 'human_takeover': False, 'human_agent_id': None,
         'status': 'active', 'opencode_session_id': 'oc_1', 'workspace_path': '/ws'}


def test_queue_requires_admin_kefu(client, dev_headers):
    resp = client.get('/admin/kefu/sessions', headers=dev_headers)
    assert resp.status_code == 403


def test_queue_lists_sessions(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.list_kefu_sessions_admin',
               return_value=[{'id': 'sess_1', 'needs_human': True}]) as L:
        resp = client.get('/admin/kefu/sessions?needs_human=1&instance=kf_1',
                          headers=admin_headers)
    assert resp.status_code == 200
    assert resp.get_json()['sessions'][0]['id'] == 'sess_1'
    kwargs = L.call_args.kwargs
    assert kwargs['needs_human'] is True and kwargs['instance_id'] == 'kf_1'


def test_takeover_sets_and_logs(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.get_kefu_session_admin', return_value=dict(_SESS)), \
         patch('routes.kefu_admin.kefu_repo.takeover_session', return_value=True) as T, \
         patch('routes.kefu_admin.log_operation') as LG:
        resp = client.post('/admin/kefu/sessions/sess_1/takeover', headers=admin_headers)
    assert resp.status_code == 200
    assert resp.get_json()['humanTakeover'] is True
    T.assert_called_once_with('sess_1', 'user-admin')
    LG.assert_called_once()


def test_takeover_missing_session_404(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.get_kefu_session_admin', return_value=None):
        resp = client.post('/admin/kefu/sessions/sess_x/takeover', headers=admin_headers)
    assert resp.status_code == 404


def test_release_clears_and_logs(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.get_kefu_session_admin', return_value=dict(_SESS)), \
         patch('routes.kefu_admin.kefu_repo.release_session', return_value=True) as R, \
         patch('routes.kefu_admin.log_operation'):
        resp = client.post('/admin/kefu/sessions/sess_1/release', headers=admin_headers)
    assert resp.status_code == 200
    assert resp.get_json()['humanTakeover'] is False
    R.assert_called_once_with('sess_1')


def test_human_reply_when_takeover(client, admin_headers):
    s = dict(_SESS, human_takeover=True)
    with patch('routes.kefu_admin.kefu_repo.get_kefu_session_admin', return_value=s), \
         patch('routes.kefu_admin.kefu_repo.insert_human_message', return_value='msg_1') as I:
        resp = client.post('/admin/kefu/sessions/sess_1/messages',
                           json={'content': '您好'}, headers=admin_headers)
    assert resp.status_code == 201
    assert resp.get_json()['messageId'] == 'msg_1'
    I.assert_called_once_with('sess_1', '您好', 'user-admin')


def test_human_reply_not_in_takeover_409(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.get_kefu_session_admin', return_value=dict(_SESS)), \
         patch('routes.kefu_admin.kefu_repo.insert_human_message') as I:
        resp = client.post('/admin/kefu/sessions/sess_1/messages',
                           json={'content': '您好'}, headers=admin_headers)
    assert resp.status_code == 409
    I.assert_not_called()


def test_human_reply_empty_content_400(client, admin_headers):
    s = dict(_SESS, human_takeover=True)
    with patch('routes.kefu_admin.kefu_repo.get_kefu_session_admin', return_value=s):
        resp = client.post('/admin/kefu/sessions/sess_1/messages',
                           json={'content': '   '}, headers=admin_headers)
    assert resp.status_code == 400


def test_takeover_publishes_events(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.get_kefu_session_admin', return_value=dict(_SESS)), \
         patch('routes.kefu_admin.kefu_repo.takeover_session', return_value=True), \
         patch('routes.kefu_admin.log_operation'), \
         patch('routes.kefu_admin.kefu_event_bus.publish') as P:
        client.post('/admin/kefu/sessions/sess_1/takeover', headers=admin_headers)
    P.assert_any_call('sess_1', {'type': 'takeover'})
    P.assert_any_call('inst:kf_1', {'sid': 'sess_1', 'type': 'takeover'})
    assert P.call_count == 2


def test_release_publishes_events(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.get_kefu_session_admin', return_value=dict(_SESS)), \
         patch('routes.kefu_admin.kefu_repo.release_session', return_value=True), \
         patch('routes.kefu_admin.log_operation'), \
         patch('routes.kefu_admin.kefu_event_bus.publish') as P:
        client.post('/admin/kefu/sessions/sess_1/release', headers=admin_headers)
    P.assert_any_call('sess_1', {'type': 'release'})
    P.assert_any_call('inst:kf_1', {'sid': 'sess_1', 'type': 'release'})
    assert P.call_count == 2


def test_human_reply_publishes_events(client, admin_headers):
    s = dict(_SESS, human_takeover=True)
    with patch('routes.kefu_admin.kefu_repo.get_kefu_session_admin', return_value=s), \
         patch('routes.kefu_admin.kefu_repo.insert_human_message', return_value='msg_1'), \
         patch('routes.kefu_admin.kefu_event_bus.publish') as P:
        client.post('/admin/kefu/sessions/sess_1/messages', json={'content': '您好'}, headers=admin_headers)
    P.assert_any_call('sess_1', {'type': 'human_message'})
    P.assert_any_call('inst:kf_1', {'sid': 'sess_1', 'type': 'human_message'})
    assert P.call_count == 2


def test_events_ticket_requires_admin(client, dev_headers):
    assert client.post('/admin/kefu/events/ticket', headers=dev_headers).status_code == 403


def test_events_ticket_returns_token(client, admin_headers):
    resp = client.post('/admin/kefu/events/ticket', headers=admin_headers)
    assert resp.status_code == 200
    assert resp.get_json()['ticket']


def test_events_bad_ticket_401(client):
    with patch('routes.kefu_admin.kefu_sse_ticket.consume', return_value=None):
        resp = client.get('/admin/kefu/events?instance=kf_1&ticket=bad')
    assert resp.status_code == 401


def test_events_missing_instance_400(client):
    with patch('routes.kefu_admin.kefu_sse_ticket.consume', return_value='user-admin'):
        resp = client.get('/admin/kefu/events?ticket=good')
    assert resp.status_code == 400


def test_admin_sse_cap_allows_three_then_blocks():
    from routes import kefu_admin as ka
    ka._admin_sse_active.clear()
    assert ka._admin_sse_acquire('u') is True
    assert ka._admin_sse_acquire('u') is True
    assert ka._admin_sse_acquire('u') is True
    assert ka._admin_sse_acquire('u') is False   # cap = 3
    ka._admin_sse_release('u')
    assert ka._admin_sse_acquire('u') is True
    ka._admin_sse_active.clear()
