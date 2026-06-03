"""Regression guard: every `require_permission('<key>')` literal used in route
modules must reference a key that exists in the permission catalog.

A typo'd key (e.g. `admin.webhook` vs `admin.webhooks`) would never resolve to a
granted capability, so the decorator would permanently 403 *everyone* — including
admins, since the catalog is the single source of truth the role manager renders.
This scans the route files statically so such a mismatch fails fast in CI.
"""
import os
import re
import glob
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.permissions import catalog_keys

_ROUTES_DIR = os.path.join(os.path.dirname(__file__), '..', 'routes')
_CALL_RE = re.compile(r"require_permission\(['\"]([^'\"]+)['\"]\)")


def _collect_keys():
    """Return list of (file, key) for every require_permission('<key>') literal."""
    found = []
    for path in glob.glob(os.path.join(_ROUTES_DIR, '*.py')):
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read()
        for key in _CALL_RE.findall(text):
            found.append((os.path.basename(path), key))
    return found


def test_all_route_permission_keys_in_catalog():
    valid = set(catalog_keys())
    found = _collect_keys()
    # Sanity: the scan actually found call sites (guards against a broken regex).
    assert found, 'no require_permission() call sites found in routes/*.py'

    unknown = sorted({(f, k) for f, k in found if k not in valid})
    assert not unknown, (
        'require_permission() references keys not in the permission catalog '
        f'(catalog_keys()): {unknown}'
    )
