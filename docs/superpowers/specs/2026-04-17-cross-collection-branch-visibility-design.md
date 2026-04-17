# 跨 Collection 分支可见性设计文档

**日期：** 2026-04-17
**作者：** Claude Code
**状态：** 设计待审核

---

## 问题描述

当前系统存在分支管理与数据关联的版本显示问题：

### 现象
1. 数据A与数据B存在关联关系
2. 在数据A创建分支时，系统递归扫描并包含了关联的数据B
3. 在数据B的版本管理页面看不到该分支（仅数据A可见）
4. 从分支中的数据A跳转到数据B时：
   - 版本管理显示该分支存在
   - 但数据页面显示主分支数据（分支上下文丢失）

### 根本原因
- 分支记录存储在 `collection_versions` 表，每个分支只关联一个 `collection`
- 快照虽包含多个collection的数据，但分支元数据归属关系不明确
- 跳转时不携带分支上下文，导致目标页面使用错误的分支

---

## 用户需求

### 期望行为（选项1：共享分支所有权）

1. **分支可见性共享**：在数据A创建分支后，该分支同时出现在数据A和数据B的版本管理列表中
2. **跨collection标记**：分支显示"跨collection"图标或标签，点击展开参与的collection列表
3. **自动包含关联数据**：创建分支时自动递归扫描所有关联数据（无需用户手动选择）
4. **分支上下文传递**：跳转到关联数据时自动切换到相同分支

---

## 设计方案

### 方案选择：多记录方案

**核心思路：** 一个分支在 `collection_versions` 表中有多条记录（每个参与的collection一条）

**关键特征：**
- 所有记录使用相同的 `version_id`
- `collection` 字段不同（记录参与的各个collection）
- `parent_version` 字段区分primary/non-primary：
  - **Primary collection（创建点）**：`parent_version = 用户传入值`
    - 如果用户创建新分支（无父版本）：`parent_version = null`
    - 如果用户基于某版本创建分支：`parent_version = 父版本ID`
  - **Non-primary collection（关联数据）**：`parent_version = version_id`（自引用标记）
    - 自引用用于标识该记录是跨collection分支的参与者，而非创建点

**优点：**
- ✅ 查询简单：现有 `WHERE collection = ?` 无需修改
- ✅ 性能最优：索引直接命中，无需JSON解析
- ✅ 兼容性好：版本列表API几乎不变
- ✅ 删除/合并逻辑清晰：批量操作同一version_id的记录

---

## 实现细节

### 1. 数据库层面

**表结构：无需修改**

```sql
-- 现有表结构保持不变
CREATE TABLE collection_versions (
    id VARCHAR(50) PRIMARY KEY,
    collection VARCHAR(50),          -- 参与的collection
    name VARCHAR(100),
    description TEXT,
    version_type VARCHAR(20),        -- 'snapshot' or 'branch'
    parent_version VARCHAR(50),
    status VARCHAR(20),
    data_hash VARCHAR(64),
    records_count INTEGER,
    relations_count INTEGER,
    created_by VARCHAR(50),
    created_at TIMESTAMP,
    ...
);
```

**数据存储示例：**

| id | collection | name | version_type | parent_version | status |
|----|------------|------|--------------|----------------|--------|
| ver-abc123 | inspectionCases | "测试分支" | branch | null | active |
| ver-abc123 | inspectionTemplates | "测试分支" | branch | ver-abc123 | active |

**关键：**
- 同一个 `id`，不同 `collection`
- Primary通过 `parent_version` 自引用标记
- 查询版本列表：`WHERE collection = 'inspectionCases'`（不变）

---

### 2. 后端API层面

#### 2.1 `create_version_snapshot()` 改造

