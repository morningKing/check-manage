<template>
  <ElDrawer
    :model-value="modelValue"
    @update:model-value="$emit('update:modelValue', $event)"
    title="管理模板" size="520px">
    <div class="tpl-mgr">
      <div class="tpl-mgr__head">
        <ElButton type="primary" @click="startNew">+ 新模板</ElButton>
      </div>

      <!-- P2 §6.2：检索 + 筛选（全部/收藏/最近使用） -->
      <div class="tpl-mgr__filters">
        <ElInput v-model="search" clearable placeholder="按名称/内容搜索…"
                 data-test="tpl-search" :prefix-icon="Search" />
        <ElRadioGroup v-model="filter" size="small">
          <ElRadioButton value="all">全部</ElRadioButton>
          <ElRadioButton value="fav">收藏</ElRadioButton>
          <ElRadioButton value="recent">最近使用</ElRadioButton>
        </ElRadioGroup>
      </div>

      <!-- 加载失败 ≠ 空列表（P2 §6.5）：失败态给出重试入口 -->
      <div v-if="loadError" class="tpl-mgr__error" data-test="tpl-error">
        <span>模板加载失败</span>
        <ElButton size="small" data-test="tpl-retry" @click="refresh">重新加载</ElButton>
      </div>
      <p v-else-if="!filtered.length" class="tpl-mgr__empty">
        {{ search || filter !== 'all' ? '没有匹配的模板' : '暂无模板，点击「+ 新模板」创建。' }}
      </p>

      <div v-for="t in filtered" :key="t.id" class="tpl"
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
          <div class="tpl__main" @click="togglePreview(t)">
            <div class="tpl__name-line">
              <span class="tpl__star" :class="{ on: isFav(t.id) }" role="button"
                    :title="isFav(t.id) ? '取消收藏' : '收藏'"
                    @click.stop="onToggleFav(t.id)">{{ isFav(t.id) ? '★' : '☆' }}</span>
              <span class="tpl__name">{{ t.name }}</span>
              <span v-if="lastUsed(t.id)" class="tpl__recent" :title="fmtTs(lastUsed(t.id))">最近使用</span>
            </div>
            <div class="tpl__preview">{{ truncated(t.content) }}</div>
            <div v-if="previewId === t.id" class="tpl__full">{{ t.content }}</div>
            <div class="tpl__meta">{{ fmtTs(t.created_at) }}<template v-if="lastUsed(t.id)"> · 上次使用 {{ fmtTs(lastUsed(t.id)) }}</template></div>
          </div>
          <div class="tpl__actions tpl__actions--col">
            <ElButton link type="primary" @click="$emit('apply', t); onUse(t)">插入</ElButton>
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
import { ref, computed, watch } from 'vue'
import { ElDrawer, ElInput, ElButton, ElMessage, ElMessageBox, ElRadioGroup, ElRadioButton } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import {
  listTemplates, createTemplate, updateTemplate, deleteTemplate,
} from '@/api/aiChatPromptTemplates'
import type { AiChatPromptTemplate } from '@/types/aiChatBatch'
import { isFavorite, toggleFavorite, lastUsedAt, recordTemplateUse, forgetTemplate } from '@/utils/templatePrefs'

const props = defineProps<{ modelValue: boolean }>()
defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'apply', t: AiChatPromptTemplate): void }>()

const templates = ref<AiChatPromptTemplate[]>([])
const editingId = ref<string | null>(null)
const form = ref({ name: '', content: '' })
const saving = ref(false)
const loadError = ref(false)

// P2 §6.2：检索 / 筛选 / 收藏 / 最近使用（本地记录）
const search = ref('')
const filter = ref<'all' | 'fav' | 'recent'>('all')
const previewId = ref<string | null>(null)
const favVersion = ref(0)  // 触发收藏态重算

