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


def test_switch_to_version_multi_collection():
    """测试切换版本时同步更新所有参与的collections"""
    collection_a = 'test-switch-coll-a'
    collection_b = 'test-switch-coll-b'
    test_user_id = 'test-switch-user-id'
    test_username = 'test-switch-user'

    # 1. 清理测试数据
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM dynamic_data WHERE collection IN (%s, %s)', (collection_a, collection_b))
        cur.execute('DELETE FROM data_relations WHERE collection IN (%s, %s) OR related_collection IN (%s, %s)',
                   (collection_a, collection_b, collection_a, collection_b))
        cur.execute('DELETE FROM user_current_branch WHERE user_id = %s', (test_user_id,))
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
            ('record-switch-a', collection_a,
             psycopg2.extras.Json({'name': 'Record A'}), 'main', 1)
        )
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('record-switch-b', collection_b,
             psycopg2.extras.Json({'name': 'Record B'}), 'main', 1)
        )
        cur.execute(
            'INSERT INTO data_relations '
            '(collection, record_id, field_name, related_collection, related_id, branch_id) '
            'VALUES (%s, %s, %s, %s, %s, %s)',
            (collection_a, 'record-switch-a', 'relatedField',
             collection_b, 'record-switch-b', 'main')
        )
        conn.commit()

    # 3. 创建跨collection版本
    from utils.version import create_version_snapshot
    version_info = create_version_snapshot(
        collection=collection_a,
        name='测试切换版本',
        description='测试switch_to_version多collection支持',
        version_type='branch',
        parent_version=None,
        created_by=test_username,
        branch_id='main'
    )
    version_id = version_info['id']

    # 4. 切换到该版本
    from utils.version import switch_to_version
    result = switch_to_version(
        version_id=version_id,
        switched_by=test_username,
        user_id=test_user_id
    )

    # 5. 验证返回结果
    assert result['success'] is True
    assert result['branchId'] == version_id
    assert 'affectedCollections' in result
    assert collection_a in result['affectedCollections']
    assert collection_b in result['affectedCollections']

    # 6. 验证所有collection的用户当前分支都已更新
    with get_db() as conn:
        cur = conn.cursor()
        for coll in [collection_a, collection_b]:
            cur.execute(
                'SELECT branch_id FROM user_current_branch WHERE user_id = %s AND collection = %s',
                (test_user_id, coll)
            )
            row = cur.fetchone()
            assert row is not None, f'Collection {coll} 的用户分支设置不存在'
            assert row[0] == version_id, f'Collection {coll} 的分支ID不正确'

    # 7. 验证分支数据已初始化（首次切换）
    assert result['initialized'] is True
    with get_db() as conn:
        cur = conn.cursor()
        for coll in [collection_a, collection_b]:
            cur.execute(
                'SELECT COUNT(*) FROM dynamic_data WHERE collection = %s AND branch_id = %s',
                (coll, version_id)
            )
            count = cur.fetchone()[0]
            assert count >= 1, f'Collection {coll} 的分支数据未初始化'

    # 8. 清理测试数据
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM dynamic_data WHERE collection IN (%s, %s)', (collection_a, collection_b))
        cur.execute('DELETE FROM data_relations WHERE collection IN (%s, %s) OR related_collection IN (%s, %s)',
                   (collection_a, collection_b, collection_a, collection_b))
        cur.execute('DELETE FROM user_current_branch WHERE user_id = %s', (test_user_id,))
        cur.execute('DELETE FROM version_snapshots WHERE version_id = %s', (version_id,))
        cur.execute('DELETE FROM version_relations WHERE version_id = %s', (version_id,))
        cur.execute('DELETE FROM version_collections WHERE version_id = %s', (version_id,))
        cur.execute('DELETE FROM collection_versions WHERE id = %s', (version_id,))
        conn.commit()


