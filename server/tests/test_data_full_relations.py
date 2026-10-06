"""族C 关联体系 —— L2 live-server API 层（TD-C01–C10）。

契约锚点（计划② Global Constraints #10–#13）：PUT /relations 全量替换+双向同步、
记录不存在不 404；reference 继承仅显示层、父删除 RESTRICT 409（单条；批量删除
的实际契约是 200 + deleted:0 + blocked 映射，不返回 409 —— 见 C06 内注释）；
quoteSelect 存 id 数组、被引删除留悬挂 id；relation-graph 单跳两级。
"""
import uuid

import pytest

import data_full_live as live

pytestmark = pytest.mark.data_full

NAME = {'id': 'f1', 'label': '名称', 'fieldName': 'name',
        'controlType': 'text', 'required': True, 'order': 1}


def _relation_field(fid, label, target, target_field='rel', order=2):
    return {'id': fid, 'label': label, 'fieldName': 'rel', 'controlType': 'relation',
            'required': False, 'order': order,
            'relationConfig': {'targetCollection': target, 'displayField': 'name',
                               'targetField': target_field}}


@pytest.fixture(scope='module')
def admin():
    if not live.server_up():
        pytest.skip('dev 栈未启动')
    return live.login()


@pytest.fixture(scope='module')
def ab_pages(admin):
    """A/B 两个互指 relation 字段的页面（双向 M:N 标准拓扑）。

    创建顺序（brief 注意项的简化解，无占位）：A 先建（暂只有 name 字段）→
    B 建（relationConfig 目标 = 已存在的 A collection）→ PUT /pageConfigs/A
    补上指向 B 的 relation 字段。PUT /pageConfigs 的 fields 是整包替换
    （routes/page_configs.py update_page_config：`fields=%s` Json 全量写），
    且此时 A 尚无数据行，FIELDS_LOCKED 锁不触发（仅 has_data 时校验）。
    """
    pa = live.make_page(admin, 'C', 'rel-a', fields=[NAME])
    pb = live.make_page(admin, 'C', 'rel-b', fields=[
        NAME, _relation_field('f2', '关联', pa['collection'])])
    fields = [NAME, _relation_field('f2', '关联', pb['collection'])]
    fix = live.api('PUT', f"/pageConfigs/{pa['page_id']}", admin, {'fields': fields})
    assert fix.status_code < 300, f'{fix.status_code} {fix.text[:200]}'
    pa['fields'] = fields
    yield pa, pb
    live.drop_page(admin, pa)
    live.drop_page(admin, pb)


def _rec(admin, collection, name):
    r = live.api('POST', f'/{collection}', admin, {'id': uuid.uuid4().hex, 'name': name})
    assert r.status_code == 201
    return r.json()


def test_td_c01_relation_put_and_get(admin, ab_pages):
    pa, pb = ab_pages
    a, b = _rec(admin, pa['collection'], 'A甲'), _rec(admin, pb['collection'], 'B甲')
    put = live.api('PUT', f"/relations/{pa['collection']}/{a['id']}/rel", admin,
                   {'targetCollection': pb['collection'], 'targetField': 'rel',
                    'ids': [b['id']]})
    assert put.status_code < 300 and put.json().get('ids') == [b['id']]
    got = live.api('GET', f"/relations/{pa['collection']}/{a['id']}", admin)
    assert got.json().get('rel') == [b['id']]


def test_td_c02_relation_reverse_visible(admin, ab_pages):
    pa, pb = ab_pages
    a, b = _rec(admin, pa['collection'], 'A乙'), _rec(admin, pb['collection'], 'B乙')
    live.api('PUT', f"/relations/{pa['collection']}/{a['id']}/rel", admin,
             {'targetCollection': pb['collection'], 'targetField': 'rel', 'ids': [b['id']]})
    rev = live.api('GET', f"/relations/{pb['collection']}/{b['id']}", admin)
    assert rev.json().get('rel') == [a['id']]  # B 侧反向可见


def test_td_c03_relation_full_replace(admin, ab_pages):
    pa, pb = ab_pages
    a = _rec(admin, pa['collection'], 'A丙')
    b1, b2, b3 = (_rec(admin, pb['collection'], f'B丙{i}') for i in (1, 2, 3))
    tgt = {'targetCollection': pb['collection'], 'targetField': 'rel'}
    live.api('PUT', f"/relations/{pa['collection']}/{a['id']}/rel", admin,
             {**tgt, 'ids': [b1['id'], b2['id']]})
    live.api('PUT', f"/relations/{pa['collection']}/{a['id']}/rel", admin,
             {**tgt, 'ids': [b2['id'], b3['id']]})
    got = live.api('GET', f"/relations/{pa['collection']}/{a['id']}", admin).json()['rel']
    # 顺序不敏感断言：PUT 落库按 set(ids) 迭代序插入（routes/relations.py
    # update_relations：new_ids = set(...)），GET 仅 ORDER BY field_name
    # （无 related_id 次级排序），多 id 间顺序无契约保证
    assert sorted(got) == sorted([b2['id'], b3['id']])
    # 被移除的 b1 反向行同步删除：先验 GET 200 再验 a 不在 b1 的反向列表
    # （T3-R1 加固：原 `in (None, [])` 空转容错会把请求失败误判为清理成功）
    rev = live.api('GET', f"/relations/{pb['collection']}/{b1['id']}", admin)
    assert rev.status_code == 200, f'GET b1 反向关系应 200，得 {rev.status_code}'
    assert a['id'] not in (rev.json().get('rel') or [])


