"""
测试版本删除时的关联数据清理

验证场景：
1. 创建版本分支
2. 添加关联数据（同一 collection 内）
3. 删除版本
4. 验证正向和反向关联是否都被清理
"""

import sys
import os
# 添加 server 目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import psycopg2.extras
from utils.version import create_version_snapshot, delete_version, track_version_collections
from db import get_db


def test_version_delete_cleans_relations():
    """测试删除版本时是否清理正向和反向关联（同一 collection 内）"""

    # 准备测试数据
    collection = 'inspection-case'
    test_user = 'test_user'

    # 1. 创建版本分支
    version_info = create_version_snapshot(
        collection=collection,
        name='测试版本-关联清理验证',
        description='测试删除版本时是否清理正向和反向关联',
        version_type='branch',
        parent_version=None,
        created_by=test_user,
        branch_id='main'
    )
    version_id = version_info['id']

    print(f'\n创建版本: {version_id}')

    # 2. 在版本分支中添加测试数据
    with get_db() as conn:
        cur = conn.cursor()

        # 插入两条测试记录
        record1_id = 'test-record-1-for-version-delete'
        record2_id = 'test-record-2-for-version-delete'

        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) VALUES (%s, %s, %s, %s, %s)',
            (record1_id, collection, psycopg2.extras.Json({'caseName': '测试记录1'}), version_id, 1)
        )

        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) VALUES (%s, %s, %s, %s, %s)',
            (record2_id, collection, psycopg2.extras.Json({'caseName': '测试记录2'}), version_id, 1)
        )

        # 创建正向关联：record1 -> record2
        cur.execute(
            'INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id, branch_id) '
            'VALUES (%s, %s, %s, %s, %s, %s)',
            (collection, record1_id, 'relatedCase', collection, record2_id, version_id)
        )

        # 创建反向关联：record2 -> record1（这是关键的测试点）
        cur.execute(
            'INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id, branch_id) '
            'VALUES (%s, %s, %s, %s, %s, %s)',
            (collection, record2_id, 'relatedCaseReverse', collection, record1_id, version_id)
        )

        conn.commit()

    print('添加测试数据和关联关系')

    # 3. 追踪版本涉及的Collection（Task 2新增）
    track_version_collections(version_id, collection, version_id)

    # 4. 验证关联数据存在
    with get_db() as conn:
        cur = conn.cursor()

        # 检查正向关联
        cur.execute(
            'SELECT COUNT(*) FROM data_relations WHERE collection = %s AND record_id = %s AND branch_id = %s',
            (collection, record1_id, version_id)
        )
        forward_count = cur.fetchone()[0]
        assert forward_count == 1, f'正向关联应该有1条，实际有{forward_count}条'

        # 检查反向关联（这是关键！）
        cur.execute(
            'SELECT COUNT(*) FROM data_relations WHERE collection = %s AND record_id = %s AND branch_id = %s',
            (collection, record2_id, version_id)
        )
        reverse_count = cur.fetchone()[0]
        assert reverse_count == 1, f'反向关联应该有1条，实际有{reverse_count}条'

    print('[OK] 关联数据验证通过')

    # 5. 删除版本（使用 confirmed=True 立即删除）
    success = delete_version(version_id, confirmed=True)
    assert success is True, '删除版本应该成功'

    print(f'删除版本: {version_id}')

    # 6. 验证所有数据都被清理
    with get_db() as conn:
        cur = conn.cursor()

        # 检查 dynamic_data 是否被清理
        cur.execute(
            'SELECT COUNT(*) FROM dynamic_data WHERE branch_id = %s',
            (version_id,)
        )
        data_count = cur.fetchone()[0]
        assert data_count == 0, f'dynamic_data应该被清理，实际有{data_count}条'

        # 检查正向关联是否被清理
        cur.execute(
            'SELECT COUNT(*) FROM data_relations WHERE collection = %s AND record_id = %s AND branch_id = %s',
            (collection, record1_id, version_id)
        )
        forward_count = cur.fetchone()[0]
        assert forward_count == 0, f'正向关联应该被清理，实际有{forward_count}条'

        # 检查反向关联是否被清理（核心验证点！）
        cur.execute(
            'SELECT COUNT(*) FROM data_relations WHERE collection = %s AND record_id = %s AND branch_id = %s',
            (collection, record2_id, version_id)
        )
        reverse_count = cur.fetchone()[0]
        assert reverse_count == 0, f'反向关联应该被清理，实际有{reverse_count}条（悬空引用！）'

        # 全局检查：该版本的所有关联都应该被清理
        cur.execute(
            'SELECT COUNT(*) FROM data_relations WHERE branch_id = %s',
            (version_id,)
        )
        total_relations = cur.fetchone()[0]
        assert total_relations == 0, f'该版本的所有关联应该被清理，实际有{total_relations}条'

    print('[OK] 所有数据清理验证通过')
    print('\n[SUCCESS] 测试通过：删除版本时正确清理了正向和反向关联！')


if __name__ == '__main__':
    test_version_delete_cleans_relations()
    print('\n测试成功完成！')