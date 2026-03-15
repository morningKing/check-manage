# Merge Interaction Redesign Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign version merge UI from a three-step wizard to a single-page unified view.

**Architecture:** Replace step-based navigation with collapsible sections. Each record type (added/removed/modified) has its own section with inline selection. Modified records support expandable field-level selection.

**Tech Stack:** Vue 3 Composition API, TypeScript, Element Plus

---

## File Structure

| File | Action | Purpose |
|------|--------|---------|
| `src/types/version.ts` | Modify | Remove MergeStep, add expandedRecords to MergeState |
| `src/components/common/composables/useMergeState.ts` | Modify | Remove step logic, add expandedRecords management |
| `src/components/common/MergeRecordSection.vue` | Create | Section for added/removed records |
| `src/components/common/MergeModifiedSection.vue` | Create | Section for modified records with field selection |
| `src/components/common/MergeConflictDialog.vue` | Rewrite | Main dialog with single-page layout |
| `src/components/common/index.ts` | Modify | Export new components, remove old ones |
| `src/components/common/StepOverview.vue` | Delete | No longer needed |
| `src/components/common/StepRecordSelect.vue` | Delete | No longer needed |
| `src/components/common/StepFieldSelect.vue` | Delete | No longer needed |

---

## Chunk 1: Types and Composable Updates

### Task 1: Update MergeState Type

**Files:**
- Modify: `src/types/version.ts:168-174`

- [ ] **Step 1: Remove MergeStep type and update MergeState**

Remove the `MergeStep` type and update `MergeState` interface:

```typescript
// Remove this line:
export type MergeStep = 'overview' | 'records' | 'fields'

// Update MergeState interface:
export interface MergeState {
  sourceVersion: CollectionVersion | null
  targetBranch: string
  diffResult: DiffResult | null
  decisions: MergeDecisions
  expandedRecords: Set<string>  // IDs of modified records with expanded field panels
}
```

- [ ] **Step 2: Run TypeScript check**

Run: `npx vue-tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add src/types/version.ts
git commit -m "refactor: remove MergeStep, add expandedRecords to MergeState"
```

---

### Task 2: Simplify useMergeState Composable

**Files:**
- Modify: `src/components/common/composables/useMergeState.ts`

- [ ] **Step 1: Remove step-related code and add expandedRecords**

Rewrite the composable to remove step navigation and add expandedRecords management:

