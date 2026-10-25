# -*- coding: utf-8 -*-
"""registry manifest 测试：注册校验/全集一致/视图形状。"""
import pytest


@pytest.fixture(autouse=True)
def _clean_test_meta():
    yield
    from tools import registry
    registry._META.pop('mtg-y', None)


def test_every_registered_tool_has_meta():
    from tools import _TOOLS
    from tools.registry import validate_registry
    validate_registry(set(_TOOLS))          # 不抛 = 全集一致
    assert len(_TOOLS) == 27                 # 25 模块，memory 出 3 个（spec §5.1）


def test_register_meta_rejects_bad_group():
    from tools.registry import register_meta
    with pytest.raises(RuntimeError, match='unknown group'):
        register_meta('mtg-x', group='no-such-group', access='read')


def test_register_meta_rejects_bad_access_and_dup():
    from tools.registry import register_meta
    with pytest.raises(RuntimeError, match='bad access'):
        register_meta('mtg-x', group='exec', access='admin')
    register_meta('mtg-y', group='exec', access='read')
    with pytest.raises(RuntimeError, match='twice'):
        register_meta('mtg-y', group='exec', access='write')


def test_validate_mismatch_raises():
    from tools.registry import validate_registry
    with pytest.raises(RuntimeError, match='missing META'):
        validate_registry({'nonexistent-tool'})


def test_tool_meta_shape_and_order():
    from tools import tool_meta
    items = {d['name']: d for d in tool_meta()}
    assert items['query_collection']['group'] == 'data-query'
    assert items['query_collection']['group_name'] == '数据查询'
    assert items['run_python']['access'] == 'write'
    assert items['memory_search']['access'] == 'read'
    orders = [d['group_order'] for d in tool_meta()]
    assert orders == sorted(orders)          # 组序单调
    assert isinstance(items['query_collection']['description'], str)
