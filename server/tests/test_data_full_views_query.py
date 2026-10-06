"""族G 列视图 + 查询台 —— L2 live-server API 层（TD-G01–G07）。

契约锚点（计划④ Global Constraints #5/#6）：列视图同名 400/默认视图不可删/设默认
清旧且仅 admin+public/copy 副本命名；查询台缺集合 400/未知 404/语法错 400、
limit 1..2000、label 重映射。

执行者实读核对（brief 注意块三项）：
- G06 label 重映射：utils/mongo_query.remap_labels 确认 label≠fieldName 时
  映射生效（'名称'→'name'），用 label 直查成立；
- G07 非法查询：mongo_query.translate 对 `$or` 非列表抛
  MongoQueryError("$or requires an array")，query.py 捕获后回
  400 「查询语法错误: …」，样例稳定；缺集合文案实测为「请指定集合 (collection)」
  （带后缀），故按前缀包含断言而非全等；
- 清理：migrations/0025 中 column_views.page_id REFERENCES page_configs(id)
  ON DELETE CASCADE —— 页删即级联删视图，默认视图 v2 留给级联，
  非默认视图（G02 v1 / G03 v+副本）显式删。
"""
import uuid

import pytest

import data_full_live as live

pytestmark = pytest.mark.data_full

NAME = {'id': 'f1', 'label': '名称', 'fieldName': 'name',
        'controlType': 'text', 'required': True, 'order': 1}
QTY = {'id': 'f2', 'label': '数量', 'fieldName': 'qty',
       'controlType': 'number', 'required': False, 'order': 2}


@pytest.fixture(scope='module')
def admin():
    if not live.server_up():
        pytest.skip('dev 栈未启动')
    return live.login()


@pytest.fixture(scope='module')
def pageh(admin):
    page = live.make_page(admin, 'G', 'views', fields=[NAME, QTY])
    yield page
    # 列视图随页删（0025 FK page_id ON DELETE CASCADE，已核实）
    live.drop_page(admin, page)


def test_td_g01_view_crud_and_dup_name(admin, pageh):
    r = live.api('POST', f"/column-views/{pageh['page_id']}/views", admin,
                 {'name': '精简视图', 'isPublic': False,
                  'columns': ['name']})
    assert r.status_code == 201, f'{r.status_code} {r.text[:300]}'
    vid = r.json()['id']
    dup = live.api('POST', f"/column-views/{pageh['page_id']}/views", admin,
                   {'name': '精简视图', 'isPublic': False, 'columns': ['name']})
    assert dup.status_code == 400  # 同名视图已存在（private 按 page+creator 判重）
    lst = live.api('GET', f"/column-views/{pageh['page_id']}/views", admin)
    assert any(v['id'] == vid for v in lst.json()['views'])
    put = live.api('PUT', f"/column-views/{pageh['page_id']}/views/{vid}", admin,
                   {'columns': ['name', 'qty']})
    assert put.status_code < 300


def test_td_g02_default_view_single_and_undeletable(admin, pageh):
    v1 = live.api('POST', f"/column-views/{pageh['page_id']}/views", admin,
                  {'name': '默认甲', 'isPublic': True, 'columns': ['name']}).json()
    live.api('PUT', f"/column-views/{pageh['page_id']}/views/{v1['id']}/default", admin)
    v2 = live.api('POST', f"/column-views/{pageh['page_id']}/views", admin,
                  {'name': '默认乙', 'isPublic': True, 'columns': ['qty']}).json()
    live.api('PUT', f"/column-views/{pageh['page_id']}/views/{v2['id']}/default", admin)
    lst = live.api('GET', f"/column-views/{pageh['page_id']}/views", admin).json()
    assert lst['defaultViewId'] == v2['id']  # 旧默认被清除
    del_default = live.api('DELETE',
                           f"/column-views/{pageh['page_id']}/views/{v2['id']}", admin)
    assert del_default.status_code == 400  # 不能删除默认视图
    # 清理：v1 已非默认 → 显式删；默认 v2 留给页删级联
    del_v1 = live.api('DELETE',
                      f"/column-views/{pageh['page_id']}/views/{v1['id']}", admin)
    assert del_v1.status_code < 300


