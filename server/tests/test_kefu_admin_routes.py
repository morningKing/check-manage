from unittest.mock import patch


def test_create_instance_requires_permission(client, dev_headers):
    # developer 无 admin.kefu
    resp = client.post('/admin/kefu/instances', json={'slug': 'x', 'name': 'X'},
                       headers=dev_headers)
    assert resp.status_code == 403


def test_create_instance_ok(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.create_instance',
               return_value={'id': 'kf_1', 'slug': 'presale', 'name': '售前'}) as m:
        resp = client.post('/admin/kefu/instances',
                           json={'slug': 'presale', 'name': '售前'},
                           headers=admin_headers)
    assert resp.status_code == 201
    assert resp.get_json()['slug'] == 'presale'
    m.assert_called_once()


def test_create_instance_validates_slug(client, admin_headers):
    resp = client.post('/admin/kefu/instances', json={'name': '缺 slug'},
                       headers=admin_headers)
    assert resp.status_code == 400


def test_list_instances(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.list_instances', return_value=[]):
        resp = client.get('/admin/kefu/instances', headers=admin_headers)
    assert resp.status_code == 200
    assert resp.get_json() == {'instances': []}
