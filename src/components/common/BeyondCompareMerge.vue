/**
 * BeyondCompareMerge - Beyond Compare 风格的项目版本合并对话框
 *
 * 核心交互：
 * - 左右分栏对比：左侧版本数据，右侧当前数据
 * - 中间箭头按钮控制合并方向
 * - 差异导航（上一处/下一处）
 * - 修改记录支持字段级箭头选择
 *
 * 适配项目级版本管理：按Collection分组显示差异
 */
<template>
  <el-dialog
    v-model="visible"
    :title="dialogTitle"
    fullscreen
    :close-on-click-modal="false"
    destroy-on-close
    class="bc-merge-dialog"
    @close="handleClose"
  >
    <!-- 加载 / 错误 / 空状态 -->
    <div v-if="loading" class="bc-center-state">
      <el-icon class="is-loading" :size="32"><Loading /></el-icon>
      <span>正在加载变更信息...</span>
    </div>
    <div v-else-if="error" class="bc-center-state">
      <el-icon :size="48" color="#f56c6c"><CircleCloseFilled /></el-icon>
      <div class="bc-error-title">加载失败</div>
      <div class="bc-error-msg">{{ error }}</div>
      <el-button type="primary" @click="loadDiff">重试</el-button>
    </div>
    <div v-else-if="!hasAnyDiff" class="bc-center-state">
      <el-empty description="没有需要处理的变更" />
    </div>

    <!-- 主体内容 -->
    <template v-else>
      <!-- Collection 选择器 - 始终显示 -->
      <div v-if="collections.length > 1" class="collection-selector">
        <span>选择数据集：</span>
        <el-radio-group v-model="selectedCollection" size="small">
          <el-radio-button v-for="c in collections" :key="c.collection" :value="c.collection">
            {{ c.pageName }}
            <el-badge v-if="getCollectionDiffCount(c) > 0" :value="getCollectionDiffCount(c)" type="primary" />
          </el-radio-button>
        </el-radio-group>
      </div>

      <!-- 当前数据集无差异时的提示 -->
      <div v-if="!currentCollectionHasDiff" class="bc-center-state bc-no-diff-state">
        <el-empty description="当前数据集没有变更" />
      </div>

      <!-- 当前数据集有差异时显示对比内容 -->
      <template v-else>

      <!-- 工具栏 -->
      <div class="bc-toolbar">
        <div class="bc-toolbar-left">
          <el-button-group>
            <el-button size="small" :disabled="diffIndex <= 0" @click="navPrev">
              <el-icon><ArrowUp /></el-icon> 上一处
            </el-button>
            <el-button size="small" :disabled="diffIndex >= diffItems.length - 1" @click="navNext">
              下一处 <el-icon><ArrowDown /></el-icon>
            </el-button>
          </el-button-group>
          <span class="bc-nav-pos">{{ diffIndex + 1 }} / {{ diffItems.length }}</span>
        </div>
        <div class="bc-toolbar-center">
          <span class="bc-stat bc-stat-added">新增 {{ addedCount }}</span>
          <span class="bc-stat bc-stat-removed">删除 {{ removedCount }}</span>
          <span class="bc-stat bc-stat-modified">修改 {{ modifiedCount }}</span>
          <span class="bc-stat-divider">|</span>
          <span class="bc-stat bc-stat-selected">已选 {{ selectedCount }} / {{ diffItems.length }}</span>
        </div>
        <div class="bc-toolbar-right">
          <el-button type="success" plain size="small" @click="handleAcceptAllSource">
            全部采用版本
          </el-button>
          <el-button type="info" plain size="small" @click="handleAcceptAllTarget">
            全部保留当前
          </el-button>
        </div>
      </div>

      <!-- 分栏标题 -->
      <div class="bc-panel-header">
        <div class="bc-panel-title bc-left">
          <el-icon><Document /></el-icon>
          版本数据
          <el-tag size="small" type="warning">{{ versionName }}</el-tag>
        </div>
        <div class="bc-gutter-title"></div>
        <div class="bc-panel-title bc-right">
          <el-icon><Monitor /></el-icon>
          当前工作区
        </div>
      </div>

      <!-- 对比主体 -->
      <div ref="compareBodyRef" class="bc-compare-body">
        <!-- 新增记录 -->
        <template v-if="currentDiff!.added.length > 0">
          <div class="bc-section-label">
            <span class="bc-section-dot bc-dot-added"></span>
            新增记录（{{ currentDiff!.added.length }}）
          </div>

          <div
            v-for="(record, idx) in currentDiff!.added"
            :key="'a-' + record.id"
            :ref="el => setDiffRef('added', idx, el as HTMLElement | null)"
            class="bc-row"
            :class="{ 'bc-row-focus': isFocused('added', idx) }"
          >
            <div class="bc-cell bc-left bc-card bc-card-added">
              <div class="bc-card-title">{{ getRecordDisplay(record) }}</div>
              <div class="bc-card-fields">
                <div v-for="f in getVisibleFields(record)" :key="f.name" class="bc-field-line">
                  <span class="bc-field-label">{{ f.label }}:</span>
                  <span class="bc-field-value">{{ f.value }}</span>
                </div>
              </div>
            </div>
            <div class="bc-gutter">
              <button
                class="bc-arrow-btn"
                :class="{ 'bc-arrow-active': isAddedSelected(record.id) }"
                :title="isAddedSelected(record.id) ? '取消采纳' : '采纳此新增'"
                @click="toggleAddedRecord(record.id)"
              >
                <el-icon :size="18"><Right /></el-icon>
              </button>
            </div>
            <div class="bc-cell bc-right bc-placeholder">
              <span class="bc-placeholder-text">当前不存在</span>
            </div>
          </div>
        </template>

        <!-- 删除记录 -->
        <template v-if="currentDiff!.removed.length > 0">
          <div class="bc-section-label">
            <span class="bc-section-dot bc-dot-removed"></span>
            仅当前存在（{{ currentDiff!.removed.length }}）
          </div>

          <div
            v-for="(record, idx) in currentDiff!.removed"
            :key="'r-' + record.id"
            :ref="el => setDiffRef('removed', idx, el as HTMLElement | null)"
            class="bc-row"
            :class="{ 'bc-row-focus': isFocused('removed', idx) }"
          >
            <div class="bc-cell bc-left bc-placeholder">
              <span class="bc-placeholder-text">版本不存在</span>
            </div>
            <div class="bc-gutter">
              <button
                class="bc-arrow-btn bc-arrow-remove"
                :class="{ 'bc-arrow-active': isRemovedSelected(record.id) }"
                :title="isRemovedSelected(record.id) ? '取消删除（保留）' : '确认删除'"
                @click="toggleRemovedRecord(record.id)"
              >
                <el-icon :size="18"><Right /></el-icon>
              </button>
            </div>
            <div class="bc-cell bc-right bc-card bc-card-removed">
              <div class="bc-card-title">{{ getRecordDisplay(record) }}</div>
              <div class="bc-card-fields">
                <div v-for="f in getVisibleFields(record)" :key="f.name" class="bc-field-line">
                  <span class="bc-field-label">{{ f.label }}:</span>
                  <span class="bc-field-value">{{ f.value }}</span>
                </div>
              </div>
            </div>
          </div>
        </template>

        <!-- 修改记录 -->
        <template v-if="currentDiff!.modified.length > 0">
          <div class="bc-section-label">
            <span class="bc-section-dot bc-dot-modified"></span>
            修改记录（{{ currentDiff!.modified.length }}）
          </div>

          <div
            v-for="(item, idx) in currentDiff!.modified"
            :key="'m-' + item.id"
            :ref="el => setDiffRef('modified', idx, el as HTMLElement | null)"
            class="bc-row bc-row-modified"
            :class="{ 'bc-row-focus': isFocused('modified', idx) }"
          >
            <!-- 左侧：版本数据 -->
            <div class="bc-cell bc-left bc-card bc-card-modified">
              <div class="bc-card-header">
                <span class="bc-card-title">{{ getModifiedRecordDisplay(item) }}</span>
                <el-button
                  v-if="isModifiedSelected(item.id)"
                  text size="small" type="success"
                  @click="handleSetAllFields(item.id, 'source')"
                >
                  全用版本
                </el-button>
              </div>
              <div class="bc-mod-fields">
                <div
                  v-for="field in getUserFields(item)"
                  :key="field.fieldName"
                  class="bc-mod-field-row"
                  :class="{
                    'bc-chosen': getFieldChoice(item.id, field.fieldName) === 'source',
                    'bc-not-chosen': isModifiedSelected(item.id) && getFieldChoice(item.id, field.fieldName) !== 'source',
                  }"
                >
                  <span class="bc-field-label">{{ getFieldLabel(field.fieldName) }}</span>
                  <span class="bc-field-value">{{ formatValue(field.newValue) }}</span>
                </div>
              </div>
            </div>

            <!-- 中间：箭头列 -->
            <div class="bc-gutter bc-gutter-modified">
              <!-- 记录级总开关 -->
              <button
                class="bc-arrow-btn bc-arrow-record"
                :class="{ 'bc-arrow-active': isModifiedSelected(item.id) }"
                :title="isModifiedSelected(item.id) ? '取消选择此记录' : '选择此记录'"
                @click="toggleModifiedRecord(item.id)"
              >
                <el-icon :size="16"><Switch /></el-icon>
              </button>
              <!-- 每个字段的箭头 -->
              <template v-if="isModifiedSelected(item.id)">
                <div
                  v-for="field in getUserFields(item)"
                  :key="field.fieldName"
                  class="bc-field-arrow"
                >
                  <button
                    v-if="getFieldChoice(item.id, field.fieldName) === 'source'"
                    class="bc-arrow-btn bc-arrow-sm bc-arrow-active"
                    title="当前：采用版本数据，点击切换为当前数据"
                    @click="setFieldDecision(item.id, field.fieldName, 'target')"
                  >
                    <el-icon :size="14"><Right /></el-icon>
                  </button>
                  <button
                    v-else
                    class="bc-arrow-btn bc-arrow-sm"
                    title="当前：保留当前数据，点击切换为版本数据"
                    @click="setFieldDecision(item.id, field.fieldName, 'source')"
                  >
                    <el-icon :size="14"><Back /></el-icon>
                  </button>
                </div>
              </template>
            </div>

            <!-- 右侧：当前数据 -->
            <div class="bc-cell bc-right bc-card bc-card-modified">
              <div class="bc-card-header">
                <span class="bc-card-title">{{ getModifiedRecordDisplay(item) }}</span>
                <el-button
                  v-if="isModifiedSelected(item.id)"
                  text size="small" type="primary"
                  @click="handleSetAllFields(item.id, 'target')"
                >
                  全用当前
                </el-button>
              </div>
              <div class="bc-mod-fields">
                <div
                  v-for="field in getUserFields(item)"
                  :key="field.fieldName"
                  class="bc-mod-field-row"
                  :class="{
                    'bc-chosen': getFieldChoice(item.id, field.fieldName) === 'target',
                    'bc-not-chosen': isModifiedSelected(item.id) && getFieldChoice(item.id, field.fieldName) !== 'target',
                  }"
                >
                  <span class="bc-field-label">{{ getFieldLabel(field.fieldName) }}</span>
                  <span class="bc-field-value">{{ formatValue(field.oldValue) }}</span>
                </div>
              </div>
            </div>
          </div>
        </template>
      </div>
      </template>
    </template>

    <!-- 底部 -->
    <template #footer>
      <div class="bc-footer">
        <div class="bc-footer-status">
          <span v-if="!hasSelection" class="bc-status-warn">请至少选择一项变更</span>
          <span v-else class="bc-status-info">
            已选 {{ selectedCount }} 项：新增 {{ currentDecisions.addedRecords.size }} / 删除 {{ currentDecisions.removedRecords.size }} / 修改 {{ currentDecisions.modifiedRecords.size }}
          </span>
        </div>
        <div class="bc-footer-actions">
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

  <!-- 关闭确认 -->
  <el-dialog v-model="showCloseConfirm" title="提示" width="400px" append-to-body>
    <p>您已选择了部分变更，关闭后将丢失这些选择。是否确认关闭？</p>
    <template #footer>
      <el-button @click="showCloseConfirm = false">取消</el-button>
      <el-button type="primary" @click="confirmClose">确认关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Loading, CircleCloseFilled, ArrowUp, ArrowDown,
  Document, Monitor, Right, Back, Switch,
} from '@element-plus/icons-vue'
import { diffProjectVersions } from '@/api/projectVersion'
import { useMergeState } from './composables/useMergeState'
import type { ProjectVersion } from '@/types/version'
import type { DiffResult, CollectionDiff } from '@/api/projectVersion'