def test_td_c04_relations_inline_on_create(admin, ab_pages):
    pa, pb = ab_pages
    b = _rec(admin, pb['collection'], 'B丁')
    r = live.api('POST', f"/{pa['collection']}", admin,
                 {'id': uuid.uuid4().hex, 'name': 'A丁',
                  '_relations': [{'fieldName': 'rel',
                                  'targetCollection': pb['collection'],
                                  'targetField': 'rel', 'ids': [b['id']]}]})
    assert r.status_code == 201
    assert live.api('GET', f"/relations/{pa['collection']}/{r.json()['id']}",
                    admin).json()['rel'] == [b['id']]


def test_td_c05_record_delete_cleans_both_directions(admin, ab_pages):
    pa, pb = ab_pages
    a, b = _rec(admin, pa['collection'], 'A戊'), _rec(admin, pb['collection'], 'B戊')
    live.api('PUT', f"/relations/{pa['collection']}/{a['id']}/rel", admin,
             {'targetCollection': pb['collection'], 'targetField': 'rel', 'ids': [b['id']]})
    assert live.api('DELETE', f"/{pa['collection']}/{a['id']}", admin).status_code < 300
    # b 的反向行（指向已删的 a）应被双向清理：先验 GET 200 再验移除
    # （T3-R1 加固：同 C03，空转容错已收紧）
    rev = live.api('GET', f"/relations/{pb['collection']}/{b['id']}", admin)
    assert rev.status_code == 200, f'GET b 反向关系应 200，得 {rev.status_code}'
    assert a['id'] not in (rev.json().get('rel') or [])


def test_td_c06_reference_parent_delete_restrict_409(admin):
    parent = live.make_page(admin, 'C', 'ref-p', fields=[NAME])
    child = live.make_page(admin, 'C', 'ref-c', fields=[
        NAME, {'id': 'f2', 'label': '父项', 'fieldName': 'ref', 'controlType': 'reference',
               'required': False, 'order': 2,
               'referenceConfig': {'targetCollection': parent['collection'],
                                   'displayField': 'name', 'inheritFields': ['name']}}])
    try:
        p = _rec(admin, parent['collection'], '父甲')
        c = _rec(admin, child['collection'], '子甲')
        live.api('PUT', f"/{child['collection']}/{c['id']}", admin,
                 {'ref': p['id'], '_version': c['_version']})
        single = live.api('DELETE', f"/{parent['collection']}/{p['id']}", admin)
        assert single.status_code == 409, f'RESTRICT 应 409，得 {single.status_code}'
        assert '引用' in single.json().get('error', '')
        # 「批量删除同样 blocked」的实际契约：HTTP 200、deleted=0、blocked 映射
        # 给出被引原因——batch_delete_items（routes/dynamic.py）将被引 id 从
        # deletable_ids 剔除后照常返回，不返回 409（实测基线跑失败留证）
        batch = live.api('POST', f"/{parent['collection']}/batch-delete", admin,
                         {'ids': [p['id']]})
        assert batch.status_code == 200, f'批量删除 HTTP 层应 200，得 {batch.status_code}'
        body = batch.json()
        assert body.get('deleted') == 0, f'被引父记录不应被批量删除：{body}'
        assert '引用' in (body.get('blocked', {}).get(p['id']) or ''), \
            f'blocked 原因应含「引用」：{body}'
    finally:
        live.drop_page(admin, child)
        live.drop_page(admin, parent)


