<!--
 * 我的待办 Widget
 *
 * 展示当前用户的工作流待办（收件箱）。点击某条 → 跳转到对应记录的数据页并定位、
 * 高亮（复用 jumpNavigation 机制）；无关联记录时跳转到「我的待办」页面。
 -->
<template>
  <el-card class="todo-widget" v-loading="loading">
    <template #header>
      <div class="todo-head">
        <span class="todo-title">{{ title || '我的待办' }}</span>
        <el-badge v-if="items.length" :value="items.length" type="danger" />
        <span class="todo-more" @click="goInbox">全部</span>
      </div>
    </template>

    <div v-if="visibleItems.length" class="todo-list">
      <div
        v-for="item in visibleItems"
        :key="item.instanceId"
        class="todo-row"
        :title="`${item.workflowName} · ${item.stageName}`"
        @click="goTo(item)"
      >
        <el-icon class="todo-dot"><Clock /></el-icon>
        <span class="todo-text">
          <span class="wf-name">{{ item.workflowName }}</span>
          <el-tag size="small" class="stage-tag">{{ item.stageName }}</el-tag>
        </span>
        <span class="todo-time">{{ formatTime(item.enteredAt) }}</span>
      </div>
    </div>
    <el-empty v-else description="暂无待办" :image-size="60" />
  </el-card>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Clock } from '@element-plus/icons-vue'
import { getInbox } from '@/api/workflow'
import { useMenuStore } from '@/stores/menu'
import { useJumpNavigationStore } from '@/stores/jumpNavigation'
import type { WidgetContentMap } from '@/types'
import type { WorkflowInboxItem } from '@/types/workflow'

const props = defineProps<{
  content: WidgetContentMap['todo']
  title?: string
}>()

const router = useRouter()
const menuStore = useMenuStore()
const jumpStore = useJumpNavigationStore()

const loading = ref(false)
const items = ref<WorkflowInboxItem[]>([])

const visibleItems = computed(() => items.value.slice(0, props.content.limit || 5))

function formatTime(value: string | null): string {
  if (!value) return ''
  const d = new Date(value)
  if (isNaN(d.getTime())) return ''
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function goInbox(): void {
  router.push('/workflow/inbox')
}

async function goTo(item: WorkflowInboxItem): Promise<void> {
  if (!item.collection || !item.recordId) {
    goInbox()
    return
  }
  if (menuStore.menuList.length === 0) {
    try { await menuStore.fetchMenus() } catch { /* non-fatal */ }
  }
  const targetMenu = menuStore.menuList.find(m => m.pageId === `page-${item.collection}`)
  if (!targetMenu?.path) {
    goInbox()
    return
  }
  jumpStore.setJump({
    targetCollection: item.collection,
    targetRecordId: item.recordId,
    jumpType: 'reference',
    sourcePageId: '',
  })
  router.push({ path: targetMenu.path })
}

async function fetchInbox(): Promise<void> {
  loading.value = true
  try {
    items.value = await getInbox()
  } catch (error) {
    console.error('待办加载失败:', error)
    items.value = []
  } finally {
    loading.value = false
  }
}

onMounted(fetchInbox)
</script>

<style scoped lang="scss">
.todo-head {
  display: flex;
  align-items: center;
  gap: 10px;

  .todo-title { font-weight: 600; }
  .todo-more {
    margin-left: auto;
    font-size: 13px;
    color: var(--el-color-primary);
    cursor: pointer;
    &:hover { text-decoration: underline; }
  }
}

.todo-list { display: flex; flex-direction: column; }

.todo-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 6px;
  font-size: 13px;
  border-radius: 4px;
  cursor: pointer;
  transition: background-color 0.15s;

  &:hover { background-color: var(--el-fill-color-light); }

  .todo-dot { color: var(--el-color-warning); flex-shrink: 0; }
  .todo-text { flex: 1; min-width: 0; display: flex; align-items: center; gap: 6px; overflow: hidden; }
  .wf-name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--el-text-color-primary); }
  .stage-tag { flex-shrink: 0; }
  .todo-time { flex-shrink: 0; font-size: 12px; color: var(--el-text-color-placeholder); }
}
</style>
