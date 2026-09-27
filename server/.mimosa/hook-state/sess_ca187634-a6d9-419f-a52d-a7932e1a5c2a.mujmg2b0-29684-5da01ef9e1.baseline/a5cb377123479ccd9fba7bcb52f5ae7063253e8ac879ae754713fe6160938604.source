from unittest.mock import patch


def test_patch_rejects_non_list_panel_blocks(client, admin_headers):
    r = client.patch('/admin/kefu/instances/kf_1', json={'panel_blocks': {'not': 'a list'}},
                     headers=admin_headers)
    assert r.status_code == 400


def test_patch_rejects_bad_block_type(client, admin_headers):
    r = client.patch('/admin/kefu/instances/kf_1',
                     json={'panel_blocks': [{'id': 'b1', 'type': 'evil'}]}, headers=admin_headers)
    assert r.status_code == 400


def test_patch_accepts_valid_panel_blocks(client, admin_headers):
    blocks = [{'id': 'b1', 'type': 'faq', 'title': '热点', 'enabled': True, 'config': {'limit': 5}}]
    with patch('routes.kefu_admin.kefu_repo.update_instance',
               return_value={'id': 'kf_1', 'name': 'X', 'panel_blocks': blocks}) as m:
        r = client.patch('/admin/kefu/instances/kf_1', json={'panel_blocks': blocks},
                         headers=admin_headers)
    assert r.status_code == 200
    m.assert_called_once()


def test_public_config_returns_panel_blocks(client):
    inst = {'id': 'kf_1', 'slug': 'presale', 'name': '售前', 'enabled': True,
            'welcome_message': 'hi', 'guided_questions': ['Q?'], 'branding': {},
            'panel_blocks': [{'id': 'b1', 'type': 'contact', 'config': {'phone': '123'}}]}
    with patch('routes.kefu_public.kefu_repo.get_instance_by_slug', return_value=inst):
        r = client.get('/kefu/i/presale')
    body = r.get_json()
    assert body['panel_blocks'] == inst['panel_blocks']
    assert body['guided_questions'] == ['Q?']