```typescript
/**
 * useMergeState - 合并决策状态管理 Composable
 *
 * 管理版本合并过程中的决策状态，包括：
 * - 差异结果
 * - 用户决策（新增、删除、修改记录的选择）
 * - 展开状态管理
 */
import { reactive, computed } from 'vue'
import { partialMergeVersion } from '@/api/version'
import type {
  MergeState,
  MergeDecisions,
  CollectionVersion,
  PartialMergeRequest,
  PartialMergeDecisions,
  ModifiedRecordDecision,
} from '@/types/version'
import type { DiffResult } from '@/types/backup'

/**
 * 创建空的决策对象
 */
function createEmptyDecisions(): MergeDecisions {
  return {
    addedRecords: new Set<string>(),
    removedRecords: new Set<string>(),
    modifiedRecords: new Map(),
  }
}

/**
 * 创建初始合并状态
 */
function createInitialState(): MergeState {
  return {
    sourceVersion: null,
    targetBranch: 'current',
    diffResult: null,
    decisions: createEmptyDecisions(),
    expandedRecords: new Set<string>(),
  }
}

export function useMergeState() {
  // ==================== State ====================

  const state = reactive<MergeState>(createInitialState())

  // ==================== Computed ====================

  /**
   * 是否有任何更改
   */
  const hasChanges = computed(() => {
    return (
      state.decisions.addedRecords.size > 0 ||
      state.decisions.removedRecords.size > 0 ||
      state.decisions.modifiedRecords.size > 0
    )
  })

  /**
   * 已选择的变更总数
   */
  const selectedCount = computed(() => {
    return (
      state.decisions.addedRecords.size +
      state.decisions.removedRecords.size +
      state.decisions.modifiedRecords.size
    )
  })

  // ==================== Actions ====================

  /**
   * 设置源版本信息
   */
  function setSourceVersion(version: CollectionVersion): void {
    state.sourceVersion = version
  }

  /**
   * 设置差异结果并初始化选择
   */
  function setDiffResult(result: DiffResult): void {
    state.diffResult = result
    state.decisions = createEmptyDecisions()
    state.expandedRecords = new Set<string>()
  }

  /**
   * 设置整个决策对象
   */
  function setDecisions(decisions: MergeDecisions): void {
    const modifiedRecordsMap = new Map<string, { recordId: string; fieldDecisions: Map<string, 'source' | 'target'> }>()
    decisions.modifiedRecords.forEach((value, key) => {
      modifiedRecordsMap.set(key, {
        recordId: value.recordId,
        fieldDecisions: new Map(value.fieldDecisions),
      })
    })
    state.decisions = {
      addedRecords: new Set(decisions.addedRecords),
      removedRecords: new Set(decisions.removedRecords),
      modifiedRecords: modifiedRecordsMap,
    }
  }

  /**
   * 切换新增记录的选择状态
   */
  function toggleAddedRecord(recordId: string): void {
    if (state.decisions.addedRecords.has(recordId)) {
      state.decisions.addedRecords.delete(recordId)
    } else {
      state.decisions.addedRecords.add(recordId)
    }
  }

  /**
   * 切换删除记录的选择状态
   */
  function toggleRemovedRecord(recordId: string): void {
    if (state.decisions.removedRecords.has(recordId)) {
      state.decisions.removedRecords.delete(recordId)
    } else {
      state.decisions.removedRecords.add(recordId)
    }
  }

  /**
   * 切换修改记录的选择状态
   */
  function toggleModifiedRecord(recordId: string): void {
    if (state.decisions.modifiedRecords.has(recordId)) {
      state.decisions.modifiedRecords.delete(recordId)
      state.expandedRecords.delete(recordId)
    } else {
      const modifiedItem = state.diffResult?.modified?.find(m => m.id === recordId)
      if (modifiedItem) {
        const fieldDecisions = new Map<string, 'source' | 'target'>()
        modifiedItem.fields.forEach(field => {
          fieldDecisions.set(field.fieldName, 'source')
        })
        state.decisions.modifiedRecords.set(recordId, {
          recordId,
          fieldDecisions,
        })
      }
    }
  }

  /**
   * 设置字段决策
   */
  function setFieldDecision(
    recordId: string,
    fieldName: string,
    choice: 'source' | 'target'
  ): void {
    let recordDecision = state.decisions.modifiedRecords.get(recordId)
    if (!recordDecision) {
      recordDecision = {
        recordId,
        fieldDecisions: new Map(),
      }
      state.decisions.modifiedRecords.set(recordId, recordDecision)
    }
    recordDecision.fieldDecisions.set(fieldName, choice)
  }

  /**
   * 切换修改记录的展开状态
   */
  function toggleRecordExpanded(recordId: string): void {
    if (state.expandedRecords.has(recordId)) {
      state.expandedRecords.delete(recordId)
    } else {
      state.expandedRecords.add(recordId)
    }
  }

  /**
   * 全选/取消全选新增记录
   */
  function selectAllAdded(selected: boolean): void {
    if (selected && state.diffResult) {
      state.decisions.addedRecords = new Set(state.diffResult.added.map(r => r.id))
    } else {
      state.decisions.addedRecords = new Set()
    }
  }

  /**
   * 全选/取消全选删除记录
   */
  function selectAllRemoved(selected: boolean): void {
    if (selected && state.diffResult) {
      state.decisions.removedRecords = new Set(state.diffResult.removed.map(r => r.id))
    } else {
      state.decisions.removedRecords = new Set()
    }
  }

  /**
   * 全选/取消全选修改记录
   */
  function selectAllModified(selected: boolean): void {
    if (selected && state.diffResult) {
      const newMap = new Map<string, { recordId: string; fieldDecisions: Map<string, 'source' | 'target'> }>()
      state.diffResult.modified.forEach(item => {
        const fieldDecisions = new Map<string, 'source' | 'target'>()
        item.fields.forEach(field => {
          fieldDecisions.set(field.fieldName, 'source')
        })
        newMap.set(item.id, { recordId: item.id, fieldDecisions })
      })
      state.decisions.modifiedRecords = newMap
    } else {
      state.decisions.modifiedRecords = new Map()
      state.expandedRecords = new Set()
    }
  }

  /**
   * 接受所有源版本变更
   */
  function acceptAllSource(): void {
    if (!state.diffResult) return

    state.diffResult.added.forEach(record => {
      state.decisions.addedRecords.add(record.id)
    })

    state.diffResult.removed.forEach(record => {
      state.decisions.removedRecords.add(record.id)
    })

    const newMap = new Map<string, { recordId: string; fieldDecisions: Map<string, 'source' | 'target'> }>()
    state.diffResult.modified.forEach(item => {
      const fieldDecisions = new Map<string, 'source' | 'target'>()
      item.fields.forEach(field => {
        fieldDecisions.set(field.fieldName, 'source')
      })
      newMap.set(item.id, { recordId: item.id, fieldDecisions })
    })
    state.decisions.modifiedRecords = newMap
  }

  /**
   * 接受所有目标版本变更
   */
  function acceptAllTarget(): void {
    if (!state.diffResult) return

    state.decisions.addedRecords = new Set()
    state.decisions.removedRecords = new Set()

    const newMap = new Map<string, { recordId: string; fieldDecisions: Map<string, 'source' | 'target'> }>()
    state.diffResult.modified.forEach(item => {
      const fieldDecisions = new Map<string, 'source' | 'target'>()
      item.fields.forEach(field => {
        fieldDecisions.set(field.fieldName, 'target')
      })
      newMap.set(item.id, { recordId: item.id, fieldDecisions })
    })
    state.decisions.modifiedRecords = newMap
  }

  /**
   * 为单条修改记录设置所有字段选择
   */
  function setAllFieldsForRecord(recordId: string, choice: 'source' | 'target'): void {
    const recordDecision = state.decisions.modifiedRecords.get(recordId)
    if (!recordDecision) return

    const modifiedItem = state.diffResult?.modified?.find(m => m.id === recordId)
    if (!modifiedItem) return

    const newFieldDecisions = new Map<string, 'source' | 'target'>()
    modifiedItem.fields.forEach(field => {
      newFieldDecisions.set(field.fieldName, choice)
    })
    recordDecision.fieldDecisions = newFieldDecisions
  }

  /**
   * 构建合并请求负载
   */
  function buildMergePayload(): PartialMergeRequest | null {
    if (!state.sourceVersion) {
      return null
    }

    const modifiedRecords: ModifiedRecordDecision[] = []

    state.decisions.modifiedRecords.forEach((decision, recordId) => {
      if (!state.diffResult) return

      const modifiedItem = state.diffResult.modified.find(m => m.id === recordId)
      if (!modifiedItem) return

      const fieldValues: Record<string, any> = {}

      decision.fieldDecisions.forEach((choice, fieldName) => {
        const field = modifiedItem.fields.find(f => f.fieldName === fieldName)
        if (field) {
          fieldValues[fieldName] = choice === 'source' ? field.newValue : field.oldValue
        }
      })

      modifiedRecords.push({
        record_id: recordId,
        field_values: fieldValues,
      })
    })

    const decisions: PartialMergeDecisions = {
      added_record_ids: Array.from(state.decisions.addedRecords),
      removed_record_ids: Array.from(state.decisions.removedRecords),
      modified_records: modifiedRecords,
    }

    return {
      source_version_id: state.sourceVersion.id,
      target_branch: state.targetBranch,
      decisions,
    }
  }

  /**
   * 提交合并
   */
  async function submitMerge() {
    const payload = buildMergePayload()
    if (!payload) {
      throw new Error('Cannot build merge payload: source version not set')
    }
    return await partialMergeVersion(payload)
  }

  /**
   * 重置状态
   */
  function reset(): void {
    const initialState = createInitialState()
    state.sourceVersion = initialState.sourceVersion
    state.targetBranch = initialState.targetBranch
    state.diffResult = initialState.diffResult
    state.decisions = initialState.decisions
    state.expandedRecords = initialState.expandedRecords
  }

  return {
    state,
    hasChanges,
    selectedCount,
    setSourceVersion,
    setDiffResult,
    setDecisions,
    toggleAddedRecord,
    toggleRemovedRecord,
    toggleModifiedRecord,
    setFieldDecision,
    toggleRecordExpanded,
    selectAllAdded,
    selectAllRemoved,
    selectAllModified,
    acceptAllSource,
    acceptAllTarget,
    setAllFieldsForRecord,
    buildMergePayload,
    submitMerge,
    reset,
  }
}
```

