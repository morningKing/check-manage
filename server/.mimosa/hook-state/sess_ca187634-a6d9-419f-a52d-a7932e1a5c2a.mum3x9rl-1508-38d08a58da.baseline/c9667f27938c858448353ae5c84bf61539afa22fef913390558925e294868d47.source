"""
数据迁移脚本：为现有版本补充 version_collections 数据

执行时机：部署新功能后一次性运行
影响范围：所有 branch 类型的版本
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db
from datetime import datetime, timezone
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def migrate_version_collections():
    """为现有版本补充 Collection 追踪数据"""
    logger.info('开始迁移 version_collections 数据...')

    with get_db() as conn:
        cur = conn.cursor()

        # 1. 查询所有活跃的分支版本
        cur.execute(
            'SELECT id, collection FROM collection_versions '
            'WHERE version_type = %s AND status != %s',
            ('branch', 'merged')
        )
        versions = cur.fetchall()

        logger.info(f'发现 {len(versions)} 个活跃的分支版本需要迁移')

        migrated_count = 0
        skipped_count = 0

        for version_id, collection in versions:
            logger.info(f'处理版本: {version_id} ({collection})')

            # 2. 检查是否已有追踪数据
            cur.execute(
                'SELECT COUNT(*) FROM version_collections WHERE version_id = %s',
                (version_id,)
            )
            existing = cur.fetchone()[0]

            if existing > 0:
                logger.info(f'  已有 {existing} 条追踪数据，跳过')
                skipped_count += 1
                continue

            # 3. 扫描直接数据
            cur.execute(
                'SELECT DISTINCT collection FROM dynamic_data WHERE branch_id = %s',
                (version_id,)
            )
            direct = [row[0] for row in cur.fetchall()]

            # 4. 扫描关联数据 - 两列都要扫描（修复Task 2发现的bug）
            cur.execute(
                'SELECT DISTINCT collection FROM data_relations WHERE branch_id = %s '
                'UNION '
                'SELECT DISTINCT related_collection FROM data_relations WHERE branch_id = %s',
                (version_id, version_id)
            )
            related = [row[0] for row in cur.fetchall()]

            # 5. 合并去重
            all_collections = set(direct + related)

            if not all_collections:
                all_collections = {collection}
                logger.info(f'  未找到数据，使用主Collection: {collection}')

            # 6. 插入追踪数据
            now = datetime.now(timezone.utc)
            for coll in all_collections:
                cur.execute(
                    'INSERT INTO version_collections (version_id, collection, created_at) '
                    'VALUES (%s, %s, %s)',
                    (version_id, coll, now)
                )

            logger.info(f'  追踪到 {len(all_collections)} 个Collection: {sorted(all_collections)}')
            migrated_count += 1

        conn.commit()

    logger.info(f'\n迁移完成！')
    logger.info(f'  成功迁移: {migrated_count} 个版本')
    logger.info(f'  已有数据跳过: {skipped_count} 个版本')
    logger.info(f'  总处理: {len(versions)} 个版本')


def verify_migration():
    """验证迁移结果"""
    logger.info('\n验证迁移结果...')

    with get_db() as conn:
        cur = conn.cursor()

        # 检查孤立数据
        cur.execute(
            'SELECT collection, branch_id, COUNT(*) '
            'FROM dynamic_data '
            'WHERE branch_id NOT IN ('
            '    SELECT id FROM collection_versions WHERE version_type = \'branch\''
            ') '
            'AND branch_id != \'main\' '
            'GROUP BY collection, branch_id'
        )
        orphaned = cur.fetchall()

        if orphaned:
            logger.warning('⚠️ 发现孤立数据：')
            for row in orphaned:
                logger.warning(f'  {row[0]} (branch: {row[1]}): {row[2]} 条')
            logger.warning('建议：运行 python scripts/migrate_version_collections.py --cleanup')
        else:
            logger.info('✅ 无孤立数据')

        # 检查追踪数据完整性
        cur.execute(
            'SELECT cv.id, cv.collection, COUNT(vc.collection) '
            'FROM collection_versions cv '
            'LEFT JOIN version_collections vc ON cv.id = vc.version_id '
            'WHERE cv.version_type = \'branch\' AND cv.status != \'merged\' '
            'GROUP BY cv.id, cv.collection '
            'HAVING COUNT(vc.collection) = 0'
        )
        missing = cur.fetchall()

        if missing:
            logger.warning('⚠️ 发现缺少追踪数据的版本：')
            for row in missing:
                logger.warning(f'  版本 {row[0]} ({row[1]})')
        else:
            logger.info('✅ 所有活跃分支版本都有追踪数据')


def cleanup_orphaned_data():
    """清理孤立数据"""
    logger.info('\n准备清理孤立数据...')

    with get_db() as conn:
        cur = conn.cursor()

        cur.execute(
            'SELECT collection, branch_id, COUNT(*) '
            'FROM dynamic_data '
            'WHERE branch_id NOT IN ('
            '    SELECT id FROM collection_versions WHERE version_type = \'branch\''
            ') '
            'AND branch_id != \'main\' '
            'GROUP BY collection, branch_id'
        )
        orphaned = cur.fetchall()

        if not orphaned:
            logger.info('无需清理，没有孤立数据')
            return

        total = sum(row[2] for row in orphaned)
        logger.info(f'发现 {total} 条孤立数据：')
        for row in orphaned:
            logger.info(f'  {row[0]} (branch: {row[1]}): {row[2]} 条')

        response = input('\n确认清理这些孤立数据吗？(y/n): ')
        if response.lower() not in ('y', 'yes'):
            logger.info('取消清理')
            return

        for collection, branch_id, count in orphaned:
            cur.execute(
                'DELETE FROM dynamic_data WHERE collection = %s AND branch_id = %s',
                (collection, branch_id)
            )
            logger.info(f'  已清理 {collection} (branch: {branch_id}): {cur.rowcount} 条')

        for _, branch_id, _ in orphaned:
            cur.execute(
                'DELETE FROM data_relations WHERE branch_id = %s',
                (branch_id,)
            )

        conn.commit()
        logger.info('✅ 清理完成')


if __name__ == '__main__':
    logger.info('========================================')
    logger.info('version_collections 数据迁移脚本')
    logger.info('========================================\n')

    migrate_version_collections()
    verify_migration()

    if '--cleanup' in sys.argv:
        cleanup_orphaned_data()

    logger.info('\n迁移脚本执行完毕')