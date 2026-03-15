/**
 * 版本合并冲突解决对话框
 *
 * 职责：
 * - 管理三步合并流程（概览 → 记录选择 → 字段选择）
 * - 加载版本差异信息
 * - 提交部分合并决策
 */
<template>
  <el-dialog
    v-model="visible"
    title="版本合并"
    width="900px"
    top="5vh"
    :close-on-click-modal="false"
    destroy-on-close
    @close="handleClose"
  >
    <!-- 步骤指示器 -->
    <div class="steps-container">
      <el-steps :active="stepIndex" align-center>
        <el-step title="概览" description="变更统计" />
        <el-step title="记录选择" description="选择记录" />
        <el-step title="字段选择" description="字段决策" />
      </el-steps>
    </div>

    <!-- 加载中状态 -->
    <div v-if="loading" class="loading-container">
      <el-icon class="is-loading" :size="32"><Loading /></el-icon>
      <span>正在加载变更信息...</span>
    </div>

    <!-- 错误状态 -->
    <el-alert
      v-else-if="error"
      :title="error"
      type="error"
      :closable="false"
      show-icon
    />

    <!-- 步骤内容 -->
    <div v-else-if="state.diffResult" class="step-content">
      <!-- 步骤1: 概览 -->
      <StepOverview
        v-if="state.step === 'overview'"
        :diff-result="state.diffResult"
        @accept-source="handleAcceptAllSource"
        @accept-target="handleAcceptAllTarget"
        @next="handleGoToRecords"
        @cancel="handleCancel"
      />

      <!-- 步骤2: 记录选择 -->
      <StepRecordSelect
        v-else-if="state.step === 'records'"
        :diff-result="state.diffResult"
        :fields="fields"
        :decisions="localDecisions"
        @update:decisions="handleDecisionsUpdate"
      />

      <!-- 步骤3: 字段选择 -->
      <StepFieldSelect
        v-else-if="state.step === 'fields'"
        :diff-result="state.diffResult"
        :fields="fields"
        :decisions="localDecisions"
        @update:decisions="handleDecisionsUpdate"
      />
    </div>

    <!-- 底部操作按钮 -->
    <template #footer>
      <div class="dialog-footer">
        <div class="footer-left">
          <el-button @click="handleCancel">取消</el-button>
        </div>
        <div class="footer-right">
          <el-button
            v-if="state.step !== 'overview'"
            @click="handlePrevStep"
          >
            上一步
          </el-button>
          <el-button
            v-if="state.step === 'overview'"
            type="primary"
            :disabled="!hasTotalChanges"
            @click="handleGoToRecords"
          >
            下一步
          </el-button>
          <el-button
            v-else-if="state.step === 'records'"
            type="primary"
            @click="handleGoToFields"
          >
            下一步
          </el-button>
          <el-button
            v-else
            type="primary"
            :loading="submitting"
            :disabled="!canSubmit"
            @click="handleSubmit"
          >
            完成合并
          </el-button>
        </div>
      </div>
      <div v-if="state.step === 'fields' && !canSubmit" class="footer-hint">
        请至少选择一项变更
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import { diffVersions } from '@/api/version'
import { useMergeState } from './composables/useMergeState'
import type { CollectionVersion, MergeDecisions, MergeStep } from '@/types/version'
import type { FieldConfig } from '@/types'
import StepOverview from './StepOverview.vue'
import StepRecordSelect from './StepRecordSelect.vue'
import StepFieldSelect from './StepFieldSelect.vue'

// ==================== Props & Emits ====================

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

// ==================== State ====================

const {
  state,
  canSubmit,
  setStep,
  setSourceVersion,
  setDiffResult,
  setDecisions,
  acceptAllSource,
  acceptAllTarget,
  submitMerge,
  reset,
} = useMergeState()

const loading = ref(false)
const error = ref('')
const submitting = ref(false)
// fields 用于存储字段配置（composable 不管理此状态）
const fields = ref<FieldConfig[]>([])
// 用于跟踪异步操作是否被中止
const loadAborted = ref(false)

// 本地决策状态（用于子组件绑定）
const localDecisions = ref<MergeDecisions>({
  addedRecords: new Set<string>(),
  removedRecords: new Set<string>(),
  modifiedRecords: new Map(),
})

