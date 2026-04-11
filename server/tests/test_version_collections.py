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


if __name__ == '__main__':
    test_version_collections_table_exists()
    print('\n表创建测试通过！')