- [ ] **Step 2: Run TypeScript check**

Run: `npx vue-tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add src/components/common/composables/useMergeState.ts
git commit -m "refactor: simplify useMergeState, remove step navigation"
```

---

## Chunk 2: Create Section Components

### Task 3: Create MergeRecordSection Component

**Files:**
- Create: `src/components/common/MergeRecordSection.vue`

- [ ] **Step 1: Create the component**

```vue
/**
 * MergeRecordSection - 新增/删除记录选择区块
 *
 * 职责：
 * - 展示新增或删除记录列表
 * - 支持单条记录选择
 * - 支持 Section 级别全选
 */
<template>
  <el-collapse-item
    v-if="records.length > 0"
    :name="name"
  >
    <template #title>
      <div class="section-header">
        <el-checkbox
          :model-value="isAllSelected"
          @change="handleSelectAll"
          @click.stop
        />
        <el-icon class="section-icon" :class="iconClass">
          <component :is="icon" />
        </el-icon>
        <span class="section-title">{{ title }}</span>
        <el-tag :type="tagType" size="small">{{ records.length }}</el-tag>
        <span class="selected-count">已选 {{ selectedCount }}</span>
      </div>
    </template>

    <div class="record-list">
      <div
        v-for="record in records"
        :key="record.id"
        class="record-item"
        :class="{ 'is-selected': isSelected(record.id) }"
      >
        <el-checkbox
          :model-value="isSelected(record.id)"
          @change="(val: boolean) => handleToggle(record.id, val)"
        />
        <span class="record-display">{{ getRecordDisplay(record) }}</span>
        <el-tag v-if="type === 'removed' && !isSelected(record.id)" type="info" size="small">
          保留
        </el-tag>
        <el-tag v-else-if="type === 'removed' && isSelected(record.id)" type="danger" size="small">
          删除
        </el-tag>
      </div>
    </div>
  </el-collapse-item>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { CirclePlusFilled, RemoveFilled } from '@element-plus/icons-vue'
import type { FieldConfig } from '@/types'

interface Props {
  type: 'added' | 'removed'
  title: string
  name: string
  records: Record<string, any>[]
  selectedIds: Set<string>
  fields: FieldConfig[]
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'toggle', recordId: string, selected: boolean): void
  (e: 'select-all', selected: boolean): void
}>()

const icon = computed(() => props.type === 'added' ? CirclePlusFilled : RemoveFilled)

const iconClass = computed(() => props.type === 'added' ? 'icon-added' : 'icon-removed')

const tagType = computed(() => props.type === 'added' ? 'success' : 'danger')

const selectedCount = computed(() => props.selectedIds.size)

const isAllSelected = computed(() => {
  return props.records.length > 0 && props.selectedIds.size === props.records.length
})

const displayField = computed(() => {
  const pkField = props.fields.find(f => f.isPrimaryKey)
  if (pkField) return pkField

  const seqField = props.fields.find(f => f.controlType === 'autoSequence')
  if (seqField) return seqField

  const textField = props.fields
    .filter(f => ['text', 'textarea'].includes(f.controlType))
    .sort((a, b) => a.order - b.order)[0]

  return textField
})

function getRecordDisplay(record: Record<string, any>): string {
  if (displayField.value) {
    const val = record[displayField.value.fieldName]
    if (val !== null && val !== undefined && val !== '') {
      return String(val)
    }
  }
  return record.id || '未命名记录'
}

function isSelected(recordId: string): boolean {
  return props.selectedIds.has(recordId)
}

function handleToggle(recordId: string, selected: boolean): void {
  emit('toggle', recordId, selected)
}

function handleSelectAll(selected: boolean): void {
  emit('select-all', selected)
}
</script>

<style scoped lang="scss">
.section-header {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
}

.section-icon {
  font-size: 18px;

  &.icon-added {
    color: #67c23a;
  }

  &.icon-removed {
    color: #f56c6c;
  }
}

.section-title {
  font-weight: 500;
  color: #303133;
}

.selected-count {
  margin-left: auto;
  font-size: 12px;
  color: #909399;
}

.record-list {
  max-height: 300px;
  overflow-y: auto;
}

.record-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  border-bottom: 1px solid #f0f0f0;
  transition: background-color 0.2s;

  &:last-child {
    border-bottom: none;
  }

  &:hover {
    background-color: #f5f7fa;
  }

  &.is-selected {
    background-color: #f0f9eb;
  }
}

.record-item.is-selected {
  background-color: var(--el-color-primary-light-9);
}

.record-display {
  flex: 1;
  font-size: 14px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
```

