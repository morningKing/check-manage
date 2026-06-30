from unittest.mock import patch, MagicMock

SESS = ('sess_1', 'kefu-bot', 'oc_1', 'active', '/ws/kf', 'kf_1')
INST = {'id': 'kf_1', 'slug': 'presale', 'rate_limit': {}}


def test_send_message_requires_content(client):
    with patch('routes.kefu_public.kefu_repo.load_kefu_session', return_value=SESS), \
         patch('routes.kefu_public.kefu_repo.get_instance', return_value=INST):
        resp = client.post('/kefu/sessions/sess_1/messages', json={'content': ''},
                           headers={'X-Visitor-Id': 'v1'})
    assert resp.status_code == 400


def test_send_message_dispatches(client, mock_cursor):
    with patch('routes.kefu_public.kefu_repo.load_kefu_session', return_value=SESS), \
         patch('routes.kefu_public.kefu_repo.get_instance', return_value=INST), \
         patch('routes.kefu_public.OpenCodeClient') as OC, \
         patch('routes.kefu_public.ensure_listener') as el:
        resp = client.post('/kefu/sessions/sess_1/messages',
                           json={'content': '价格多少'},
                           headers={'X-Visitor-Id': 'v1'})
    assert resp.status_code == 202
    OC.return_value.send_prompt_async.assert_called_once()
    el.assert_called_once()


def test_upload_rejects_oversize(client):
    import io
    big = io.BytesIO(b'x' * (21 * 1024 * 1024))  # 21MB > 20MB 上限
    with patch('routes.kefu_public.kefu_repo.load_kefu_session', return_value=SESS):
        resp = client.post('/kefu/sessions/sess_1/files',
                           data={'file': (big, 'big.bin')},
                           headers={'X-Visitor-Id': 'v1'},
                           content_type='multipart/form-data')
    assert resp.status_code == 413