const filtered = computed(() => {
  void favVersion.value
  const q = search.value.trim().toLowerCase()
  let list = templates.value.filter((t) => {
    if (q && !(t.name.toLowerCase().includes(q) || t.content.toLowerCase().includes(q))) return false
    if (filter.value === 'fav') return isFavorite(t.id)
    if (filter.value === 'recent') return lastUsedAt(t.id) != null
    return true
  })
  // 排序：收藏优先 → 最近使用时间 → 创建时间倒序
  list = [...list].sort((a, b) => {
    const favDiff = Number(isFavorite(b.id)) - Number(isFavorite(a.id))
    if (favDiff) return favDiff
    const ru = lastUsedAt(b.id) ?? 0
    const rl = lastUsedAt(a.id) ?? 0
    if (ru !== rl) return ru - rl
    return (b.created_at || '').localeCompare(a.created_at || '')
  })
  return list
})

function isFav(id: string) { void favVersion.value; return isFavorite(id) }
function lastUsed(id: string) { void favVersion.value; return lastUsedAt(id) }
function onToggleFav(id: string) { toggleFavorite(id); favVersion.value++ }
function onUse(t: AiChatPromptTemplate) { recordTemplateUse(t.id); favVersion.value++ }

async function refresh() {
  loadError.value = false
  try {
    templates.value = await listTemplates()
  } catch {
    // P2 §6.5：失败绝不渲染成空列表
    loadError.value = true
  }
}

watch(() => props.modelValue, async (v) => {
  if (v) {
    editingId.value = null
    search.value = ''
    filter.value = 'all'
    previewId.value = null
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
function togglePreview(t: AiChatPromptTemplate) {
  previewId.value = previewId.value === t.id ? null : t.id
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
  try {
    await deleteTemplate(t.id)
    forgetTemplate(t.id)
    favVersion.value++
    await refresh()
    ElMessage.success('已删除')
  } catch {
    // P2 §6.5：删除失败时模板必须留在列表里
    ElMessage.error('删除失败，请重试')
  }
}

function truncated(s: string) { return s.length > 120 ? s.slice(0, 120) + '…' : s }
function fmtTs(v: string | number | null): string {
  if (!v) return '—'
  const d = typeof v === 'number' ? new Date(v) : new Date(String(v).replace(' ', 'T'))
  return Number.isNaN(d.getTime()) ? String(v) : d.toLocaleString()
}
</script>

<style scoped lang="scss">
.tpl-mgr { display: flex; flex-direction: column; gap: 10px; padding: 10px; }
.tpl-mgr__head { margin-bottom: 4px; }
.tpl-mgr__filters { display: flex; flex-direction: column; gap: 8px; }
.tpl-mgr__error {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 10px 12px; border: 1px solid var(--el-color-danger-light-7);
  border-radius: 8px; color: var(--el-color-danger); font-size: 13px;
  background: var(--el-color-danger-light-9);
}
.tpl-mgr__empty { color: var(--el-text-color-secondary); padding: 12px 4px; }
.tpl { border: 1px solid var(--el-border-color-light); border-radius: 8px; padding: 10px; }
.tpl.active { border-color: var(--el-color-primary); }
.tpl__row { display: flex; justify-content: space-between; gap: 12px; }
.tpl__main { flex: 1; min-width: 0; cursor: pointer; }
.tpl__name-line { display: flex; align-items: center; gap: 6px; }
.tpl__star { cursor: pointer; color: var(--el-text-color-placeholder); &.on { color: var(--el-color-warning); } }
.tpl__name { font-weight: 600; }
.tpl__recent { font-size: 11px; color: var(--el-color-success);
  background: var(--el-color-success-light-9); border-radius: 6px; padding: 0 6px; }
.tpl__preview { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 2px; white-space: pre-wrap; }
.tpl__full {
  margin-top: 6px; padding: 8px; font-size: 12px; white-space: pre-wrap;
  background: var(--el-fill-color-light); border-radius: 6px; word-break: break-word;
}
.tpl__meta { margin-top: 4px; font-size: 11px; color: var(--el-text-color-placeholder); }
.tpl__edit { display: flex; flex-direction: column; gap: 8px; }
.tpl__actions { display: flex; gap: 6px; justify-content: flex-end; }
.tpl__actions--col { flex-direction: column; justify-content: flex-start; flex: 0 0 auto; }
</style>
