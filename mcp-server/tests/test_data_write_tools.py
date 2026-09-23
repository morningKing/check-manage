"""Tests for the data-write tools (data_create_records / data_update_record /
data_delete_record / data_attach_menu)。

transport(_data_api.execute)打桩:验证请求整形(集合解析、id 生成、乐观锁、
菜单 body)、错误映射(DataApiError → 带上下文的异常)、只读/管理员门禁。
转发端点本身的真实语义在 server/tests/test_ai_data_internal.py 用真库覆盖。
"""

from contextlib import contextmanager
from unittest.mock import patch

import pytest

from context import ToolContext
from tools._data_api import DataApiError


def _ctx(role="developer"):
    return ToolContext(session_id="sess-1", user_id="u1", role=role)


def _fake_db_fetchone(results):
    """fetchone 按次序返回 results;用于 resolve_collection/页定位。"""
    it = iter(results)
    cur = MagicMock()
    cur.__enter__.return_value = cur
    cur.fetchone.side_effect = lambda: next(it, None)
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.return_value = cur

    @contextmanager
    def _get():
        yield conn
    return _get


from unittest.mock import MagicMock  # noqa: E402


def test_create_records_generates_ids_and_stops_on_error():
    import tools.data_create_records as t
    with patch.object(t, 'get_db', _fake_db_fetchone([('page-abc',)])), \
         patch.object(t, 'execute') as ex:
        ex.return_value = {'id': 'r1', 'title': 'x'}
        out = t.handle({'collection': 'abc', 'records': [{'title': 'x'}, {'title': 'y'}]},
                       _ctx())
        assert out['created'] == 2 and len(out['records']) == 2
        # id 缺省自动生成;显式字段透传
        first_call = ex.call_args_list[0]
        assert first_call.args[1:3] == ('POST', '/abc')
        assert first_call.args[3]['id']
        assert first_call.args[3]['title'] == 'x'


def test_create_records_explicit_id_preserved_and_fail_fast():
    import tools.data_create_records as t
    with patch.object(t, 'get_db', _fake_db_fetchone([('page-abc',)])), \
         patch.object(t, 'execute') as ex:
        ex.side_effect = [{'id': 'my-id'}, DataApiError(409, '主键重复：orderNo=A-1')]
        with pytest.raises(Exception, match='第 2 条创建失败.*主键重复.*orderNo=A-1'):
            t.handle({'collection': 'abc',
                      'records': [{'id': 'my-id', 'title': 'x'}, {'title': 'y'}]},
                     _ctx())
        # 显式 id 原样保留
        assert ex.call_args_list[0].args[3]['id'] == 'my-id'
        # fail-fast:只发起了 2 次调用(第 2 条失败后不再继续)
        assert ex.call_count == 2


def test_create_records_continue_on_error_collects():
    import tools.data_create_records as t
    with patch.object(t, 'get_db', _fake_db_fetchone([('page-abc',)])), \
         patch.object(t, 'execute') as ex:
        ex.side_effect = [DataApiError(400, '校验失败'), {'id': 'ok-2'}]
        out = t.handle({'collection': 'abc',
                        'records': [{'title': 'bad'}, {'title': 'good'}],
                        'continue_on_error': True}, _ctx())
        assert out['created'] == 1 and out['failed'] == 1
        assert out['errors'][0]['error'] == '校验失败'
        assert out['records'][0]['id'] == 'ok-2'


def test_create_records_validates_and_resolves_display_name():
    import tools.data_create_records as t
    with pytest.raises(Exception, match='records 必填'):
        t.handle({'collection': 'abc', 'records': []}, _ctx())
    with patch.object(t, 'get_db', _fake_db_fetchone([(None,)])):
        with pytest.raises(Exception, match='不存在'):
            t.handle({'collection': 'nope', 'records': [{'a': 1}]}, _ctx())
    with patch.object(t, 'get_db', _fake_db_fetchone([('page-abc',)])):
        with pytest.raises(Exception, match='最多 50 条'):
            t.handle({'collection': 'abc', 'records': [{}] * 51}, _ctx())


def test_readonly_roles_blocked():
    import tools.data_create_records as tc
    import tools.data_delete_record as td
    with pytest.raises(PermissionError):
        tc.handle({'collection': 'a', 'records': [{'x': 1}]}, _ctx(role='kefu-guest'))
    with pytest.raises(PermissionError):
        td.handle({'collection': 'a', 'id': 'r1'}, _ctx(role='guest'))


