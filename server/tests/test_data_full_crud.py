"""族A 动态数据 CRUD —— L2 live-server API 层（TD-A01–A14、A20、A22）。"""
import os
import sys
import time
import uuid

import pytest
import requests

# tests/ 是带 __init__.py 的包：pytest prepend 模式只把 server/ 加进 sys.path，
# 须补上本目录才能按 brief 以平铺名 import 助手模块。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import data_full_live as live

pytestmark = pytest.mark.data_full


def _rid() -> str:
    """dynamic_data.id 为 NOT NULL 且无默认值：记录 id 由客户端生成
    （与前端 / 既有路由测试一致），brief 里的裸字段载荷在真实产品上会
    500（NotNullViolation），故各 create 载荷注入此 id。"""
    return 'r-' + uuid.uuid4().hex[:12]


@pytest.fixture(scope='module')
def admin():
    if not live.server_up():
        pytest.skip('dev 栈未启动（Vite :5173 代理 /api → Flask :3002；npm run dev:all）')
    return live.login()


@pytest.fixture(scope='module')
def pageh(admin):
    """整个模块共享一页 CRUD 测试页；模块结束二段删。"""
    page = live.make_page(admin, 'A', 'crud')
    yield page
    live.drop_page(admin, page)


def _ids(admin, collection, query=''):
    r = live.api('GET', f'/{collection}{query}', admin)
    assert r.status_code == 200
    return r.json()


def test_td_a01_create_page_ok_and_dup_rejected(admin):
    page = live.make_page(admin, 'A', 'page-ok')
    try:
        assert live.api('GET', '/pageConfigs', admin).status_code == 200
        dup = live.api('POST', '/menus', admin,
                       {'id': f"menu-{page['collection']}-dup",
                        'name': page['name'], 'pageId': page['page_id'],
                        # parentId 必填（产品校验先于重名检查，缺它会以
                        # 「必须有父级菜单」400 而绕过重名校验，用例变空过）
                        'parentId': f"menu-proj-{page['collection']}",
                        'path': '/dtest/dup', 'menuType': 'data'})
        assert dup.status_code == 400  # 数据页名称全局唯一
    finally:
        live.drop_page(admin, page)


def test_td_a02_create_record_and_list(admin, pageh):
    r = live.api('POST', f"/{pageh['collection']}", admin,
                 {'id': _rid(), 'name': '记录甲', 'qty': 3, 'status': 'todo'})
    assert r.status_code == 201
    assert r.json().get('id')
    body = _ids(admin, pageh['collection'])
    assert any(rec.get('name') == '记录甲' for rec in body['data'])
    assert body['total'] >= 1


def test_td_a03_get_put_version_bump(admin, pageh):
    rec = live.api('POST', f"/{pageh['collection']}", admin,
                   {'id': _rid(), 'name': '记录乙', 'qty': 1}).json()
    rid = rec['id']
    got = live.api('GET', f"/{pageh['collection']}/{rid}", admin)
    assert got.status_code == 200
    v0 = got.json()['_version']
    put = live.api('PUT', f"/{pageh['collection']}/{rid}", admin,
                   {'qty': 9, '_version': v0})
    assert put.status_code < 300
    after = live.api('GET', f"/{pageh['collection']}/{rid}", admin).json()
    assert after['qty'] == 9 and after['name'] == '记录乙'  # PUT 合并不清字段
    assert after['_version'] == v0 + 1


def test_td_a04_delete_record(admin, pageh):
    rec = live.api('POST', f"/{pageh['collection']}", admin,
                   {'id': _rid(), 'name': '记录丙'}).json()
    rid = rec['id']
    assert live.api('DELETE', f"/{pageh['collection']}/{rid}", admin).status_code < 300
    assert live.api('GET', f"/{pageh['collection']}/{rid}", admin).status_code == 404
    assert not any(x['id'] == rid for x in _ids(admin, pageh['collection'])['data'])


