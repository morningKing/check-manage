"""
测试页面配置关联关系扫描工具
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2.extras
import pytest
from unittest.mock import patch
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

        # 先清理可能存在的旧数据
        cur.execute('DELETE FROM page_configs WHERE id IN (%s, %s)',
            ('page-test-a', 'page-test-b'))
        conn.commit()

        # Create page A with relation to page B
        cur.execute(
            'INSERT INTO page_configs (id, name, fields) VALUES (%s, %s, %s)',
            ('page-test-a', '页面A', psycopg2.extras.Json([
                {
                    'fieldName': 'relatedB',
                    'label': '关联B',
                    'controlType': 'relation',
                    'relationConfig': {
                        'targetCollection': 'test-b',
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

        # 先清理可能存在的旧数据
        cur.execute('DELETE FROM page_configs WHERE id IN (%s, %s)',
            ('page-test-ref-a', 'page-test-ref-b'))
        conn.commit()

        # Create page A with reference to page B
        cur.execute(
            'INSERT INTO page_configs (id, name, fields) VALUES (%s, %s, %s)',
            ('page-test-ref-a', '页面A', psycopg2.extras.Json([
                {
                    'fieldName': 'parentB',
                    'label': '父页面B',
                    'controlType': 'reference',
                    'referenceConfig': {
                        'targetCollection': 'test-ref-b',
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

        # 先清理可能存在的旧数据
        cur.execute('DELETE FROM page_configs WHERE id IN (%s, %s)',
            ('page-test-quote-a', 'page-test-quote-b'))
        conn.commit()

        cur.execute(
            'INSERT INTO page_configs (id, name, fields) VALUES (%s, %s, %s)',
            ('page-test-quote-a', '页面A', psycopg2.extras.Json([
                {
                    'fieldName': 'quotedB',
                    'label': '引用B',
                    'controlType': 'quoteSelect',
                    'quoteConfig': {
                        'targetCollection': 'test-quote-b',
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


def test_circular_reference():
    """测试循环引用 A → B → A"""
    with get_db() as conn:
        cur = conn.cursor()

        # 先清理可能存在的旧数据
        cur.execute('DELETE FROM page_configs WHERE id IN (%s, %s)',
            ('page-test-circ-a', 'page-test-circ-b'))
        conn.commit()

        # A relates to B
        cur.execute(
            'INSERT INTO page_configs (id, name, fields) VALUES (%s, %s, %s)',
            ('page-test-circ-a', '页面A', psycopg2.extras.Json([
                {
                    'fieldName': 'relatedB',
                    'label': '关联B',
                    'controlType': 'relation',
                    'relationConfig': {
                        'targetCollection': 'test-circ-b',
                        'displayField': 'name',
                        'targetField': 'relatedA'
                    }
                }
            ]))
        )

        # B relates to A (circular)
        cur.execute(
            'INSERT INTO page_configs (id, name, fields) VALUES (%s, %s, %s)',
            ('page-test-circ-b', '页面B', psycopg2.extras.Json([
                {
                    'fieldName': 'relatedA',
                    'label': '关联A',
                    'controlType': 'relation',
                    'relationConfig': {
                        'targetCollection': 'test-circ-a',
                        'displayField': 'name',
                        'targetField': 'relatedB'
                    }
                }
            ]))
        )
        conn.commit()

    # Test
    result = get_page_config_relations('page-test-circ-a')

    # Should not infinite loop
    # Should have exactly 2 nodes (not duplicated)
    assert len(result['nodes']) == 2

    # Should have 2 edges (A→B and B→A)
    assert len(result['edges']) == 2

    # Cleanup
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM page_configs WHERE id IN (%s, %s)',
            ('page-test-circ-a', 'page-test-circ-b'))
        conn.commit()


@pytest.fixture
def relations_route_setup(mock_conn, mock_cursor):
    """Setup for testing actual relations route"""
    from contextlib import contextmanager
    from auth import create_token

    @contextmanager
    def fake_get_db():
        yield mock_conn

    patches = [
        patch('db.get_db', fake_get_db),
        patch('routes.page_configs.get_db', fake_get_db),
        patch('utils.page_config_relations.get_db', fake_get_db),
    ]

    for p in patches:
        p.start()

    from app import app
    app.config['TESTING'] = True

    # Create admin token for authentication
    admin_token = create_token({'id': 'u1', 'username': 'admin', 'role': 'admin'})

    yield (
        app.test_client(),
        mock_cursor,
        {'Authorization': f'Bearer {admin_token}'}
    )

    for p in patches:
        p.stop()


def test_relations_route_parameter_validation(relations_route_setup):
    """测试relations API路由参数验证（测试实际路由）"""
    client, mock_cursor, headers = relations_route_setup

    # Mock empty result for non-existent page
    mock_cursor.fetchall.return_value = []

    # Test 1: Invalid depth string (returns 400)
    response = client.get('/pageConfigs/page-test-nonexistent/relations?depth=abc', headers=headers)
    assert response.status_code == 400
    data = response.get_json()
    assert 'depth参数必须是整数' in data['error']

    # Test 2: Out-of-bounds depth (> 10)
    response = client.get('/pageConfigs/page-test-nonexistent/relations?depth=20', headers=headers)
    assert response.status_code == 400
    data = response.get_json()
    assert 'depth参数必须在1-10之间' in data['error']

    # Test 3: Zero depth (out of bounds)
    response = client.get('/pageConfigs/page-test-nonexistent/relations?depth=0', headers=headers)
    assert response.status_code == 400
    data = response.get_json()
    assert 'depth参数必须在1-10之间' in data['error']

    # Test 4: Negative depth (out of bounds)
    response = client.get('/pageConfigs/page-test-nonexistent/relations?depth=-5', headers=headers)
    assert response.status_code == 400
    data = response.get_json()
    assert 'depth参数必须在1-10之间' in data['error']


def test_relations_route_requires_authentication(relations_route_setup):
    """测试relations API路由需要认证"""
    client, mock_cursor, headers = relations_route_setup

    # Test without authentication headers - should return 401
    response = client.get('/pageConfigs/page-test/relations?depth=3')
    assert response.status_code == 401