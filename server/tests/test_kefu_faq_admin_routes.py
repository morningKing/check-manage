"""测试管理 FAQ 端点"""
from unittest.mock import patch

FAQ = {'id': 'faq_1', 'instance_id': 'kf_1', 'question': 'Q?', 'answer': 'A',
       'category': None, 'sort_order': 0, 'click_count': 0, 'enabled': True}


def test_list_faq_requires_permission(client, dev_headers):
    r = client.get('/admin/kefu/instances/kf_1/faq', headers=dev_headers)
    assert r.status_code == 403


def test_create_faq_ok(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.get_instance', return_value={'id': 'kf_1'}), \
         patch('routes.kefu_admin.kefu_repo.create_faq', return_value=FAQ) as m:
        r = client.post('/admin/kefu/instances/kf_1/faq',
                        json={'question': 'Q?', 'answer': 'A'}, headers=admin_headers)
    assert r.status_code == 201 and r.get_json()['id'] == 'faq_1'
    m.assert_called_once()


def test_create_faq_validates(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.get_instance', return_value={'id': 'kf_1'}):
        r = client.post('/admin/kefu/instances/kf_1/faq',
                        json={'question': 'Q?'}, headers=admin_headers)  # missing answer
    assert r.status_code == 400


def test_create_faq_instance_404(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.get_instance', return_value=None):
        r = client.post('/admin/kefu/instances/nope/faq',
                        json={'question': 'Q?', 'answer': 'A'}, headers=admin_headers)
    assert r.status_code == 404


def test_patch_faq_ownership_404(client, admin_headers):
    other = {**FAQ, 'instance_id': 'other'}
    with patch('routes.kefu_admin.kefu_repo.get_faq', return_value=other):
        r = client.patch('/admin/kefu/instances/kf_1/faq/faq_1',
                         json={'question': 'X'}, headers=admin_headers)
    assert r.status_code == 404


def test_reorder_ok(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.reorder_faq') as m:
        r = client.patch('/admin/kefu/instances/kf_1/faq/reorder',
                         json={'order': ['faq_b', 'faq_a']}, headers=admin_headers)
    assert r.status_code == 200
    m.assert_called_once_with('kf_1', ['faq_b', 'faq_a'])


def test_delete_faq_ownership_404(client, admin_headers):
    other = {**FAQ, 'instance_id': 'other'}
    with patch('routes.kefu_admin.kefu_repo.get_faq', return_value=other):
        r = client.delete('/admin/kefu/instances/kf_1/faq/faq_1', headers=admin_headers)
    assert r.status_code == 404


def test_reorder_invalid_body(client, admin_headers):
    r = client.patch('/admin/kefu/instances/kf_1/faq/reorder',
                     json={}, headers=admin_headers)
    assert r.status_code == 400