// ==================== Props / Emits ====================

interface Props {
  modelValue: boolean
  projectMenuId: string
  versionId: string
  versionName: string
  sourceVersion: ProjectVersion | null
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'success'): void
}>()

// ==================== Composable ====================

const {
  currentDecisions,
  hasSelection,
  selectedCount,
  hasFieldDecisionChanged,
  setSourceVersion,
  setDiffResult,
  toggleAddedRecord,
  toggleRemovedRecord,
  toggleModifiedRecord,
  setFieldDecision,
  acceptAllSource,
  acceptAllTarget,
  setAllFieldsForRecord,
  submitMerge,
  reset,
} = useMergeState()

// ==================== State ====================

const loading = ref(false)
const error = ref('')
const submitting = ref(false)
const fields = ref<string[]>([])
const loadAborted = ref(false)
const showCloseConfirm = ref(false)
const diffIndex = ref(0)
const compareBodyRef = ref<HTMLElement | null>(null)
const selectedCollection = ref<string>('')
const collections = ref<CollectionDiff[]>([])
const fullDiffResult = ref<DiffResult | null>(null)

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const dialogTitle = computed(() => {
  return `版本合并 — 将「${props.versionName}」合并到当前工作区`
})

const currentDiff = computed(() => {
  if (!selectedCollection.value || !collections.value.length) return null
  return collections.value.find(c => c.collection === selectedCollection.value) || null
})

