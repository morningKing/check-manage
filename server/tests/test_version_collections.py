"""
验证 version_collections 表创建
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db


def test_version_collections_table_exists():
    """验证 version_collections 表存在"""
    with get_db() as conn:
        cur = conn.cursor()

        # 查询表是否存在
        cur.execute(
            "SELECT EXISTS ("
            "  SELECT FROM information_schema.tables "
            "  WHERE table_schema = 'public' "
            "  AND table_name = 'version_collections'"
            ")"
        )
        exists = cur.fetchone()[0]

        assert exists, 'version_collections 表应该存在'

        # 验证表结构
        cur.execute(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name = 'version_collections' "
            "ORDER BY ordinal_position"
        )
        columns = cur.fetchall()

        expected_columns = [
            ('version_id', 'character varying', 'NO'),
            ('collection', 'character varying', 'NO'),
            ('created_at', 'timestamp with time zone', 'YES'),
        ]

        assert len(columns) == 3, f'应该有3列，实际{len(columns)}列'
        for i, (col_name, col_type, nullable) in enumerate(expected_columns):
            assert columns[i][0] == col_name, f'列名应为{col_name}'
            assert columns[i][1] == col_type, f'列类型应为{col_type}'
            assert columns[i][2] == nullable, f'{col_name} nullable应为{nullable}'

    print('[OK] version_collections 表结构验证通过')


def test_track_single_collection():
    """测试单Collection版本的追踪"""
    from utils.version import create_version_snapshot, delete_version, track_version_collections
    import psycopg2.extras
    from datetime import datetime, timezone

    collection = 'inspection-case'
    test_user = 'test_user_track_single'

    # 1. 创建版本分支
    version_info = create_version_snapshot(
        collection=collection,
        name='单Collection追踪测试',
        description='测试单Collection追踪',
        version_type='branch',
        parent_version=None,
        created_by=test_user,
        branch_id='main'
    )
    version_id = version_info['id']

    # 2. 在版本分支中添加测试数据
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('test-track-001', collection, psycopg2.extras.Json({'caseName': '测试用例'}), version_id, 1)
        )
        conn.commit()

    # 3. 手动调用追踪（模拟创建版本后的追踪）
    track_version_collections(version_id, collection, version_id)

    # 4. 验证追踪结果
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT collection FROM version_collections WHERE version_id = %s ORDER BY collection',
            (version_id,)
        )
        tracked = [row[0] for row in cur.fetchall()]

        assert len(tracked) == 1, f'应追踪到1个Collection，实际{len(tracked)}'
        assert tracked[0] == collection, f'应为{collection}'

    # 5. 清理
    delete_version(version_id)
    print('[OK] 单Collection追踪测试通过')


def test_track_cross_collection():
    """测试跨Collection版本的追踪"""
    from utils.version import create_version_snapshot, delete_version, track_version_collections
    import psycopg2.extras

    collection = 'inspection-case'
    test_user = 'test_user_track_cross'

    # 1. 创建版本
    version_info = create_version_snapshot(
        collection=collection,
        name='跨Collection追踪测试',
        description='测试跨Collection追踪',
        version_type='branch',
        parent_version=None,
        created_by=test_user,
        branch_id='main'
    )
    version_id = version_info['id']

    # 2. 添加跨Collection数据
    with get_db() as conn:
        cur = conn.cursor()

        # inspection-case 数据
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('test-track-case-001', collection, psycopg2.extras.Json({'caseName': '测试用例'}), version_id, 1)
        )

        # inspection-plan 数据（跨Collection）
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('test-track-plan-001', 'inspection-plan', psycopg2.extras.Json({'planName': '测试计划'}), version_id, 1)
        )

        # 关联关系
        cur.execute(
            'INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id, branch_id) '
            'VALUES (%s, %s, %s, %s, %s, %s)',
            (collection, 'test-track-case-001', 'relatedPlan', 'inspection-plan', 'test-track-plan-001', version_id)
        )

        conn.commit()

    # 3. 追踪
    track_version_collections(version_id, collection, version_id)

    # 4. 验证追踪到2个Collection
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT collection FROM version_collections WHERE version_id = %s ORDER BY collection',
            (version_id,)
        )
        tracked = [row[0] for row in cur.fetchall()]

        assert len(tracked) == 2, f'应追踪到2个Collection，实际{len(tracked)}'
        assert 'inspection-case' in tracked
        assert 'inspection-plan' in tracked

    # 5. 清理
    delete_version(version_id)
    print('[OK] 跨Collection追踪测试通过')


if __name__ == '__main__':
    test_version_collections_table_exists()
    test_track_single_collection()
    test_track_cross_collection()
    print('\n所有测试通过！')