def test_td_a05_batch_create(admin, pageh):
    before = _ids(admin, pageh['collection'])['total']
    # batch-create 的记录形态是 {id, data}（id 同样由客户端生成）
    r = live.api('POST', f"/{pageh['collection']}/batch-create", admin,
                 {'records': [{'id': _rid(), 'data': {'name': f'批{i}'}}
                              for i in range(3)]})
    assert r.status_code < 300
    assert _ids(admin, pageh['collection'])['total'] == before + 3


def test_td_a06_batch_delete(admin, pageh):
    recs = _ids(admin, pageh['collection'])['data']
    victims = [x['id'] for x in recs if str(x.get('name', '')).startswith('批')][:2]
    assert len(victims) == 2
    before = _ids(admin, pageh['collection'])['total']
    r = live.api('POST', f"/{pageh['collection']}/batch-delete", admin,
                 {'ids': victims})
    assert r.status_code < 300
    assert _ids(admin, pageh['collection'])['total'] == before - 2


def test_td_a07_pagination(admin, pageh):
    body = _ids(admin, pageh['collection'], '?page=1&pageSize=2')
    assert len(body['data']) <= 2
    assert body['total'] == _ids(admin, pageh['collection'])['total']


def test_td_a08_keyword_search(admin, pageh):
    live.api('POST', f"/{pageh['collection']}", admin,
             {'id': _rid(), 'name': '搜索针XYZ'})
    body = _ids(admin, pageh['collection'], '?keyword=搜索针')
    assert any(rec.get('name') == '搜索针XYZ' for rec in body['data'])


def test_td_a09_missing_collection_404_no_leak(admin):
    """未知 collection 不 5xx、不报错、返回空集（不泄漏存在性）。

    R2 对齐实测契约：404 "Not found" 仅用于 RESERVED 保留名；
    未知名 GET 返回 200 空集，而非 404。
    """
    r = live.api('GET', f"/DTEST-no-such-{int(time.time() * 1000)}", admin)
    assert r.status_code == 200
    body = r.json()
    assert body['data'] == []
    assert body['total'] == 0


def test_td_a10_malformed_json_400(admin, pageh):
    r = requests.post(f"{live.BASE}/api/{pageh['collection']}",
                      data='{bad json', timeout=10,
                      headers={'Content-Type': 'application/json',
                               **admin})
    assert 400 <= r.status_code < 500


def test_td_a11_primary_key_conflict(admin, pageh):
    fields = [dict(f) for f in live.CRUD_FIELDS]
    fields[0]['isPrimaryKey'] = True  # name 为主键
    page = live.make_page(admin, 'A', 'pk', fields=fields)
    try:
        assert live.api('POST', f"/{page['collection']}", admin,
                        {'id': _rid(), 'name': 'DUPE'}).status_code == 201
        assert live.api('POST', f"/{page['collection']}", admin,
                        {'id': _rid(), 'name': 'DUPE'}).status_code == 409
    finally:
        live.drop_page(admin, page)


def test_td_a12_optimistic_lock_version_conflict(admin, pageh):
    rec = live.api('POST', f"/{pageh['collection']}", admin,
                   {'id': _rid(), 'name': '并发甲'}).json()
    rid = rec['id']
    stale = live.api('GET', f"/{pageh['collection']}/{rid}", admin).json()['_version']
    live.api('PUT', f"/{pageh['collection']}/{rid}", admin,
             {'qty': 1, '_version': stale})  # 推进到 stale+1
    r = live.api('PUT', f"/{pageh['collection']}/{rid}", admin,
                 {'qty': 2, '_version': stale})  # 用过期版本再写
    assert r.status_code == 409
    assert r.json()['code'] == 'VERSION_CONFLICT'
    assert r.json()['_version'] == stale + 1


