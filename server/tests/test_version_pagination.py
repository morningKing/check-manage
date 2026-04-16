"""
测试版本列表分页功能
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2.extras
from db import get_db
from utils.version import get_version_list, create_version_snapshot


def test_get_version_list_with_pagination():
    """测试 get_version_list 支持分页参数"""
    collection = 'test-pagination-coll'
    test_user = 'test_user'

    # Setup: Create multiple versions
    with get_db() as conn:
        cur = conn.cursor()
        # Delete in correct order due to foreign key constraints
        cur.execute('DELETE FROM version_snapshots WHERE version_id IN (SELECT id FROM collection_versions WHERE collection = %s)', (collection,))
        cur.execute('DELETE FROM version_relations WHERE version_id IN (SELECT id FROM collection_versions WHERE collection = %s)', (collection,))
        cur.execute('DELETE FROM version_collections WHERE version_id IN (SELECT id FROM collection_versions WHERE collection = %s)', (collection,))
        cur.execute('DELETE FROM collection_versions WHERE collection = %s', (collection,))
        cur.execute('DELETE FROM dynamic_data WHERE collection = %s', (collection,))
        conn.commit()

        # Create test data
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s, %s, %s, %s)',
            ('test-pag-001', collection, psycopg2.extras.Json({'name': 'Test'}), 'main')
        )
        conn.commit()

    # Create 15 versions
    version_ids = []
    for i in range(15):
        result = create_version_snapshot(
            collection=collection,
            name=f'v{i:02d}-test',
            description=f'Version {i}',
            version_type='snapshot',
            parent_version=None,
            created_by=test_user,
            branch_id='main'
        )
        version_ids.append(result['id'])

    # Test pagination
    page1 = get_version_list(collection=collection, page=1, pageSize=5)
    assert len(page1['items']) == 5
    assert page1['total'] == 15

    page2 = get_version_list(collection=collection, page=2, pageSize=5)
    assert len(page2['items']) == 5
    assert page2['total'] == 15

    # Verify ordering (DESC by created_at)
    assert page1['items'][0]['name'] == 'v14-test'

    # Cleanup
    with get_db() as conn:
        cur = conn.cursor()
        for vid in version_ids:
            cur.execute('DELETE FROM version_snapshots WHERE version_id = %s', (vid,))
            cur.execute('DELETE FROM version_relations WHERE version_id = %s', (vid,))
            cur.execute('DELETE FROM version_collections WHERE version_id = %s', (vid,))
        cur.execute('DELETE FROM collection_versions WHERE collection = %s', (collection,))
        cur.execute('DELETE FROM dynamic_data WHERE collection = %s', (collection,))
        conn.commit()


def test_get_version_list_with_keyword_search():
    """测试 get_version_list 支持关键词搜索"""
    collection = 'test-search-coll'
    test_user = 'test_user'

    # Setup
    with get_db() as conn:
        cur = conn.cursor()
        # Delete in correct order due to foreign key constraints
        cur.execute('DELETE FROM version_snapshots WHERE version_id IN (SELECT id FROM collection_versions WHERE collection = %s)', (collection,))
        cur.execute('DELETE FROM version_relations WHERE version_id IN (SELECT id FROM collection_versions WHERE collection = %s)', (collection,))
        cur.execute('DELETE FROM version_collections WHERE version_id IN (SELECT id FROM collection_versions WHERE collection = %s)', (collection,))
        cur.execute('DELETE FROM collection_versions WHERE collection = %s', (collection,))
        cur.execute('DELETE FROM dynamic_data WHERE collection = %s', (collection,))
        conn.commit()

        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s, %s, %s, %s)',
            ('test-search-001', collection, psycopg2.extras.Json({'name': 'Test'}), 'main')
        )
        conn.commit()

    # Create versions with different names
    create_version_snapshot(collection, 'release-v1.0', 'Release version', 'snapshot', None, test_user, 'main')
    create_version_snapshot(collection, 'feature-abc', 'Feature branch', 'branch', None, test_user, 'main')
    create_version_snapshot(collection, 'hotfix-123', 'Hotfix', 'snapshot', None, test_user, 'main')

    # Test search with pagination (returns dict with items/total)
    result = get_version_list(collection=collection, keyword='release', page=1, pageSize=10)
    assert len(result['items']) == 1
    assert result['items'][0]['name'] == 'release-v1.0'

    result2 = get_version_list(collection=collection, keyword='v', page=1, pageSize=10)
    assert result2['total'] >= 1

    # Test search without pagination (returns list, backward compatible)
    result3 = get_version_list(collection=collection, keyword='hotfix')
    assert isinstance(result3, list)
    assert len(result3) == 1
    assert result3[0]['name'] == 'hotfix-123'

    # Cleanup
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM version_snapshots WHERE version_id IN (SELECT id FROM collection_versions WHERE collection = %s)', (collection,))
        cur.execute('DELETE FROM version_relations WHERE version_id IN (SELECT id FROM collection_versions WHERE collection = %s)', (collection,))
        cur.execute('DELETE FROM version_collections WHERE version_id IN (SELECT id FROM collection_versions WHERE collection = %s)', (collection,))
        cur.execute('DELETE FROM collection_versions WHERE collection = %s', (collection,))
        cur.execute('DELETE FROM dynamic_data WHERE collection = %s', (collection,))
        conn.commit()


if __name__ == '__main__':
    test_get_version_list_with_pagination()
    test_get_version_list_with_keyword_search()
    print('All pagination tests passed!')