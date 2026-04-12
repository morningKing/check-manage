# 跨 Collection 版本管理问题

## 问题发现时间

2026-04-08

## 问题状态

⚠️ **已发现，暂未修复**（待后续讨论优化方案）

## 问题严重程度

**中等风险** - 在特定使用场景下会产生孤立数据

## 问题场景

### 触发条件

1. 用户创建某个 Collection 的版本分支
2. 在该版本中添加跨 Collection 关联的数据
3. 删除该版本

### 具体示例

```
场景：巡检用例关联巡检计划

1. 创建版本
   - 版本类型：branch
   - 版本 ID：ver-test-case-branch
   - Collection：inspection-case（仅针对巡检用例）

2. 在版本中添加数据
   - 巡检用例 A（inspection-case）
     - ID: case-001
     - Branch: ver-test-case-branch

   - 巡检计划 P（inspection-plan）← 关键！不同 Collection
     - ID: plan-001
     - Branch: ver-test-case-branch

   - 关联关系
     - case-001 -> plan-001

3. 删除版本

系统行为：
  ✓ 删除 inspection-case 的数据（case-001）
  ✓ 删除关联关系
  ✗ 保留 inspection-plan 的数据（plan-001）

结果：
  plan-001 成为孤立数据
```

## 问题根因

### 设计局限

```
版本管理的作用域 = 单个 Collection
数据关联的作用域 = 跨多个 Collection

不匹配！
```

### 代码位置

**文件**：`server/utils/version.py`

**函数**：`delete_version(version_id)`

**当前实现**（第 513-520 行）：
```python
if version_type == 'branch':
    # 只删除当前 collection 的数据
    cur.execute(
        'DELETE FROM dynamic_data WHERE collection = %s AND branch_id = %s',
        (collection, version_id)
    )

    # 已修复：清理正向和反向关联
    cur.execute(
        'DELETE FROM data_relations WHERE (collection = %s OR related_collection = %s) AND branch_id = %s',
        (collection, collection, version_id)
    )
```

**问题**：
- 版本创建时记录了 `collection`（单个）
- 删除时只清理该 `collection` 的数据
- 但版本中可能包含其他 collection 的关联数据

## 影响范围

### 直接影响

1. **数据残留**
   - 孤立数据占用存储空间
   - 无法通过版本管理访问
   - 数据库中存在无意义的行

2. **违反数据库设计原则**
   - 第一范式（原子性）：存在无意义的行
   - 第三范式（参照完整性）：数据指向不存在的版本

### 实际影响评估

**当前系统发现**：
```sql
-- 发现孤立数据
SELECT collection, branch_id
FROM dynamic_data
WHERE branch_id NOT IN (
    SELECT id FROM collection_versions WHERE version_type = 'branch'
)
AND branch_id != 'main'
```

结果：
- `inspection-plan` (branch: ver-9c69b7b0): 1 条（测试产生，已清理）

**使用频率**：
- 系统中存在 5 个分支版本
- 存在跨 Collection 关联：`inspection-case <-> inspection-plan`
- 如果频繁创建/删除版本，孤立数据会累积

## 解决方案（待讨论）

### 方案 1：简单粗暴（推荐短期方案）

**思路**：删除版本时，清理该分支的所有 Collection 数据

**优点**：
- 实现简单
- 确保数据一致性
- 性能开销小

**缺点**：
- 可能误删其他 Collection 的有用数据
- 需要明确告知用户影响范围

**实现**：
```python
# 删除版本时
cur.execute('DELETE FROM dynamic_data WHERE branch_id = %s', (version_id,))
cur.execute('DELETE FROM data_relations WHERE branch_id = %s', (version_id,))
cur.execute('DELETE FROM user_current_branch WHERE branch_id = %s', (version_id,))
```

### 方案 2：精确追踪（推荐长期方案）

**思路**：版本创建时记录涉及的所有 Collection

**优点**：
- 精确控制清理范围
- 避免误删数据
- 可追溯版本的数据范围

**缺点**：
- 实现复杂度高
- 需要修改版本创建逻辑
- 增加存储开销

**实现**：

1. 增加版本元数据表：
```sql
CREATE TABLE version_collections (
    version_id VARCHAR(255),
    collection VARCHAR(255),
    created_at TIMESTAMP,
    PRIMARY KEY (version_id, collection)
);
```

2. 创建版本时记录：
```python
def create_version_snapshot(collection, ...):
    # 收集涉及的所有 collection
    related_collections = find_related_collections(collection, branch_id)

    # 记录到 version_collections
    for coll in [collection] + related_collections:
        cur.execute(
            'INSERT INTO version_collections VALUES (%s, %s, %s)',
            (version_id, coll, now)
        )
```

