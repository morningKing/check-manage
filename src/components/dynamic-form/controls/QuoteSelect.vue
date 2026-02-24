<template>
  <div class="quote-select-wrapper">
    <el-select
      v-model="selectValue"
      :placeholder="field.placeholder || '请选择引用记录'"
      :disabled="field.disabled"
      :loading="loading"
      multiple
      clearable
      filterable
      style="width: 100%"
    >
      <el-option
        v-for="option in options"
        :key="option.value"
        :label="option.label"
        :value="option.value"
      />
    </el-select>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted, watch } from 'vue'
import type { FieldConfig, FieldOption } from '@/types'
import { get } from '@/utils/request'

interface Props {
  field: FieldConfig
  modelValue: string[] | null
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: string[]): void
}>()

const options = ref<FieldOption[]>([])
const loading = ref(false)

const selectValue = computed({
  get: () => props.modelValue || [],
  set: (value) => emit('update:modelValue', value)
})

async function loadOptions(): Promise<void> {
  const config = props.field.quoteConfig
  if (!config || !config.targetCollection) {
    options.value = []
    return
  }

  loading.value = true
  try {
    const data = await get<any[]>(`/${config.targetCollection}`)
    options.value = data.map((item) => ({
      label: item[config.displayField] || item.id,
      value: item.id
    }))
  } catch (error) {
    console.error('加载引用选项失败:', error)
    options.value = []
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadOptions()
})

watch(
  () => props.field.quoteConfig,
  () => loadOptions(),
  { deep: true }
)
</script>

<style scoped>
.quote-select-wrapper :deep(.el-select .el-select__tags) {
  max-height: 120px;
  overflow-y: auto;
}
</style>
