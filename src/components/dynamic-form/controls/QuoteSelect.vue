<template>
  <div class="quote-select-wrapper">
    <el-select-v2
      v-model="selectValue"
      :options="options"
      :placeholder="field.placeholder || '请选择引用记录'"
      :disabled="field.disabled"
      :loading="loading"
      multiple
      clearable
      filterable
      remote
      :remote-method="onSearch"
      style="width: 100%"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import type { FieldConfig } from '@/types'
import { useRemoteCollectionOptions } from '@/composables/useRemoteCollectionOptions'

interface Props {
  field: FieldConfig
  modelValue: string[] | null
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: string[]): void
}>()

const { options, loading, onSearch, ensureSelectedLabels, init } = useRemoteCollectionOptions({
  collection: () => props.field.quoteConfig?.targetCollection,
  labelField: () => props.field.quoteConfig?.displayField || 'id',
})

const selectValue = computed({
  get: () => props.modelValue || [],
  set: (value) => emit('update:modelValue', value)
})

onMounted(() => init(props.modelValue || []))

watch(() => props.modelValue, (v) => ensureSelectedLabels(v || []))

watch(
  () => props.field.quoteConfig,
  () => init(props.modelValue || []),
  { deep: true }
)
</script>

<style scoped>
.quote-select-wrapper :deep(.el-select-v2 .el-select-v2__tags-text) {
  max-width: 200px;
}
.quote-select-wrapper :deep(.el-select-v2__wrapper) {
  max-height: 120px;
  overflow-y: auto;
}
</style>