// ==================== Computed ====================

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const stepIndex = computed(() => {
  const stepMap: Record<MergeStep, number> = {
    overview: 0,
    records: 1,
    fields: 2,
  }
  return stepMap[state.step]
})

/**
 * 是否有任何变更（基于 diff 结果）
 */
const hasTotalChanges = computed(() => {
  if (!state.diffResult) return false
  return (
    state.diffResult.added.length > 0 ||
    state.diffResult.removed.length > 0 ||
    state.diffResult.modified.length > 0
  )
})

// ==================== Methods ====================

/**
 * 同步 composable 状态到本地决策
 */
function syncStateToLocal(): void {
  // 转换 modifiedRecords Map，确保创建新的可变对象
  const modifiedRecordsMap = new Map<string, {
    recordId: string
    fieldDecisions: Map<string, 'source' | 'target'>
  }>()

  state.decisions.modifiedRecords.forEach((value, key) => {
    modifiedRecordsMap.set(key, {
      recordId: value.recordId,
      fieldDecisions: new Map(value.fieldDecisions),
    })
  })

  localDecisions.value = {
    addedRecords: new Set(state.decisions.addedRecords),
    removedRecords: new Set(state.decisions.removedRecords),
    modifiedRecords: modifiedRecordsMap,
  }
}

/**
 * 加载版本差异
 */
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

    // 检查是否已被中止（对话框关闭）
    if (loadAborted.value) {
      return
    }

    fields.value = result.fields || []
    setDiffResult(result)
    setSourceVersion(props.sourceVersion)
    syncStateToLocal()
  } catch (e: any) {
    // 检查是否已被中止
    if (loadAborted.value) {
      return
    }
    const msg = e?.response?.data?.error || e?.message || '加载差异信息失败'
    error.value = msg
    ElMessage.error(msg)
  } finally {
    if (!loadAborted.value) {
      loading.value = false
    }
  }
}

/**
 * 处理决策更新
 */
function handleDecisionsUpdate(newDecisions: MergeDecisions): void {
  localDecisions.value = newDecisions
  setDecisions(newDecisions)
}

/**
 * 处理全部接受源版本
 */
function handleAcceptAllSource(): void {
  acceptAllSource()
  syncStateToLocal()
}

/**
 * 处理全部接受目标版本
 */
function handleAcceptAllTarget(): void {
  acceptAllTarget()
  syncStateToLocal()
}

/**
 * 进入记录选择步骤
 */
function handleGoToRecords(): void {
  setStep('records')
}

/**
 * 进入字段选择步骤
 */
function handleGoToFields(): void {
  setStep('fields')
}

/**
 * 返回上一步
 */
function handlePrevStep(): void {
  const stepOrder: MergeStep[] = ['overview', 'records', 'fields']
  const currentIndex = stepOrder.indexOf(state.step)
  if (currentIndex > 0) {
    setStep(stepOrder[currentIndex - 1])
  }
}

/**
 * 取消操作
 */
function handleCancel(): void {
  visible.value = false
}

/**
 * 关闭对话框
 */
function handleClose(): void {
  reset()
  fields.value = []
  error.value = ''
  loadAborted.value = true
  localDecisions.value = {
    addedRecords: new Set<string>(),
    removedRecords: new Set<string>(),
    modifiedRecords: new Map(),
  }
}

/**
 * 提交合并
 */
async function handleSubmit(): Promise<void> {
  if (!canSubmit.value) {
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

// ==================== Watch ====================

watch(visible, (v) => {
  if (v && props.sourceVersion) {
    reset()
    loadDiff()
  } else if (!v) {
    // 对话框关闭时，中止进行中的加载操作
    loadAborted.value = true
  }
})
</script>

<style scoped lang="scss">
.steps-container {
  padding: 0 20px 20px;
  border-bottom: 1px solid #ebeef5;
  margin-bottom: 20px;
}

.loading-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 0;
  color: #909399;
  gap: 12px;

  .is-loading {
    animation: rotating 2s linear infinite;
  }
}

@keyframes rotating {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

.step-content {
  min-height: 400px;
  max-height: 60vh;
  overflow-y: auto;
}

.dialog-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.footer-left {
  display: flex;
  gap: 10px;
}

.footer-right {
  display: flex;
  gap: 10px;
}

.footer-hint {
  text-align: right;
  font-size: 12px;
  color: #f56c6c;
  margin-top: 8px;
}
</style>