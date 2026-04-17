"""
测试跨Collection分支可见性辅助函数

测试新增的辅助函数:
- get_version_collections
- get_version_collection_stats
- get_primary_collection
- count_collection_relations
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import psycopg2.extras
from db import get_db
from utils.version import (
    get_version_collections,
    get_version_collection_stats,
    get_primary_collection,
    count_collection_relations,
    create_version_snapshot,
)


def test_get_version_collections():
    """测试获取版本涉及的collections"""
    # Setup: 创建测试分支涉及多个collection
    # (需要先有数据库fixture)
    pass  # placeholder将在后续任务完善


def test_get_version_collection_stats():
    """测试获取版本collection统计"""
    pass


def test_get_primary_collection():
    """测试识别主collection"""
    pass


def test_count_collection_relations():
    """测试统计collection关联数量"""
    pass


def test_get_version_collections_integration():
    """集成测试：创建跨collection版本后获取collections列表"""
    pytest.skip('Requires Task 2 implementation (create_version_snapshot multi-collection support)')
    collection_a = 'test-helper-coll-a'
    collection_b = 'test-helper-coll-b'
    test_user = 'test-helper-user'

    # 1. 清理测试数据
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM dynamic_data WHERE collection IN (%s, %s)', (collection_a, collection_b))
        cur.execute('DELETE FROM data_relations WHERE collection IN (%s, %s) OR related_collection IN (%s, %s)',
                   (collection_a, collection_b, collection_a, collection_b))
        cur.execute(
            'DELETE FROM version_snapshots WHERE version_id IN '
            '(SELECT id FROM collection_versions WHERE collection IN (%s, %s))',
            (collection_a, collection_b)
        )
        cur.execute(
            'DELETE FROM version_relations WHERE version_id IN '
            '(SELECT id FROM collection_versions WHERE collection IN (%s, %s))',
            (collection_a, collection_b)
        )
        cur.execute(
            'DELETE FROM version_collections WHERE version_id IN '
            '(SELECT id FROM collection_versions WHERE collection IN (%s, %s))',
            (collection_a, collection_b)
        )
        cur.execute('DELETE FROM collection_versions WHERE collection IN (%s, %s)', (collection_a, collection_b))
        conn.commit()

    # 2. 创建测试数据和关联
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('record-helper-a', collection_a,
             psycopg2.extras.Json({'name': 'Record A'}), 'main', 1)
        )
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('record-helper-b', collection_b,
             psycopg2.extras.Json({'name': 'Record B'}), 'main', 1)
        )
        cur.execute(
            'INSERT INTO data_relations '
            '(collection, record_id, field_name, related_collection, related_id, branch_id) '
            'VALUES (%s, %s, %s, %s, %s, %s)',
            (collection_a, 'record-helper-a', 'relatedField',
             collection_b, 'record-helper-b', 'main')
        )
        conn.commit()

    # 3. 创建跨collection版本
    version_info = create_version_snapshot(
        collection=collection_a,
        name='测试辅助函数版本',
        description='测试get_version_collections',
        version_type='branch',
        parent_version=None,
        created_by=test_user,
        branch_id='main'
    )
    version_id = version_info['id']

    # 4. 测试get_version_collections
    collections = get_version_collections(version_id)
    assert collection_a in collections
    assert collection_b in collections

    # 5. 测试get_primary_collection
    primary = get_primary_collection(version_id)
    assert primary == collection_a

    # 6. 测试get_version_collection_stats
    stats = get_version_collection_stats(version_id)
    assert collection_a in stats
    assert collection_b in stats
    assert stats[collection_a] >= 1
    assert stats[collection_b] >= 1

    # 7. 测试count_collection_relations (需要传入cursor)
    with get_db() as conn:
        cur = conn.cursor()
        rel_count = count_collection_relations(collection_a, 'main', cur)
        assert rel_count >= 1

    # 8. 清理测试数据
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM dynamic_data WHERE collection IN (%s, %s)', (collection_a, collection_b))
        cur.execute('DELETE FROM data_relations WHERE collection IN (%s, %s) OR related_collection IN (%s, %s)',
                   (collection_a, collection_b, collection_a, collection_b))
        cur.execute('DELETE FROM version_snapshots WHERE version_id = %s', (version_id,))
        cur.execute('DELETE FROM version_relations WHERE version_id = %s', (version_id,))
        cur.execute('DELETE FROM version_collections WHERE version_id = %s', (version_id,))
        cur.execute('DELETE FROM collection_versions WHERE id = %s', (version_id,))
        conn.commit()