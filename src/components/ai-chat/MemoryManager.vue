<template>
  <ElDrawer
    :model-value="modelValue"
    @update:model-value="$emit('update:modelValue', $event)"
    title="我的长期记忆" size="480px" @open="load">
    <div v-loading="loading">
      <p v-if="!items.length" class="empty">暂无长期记忆。</p>
      <ul v-else class="mem-list">
        <li v-for="m in items" :key="m.id">
          <span class="mem-text">{{ m.memory }}</span>
          <ElButton link type="danger" size="small" @click="remove(m.id)">删除</ElButton>
        </li>
      </ul>
    </div>
  </ElDrawer>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElDrawer, ElButton, ElMessage, ElMessageBox } from 'element-plus'
import { listMemories, deleteMemory, type AiMemory } from '@/api/aiChat'

defineProps<{ modelValue: boolean }>()
defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

const items = ref<AiMemory[]>([])
const loading = ref(false)

async function load() {
  loading.value = true
  try { items.value = (await listMemories()).memories || [] }
  catch { items.value = [] }
  finally { loading.value = false }
}

async function remove(id: string) {
  try {
    await ElMessageBox.confirm('确定删除这条记忆？', '提示', { type: 'warning' })
  } catch { return }
  await deleteMemory(id)
  items.value = items.value.filter((m) => m.id !== id)
  ElMessage.success('已删除')
}
</script>

<style scoped>
.mem-list { list-style: none; padding: 0; margin: 0; }
.mem-list li { display: flex; align-items: center; justify-content: space-between;
  gap: 12px; padding: 10px 4px; border-bottom: 1px solid var(--el-border-color-lighter); }
.mem-text { flex: 1; word-break: break-word; }
.empty { color: var(--el-text-color-secondary); padding: 16px 4px; }
</style>
