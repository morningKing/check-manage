<template>
  <el-dialog
    :model-value="modelValue"
    title="AI 建表"
    width="720px"
    :close-on-click-modal="false"
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
    @closed="handleClosed"
  >
    <template v-if="phase === 'input'">
      <el-form label-width="100px">
        <el-form-item label="描述">
          <el-input
            v-model="description"
            data-test="description"
            type="textarea"
            :rows="3"
            maxlength="500"
            show-word-limit
            placeholder="描述你想创建的数据表，例如：我要创建一张订货表，记录客户、商品、数量、单价、下单日期和订单状态"
          />
        </el-form-item>
        <el-form-item label="挂载到项目" required>
          <el-select v-model="projectId" data-test="project" placeholder="选择新页面挂载到哪个项目菜单下" style="width: 100%">
            <el-option v-for="p in projectOptions" :key="p.value" :label="p.label" :value="p.value" />
          </el-select>
          <div class="form-tip">数据菜单必须挂在一个已有的项目菜单下，AI 不知道系统里有哪些项目，需要你手动选择</div>
        </el-form-item>
      </el-form>
    </template>

    <template v-else-if="phase === 'preview'">
      <el-alert
        v-if="menuError"
        type="warning"
        :closable="false"
        show-icon
        style="margin-bottom: 16px"
      >
        <template #title>
          页面已创建，但菜单挂载失败（{{ menuError }}）。请在「菜单管理」里为页面「{{ draft!.name }}」手动添加菜单。
        </template>
      </el-alert>

      <el-form label-width="100px">
        <el-form-item label="页面名称" required>
          <el-input v-model="draft!.name" maxlength="50" />
        </el-form-item>
        <el-form-item label="页面描述">
          <el-input v-model="draft!.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="集合标识" required>
          <el-input v-model="collectionSlug" data-test="slug" placeholder="英文 kebab-case，如 purchase-orders" />
          <div class="form-tip" :class="{ 'form-tip--error': slugConflict }">
            API 端点：<code>/{{ collectionSlug || '...' }}</code>
            <span v-if="slugConflict"> —— 该标识已被占用，请修改</span>
          </div>
        </el-form-item>
        <el-form-item label="菜单名称" required>
          <el-input v-model="draft!.menuName" maxlength="20" />
        </el-form-item>
        <el-form-item label="路由路径" required>
          <el-input v-model="draft!.menuPath" placeholder="如 /purchase-orders" />
        </el-form-item>
      </el-form>

      <div class="fields-preview">
        <div class="fields-preview__head">
          <span>字段预览（可编辑，确认创建前请检查）</span>
          <el-button link type="primary" :icon="Plus" @click="addFieldRow">添加字段</el-button>
        </div>
        <el-table :data="draft!.fields" size="small" border max-height="320">
          <el-table-column label="字段名" width="140">
            <template #default="{ row }">
              <el-input v-model="row.fieldName" size="small" />
            </template>
          </el-table-column>
          <el-table-column label="显示名称" width="140">
            <template #default="{ row }">
              <el-input v-model="row.label" size="small" />
            </template>
          </el-table-column>
          <el-table-column label="控件类型" width="140">
            <template #default="{ row }">
              <el-select v-model="row.controlType" size="small" style="width: 100%">
                <el-option v-for="opt in SAFE_CONTROL_TYPE_OPTIONS" :key="opt.value" :label="opt.label" :value="opt.value" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="必填" width="70" align="center">
            <template #default="{ row }">
              <el-checkbox v-model="row.required" />
            </template>
          </el-table-column>
          <el-table-column label="选项（逗号分隔）" min-width="160">
            <template #default="{ row }">
              <el-input
                v-if="OPTIONS_TYPES.has(row.controlType)"
                :model-value="optionsText(row)"
                size="small"
                placeholder="标签1:值1, 标签2:值2"
                @update:model-value="(v: string) => setOptionsFromText(row, v)"
              />
              <span v-else class="fields-preview__na">—</span>
            </template>
          </el-table-column>
          <el-table-column label="" width="50" align="center">
            <template #default="{ $index }">
              <el-icon class="fields-preview__delete" @click="removeFieldRow($index)"><Delete /></el-icon>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <p class="fields-preview__note">
        AI 不会生成关联、引用、状态徽标等复杂字段——需要这些能力请在创建后于「字段配置」标签页手动添加。
      </p>
    </template>

    <template #footer>
      <template v-if="phase === 'input'">
        <el-button @click="emit('update:modelValue', false)">取消</el-button>
        <el-button
          type="primary"
          data-test="generate-btn"
          :loading="generating"
          :disabled="!description.trim() || !projectId"
          @click="handleGenerate"
        >
          生成
        </el-button>
      </template>
      <template v-else-if="phase === 'preview'">
        <el-button @click="phase = 'input'">上一步</el-button>
        <el-button
          type="primary"
          data-test="confirm-btn"
          :loading="submitting"
          :disabled="!canSubmit"
          @click="handleConfirm"
        >
          确认创建
        </el-button>
      </template>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Delete } from '@element-plus/icons-vue'
