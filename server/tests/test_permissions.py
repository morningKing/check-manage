import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.permissions import PERMISSION_CATALOG, catalog_keys


def test_catalog_has_expected_keys():
    keys = catalog_keys()
    assert 'admin.users' in keys
    assert 'admin.roles' in keys
    assert 'admin.backup' in keys
    # every entry has key + label + group
    for entry in PERMISSION_CATALOG:
        assert entry['key'] and entry['label'] and entry['group']


def test_catalog_keys_are_unique():
    keys = catalog_keys()
    assert len(keys) == len(set(keys))