```python
def create_version_snapshot(collection, name, description, version_type,
                            parent_version, created_by, branch_id=None):
    """创建版本快照（支持多collection记录）"""
    version_id = f'ver-{uuid.uuid4().hex[:8]}'
    actual_branch_id = branch_id or MAIN_BRANCH_ID

    # 1. 递归扫描所有关联数据
    all_collections_data = scan_all_related_data(
        start_collection=collection,
        branch_id=actual_branch_id
    )

    # 2. 为每个参与的collection插入记录
    with get_db() as conn:
        cur = conn.cursor()

        for coll in all_collections_data.keys():
            records_count = len(all_collections_data[coll])
            relations_count = count_collection_relations(coll, actual_branch_id, cur)

            # Primary collection: parent_version = 用户传入值
            # Non-primary: parent_version = version_id (自引用)
            effective_parent = (
                parent_version if coll == collection else version_id
            )

            cur.execute(
                'INSERT INTO collection_versions '
                '(id, collection, name, description, version_type, parent_version, '
                'status, records_count, relations_count, created_by, created_at) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                (version_id, coll, name, description, version_type,
                 effective_parent, 'active', records_count, relations_count,
                 created_by, datetime.now(timezone.utc))
            )

        # 3. 插入快照数据（已有逻辑）
        ...

        # 4. 记录参与的collections
        track_version_collections(version_id, collection, actual_branch_id, conn,
                                  list(all_collections_data.keys()))

    return {
        'id': version_id,
        'collection': collection,  # Primary collection
        'collections': list(all_collections_data.keys()),  # 所有参与的collections
        'name': name,
        'versionType': version_type,
        ...
    }
```

#### 2.2 新增辅助函数

```python
def get_version_collections(version_id):
    """获取版本涉及的所有collections"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT collection FROM collection_versions WHERE id = %s',
            (version_id,)
        )
        return [row[0] for row in cur.fetchall()]

def get_version_collection_stats(version_id):
    """获取每个collection的记录数统计"""
    stats = {}
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT collection, records_count FROM collection_versions WHERE id = %s',
            (version_id,)
        )
        for row in cur.fetchall():
            stats[row[0]] = row[1]
    return stats

def get_primary_collection(version_id):
    """获取版本的主collection（创建点）"""
    with get_db() as conn:
        cur = conn.cursor()
        # Primary collection的parent_version不自引用（null或指向其他版本）
        cur.execute(
            'SELECT collection FROM collection_versions '
            'WHERE id = %s AND parent_version IS NULL OR parent_version != id',
            (version_id,)
        )
        row = cur.fetchone()
        return row[0] if row else None

def count_collection_relations(collection, branch_id, cur):
    """统计collection在指定分支的关联数量"""
    cur.execute(
        'SELECT COUNT(*) FROM data_relations '
        'WHERE collection = %s AND branch_id = %s',
        (collection, branch_id)
    )
    return cur.fetchone()[0]
```

#### 2.3 版本列表API

```python
@versions_bp.route('/versions', methods=['GET'])
def list_versions():
    collection = request.args.get('collection')

    versions = get_version_list(collection=collection)

    # 聚合collections信息
    for v in versions:
        v['collections'] = get_version_collections(v['id'])
        v['collectionStats'] = get_version_collection_stats(v['id'])

    return jsonify(versions)
```

#### 2.4 版本详情API

```python
@versions_bp.route('/versions/<version_id>', methods=['GET'])
def get_version(version_id):
    version = get_version_detail(version_id)

    # 添加跨collection信息
    version['collections'] = get_version_collections(version_id)
    version['collectionStats'] = get_version_collection_stats(version_id)

    return jsonify(version)
```

#### 2.5 分支切换改造

```python
def switch_to_version(version_id, switched_by='unknown', user_id=None):
    """切换到指定分支（多collection同步切换）"""

    # 1. 获取所有参与的collections
    all_collections = get_version_collections(version_id)

    # 2. 为每个collection设置用户当前分支
    for coll in all_collections:
        set_user_current_branch(user_id, switched_by, coll, version_id)

    # 3. 首次切换：从快照初始化数据
    primary_collection = get_primary_collection(version_id)
    data_count = get_branch_data_count(primary_collection, version_id)

    if data_count == 0:
        for coll in all_collections:
            initialize_branch_from_snapshot(version_id, coll)

    return {
        'success': True,
        'branchId': version_id,
        'affectedCollections': all_collections,
        'initialized': data_count > 0,
        ...
    }
```

#### 2.6 删除版本逻辑

