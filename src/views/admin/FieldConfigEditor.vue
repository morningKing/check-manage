/**
 * 字段配置编辑器组件
 *
 * 职责：
 * - 可视化编辑页面字段配置
 * - 支持字段的增删改
 * - 支持拖拽排序
 * - 配置字段属性（名称、类型、必填等）
 */
<template>
  <div class="field-config-editor">
    <!-- 工具栏 -->
    <div class="editor-toolbar">
      <el-button type="primary" size="small" @click="handleAddField">
        <el-icon><Plus /></el-icon>
        添加字段
      </el-button>
      <el-button size="small" @click="handleSaveAll" :loading="saving">
        <el-icon><Check /></el-icon>
        保存配置
      </el-button>
    </div>

    <!-- 字段列表 -->
    <div class="field-list" v-if="localFields.length > 0">
      <draggable
        v-model="localFields"
        item-key="id"
        handle=".drag-handle"
        @end="handleDragEnd"
      >
        <template #item="{ element, index }">
          <div class="field-item">
            <!-- 拖拽手柄 -->
            <div class="drag-handle">
              <el-icon><Rank /></el-icon>
            </div>

            <!-- 字段信息 -->
            <div class="field-content">
              <div class="field-main">
                <span class="field-label">{{ element.label }}</span>
                <el-tag size="small" type="info">
                  {{ getControlTypeLabel(element.controlType) }}
                </el-tag>
                <el-tag v-if="element.required" size="small" type="danger">
                  必填
                </el-tag>
                <el-tag v-if="element.isPrimaryKey" size="small" type="warning">
                  主键
                </el-tag>
              </div>
              <div class="field-meta">
                字段名: {{ element.fieldName }}
              </div>
            </div>

            <!-- 操作按钮 -->
            <div class="field-actions">
              <el-button type="primary" link @click="handleEditField(element, index)">
                编辑
              </el-button>
              <el-button type="danger" link @click="handleDeleteField(index)">
                删除
              </el-button>
            </div>
          </div>
        </template>
      </draggable>
    </div>

    <el-empty v-else description="暂无字段配置，点击上方按钮添加" />

    <!-- 字段编辑对话框 -->
    <el-dialog
      v-model="editDialogVisible"
      :title="editDialogTitle"
      width="650px"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <el-form
        ref="fieldFormRef"
        :model="fieldFormData"
        :rules="fieldFormRules"
        label-width="100px"
      >
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="显示名称" prop="label">
              <el-input
                v-model="fieldFormData.label"
                placeholder="请输入字段显示名称"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="字段名" prop="fieldName">
              <el-input
                v-model="fieldFormData.fieldName"
                placeholder="后端字段名，如 userName"
              />
            </el-form-item>
          </el-col>
        </el-row>

        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="控件类型" prop="controlType">
              <el-select
                v-model="fieldFormData.controlType"
                placeholder="请选择控件类型"
                style="width: 100%"
              >
                <el-option
                  v-for="type in controlTypeOptions"
                  :key="type.value"
                  :label="type.label"
                  :value="type.value"
                />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="6">
            <el-form-item label="必填" prop="required">
              <el-switch v-model="fieldFormData.required" />
            </el-form-item>
          </el-col>
          <el-col :span="6">
            <el-form-item label="主键" prop="isPrimaryKey">
              <el-switch v-model="fieldFormData.isPrimaryKey" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="占位提示" prop="placeholder">
          <el-input
            v-model="fieldFormData.placeholder"
            placeholder="输入框的提示文字"
          />
        </el-form-item>

        <!-- 选项配置（仅下拉、单选、多选显示） -->
        <el-form-item
          v-if="showOptionsConfig"
          label="选项配置"
        >
          <div class="options-config">
            <el-radio-group
              v-model="fieldFormData.optionsSource.type"
              class="options-type"
            >
              <el-radio value="static">静态选项</el-radio>
              <el-radio value="api">API获取</el-radio>
              <el-radio value="collection">数据页数据</el-radio>
            </el-radio-group>

            <!-- 静态选项配置 -->
            <div v-if="fieldFormData.optionsSource.type === 'static'" class="static-options">
              <div
                v-for="(option, optIndex) in fieldFormData.options"
                :key="optIndex"
                class="option-row"
              >
                <el-input
                  v-model="option.label"
                  placeholder="显示文本"
                  style="width: 45%"
                />
                <el-input
                  v-model="option.value"
                  placeholder="值"
                  style="width: 45%"
                />
                <el-button
                  type="danger"
                  link
                  @click="removeOption(optIndex)"
                >
                  <el-icon><Delete /></el-icon>
                </el-button>
              </div>
              <el-button type="primary" link @click="addOption">
                <el-icon><Plus /></el-icon>
                添加选项
              </el-button>
            </div>

            <!-- API选项配置 -->
            <div v-else-if="fieldFormData.optionsSource.type === 'api'" class="api-options">
              <el-form-item label="API地址" label-width="80px">
                <el-input
                  v-model="fieldFormData.optionsSource.url"
                  placeholder="如：/api/options/status"
                />
              </el-form-item>
              <el-row :gutter="16">
                <el-col :span="12">
                  <el-form-item label="标签字段" label-width="80px">
                    <el-input
                      v-model="fieldFormData.optionsSource.labelField"
                      placeholder="label"
                    />
                  </el-form-item>
                </el-col>
                <el-col :span="12">
                  <el-form-item label="值字段" label-width="80px">
                    <el-input
                      v-model="fieldFormData.optionsSource.valueField"
                      placeholder="value"
                    />
                  </el-form-item>
                </el-col>
              </el-row>
            </div>

            <!-- 数据页数据选项配置 -->
            <div v-else-if="fieldFormData.optionsSource.type === 'collection'" class="collection-options">
              <el-form-item label="数据页" label-width="80px">
                <el-select
                  v-model="fieldFormData.optionsSource.collection"
                  placeholder="请选择数据页"
                  filterable
                  style="width: 100%"
                  @change="handleOptionsCollectionChange"
                >
                  <el-option
                    v-for="opt in optionsCollectionList"
                    :key="opt.value"
                    :label="opt.label"
                    :value="opt.value"
                  />
                </el-select>
              </el-form-item>
              <el-row :gutter="16">
                <el-col :span="12">
                  <el-form-item label="标签字段" label-width="80px">
                    <el-select
                      v-model="fieldFormData.optionsSource.labelField"
                      placeholder="选择显示字段"
                      filterable
                      style="width: 100%"
                      :disabled="!fieldFormData.optionsSource.collection"
                    >
                      <el-option
                        v-for="opt in optionsCollectionFieldList"
                        :key="opt.value"
                        :label="`${opt.label}（${opt.value}）`"
                        :value="opt.value"
                      />
                    </el-select>
                  </el-form-item>
                </el-col>
                <el-col :span="12">
                  <el-form-item label="值字段" label-width="80px">
                    <el-select
                      v-model="fieldFormData.optionsSource.valueField"
                      placeholder="选择值字段"
                      filterable
                      style="width: 100%"
                      :disabled="!fieldFormData.optionsSource.collection"
                    >
                      <el-option
                        v-for="opt in optionsCollectionFieldList"
                        :key="opt.value"
                        :label="`${opt.label}（${opt.value}）`"
                        :value="opt.value"
                      />
                    </el-select>
                  </el-form-item>
                </el-col>
              </el-row>
            </div>
          </div>
        </el-form-item>

        <!-- 关联配置（仅关联类型显示） -->
        <el-form-item
          v-if="showRelationConfig"
          label="关联配置"
        >
          <div class="relation-config">
            <el-form-item label="目标集合" label-width="80px">
              <el-select
                v-model="fieldFormData.relationConfig!.targetCollection"
                placeholder="请选择目标集合"
                filterable
                style="width: 100%"
                @change="handleTargetCollectionChange"
              >
                <el-option
                  v-for="opt in collectionOptions"
                  :key="opt.value"
                  :label="opt.label"
                  :value="opt.value"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="显示字段" label-width="80px">
              <el-select
                v-model="fieldFormData.relationConfig!.displayField"
                placeholder="请选择显示字段"
                filterable
                style="width: 100%"
                :disabled="!fieldFormData.relationConfig!.targetCollection"
              >
                <el-option
                  v-for="opt in displayFieldOptions"
                  :key="opt.value"
                  :label="`${opt.label}（${opt.value}）`"
                  :value="opt.value"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="反向字段" label-width="80px">
              <el-select
                v-model="fieldFormData.relationConfig!.targetField"
                placeholder="请选择或输入反向字段名"
                filterable
                allow-create
                style="width: 100%"
                :disabled="!fieldFormData.relationConfig!.targetCollection"
              >
                <el-option
                  v-for="opt in targetFieldOptions"
                  :key="opt.value"
                  :label="`${opt.label}（${opt.value}）`"
                  :value="opt.value"
                />
              </el-select>
            </el-form-item>
          </div>
        </el-form-item>

        <!-- 引用配置（仅引用类型显示） -->
        <el-form-item
          v-if="showReferenceConfig"
          label="引用配置"
        >
          <div class="relation-config">
            <el-form-item label="目标集合" label-width="80px">
              <el-select
                v-model="fieldFormData.referenceConfig!.targetCollection"
                placeholder="请选择目标集合"
                filterable
                style="width: 100%"
                @change="handleRefTargetCollectionChange"
              >
                <el-option
                  v-for="opt in collectionOptions"
                  :key="opt.value"
                  :label="opt.label"
                  :value="opt.value"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="显示字段" label-width="80px">
              <el-select
                v-model="fieldFormData.referenceConfig!.displayField"
                placeholder="请选择下拉显示字段"
                filterable
                style="width: 100%"
                :disabled="!fieldFormData.referenceConfig!.targetCollection"
              >
                <el-option
                  v-for="opt in refDisplayFieldOptions"
                  :key="opt.value"
                  :label="`${opt.label}（${opt.value}）`"
                  :value="opt.value"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="继承字段" label-width="80px">
              <el-select
                v-model="fieldFormData.referenceConfig!.inheritFields"
                placeholder="选择需要在表格中显示的父字段"
                filterable
                multiple
                style="width: 100%"
                :disabled="!fieldFormData.referenceConfig!.targetCollection"
              >
                <el-option
                  v-for="opt in refDisplayFieldOptions"
                  :key="opt.value"
                  :label="`${opt.label}（${opt.value}）`"
                  :value="opt.value"
                />
              </el-select>
            </el-form-item>
          </div>
        </el-form-item>

        <!-- 序列配置（仅自增序列类型显示） -->
        <el-form-item
          v-if="showSequenceConfig"
          label="序列配置"
        >
          <div class="sequence-config">
            <el-row :gutter="16">
              <el-col :span="12">
                <el-form-item label="前缀" label-width="60px">
                  <el-input
                    v-model="fieldFormData.sequenceConfig!.prefix"
                    placeholder="如 IC-"
                  />
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="上限" label-width="60px">
                  <el-input-number
                    v-model="fieldFormData.sequenceConfig!.max"
                    :min="1"
                    :max="999999"
                    style="width: 100%"
                  />
                </el-form-item>
              </el-col>
            </el-row>
            <div v-if="sequencePreview" class="sequence-preview">
              格式预览：{{ sequencePreview }}
            </div>
          </div>
        </el-form-item>

        <!-- 引用选择配置（仅引用选择类型显示） -->
        <el-form-item
          v-if="showQuoteConfig"
          label="引用配置"
        >
          <div class="relation-config">
            <el-form-item label="目标集合" label-width="80px">
              <el-select
                v-model="fieldFormData.quoteConfig!.targetCollection"
                placeholder="请选择目标集合"
                filterable
                style="width: 100%"
                @change="handleQuoteTargetCollectionChange"
              >
                <el-option
                  v-for="opt in collectionOptions"
                  :key="opt.value"
                  :label="opt.label"
                  :value="opt.value"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="显示字段" label-width="80px">
              <el-select
                v-model="fieldFormData.quoteConfig!.displayField"
                placeholder="请选择显示字段"
                filterable
                style="width: 100%"
                :disabled="!fieldFormData.quoteConfig!.targetCollection"
              >
                <el-option
                  v-for="opt in quoteDisplayFieldOptions"
                  :key="opt.value"
                  :label="`${opt.label}（${opt.value}）`"
                  :value="opt.value"
                />
              </el-select>
            </el-form-item>
          </div>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="editDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleSaveField">
          确定
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * FieldConfigEditor 组件脚本
 *
 * Props：
 * - pageId: 页面ID
 * - fields: 字段配置列表
 *
 * Events：
 * - update: 字段配置更新
 */
