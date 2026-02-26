/**
 * 单选下拉控件
 *
 * 职责：
 * - 渲染单选下拉框
 * - 支持静态选项和 API 动态获取选项
 * - 支持 placeholder 和禁用状态
 */
<template>
  <el-select
    v-model="selectValue"
    :placeholder="field.placeholder || '请选择'"
    :disabled="field.disabled"
    :loading="loading"
    clearable
    filterable
    style="width: 100%"
  >
    <el-option
      v-for="option in options"
      :key="String(option.value)"
      :label="option.label"
      :value="option.value"
    />
  </el-select>
</template>

<script setup lang="ts">
/**
 * SelectInput 组件
 *
 * 基于 Element Plus Select 组件封装
 * 用于动态表单中的单选下拉
 *
 * 选项来源：
 * 1. 静态配置：从 field.options 获取
 * 2. API 获取：从 field.optionsSource.url 请求数据
 */
import { computed, ref, onMounted, watch } from 'vue'
import type { FieldConfig, FieldOption } from '@/types'
import { get } from '@/utils/request'

// ==================== Props & Emits ====================

interface Props {
  /** 字段配置 */
  field: FieldConfig
  /** 当前值 */
  modelValue: string | number | boolean | null
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: string | number | boolean | null): void
}>()

// ==================== State ====================

/**
 * 选项列表
 */
const options = ref<FieldOption[]>([])

/**
 * 加载状态
 */
const loading = ref(false)

// ==================== 计算属性 ====================

/**
 * 双向绑定的选择值
 */
const selectValue = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value)
})

// ==================== 方法 ====================

/**
 * 加载选项数据
 *
 * 根据配置决定是使用静态选项、从 API 获取或从数据页获取
 */
async function loadOptions(): Promise<void> {
  const source = props.field.optionsSource

  // 静态选项
  if (!source || source.type === 'static') {
    options.value = props.field.options || []
    return
  }

  // API 获取选项
  if (source.type === 'api' && source.url) {
    loading.value = true
    try {
      const data = await get<any[]>(source.url)
      options.value = data.map((item) => ({
        label: item[source.labelField || 'label'],
        value: item[source.valueField || 'value']
      }))
    } catch (error) {
      console.error('加载选项失败:', error)
      options.value = []
    } finally {
      loading.value = false
    }
  }

  // 数据页数据获取选项
  if (source.type === 'collection' && source.collection) {
    loading.value = true
    try {
      const data = await get<any[]>(`/${source.collection}`)
      options.value = data.map((item) => ({
        label: String(item[source.labelField || 'id'] ?? item.id),
        value: item[source.valueField || 'id'] ?? item.id
      }))
    } catch (error) {
      console.error('加载数据页选项失败:', error)
      options.value = []
    } finally {
      loading.value = false
    }
  }
}

// ==================== 生命周期 ====================

onMounted(() => {
  loadOptions()
})

// 监听字段配置变化，重新加载选项
watch(
  () => props.field.optionsSource,
  () => loadOptions(),
  { deep: true }
)
</script>