def test_td_c07_reference_inheritance_display_layer_only(admin):
    """实际契约：继承不落库——子记录 JSONB 无父字段拷贝，仅前端 _ref_ 显示键。"""
    parent = live.make_page(admin, 'C', 'ref-p2', fields=[NAME])
    child = live.make_page(admin, 'C', 'ref-c2', fields=[
        NAME, {'id': 'f2', 'label': '父项', 'fieldName': 'ref', 'controlType': 'reference',
               'required': False, 'order': 2,
               'referenceConfig': {'targetCollection': parent['collection'],
                                   'displayField': 'name', 'inheritFields': ['name']}}])
    try:
        p = _rec(admin, parent['collection'], '父乙')
        c = _rec(admin, child['collection'], '子乙')
        live.api('PUT', f"/{child['collection']}/{c['id']}", admin,
                 {'ref': p['id'], '_version': c['_version']})
        got = live.api('GET', f"/{child['collection']}/{c['id']}", admin).json()
        assert got['ref'] == p['id']
        assert 'name' not in got or got.get('name') == '子乙'  # 父 name 未被拷入
    finally:
        live.drop_page(admin, child)
        live.drop_page(admin, parent)


def test_td_c08_quoteselect_dangling_id_on_delete(admin):
    """实际契约：被引记录删除后引用方悬挂 id 不清理。"""
    q = live.make_page(admin, 'C', 'quote-q', fields=[NAME])
    a = live.make_page(admin, 'C', 'quote-a', fields=[
        NAME, {'id': 'f2', 'label': '引用', 'fieldName': 'quote',
               'controlType': 'quoteSelect', 'required': False, 'order': 2,
               'quoteConfig': {'targetCollection': q['collection'],
                               'displayField': 'name'}}])
    try:
        q1 = _rec(admin, q['collection'], '被引甲')
        r = _rec(admin, a['collection'], '引用方')
        live.api('PUT', f"/{a['collection']}/{r['id']}", admin,
                 {'quote': [q1['id']], '_version': r['_version']})
        assert live.api('DELETE', f"/{q['collection']}/{q1['id']}",
                        admin).status_code < 300
        got = live.api('GET', f"/{a['collection']}/{r['id']}", admin).json()
        assert got['quote'] == [q1['id']]  # 悬挂 id 保留（前端回退显示原始 id）
    finally:
        live.drop_page(admin, a)
        live.drop_page(admin, q)


def test_td_c09_relation_graph_two_level(admin):
    """A1 -relation- B1、A1 -reference- P1、A1 quoteSelect [Q1] → 单跳两级全在图上。"""
    parent = live.make_page(admin, 'C', 'g-p', fields=[NAME])
    qb = live.make_page(admin, 'C', 'g-q', fields=[NAME])
    gb = live.make_page(admin, 'C', 'g-b', fields=[
        NAME, _relation_field('f2', '关联', 'PLACEHOLDER')])
    ga = live.make_page(admin, 'C', 'g-a', fields=[
        NAME, _relation_field('f2', '关联', gb['collection']),
        {'id': 'f3', 'label': '父项', 'fieldName': 'ref', 'controlType': 'reference',
         'required': False, 'order': 3,
         'referenceConfig': {'targetCollection': parent['collection'],
                             'displayField': 'name', 'inheritFields': []}},
        {'id': 'f4', 'label': '引用', 'fieldName': 'quote', 'controlType': 'quoteSelect',
         'required': False, 'order': 4,
         'quoteConfig': {'targetCollection': qb['collection'], 'displayField': 'name'}}])
    try:
        p, q1 = _rec(admin, parent['collection'], '图父'), _rec(admin, qb['collection'], '图引')
        b1 = _rec(admin, gb['collection'], '图B')
        a1 = _rec(admin, ga['collection'], '图A')
        live.api('PUT', f"/relations/{ga['collection']}/{a1['id']}/rel", admin,
                 {'targetCollection': gb['collection'], 'targetField': 'rel',
                  'ids': [b1['id']]})
        live.api('PUT', f"/{ga['collection']}/{a1['id']}", admin,
                 {'ref': p['id'], 'quote': [q1['id']], '_version': a1['_version']})
        g = live.api('GET', f"/relation-graph/{ga['collection']}/{a1['id']}", admin)
        assert g.status_code == 200
        body = g.json()
        assert body['centerId'] == a1['id']
        node_ids = {n['id'] for n in body['nodes']}
        assert {b1['id'], p['id'], q1['id']} <= node_ids, f'邻居缺失: {node_ids}'
        rel_types = {e['relType'] for e in body['edges']}
        assert {'relation', 'reference', 'quoteSelect'} <= rel_types
    finally:
        for h in (ga, gb, qb, parent):
            live.drop_page(admin, h)


def test_td_c10_relations_put_missing_record_no_404(admin, ab_pages):
    """实际契约：PUT /relations 对不存在的记录不 404（仅取显示名失败容忍）。"""
    pa, pb = ab_pages
    b = _rec(admin, pb['collection'], 'B己')
    put = live.api('PUT', f"/relations/{pa['collection']}/no-such-record/rel", admin,
                   {'targetCollection': pb['collection'], 'targetField': 'rel',
                    'ids': [b['id']]})
    assert put.status_code < 300, f'实际契约不 404，得 {put.status_code}: {put.text[:200]}'
