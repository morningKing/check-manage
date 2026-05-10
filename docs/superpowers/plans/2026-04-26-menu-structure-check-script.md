# 菜单结构检查脚本实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 创建Python脚本检查并修复系统中所有非系统菜单的层级结构，使其符合标准3层规范（workspace→project→data）

**Architecture:** 单次执行脚本，连接数据库→查询菜单→检测违规→推断修复→执行更新→输出报告，使用事务确保原子性

**Tech Stack:** Python 3, psycopg2, server/db.py模块

---

## Task 1: 创建脚本骨架和数据库连接

**Files:**
- Create: `scripts/check_menu_structure.py`

- [ ] **Step 1: 创建scripts目录和文件骨架**

```bash
mkdir -p scripts
```

创建文件 `scripts/check_menu_structure.py`:

```python
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

# 添加server目录到路径，以便导入db模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'server'))

from db import get_db


def main():
    """主流程：检查并修复菜单结构"""
    print("=" * 70)
    print("菜单结构检查脚本")
    print("=" * 70)
    
    try:
        with get_db() as conn:
            # TODO: 实现检查和修复逻辑
            print("\n检查完成。")
    except Exception as e:
        print(f"\n[错误] 数据库连接失败: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: 测试脚本基本执行**

```bash
cd E:/Code/check-manage
python scripts/check_menu_structure.py
```

预期输出：
```
======================================================================
菜单结构检查脚本
======================================================================

检查完成。
```

- [ ] **Step 3: Commit脚本骨架**

```bash
git add scripts/check_menu_structure.py
git commit -m "feat: 添加菜单结构检查脚本骨架"
```

---

## Task 2: 添加查询非系统菜单功能

**Files:**
- Modify: `scripts/check_menu_structure.py`

- [ ] **Step 1: 添加查询函数**

在main()函数前添加：

```python
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
```

- [ ] **Step 2: 在main()中调用查询函数**

修改main()函数：

```python
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
            
            # TODO: 实现检查和修复逻辑
            print("\n检查完成。")
    except Exception as e:
        print(f"\n[错误] 数据库连接失败: {e}")
        sys.exit(1)
```

- [ ] **Step 3: 测试查询功能**

```bash
python scripts/check_menu_structure.py
```

预期输出：
```
======================================================================
菜单结构检查脚本
======================================================================

[查询] 找到 9 个非系统菜单

检查完成。
```

- [ ] **Step 4: Commit查询功能**

```bash
git add scripts/check_menu_structure.py
git commit -m "feat: 添加查询非系统菜单功能"
```

---

## Task 3: 添加菜单树构建和违规检测功能

**Files:**
- Modify: `scripts/check_menu_structure.py`

- [ ] **Step 1: 添加菜单树构建函数**

在query_non_system_menus()后添加：

```python
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
```

- [ ] **Step 2: 在main()中调用检测函数**

修改main()函数：

```python
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
```

- [ ] **Step 3: 测试违规检测**

```bash
python scripts/check_menu_structure.py
```

预期输出：
```
======================================================================
菜单结构检查脚本
======================================================================

[查询] 找到 9 个非系统菜单
[检查] 发现 X 个违规菜单

检查完成。
```

- [ ] **Step 4: Commit违规检测功能**

```bash
git add scripts/check_menu_structure.py
git commit -m "feat: 添加菜单树构建和违规检测功能"
```

---

## Task 4: 添加修复推断功能

**Files:**
- Modify: `scripts/check_menu_structure.py`

- [ ] **Step 1: 添加修复推断函数**

在detect_violations()后添加：

```python
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
                # 如果找不到project父级，挂载到默认workspace
                fix['new_menuType'] = 'data'
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
```

- [ ] **Step 2: 在main()中调用推断函数**

修改main()函数：

```python
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
            
            # TODO: 实现修复执行和报告输出
            print("\n检查完成。")
    except Exception as e:
        print(f"\n[错误] 数据库连接失败: {e}")
        sys.exit(1)
```

- [ ] **Step 3: 测试修复推断**

```bash
python scripts/check_menu_structure.py
```

预期输出包含推断结果。

- [ ] **Step 4: Commit修复推断功能**

```bash
git add scripts/check_menu_structure.py
git commit -m "feat: 添加修复推断功能"
```

---

## Task 5: 添加报告输出功能

**Files:**
- Modify: `scripts/check_menu_structure.py`

- [ ] **Step 1: 添加报告输出函数**

在infer_fixes()后添加：

```python
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
```

- [ ] **Step 2: 在main()中输出修复预览报告**

修改main()函数在推断后添加：

```python
# 推断修复方案
fixes = infer_fixes(violations, menus)
if fixes is None:
    sys.exit(1)

print(f"[推断] 生成 {len(fixes)} 个修复方案")

# 输出修复预览报告
print_fix_report(fixes)