const addedCount = computed(() => currentDiff.value?.added.length ?? 0)
const removedCount = computed(() => currentDiff.value?.removed.length ?? 0)
const modifiedCount = computed(() => currentDiff.value?.modified.length ?? 0)

// 整个项目是否有任何差异（基于 fullDiffResult）
const hasAnyDiff = computed(() => {
  if (!fullDiffResult.value) return false
  return (
    fullDiffResult.value.totalAdded > 0 ||
    fullDiffResult.value.totalRemoved > 0 ||
    fullDiffResult.value.totalModified > 0
  )
})

// 当前选中的数据集是否有差异
const currentCollectionHasDiff = computed(() => {
  if (!currentDiff.value) return false
  return (
    currentDiff.value.added.length > 0 ||
    currentDiff.value.removed.length > 0 ||
    currentDiff.value.modified.length > 0
  )
})

// 获取数据集的差异总数（用于页签 badge）
function getCollectionDiffCount(collection: CollectionDiff): number {
  return collection.added.length + collection.removed.length + collection.modified.length
}

// ==================== Constants ====================

const SYSTEM_FIELDS = ['createTime', 'createUser', 'updateTime', 'updateUser']

// ==================== Diff navigation ====================

interface DiffItem { type: 'added' | 'removed' | 'modified'; idx: number }

