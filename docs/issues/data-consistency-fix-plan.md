# 数据一致性修复方案

## 执行摘要

通过系统性分析，发现系统存在**严重的数据一致性问题**，违反数据库三范式。已验证存在 5 条外键悬空记录，另有 4 个高风险场景可能导致数据残留。

## 问题优先级

### P0 - 立即修复（已验证存在）

**问题 2：外键悬空**
- 影响：5 条 `data_relations` 记录指向不存在的记录
- 修复成本：低
- 修复时间：< 1小时

### P1 - 高优先级（高风险场景）

**问题 1：删除 relation 字段后的残留数据**
- 影响：402 条潜在残留数据
- 修复成本：中
- 修复时间：2-3小时

**问题 3：删除 page_config 后的数据残留**
- 影响：整个 collection 的数据残留
- 修复成本：中
- 修复时间：2-3小时

**问题 4：修改 targetCollection 后的语义错误**
- 影响：数据语义错误，查询结果不正确
- 修复成本：高
- 修复时间：4-6小时

### P2 - 中优先级

**问题 5：删除 reference 字段后依赖检查失效**
- 影响：可能删除被引用的父记录
- 修复成本：中
- 修复时间：2-3小时

## 详细修复方案

### 方案 1：立即清理外键悬空（P0）

**修复内容**：
```sql
-- 清理 data_relations 中的悬空引用
DELETE FROM data_relations dr
WHERE NOT EXISTS (
    SELECT 1 FROM dynamic_data dd
    WHERE dd.id = dr.related_id
    AND dd.collection = dr.related_collection
);
```

**防御措施**：
- 在删除记录时，同时清理反向关联
- 修改 `routes/dynamic.py` 的 `delete_item` 函数，增加反向关联清理

**影响范围**：
- 仅影响数据清理，不影响正常业务
- 需要通知用户有数据被清理

### 方案 2：字段删除时的级联清理（P1）

**修复内容**：
- 修改 `routes/page_configs.py` 的 `update_page_config` 函数
- 当检测到 relation/reference/quoteSelect 字段被删除时：
  1. 查询该字段的所有关联数据
  2. 删除 `data_relations` 中的相关记录
  3. 记录操作日志

**实现要点**：
```python
# 伪代码
def update_page_config(config_id):
    old_fields = get_current_fields(config_id)
    new_fields = request.body.get('fields', [])

    deleted_relation_fields = find_deleted_relation_fields(old_fields, new_fields)

    for field in deleted_relation_fields:
        collection = config_id.replace('page-', '')
        field_name = field['fieldName']

        # 删除关联数据
        DELETE FROM data_relations
        WHERE collection = collection
        AND field_name = field_name

        # 记录日志
        log_operation('cascade_delete', 'relation', ...)
```

**影响范围**：
- 影响字段配置更新逻辑
- 需要严格的测试
- 需要提示用户数据将被清理

### 方案 3：page_config 删除时的级联清理（P1）

**修复内容**：
- 修改 `routes/page_configs.py` 的 `delete_page_config` 函数
- 删除 page_config 前：
  1. 获取 collection 名称
  2. 删除 `dynamic_data` 中该 collection 的所有数据
  3. 删除 `data_relations` 中涉及该 collection 的所有关系
  4. 删除 `menus` 中的相关菜单项

**实现要点**：
```python
def delete_page_config(config_id):
    collection = config_id.replace('page-', '')

    with get_db() as conn:
        cur = conn.cursor()

        # 1. 删除关联关系
        cur.execute(
            'DELETE FROM data_relations WHERE collection = %s OR related_collection = %s',
            (collection, collection)
        )

        # 2. 删除业务数据
        cur.execute(
            'DELETE FROM dynamic_data WHERE collection = %s',
            (collection,)
        )

        # 3. 删除菜单
        cur.execute(
            'DELETE FROM menus WHERE page_id = %s',
            (config_id,)
        )

        # 4. 删除配置
        cur.execute(
            'DELETE FROM page_configs WHERE id = %s',
            (config_id,)
        )
```

**影响范围**：
- 严重影响，会删除大量数据
- 必须提示用户确认
- 建议增加"预检查"功能，显示将要删除的数据统计

### 方案 4：禁止修改 targetCollection（P1）

**修复内容**：
- 在 `update_page_config` 中检测 `relationConfig.targetCollection` 的修改
- 如果检测到修改，返回错误提示用户：
  - 旧字段必须先删除（级联清理数据）
  - 然后创建新字段指向新的 collection

**理由**：
- 修改 targetCollection 会导致历史数据语义错误
- 技术上可以迁移数据，但成本高、风险大
- 禁止修改更安全

**实现要点**：
```python
def update_page_config(config_id):
    old_fields = get_current_fields(config_id)
    new_fields = request.body.get('fields', [])

    for old_field, new_field in zip(old_fields, new_fields):
        if old_field['controlType'] == 'relation':
            old_target = old_field.get('relationConfig', {}).get('targetCollection')
            new_target = new_field.get('relationConfig', {}).get('targetCollection')

            if old_target != new_target:
                return jsonify({
                    "error": f"禁止修改字段 '{old_field['fieldName']}' 的目标集合。请先删除该字段，再创建新字段。"
                }), 400
```

