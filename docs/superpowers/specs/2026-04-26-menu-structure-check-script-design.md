# 菜单结构检查脚本设计文档

**创建日期**: 2026-04-26
**目标**: 创建脚本验证系统中所有非系统菜单是否符合标准3层结构（工作空间→项目→数据菜单），并自动修复不符合规则的菜单

---

## 1. 目标和范围

### 问题背景

当前系统中存在菜单层级结构混乱的问题：
- 部分菜单的menuType标记与实际层级不匹配
- 部分菜单的parentId指向错误的父级菜单类型
- 菜单ID命名不规范（存在`project-page-*`格式的菜单）
- 层级关系违反了标准3层结构规范

### 目标

创建一个Python脚本 `scripts/check_menu_structure.py`，实现：
1. 检查所有非系统菜单的层级结构是否符合规范
2. 自动推断并修复违规菜单的层级关系
3. 输出详细的检查报告（修复前/修复后对比）
4. 使用数据库事务确保修复操作的原子性

### 范围

**包含的内容：**
- 检查workspace/project/data三种类型菜单的层级关系
- 自动修正menuType和parentId字段
- 生成文本格式的检查报告
- 基本错误处理和异常报告

**不包含的内容：**
- 修改菜单ID（保留现有ID命名）
- 前端UI展示检查报告
- 自动定时检查机制
- 处理系统菜单（menuType='system'）

---

## 2. 层级结构规范

### 标准三层结构

```
一级菜单（workspace）:
  - menuType = 'workspace'
  - parentId = null
  - 例如：menu-2 (巡检管理)

二级菜单（project）:
  - menuType = 'project'
  - parentId 指向 workspace菜单
  - 例如：menu-2-1 (巡检用例)

三级菜单（data）:
  - menuType = 'data'
  - parentId 指向 project菜单
  - 例如：menu-2-1-1 (巡检用例详情)
```

### 违规检测规则

以下情况视为违规：
1. workspace菜单但 parentId != null
2. project菜单但 parent不是workspace类型
3. data菜单但 parent不是project类型
4. parentId=null 但 menuType不是workspace
5. parentId指向不存在或已被删除的菜单
6. 菜单层级超过3级（data菜单的子菜单）

---

## 3. 检查和修复逻辑

### 检查流程

```
步骤1: 连接数据库
步骤2: 查询所有menuType != 'system'的菜单
步骤3: 按menuType分类（workspace/project/data）
步骤4: 构建菜单树结构（内存中）
步骤5: 逐个检查每个菜单的parentId关系
步骤6: 检查每个菜单的menuType是否与层级匹配
步骤7: 生成违规菜单列表（包含问题描述）
步骤8: 为每个违规菜单推断正确的修复方案
步骤9: 输出修复预览报告
步骤10: 执行修复操作（事务内）
步骤11: 输出最终报告
```

### 修复推断规则

**自动推断正确的层级（优先级从高到低）：**

| 当前状态 | 推断规则 | 修复操作 |
|---------|---------|---------|
| parentId=null | 该菜单应是一级workspace | menuType改为'workspace' |
| parentId指向workspace | 该菜单应是二级project | menuType改为'project' |
| parentId指向project | 该菜单应是三级data | menuType改为'data' |
| parentId指向data | 层级过深（四级） | parentId改为parent的父级project |
| parentId不存在 | 父级丢失 | parentId改为默认workspace（如menu-2） |

**特殊情况处理：**
- 多个workspace菜单：允许存在（系统支持多个工作空间）
- project菜单直接挂在一级（无workspace父级）：挂载到第一个workspace菜单
- 菜单层级超过3级：提升到正确的层级

---

## 4. 输出报告设计

### 报告结构

```
=================================================================
菜单结构检查报告
执行时间: YYYY-MM-DD HH:MM:SS
=================================================================

[检查结果摘要]
总菜单数: N
违规菜单数: M
修复成功: K
修复失败: 0

[详细修复列表]

序号. 菜单ID: xxx (菜单名称)
   问题: [问题描述]
   修复: [修复方案详情]
   状态: ✓ 已修复 / ✗ 修复失败

[修复前后对比树结构]
修复前:
  workspace-menu (workspace)
    ├─ menu-a (data) ❌
    └─ menu-b (project) ❌

修复后:
  workspace-menu (workspace) ✓
    ├─ menu-b (project) ✓
    └─ menu-c (project) ✓
        └─ menu-a (data) ✓

=================================================================
检查完成，数据库已更新。
=================================================================
```

### 树结构渲染

使用文本树形图展示菜单层级关系：
- 使用 `├─` 和 `└─` 表示同级菜单
- 使用缩进表示层级深度
- 使用 `✓` 和 `❌` 标记菜单状态

