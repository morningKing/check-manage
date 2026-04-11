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

    collection_a = 'inspection-case'
    collection_b = 'inspection-plan'
    test_user_id = 'test-user-cross-switch'
    test_username = 'test_cross_switch'

    # 1. 创建版本并添加跨Collection数据
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

    # 2. 在版本分支中添加跨Collection数据(触发自动追踪)
    with get_db() as conn:
        cur = conn.cursor()

        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('test-switch-case-001', collection_a,
             psycopg2.extras.Json({'caseName': '测试用例'}), version_id, 1)
        )

        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('test-switch-plan-001', collection_b,
             psycopg2.extras.Json({'planName': '测试计划'}), version_id, 1)
        )

        cur.execute(
            'INSERT INTO data_relations '
            '(collection, record_id, field_name, related_collection, related_id, branch_id) '
            'VALUES (%s, %s, %s, %s, %s, %s)',
            (collection_a, 'test-switch-case-001', 'relatedPlan',
             collection_b, 'test-switch-plan-001', version_id)
        )

        conn.commit()

    # 3. 切换到版本
    result = switch_to_version(version_id, test_username, test_user_id)

    # 4. 验证返回结果
    assert 'affectedCollections' in result
    assert len(result['affectedCollections']) >= 2
    assert collection_a in result['affectedCollections']
    assert collection_b in result['affectedCollections']

    # 5. 验证user_current_branch更新(关键验证)
    branch_a = get_user_current_branch(test_user_id, collection_a)
    branch_b = get_user_current_branch(test_user_id, collection_b)

    assert branch_a == version_id, f'{collection_a} 应切换到 {version_id}'
    assert branch_b == version_id, f'{collection_b} 应切换到 {version_id}(关键验证)'

    # 6. 清理
    delete_version(version_id, confirmed=True)

    print('[OK] 跨Collection分支切换测试通过')


def test_switch_fallback_without_tracking():
    """测试追踪数据缺失时的降级逻辑"""

    collection = 'inspection-case'
    test_user_id = 'test-user-fallback'
    test_username = 'test_fallback'

    # 1. 创建版本(自动追踪)
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

    # 2. 删除追踪数据(模拟缺失情况)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'DELETE FROM version_collections WHERE version_id = %s',
            (version_id,)
        )
        conn.commit()

    # 3. 切换到版本(应降级到使用metadata collection)
    result = switch_to_version(version_id, test_username, test_user_id)

    # 4. 验证:降级逻辑生效
    assert result['affectedCollections'] == [collection]

    branch = get_user_current_branch(test_user_id, collection)
    assert branch == version_id, '降级逻辑应确保切换成功'

    # 5. 清理
    delete_version(version_id, confirmed=True)

    print('[OK] 追踪数据缺失降级测试通过')


if __name__ == '__main__':
    test_switch_to_version_cross_collection()
    test_switch_fallback_without_tracking()
    print('\n所有测试通过!')