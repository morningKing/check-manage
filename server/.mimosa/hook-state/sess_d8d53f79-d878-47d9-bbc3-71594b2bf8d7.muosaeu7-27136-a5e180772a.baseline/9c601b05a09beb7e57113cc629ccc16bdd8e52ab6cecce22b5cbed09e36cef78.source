from unittest.mock import patch

INST = {'id': 'kf_1', 'slug': 'presale', 'enabled': True}


def test_public_faq_list(client):
    items = [{'id': 'faq_1', 'question': 'Q?', 'answer': 'A', 'category': 'billing'}]
    with patch('routes.kefu_public.kefu_repo.get_instance_by_slug', return_value=INST), \
         patch('routes.kefu_public.kefu_repo.list_faq_public', return_value=items):
        r = client.get('/kefu/i/presale/faq')
    assert r.status_code == 200 and r.get_json()['items'][0]['id'] == 'faq_1'


def test_public_faq_list_disabled_empty(client):
    with patch('routes.kefu_public.kefu_repo.get_instance_by_slug',
               return_value={**INST, 'enabled': False}):
        r = client.get('/kefu/i/presale/faq')
    assert r.status_code == 200 and r.get_json()['items'] == []


def test_public_faq_list_404(client):
    with patch('routes.kefu_public.kefu_repo.get_instance_by_slug', return_value=None):
        r = client.get('/kefu/i/none/faq')
    assert r.status_code == 404


def test_click_increments(client):
    with patch('routes.kefu_public.kefu_repo.get_instance_by_slug', return_value=INST), \
         patch('routes.kefu_public._faqclick_ok', return_value=True), \
         patch('routes.kefu_public.kefu_repo.increment_faq_click', return_value=True) as m:
        r = client.post('/kefu/i/presale/faq/faq_1/click')
    assert r.status_code == 204
    m.assert_called_once_with('kf_1', 'faq_1')


def test_click_unknown_silent_204(client):
    with patch('routes.kefu_public.kefu_repo.get_instance_by_slug', return_value=INST), \
         patch('routes.kefu_public.kefu_repo.increment_faq_click', return_value=False):
        r = client.post('/kefu/i/presale/faq/nope/click')
    assert r.status_code == 204


def test_click_rate_limited_silent_204(client):
    with patch('routes.kefu_public.kefu_repo.get_instance_by_slug', return_value=INST), \
         patch('routes.kefu_public._faqclick_ok', return_value=False), \
         patch('routes.kefu_public.kefu_repo.increment_faq_click') as m:
        r = client.post('/kefu/i/presale/faq/faq_1/click')
    assert r.status_code == 204
    m.assert_not_called()
