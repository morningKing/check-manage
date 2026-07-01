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
    for field in ('bot_user_id', 'rate_limit', 'system_prompt', 'agent', 'model'):
        assert field not in body, f"internal field '{field}' must not be in public config"


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
               return_value={'id': 'sess_1', 'title': '客服会话'}) as m, \
         patch('routes.kefu_public._rate_ok', return_value=True):
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


def test_create_session_ratelimited(client):
    with patch('routes.kefu_public.kefu_repo.get_instance_by_slug', return_value=INST), \
         patch('routes.kefu_public._rate_ok', return_value=False):
        resp = client.post('/kefu/i/presale/sessions', headers={'X-Visitor-Id': 'v1'})
    assert resp.status_code == 429


def test_messages_visitor_ownership(client):
    with patch('routes.kefu_public.kefu_repo.load_kefu_session', return_value=None):
        resp = client.get('/kefu/sessions/sess_x/messages',
                          headers={'X-Visitor-Id': 'v1'})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Default rate-limit floor
# ---------------------------------------------------------------------------

def test_rate_default_floor_applies_when_unset(client):
    """When rate_limit dict is empty, _rate_ok applies DEFAULT_PER_MINUTE/DAY for the
    per-visitor bucket and DEFAULT_IP_PER_MINUTE/DAY for the IP-only bucket."""
    import routes.kefu_public as mod
    from unittest.mock import MagicMock, patch as _patch

    inst_no_limit = {**INST, 'rate_limit': {}}
    mock_limiter = MagicMock()
    mock_limiter.allow.return_value = True

    with _patch('routes.kefu_public.kefu_repo.get_instance_by_slug', return_value=inst_no_limit), \
         _patch('routes.kefu_public.kefu_repo.create_kefu_session', return_value={'id': 's1'}), \
         _patch.object(mod, '_limiter', mock_limiter):
        resp = client.post('/kefu/i/presale/sessions', headers={'X-Visitor-Id': 'v1'})

    assert resp.status_code == 201
    calls = mock_limiter.allow.call_args_list
    # First call: per-visitor bucket
    visitor_args = calls[0][0]  # positional args of first call
    assert visitor_args[1] == mod.DEFAULT_PER_MINUTE, f"expected {mod.DEFAULT_PER_MINUTE}, got {visitor_args[1]}"
    assert visitor_args[2] == mod.DEFAULT_PER_DAY, f"expected {mod.DEFAULT_PER_DAY}, got {visitor_args[2]}"
    # Second call: IP-only bucket (fixed floors, no opt-out)
    ip_args = calls[1][0]
    assert ip_args[1] == mod.DEFAULT_IP_PER_MINUTE, f"expected {mod.DEFAULT_IP_PER_MINUTE}, got {ip_args[1]}"
    assert ip_args[2] == mod.DEFAULT_IP_PER_DAY, f"expected {mod.DEFAULT_IP_PER_DAY}, got {ip_args[2]}"


def test_rate_explicit_zero_means_unlimited(client):
    """Explicit 0 in instance config is the admin opt-out for the per-visitor bucket.
    The IP-only bucket always uses the fixed floor regardless of the explicit-0."""
    import routes.kefu_public as mod
    from unittest.mock import MagicMock, patch as _patch

    inst_zero = {**INST, 'rate_limit': {'perMinute': 0, 'perDay': 0}}
    mock_limiter = MagicMock()
    mock_limiter.allow.return_value = True

    with _patch('routes.kefu_public.kefu_repo.get_instance_by_slug', return_value=inst_zero), \
         _patch('routes.kefu_public.kefu_repo.create_kefu_session', return_value={'id': 's2'}), \
         _patch.object(mod, '_limiter', mock_limiter):
        resp = client.post('/kefu/i/presale/sessions', headers={'X-Visitor-Id': 'v2'})

    assert resp.status_code == 201
    calls = mock_limiter.allow.call_args_list
    # First call: per-visitor bucket — explicit 0 must stay 0 (unlimited)
    visitor_args = calls[0][0]
    assert visitor_args[1] == 0, f"explicit 0 perMinute must stay 0 (unlimited), got {visitor_args[1]}"
    assert visitor_args[2] == 0, f"explicit 0 perDay must stay 0 (unlimited), got {visitor_args[2]}"
    # Second call: IP-only bucket — always uses fixed floors (no opt-out applies here)
    ip_args = calls[1][0]
    assert ip_args[1] == mod.DEFAULT_IP_PER_MINUTE
    assert ip_args[2] == mod.DEFAULT_IP_PER_DAY


def test_rate_ip_floor_catches_rotating_visitor():
    """Rotating visitor_id does not bypass the IP-only bucket floor."""
    import routes.kefu_public as mod
    from unittest.mock import patch as _patch
    from utils.rate_limit import RateLimiter

    inst = {'id': 'kf_ipfloor', 'rate_limit': {}}
    limiter = RateLimiter()

    with _patch.object(mod, '_limiter', limiter), \
         _patch('routes.kefu_public._client_ip', return_value='10.0.0.99'):
        # Exhaust the IP per-minute floor with different visitor IDs each time.
        results = [
            mod._rate_ok(inst, f'vid-{i}')
            for i in range(mod.DEFAULT_IP_PER_MINUTE)
        ]
        # One more call with a brand-new visitor ID — IP bucket must reject.
        final = mod._rate_ok(inst, 'vid-brand-new')

    assert all(results), "all requests within IP floor should pass"
    assert final is False, "IP bucket must block even with a fresh visitor_id"