- [ ] **Step 2: Run TypeScript check**

Run: `npx vue-tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add src/components/common/MergeRecordSection.vue
git commit -m "feat: add MergeRecordSection component for added/removed records"
```

---

### Task 4: Create MergeModifiedSection Component

**Files:**
- Create: `src/components/common/MergeModifiedSection.vue`

- [ ] **Step 1: Create the component**

```vue
/**
 * MergeModifiedSection - 修改记录选择区块
 *
 * 职责：
 * - 展示修改记录列表
 * - 支持记录级选择
 * - 支持展开的字段级选择表格
 */
<template>
  <el-collapse-item
    v-if="records.length > 0"
    name="modified"
  >
    <template #title>
      <div class="section-header">
        <el-checkbox
          :model-value="isAllSelected"
          :indeterminate="isIndeterminate"
          @change="handleSelectAll"
          @click.stop
        />
        <el-icon class="section-icon icon-modified"><EditPen /></el-icon>
        <span class="section-title">修改记录</span>
        <el-tag type="warning" size="small">{{ records.length }}</el-tag>
        <span class="selected-count">已选 {{ selectedCount }}</span>
      </div>
    </template>

    <div class="record-list">
      <div
        v-for="item in records"
        :key="item.id"
        class="record-item"
        :class="{ 'is-selected': isSelected(item.id) }"
      >
        <el-checkbox
          :model-value="isSelected(item.id)"
          @change="(val: boolean) => handleToggleRecord(item.id, val)"
        />
        <span class="record-display">{{ getRecordDisplay(item) }}</span>
        <div class="record-actions">
          <el-button
            v-if="isSelected(item.id) && isExpanded(item.id)"
            text
            size="small"
            @click="handleSetAllFields(item.id, 'source')"
          >
            全用源版本
          </el-button>
          <el-button
            v-if="isSelected(item.id) && isExpanded(item.id)"
            text
            size="small"
            @click="handleSetAllFields(item.id, 'target')"
          >
            全用目标版本
          </el-button>
          <el-button
            v-if="isSelected(item.id)"
            text
            size="small"
            @click="handleToggleExpand(item.id)"
          >
            {{ isExpanded(item.id) ? '收起' : '展开' }}
          </el-button>
          <span v-else class="expand-hint">点击选择后可展开</span>
        </div>

        <!-- 字段选择表格 -->
        <div v-if="isSelected(item.id) && isExpanded(item.id)" class="field-table">
          <div class="field-header">
            <span class="col-field">字段</span>
            <span class="col-value">源版本</span>
            <span class="col-value">目标版本</span>
            <span class="col-action">选择</span>
          </div>
          <div
            v-for="field in item.fields"
            :key="field.fieldName"
            class="field-row"
            :class="{ 'is-auto': isAutoField(field.fieldName) }"
          >
            <span class="col-field">{{ getFieldLabel(field.fieldName) }}</span>
            <span class="col-value" :class="{ 'is-chosen': getFieldChoice(item.id, field.fieldName) === 'source' }">
              {{ formatValue(field.newValue) }}
            </span>
            <span class="col-value" :class="{ 'is-chosen': getFieldChoice(item.id, field.fieldName) === 'target' }">
              {{ formatValue(field.oldValue) }}
            </span>
            <span class="col-action">
              <template v-if="isAutoField(field.fieldName)">
                <el-tag type="info" size="small">自动</el-tag>
              </template>
              <template v-else>
                <el-radio-group
                  :model-value="getFieldChoice(item.id, field.fieldName)"
                  size="small"
                  @change="(val: 'source' | 'target') => handleFieldSelect(item.id, field.fieldName, val)"
                >
                  <el-radio-button value="source">源</el-radio-button>
                  <el-radio-button value="target">目标</el-radio-button>
                </el-radio-group>
              </template>
            </span>
          </div>
        </div>

        <!-- 折叠状态摘要 -->
        <div v-if="isSelected(item.id) && !isExpanded(item.id)" class="collapse-summary">
          <span v-if="getSummary(item.id)">
            {{ getSummary(item.id) }}
          </span>
        </div>
      </div>
    </div>
  </el-collapse-item>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { EditPen } from '@element-plus/icons-vue'
import type { FieldConfig, DiffModifiedItem } from '@/types'

interface FieldDecision {
  recordId: string
  fieldDecisions: Map<string, 'source' | 'target'>
}

interface Props {
  records: DiffModifiedItem[]
  selectedRecords: Map<string, FieldDecision>
  expandedRecords: Set<string>
  fields: FieldConfig[]
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'toggle-record', recordId: string, selected: boolean): void
  (e: 'toggle-expand', recordId: string): void
  (e: 'field-select', recordId: string, fieldName: string, choice: 'source' | 'target'): void
  (e: 'set-all-fields', recordId: string, choice: 'source' | 'target'): void
  (e: 'select-all', selected: boolean): void
}>()

const AUTO_FIELDS = ['createTime', 'createUser', 'updateTime', 'updateUser']

const selectedCount = computed(() => props.selectedRecords.size)

const isAllSelected = computed(() => {
  return props.records.length > 0 && props.selectedRecords.size === props.records.length
})

const isIndeterminate = computed(() => {
  return props.selectedRecords.size > 0 && props.selectedRecords.size < props.records.length
})

const fieldLabelMap = computed(() => {
  const map: Record<string, string> = {}
  for (const f of props.fields) {
    map[f.fieldName] = f.label
  }
  return map
})

const displayField = computed(() => {
  const pkField = props.fields.find(f => f.isPrimaryKey)
  if (pkField) return pkField

  const seqField = props.fields.find(f => f.controlType === 'autoSequence')
  if (seqField) return seqField

  const textField = props.fields
    .filter(f => ['text', 'textarea'].includes(f.controlType))
    .sort((a, b) => a.order - b.order)[0]

  return textField
})

function getRecordDisplay(item: DiffModifiedItem): string {
  const record = item.record || item.oldRecord || {}
  if (displayField.value) {
    const val = record[displayField.value.fieldName]
    if (val !== null && val !== undefined && val !== '') {
      return String(val)
    }
  }
  return item.id || '未命名记录'
}

function isSelected(recordId: string): boolean {
  return props.selectedRecords.has(recordId)
}

function isExpanded(recordId: string): boolean {
  return props.expandedRecords.has(recordId)
}

function isAutoField(fieldName: string): boolean {
  return AUTO_FIELDS.includes(fieldName)
}

function getFieldLabel(fieldName: string): string {
  return fieldLabelMap.value[fieldName] || fieldName
}

function formatValue(val: any): string {
  if (val === null || val === undefined) return '-'
  if (Array.isArray(val)) return val.join('、')
  if (typeof val === 'object') return JSON.stringify(val)
  return String(val)
}

function getFieldChoice(recordId: string, fieldName: string): 'source' | 'target' {
  const record = props.selectedRecords.get(recordId)
  if (record) {
    return record.fieldDecisions.get(fieldName) || 'source'
  }
  return 'source'
}

function getSummary(recordId: string): string {
  const record = props.selectedRecords.get(recordId)
  if (!record) return ''

  let sourceCount = 0
  let targetCount = 0
  record.fieldDecisions.forEach(choice => {
    if (choice === 'source') sourceCount++
    else targetCount++
  })

  return `源版本: ${sourceCount}字段, 目标版本: ${targetCount}字段`
}

function handleToggleRecord(recordId: string, selected: boolean): void {
  emit('toggle-record', recordId, selected)
}

function handleToggleExpand(recordId: string): void {
  emit('toggle-expand', recordId)
}

function handleFieldSelect(recordId: string, fieldName: string, choice: 'source' | 'target'): void {
  emit('field-select', recordId, fieldName, choice)
}

function handleSetAllFields(recordId: string, choice: 'source' | 'target'): void {
  emit('set-all-fields', recordId, choice)
}

function handleSelectAll(selected: boolean): void {
  emit('select-all', selected)
}
</script>

<style scoped lang="scss">
.section-header {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
}

.section-icon {
  font-size: 18px;

  &.icon-modified {
    color: #e6a23c;
  }
}

.section-title {
  font-weight: 500;
  color: #303133;
}

.selected-count {
  margin-left: auto;
  font-size: 12px;
  color: #909399;
}

.record-list {
  max-height: 500px;
  overflow-y: auto;
}

.record-item {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid #f0f0f0;
  transition: background-color 0.2s;

  &:last-child {
    border-bottom: none;
  }

  &:hover {
    background-color: #f5f7fa;
  }

  &.is-selected {
    background-color: #fdf6ec;
  }
}

.record-display {
  flex: 1;
  min-width: 150px;
  font-size: 14px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.record-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.expand-hint {
  font-size: 12px;
  color: #909399;
}

.field-table {
  width: 100%;
  margin-top: 12px;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  overflow: hidden;
}

.field-header {
  display: flex;
  align-items: center;
  padding: 10px 12px;
  background-color: #f5f7fa;
  font-size: 13px;
  font-weight: 500;
  color: #606266;
}

.field-row {
  display: flex;
  align-items: center;
  padding: 10px 12px;
  border-top: 1px solid #ebeef5;

  &.is-auto {
    background-color: #fafafa;
    color: #909399;
  }
}

.col-field {
  width: 100px;
  flex-shrink: 0;
  font-weight: 500;
}

.col-value {
  flex: 1;
  min-width: 0;
  padding: 0 8px;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;

  &.is-chosen {
    font-weight: 500;
    color: #409eff;
  }
}

.col-action {
  width: 140px;
  flex-shrink: 0;
  text-align: right;
}

.collapse-summary {
  width: 100%;
  margin-left: 28px;
  font-size: 12px;
  color: #909399;
}
</style>
```