const diffItems = computed<DiffItem[]>(() => {
  const items: DiffItem[] = []
  if (!currentDiff.value) return items
  currentDiff.value.added.forEach((_, i) => items.push({ type: 'added', idx: i }))
  currentDiff.value.removed.forEach((_, i) => items.push({ type: 'removed', idx: i }))
  currentDiff.value.modified.forEach((_, i) => items.push({ type: 'modified', idx: i }))
  return items
})

// Store refs to diff row elements for scrollIntoView
const diffRefs = new Map<string, HTMLElement>()

function setDiffRef(type: string, idx: number, el: HTMLElement | null) {
  const key = `${type}-${idx}`
  if (el) {
    diffRefs.set(key, el)
  } else {
    diffRefs.delete(key)
  }
}

function isFocused(type: string, idx: number): boolean {
  const current = diffItems.value[diffIndex.value]
  return current?.type === type && current?.idx === idx
}

function scrollToCurrent() {
  const item = diffItems.value[diffIndex.value]
  if (!item) return
  const el = diffRefs.get(`${item.type}-${item.idx}`)
  el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

function navPrev() {
  if (diffIndex.value > 0) {
    diffIndex.value--
    nextTick(scrollToCurrent)
  }
}

function navNext() {
  if (diffIndex.value < diffItems.value.length - 1) {
    diffIndex.value++
    nextTick(scrollToCurrent)
  }
}

// ==================== Field helpers ====================

function getRecordDisplay(record: Record<string, any>): string {
  const parts: string[] = []
  // 尝试常见字段名
  const displayFields = ['name', 'Name', 'title', 'Title', 'id']
  for (const field of displayFields) {
    const val = record[field]
    if (val !== null && val !== undefined && val !== '') {
      parts.push(String(val))
      if (parts.length >= 2) break
    }
  }
  return parts.length > 0 ? parts.join(' | ') : record.id || '未命名记录'
}

function getModifiedRecordDisplay(item: any): string {
  return getRecordDisplay(item.record || item.oldRecord || {})
}

function getVisibleFields(record: Record<string, any>): { name: string; label: string; value: string }[] {
  return Object.entries(record)
    .filter(([key]) => !SYSTEM_FIELDS.includes(key) && !key.startsWith('_'))
    .filter(([, val]) => val !== null && val !== undefined && val !== '')
    .slice(0, 5)
    .map(([key, val]) => ({
      name: key,
      label: key,
      value: formatValue(val),
    }))
}

function getUserFields(item: any) {
  return item.fields.filter((f: any) => !SYSTEM_FIELDS.includes(f.fieldName))
}

function getFieldLabel(fieldName: string): string {
  return fieldName
}

function formatValue(val: any): string {
  if (val === null || val === undefined) return '-'
  if (Array.isArray(val)) return val.join('、')
  if (typeof val === 'object') return JSON.stringify(val)
  return String(val)
}

// ==================== Selection helpers ====================

function isAddedSelected(id: string): boolean {
  return currentDecisions.value.addedRecords.has(id)
}

function isRemovedSelected(id: string): boolean {
  return currentDecisions.value.removedRecords.has(id)
}

function isModifiedSelected(id: string): boolean {
  return currentDecisions.value.modifiedRecords.has(id)
}

function getFieldChoice(recordId: string, fieldName: string): 'source' | 'target' {
  const rd = currentDecisions.value.modifiedRecords.get(recordId)
  return rd?.fieldDecisions.get(fieldName) ?? 'source'
}

// ==================== Actions ====================

function handleAcceptAllSource() {
  acceptAllSource()
}

function handleAcceptAllTarget() {
  acceptAllTarget()
}

function handleSetAllFields(recordId: string, choice: 'source' | 'target') {
  setAllFieldsForRecord(recordId, choice)
}

function handleCancel() {
  if (selectedCount.value >= 3 || hasFieldDecisionChanged.value) {
    showCloseConfirm.value = true
  } else {
    visible.value = false
  }
}

function confirmClose() {
  showCloseConfirm.value = false
  visible.value = false
}

function handleClose() {
  reset()
  fields.value = []
  error.value = ''
  loadAborted.value = true
  diffRefs.clear()
}

async function handleSubmit() {
  if (!hasSelection.value) {
    ElMessage.warning('请至少选择一项变更')
    return
  }
  submitting.value = true
  try {
    const result = await submitMerge(props.projectMenuId)
    // 显示合并成功信息，提示可以再次合并
    const totalRecords = result.collections?.reduce(
      (sum: number, c: any) => sum + c.recordsCreated + c.recordsUpdated + c.recordsDeleted, 0
    ) || 0
    ElMessage.success({
      message: `合并成功！共处理 ${totalRecords} 条记录。版本「${props.versionName}」仍可继续合并到其他分支。`,
      duration: 5000,
    })
    emit('success')
    visible.value = false
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.message || e?.message || '合并失败')
  } finally {
    submitting.value = false
  }
}