import { ref, computed, watch } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { ElMessage } from 'element-plus'
import { Plus, Check, Rank, Delete } from '@element-plus/icons-vue'
import draggable from 'vuedraggable'
import type { FieldConfig, FieldFormData, FieldOption } from '@/types'
import { CONTROL_TYPE_OPTIONS, createEmptyFieldFormData } from '@/types'
import { usePageConfigStore } from '@/stores'
import { v4 as uuidv4 } from 'uuid'

// ==================== Props & Emits ====================

interface Props {
  /** 页面ID */
  pageId: string
  /** 字段配置列表 */
  fields: FieldConfig[]
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'update', fields: FieldConfig[]): void
}>()

// ==================== Store ====================

const pageConfigStore = usePageConfigStore()

// ==================== Refs ====================

const fieldFormRef = ref<FormInstance>()

// ==================== State ====================

/**
 * 本地字段列表（用于编辑）
 */
const localFields = ref<FieldConfig[]>([])

/**
 * 字段编辑表单数据
 */
const fieldFormData = ref<FieldFormData>(createEmptyFieldFormData())

/**
 * 编辑对话框可见性
 */
const editDialogVisible = ref(false)

/**
 * 当前编辑的字段索引（-1表示新增）
 */
const editingIndex = ref(-1)

