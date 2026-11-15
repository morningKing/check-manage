"""OC 栈路由注册表单测（OC2 适配阶段一）。

fail-closed 契约：行缺失/NULL/未知值/DB 异常一律 'v1'；runtime_for 双检锁
缓存；未知 kind 显式抛错。
"""
from unittest.mock import patch, MagicMock

import pytest

from ai.runtime import registry


def _patch_db(row=None, raise_exc=None):
    """registry 只经 cursor().execute→fetchone 读单列——纯 MagicMock 桩。"""
    conn = MagicMock()
    cur = MagicMock()
    if raise_exc is not None:
        cur.execute.side_effect = raise_exc
    else:
        cur.fetchone.return_value = row
    conn.cursor.return_value = cur
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=conn)
    ctx.__exit__ = MagicMock(return_value=False)
    return patch('db.get_db', return_value=ctx)


def test_missing_sid_fails_closed_v1():
    assert registry.session_runtime_kind('') == 'v1'
    assert registry.session_runtime_kind(None) == 'v1'


def test_null_and_unknown_values_fail_closed_v1():
    with _patch_db(row=None):
        assert registry.session_runtime_kind('s1') == 'v1'
    with _patch_db(row=('weird',)):
        assert registry.session_runtime_kind('s1') == 'v1'
    with _patch_db(row=(None,)):
        assert registry.session_runtime_kind('s1') == 'v1'


def test_v2_row_and_case_insensitive():
    with _patch_db(row=('v2',)):
        assert registry.session_runtime_kind('s1') == 'v2'
    with _patch_db(row=('  V2 ',)):
        assert registry.session_runtime_kind('s1') == 'v2'


def test_db_error_fails_closed_v1():
    with _patch_db(raise_exc=RuntimeError('db down')):
        assert registry.session_runtime_kind('s1') == 'v1'


def test_runtime_for_unknown_kind_raises():
    from ai.runtime.base import RuntimeCapabilityError
    with pytest.raises(RuntimeCapabilityError):
        registry.runtime_for('v3')


def test_runtime_for_returns_cached_instances():
    rt1 = registry.runtime_for('v1')
    rt2 = registry.runtime_for('v1')
    assert rt1 is rt2
    assert rt1.kind == 'opencode_local'
    rt_v2 = registry.runtime_for('v2')
    assert rt_v2.kind == 'opencode_v2_local'
    assert registry.runtime_for('v2') is rt_v2


def test_client_for_session_v2_returns_client_v2():
    with _patch_db(row=('v2',)):
        client = registry.client_for_session('s2')
    from ai.opencode.opencode_client_v2 import OpenCodeClientV2
    assert isinstance(client, OpenCodeClientV2)
    from config import OPENCODE2_BASE_URL
    assert client.base_url == OPENCODE2_BASE_URL.rstrip('/')


def test_client_for_oc_id_missing_row_fails_closed_v1():
    with _patch_db(row=None):
        client = registry.client_for_oc_id('ses_missing')
    from ai.opencode.opencode_client import OpenCodeClient
    assert isinstance(client, OpenCodeClient)
    assert registry.client_for_oc_id('') is not None
