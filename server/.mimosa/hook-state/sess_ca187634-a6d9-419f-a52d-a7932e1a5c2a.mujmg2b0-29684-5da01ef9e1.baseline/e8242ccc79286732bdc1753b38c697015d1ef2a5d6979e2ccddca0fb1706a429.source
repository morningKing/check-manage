"""
数据级分支状态迁移到项目级

执行步骤：
1. 查找所有 project 类型菜单
2. 对于每个 project，找到其下所有 data 菜单对应的 collection
3. 将用户在这些 collection 上的分支状态汇总到 project 级别
4. 使用第一个 collection 的分支状态作为项目的统一分支状态

运行方式：
    cd server && python -m migrations.001_migrate_branch_to_project_level
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from db import get_db
import psycopg2.extras


def migrate_branch_to_project_level():
    """
    将数据级分支状态迁移到项目级
    """
    print("开始迁移数据级分支状态到项目级...")

    with get_db() as conn:
        cur = conn.cursor()

        # 1. 查找所有 project 类型菜单
        cur.execute(
            "SELECT id, name FROM menus WHERE menu_type = 'project'"
        )
        project_menus = cur.fetchall()
        print(f"找到 {len(project_menus)} 个项目菜单")

        migrated_count = 0

        for project_id, project_name in project_menus:
            print(f"\n处理项目: {project_name} ({project_id})")

            # 2. 获取项目下的所有 data 菜单对应的 collection
            cur.execute(
                'SELECT m.id, m.page_id FROM menus m '
                'WHERE m.parent_id = %s AND m.menu_type = %s',
                (project_id, 'data')
            )
            data_menus = cur.fetchall()

            collections = []
            for menu_id, page_id in data_menus:
                if page_id:
                    collection = page_id.replace('page-', '')
                    collections.append(collection)

            if not collections:
                print(f"  项目下没有数据菜单，跳过")
                continue

            print(f"  项目包含 {len(collections)} 个 collection: {collections}")

            # 3. 查询用户在这些 collection 上的分支状态
            # 使用第一个 collection 的分支状态作为项目统一分支
            first_collection = collections[0]

            cur.execute(
                'SELECT user_id, username, branch_id FROM user_current_branch '
                'WHERE collection = %s',
                (first_collection,)
            )
            user_branches = cur.fetchall()

            if not user_branches:
                print(f"  没有用户分支状态记录，跳过")
                continue

            # 4. 迁移到 user_current_project_branch
            for user_id, username, branch_id in user_branches:
                now = datetime.now(timezone.utc)
                record_id = f'ucpb-{user_id}-{project_id}'

                cur.execute(
                    'INSERT INTO user_current_project_branch '
                    '(id, user_id, username, project_menu_id, branch_id, updated_at) '
                    'VALUES (%s, %s, %s, %s, %s, %s) '
                    'ON CONFLICT (user_id, project_menu_id) DO UPDATE SET '
                    'branch_id = %s, updated_at = %s',
                    (record_id, user_id, username, project_id, branch_id, now,
                     branch_id, now)
                )
                migrated_count += 1
                print(f"  迁移用户 {username}: 分支 {branch_id}")

        conn.commit()

    print(f"\n迁移完成！共迁移 {migrated_count} 条用户分支状态记录")
    print("提示：user_current_branch 表的数据已保留，但新逻辑将使用 user_current_project_branch")


if __name__ == '__main__':
    migrate_branch_to_project_level()