- [ ] **Step 2: Run TypeScript check**

Run: `npx vue-tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add src/components/common/MergeModifiedSection.vue
git commit -m "feat: add MergeModifiedSection component with field selection"
```

---

## Chunk 3: Rewrite Main Dialog

### Task 5: Rewrite MergeConflictDialog Component

**Files:**
- Rewrite: `src/components/common/MergeConflictDialog.vue`

- [ ] **Step 1: Rewrite the component with single-page layout**

```vue
/**
 * 版本合并对话框
 *
 * 单页统一视图设计：
 * - 无向导步骤，所有变更一页展示
 * - 新增/删除记录：复选框选择
 * - 修改记录：记录级选择 + 可展开的字段级选择
 */
<template>
  <el-dialog
    v-model="visible"
    :title="dialogTitle"
    width="1000px"
    top="5vh"
    :close-on-click-modal="false"
    destroy-on-close
    @close="handleClose"
  >
    <!-- 顶部批量操作按钮 -->
    <div v-if="!loading && !error && hasChanges" class="batch-actions">
      <el-button type="success" plain @click="handleAcceptAllSource">
        全部接受源版本
      </el-button>
      <el-button type="warning" plain @click="handleAcceptAllTarget">
        全部接受目标版本
      </el-button>
    </div>

    <!-- 统计条 -->
    <div v-if="!loading && !error && diffResult" class="statistics-bar">
      <span class="stat-item">
        <span class="stat-label">共</span>
        <span class="stat-value">{{ totalChanges }}</span>
        <span class="stat-label">处变更</span>
      </span>
      <span class="stat-divider">|</span>
      <span class="stat-item stat-added">
        新增 {{ diffResult.added.length }}
      </span>
      <span class="stat-item stat-removed">
        删除 {{ diffResult.removed.length }}
      </span>
      <span class="stat-item stat-modified">
        修改 {{ diffResult.modified.length }}
      </span>
      <span class="stat-divider">|</span>
      <span class="stat-item stat-selected">
        已选 {{ state.selectedCount }}
      </span>
    </div>

    <!-- 加载状态 -->
    <div v-if="loading" class="loading-container">
      <el-icon class="is-loading" :size="32"><Loading /></el-icon>
      <span>正在加载变更信息...</span>
    </div>

    <!-- 错误状态 -->
    <div v-else-if="error" class="error-container">
      <el-icon :size="48" color="#f56c6c"><CircleCloseFilled /></el-icon>
      <div class="error-title">加载变更信息失败</div>
      <div class="error-message">{{ error }}</div>
      <el-button type="primary" @click="loadDiff">重试</el-button>
    </div>

    <!-- 空状态 -->
    <div v-else-if="!hasChanges" class="empty-container">
      <el-empty description="没有需要处理的变更" />
    </div>

    <!-- 主内容区域 -->
    <div v-else class="main-content">
      <el-collapse v-model="activePanels">
        <!-- 新增记录 -->
        <MergeRecordSection
          v-if="diffResult"
          type="added"
          title="新增记录"
          name="added"
          :records="diffResult.added"
          :selected-ids="state.decisions.addedRecords"
          :fields="fields"
          @toggle="handleToggleAdded"
          @select-all="handleSelectAllAdded"
        />

        <!-- 删除记录 -->
        <MergeRecordSection
          v-if="diffResult"
          type="removed"
          title="删除记录"
          name="removed"
          :records="diffResult.removed"
          :selected-ids="state.decisions.removedRecords"
          :fields="fields"
          @toggle="handleToggleRemoved"
          @select-all="handleSelectAllRemoved"
        />

        <!-- 修改记录 -->
        <MergeModifiedSection
          v-if="diffResult"
          :records="diffResult.modified"
          :selected-records="state.decisions.modifiedRecords"
          :expanded-records="state.expandedRecords"
          :fields="fields"
          @toggle-record="handleToggleModified"
          @toggle-expand="handleToggleExpand"
          @field-select="handleFieldSelect"
          @set-all-fields="handleSetAllFields"
          @select-all="handleSelectAllModified"
        />
      </el-collapse>
    </div>

    <!-- 底部确认区 -->
    <template #footer>
      <div class="dialog-footer">
        <div class="footer-status">
          <span v-if="!hasSelection" class="status-warning">
            ⚠️ 请至少选择一项变更
          </span>
          <span v-else class="status-info">
            💡 已选择 {{ state.selectedCount }} 项变更：新增 {{ state.decisions.addedRecords.size }} / 删除 {{ state.decisions.removedRecords.size }} / 修改 {{ state.decisions.modifiedRecords.size }}
          </span>
        </div>
        <div class="footer-actions">
          <el-button @click="handleCancel">取消</el-button>
          <el-button
            type="primary"
            :loading="submitting"
            :disabled="!hasSelection"
            @click="handleSubmit"
          >
            确认合并
          </el-button>
        </div>
      </div>
    </template>
  </el-dialog>

  <!-- 关闭确认对话框 -->
  <el-dialog
    v-model="showCloseConfirm"
    title="提示"
    width="400px"
    append-to-body
  >
    <p>您已选择了部分变更，关闭后将丢失这些选择。是否确认关闭？</p>
    <template #footer>
      <el-button @click="showCloseConfirm = false">取消</el-button>
      <el-button type="primary" @click="confirmClose">确认关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Loading, CircleCloseFilled } from '@element-plus/icons-vue'
import { diffVersions } from '@/api/version'
import { useMergeState } from './composables/useMergeState'
import MergeRecordSection from './MergeRecordSection.vue'
import MergeModifiedSection from './MergeModifiedSection.vue'
import type { CollectionVersion, MergeDecisions } from '@/types/version'
import type { FieldConfig } from '@/types'

interface Props {
  modelValue: boolean
  collection: string
  sourceVersion: CollectionVersion | null
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'success'): void
}>()

const {
  state,
  hasChanges,
  setSourceVersion,
  setDiffResult,
  toggleAddedRecord,
  toggleRemovedRecord,
  toggleModifiedRecord,
  setFieldDecision,
  toggleRecordExpanded,
  selectAllAdded,
  selectAllRemoved,
  selectAllModified,
  acceptAllSource,
  acceptAllTarget,
  setAllFieldsForRecord,
  submitMerge,
  reset,
} = useMergeState()

const loading = ref(false)
const error = ref('')
const submitting = ref(false)
const fields = ref<FieldConfig[]>([])
const loadAborted = ref(false)
const showCloseConfirm = ref(false)
const activePanels = ref<string[]>(['added', 'removed', 'modified'])

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const dialogTitle = computed(() => {
  if (props.sourceVersion) {
    return `版本合并 - 将「${props.sourceVersion.name}」合并到「当前工作区」`
  }
  return '版本合并'
})

const diffResult = computed(() => state.diffResult)

const totalChanges = computed(() => {
  if (!state.diffResult) return 0
  return state.diffResult.added.length + state.diffResult.removed.length + state.diffResult.modified.length
})

const hasSelection = computed(() => state.selectedCount > 0)

async function loadDiff(): Promise<void> {
  if (!props.sourceVersion) {
    error.value = '未指定源版本'
    return
  }

  loading.value = true
  error.value = ''
  loadAborted.value = false

  try {
    const result = await diffVersions({
      collection: props.collection,
      baseVersion: 'current',
      targetVersion: props.sourceVersion.id,
    })

    if (loadAborted.value) return

    fields.value = result.fields || []
    setDiffResult(result)
    setSourceVersion(props.sourceVersion)
  } catch (e: any) {
    if (loadAborted.value) return
    const msg = e?.response?.data?.error || e?.message || '加载差异信息失败'
    error.value = msg
    ElMessage.error(msg)
  } finally {
    if (!loadAborted.value) {
      loading.value = false
    }
  }
}

function handleToggleAdded(recordId: string, selected: boolean): void {
  toggleAddedRecord(recordId)
}

function handleToggleRemoved(recordId: string, selected: boolean): void {
  toggleRemovedRecord(recordId)
}

function handleToggleModified(recordId: string, selected: boolean): void {
  toggleModifiedRecord(recordId)
}

function handleToggleExpand(recordId: string): void {
  toggleRecordExpanded(recordId)
}

function handleFieldSelect(recordId: string, fieldName: string, choice: 'source' | 'target'): void {
  setFieldDecision(recordId, fieldName, choice)
}

function handleSelectAllAdded(selected: boolean): void {
  selectAllAdded(selected)
}

function handleSelectAllRemoved(selected: boolean): void {
  selectAllRemoved(selected)
}

function handleSelectAllModified(selected: boolean): void {
  selectAllModified(selected)
}

function handleSetAllFields(recordId: string, choice: 'source' | 'target'): void {
  setAllFieldsForRecord(recordId, choice)
}

function handleAcceptAllSource(): void {
  acceptAllSource()
}

function handleAcceptAllTarget(): void {
  acceptAllTarget()
}

function handleCancel(): void {
  if (hasSelection.value) {
    showCloseConfirm.value = true
  } else {
    visible.value = false
  }
}

function confirmClose(): void {
  showCloseConfirm.value = false
  visible.value = false
}

function handleClose(): void {
  reset()
  fields.value = []
  error.value = ''
  loadAborted.value = true
}

async function handleSubmit(): Promise<void> {
  if (!hasSelection.value) {
    ElMessage.warning('请至少选择一项变更')
    return
  }

  submitting.value = true

  try {
    const result = await submitMerge()
    if (result.success) {
      ElMessage.success(`合并成功，共处理 ${result.merged_count} 项变更`)
      emit('success')
      visible.value = false
    }
  } catch (e: any) {
    const msg = e?.response?.data?.message || e?.message || '合并失败'
    ElMessage.error(msg)
  } finally {
    submitting.value = false
  }
}

watch(visible, (v) => {
  if (v && props.sourceVersion) {
    reset()
    loadDiff()
  } else if (!v) {
    loadAborted.value = true
  }
})
</script>

<style scoped lang="scss">
.batch-actions {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid #ebeef5;
}

.statistics-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  background-color: #f5f7fa;
  border-radius: 4px;
  margin-bottom: 16px;
  font-size: 14px;
}

.stat-item {
  display: flex;
  align-items: center;
  gap: 4px;
}

.stat-value {
  font-weight: 600;
  color: #303133;
}

.stat-label {
  color: #606266;
}

.stat-divider {
  color: #dcdfe6;
}

.stat-added {
  color: #67c23a;
}

.stat-removed {
  color: #f56c6c;
}

.stat-modified {
  color: #e6a23c;
}

.stat-selected {
  color: #409eff;
  font-weight: 500;
}

.loading-container,
.error-container,
.empty-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 0;
  gap: 12px;
}

.error-title {
  font-size: 16px;
  font-weight: 500;
  color: #303133;
}

.error-message {
  font-size: 14px;
  color: #909399;
}

.main-content {
  max-height: 60vh;
  overflow-y: auto;
}

.dialog-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.footer-status {
  font-size: 14px;
}

.status-warning {
  color: #f56c6c;
}

.status-info {
  color: #606266;
}

.footer-actions {
  display: flex;
  gap: 12px;
}

.is-loading {
  animation: rotating 2s linear infinite;
}

@keyframes rotating {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
</style>
```

