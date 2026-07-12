"""
集成测试：数据页关键字搜索（reference / quoteSelect / relation 字段）

验证 build_keyword_conditions 对关联字段的搜索支持。
使用真实 DB（需要 casemanage 可用），测试数据在 setup/teardown 中自清理。
"""

import sys
import os
import json
import pytest
import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import DB_CONFIG
from utils.search_text import compute_search_text


@pytest.fixture(autouse=True)
def _ensure_cache_fresh():
    """Ensure the RBAC permission cache is clean for this module."""
    import utils.permissions as perms
    perms.invalidate_cache()
    yield
    perms.invalidate_cache()


@pytest.fixture
def search_db():
    """准备含 reference + quoteSelect 字段的测试数据，测试后自动清理。"""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    # 清理
    for tbl, where in [('dynamic_data', "collection IN ('_t_products','_t_orders')"),
                       ('page_configs', "id IN ('page-_t_products','page-_t_orders')"),
                       ('data_relations', "collection = '_t_orders'")]:
        cur.execute(f"DELETE FROM {tbl} WHERE {where}")
    conn.commit()

    # 产品表：有两个 text 字段，第一个是'产品编号'，第二个是'产品名称'
    products_fields = [
        {'fieldName': '产品编号', 'label': '产品编号', 'controlType': 'text'},
        {'fieldName': '产品名称', 'label': '产品名称', 'controlType': 'text'},
        {'fieldName': '价格', 'label': '价格', 'controlType': 'number'},
    ]
    cur.execute("INSERT INTO page_configs (id, name, fields) VALUES (%s,%s,%s)",
                ('page-_t_products', '产品管理', json.dumps(products_fields)))
    for pid, name in [('tp1', '苹果手机'), ('tp2', '华为笔记本'), ('tp3', '小米耳机')]:
        p_data = {'产品编号': pid, '产品名称': name, '价格': 999}
        cur.execute(
            "INSERT INTO dynamic_data (id, collection, data, branch_id, search_text) VALUES (%s,%s,%s,'main',%s)",
            (pid, '_t_products', json.dumps(p_data), compute_search_text(p_data, products_fields)))

    # 订单表：有 reference 字段和 quoteSelect 字段，displayField 都是 '产品名称'
    # 注意：第一个 text 字段是 '产品编号'，但 displayField 指定为 '产品名称'
    orders_fields = [
        {'fieldName': '订单名', 'label': '订单名', 'controlType': 'text'},
        {'fieldName': '引用产品', 'label': '引用产品', 'controlType': 'reference',
         'referenceConfig': {'targetCollection': '_t_products', 'displayField': '产品名称', 'inheritFields': []}},
        {'fieldName': '批量引用', 'label': '批量引用', 'controlType': 'quoteSelect',
         'quoteConfig': {'targetCollection': '_t_products', 'displayField': '产品名称'}},
    ]
    cur.execute("INSERT INTO page_configs (id, name, fields) VALUES (%s,%s,%s)",
                ('page-_t_orders', '订单管理', json.dumps(orders_fields)))
    # 订单A: reference=tp1(苹果手机), quoteSelect=[tp2,tp3]
    o1_data = {'订单名': '订单A', '引用产品': 'tp1', '批量引用': ['tp2', 'tp3']}
    cur.execute(
        "INSERT INTO dynamic_data (id, collection, data, branch_id, search_text) VALUES (%s,%s,%s,'main',%s)",
        ('to1', '_t_orders', json.dumps(o1_data), compute_search_text(o1_data, orders_fields)))
    # 订单B: reference=tp2(华为笔记本), quoteSelect=[]
    o2_data = {'订单名': '订单B', '引用产品': 'tp2', '批量引用': []}
    cur.execute(
        "INSERT INTO dynamic_data (id, collection, data, branch_id, search_text) VALUES (%s,%s,%s,'main',%s)",
        ('to2', '_t_orders', json.dumps(o2_data), compute_search_text(o2_data, orders_fields)))
    conn.commit()

    yield conn

    # 清理
    for tbl, where in [('dynamic_data', "collection IN ('_t_products','_t_orders')"),
                       ('page_configs', "id IN ('page-_t_products','page-_t_orders')"),
                       ('data_relations', "collection = '_t_orders'")]:
        cur.execute(f"DELETE FROM {tbl} WHERE {where}")
    conn.commit()
    conn.close()