/**
 * 保存加载状态
 */
const saving = ref(false)

// ==================== 常量 ====================

/**
 * 控件类型选项
 */
const controlTypeOptions = CONTROL_TYPE_OPTIONS

/**
 * 字段表单验证规则
 */
const fieldFormRules: FormRules = {
  label: [
    { required: true, message: '请输入显示名称', trigger: 'blur' }
  ],
  fieldName: [
    { required: true, message: '请输入字段名', trigger: 'blur' },
    { pattern: /^[a-zA-Z][a-zA-Z0-9_]*$/, message: '字段名必须以字母开头，只能包含字母、数字和下划线', trigger: 'blur' }
  ],
  controlType: [
    { required: true, message: '请选择控件类型', trigger: 'change' }
  ]
}

// ==================== 计算属性 ====================

/**
 * 编辑对话框标题
 */
const editDialogTitle = computed(() => {
  return editingIndex.value === -1 ? '添加字段' : '编辑字段'
})

/**
 * 是否显示选项配置
 */
const showOptionsConfig = computed(() => {
  const optionTypes = ['select', 'multiSelect', 'radio', 'checkbox']
  return optionTypes.includes(fieldFormData.value.controlType)
})

const showRelationConfig = computed(() => {
  return fieldFormData.value.controlType === 'relation'
})