def test_delete_version_cascading():
    """测试删除版本时级联删除所有collection的分支数据"""
    collection_a = 'test-delete-coll-a'
    collection_b = 'test-delete-coll-b'
    test_user_id = 'test-delete-user-id'
    test_username = 'test-delete-user'

    # 1. 清理测试数据
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM dynamic_data WHERE collection IN (%s, %s)', (collection_a, collection_b))
        cur.execute('DELETE FROM data_relations WHERE collection IN (%s, %s) OR related_collection IN (%s, %s)',
                   (collection_a, collection_b, collection_a, collection_b))
        cur.execute('DELETE FROM user_current_branch WHERE user_id = %s', (test_user_id,))
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
            ('record-delete-a', collection_a,
             psycopg2.extras.Json({'name': 'Record A'}), 'main', 1)
        )
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('record-delete-b', collection_b,
             psycopg2.extras.Json({'name': 'Record B'}), 'main', 1)
        )
        cur.execute(
            'INSERT INTO data_relations '
            '(collection, record_id, field_name, related_collection, related_id, branch_id) '
            'VALUES (%s, %s, %s, %s, %s, %s)',
            (collection_a, 'record-delete-a', 'relatedField',
             collection_b, 'record-delete-b', 'main')
        )
        conn.commit()

    # 3. 创建跨collection版本
    from utils.version import create_version_snapshot
    version_info = create_version_snapshot(
        collection=collection_a,
        name='测试删除版本',
        description='测试delete_version多collection支持',
        version_type='branch',
        parent_version=None,
        created_by=test_username,
        branch_id='main'
    )
    version_id = version_info['id']

    # 4. 切换到该版本，创建分支数据
    from utils.version import switch_to_version
    result = switch_to_version(
        version_id=version_id,
        switched_by=test_username,
        user_id=test_user_id
    )
    assert result['success'] is True

    # 5. 验证分支数据已创建
    with get_db() as conn:
        cur = conn.cursor()
        for coll in [collection_a, collection_b]:
            cur.execute(
                'SELECT COUNT(*) FROM dynamic_data WHERE collection = %s AND branch_id = %s',
                (coll, version_id)
            )
            count = cur.fetchone()[0]
            assert count >= 1, f'Collection {coll} 的分支数据应已创建'

    # 6. 测试未确认删除时返回影响报告
    from utils.version import delete_version
    impact_report = delete_version(version_id, confirmed=False)

    assert 'versionInfo' in impact_report
    assert 'affectedCollections' in impact_report
    assert len(impact_report['affectedCollections']) >= 2
    assert collection_a in [item['collection'] for item in impact_report['affectedCollections']]
    assert collection_b in [item['collection'] for item in impact_report['affectedCollections']]
    assert 'hasCrossCollectionData' in impact_report
    assert impact_report['hasCrossCollectionData'] is True

    # 清除用户分支设置，避免用户检查阻止删除
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'DELETE FROM user_current_branch WHERE branch_id = %s',
            (version_id,)
        )
        conn.commit()

    # 7. 测试确认删除
    delete_result = delete_version(version_id, confirmed=True)
    assert delete_result is True

    # 8. 验证所有collection的分支数据已删除
    with get_db() as conn:
        cur = conn.cursor()
        for coll in [collection_a, collection_b]:
            # 验证dynamic_data已删除
            cur.execute(
                'SELECT COUNT(*) FROM dynamic_data WHERE collection = %s AND branch_id = %s',
                (coll, version_id)
            )
            count = cur.fetchone()[0]
            assert count == 0, f'Collection {coll} 的分支数据应已删除'

            # 验证user_current_branch已删除
            cur.execute(
                'SELECT COUNT(*) FROM user_current_branch WHERE collection = %s AND branch_id = %s',
                (coll, version_id)
            )
            count = cur.fetchone()[0]
            assert count == 0, f'Collection {coll} 的用户分支设置应已删除'

        # 验证data_relations已删除
        cur.execute(
            'SELECT COUNT(*) FROM data_relations WHERE branch_id = %s',
            (version_id,)
        )
        count = cur.fetchone()[0]
        assert count == 0, '分支的关联关系应已删除'

        # 验证version_snapshots已删除
        cur.execute(
            'SELECT COUNT(*) FROM version_snapshots WHERE version_id = %s',
            (version_id,)
        )
        count = cur.fetchone()[0]
        assert count == 0, '版本快照应已删除'

        # 验证version_relations已删除
        cur.execute(
            'SELECT COUNT(*) FROM version_relations WHERE version_id = %s',
            (version_id,)
        )
        count = cur.fetchone()[0]
        assert count == 0, '版本关联应已删除'

        # 验证version_collections已删除
        cur.execute(
            'SELECT COUNT(*) FROM version_collections WHERE version_id = %s',
            (version_id,)
        )
        count = cur.fetchone()[0]
        assert count == 0, '版本collection追踪应已删除'

        # 验证collection_versions已删除
        cur.execute(
            'SELECT COUNT(*) FROM collection_versions WHERE id = %s',
            (version_id,)
        )
        count = cur.fetchone()[0]
        assert count == 0, '版本记录应已删除'

    # 9. 清理主分支数据
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM dynamic_data WHERE collection IN (%s, %s)', (collection_a, collection_b))
        cur.execute('DELETE FROM data_relations WHERE collection IN (%s, %s) OR related_collection IN (%s, %s)',
                   (collection_a, collection_b, collection_a, collection_b))
        conn.commit()