import { usePageConfigStore, useMenuStore } from '@/stores'
import { draftPageConfigWithAi } from '@/api/page'
import type { FieldConfig, FieldOption, ControlType } from '@/types'

interface DraftField {
  fieldName: string
  label: string
  controlType: ControlType
  required: boolean
  options?: FieldOption[]
  sequenceConfig?: { prefix: string; max: number }
}

interface Draft {
  name: string
  description: string
  menuName: string
  menuPath: string
  fields: DraftField[]
}

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  created: [pageId: string]
}>()

const pageConfigStore = usePageConfigStore()
const menuStore = useMenuStore()

// AI 只允许生成这个安全子集——不含 relation/reference/quoteSelect（需要选目标
// 集合，AI 不知道系统里有哪些）、statusBadge/compositeText（配置太重）。
const SAFE_CONTROL_TYPE_OPTIONS: { label: string; value: ControlType }[] = [
  { label: '单行文本', value: 'text' },
  { label: '多行文本', value: 'textarea' },
  { label: 'Markdown', value: 'markdown' },
  { label: '富文本', value: 'richText' },
  { label: '数字输入', value: 'number' },
  { label: '单选下拉', value: 'select' },
  { label: '多选下拉', value: 'multiSelect' },
  { label: '单选按钮', value: 'radio' },
  { label: '复选框', value: 'checkbox' },
  { label: '日期选择', value: 'date' },
  { label: '日期时间', value: 'datetime' },
  { label: '文件上传', value: 'file' },
  { label: '图片上传', value: 'image' },
  { label: '自动时间戳', value: 'autoTimestamp' },
  { label: '自增序列', value: 'autoSequence' },
]
const OPTIONS_TYPES = new Set(['select', 'multiSelect', 'radio', 'checkbox'])

const phase = ref<'input' | 'preview'>('input')
const description = ref('')
const projectId = ref('')
const generating = ref(false)
const submitting = ref(false)
const menuError = ref('')
const draft = ref<Draft | null>(null)
const collectionSlug = ref('')

const projectOptions = computed(() =>
  menuStore.menuList
    .filter(m => m.menuType === 'project')
    .map(m => ({ label: m.name, value: m.id }))
)

const slugConflict = computed(() => {
  if (!collectionSlug.value) return false
  const id = `page-${collectionSlug.value}`
  return pageConfigStore.pageConfigs.some(p => p.id === id)
})

const canSubmit = computed(() => {
  if (!draft.value) return false
  return (
    draft.value.name.trim().length > 0 &&
    collectionSlug.value.trim().length > 0 &&
    !slugConflict.value &&
    draft.value.fields.length > 0 &&
    draft.value.menuPath.trim().length > 0
  )
})

watch(() => props.modelValue, (open) => {
  if (open) reset()
})

function reset(): void {
  phase.value = 'input'
  description.value = ''
  projectId.value = ''
  draft.value = null
  collectionSlug.value = ''
  menuError.value = ''
}

function handleClosed(): void {
  reset()
}