```python
def delete_version(version_id, confirmed=False):
    """删除版本（级联删除所有collection记录）"""

    # 1. 获取所有参与的collections
    collections = get_version_collections(version_id)

    # 2. 检查是否有用户在使用
    users_using = []
    for coll in collections:
        users = get_users_on_branch(coll, version_id)
        users_using.extend(users)

    if users_using:
        raise ValueError(f'有用户正在使用该分支：{", ".join(users_using)}')

    # 3. 未确认：返回影响报告
    if not confirmed:
        return {
            'versionId': version_id,
            'collections': collections,
            'totalRecords': sum(get_branch_data_count(c, version_id) for c in collections),
            'requiresConfirmation': True
        }

    # 4. 确认后执行删除
    with get_db() as conn:
        cur = conn.cursor()

        # 删除所有collection_versions记录
        cur.execute('DELETE FROM collection_versions WHERE id = %s', (version_id,))

        # 删除所有collection的分支数据
        for coll in collections:
            cur.execute(
                'DELETE FROM dynamic_data WHERE collection = %s AND branch_id = %s',
                (coll, version_id)
            )
            cur.execute(
                'DELETE FROM data_relations WHERE collection = %s AND branch_id = %s',
                (coll, version_id)
            )

        # 删除快照
        cur.execute('DELETE FROM version_snapshots WHERE version_id = %s', (version_id,))
        cur.execute('DELETE FROM version_relations WHERE version_id = %s', (version_id,))

        return True
```

---

### 3. 前端展示层面

#### 3.1 TypeScript类型定义

```typescript
// src/types/version.ts
export interface CollectionVersion {
  id: string
  collection: string          // Primary collection（创建点）
  collections: string[]       // 所有参与的collections（新增）
  collectionStats?: Record<string, number>  // 每个collection的记录数
  name: string
  description: string
  versionType: 'snapshot' | 'branch'
  status: 'active' | 'merged' | 'deleted'
  recordsCount: number        // 总记录数
  relationsCount: number
  createdAt: string
  createdBy: string
  parentVersion: string | null
}
```

#### 3.2 版本列表组件

```vue
<template>
  <el-table :data="versions">
    <el-table-column prop="name" label="名称">
      <template #default="{ row }">
        <div class="version-name-cell">
          <!-- 跨collection图标 -->
          <el-icon
            v-if="row.collections && row.collections.length > 1"
            class="cross-collection-icon"
          >
            <Link />
          </el-icon>
          <span>{{ row.name }}</span>

          <el-tag :type="row.versionType === 'branch' ? 'primary' : 'info'" size="small">
            {{ row.versionType === 'branch' ? '分支' : '快照' }}
          </el-tag>
        </div>
      </template>
    </el-table-column>

    <el-table-column prop="collections" label="涉及的Collection">
      <template #default="{ row }">
        <!-- 单collection：直接显示 -->
        <span v-if="!row.collections || row.collections.length === 1">
          {{ formatCollectionName(row.collection) }}
        </span>

        <!-- 多collection：Popover展示 -->
        <el-popover v-else placement="right" :width="280" trigger="hover">
          <template #reference>
            <el-button type="primary" link size="small">
              <el-icon><Link /></el-icon>
              {{ row.collections.length }} 个Collection
            </el-button>
          </template>

          <div class="collections-popover">
            <div class="popover-title">参与的Collection：</div>
            <div class="collection-list">
              <div v-for="coll in row.collections" :key="coll" class="collection-item">
                <el-icon v-if="coll === row.collection" color="#409EFF">
                  <Star />
                </el-icon>
                <span>{{ formatCollectionName(coll) }}</span>
                <span class="record-count">
                  ({{ row.collectionStats?.[coll] || 0 }}条)
                </span>
              </div>
            </div>
          </div>
        </el-popover>
      </template>
    </el-table-column>

    <!-- 其他列保持不变 -->
  </el-table>
</template>

<style scoped>
.cross-collection-icon {
  color: #409EFF;
  margin-right: 6px;
}

.collections-popover {
  .popover-title {
    font-weight: bold;
    margin-bottom: 8px;
  }

  .collection-item {
    display: flex;
    align-items: center;
    padding: 4px 0;
    gap: 6px;
  }

  .record-count {
    color: #909399;
    font-size: 12px;
  }
}
</style>
```

