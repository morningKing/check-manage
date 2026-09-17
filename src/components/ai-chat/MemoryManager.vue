<template>
  <ElDrawer
    :model-value="modelValue"
    @update:model-value="$emit('update:modelValue', $event)"
    title="我的长期记忆" size="520px" @open="load">
    <div v-loading="loading">
      <!-- 手动添加（P2 §7.3）：明确「原样保存 / AI 提炼」两种模式 -->
      <div class="mem-add">
        <ElInput v-model="draft" type="textarea" :rows="2" :maxlength="2000" show-word-limit
          placeholder="写一句话关键事实，如：负责 PostgreSQL 运维" />
        <div class="mem-add__bar">
          <ElSwitch v-model="verbatim" />
          <span class="mem-add__hint">原样保存（不提炼）— 默认会被 AI 提炼成简洁事实，原样适合一句话关键事实</span>
          <ElButton type="primary" size="small" :loading="adding"
                    :disabled="adding || !draft.trim()" @click="add">添加</ElButton>
        </div>
      </div>

      <!-- 检索 + 来源筛选（P2 §7.2） -->
      <div class="mem-filters">
        <ElInput v-model="search" clearable size="small" placeholder="搜索记忆内容…"
                 data-test="mem-search" :prefix-icon="Search" />
        <ElSelect v-model="sourceFilter" size="small" clearable placeholder="全部来源" style="width: 150px">
          <ElOption label="手动添加" value="manual" />
          <ElOption label="对话自动提取" value="conversation" />
          <ElOption label="批任务自动提取" value="batch" />
        </ElSelect>
      </div>

      <!-- 加载失败 ≠ 暂无记忆（P2 §7.6） -->
      <div v-if="loadError" class="mem-error" data-test="mem-error">
        <span>记忆加载失败</span>
        <ElButton size="small" data-test="mem-retry" @click="load">重新加载</ElButton>
      </div>
      <p v-else-if="!loading && !filtered.length" class="empty">
        {{ search || sourceFilter ? '没有匹配的记忆' : '暂无长期记忆。' }}
      </p>

      <template v-else>
        <ul class="mem-list">
          <li v-for="m in visibleItems" :key="m.id" :data-mem-id="m.id">
            <div class="mem-body">
              <span class="mem-text">{{ m.memory }}</span>
              <span class="mem-meta">
                <span class="mem-source" :class="`mem-source--${sourceOf(m)}`">{{ sourceLabel(m) }}</span>
                <span v-if="fmtTs(createdOf(m))" class="mem-time">{{ fmtTs(createdOf(m)) }}</span>
              </span>
            </div>
            <ElButton link type="danger" size="small" :loading="deletingId === m.id"
                      @click="remove(m)">删除</ElButton>
          </li>
        </ul>
        <div v-if="filtered.length > visibleCount" class="mem-more">
          <ElButton size="small" @click="visibleCount += PAGE">加载更多（已显示 {{ visibleCount }}/{{ filtered.length }}）</ElButton>
        </div>
      </template>
    </div>
  </ElDrawer>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { ElDrawer, ElButton, ElInput, ElSelect, ElOption, ElSwitch, ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { listMemories, deleteMemory, addMemory, type AiMemory } from '@/api/aiChat'

defineProps<{ modelValue: boolean }>()
defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

const PAGE = 50

const items = ref<AiMemory[]>([])
const loading = ref(false)
const loadError = ref(false)
const draft = ref('')
const verbatim = ref(false)
const adding = ref(false)
const deletingId = ref<string | null>(null)
const search = ref('')
const sourceFilter = ref<string>('')
const visibleCount = ref(PAGE)

// P2 §7.4：来源展示；mem0 老条目没有来源 → 系统记忆
function sourceOf(m: AiMemory): string {
  const rec = m as unknown as Record<string, unknown>
  const meta = rec.metadata as Record<string, unknown> | null | undefined
  const s = rec.source ?? meta?.source
  return typeof s === 'string' && s ? s : 'system'
}
function sourceLabel(m: AiMemory): string {
  return ({ manual: '手动添加', conversation: '对话自动提取', batch: '批任务自动提取' } as Record<string, string>)[sourceOf(m)] || '系统记忆'
}
function createdOf(m: AiMemory): string | null {
  const rec = m as unknown as Record<string, unknown>
  const v = rec.created_at ?? rec.createdAt ?? null
  return typeof v === 'string' ? v : null
}
function fmtTs(v: string | null): string {
  if (!v) return ''
  const d = new Date(v.includes('T') ? v : v.replace(' ', 'T'))
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString()
}

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  return items.value.filter((m) => {
    if (q && !m.memory.toLowerCase().includes(q)) return false
    if (sourceFilter.value && sourceOf(m) !== sourceFilter.value) return false
    return true
  })
})
const visibleItems = computed(() => filtered.value.slice(0, visibleCount.value))