const showReferenceConfig = computed(() => {
  return fieldFormData.value.controlType === 'reference'
})

const showSequenceConfig = computed(() => {
  return fieldFormData.value.controlType === 'autoSequence'
})

const showQuoteConfig = computed(() => {
  return fieldFormData.value.controlType === 'quoteSelect'
})

const sequencePreview = computed(() => {
  const cfg = fieldFormData.value.sequenceConfig
  if (!cfg) return ''
  const padLen = String(cfg.max).length
  const first = `${cfg.prefix}${String(1).padStart(padLen, '0')}`
  const last = `${cfg.prefix}${String(cfg.max).padStart(padLen, '0')}`
  return `${first} ~ ${last}`
})

/**
 * 目标集合下拉选项（排除当前页面）
 */
const collectionOptions = computed(() => {
  return pageConfigStore.pageConfigs
    .filter((c) => c.id !== props.pageId)
    .map((c) => ({
      label: c.name,
      value: c.id.replace('page-', '')
    }))
})

/**
 * 选中目标集合的字段列表
 */
const targetCollectionFields = computed(() => {
  const tc = fieldFormData.value.relationConfig?.targetCollection
  if (!tc) return []
  const config = pageConfigStore.pageConfigs.find((c) => c.id === `page-${tc}`)
  if (!config) return []
  return config.fields
})

/**
 * 显示字段下拉选项（目标集合的非关联字段）
 */
const displayFieldOptions = computed(() => {
  return targetCollectionFields.value
    .filter((f) => f.controlType !== 'relation')
    .map((f) => ({
      label: f.label,
      value: f.fieldName
    }))
})

/**
 * 反向字段下拉选项（目标集合的关联字段）
 */
const targetFieldOptions = computed(() => {
  return targetCollectionFields.value
    .filter((f) => f.controlType === 'relation')
    .map((f) => ({
      label: f.label,
      value: f.fieldName
    }))
})

/**
 * 引用配置：选中目标集合的字段列表
 */
const refTargetCollectionFields = computed(() => {
  const tc = fieldFormData.value.referenceConfig?.targetCollection
  if (!tc) return []
  const config = pageConfigStore.pageConfigs.find((c) => c.id === `page-${tc}`)
  if (!config) return []
  return config.fields
})

