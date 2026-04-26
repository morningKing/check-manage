#!/usr/bin/env python3
"""
菜单结构检查和修复脚本

检查系统中所有非系统菜单是否符合标准3层结构规范：
- workspace (一级): parentId=null
- project (二级): parentId指向workspace
- data (三级): parentId指向project

自动修复违规菜单并输出详细报告。
"""

import sys
import os

# 添加server目录到路径,以便导入db模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'server'))

from db import get_db


def query_non_system_menus(conn):
    """查询所有非系统菜单

    返回: list of dict，每个dict包含菜单信息
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, menu_type, parent_id, "order"
        FROM menus
        WHERE menu_type != 'system'
        ORDER BY menu_type, "order"
    """)

    menus = []
    for row in cur.fetchall():
        menus.append({
            'id': row[0],
            'name': row[1],
            'menuType': row[2],
            'parentId': row[3],
            'order': row[4]
        })

    return menus


def build_menu_map(menus):
    """构建菜单ID到菜单对象的映射

    返回: dict {menu_id: menu_dict}
    """
    return {menu['id']: menu for menu in menus}


def detect_violations(menus):
    """检测所有违规菜单

    返回: list of dict，每个dict包含违规菜单信息
    """
    menu_map = build_menu_map(menus)
    violations = []

    for menu in menus:
        violation = None

        # 检查规则1: workspace菜单但 parentId != null
        if menu['menuType'] == 'workspace' and menu['parentId'] is not None:
            violation = {
                'menu': menu,
                'issue': 'workspace菜单应该是一级菜单（parentId=null）',
                'expected_menuType': 'workspace',
                'expected_parentId': None
            }

        # 检查规则2: parentId=null 但 menuType不是workspace
        elif menu['parentId'] is None and menu['menuType'] != 'workspace':
            violation = {
                'menu': menu,
                'issue': '一级菜单应该是workspace类型',
                'expected_menuType': 'workspace',
                'expected_parentId': None
            }

        # 检查规则3-6: 有parentId的菜单
        elif menu['parentId'] is not None:
            parent = menu_map.get(menu['parentId'])

            if not parent:
                # 规则5: parentId指向不存在的菜单
                violation = {
                    'menu': menu,
                    'issue': f"父级菜单 {menu['parentId']} 不存在",
                    'expected_menuType': menu['menuType'],  # 保持原类型
                    'expected_parentId': None  # 需要推断正确的父级
                }

            elif menu['menuType'] == 'project' and parent['menuType'] != 'workspace':
                # 规则2: project菜单的parent不是workspace
                violation = {
                    'menu': menu,
                    'issue': f"project菜单的父级应该是workspace，当前是{parent['menuType']}",
                    'expected_menuType': 'project',
                    'expected_parentId': None  # 需要推断正确的workspace父级
                }

            elif menu['menuType'] == 'data' and parent['menuType'] != 'project':
                # 规则3: data菜单的parent不是project
                violation = {
                    'menu': menu,
                    'issue': f"data菜单的父级应该是project，当前是{parent['menuType']}",
                    'expected_menuType': 'data',
                    'expected_parentId': None  # 需要推断正确的project父级
                }

            elif parent['menuType'] == 'data':
                # 规则6: 层级过深（parent是data，当前是四级）
                violation = {
                    'menu': menu,
                    'issue': '菜单层级过深（超过3层）',
                    'expected_menuType': 'data',
                    'expected_parentId': None  # 需要推断正确的project父级
                }

        if violation:
            violations.append(violation)

    return violations


def main():
    """主流程：检查并修复菜单结构"""
    print("=" * 70)
    print("菜单结构检查脚本")
    print("=" * 70)

    try:
        with get_db() as conn:
            # 查询所有非系统菜单
            menus = query_non_system_menus(conn)
            print(f"\n[查询] 找到 {len(menus)} 个非系统菜单")

            # 检测违规菜单
            violations = detect_violations(menus)
            print(f"[检查] 发现 {len(violations)} 个违规菜单")

            # TODO: 实现修复推断和执行
            print("\n检查完成。")
    except Exception as e:
        print(f"\n[错误] 数据库连接失败: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()