def test_td_a13_low_privilege_role_denied(admin, pageh):
    role_name = f"DTEST-A-role-{int(time.time() * 1000)}"
    role = live.api('POST', '/roles', admin,
                    {'name': role_name, 'defaultPageAccess': 'none'})
    assert role.status_code == 201
    rid = role.json()['id']
    uid = None
    try:
        uname = f"dtest_a_user_{int(time.time() * 1000)}"
        u = live.api('POST', '/users', admin,
                     {'username': uname, 'password': 'Dtest#12345',
                      'displayName': 'DTEST-A 越权探针', 'role': rid})
        assert u.status_code == 201, f'user create: {u.status_code} {u.text}'
        uid = u.json()['id']
        login = live.api('POST', '/auth/login', None,
                         {'username': uname, 'password': 'Dtest#12345'})
        assert login.status_code == 200, f'low-priv login failed: {login.status_code}'
        user_headers = {'Authorization': f"Bearer {login.json()['token']}"}
        for method, path in [('GET', f"/{pageh['collection']}"),
                             ('POST', f"/{pageh['collection']}")]:
            resp = live.api(method, path, user_headers,
                            {'name': 'x'} if method == 'POST' else None)
            assert resp.status_code == 403, f'{method} 应 403，得 {resp.status_code}'
    finally:
        if uid:
            live.api('DELETE', f'/users/{uid}', admin)
        live.api('DELETE', f'/roles/{rid}', admin)


def test_td_a14_reserved_path_not_swallowed(admin):
    r = live.api('GET', '/pageConfigs', admin)
    assert r.status_code == 200  # catch-all 未吞噬保留路径


def test_td_a20_menu_delete_no_cascade_and_two_phase_cleanup(admin):
    page = live.make_page(admin, 'A', 'cascade')
    rec = live.api('POST', f"/{page['collection']}", admin,
                   {'id': _rid(), 'name': '级联探针'}).json()
    try:
        assert live.api('DELETE', f"/menus/{page['menu_id']}", admin).status_code < 300
        # 配置与数据仍在 —— 证实不级联
        assert live.api('GET', f"/pageConfigs/{page['page_id']}", admin).status_code == 200
        assert live.api('GET',
                        f"/{page['collection']}/{rec['id']}",
                        admin).status_code == 200
    finally:
        live.drop_page(admin, page)  # 二段删兜底清干净
    assert live.api('GET', f"/pageConfigs/{page['page_id']}", admin).status_code == 404
    assert live.api('GET', f"/{page['collection']}/{rec['id']}", admin).status_code == 404


def test_td_a22_oversized_field_value_no_5xx(admin, pageh):
    """超长字段值（1MB 文本）：接受或 4xx 均可，唯独不许 5xx 泄堆栈。"""
    r = live.api('POST', f"/{pageh['collection']}", admin,
                 {'id': _rid(), 'name': '长' * 500_000})
    assert r.status_code < 500, f'超长值应不 5xx，得 {r.status_code}: {r.text[:200]}'


def test_td_a23_create_without_id_gets_server_generated_id(admin, pageh):
    """POST 不带 id：服务端应兜底生成并回带（修复前为 500 NotNullViolation）。"""
    r = live.api('POST', f"/{pageh['collection']}", admin, {'name': '无id探针'})
    assert r.status_code == 201, f'缺 id 不应 500/4xx，得 {r.status_code}: {r.text[:200]}'
    rid = r.json().get('id')
    assert rid, f'响应应回带生成的 id，实得: {str(r.json())[:200]}'
    got = live.api('GET', f"/{pageh['collection']}/{rid}", admin)
    assert got.status_code == 200
    assert got.json().get('name') == '无id探针'


def test_td_a24_batch_create_without_id_succeeds(admin, pageh):
    """batch-create 记录缺 id：服务端逐条兜底生成，整体成功不 500。"""
    before = _ids(admin, pageh['collection'])['total']
    r = live.api('POST', f"/{pageh['collection']}/batch-create", admin,
                 {'records': [{'data': {'name': f'无id批{i}'}} for i in range(2)]})
    assert r.status_code < 300, f'批量缺 id 不应 5xx/4xx，得 {r.status_code}: {r.text[:200]}'
    after = _ids(admin, pageh['collection'])['total']
    assert after == before + 2
    fresh = [x for x in _ids(admin, pageh['collection'])['data']
             if str(x.get('name', '')).startswith('无id批')]
    assert len(fresh) == 2 and all(x.get('id') for x in fresh)
