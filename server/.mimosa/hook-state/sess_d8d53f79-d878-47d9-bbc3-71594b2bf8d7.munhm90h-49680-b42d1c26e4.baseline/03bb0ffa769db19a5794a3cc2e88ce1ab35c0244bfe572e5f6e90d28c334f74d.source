"""
修复历史数据菜单的 project_id 字段

执行步骤：
1. 查找所有 data 类型菜单
2. 对于每个 data 菜单，检查其 parent_id 是否为 project 类型
3. 如果是，则设置 project_id = parent_id

运行方式：
    cd server && python -m migrations.002_fix_menu_project_id
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db


def fix_menu_project_id():
    """
    修复历史数据菜单的 project_id 字段
    """
    print("开始修复数据菜单的 project_id 字段...")

    with get_db() as conn:
        cur = conn.cursor()

        # 1. 查找所有 data 类型菜单
        cur.execute(
            "SELECT id, name, parent_id, project_id FROM menus WHERE menu_type = 'data'"
        )
        data_menus = cur.fetchall()
        print(f"找到 {len(data_menus)} 个数据菜单")

        fixed_count = 0

        for menu_id, menu_name, parent_id, current_project_id in data_menus:
            # 如果已经有 project_id，跳过
            if current_project_id:
                print(f"  菜单「{menu_name}」已有 project_id: {current_project_id}")
                continue

            # 如果没有 parent_id，跳过
            if not parent_id:
                print(f"  菜单「{menu_name}」没有父级，跳过")
                continue

            # 检查父级是否为 project 类型
            cur.execute(
                'SELECT menu_type FROM menus WHERE id = %s',
                (parent_id,)
            )
            row = cur.fetchone()
            if row and row[0] == 'project':
                # 设置 project_id = parent_id
                cur.execute(
                    'UPDATE menus SET project_id = %s WHERE id = %s',
                    (parent_id, menu_id)
                )
                fixed_count += 1
                print(f"  菜单「{menu_name}」project_id 已设置为: {parent_id}")
            else:
                print(f"  菜单「{menu_name}」父级不是 project 类型，跳过")

        conn.commit()

    print(f"\n修复完成！共修复 {fixed_count} 个数据菜单")


if __name__ == '__main__':
    fix_menu_project_id()