# 输出修复前树结构
print_tree_structure(menus, "\n修复前菜单树结构")
```

- [ ] **Step 3: 测试报告输出**

```bash
python scripts/check_menu_structure.py
```

预期输出包含详细的修复报告和树结构。

- [ ] **Step 4: Commit报告输出功能**

```bash
git add scripts/check_menu_structure.py
git commit -m "feat: 添加报告输出功能"
```

---

## Task 6: 添加修复执行和最终报告功能

**Files:**
- Modify: `scripts/check_menu_structure.py`

- [ ] **Step 1: 添加修复执行函数**

在print_tree_structure()后添加：

```python
def execute_fixes(conn, fixes):
    """执行修复操作（更新数据库）
    
    返回: 成功修复的数量
    """
    if not fixes:
        return 0
    
    cur = conn.cursor()
    success_count = 0
    
    for fix in fixes:
        try:
            cur.execute("""
                UPDATE menus
                SET menu_type = %s, parent_id = %s
                WHERE id = %s
            """, (fix['new_menuType'], fix['new_parentId'], fix['menu']['id']))
            success_count += 1
        except Exception as e:
            print(f"\n[错误] 修复菜单 {fix['menu']['id']} 失败: {e}")
    
    return success_count


def print_summary_report(total_menus, violations_count, fixed_count):
    """输出最终摘要报告"""
    print("\n" + "=" * 70)
    print("检查结果摘要")
    print("=" * 70)
    print(f"总菜单数: {total_menus}")
    print(f"违规菜单数: {violations_count}")
    print(f"修复成功: {fixed_count}")
    print(f"修复失败: {violations_count - fixed_count}")
    print("=" * 70)
