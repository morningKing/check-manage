# 版本合并冲突解决功能设计

## 背景

当前版本合并功能仅支持全量覆盖策略（`theirs` 或 `ours`），无法像 Git Merge 那样针对差异点选择要合并的内容。用户需要更精细的合并控制能力。

## 目标

设计并实现类似 Git Merge 的版本合并功能，支持：
- 记录级合并选择（选择要合并的新增/删除/修改记录）
- 字段级合并选择（对修改记录逐字段选择保留哪个值）
- 强制用户明确选择冲突字段的值

## 架构设计

```
src/components/common/MergeConflictDialog.vue (新建)
├── StepOverview.vue        # 步骤1：变更概览
├── StepRecordSelect.vue    # 步骤2：记录级选择
├── StepFieldSelect.vue     # 步骤3：字段级选择
└── composables/
    └── useMergeState.ts    # 合并状态管理

server/routes/versions.py
└── POST /api/versions/partial-merge  # 新增部分合并 API

server/utils/version.py
├── compute_diff()          # 已有，复用
└── apply_partial_merge()   # 新增，应用部分合并
```

### 组件职责

| 组件 | 职责 |
|------|------|
| `MergeConflictDialog` | 容器组件，管理步骤流转和最终提交 |
| `StepOverview` | 展示变更统计，提供快捷操作按钮 |
| `StepRecordSelect` | 记录列表，支持批量选择 |
| `StepFieldSelect` | 字段级差异展示和选择 |
| `useMergeState` | 封装合并决策状态，提供计算属性和方法 |

## 组件交互流程

```
┌─────────────────────────────────────────────────────────────┐
│                    MergeConflictDialog                       │
├─────────────────────────────────────────────────────────────┤
│  步骤指示器: [①概览] → [②记录选择] → [③字段选择]          │
├─────────────────────────────────────────────────────────────┤
│              当前步骤内容区域                                │
│         (StepOverview / StepRecordSelect /                  │
│                    StepFieldSelect)                         │
├─────────────────────────────────────────────────────────────┤
│  [取消]              [上一步]      [下一步/完成]            │
└─────────────────────────────────────────────────────────────┘
```

### 状态定义

```typescript
interface MergeState {
  step: 'overview' | 'records' | 'fields'

  // 源版本和目标版本信息
  sourceVersion: VersionInfo
  targetBranch: string

  // diff 结果
  diffResult: DiffResult

  // 用户决策
  decisions: {
    addedRecords: Set<string>       // 选择合并的新增记录 ID
    removedRecords: Set<string>     // 选择删除的记录 ID
    modifiedRecords: Map<string, {  // 修改记录的字段决策
      recordId: string
      fieldDecisions: Map<string, 'source' | 'target'>
    }>
  }
}
```

### 步骤行为

| 步骤 | 用户操作 | 状态更新 |
|------|----------|----------|
| 概览 | 点击"全部接受源"/"全部接受目标" | 预填充 decisions |
| 记录选择 | 勾选/取消勾选记录 | 更新 decisions.addedRecords/removedRecords/modifiedRecords |
| 字段选择 | 对修改记录逐字段选择值 | 更新 fieldDecisions |

### 步骤导航规则

- **允许跳过步骤**：用户可以从概览直接跳到字段选择，或跳过任意步骤
- **回退保留选择**：返回上一步时，已做的选择保留不丢失
- **空合并禁用提交**：当用户未选择任何变更时，提交按钮禁用并显示提示"请至少选择一项变更"

## API 设计

### 新增端点

`POST /api/versions/partial-merge`

**请求体**：

```typescript
interface PartialMergeRequest {
  source_version_id: string
  target_branch: string
  decisions: {
    added_record_ids: string[]      // 要合并的新增记录
    removed_record_ids: string[]    // 要删除的记录
    modified_records: {
      record_id: string
      field_values: {               // 字段级合并结果
        [fieldName: string]: any    // 用户选择的最终值
      }
    }[]
  }
}
```

**响应体**：

```typescript
interface PartialMergeResponse {
  success: boolean
  merged_count: number
  message: string
}
```

### 后端处理流程

```python
def apply_partial_merge(source_version_id, target_branch, decisions):
    """
    应用部分合并决策（事务包装，部分失败则回滚）
    """
    with get_db() as conn:
        try:
            # 1. 获取源版本快照数据
            source_data = get_snapshot_data(source_version_id)

            # 2. 处理新增记录（同步处理关系数据）
            for record_id in decisions.added_record_ids:
                record = source_data.find(record_id)
                insert_to_branch(record, target_branch)
                # 复制该记录的关系数据
                copy_relations(record_id, source_version_id, target_branch)

            # 3. 处理删除记录（同步删除关系数据）
            for record_id in decisions.removed_record_ids:
                delete_from_branch(record_id, target_branch)
                # 删除该记录的关系数据
                delete_relations(record_id, target_branch)

            # 4. 处理修改记录
            for mod in decisions.modified_records:
                update_record_fields(
                    mod.record_id,
                    mod.field_values,
                    target_branch
                )

            # 5. 更新 data_hash
            recalculate_branch_hash(target_branch)

            conn.commit()
            return {'success': True, 'merged_count': len(...)}

        except Exception as e:
            conn.rollback()
            raise MergeError('MERGE_FAILED', f'合并失败: {str(e)}')
```

