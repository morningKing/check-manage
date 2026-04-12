"""
测试跨Collection分支切换
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2.extras
from db import get_db
from utils.version import (
    create_version_snapshot,
    delete_version,
    switch_to_version,
    get_user_current_branch
)

def test_switch_to_version_cross_collection():
    """测试切换到跨Collection版本"""

    collection_a = 'test-switch-cross-a'
    collection_b = 'test-switch-cross-b'
    test_user_id = 'test-user-cross-switch'
    test_username = 'test_cross_switch'

    # 1. 清理测试数据
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM dynamic_data WHERE collection IN (%s, %s)', (collection_a, collection_b))
        cur.execute('DELETE FROM data_relations WHERE collection IN (%s, %s) OR related_collection IN (%s, %s)',
                   (collection_a, collection_b, collection_a, collection_b))
        cur.execute('DELETE FROM version_snapshots WHERE version_id LIKE %s', ('ver-test-switch%',))
        cur.execute('DELETE FROM version_relations WHERE version_id LIKE %s', ('ver-test-switch%',))
        cur.execute('DELETE FROM version_collections WHERE version_id LIKE %s', ('ver-test-switch%',))
        cur.execute('DELETE FROM user_current_branch WHERE user_id = %s', (test_user_id,))
        cur.execute('DELETE FROM collection_versions WHERE collection IN (%s, %s)', (collection_a, collection_b))
        conn.commit()

    # 2. 在main分支添加数据(用于创建版本快照)
    with get_db() as conn:
        cur = conn.cursor()

        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('test-switch-case-main', collection_a,
             psycopg2.extras.Json({'caseName': '测试用例-Main'}), 'main', 1)
        )

        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('test-switch-plan-main', collection_b,
             psycopg2.extras.Json({'planName': '测试计划-Main'}), 'main', 1)
        )

        cur.execute(
            'INSERT INTO data_relations '
            '(collection, record_id, field_name, related_collection, related_id, branch_id) '
            'VALUES (%s, %s, %s, %s, %s, %s)',
            (collection_a, 'test-switch-case-main', 'relatedPlan',
             collection_b, 'test-switch-plan-main', 'main')
        )

        conn.commit()

    # 3. 创建版本并添加跨Collection数据
    version_info = create_version_snapshot(
        collection=collection_a,
        name='跨Collection切换测试',
        description='测试',
        version_type='branch',
        parent_version=None,
        created_by=test_username,
        branch_id='main'
    )
    version_id = version_info['id']

    # 4. 删除分支数据(模拟未初始化状态)
    # 这会测试switch_to_version的初始化逻辑
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM dynamic_data WHERE branch_id = %s', (version_id,))
        cur.execute('DELETE FROM data_relations WHERE branch_id = %s', (version_id,))
        conn.commit()

    # 5. 切换到版本(应从快照初始化分支数据)
    result = switch_to_version(version_id, test_username, test_user_id)

    # 6. 验证返回结果
    assert 'affectedCollections' in result
    assert len(result['affectedCollections']) >= 2
    assert collection_a in result['affectedCollections']
    assert collection_b in result['affectedCollections']

    # 7. 验证user_current_branch更新
    branch_a = get_user_current_branch(test_user_id, collection_a)
    branch_b = get_user_current_branch(test_user_id, collection_b)

    assert branch_a == version_id, f'{collection_a} 应切换到 {version_id}'
    assert branch_b == version_id, f'{collection_b} 应切换到 {version_id}'

    # 8. 验证switch_to_version从快照初始化了分支数据(关键验证:bug修复)
    with get_db() as conn:
        cur = conn.cursor()

        # 检查Collection A分支数据
        cur.execute('SELECT COUNT(*) FROM dynamic_data WHERE collection = %s AND branch_id = %s',
            (collection_a, version_id))
        count_a = cur.fetchone()[0]
        assert count_a > 0, 'Collection A应该有从快照初始化的分支数据'

        # 检查Collection B分支数据(关键验证:跨Collection初始化)
        cur.execute('SELECT COUNT(*) FROM dynamic_data WHERE collection = %s AND branch_id = %s',
            (collection_b, version_id))
        count_b = cur.fetchone()[0]
        assert count_b > 0, 'Collection B应该有从快照初始化的分支数据(跨Collection bug修复验证)'

    # 7. 清理
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM dynamic_data WHERE collection IN (%s, %s)', (collection_a, collection_b))
        cur.execute('DELETE FROM data_relations WHERE collection IN (%s, %s)', (collection_a, collection_b))
        cur.execute('DELETE FROM user_current_branch WHERE user_id = %s', (test_user_id,))
        cur.execute('DELETE FROM version_collections WHERE version_id = %s', (version_id,))
        cur.execute('DELETE FROM collection_versions WHERE id = %s', (version_id,))
        conn.commit()

    delete_version(version_id, confirmed=True)

    print('[OK] 跨Collection分支切换测试通过')


def test_switch_fallback_without_tracking():
    """测试追踪数据缺失时的降级逻辑"""

    collection = 'test-switch-fallback'
    test_user_id = 'test-user-fallback'
    test_username = 'test_fallback'

    # 1. 清理测试数据
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM dynamic_data WHERE collection = %s', (collection,))
        cur.execute('DELETE FROM data_relations WHERE collection = %s OR related_collection = %s',
                   (collection, collection))
        cur.execute('DELETE FROM version_snapshots WHERE version_id LIKE %s', ('ver-test-fallback%',))
        cur.execute('DELETE FROM version_relations WHERE version_id LIKE %s', ('ver-test-fallback%',))
        cur.execute('DELETE FROM version_collections WHERE version_id LIKE %s', ('ver-test-fallback%',))
        cur.execute('DELETE FROM user_current_branch WHERE user_id = %s', (test_user_id,))
        cur.execute('DELETE FROM collection_versions WHERE collection = %s', (collection,))
        conn.commit()

    # 2. 在main分支添加数据
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('test-fallback-001', collection, psycopg2.extras.Json({'name': '测试数据'}), 'main', 1)
        )
        conn.commit()

    # 3. 创建版本(自动追踪)
    version_info = create_version_snapshot(
        collection=collection,
        name='追踪降级测试',
        description='测试',
        version_type='branch',
        parent_version=None,
        created_by=test_username,
        branch_id='main'
    )
    version_id = version_info['id']

    # 4. 删除追踪数据(模拟缺失情况)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'DELETE FROM version_collections WHERE version_id = %s',
            (version_id,)
        )
        conn.commit()

    # 5. 切换到版本(应降级到使用metadata collection)
    result = switch_to_version(version_id, test_username, test_user_id)

    # 6. 验证:降级逻辑生效
    assert result['affectedCollections'] == [collection]

    branch = get_user_current_branch(test_user_id, collection)
    assert branch == version_id, '降级逻辑应确保切换成功'

    # 7. 清理
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM dynamic_data WHERE collection = %s', (collection,))
        cur.execute('DELETE FROM user_current_branch WHERE user_id = %s', (test_user_id,))
        cur.execute('DELETE FROM version_collections WHERE version_id = %s', (version_id,))
        cur.execute('DELETE FROM collection_versions WHERE id = %s', (version_id,))
        conn.commit()

    delete_version(version_id, confirmed=True)

    print('[OK] 追踪数据缺失降级测试通过')


if __name__ == '__main__':
    test_switch_to_version_cross_collection()
    test_switch_fallback_without_tracking()
    print('\n所有测试通过!')