- [ ] **Step 2: Run TypeScript check**

Run: `npx vue-tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add src/components/common/MergeConflictDialog.vue
git commit -m "feat: rewrite MergeConflictDialog with single-page layout"
```

---

## Chunk 4: Cleanup and Integration

### Task 6: Update Component Exports

**Files:**
- Modify: `src/components/common/index.ts`

- [ ] **Step 1: Update exports**

```typescript
// Remove old exports and add new ones
export { default as MergeConflictDialog } from './MergeConflictDialog.vue'
export { default as MergeRecordSection } from './MergeRecordSection.vue'
export { default as MergeModifiedSection } from './MergeModifiedSection.vue'

// Remove these exports (if they exist):
// export { default as StepOverview } from './StepOverview.vue'
// export { default as StepRecordSelect } from './StepRecordSelect.vue'
// export { default as StepFieldSelect } from './StepFieldSelect.vue'
```

- [ ] **Step 2: Delete old components**

```bash
rm src/components/common/StepOverview.vue
rm src/components/common/StepRecordSelect.vue
rm src/components/common/StepFieldSelect.vue
```

- [ ] **Step 3: Run TypeScript check**

Run: `npx vue-tsc --noEmit`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add src/components/common/index.ts
git add -u src/components/common/StepOverview.vue src/components/common/StepRecordSelect.vue src/components/common/StepFieldSelect.vue
git commit -m "refactor: remove old step components, update exports"
```

---

### Task 7: Integration Test

**Files:**
- Test: Manual testing in browser

- [ ] **Step 1: Start development server**

Run: `npm run dev:all`

- [ ] **Step 2: Test merge dialog**

1. Navigate to version management page
2. Click "Merge" button on a version
3. Verify dialog opens with single-page layout
4. Test selecting added records
5. Test selecting removed records
6. Test selecting modified records and expanding field selection
7. Test batch "Accept All Source" and "Accept All Target" buttons
8. Test submit merge
9. Test close confirmation when selections exist

- [ ] **Step 3: Run frontend tests**

Run: `npm run test`

Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "test: verify merge interaction redesign"
```

---

### Task 8: Final Commit and Push

- [ ] **Step 1: Push all changes**

```bash
git push
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Update MergeState type | `src/types/version.ts` |
| 2 | Simplify useMergeState | `src/components/common/composables/useMergeState.ts` |
| 3 | Create MergeRecordSection | `src/components/common/MergeRecordSection.vue` |
| 4 | Create MergeModifiedSection | `src/components/common/MergeModifiedSection.vue` |
| 5 | Rewrite MergeConflictDialog | `src/components/common/MergeConflictDialog.vue` |
| 6 | Update exports, delete old | `src/components/common/index.ts` + deletions |
| 7 | Integration test | Manual + automated |
| 8 | Push | - |