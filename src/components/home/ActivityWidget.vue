<!--
 * 最近动态 Widget
 *
 * 展示最近的操作日志（谁在何时改了什么）。
 * 注意：/operationLogs 接口需要 admin.operation_logs 权限，因此该区块实际仅对
 * 有该权限的角色有效；其他角色会拿到 403，组件优雅降级为空状态。
 -->
<template>
  <el-card class="activity-widget" v-loading="loading">
    <template #header>
      <span class="activity-title">{{ title || '最近动态' }}</span>
    </template>

    <div v-if="logs.length" class="activity-list">
      <div v-for="log in logs" :key="log.id" class="activity-row">
        <el-tag size="small" :type="actionTagType(log.action)" class="action-tag">{{ actionLabel(log.action) }}</el-tag>
        <span class="activity-text" :title="log.description">
          <span class="op-name">{{ log.operatorName }}</span>
          {{ log.description || log.targetName }}
        </span>
        <span class="activity-time">{{ formatTime(log.createdAt) }}</span>
      </div>
    </div>
    <el-empty v-else :description="emptyHint" :image-size="60" />
  </el-card>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { getOperationLogs } from '@/api/operationLog'
import type { WidgetContentMap } from '@/types'
import type { OperationLog } from '@/types/operationLog'

const props = defineProps<{
  content: WidgetContentMap['activity']
  title?: string
}>()

const loading = ref(false)
const logs = ref<OperationLog[]>([])
const denied = ref(false)

const emptyHint = computed(() => denied.value ? '无权限查看操作日志' : '暂无动态')

const ACTION_LABELS: Record<string, string> = {
  create: '新增', update: '修改', delete: '删除', batchCreate: '批量新增',
  batchDelete: '批量删除', import: '导入', export: '导出', merge: '合并',
}
function actionLabel(action: string): string {
  return ACTION_LABELS[action] || action
}
function actionTagType(action: string): 'success' | 'warning' | 'danger' | 'info' | '' {
  if (action.includes('create') || action === 'import') return 'success'
  if (action.includes('delete')) return 'danger'
  if (action === 'update' || action === 'merge') return 'warning'
  return 'info'
}

function formatTime(value: string): string {
  if (!value) return ''
  const d = new Date(value)
  if (isNaN(d.getTime())) return ''
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

async function fetchLogs(): Promise<void> {
  loading.value = true
  try {
    const resp = await getOperationLogs({ page: 1, pageSize: props.content.limit || 8 })
    logs.value = resp.items || []
  } catch (error: any) {
    if (error?.response?.status === 403) denied.value = true
    else console.error('动态加载失败:', error)
    logs.value = []
  } finally {
    loading.value = false
  }
}

onMounted(fetchLogs)
</script>

<style scoped lang="scss">
.activity-title { font-weight: 600; }

.activity-list { display: flex; flex-direction: column; }

.activity-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  font-size: 13px;
  border-bottom: 1px solid var(--el-border-color-lighter);

  &:last-child { border-bottom: none; }

  .action-tag { flex-shrink: 0; }
  .activity-text { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--el-text-color-regular); }
  .op-name { color: var(--el-text-color-primary); font-weight: 500; margin-right: 4px; }
  .activity-time { flex-shrink: 0; font-size: 12px; color: var(--el-text-color-placeholder); }
}
</style>
