"""
迁移脚本：为现有版本填充 version_collections 数据
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db
from utils.version import track_version_collections

def migrate_version_collections():
    """为所有现有版本填充 version_collections 数据"""
    with get_db() as conn:
        cur = conn.cursor()

        # Query all branch versions
        cur.execute(
            'SELECT id, collection FROM collection_versions WHERE version_type = %s',
            ('branch',)
        )
        versions = cur.fetchall()

        print(f'找到 {len(versions)} 个分支版本')

        for version_id, collection in versions:
            print(f'处理版本 {version_id} ({collection})...')

            # Check if tracking data exists
            cur.execute(
                'SELECT COUNT(*) FROM version_collections WHERE version_id = %s',
                (version_id,)
            )
            count = cur.fetchone()[0]

            if count == 0:
                track_version_collections(version_id, collection, version_id)
                print(f'  已填充 {version_id} 的追踪数据')
            else:
                print(f'  已存在追踪数据，跳过')

        conn.commit()

    print('迁移完成！')

if __name__ == '__main__':
    migrate_version_collections()