function optionsText(row: DraftField): string {
  return (row.options || []).map(o => `${o.label}:${o.value}`).join(', ')
}

function setOptionsFromText(row: DraftField, text: string): void {
  row.options = text
    .split(',')
    .map(part => part.trim())
    .filter(Boolean)
    .map(part => {
      const [label, value] = part.split(':')
      return { label: (label || part).trim(), value: (value ?? label ?? part).trim() }
    })
}

function addFieldRow(): void {
  if (!draft.value) return
  draft.value.fields.push({
    fieldName: `field${draft.value.fields.length + 1}`,
    label: '新字段',
    controlType: 'text',
    required: false,
  })
}

function removeFieldRow(index: number): void {
  draft.value?.fields.splice(index, 1)
}

async function handleGenerate(): Promise<void> {
  if (!description.value.trim() || !projectId.value) return
  generating.value = true
  try {
    const result = await draftPageConfigWithAi(description.value.trim())
    draft.value = {
      name: result.name,
      description: result.description,
      menuName: result.menuName,
      menuPath: result.menuPath,
      fields: result.fields.map(f => ({
        fieldName: f.fieldName,
        label: f.label,
        controlType: f.controlType as ControlType,
        required: f.required,
        options: f.options,
        sequenceConfig: f.sequenceConfig,
      })),
    }
    collectionSlug.value = result.collectionSlug
    phase.value = 'preview'
  } catch (error: any) {
    ElMessage.error(error.response?.data?.error || error.message || 'AI 生成失败')
  } finally {
    generating.value = false
  }
}

function toFieldConfig(f: DraftField, index: number): FieldConfig {
  return {
    id: `field-${Date.now()}-${index}`,
    label: f.label,
    fieldName: f.fieldName,
    controlType: f.controlType,
    required: f.required,
    order: index + 1,
    options: f.options,
    sequenceConfig: f.controlType === 'autoSequence'
      ? (f.sequenceConfig || { prefix: '', max: 9999 })
      : undefined,
  }
}

async function handleConfirm(): Promise<void> {
  if (!draft.value || !canSubmit.value) return
  submitting.value = true
  menuError.value = ''
  try {
    const created = await pageConfigStore.addPageConfig(
      {
        name: draft.value.name,
        description: draft.value.description,
        apiEndpoint: `/${collectionSlug.value}`,
        fields: draft.value.fields.map(toFieldConfig),
      },
      { id: `page-${collectionSlug.value}` }
    )

    try {
      const siblings = menuStore.getMenuById(projectId.value)
        ? menuStore.menuList.filter(m => m.parentId === projectId.value)
        : []
      const nextOrder = siblings.length
        ? Math.max(...siblings.map(m => m.order)) + 1
        : 1
      await menuStore.addMenu({
        name: draft.value.menuName,
        icon: 'Document',
        menuType: 'data',
        pageId: created.id,
        parentId: projectId.value,
        projectId: projectId.value,
        order: nextOrder,
        path: draft.value.menuPath,
        roles: ['admin', 'developer', 'guest'],
      })
      ElMessage.success('创建成功')
      emit('update:modelValue', false)
      emit('created', created.id)
    } catch (menuErr: any) {
      // 页面已经创建成功；菜单挂载失败不回滚页面，避免"看似失败实则半成品已落库"。
      menuError.value = menuErr.response?.data?.error || menuErr.message || '未知错误'
      emit('created', created.id)
    }
  } catch (error: any) {
    ElMessage.error(error.response?.data?.error || error.message || '创建失败')
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped lang="scss">
.form-tip {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;

  code { color: var(--el-text-color-primary); }

  &--error { color: var(--el-color-danger); }
}

.fields-preview {
  margin-top: 8px;

  &__head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
    font-size: 13px;
    color: var(--el-text-color-regular);
  }

  &__na { color: var(--el-text-color-placeholder); }

  &__delete {
    cursor: pointer;
    color: var(--el-color-danger);
    &:hover { color: var(--el-color-danger-light-3); }
  }

  &__note {
    font-size: 12px;
    color: var(--el-text-color-secondary);
    margin: 8px 0 0;
  }
}
</style>
