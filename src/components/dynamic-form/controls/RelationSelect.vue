<template>
  <div class="relation-select-wrapper">
    <el-select-v2
      v-model="selectValue"
      :options="options"
      :placeholder="field.placeholder || '请选择关联记录'"
      :disabled="field.disabled"
      :loading="loading"
      multiple
      clearable
      filterable
      style="width: 100%"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted, watch } from 'vue'
import type { FieldConfig } from '@/types'
import { get } from '@/utils/request'

interface SelectOption {
  label: string
  value: string
}

interface Props {
  field: FieldConfig
  modelValue: string[] | null
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: string[]): void
}>()

const options = ref<SelectOption[]>([])
const loading = ref(false)

const selectValue = computed({
  get: () => props.modelValue || [],
  set: (value) => emit('update:modelValue', value)
})

async function loadOptions(): Promise<void> {
  const config = props.field.relationConfig
  if (!config || !config.targetCollection) {
    options.value = []
    return
  }

  loading.value = true
  try {
    const response = await get<{ data: any[]; total: number }>(`/${config.targetCollection}`, { pageSize: 10000 })
    const data = response.data || []
    options.value = data.map((item) => ({
      label: item[config.displayField] || item.id,
      value: item.id
    }))
  } catch (error) {
    console.error('加载关联选项失败:', error)
    options.value = []
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadOptions()
})

watch(
  () => props.field.relationConfig,
  () => loadOptions(),
  { deep: true }
)
</script>

<style scoped>
.relation-select-wrapper :deep(.el-select-v2 .el-select-v2__tags-text) {
  max-width: 200px;
}
.relation-select-wrapper :deep(.el-select-v2__wrapper) {
  max-height: 120px;
  overflow-y: auto;
}
</style>
