from unittest.mock import patch


def test_create_instance_requires_permission(client, dev_headers):
    # developer 无 admin.kefu
    resp = client.post('/admin/kefu/instances', json={'slug': 'x', 'name': 'X'},
                       headers=dev_headers)
    assert resp.status_code == 403


def test_create_instance_ok(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.get_instance_by_slug', return_value=None), \
         patch('routes.kefu_admin.kefu_repo.create_instance',
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


def test_create_instance_duplicate_slug(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.get_instance_by_slug',
               return_value={'id': 'existing'}):
        resp = client.post('/admin/kefu/instances',
                           json={'slug': 'presale', 'name': '售前'},
                           headers=admin_headers)
    assert resp.status_code == 409


def test_list_instances(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.list_instances', return_value=[]):
        resp = client.get('/admin/kefu/instances', headers=admin_headers)
    assert resp.status_code == 200
    assert resp.get_json() == {'instances': []}


def test_get_instance_404(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.get_instance', return_value=None):
        resp = client.get('/admin/kefu/instances/nope', headers=admin_headers)
    assert resp.status_code == 404


def test_update_instance_404(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.update_instance', return_value=None):
        resp = client.patch('/admin/kefu/instances/nope', json={'name': 'x'},
                            headers=admin_headers)
    assert resp.status_code == 404


def test_delete_instance_404(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.delete_instance', return_value=False):
        resp = client.delete('/admin/kefu/instances/nope', headers=admin_headers)
    assert resp.status_code == 404