/**
 * 引用配置：显示字段和继承字段的选项（目标集合的非关联/非引用字段）
 */
const refDisplayFieldOptions = computed(() => {
  return refTargetCollectionFields.value
    .filter((f) => !['relation', 'reference'].includes(f.controlType))
    .map((f) => ({
      label: f.label,
      value: f.fieldName
    }))
})

/**
 * 引用选择配置：选中目标集合的字段列表
 */
const quoteTargetCollectionFields = computed(() => {
  const tc = fieldFormData.value.quoteConfig?.targetCollection
  if (!tc) return []
  const config = pageConfigStore.pageConfigs.find((c) => c.id === `page-${tc}`)
  if (!config) return []
  return config.fields
})

/**
 * 引用选择配置：显示字段的选项（目标集合的非关联/非引用字段）
 */
const quoteDisplayFieldOptions = computed(() => {
  return quoteTargetCollectionFields.value
    .filter((f) => !['relation', 'reference', 'quoteSelect'].includes(f.controlType))
    .map((f) => ({
      label: f.label,
      value: f.fieldName
    }))
})

/**
 * 选项配置 - 数据页列表（可选择包含自身页面，因为选项数据和当前页面之间没有约束）
 */
const optionsCollectionList = computed(() => {
  return pageConfigStore.pageConfigs.map((c) => ({
    label: c.name,
    value: c.id.replace('page-', '')
  }))
})

/**
 * 选项配置 - 选中数据页的字段列表（非关联/非引用类字段）
 */
const optionsCollectionFieldList = computed(() => {
  const tc = fieldFormData.value.optionsSource?.collection
  if (!tc) return []
  const config = pageConfigStore.pageConfigs.find((c) => c.id === `page-${tc}`)
  if (!config) return []
  return config.fields
    .filter((f) => !['relation', 'reference', 'quoteSelect', 'file', 'image'].includes(f.controlType))
    .map((f) => ({
      label: f.label,
      value: f.fieldName
    }))
})

// ==================== 方法 ====================

/**
 * 获取控件类型标签
 */
function getControlTypeLabel(type: string): string {
  const option = controlTypeOptions.find((opt) => opt.value === type)
  return option?.label || type
}

/**
 * 处理目标集合变更，清空依赖字段
 */
function handleTargetCollectionChange(): void {
  if (fieldFormData.value.relationConfig) {
    fieldFormData.value.relationConfig.displayField = ''
    fieldFormData.value.relationConfig.targetField = ''
  }
}

/**
 * 处理引用目标集合变更，清空依赖字段
 */
function handleRefTargetCollectionChange(): void {
  if (fieldFormData.value.referenceConfig) {
    fieldFormData.value.referenceConfig.displayField = ''
    fieldFormData.value.referenceConfig.inheritFields = []
  }
}

/**
 * 处理引用选择目标集合变更，清空依赖字段
 */
function handleQuoteTargetCollectionChange(): void {
  if (fieldFormData.value.quoteConfig) {
    fieldFormData.value.quoteConfig.displayField = ''
  }
}

/**
 * 处理选项数据页变更，清空标签字段和值字段
 */
function handleOptionsCollectionChange(): void {
  fieldFormData.value.optionsSource.labelField = ''
  fieldFormData.value.optionsSource.valueField = ''
}

/**
 * 处理添加字段
 */
function handleAddField(): void {
  editingIndex.value = -1
  fieldFormData.value = createEmptyFieldFormData(localFields.value.length + 1)
  editDialogVisible.value = true
}

/**
 * 处理编辑字段
 */
function handleEditField(field: FieldConfig, index: number): void {
  editingIndex.value = index
  fieldFormData.value = {
    id: field.id,
    label: field.label,
    fieldName: field.fieldName,
    controlType: field.controlType,
    required: field.required,
    order: field.order,
    placeholder: field.placeholder || '',
    defaultValue: field.defaultValue,
    options: field.options ? [...field.options] : [],
    optionsSource: field.optionsSource
      ? { ...field.optionsSource }
      : { type: 'static' },
    relationConfig: field.relationConfig
      ? { ...field.relationConfig }
      : { targetCollection: '', displayField: '', targetField: '' },
    isPrimaryKey: field.isPrimaryKey || false,
    referenceConfig: field.referenceConfig
      ? { ...field.referenceConfig, inheritFields: [...(field.referenceConfig.inheritFields || [])] }
      : { targetCollection: '', displayField: '', inheritFields: [] },
    sequenceConfig: field.sequenceConfig
      ? { ...field.sequenceConfig }
      : { prefix: '', max: 999 },
    quoteConfig: field.quoteConfig
      ? { ...field.quoteConfig }
      : { targetCollection: '', displayField: '' }
  }
  editDialogVisible.value = true
}

