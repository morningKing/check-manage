/**
 * 多选下拉控件
 *
 * 职责：
 * - 渲染多选下拉框
 * - 支持静态选项和 API 动态获取选项
 * - 静态选项使用 el-select，动态选项使用 el-select-v2（虚拟滚动）
 */
<template>
  <!-- 静态少量选项使用 el-select -->
  <el-select
    v-if="useBasicSelect"
    v-model="selectValue"
    :placeholder="field.placeholder || '请选择'"
    :disabled="field.disabled"
    :loading="loading"
    multiple
    collapse-tags
    collapse-tags-tooltip
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
  <!-- 动态/大量选项使用 el-select-v2（虚拟滚动，避免大量 DOM 导致页面卡死） -->
  <el-select-v2
    v-else
    v-model="selectValue"
    :options="v2Options"
    :placeholder="field.placeholder || '请选择'"
    :disabled="field.disabled"
    :loading="loading"
    multiple
    collapse-tags
    collapse-tags-tooltip
    clearable
    filterable
    style="width: 100%"
  />
</template>

<script setup lang="ts">
import { computed, ref, onMounted, watch } from 'vue'
import type { FieldConfig, FieldOption } from '@/types'
import { get } from '@/utils/request'

// ==================== Props & Emits ====================

interface Props {
  field: FieldConfig
  modelValue: Array<string | number | boolean> | null
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: Array<string | number | boolean>): void
}>()

// ==================== State ====================

const options = ref<FieldOption[]>([])
const loading = ref(false)
const useBasicSelect = ref(true)

// ==================== 计算属性 ====================

const selectValue = computed({
  get: () => props.modelValue || [],
  set: (value) => emit('update:modelValue', value)
})

/** el-select-v2 要求 { label, value } 格式 */
const v2Options = computed(() =>
  options.value.map((o) => ({ label: String(o.label), value: o.value }))
)

// ==================== 方法 ====================

async function loadOptions(): Promise<void> {
  const source = props.field.optionsSource

  if (!source || source.type === 'static') {
    options.value = props.field.options || []
    useBasicSelect.value = true
    return
  }

  // API / collection 来源使用虚拟滚动
  useBasicSelect.value = false

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

  if (source.type === 'collection' && source.collection) {
    loading.value = true
    try {
      const response = await get<{ data: any[]; total: number }>(`/${source.collection}`, { pageSize: 10000 })
      const data = response.data || []
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

watch(
  () => props.field.optionsSource,
  () => loadOptions(),
  { deep: true }
)
</script>