def _search(cur, keyword):
    """调用 build_keyword_conditions 并返回匹配的 ID 集合。"""
    from routes.dynamic import build_keyword_conditions
    cur.execute("SELECT fields FROM page_configs WHERE id = 'page-_t_orders'")
    fields = cur.fetchone()[0]
    conditions, params, matching_ids = build_keyword_conditions(
        cur, '_t_orders', keyword, fields, 'main'
    )
    # 加上条件本身的 ID 匹配
    all_ids = set(matching_ids)
    # 直接字段匹配：运行查询
    if conditions:
        where = ' OR '.join(conditions)
        cur.execute(f"SELECT id FROM dynamic_data WHERE collection = '_t_orders' AND branch_id = 'main' AND ({where})",
                    params)
        all_ids.update(row[0] for row in cur.fetchall())
    return all_ids


class TestReferenceSearch:
    """reference 字段：通过引用记录的 displayField 搜索。"""

    def test_finds_by_referenced_display_field(self, search_db):
        """搜 '苹果手机' → 找到 to1（引用产品=tp1，产品名称=苹果手机）"""
        cur = search_db.cursor()
        ids = _search(cur, '苹果手机')
        assert 'to1' in ids

    def test_finds_second_reference(self, search_db):
        """搜 '华为笔记本' → 找到 to2（引用产品=tp2）"""
        cur = search_db.cursor()
        ids = _search(cur, '华为笔记本')
        assert 'to2' in ids

    def test_uses_configured_display_field_not_first_text_field(self, search_db):
        """
        验证：displayField='产品名称' 而非自动取第一个 text 字段 '产品编号'。
        产品编号 是 'tp1'（与名称不同），搜索'苹果手机'必须通过'产品名称'字段找到。
        """
        cur = search_db.cursor()
        ids = _search(cur, '苹果手机')
        assert 'to1' in ids, '应通过 displayField=产品名称 而非第一个 text 字段找到'


class TestQuoteSelectSearch:
    """quoteSelect 字段：通过 JSONB 数组中的引用记录搜索。"""

    def test_finds_by_quoted_display_field(self, search_db):
        """搜 '小米耳机' → 找到 to1（批量引用包含 tp3，产品名称=小米耳机）"""
        cur = search_db.cursor()
        ids = _search(cur, '小米耳机')
        assert 'to1' in ids

    def test_finds_by_another_quoted_record(self, search_db):
        """搜 '华为笔记本' → 找到 to1（批量引用包含 tp2）和 to2（引用产品=tp2）"""
        cur = search_db.cursor()
        ids = _search(cur, '华为笔记本')
        assert 'to1' in ids  # via quoteSelect
        assert 'to2' in ids  # via reference

    def test_empty_quote_select_finds_nothing(self, search_db):
        """to2 的批量引用为空，搜'小米耳机'不应找到 to2"""
        cur = search_db.cursor()
        ids = _search(cur, '小米耳机')
        assert 'to2' not in ids


class TestDirectFieldSearchStillWorks:
    """直接字段搜索不受影响。"""

    def test_direct_text_search(self, search_db):
        """搜 '订单A' → 找到 to1"""
        cur = search_db.cursor()
        ids = _search(cur, '订单A')
        assert ids == {'to1'}

    def test_id_exact_match(self, search_db):
        """搜记录 ID → 精确匹配"""
        cur = search_db.cursor()
        ids = _search(cur, 'to2')
        assert ids == {'to2'}
