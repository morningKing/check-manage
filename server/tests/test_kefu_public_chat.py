from unittest.mock import patch, MagicMock

SESS = ('sess_1', 'kefu-bot', 'oc_1', 'active', '/ws/kf', 'kf_1', False)
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


def test_events_wrong_visitor_404(client):
    with patch('routes.kefu_public.kefu_repo.load_kefu_session', return_value=None):
        resp = client.get('/kefu/sessions/sess_1/events?visitor_id=wrong')
    assert resp.status_code == 404


def test_upload_rejects_bad_type(client):
    import io
    with patch('routes.kefu_public.kefu_repo.load_kefu_session', return_value=SESS):
        resp = client.post('/kefu/sessions/sess_1/files',
                           data={'file': (io.BytesIO(b'x'), 'evil.exe')},
                           headers={'X-Visitor-Id': 'v1'},
                           content_type='multipart/form-data')
    assert resp.status_code == 415


def test_send_message_publishes_instance_event(client, mock_cursor):
    with patch('routes.kefu_public.kefu_repo.load_kefu_session', return_value=SESS), \
         patch('routes.kefu_public.kefu_repo.get_instance', return_value=INST), \
         patch('routes.kefu_public.OpenCodeClient'), \
         patch('routes.kefu_public.ensure_listener'), \
         patch('routes.kefu_public.kefu_event_bus.publish') as P:
        client.post('/kefu/sessions/sess_1/messages', json={'content': '价格'},
                    headers={'X-Visitor-Id': 'v1'})
    P.assert_any_call('inst:kf_1', {'sid': 'sess_1', 'type': 'visitor_message'})


def test_request_human_publishes_instance_event(client):
    with patch('routes.kefu_public.kefu_repo.load_kefu_session', return_value=SESS), \
         patch('routes.kefu_public.kefu_repo.set_needs_human'), \
         patch('routes.kefu_public.kefu_event_bus.publish') as P:
        resp = client.post('/kefu/sessions/sess_1/request-human', headers={'X-Visitor-Id': 'v1'})
    assert resp.status_code == 200
    P.assert_any_call('inst:kf_1', {'sid': 'sess_1', 'type': 'needs_human'})


def test_send_message_ratelimited(client):
    with patch('routes.kefu_public.kefu_repo.load_kefu_session', return_value=SESS), \
         patch('routes.kefu_public.kefu_repo.get_instance', return_value=INST), \
         patch('routes.kefu_public._rate_ok', return_value=False):
        resp = client.post('/kefu/sessions/sess_1/messages', json={'content': 'hi'},
                           headers={'X-Visitor-Id': 'v1'})
    assert resp.status_code == 429


# ---------------------------------------------------------------------------
# SSE concurrency cap
# ---------------------------------------------------------------------------

def test_events_sse_acquire_release_helper():
    """Unit-test _sse_acquire/_sse_release without Flask (deterministic)."""
    import routes.kefu_public as mod
    key = '__test_sse_cap_unique_key__'
    # Ensure clean state
    mod._sse_active.pop(key, None)

    assert mod._sse_acquire(key) is True   # 1st
    assert mod._sse_acquire(key) is True   # 2nd
    assert mod._sse_acquire(key) is True   # 3rd  (== MAX_SSE_PER_VISITOR)
    assert mod._sse_acquire(key) is False  # 4th  -> rejected

    mod._sse_release(key)                  # free one slot
    assert mod._sse_acquire(key) is True   # now 3 again -> allowed

    # Cleanup
    mod._sse_release(key)
    mod._sse_release(key)
    mod._sse_release(key)
    assert key not in mod._sse_active


def test_events_concurrency_cap(client):
    """4th concurrent SSE stream for same visitor+instance returns 429."""
    with patch('routes.kefu_public.kefu_repo.load_kefu_session', return_value=SESS), \
         patch('routes.kefu_public._sse_acquire', return_value=False):
        resp = client.get('/kefu/sessions/sess_1/events?visitor_id=v1')
    assert resp.status_code == 429


def test_events_sse_release_called_on_stream_end(client):
    """_sse_release is called exactly once per acquired stream when the generator completes."""
    release_mock = MagicMock()
    sse_key = f"{SESS[5]}:v1"   # "kf_1:v1"

    with patch('routes.kefu_public.kefu_repo.load_kefu_session', return_value=SESS), \
         patch('routes.kefu_public._sse_acquire', return_value=True), \
         patch('routes.kefu_public._sse_acquire_ip', return_value=True), \
         patch('routes.kefu_public._sse_release', release_mock), \
         patch('routes.kefu_public._sse_release_ip'), \
         patch('routes.kefu_public.OpenCodeClient') as MockOC:
        MockOC.return_value.subscribe_events.return_value = iter([])
        resp = client.get('/kefu/sessions/sess_1/events?visitor_id=v1')
        _ = resp.data   # consume the streaming response so the generator runs to completion

    release_mock.assert_called_once_with(sse_key)


# ---------------------------------------------------------------------------
# SSE per-IP cap
# ---------------------------------------------------------------------------

def test_events_sse_ip_cap_helper():
    """Unit-test _sse_acquire_ip/_sse_release_ip without Flask (deterministic)."""
    import routes.kefu_public as mod
    key = '__test_ip_cap_unique__'
    mod._sse_active.pop(key, None)

    for _ in range(mod.MAX_SSE_PER_IP):
        assert mod._sse_acquire_ip(key) is True
    assert mod._sse_acquire_ip(key) is False  # over cap

    mod._sse_release_ip(key)
    assert mod._sse_acquire_ip(key) is True   # one slot freed

    # Cleanup
    for _ in range(mod.MAX_SSE_PER_IP):
        mod._sse_release_ip(key)
    assert key not in mod._sse_active


def test_events_ip_sse_cap_returns_429(client):
    """When IP SSE cap is hit, visitor slot is released (all-or-nothing) and 429 is returned."""
    release_mock = MagicMock()
    with patch('routes.kefu_public.kefu_repo.load_kefu_session', return_value=SESS), \
         patch('routes.kefu_public._sse_acquire', return_value=True), \
         patch('routes.kefu_public._sse_acquire_ip', return_value=False), \
         patch('routes.kefu_public._sse_release', release_mock):
        resp = client.get('/kefu/sessions/sess_1/events?visitor_id=v1')
    assert resp.status_code == 429
    # Visitor slot must have been released since IP cap was full (all-or-nothing).
    release_mock.assert_called_once()


# ---------------------------------------------------------------------------
# Human-events SSE endpoint (independent concurrency counter)
# ---------------------------------------------------------------------------

def test_human_events_404_when_not_owner(client):
    with patch('routes.kefu_public.kefu_repo.load_kefu_session', return_value=None):
        resp = client.get('/kefu/sessions/sess_x/human-events?visitor_id=v1')
    assert resp.status_code == 404


def test_human_sse_cap_allows_two_then_blocks():
    from routes import kefu_public as kp
    kp._human_sse_active.clear()
    assert kp._human_sse_acquire('k') is True
    assert kp._human_sse_acquire('k') is True
    assert kp._human_sse_acquire('k') is False   # cap = 2
    kp._human_sse_release('k')
    assert kp._human_sse_acquire('k') is True
    kp._human_sse_active.clear()