// ==================== Data loading ====================

async function loadDiff() {
  if (!props.versionId) {
    error.value = '未指定源版本'
    return
  }
  loading.value = true
  error.value = ''
  loadAborted.value = false

  try {
    const result = await diffProjectVersions(
      props.projectMenuId,
      'current',
      props.versionId
    )
    if (loadAborted.value) return

    fullDiffResult.value = result
    collections.value = result.collections

    // 默认选中第一个 collection
    if (collections.value.length > 0) {
      selectedCollection.value = collections.value[0].collection
      // 设置第一个 collection 的 diffResult 到 state
      setDiffResult(collections.value[0])
    }

    if (props.sourceVersion) {
      setSourceVersion(props.sourceVersion)
    }
    diffIndex.value = 0
  } catch (e: any) {
    if (loadAborted.value) return
    error.value = e?.response?.data?.error || e?.message || '加载差异信息失败'
    ElMessage.error(error.value)
  } finally {
    if (!loadAborted.value) loading.value = false
  }
}

// ==================== Watch ====================

// 当切换 collection 时更新 diffResult
watch(selectedCollection, (coll) => {
  const diff = collections.value.find(c => c.collection === coll)
  if (diff) {
    setDiffResult(diff)
    diffIndex.value = 0
  }
})

watch(visible, (v) => {
  if (v && props.versionId) {
    reset()
    loadDiff()
  } else if (!v) {
    loadAborted.value = true
  }
})
</script>

