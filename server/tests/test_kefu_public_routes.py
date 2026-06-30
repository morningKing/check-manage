from unittest.mock import patch

INST = {'id': 'kf_1', 'slug': 'presale', 'name': '售前', 'enabled': True,
        'welcome_message': '你好', 'guided_questions': ['价格?'], 'branding': {},
        'bot_user_id': 'kefu-bot', 'rate_limit': {'perMinute': 5}}


def test_public_config_ok(client):
    with patch('routes.kefu_public.kefu_repo.get_instance_by_slug', return_value=INST):
        resp = client.get('/kefu/i/presale')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['name'] == '售前' and body['enabled'] is True
    assert 'bot_user_id' not in body  # 不泄露内部字段


def test_public_config_404(client):
    with patch('routes.kefu_public.kefu_repo.get_instance_by_slug', return_value=None):
        resp = client.get('/kefu/i/none')
    assert resp.status_code == 404


def test_create_session_requires_visitor_header(client):
    with patch('routes.kefu_public.kefu_repo.get_instance_by_slug', return_value=INST):
        resp = client.post('/kefu/i/presale/sessions')
    assert resp.status_code == 400


def test_create_session_ok(client):
    with patch('routes.kefu_public.kefu_repo.get_instance_by_slug', return_value=INST), \
         patch('routes.kefu_public.kefu_repo.create_kefu_session',
               return_value={'id': 'sess_1', 'title': '客服会话'}) as m:
        resp = client.post('/kefu/i/presale/sessions',
                           headers={'X-Visitor-Id': 'visitor-abc'})
    assert resp.status_code == 201
    assert resp.get_json()['id'] == 'sess_1'
    m.assert_called_once()


def test_create_session_disabled_403(client):
    disabled = {**INST, 'enabled': False}
    with patch('routes.kefu_public.kefu_repo.get_instance_by_slug', return_value=disabled):
        resp = client.post('/kefu/i/presale/sessions',
                           headers={'X-Visitor-Id': 'v1'})
    assert resp.status_code == 403


def test_messages_visitor_ownership(client):
    with patch('routes.kefu_public.kefu_repo.load_kefu_session', return_value=None):
        resp = client.get('/kefu/sessions/sess_x/messages',
                          headers={'X-Visitor-Id': 'v1'})
    assert resp.status_code == 404
