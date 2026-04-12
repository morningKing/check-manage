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