<style scoped lang="scss">
.collection-selector {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 20px;
  background: #f5f7fa;
  border-bottom: 1px solid #ebeef5;
}

/* ===== Dialog overrides ===== */
.bc-merge-dialog {
  :deep(.el-dialog__body) {
    padding: 0 20px;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    flex: 1;
  }
  :deep(.el-dialog__header) {
    padding: 12px 20px;
    border-bottom: 1px solid #dcdfe6;
    margin-right: 0;
  }
  :deep(.el-dialog__footer) {
    padding: 12px 20px;
    border-top: 1px solid #dcdfe6;
  }
  :deep(.el-dialog) {
    display: flex;
    flex-direction: column;
  }
}

/* ===== Center states ===== */
.bc-center-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 0;
  gap: 12px;
  flex: 1;
}
.bc-error-title { font-size: 16px; font-weight: 500; }
.bc-error-msg   { font-size: 14px; color: #909399; }

/* ===== Toolbar ===== */
.bc-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 0;
  gap: 16px;
  flex-shrink: 0;
}
.bc-toolbar-left {
  display: flex;
  align-items: center;
  gap: 10px;
}
.bc-toolbar-center {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}
.bc-toolbar-right {
  display: flex;
  gap: 8px;
}
.bc-nav-pos {
  font-size: 12px;
  color: #909399;
  min-width: 50px;
}
.bc-stat { font-weight: 500; }
.bc-stat-added    { color: #67c23a; }
.bc-stat-removed  { color: #f56c6c; }
.bc-stat-modified { color: #e6a23c; }
.bc-stat-selected { color: #409eff; }
.bc-stat-divider  { color: #dcdfe6; }

/* ===== Panel header ===== */
.bc-panel-header {
  display: grid;
  grid-template-columns: 1fr 48px 1fr;
  gap: 0;
  flex-shrink: 0;
  border-bottom: 2px solid #dcdfe6;
  background: #f5f7fa;
}
.bc-panel-title {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  font-weight: 600;
  font-size: 13px;
  color: #303133;
}
.bc-gutter-title {
  background: #ebeef5;
}

/* ===== Compare body ===== */
.bc-compare-body {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}

/* ===== Section labels ===== */
.bc-section-label {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  font-size: 13px;
  font-weight: 600;
  color: #606266;
  background: #fafafa;
  border-bottom: 1px solid #ebeef5;
  position: sticky;
  top: 0;
  z-index: 2;
}
.bc-section-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.bc-dot-added    { background: #67c23a; }
.bc-dot-removed  { background: #f56c6c; }
.bc-dot-modified { background: #e6a23c; }

/* ===== Row ===== */
.bc-row {
  display: grid;
  grid-template-columns: 1fr 48px 1fr;
  border-bottom: 1px solid #ebeef5;
  transition: box-shadow 0.2s;

  &.bc-row-focus {
    box-shadow: inset 0 0 0 2px #409eff;
    border-radius: 2px;
  }
}

/* ===== Cells ===== */
.bc-cell {
  padding: 12px 14px;
  min-height: 60px;
}

/* ===== Placeholder ===== */
.bc-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  background: #fafafa;
}
.bc-placeholder-text {
  color: #c0c4cc;
  font-size: 13px;
  font-style: italic;
}

/* ===== Card ===== */
.bc-card {
  border-radius: 4px;
}
.bc-card-added {
  background: #f0f9eb;
  border-left: 3px solid #67c23a;
}
.bc-card-removed {
  background: #fef0f0;
  border-left: 3px solid #f56c6c;
}
.bc-card-modified {
  background: #fdf6ec;
  border-left: 3px solid #e6a23c;
}
.bc-card-title {
  font-weight: 600;
  font-size: 14px;
  color: #303133;
  margin-bottom: 6px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.bc-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}
.bc-card-fields {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

/* ===== Field lines ===== */
.bc-field-line {
  font-size: 13px;
  display: flex;
  gap: 6px;
}
.bc-field-label {
  color: #909399;
  flex-shrink: 0;
}
.bc-field-value {
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ===== Modified fields ===== */
.bc-mod-fields {
  display: flex;
  flex-direction: column;
}
.bc-mod-field-row {
  display: flex;
  gap: 8px;
  padding: 5px 8px;
  font-size: 13px;
  border-radius: 3px;
  transition: background 0.15s, opacity 0.15s;

  &.bc-chosen {
    background: rgba(64, 158, 255, 0.10);
    font-weight: 500;

    .bc-field-value { color: #409eff; }
  }
  &.bc-not-chosen {
    opacity: 0.45;
  }
}

/* ===== Gutter (arrow column) ===== */
.bc-gutter {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: #ebeef5;
  gap: 2px;
  padding: 4px 0;
}
.bc-gutter-modified {
  padding-top: 12px;
  justify-content: flex-start;
}

/* ===== Arrow buttons ===== */
.bc-arrow-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  border: 2px solid #c0c4cc;
  background: white;
  cursor: pointer;
  transition: all 0.15s;
  color: #909399;

  &:hover {
    border-color: #409eff;
    color: #409eff;
    background: #ecf5ff;
  }

  &.bc-arrow-active {
    border-color: #67c23a;
    background: #67c23a;
    color: white;

    &:hover {
      background: #529b2e;
      border-color: #529b2e;
    }
  }

  &.bc-arrow-remove.bc-arrow-active {
    border-color: #f56c6c;
    background: #f56c6c;

    &:hover {
      background: #c45656;
      border-color: #c45656;
    }
  }
}
.bc-arrow-sm {
  width: 24px;
  height: 24px;
}
.bc-arrow-record {
  margin-bottom: 6px;
}
.bc-field-arrow {
  height: 29px;
  display: flex;
  align-items: center;
}

/* ===== Footer ===== */
.bc-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.bc-footer-status { font-size: 14px; }
.bc-status-warn { color: #f56c6c; }
.bc-status-info { color: #606266; }
.bc-footer-actions {
  display: flex;
  gap: 12px;
}

/* ===== Loading spinner ===== */
.is-loading {
  animation: bc-spin 2s linear infinite;
}
@keyframes bc-spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}
</style>