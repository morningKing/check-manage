"""
测试页面配置关联关系扫描工具
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2.extras
from utils.page_config_relations import get_page_config_relations
from db import get_db


def test_single_page_no_relations():
    """测试无关联的单个页面"""
    # Setup: Create simple page config with no relation fields
    with get_db() as conn:
        cur = conn.cursor()

        # Create test page config
        cur.execute(
            'INSERT INTO page_configs (id, name, fields) VALUES (%s, %s, %s)',
            ('page-test-simple', '测试页面', psycopg2.extras.Json([
                {'fieldName': 'name', 'controlType': 'text'}
            ]))
        )
        conn.commit()

    # Test
    result = get_page_config_relations('page-test-simple')

    assert 'nodes' in result
    assert 'edges' in result
    assert len(result['nodes']) == 1
    assert result['nodes'][0]['id'] == 'page-test-simple'
    assert len(result['edges']) == 0

    # Cleanup
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM page_configs WHERE id = %s', ('page-test-simple',))
        conn.commit()


def test_two_pages_with_relation():
    """测试两个页面通过relation字段关联"""
    with get_db() as conn:
        cur = conn.cursor()

        # Create page A with relation to page B
        cur.execute(
            'INSERT INTO page_configs (id, name, fields) VALUES (%s, %s, %s)',
            ('page-test-a', '页面A', psycopg2.extras.Json([
                {
                    'fieldName': 'relatedB',
                    'label': '关联B',
                    'controlType': 'relation',
                    'relationConfig': {
                        'targetCollection': 'page-test-b',
                        'displayField': 'name',
                        'targetField': 'relatedA'
                    }
                }
            ]))
        )

        # Create page B
        cur.execute(
            'INSERT INTO page_configs (id, name, fields) VALUES (%s, %s, %s)',
            ('page-test-b', '页面B', psycopg2.extras.Json([
                {'fieldName': 'name', 'controlType': 'text'}
            ]))
        )
        conn.commit()

    # Test
    result = get_page_config_relations('page-test-a', max_depth=2)

    assert len(result['nodes']) == 2
    assert len(result['edges']) == 1

    edge = result['edges'][0]
    assert edge['source'] == 'page-test-a'
    assert edge['target'] == 'page-test-b'
    assert edge['type'] == 'relation'
    assert edge['field'] == 'relatedB'
    assert edge['label'] == '关联B'

    # Cleanup
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM page_configs WHERE id IN (%s, %s)',
            ('page-test-a', 'page-test-b'))
        conn.commit()


def test_two_pages_with_reference():
    """测试两个页面通过reference字段关联"""
    with get_db() as conn:
        cur = conn.cursor()

        # Create page A with reference to page B
        cur.execute(
            'INSERT INTO page_configs (id, name, fields) VALUES (%s, %s, %s)',
            ('page-test-ref-a', '页面A', psycopg2.extras.Json([
                {
                    'fieldName': 'parentB',
                    'label': '父页面B',
                    'controlType': 'reference',
                    'referenceConfig': {
                        'targetCollection': 'page-test-ref-b',
                        'displayField': 'name',
                        'inheritFields': ['field1', 'field2']
                    }
                }
            ]))
        )

        cur.execute(
            'INSERT INTO page_configs (id, name, fields) VALUES (%s, %s, %s)',
            ('page-test-ref-b', '页面B', psycopg2.extras.Json([
                {'fieldName': 'name', 'controlType': 'text'}
            ]))
        )
        conn.commit()

    result = get_page_config_relations('page-test-ref-a', max_depth=2)

    assert len(result['nodes']) == 2
    assert len(result['edges']) == 1
    assert result['edges'][0]['type'] == 'reference'
    assert result['edges'][0]['field'] == 'parentB'

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM page_configs WHERE id IN (%s, %s)',
            ('page-test-ref-a', 'page-test-ref-b'))
        conn.commit()


def test_two_pages_with_quote_select():
    """测试两个页面通过quoteSelect字段关联"""
    with get_db() as conn:
        cur = conn.cursor()

        cur.execute(
            'INSERT INTO page_configs (id, name, fields) VALUES (%s, %s, %s)',
            ('page-test-quote-a', '页面A', psycopg2.extras.Json([
                {
                    'fieldName': 'quotedB',
                    'label': '引用B',
                    'controlType': 'quoteSelect',
                    'quoteConfig': {
                        'targetCollection': 'page-test-quote-b',
                        'displayField': 'name'
                    }
                }
            ]))
        )

        cur.execute(
            'INSERT INTO page_configs (id, name, fields) VALUES (%s, %s, %s)',
            ('page-test-quote-b', '页面B', psycopg2.extras.Json([
                {'fieldName': 'name', 'controlType': 'text'}
            ]))
        )
        conn.commit()

    result = get_page_config_relations('page-test-quote-a', max_depth=2)

    assert len(result['nodes']) == 2
    assert len(result['edges']) == 1
    assert result['edges'][0]['type'] == 'quoteSelect'
    assert result['edges'][0]['field'] == 'quotedB'

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM page_configs WHERE id IN (%s, %s)',
            ('page-test-quote-a', 'page-test-quote-b'))
        conn.commit()