async function load() {
  loading.value = true
  loadError.value = false
  visibleCount.value = PAGE
  try {
    items.value = (await listMemories()).memories || []
  } catch {
    // P2 §7.6：失败绝不渲染成「暂无长期记忆」
    loadError.value = true
  } finally {
    loading.value = false
  }
}

async function add() {
  const text = draft.value.trim()
  if (!text || adding.value) return
  adding.value = true
  try {
    const res = await addMemory(text, verbatim.value)
    items.value = res.memories || items.value
    // P2 §7.3：成功后定位到新记忆（列表首条附近即新加条目，这里滚回顶部）
    draft.value = ''
    ElMessage.success('已添加')
    if (items.value.length) {
      const first = document.querySelector('.mem-list li')
      first?.scrollIntoView({ block: 'nearest' })
    }
  } catch (e: unknown) {
    // 失败保留用户输入，便于重试
    const ax = e as { response?: { data?: { error?: { message?: string } | string } } }
    const raw = ax?.response?.data?.error
    ElMessage.error((typeof raw === 'string' ? raw : raw?.message) || '添加失败')
  } finally {
    adding.value = false
  }
}

async function remove(m: AiMemory) {
  if (deletingId.value) return
  try {
    await ElMessageBox.confirm('删除这条长期记忆？删除后 AI 将不再使用它。', '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch { return }
  deletingId.value = m.id
  try {
    await deleteMemory(m.id)
    items.value = items.value.filter((x) => x.id !== m.id)
    ElMessage.success('已删除')
  } catch {
    // P2 §7.5：删除失败保留列表项并给出重试
    ElMessage.error('删除失败，请重试')
  } finally {
    deletingId.value = null
  }
}
</script>

<style scoped>
.mem-list { list-style: none; padding: 0; margin: 0; }
.mem-list li { display: flex; align-items: flex-start; justify-content: space-between;
  gap: 12px; padding: 10px 4px; border-bottom: 1px solid var(--el-border-color-lighter); }
.mem-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.mem-text { word-break: break-word; }
.mem-meta { display: flex; gap: 8px; align-items: center; }
.mem-source { font-size: 11px; padding: 0 6px; border-radius: 6px;
  background: var(--el-fill-color); color: var(--el-text-color-secondary); }
.mem-source--manual { background: var(--el-color-primary-light-9); color: var(--el-color-primary); }
.mem-source--conversation { background: var(--el-color-success-light-9); color: var(--el-color-success); }
.mem-source--batch { background: var(--el-color-warning-light-9); color: var(--el-color-warning); }
.mem-time { font-size: 11px; color: var(--el-text-color-placeholder); }
.mem-filters { display: flex; gap: 8px; margin-bottom: 10px; }
.mem-error { display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 10px 12px; margin-bottom: 10px; border: 1px solid var(--el-color-danger-light-7);
  border-radius: 8px; color: var(--el-color-danger); font-size: 13px;
  background: var(--el-color-danger-light-9); }
.empty { color: var(--el-text-color-secondary); padding: 16px 4px; }
.mem-more { display: flex; justify-content: center; padding: 10px 0; }
.mem-add { margin-bottom: 12px; }
.mem-add__bar { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.mem-add__hint { flex: 1; font-size: 12px; color: var(--el-text-color-secondary); }
</style>
