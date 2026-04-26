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


def infer_fixes(violations, menus):
    """为每个违规菜单推断正确的修复方案

    返回: list of dict，每个dict包含修复操作详情
    """
    menu_map = build_menu_map(menus)
    fixes = []

    # 找到第一个workspace菜单作为默认父级
    default_workspace = None
    for menu in menus:
        if menu['menuType'] == 'workspace' and menu['parentId'] is None:
            default_workspace = menu['id']
            break

    if not default_workspace:
        print("\n[警告] 找不到workspace菜单作为默认父级，请先创建workspace菜单")
        return None

    for violation in violations:
        menu = violation['menu']
        fix = {
            'menu': menu,
            'issue': violation['issue'],
            'old_menuType': menu['menuType'],
            'old_parentId': menu['parentId'],
            'new_menuType': menu['menuType'],
            'new_parentId': menu['parentId']
        }

        # 推断修复方案
        parent = menu_map.get(menu['parentId']) if menu['parentId'] else None

        # 情况1: parentId=null → 应该是workspace
        if menu['parentId'] is None:
            fix['new_menuType'] = 'workspace'
            fix['new_parentId'] = None

        # 情况2: parentId指向workspace → 应该是project
        elif parent and parent['menuType'] == 'workspace':
            fix['new_menuType'] = 'project'
            fix['new_parentId'] = menu['parentId']

        # 情况3: parentId指向project → 应该是data
        elif parent and parent['menuType'] == 'project':
            fix['new_menuType'] = 'data'
            fix['new_parentId'] = menu['parentId']

        # 情况4: parentId指向data → 层级过深，提升到project
        elif parent and parent['menuType'] == 'data':
            # 找到parent的父级project
            grandparent = menu_map.get(parent['parentId']) if parent['parentId'] else None
            if grandparent and grandparent['menuType'] == 'project':
                fix['new_menuType'] = 'data'
                fix['new_parentId'] = grandparent['id']
            else:
                # 如果找不到project父级，提升为project类型并挂载到默认workspace
                fix['new_menuType'] = 'project'
                fix['new_parentId'] = default_workspace

        # 情况5: parentId不存在或类型不匹配 → 挂载到默认workspace
        else:
            # 根据当前menuType推断正确层级
            if menu['menuType'] == 'project':
                fix['new_parentId'] = default_workspace
            elif menu['menuType'] == 'data':
                # 需要找到一个project菜单作为父级
                # 简化处理：挂载到默认workspace下的第一个project
                project_menus = [m for m in menus if m['menuType'] == 'project' and m['parentId'] == default_workspace]
                if project_menus:
                    fix['new_parentId'] = project_menus[0]['id']
                else:
                    fix['new_parentId'] = default_workspace
                    fix['new_menuType'] = 'project'  # 提升为project

        fixes.append(fix)

    return fixes


def print_fix_report(fixes):
    """输出详细的修复报告"""
    if not fixes:
        print("\n[结果] 所有菜单结构正常，无需修复。")
        return

    print("\n" + "=" * 70)
    print("修复报告详情")
    print("=" * 70)

    for i, fix in enumerate(fixes, 1):
        menu = fix['menu']
        print(f"\n{i}. 菜单ID: {menu['id']} ({menu['name']})")
        print(f"   问题: {fix['issue']}")
        print(f"   当前: menuType={fix['old_menuType']}, parentId={fix['old_parentId']}")
        print(f"   修复: menuType={fix['new_menuType']}, parentId={fix['new_parentId']}")


def print_tree_structure(menus, title):
    """输出菜单树结构"""
    print(f"\n{title}")
    print("-" * 70)

    menu_map = build_menu_map(menus)

    # 构建树结构
    roots = [m for m in menus if m['parentId'] is None]

    def print_menu(menu, indent=0):
        prefix = "  " * indent
        marker = "├─" if indent > 0 else ""
        print(f"{prefix}{marker}{menu['name']} ({menu['menuType']}) [{menu['id']}]")

        # 找到子菜单
        children = [m for m in menus if m['parentId'] == menu['id']]
        for child in children:
            print_menu(child, indent + 1)

    for root in roots:
        print_menu(root)


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

            # 推断修复方案
            fixes = infer_fixes(violations, menus)
            if fixes is None:
                sys.exit(1)

            print(f"[推断] 生成 {len(fixes)} 个修复方案")

            # 输出修复预览报告
            print_fix_report(fixes)

            # 输出修复前树结构
            print_tree_structure(menus, "\n修复前菜单树结构")

            # TODO: 实现修复执行和报告输出
            print("\n检查完成。")
    except Exception as e:
        print(f"\n[错误] 数据库连接失败: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()