---

## 5. 数据库操作

### 事务处理

```python
try:
    # 开始事务（自动）
    conn = get_db()

    # 查询所有非系统菜单
    menus = query_non_system_menus(conn)

    # 检查并生成修复列表（内存中）
    violations = detect_violations(menus)
    fix_plan = generate_fix_plan(violations)

    # 输出修复预览
    print_fix_preview(fix_plan)

    # 执行修复
    execute_fixes(conn, fix_plan)

    # 提交事务
    conn.commit()

    # 输出最终报告
    print_final_report(fix_plan)

except Exception as e:
    # 回滚事务（自动）
    conn.rollback()
    print_error_report(e)
```

### SQL查询

**查询非系统菜单：**
```sql
SELECT id, name, menuType, parentId, order
FROM menus
WHERE menuType != 'system'
ORDER BY menuType, order;
```

**更新菜单：**
```sql
UPDATE menus
SET menuType = ?, parentId = ?
WHERE id = ?;
```

---

## 6. 错误处理

### 错误场景和处理

| 错误场景 | 处理方法 |
|---------|---------|
| 找不到workspace菜单作为默认父级 | 报告错误，提示用户先创建workspace菜单，退出脚本 |
| 菜单循环引用（A→B→A） | 检测循环并报告，标记为需手动干预，不自动修复 |
| parentId指向已删除菜单 | 将菜单挂载到默认workspace或提升为一级 |
| 数据库连接失败 | 输出错误信息，退出脚本 |
| 修复过程中异常 | 回滚事务，输出错误信息，退出脚本 |

### 日志和报告

**正常执行：**
- 输出检查过程日志到stdout
- 使用清晰的格式化输出便于阅读

**异常执行：**
- 输出详细的错误堆栈信息
- 提示用户如何手动修复问题

---

## 7. 文件结构

```
scripts/
  └─ check_menu_structure.py     # 主脚本文件

依赖模块：
  - server/db.py                  # 数据库连接模块
  - psycopg2                      # PostgreSQL驱动
```

### 脚本参数

**无需参数** - 脚本自动连接数据库并执行检查修复

**可选参数（未来扩展）：**
```
--dry-run        仅检查不修复，输出报告
--verbose        显示详细调试信息
--target-id=xxx  仅检查指定菜单ID
```

---

## 8. 测试验证

### 测试步骤

1. **执行前检查：**
   ```bash
   # 查询当前违规菜单数量
   python scripts/check_menu_structure.py --dry-run
   ```

2. **执行修复：**
   ```bash
   python scripts/check_menu_structure.py
   ```

3. **验证结果：**
   ```bash
   # 重新查询菜单结构，确认无违规
   # 查看输出的"修复前后对比"
   # 验证前端菜单树显示是否正确
   ```

### 验证标准

修复成功的标准：
- 所有菜单的menuType与层级匹配
- 所有菜单的parentId指向正确类型的父级
- 前端菜单树正确显示三层结构
- 项目版本管理功能正常工作（依赖正确的project菜单层级）

---

## 9. 后续维护

### 使用时机

**何时执行：**
- 发现菜单层级混乱导致功能异常时
- 手动修改菜单后验证结构是否正确
- 系统升级后检查数据完整性

**维护建议：**
- 将脚本保留在scripts目录中，随时可执行
- 如果后续频繁添加菜单，考虑将检查逻辑集成到菜单创建API中
- 定期（如每月）执行一次检查，确保结构正确

---

## 10. 约束和限制

### 不处理的内容

**系统菜单：**
- menuType='system'的菜单不在检查范围内
- 系统菜单可以有特殊层级结构（如平台管理的二级分组）

**菜单ID修改：**
- 不修改任何菜单的ID字段
- 即使ID命名不规范也保留原ID

**菜单内容：**
- 不修改菜单的name、icon、path等其他字段
- 仅修改menuType和parentId

### 前置条件

**必需条件：**
- 数据库连接配置正确
- 至少存在一个workspace类型的菜单（作为默认父级）
- menus表结构包含menuType和parentId字段

---

## 附录：设计决策

### 关键决策记录

| 决策点 | 选择 | 理由 |
|-------|------|------|
| 修复模式 | 严格修复 | 标准化层级结构对权限控制、版本管理至关重要 |
| ID处理 | 保留现有ID | 避免影响外键关联和数据完整性 |
| menuType处理 | 自动推断修正 | menuType应与层级一致，自动修正避免手动错误 |
| 实现方案 | 单次执行脚本 | 一次性修复历史遗留问题，简单可靠 |
| 输出报告 | 文本格式 | 清晰易读，便于验证修复结果 |

---

**设计完成，等待用户确认后进入实现计划阶段。**