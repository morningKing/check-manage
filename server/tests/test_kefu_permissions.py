from utils.permissions import PERMISSION_CATALOG, all_permission_keys


def test_admin_kefu_in_catalog():
    keys = {e['key'] for e in PERMISSION_CATALOG}
    assert 'admin.kefu' in keys


def test_admin_kefu_in_all_keys():
    assert 'admin.kefu' in all_permission_keys()