def test_update_record_sends_partial_data_and_version():
    import tools.data_update_record as t
    with patch.object(t, 'get_db', _fake_db_fetchone([('page-abc',)])), \
         patch.object(t, 'execute') as ex:
        ex.return_value = {'id': 'r1', '_version': 3, 'status': 'done'}
        out = t.handle({'collection': '巡检记录', 'id': 'r1', 'data': {'status': 'done'},
                        'expected_version': 2}, _ctx())
        assert ex.call_args.args[1:3] == ('PUT', '/abc/r1')
        body = ex.call_args.args[3]
        assert body == {'status': 'done', 'id': 'r1', '_version': 2}
        assert out['version'] == 3


def test_update_record_surfaces_route_error():
    import tools.data_update_record as t
    with patch.object(t, 'get_db', _fake_db_fetchone([('page-abc',)])), \
         patch.object(t, 'execute', side_effect=DataApiError(409, '状态不允许从已完成改回进行中')):
        with pytest.raises(Exception, match='状态不允许'):
            t.handle({'collection': 'abc', 'id': 'r1', 'data': {'status': 'running'}}, _ctx())


def test_delete_record_paths():
    import tools.data_delete_record as t
    with patch.object(t, 'get_db', _fake_db_fetchone([('page-abc',)])), \
         patch.object(t, 'execute') as ex:
        out = t.handle({'collection': 'abc', 'id': 'r9'}, _ctx())
        assert ex.call_args.args[1:3] == ('DELETE', '/abc/r9')
        assert out['deleted'] is True
    with patch.object(t, 'get_db', _fake_db_fetchone([('page-abc',)])), \
         patch.object(t, 'execute',
                      side_effect=DataApiError(409, '无法删除：被「订单」的 2 条记录引用')):
        with pytest.raises(Exception, match='被「订单」的 2 条记录引用'):
            t.handle({'collection': 'abc', 'id': 'r9'}, _ctx())


def test_attach_menu_requires_admin_and_resolves_page_and_parent():
    import tools.data_attach_menu as t
    # 非管理员直接拒绝
    with pytest.raises(PermissionError):
        t.handle({'collection': 'abc', 'parent': 'p'}, _ctx(role='developer'))

    # 页定位 + 项目父级解析 + 路由 body
    fetches = iter([('page-abc', '巡检记录', '/abc'), ('proj-1',)])
    cur = MagicMock()
    cur.__enter__.return_value = cur
    cur.fetchone.side_effect = lambda: next(fetches, None)
    conn = MagicMock()
    conn.cursor.return_value = cur

    @contextmanager
    def _get():
        yield conn
    with patch.object(t, 'get_db', _get), patch.object(t, 'execute') as ex:
        ex.return_value = {}
        out = t.handle({'collection': 'abc', 'parent': '巡检项目'}, _ctx(role='admin'))
        assert ex.call_args.args[1:3] == ('POST', '/menus')
        body = ex.call_args.args[3]
        assert body['name'] == '巡检记录' and body['menuType'] == 'data'
        assert body['pageId'] == 'page-abc' and body['path'] == '/abc'
        assert body['roles'] == ['admin', 'developer', 'guest']
        assert body['icon'] == 'Document' and body['parentId'] == 'proj-1'
        assert body['id']  # menus.id 无默认值,工具侧生成
        assert out['attached'] is True


def test_attach_menu_parent_required_and_page_must_exist():
    import tools.data_attach_menu as t
    with pytest.raises(Exception, match='parent 必填'):
        t.handle({'collection': 'abc'}, _ctx(role='admin'))
    # 页不存在
    with patch.object(t, 'get_db', _fake_db_fetchone([None])):
        with pytest.raises(Exception, match='数据页「abc」不存在'):
            t.handle({'collection': 'abc', 'parent': 'p'}, _ctx(role='admin'))
    # 父级不是项目菜单
    fetches = iter([('page-abc', '页', '/abc'), None])
    cur = MagicMock()
    cur.__enter__.return_value = cur
    cur.fetchone.side_effect = lambda: next(fetches, None)
    conn = MagicMock()
    conn.cursor.return_value = cur

    @contextmanager
    def _get2():
        yield conn
    with patch.object(t, 'get_db', _get2):
        with pytest.raises(Exception, match='不存在或不是项目类型'):
            t.handle({'collection': 'abc', 'parent': 'mystery'}, _ctx(role='admin'))