#### 3.3 跳转时携带分支上下文

**JumpStore改造：**

```typescript
// src/stores/jump.ts
interface JumpIntent {
  targetCollection: string
  targetRecordId: string
  jumpType: 'relation' | 'reference' | 'quote'
  sourcePageId: string
  timestamp: number
  branchId?: string  // 新增：分支上下文
}

export const useJumpStore = defineStore('jump', {
  state: () => ({
    intent: null as JumpIntent | null,
    history: [] as JumpSource[],
  }),

  actions: {
    setJump(intent: Omit<JumpIntent, 'branchId'>, source: JumpSource, branchId?: string) {
      // 从当前页面获取分支信息
      const authStore = useAuthStore()
      const currentBranch = authStore.currentBranch

      this.intent = {
        ...intent,
        branchId: branchId || currentBranch?.branchId || 'main',  // 自动填充
      }

      this.history.push(source)
    },
  },
})
```

**DynamicPage接收跳转意图：**

```typescript
// src/views/dynamic/DynamicPage.vue
onMounted(async () => {
  await loadPageConfig()
  await loadCurrentBranch()

  // 处理跳转意图
  const intent = jumpStore.consumeJump()

  if (intent?.branchId && intent.branchId !== 'main') {
    // 自动切换到跳转携带的分支
    try {
      await switchToVersion(intent.branchId)
      await loadCurrentBranch()
      ElMessage.success(`已切换到分支：${currentBranch.value?.branchName}`)
    } catch (error) {
      console.error('分支切换失败:', error)
      ElMessage.warning('跳转携带的分支不存在或已失效')
    }
  }

  // 定位记录
  if (intent?.targetRecordId) {
    intelligentLocateRecord(intent.targetRecordId)
  }
})
```

---

### 4. 数据一致性保证

#### 4.1 引用完整性验证

```python
def validate_relation_target(collection, record_id, field_name, related_id, branch_id):
    """验证关联目标记录是否在当前分支存在"""
    field_config = get_field_config(collection, field_name)

    if field_config['controlType'] == 'relation':
        target_collection = field_config['relationConfig']['targetCollection']

        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'SELECT id FROM dynamic_data '
                'WHERE collection = %s AND id = %s AND branch_id = %s',
                (target_collection, related_id, branch_id)
            )

            if not cur.fetchone():
                raise ValueError(
                    f'关联目标记录 {related_id} 在当前分支不存在。'
                    f'请先在 {target_collection} 切换到相同分支。'
                )
```

#### 4.2 删除分支影响报告

在删除前向用户展示完整影响范围：
- 所有参与的collections
- 每个collection的记录数
- 是否有用户正在使用
- 快照数据是否已被合并

---

### 5. 测试策略

#### 5.1 后端单元测试

```python
# server/tests/test_cross_collection_branch.py

def test_create_branch_with_relations():
    """验证跨collection分支创建"""
    branch = create_version_snapshot(collection='inspectionCases', ...)
    assert 'inspectionTemplates' in branch['collections']

def test_switch_cross_collection_branch():
    """验证切换时同步更新所有collections"""
    switch_to_version(branch_id, user_id='user-001')
    assert get_user_current_branch('user-001', 'inspectionTemplates') == branch_id

def test_delete_cross_collection_branch():
    """验证删除时级联清理"""
    delete_version(branch_id, confirmed=True)
    assert branch_id not in get_collection_versions('inspectionTemplates')

def test_jump_with_branch_context():
    """验证跳转携带分支信息（需前端配合）"""
    # 测试API层面是否正确返回参与的collections
    branch = get_version_detail(branch_id)
    assert branch['collections'] == ['inspectionCases', 'inspectionTemplates']
```

#### 5.2 前端集成测试