3. 删除版本时精确清理：
```python
def delete_version(version_id):
    # 获取版本涉及的所有 collection
    cur.execute(
        'SELECT collection FROM version_collections WHERE version_id = %s',
        (version_id,)
    )
    collections = [row[0] for row in cur.fetchall()]

    # 清理每个 collection 的数据
    for coll in collections:
        cur.execute(
            'DELETE FROM dynamic_data WHERE collection = %s AND branch_id = %s',
            (coll, version_id)
        )

    # 清理关联关系
    cur.execute('DELETE FROM data_relations WHERE branch_id = %s', (version_id,))

    # 清理版本元数据
    cur.execute('DELETE FROM version_collections WHERE version_id = %s', (version_id,))
```

### 方案 3：限制跨 Collection 版本（保守方案）

**思路**：禁止在单个 Collection 版本中包含其他 Collection 的数据

**优点**：
- 避免问题产生
- 简化版本管理逻辑

**缺点**：
- 限制功能灵活性
- 用户体验不佳

**实现**：
- 在创建关联关系时检查是否跨 Collection 分支
- 如果跨 Collection，返回错误提示用户

### 方案 4：跨 Collection 版本管理（架构优化）

**思路**：重新设计版本管理，支持跨 Collection 版本

**优点**：
- 从根本上解决问题
- 支持更复杂的版本场景

**缺点**：
- 大幅重构
- 开发周期长
- 可能影响现有功能

**实现**：
- 引入"全局版本"概念
- 版本不再绑定单个 Collection
- 支持选择多个 Collection 一起版本化

## 临时缓解措施

### 已实施

1. **修复版本删除时的反向关联清理**
   - 文件：`server/utils/version.py` 第 515-519 行
   - 状态：✅ 已修复

2. **清理现有悬空数据**
   - 清理了 402 条悬空引用
   - 状态：✅ 已完成

3. **创建测试用例验证修复**
   - 文件：`server/tests/test_version_relation_cleanup.py`
   - 状态：✅ 测试通过

### 待实施

1. **增加孤立数据检测工具**
   - 定期检查数据库中的孤立数据
   - 记录日志或发送告警

2. **版本删除前的确认提示**
   - 提示用户版本可能涉及多个 Collection
   - 显示将被删除的数据统计

3. **文档说明**
   - 在用户手册中说明版本的作用域限制
   - 建议用户谨慎使用跨 Collection 关联的版本

## 决策建议

### 短期（1-2 周）

- 实施方案 1（简单粗暴）
- 增加用户确认提示
- 添加孤立数据检测工具

### 中期（1-2 个月）

- 收集用户反馈
- 评估孤立数据的实际影响
- 决定是否实施方案 2

### 长期（3-6 个月）

- 如果问题严重，实施方案 2 或方案 4
- 优化版本管理架构

## 相关资料

- 数据一致性分析报告：见本次分析记录
- 测试用例：`server/tests/test_version_relation_cleanup.py`
- 修复代码：`server/utils/version.py` 第 515-519 行

## 跟踪记录

| 日期 | 操作 | 结果 |
|------|------|------|
| 2026-04-08 | 发现问题 | 通过系统性分析发现跨 Collection 版本管理的孤立数据问题 |
| 2026-04-08 | 验证问题 | 发现 1 条孤立数据（测试产生） |
| 2026-04-08 | 修复部分问题 | 修复版本删除时的反向关联清理 |
| 2026-04-08 | 清理数据 | 清理了测试产生的孤立数据 |
| 2026-04-08 | 记录问题 | 创建本文档记录问题和解决方案 |
| 待定 | 讨论优化方案 | 需评估是否实施完整解决方案 |

## 附录：数据结构

### 相关表结构

```sql
-- 版本表
CREATE TABLE collection_versions (
    id VARCHAR(255) PRIMARY KEY,
    collection VARCHAR(255) NOT NULL,  -- ← 关键：单个 Collection
    name VARCHAR(255),
    version_type VARCHAR(50),  -- 'snapshot' | 'branch'
    ...
);

-- 动态数据表
CREATE TABLE dynamic_data (
    id VARCHAR(255),
    collection VARCHAR(255) NOT NULL,
    data JSONB NOT NULL,
    branch_id VARCHAR(255),  -- ← 关键：指向版本
    ...
);

-- 关联关系表
CREATE TABLE data_relations (
    collection VARCHAR(255) NOT NULL,
    record_id VARCHAR(255) NOT NULL,
    field_name VARCHAR(255) NOT NULL,
    related_collection VARCHAR(255) NOT NULL,  -- ← 关键：可能跨 Collection
    related_id VARCHAR(255) NOT NULL,
    branch_id VARCHAR(255),
    PRIMARY KEY (collection, record_id, field_name, related_id, branch_id)
);
```

### 关键约束

- 版本创建时绑定单个 Collection
- 关联关系可以跨 Collection
- 删除版本时只清理当前 Collection 的数据
- 导致其他 Collection 的关联数据残留