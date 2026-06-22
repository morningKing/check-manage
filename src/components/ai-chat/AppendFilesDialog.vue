<template>
  <ElDialog :model-value="modelValue" title="追加文件到批次" width="460px"
            @update:model-value="$emit('update:modelValue', $event)">
    <input ref="fileEl" type="file" multiple style="display:none" @change="onPick" />
    <ElButton :icon="Plus" @click="fileEl?.click()">选择文件</ElButton>
    <ul class="staged">
      <li v-for="f in staged" :key="f.path">{{ f.name }}</li>
    </ul>
    <p v-if="!staged.length" class="hint">每个文件会作为该批次的一个新子任务。</p>
    <template #footer>
      <ElButton @click="$emit('update:modelValue', false)">取消</ElButton>
      <ElButton type="primary" :loading="submitting" :disabled="!staged.length" @click="submit">追加</ElButton>
    </template>
  </ElDialog>
</template>
<script setup lang="ts">
import { ref } from 'vue'
import { ElDialog, ElButton, ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { stagingUpload } from '@/api/aiChatBatches'
import { useAiChatBatchesStore } from '@/stores/aiChatBatches'
import type { StagedFile } from '@/types/aiChatBatch'

const props = defineProps<{ modelValue: boolean; batchId: string }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'appended'): void }>()
const store = useAiChatBatchesStore()
const fileEl = ref<HTMLInputElement | null>(null)
const staged = ref<StagedFile[]>([])
const submitting = ref(false)
const usid = (crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2))

async function onPick(e: Event) {
  const files = (e.target as HTMLInputElement).files
  if (!files) return
  for (const f of Array.from(files)) {
    try { staged.value.push(await stagingUpload(f, usid)) }
    catch { ElMessage.error(`上传失败：${f.name}`) }
  }
  ;(e.target as HTMLInputElement).value = ''
}
async function submit() {
  submitting.value = true
  try {
    await store.appendToBatch(props.batchId, staged.value)
    ElMessage.success('已追加')
    staged.value = []
    emit('appended'); emit('update:modelValue', false)
  } catch (e: unknown) {
    const err = e as { response?: { data?: { error?: string } } }
    ElMessage.error(err?.response?.data?.error || '追加失败')
  } finally { submitting.value = false }
}
</script>
<style scoped>
.staged { list-style: none; padding: 0; margin: 8px 0; }
.staged li { padding: 4px 0; font-size: 13px; }
.hint { color: var(--el-text-color-secondary); font-size: 12px; }
</style>
