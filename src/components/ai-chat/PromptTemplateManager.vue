<template>
  <ElDrawer
    :model-value="modelValue"
    @update:model-value="$emit('update:modelValue', $event)"
    title="管理模板" size="480px">
    <div class="tpl-mgr">
      <div class="tpl-mgr__head">
        <ElButton type="primary" @click="startNew">+ 新模板</ElButton>
      </div>

      <div v-for="t in templates" :key="t.id" class="tpl"
           :class="{ active: editingId === t.id }">
        <div v-if="editingId === t.id" class="tpl__edit">
          <ElInput v-model="form.name" placeholder="模板名" />
          <ElInput v-model="form.content" type="textarea" :rows="5" placeholder="prompt 内容" />
          <div class="tpl__actions">
            <ElButton @click="cancelEdit">取消</ElButton>
            <ElButton type="primary" :loading="saving" @click="save">保存</ElButton>
          </div>
        </div>
        <div v-else class="tpl__row">
          <div>
            <div class="tpl__name">{{ t.name }}</div>
            <div class="tpl__preview">{{ truncated(t.content) }}</div>
          </div>
          <div class="tpl__actions">
            <ElButton link @click="startEdit(t)">编辑</ElButton>
            <ElButton link @click="remove(t)" type="danger">删除</ElButton>
          </div>
        </div>
      </div>

      <div v-if="editingId === '__new__'" class="tpl active">
        <div class="tpl__edit">
          <ElInput v-model="form.name" placeholder="模板名" />
          <ElInput v-model="form.content" type="textarea" :rows="5" placeholder="prompt 内容" />
          <div class="tpl__actions">
            <ElButton @click="cancelEdit">取消</ElButton>
            <ElButton type="primary" :loading="saving" @click="save">保存</ElButton>
          </div>
        </div>
      </div>
    </div>
  </ElDrawer>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElDrawer, ElInput, ElButton, ElMessage, ElMessageBox } from 'element-plus'
import {
  listTemplates, createTemplate, updateTemplate, deleteTemplate,
} from '@/api/aiChatPromptTemplates'
import type { AiChatPromptTemplate } from '@/types/aiChatBatch'

const props = defineProps<{ modelValue: boolean }>()
defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

const templates = ref<AiChatPromptTemplate[]>([])
const editingId = ref<string | null>(null)
const form = ref({ name: '', content: '' })
const saving = ref(false)

async function refresh() {
  templates.value = await listTemplates()
}

watch(() => props.modelValue, async (v) => {
  if (v) {
    editingId.value = null
    await refresh()
  }
})

function startNew() {
  editingId.value = '__new__'
  form.value = { name: '', content: '' }
}
function startEdit(t: AiChatPromptTemplate) {
  editingId.value = t.id
  form.value = { name: t.name, content: t.content }
}
function cancelEdit() {
  editingId.value = null
}

async function save() {
  if (!form.value.name.trim() || !form.value.content.trim()) {
    ElMessage.warning('名称和内容不能为空'); return
  }
  saving.value = true
  try {
    if (editingId.value === '__new__') {
      await createTemplate(form.value.name.trim(), form.value.content.trim())
    } else if (editingId.value) {
      await updateTemplate(editingId.value, form.value.name.trim(), form.value.content.trim())
    }
    editingId.value = null
    await refresh()
  } catch (e: any) {
    if (e?.response?.status === 409) ElMessage.error('已有同名模板')
    else ElMessage.error('保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(t: AiChatPromptTemplate) {
  try {
    await ElMessageBox.confirm(`删除模板「${t.name}」?`, '确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch { return }
  await deleteTemplate(t.id)
  await refresh()
}

function truncated(s: string) { return s.length > 120 ? s.slice(0, 120) + '…' : s }
</script>

<style scoped lang="scss">
.tpl-mgr { display: flex; flex-direction: column; gap: 10px; padding: 10px; }
.tpl-mgr__head { margin-bottom: 8px; }
.tpl { border: 1px solid var(--el-border-color-light); border-radius: 8px; padding: 10px; }
.tpl.active { border-color: var(--el-color-primary); }
.tpl__row { display: flex; justify-content: space-between; gap: 12px; }
.tpl__name { font-weight: 600; }
.tpl__preview { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 2px; white-space: pre-wrap; }
.tpl__edit { display: flex; flex-direction: column; gap: 8px; }
.tpl__actions { display: flex; gap: 6px; justify-content: flex-end; }
</style>
