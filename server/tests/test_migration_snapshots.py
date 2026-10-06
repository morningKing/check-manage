"""版本快照 schema 校验。

历史注记：dev 库 version_snapshots.collection 列来自一次手工迁移
（migrations/migrate_version_snapshots_add_collection.py，未纳入 init_db /
启动钩子的自动链，脚本本身不幂等）。现役代码已不使用该列——快照的
collection 归属由后继表 project_version_snapshots 承担（init_db 种子
schema 自带）。这里断言现役规范 schema，而不是某个历史手工迁移的遗迹。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db


def test_version_snapshots_matches_canonical_schema():
    """version_snapshots 保持规范四列（无 collection 遗留列）。"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT column_name FROM information_schema.columns '
            'WHERE table_name = %s ORDER BY ordinal_position',
            ('version_snapshots',)
        )
        cols = [r[0] for r in cur.fetchall()]
        assert cols == ['version_id', 'record_id', 'record_data', 'created_at'], \
            f'version_snapshots 列形状偏离规范 schema: {cols}'


def test_project_version_snapshots_has_collection_field():
    """现役快照表 project_version_snapshots 自带 collection 归属列。"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT column_name FROM information_schema.columns '
            'WHERE table_name = %s AND column_name = %s',
            ('project_version_snapshots', 'collection')
        )
        assert cur.fetchone() is not None, \
            'collection column should exist in project_version_snapshots'


if __name__ == '__main__':
    test_version_snapshots_matches_canonical_schema()
    test_project_version_snapshots_has_collection_field()
    print('\nTest passed!')