def test_td_g03_view_copy_and_private_scope(admin, pageh):
    v = live.api('POST', f"/column-views/{pageh['page_id']}/views", admin,
                 {'name': '私甲', 'isPublic': False, 'columns': ['name']}).json()
    vid = v['id']
    cp_id = None
    role_id = None
    uid = None
    try:
        cp = live.api('POST',
                      f"/column-views/{pageh['page_id']}/views/{vid}/copy", admin)
        assert cp.status_code < 300, f'copy: {cp.status_code} {cp.text[:300]}'
        cp_id = cp.json()['id']
        assert '副本' in cp.json()['name']
        # 非 admin 用户看不到他人 private 视图
        role = live.api('POST', '/roles', admin,
                        {'name': f"DTEST-G-role-{uuid.uuid4().hex[:6]}",
                         'defaultPageAccess': 'read'})
        assert role.status_code == 201, f'role create: {role.status_code} {role.text}'
        role_id = role.json()['id']
        uname = f"dtest_g_user_{uuid.uuid4().hex[:6]}"
        u = live.api('POST', '/users', admin,
                     {'username': uname, 'password': 'Dtest#12345',
                      'displayName': 'G 视图探针', 'role': role_id})
        assert u.status_code == 201, f'user create: {u.status_code} {u.text}'
        uid = u.json()['id']
        login = live.api('POST', '/auth/login', None,
                         {'username': uname, 'password': 'Dtest#12345'})
        assert login.status_code == 200, f'probe login: {login.status_code}'
        other = {'Authorization': f"Bearer {login.json()['token']}"}
        others_list = live.api('GET',
                               f"/column-views/{pageh['page_id']}/views", other)
        names = [x['name'] for x in others_list.json()['views']]
        assert '私甲' not in names and cp.json()['name'] not in names
    finally:
        if uid:
            live.api('DELETE', f'/users/{uid}', admin)
        if role_id:
            live.api('DELETE', f'/roles/{role_id}', admin)
        live.api('DELETE', f"/column-views/{pageh['page_id']}/views/{vid}", admin)
        if cp_id:
            live.api('DELETE',
                     f"/column-views/{pageh['page_id']}/views/{cp_id}", admin)


def test_td_g04_view_missing_page_404(admin):
    r = live.api('POST', '/column-views/page-DTEST-nope/views', admin,
                 {'name': '孤儿', 'isPublic': False, 'columns': []})
    assert r.status_code == 404  # 页面配置不存在


def test_td_g05_query_collections_listing(admin, pageh):
    r = live.api('GET', '/query/collections', admin)
    assert r.status_code == 200
    entry = next((c for c in r.json() if c['collection'] == pageh['collection']), None)
    assert entry, '查询台应列出 DTEST 页'
    assert any(f['fieldName'] == 'name' for f in entry['fields'])


def test_td_g06_query_execute_valid(admin, pageh):
    marker = f'查针{uuid.uuid4().hex[:6]}'
    live.api('POST', f"/{pageh['collection']}", admin,
             {'id': uuid.uuid4().hex, 'name': marker, 'qty': 66})
    r = live.api('POST', '/query/execute', admin,
                 {'collection': pageh['collection'],
                  'query': {'名称': marker}, 'limit': 10})
    assert r.status_code < 300, f'{r.status_code} {r.text[:300]}'
    body = r.json()
    assert body['total'] == 1 and body['data'][0]['name'] == marker
    assert body['limit'] == 10
    assert any(c['key'] == 'name' for c in body['columns'])


def test_td_g07_query_error_contract(admin, pageh):
    missing = live.api('POST', '/query/execute', admin, {'query': {}})
    assert missing.status_code == 400
    # 实测文案为「请指定集合 (collection)」（带后缀），按前缀包含断言
    assert '请指定集合' in missing.json()['error']
    unknown = live.api('POST', '/query/execute', admin,
                       {'collection': 'DTEST-ghost', 'query': {}})
    assert unknown.status_code == 404
    assert '集合不存在' in unknown.json()['error']
    bad = live.api('POST', '/query/execute', admin,
                   {'collection': pageh['collection'], 'query': {'$or': 'not-a-list'}})
    assert bad.status_code == 400 and '查询语法错误' in bad.json()['error']