### 关系数据处理

合并记录时需同步处理 `data_relations` 表：

| 操作 | 关系数据处理 |
|------|-------------|
| 新增记录 | 从源版本复制该记录的所有关系到目标分支 |
| 删除记录 | 删除目标分支中该记录的所有关系 |
| 修改记录 | 保留现有关系，仅更新字段值（关系不随字段变化） |

### 事务原子性

部分合并操作使用数据库事务包装：
- 所有操作（新增、删除、修改、关系处理）在同一事务中执行
- 任一操作失败，全部回滚
- 保证数据一致性，避免部分成功导致的数据损坏

## 前端状态管理

### useMergeState composable

```typescript
export function useMergeState() {
  const state = reactive<MergeState>({
    step: 'overview',
    sourceVersion: null,
    targetBranch: '',
    diffResult: null,
    decisions: {
      addedRecords: new Set(),
      removedRecords: new Set(),
      modifiedRecords: new Map()
    }
  })

  // 计算属性
  const hasChanges = computed(() =>
    state.decisions.addedRecords.size > 0 ||
    state.decisions.removedRecords.size > 0 ||
    state.decisions.modifiedRecords.size > 0
  )

  const canSubmit = computed(() => hasChanges.value)

  // 提交按钮状态
  // :disabled="!canSubmit"
  // 禁用时显示提示："请至少选择一项变更"

  const modifiedRecordCount = computed(() =>
    state.diffResult?.modified?.length ?? 0
  )

  // 快捷操作
  function acceptAllSource() {
    // 新增记录：全部接受
    // 删除记录：全部接受
    // 修改记录：全部选源版本
  }

  function acceptAllTarget() {
    // 新增记录：全部拒绝（不合并）
    // 删除记录：全部拒绝（保留）
    // 修改记录：全部选目标版本
  }

  // 提交合并
  async function submitMerge() {
    const payload = buildMergePayload(state)
    return await api.partialMerge(payload)
  }

  return {
    state,
    hasChanges,
    canSubmit,
    modifiedRecordCount,
    acceptAllSource,
    acceptAllTarget,
    submitMerge
  }
}
```

## 错误处理

### 前端错误码

| 错误码 | 说明 |
|--------|------|
| `LOAD_DIFF_FAILED` | 加载差异信息失败 |
| `MERGE_CONFLICT` | 合并过程中发生冲突 |
| `NETWORK_ERROR` | 网络连接失败 |
| `PERMISSION_DENIED` | 无权限执行此操作 |
| `VERSION_NOT_FOUND` | 源版本不存在或已被删除 |
| `BRANCH_CHANGED` | 当前分支已变更 |

### 后端错误处理

```python
class MergeError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message

def apply_partial_merge(...):
    try:
        # 验证源版本存在
        source = get_version(source_version_id)
        if not source:
            raise MergeError('VERSION_NOT_FOUND', '源版本不存在')

        # 验证目标分支
        if not branch_exists(target_branch):
            raise MergeError('BRANCH_NOT_FOUND', '目标分支不存在')

        # 验证权限
        if not has_merge_permission(user, target_branch):
            raise MergeError('PERMISSION_DENIED', '无合并权限')

        # 执行合并...

    except MergeError as e:
        return {'success': False, 'code': e.code, 'message': e.message}
```

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/components/common/MergeConflictDialog.vue` | 新建 | 合并冲突对话框主组件 |
| `src/components/common/StepOverview.vue` | 新建 | 变更概览步骤组件 |
| `src/components/common/StepRecordSelect.vue` | 新建 | 记录选择步骤组件 |
| `src/components/common/StepFieldSelect.vue` | 新建 | 字段选择步骤组件 |
| `src/components/common/composables/useMergeState.ts` | 新建 | 合并状态管理 composable |
| `src/api/version.ts` | 修改 | 添加 partialMerge API |
| `src/types/version.ts` | 修改 | 添加相关类型定义 |
| `src/components/common/VersionManager.vue` | 修改 | 集成新的合并对话框 |
| `server/routes/versions.py` | 修改 | 添加部分合并 API 端点 |
| `server/utils/version.py` | 修改 | 添加 apply_partial_merge 函数 |

## 测试要点

1. **记录级选择**：验证新增/删除记录的选择和合并
2. **字段级选择**：验证修改记录的字段级合并
3. **快捷操作**：验证"全部接受源"/"全部接受目标"功能
4. **错误处理**：验证各种错误场景的提示
5. **权限控制**：验证无权限用户的操作限制