/**
 * 处理删除字段
 */
function handleDeleteField(index: number): void {
  localFields.value.splice(index, 1)
  // 重新排序
  localFields.value.forEach((field, i) => {
    field.order = i + 1
  })
}

/**
 * 处理保存字段
 */
async function handleSaveField(): Promise<void> {
  const valid = await fieldFormRef.value?.validate()
  if (!valid) return

  const fieldData: FieldConfig = {
    id: fieldFormData.value.id || `field-${uuidv4().slice(0, 8)}`,
    label: fieldFormData.value.label,
    fieldName: fieldFormData.value.fieldName,
    controlType: fieldFormData.value.controlType,
    required: fieldFormData.value.required,
    order: fieldFormData.value.order,
    placeholder: fieldFormData.value.placeholder,
    defaultValue: fieldFormData.value.defaultValue,
    options: showOptionsConfig.value ? fieldFormData.value.options : undefined,
    optionsSource: showOptionsConfig.value ? fieldFormData.value.optionsSource : undefined,
    relationConfig: showRelationConfig.value ? fieldFormData.value.relationConfig : undefined,
    isPrimaryKey: fieldFormData.value.isPrimaryKey || undefined,
    referenceConfig: showReferenceConfig.value ? fieldFormData.value.referenceConfig : undefined,
    sequenceConfig: showSequenceConfig.value ? fieldFormData.value.sequenceConfig : undefined,
    quoteConfig: showQuoteConfig.value ? fieldFormData.value.quoteConfig : undefined
  }

  if (editingIndex.value === -1) {
    // 新增
    localFields.value.push(fieldData)
  } else {
    // 编辑
    localFields.value[editingIndex.value] = fieldData
  }

  editDialogVisible.value = false
  ElMessage.success('字段配置已更新，请点击「保存配置」按钮保存到服务器')
}

/**
 * 处理拖拽结束
 */
function handleDragEnd(): void {
  // 重新排序
  localFields.value.forEach((field, i) => {
    field.order = i + 1
  })
}

/**
 * 处理保存所有配置
 */
async function handleSaveAll(): Promise<void> {
  saving.value = true
  try {
    emit('update', [...localFields.value])
  } finally {
    saving.value = false
  }
}

/**
 * 添加选项
 */
function addOption(): void {
  fieldFormData.value.options.push({ label: '', value: '' })
}

/**
 * 移除选项
 */
function removeOption(index: number): void {
  fieldFormData.value.options.splice(index, 1)
}

// ==================== 监听 ====================

/**
 * 监听 props.fields 变化，同步到本地
 */
watch(
  () => props.fields,
  (newFields) => {
    localFields.value = newFields.map((f) => ({ ...f }))
  },
  { immediate: true, deep: true }
)
</script>

<style scoped lang="scss">
.field-config-editor {
  width: 100%;
}

.editor-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}

.field-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.field-item {
  display: flex;
  align-items: center;
  padding: 12px;
  background-color: #f5f7fa;
  border-radius: 4px;
  border: 1px solid #e4e7ed;

  &:hover {
    border-color: #c0c4cc;
  }

  .drag-handle {
    cursor: move;
    padding: 0 8px;
    color: #909399;

    &:hover {
      color: #409eff;
    }
  }

  .field-content {
    flex: 1;
    margin: 0 12px;

    .field-main {
      display: flex;
      align-items: center;
      gap: 8px;

      .field-label {
        font-weight: 500;
        color: #303133;
      }
    }

    .field-meta {
      font-size: 12px;
      color: #909399;
      margin-top: 4px;
    }
  }

  .field-actions {
    display: flex;
    gap: 4px;
  }
}

.options-config {
  width: 100%;

  .options-type {
    margin-bottom: 12px;
  }

  .static-options {
    .option-row {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 8px;
    }
  }

  .api-options {
    padding-top: 8px;
  }

  .collection-options {
    padding-top: 8px;
  }
}

.sequence-preview {
  margin-top: 8px;
  padding: 8px 12px;
  background-color: #f5f7fa;
  border-radius: 4px;
  font-size: 13px;
  color: #606266;
}
</style>
