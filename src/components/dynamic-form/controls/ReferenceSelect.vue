<template>
  <el-select-v2
    v-model="selectValue"
    :options="options"
    :placeholder="field.placeholder || '请选择引用记录'"
    :disabled="field.disabled"
    :loading="loading"
    clearable
    filterable
    remote
    :remote-method="onSearch"
    style="width: 100%"
  />
</template>

<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import type { FieldConfig } from '@/types'
import { useRemoteCollectionOptions } from '@/composables/useRemoteCollectionOptions'

interface Props {
  field: FieldConfig
  modelValue: string | null
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: string | null): void
}>()

const { options, loading, onSearch, ensureSelectedLabels, init } = useRemoteCollectionOptions({
  collection: () => props.field.referenceConfig?.targetCollection,
  labelField: () => props.field.referenceConfig?.displayField || 'id',
})

const selectValue = computed({
  get: () => props.modelValue || '',
  set: (value) => emit('update:modelValue', value || null)
})

onMounted(() => init([props.modelValue]))

watch(() => props.modelValue, (v) => ensureSelectedLabels([v]))

watch(
  () => props.field.referenceConfig,
  () => init([props.modelValue]),
  { deep: true }
)
</script>