**影响范围**：
- 限制用户操作，但更安全
- 需要清晰的错误提示

### 方案 5：reference 字段删除时的依赖检查（P2）

**修复内容**：
- 修改 `update_page_config` 函数
- 检测到 reference 字段被删除时：
  1. 查询所有使用该字段的子记录
  2. 阻止删除或提示用户先清理子记录

**实现要点**：
```python
def update_page_config(config_id):
    old_fields = get_current_fields(config_id)
    new_fields = request.body.get('fields', [])

    deleted_reference_fields = find_deleted_reference_fields(old_fields, new_fields)

    for field in deleted_reference_fields:
        collection = config_id.replace('page-', '')
        field_name = field['fieldName']

        # 检查是否有子记录使用该字段
        cur.execute(
            'SELECT COUNT(*) FROM dynamic_data WHERE collection = %s AND data->%s IS NOT NULL',
            (collection, field_name)
        )
        count = cur.fetchone()[0]

        if count > 0:
            return jsonify({
                "error": f"无法删除引用字段 '{field_name}'，存在 {count} 条子记录使用该字段"
            }), 400
```

## 数据库约束建议

为了从根本上解决问题，建议增加数据库约束：

### 建议 1：外键约束（长期目标）

```sql
-- 添加外键约束（需要评估性能影响）
ALTER TABLE data_relations
ADD CONSTRAINT fk_source_record
FOREIGN KEY (collection, record_id, branch_id)
REFERENCES dynamic_data (collection, id, branch_id)
ON DELETE CASCADE;

ALTER TABLE data_relations
ADD CONSTRAINT fk_target_record
FOREIGN KEY (related_collection, related_id, branch_id)
REFERENCES dynamic_data (collection, id, branch_id)
ON DELETE CASCADE;
```

**优点**：
- 数据库级别保证一致性
- 自动级联删除

**缺点**：
- 可能影响性能
- 需要全面测试

### 建议 2：触发器（中期目标）

```sql
-- 创建触发器自动清理关联数据
CREATE OR REPLACE FUNCTION cleanup_relations()
RETURNS TRIGGER AS $$
BEGIN
    DELETE FROM data_relations
    WHERE (collection = OLD.collection AND record_id = OLD.id)
       OR (related_collection = OLD.collection AND related_id = OLD.id);
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_cleanup_relations
BEFORE DELETE ON dynamic_data
FOR EACH ROW EXECUTE FUNCTION cleanup_relations();
```

**优点**：
- 自动清理，无需修改应用代码
- 保证数据一致性

**缺点**：
- 增加数据库负担
- 可能影响删除性能

## 实施计划

### 阶段 1：紧急修复（本周）

1. **清理现有外键悬空**（P0）
   - 执行清理脚本
   - 验证清理结果
   - 通知用户

2. **修复删除记录时的反向关联清理**（P0）
   - 修改 `routes/dynamic.py`
   - 增加测试用例
   - 部署上线

### 阶段 2：防御性修复（下周）

1. **实现字段删除级联清理**（P1）
   - 修改 `routes/page_configs.py`
   - 增加操作确认提示
   - 全面测试

2. **实现 page_config 删除级联清理**（P1）
   - 修改 `routes/page_configs.py`
   - 增加预检查功能
   - 全面测试

3. **禁止修改 targetCollection**（P1）
   - 增加验证逻辑
   - 提供清晰的错误提示

### 阶段 3：完善和优化（后续迭代）

1. **reference 字段删除检查**（P2）
2. **数据库触发器**（可选）
3. **外键约束**（长期目标）

## 风险评估

### 高风险操作

1. **级联删除数据**
   - 风险：误删重要数据
   - 缓解：增加确认提示，记录详细日志

2. **禁止修改 targetCollection**
   - 风险：限制用户灵活性
   - 缓解：提供清晰的迁移指南

### 测试要求

1. **单元测试**
   - 每个修复点必须有对应的测试用例
   - 覆盖边界场景

2. **集成测试**
   - 测试完整的删除流程
   - 验证数据一致性

3. **回归测试**
   - 确保修复不影响现有功能
   - 性能测试

## 验收标准

1. **P0 问题修复**
   - [ ] 清理所有外键悬空
   - [ ] 删除记录时自动清理反向关联
   - [ ] 测试通过

2. **P1 问题修复**
   - [ ] 删除 relation 字段时自动清理关联数据
   - [ ] 删除 page_config 时自动清理所有相关数据
   - [ ] 禁止修改 targetCollection，返回明确错误
   - [ ] 测试通过

3. **文档完善**
   - [ ] 更新系统文档，说明数据一致性保证机制
   - [ ] 用户手册中增加相关说明

## 总结

当前系统存在**严重的数据一致性问题**，已验证有 5 条外键悬空记录。建议按照优先级分阶段修复，先解决已存在的问题，再防御性修复潜在风险。

修复过程需要严格遵守测试流程，确保不影响现有功能。长期目标是引入数据库级别的约束，从根本上保证数据一致性。