```typescript
// src/views/dynamic/__tests__/DynamicPage.test.ts

describe('跨collection分支', () => {
  it('跳转应携带分支上下文', async () => {
    await switchBranch('branch-001')
    handleRelationClick(relatedId, relationField)

    const intent = jumpStore.getJump()
    expect(intent.branchId).toBe('branch-001')
  })

  it('加载时应自动切换到跳转分支', async () => {
    jumpStore.setJump({
      targetCollection: 'collectionB',
      targetRecordId: 'record-123',
      branchId: 'branch-001'
    })

    mount(DynamicPage)
    await nextTick()

    expect(currentBranch.value.branchId).toBe('branch-001')
  })
})
```

---

### 6. 数据迁移计划

#### 6.1 迁移脚本

```python
# server/migrations/migrate_cross_collection_branches.py

def migrate_existing_branches():
    """迁移现有分支到跨collection模式"""
    with get_db() as conn:
        cur = conn.cursor()

        # 获取所有现有分支
        cur.execute(
            "SELECT id, collection FROM collection_versions "
            "WHERE version_type = 'branch' AND status = 'active'"
        )
        branches = cur.fetchall()

        for branch_id, primary_collection in branches:
            # 递归扫描关联数据
            try:
                all_data = scan_all_related_data(
                    start_collection=primary_collection,
                    branch_id=branch_id,
                    max_records=5000
                )
            except ValueError:
                continue

            # 为每个参与的collection补充记录
            for coll in all_data.keys():
                if coll == primary_collection:
                    continue

                # 检查是否已存在
                cur.execute(
                    'SELECT id FROM collection_versions '
                    'WHERE id = %s AND collection = %s',
                    (branch_id, coll)
                )
                if cur.fetchone():
                    continue

                # 插入记录（parent_version自引用）
                cur.execute(
                    'INSERT INTO collection_versions '
                    '(id, collection, name, description, version_type, parent_version, ...) '
                    'SELECT id, %s, name, description, version_type, id, ... '
                    'FROM collection_versions WHERE id = %s LIMIT 1',
                    (coll, branch_id)
                )
```

#### 6.2 迁移步骤

1. **备份数据**：导出 `collection_versions` 表
2. **运行迁移**：`python server/migrations/migrate_cross_collection_branches.py`
3. **验证结果**：
   - 版本列表显示跨collection分支
   - 切换功能正常
   - 跳转携带分支上下文
4. **部署前端**：部署新版本前端代码
5. **监控运行**：观察一周确保无异常

#### 6.3 回滚策略

```python
def rollback_cross_collection_migration():
    """回滚迁移"""
    with get_db() as conn:
        cur = conn.cursor()
        # 删除parent_version自引用的记录
        cur.execute("DELETE FROM collection_versions WHERE parent_version = id")
```

---

## 影响范围评估

### 改动的文件

**后端：**
- `server/utils/version.py`：`create_version_snapshot`, `switch_to_version`, `delete_version`
- `server/routes/versions.py`：版本列表和详情API
- `server/migrations/migrate_cross_collection_branches.py`：新增迁移脚本

**前端：**
- `src/types/version.ts`：类型定义
- `src/stores/jump.ts`：跳转意图携带分支信息
- `src/views/dynamic/DynamicPage.vue`：加载时自动切换分支
- `src/components/VersionList.vue`（或类似组件）：跨collection标记展示

**数据库：**
- 无表结构改动（仅数据迁移）

---

## 风险与挑战

### 风险1：数据一致性
- **场景**：用户在分支A修改关联字段指向主分支的记录
- **对策**：引用完整性验证（修改前检查目标记录是否在同一分支）

### 风险2：性能影响
- **场景**：跨collection分支包含大量数据
- **对策**：`scan_all_related_data` 已有 `max_records` 限制（10000条）

### 风险3：用户体验
- **场景**：用户不理解跨collection概念
- **对策**：清晰的图标 + Popover说明 + 文档更新

---

## 总结

本设计采用**多记录方案**实现跨collection分支可见性共享：

1. **数据层**：一个分支多条记录，相同 `version_id`，不同 `collection`
2. **API层**：批量插入、聚合查询、级联删除
3. **前端层**：跨collection标记展示、跳转携带分支上下文、自动切换
4. **迁移**：递归扫描现有分支并补充缺失记录
5. **测试**：单元测试 + 集成测试覆盖核心场景

改动集中、逻辑清晰、性能最优、向后兼容，能够完整解决用户报告的问题。