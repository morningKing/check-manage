<template>
  <ElDrawer
    :model-value="modelValue"
    @update:model-value="$emit('update:modelValue', $event)"
    title="我的长期记忆" size="480px" @open="load">
    <div v-loading="loading">
      <div class="mem-add">
        <ElInput v-model="draft" type="textarea" :rows="2" :maxlength="2000" show-word-limit
          placeholder="写一句话关键事实，如：负责 PostgreSQL 运维" />
        <div class="mem-add__bar">
          <ElSwitch v-model="verbatim" />
          <span class="mem-add__hint">原样保存（不提炼）— 默认会被 AI 提炼成简洁事实，原样适合一句话关键事实</span>
          <ElButton type="primary" size="small" :loading="adding" :disabled="!draft.trim()" @click="add">添加</ElButton>
        </div>
      </div>
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
import { ElDrawer, ElButton, ElInput, ElSwitch, ElMessage, ElMessageBox } from 'element-plus'
import { listMemories, deleteMemory, addMemory, type AiMemory } from '@/api/aiChat'

defineProps<{ modelValue: boolean }>()
defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

const items = ref<AiMemory[]>([])
const loading = ref(false)
const draft = ref('')
const verbatim = ref(false)
const adding = ref(false)

async function load() {
  loading.value = true
  try { items.value = (await listMemories()).memories || [] }
  catch { items.value = [] }
  finally { loading.value = false }
}

async function add() {
  const text = draft.value.trim()
  if (!text) return
  adding.value = true
  try {
    const res = await addMemory(text, verbatim.value)
    items.value = res.memories || []
    draft.value = ''
    ElMessage.success('已添加')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || '添加失败')
  } finally {
    adding.value = false
  }
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
.mem-add { margin-bottom: 12px; }
.mem-add__bar { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.mem-add__hint { flex: 1; font-size: 12px; color: var(--el-text-color-secondary); }
</style>