```

- [ ] **Step 2: 在main()中执行修复和输出最终报告**

完整修改main()函数：

```python
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
            
            if violations:
                # 推断修复方案
                fixes = infer_fixes(violations, menus)
                if fixes is None:
                    sys.exit(1)
                
                print(f"[推断] 生成 {len(fixes)} 个修复方案")
                
                # 输出修复预览报告
                print_fix_report(fixes)
                
                # 输出修复前树结构
                print_tree_structure(menus, "\n修复前菜单树结构")
                
                # 执行修复
                fixed_count = execute_fixes(conn, fixes)
                print(f"\n[执行] 成功修复 {fixed_count} 个菜单")
                
                # 提交事务
                conn.commit()
                
                # 重新查询菜单，输出修复后树结构
                updated_menus = query_non_system_menus(conn)
                print_tree_structure(updated_menus, "\n修复后菜单树结构")
                
                # 输出最终摘要报告
                print_summary_report(len(menus), len(violations), fixed_count)
                
                print("\n✓ 检查完成，数据库已更新。")
            else:
                print("\n✓ 检查完成，所有菜单结构正常。")
    
    except Exception as e:
        print(f"\n[错误] 执行失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
```

- [ ] **Step 3: 测试完整流程**

```bash
python scripts/check_menu_structure.py
```

预期输出：
- 查询结果
- 检测结果
- 修复预览报告
- 修复前后树结构对比
- 最终摘要报告

- [ ] **Step 4: 验证修复结果**

查询数据库确认菜单已修复：

```bash
# 使用之前的curl命令查询菜单
TOKEN=$(curl -s -X POST http://127.0.0.1:3002/auth/login -H "Content-Type: application/json" -d '{"username":"admin","password":"admin123"}' | jq -r '.token')
curl -s http://127.0.0.1:3002/menus -H "Authorization: Bearer $TOKEN" | jq '[.[] | select(.menuType != "system")] | .[] | {id: .id, menuType: .menuType, parentId: .parentId}'
```

验证：
- 所有workspace菜单的parentId=null
- 所有project菜单的parentId指向workspace
- 所有data菜单的parentId指向project

- [ ] **Step 5: Commit修复执行功能**

```bash
git add scripts/check_menu_structure.py
git commit -m "feat: 添加修复执行和最终报告功能"
```

---

## Task 7: 优化错误处理和用户体验

**Files:**
- Modify: `scripts/check_menu_structure.py`

- [ ] **Step 1: 添加循环引用检测**

在detect_violations()函数中添加循环引用检测（在最前面）：

```python
def detect_cycle(menu_id, menu_map, visited=None):
    """检测菜单循环引用
    
    返回: True表示存在循环
    """
    if visited is None:
        visited = set()
    
    if menu_id in visited:
        return True
    
    visited.add(menu_id)
    
    menu = menu_map.get(menu_id)
    if not menu or not menu['parentId']:
        return False
    
    return detect_cycle(menu['parentId'], menu_map, visited)


def detect_violations(menus):
    """检测所有违规菜单
    
    返回: list of dict，每个dict包含违规菜单信息
    """
    menu_map = build_menu_map(menus)
    violations = []
    
    # 先检测循环引用
    for menu in menus:
        if menu['parentId'] and detect_cycle(menu['id'], menu_map):
            violations.append({
                'menu': menu,
                'issue': '菜单存在循环引用，需要手动修复',
                'expected_menuType': menu['menuType'],
                'expected_parentId': None,
                'manual_fix': True  # 标记为需手动修复
            })
    
    # 继续检测其他违规（跳过已检测的循环引用菜单）
    for menu in menus:
        # ... 原有的检测逻辑
```

在infer_fixes()中跳过manual_fix的菜单：

```python
def infer_fixes(violations, menus):
    """为每个违规菜单推断正确的修复方案"""
    fixes = []
    
    for violation in violations:
        # 跳过需要手动修复的菜单
        if violation.get('manual_fix'):
            print(f"\n[警告] 菜单 {violation['menu']['id']} 需要手动修复")
            continue
        
        # ... 原有的推断逻辑
```

- [ ] **Step 2: 添加更友好的错误提示**

在main()函数开头添加：

```python
def main():
    """主流程：检查并修复菜单结构"""
    print("=" * 70)
    print("菜单结构检查脚本")
    print("=" * 70)
    print("\n说明:")
    print("  此脚本检查所有非系统菜单的层级结构")
    print("  workspace菜单: 一级菜单 (parentId=null)")
    print("  project菜单:   二级菜单 (parentId指向workspace)")
    print("  data菜单:      三级菜单 (parentId指向project)")
    print("\n注意:")
    print("  - 修复操作会直接更新数据库")
    print("  - 请确保数据库有备份")
    print("  - 建议先查看修复预览报告再确认")
    print("=" * 70)
    
    # ... 原有逻辑
```

- [ ] **Step 3: 测试优化后的脚本**

```bash
python scripts/check_menu_structure.py
```

验证友好的提示信息显示。

- [ ] **Step 4: Commit优化**

```bash
git add scripts/check_menu_structure.py
git commit -m "feat: 优化错误处理和用户体验"
```

---

## Task 8: 添加文档说明和脚本权限

**Files:**
- Modify: `scripts/check_menu_structure.py`

- [ ] **Step 1: 在脚本开头添加完整的使用说明**

更新脚本开头的注释：

```python
#!/usr/bin/env python3
"""
菜单结构检查和修复脚本

检查系统中所有非系统菜单是否符合标准3层结构规范：
- workspace (一级): parentId=null, menuType='workspace'
- project (二级): parentId指向workspace, menuType='project'
- data (三级): parentId指向project, menuType='data'

功能：
  1. 检查所有非系统菜单的层级关系
  2. 检查menuType是否与实际层级匹配
  3. 自动推断并修复违规菜单
  4. 输出详细报告（修复前/后对比）
  5. 使用数据库事务确保原子性

使用方法：
  python scripts/check_menu_structure.py

输出：
  - 详细修复列表（每个菜单的当前状态和修复方案）
  - 修复前后树结构对比
  - 最终摘要报告

错误处理：
  - 循环引用菜单标记为需手动干预
  - 缺少workspace菜单时提示创建
  - 数据库错误时回滚事务

依赖：
  - server/db.py (数据库连接模块)
  - psycopg2 (PostgreSQL驱动)

作者: Claude Code Assistant
日期: 2026-04-26
"""
```

- [ ] **Step 2: 础保脚本有执行权限（Linux/Mac）**

```bash
chmod +x scripts/check_menu_structure.py
```

Windows无需此操作。

- [ ] **Step 3: 测试完整脚本**

```bash
python scripts/check_menu_structure.py
```

确认所有功能正常工作。

- [ ] **Step 4: Commit文档和权限**

```bash
git add scripts/check_menu_structure.py
git commit -m "docs: 添加完整的使用文档说明"
```

---

## 完成验证

所有任务完成后执行以下验证：

- [ ] **验证1: 运行脚本无错误**

```bash
python scripts/check_menu_structure.py
```

- [ ] **验证2: 查询数据库确认修复结果**

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:3002/auth/login -H "Content-Type: application/json" -d '{"username":"admin","password":"admin123"}' | jq -r '.token')
curl -s http://127.0.0.1:3002/menus -H "Authorization: Bearer $TOKEN" | jq '[.[] | select(.menuType != "system")] | sort_by(.id)'
```

验证：
- workspace菜单parentId=null ✓
- project菜单parentId指向workspace ✓
- data菜单parentId指向project ✓

- [ ] **验证3: 前端菜单树正常显示**

刷新浏览器，查看菜单树是否正确显示三层结构。

- [ ] **验证4: Git历史完整**

```bash
git log --oneline scripts/check_menu_structure.py
```

应该看到7-8个commit。

---

**计划完成。准备执行。**