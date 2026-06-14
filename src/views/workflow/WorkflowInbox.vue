<template>
  <div class="workflow-inbox">
    <div class="inbox-header">
      <h2 class="inbox-title">我的待办</h2>
      <el-button :loading="loading" text @click="refresh">
        <el-icon><Refresh /></el-icon>
        <span style="margin-left:4px">刷新</span>
      </el-button>
    </div>

    <el-table
      v-loading="loading"
      :data="inbox"
      class="inbox-table"
      empty-text="暂无待办事项"
      border
    >
      <el-table-column prop="workflowName" label="工作流" min-width="160" />
      <el-table-column prop="stageName" label="阶段" min-width="140" />
      <el-table-column label="进入时间" min-width="180">
        <template #default="{ row }">
          {{ formatTime(row.enteredAt) }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="120" fixed="right">
        <template #default="{ row }">
          <el-button
            type="primary"
            link
            size="small"
            :disabled="!row.collection || !row.recordId"
            @click="handleGo(row)"
          >
            去处理
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { useWorkflowStore } from '@/stores/workflow'
import { useMenuStore } from '@/stores/menu'
import { useJumpNavigationStore } from '@/stores/jumpNavigation'
import type { WorkflowInboxItem } from '@/types/workflow'

const router = useRouter()
const workflowStore = useWorkflowStore()
const menuStore = useMenuStore()
const jumpStore = useJumpNavigationStore()
const { inbox, loading } = storeToRefs(workflowStore)

onMounted(() => {
  refresh()
})

async function refresh(): Promise<void> {
  try {
    await workflowStore.loadInbox()
  } catch {
    ElMessage.error('加载待办失败')
  }
}

function formatTime(value: string | null): string {
  if (!value) return '-'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleString()
}

/**
 * 去处理：复用既有的记录深链机制 —
 * 设置跳转意图（jumpStore）并导航到目标数据页，
 * DynamicPage 消费意图后会定位并高亮目标记录。
 */
function handleGo(row: WorkflowInboxItem): void {
  if (!row.collection || !row.recordId) {
    ElMessage.warning('该待办未关联具体记录')
    return
  }
  const targetPageId = `page-${row.collection}`
  const targetMenu = menuStore.menuList.find((m) => m.pageId === targetPageId)
  if (!targetMenu?.path) {
    ElMessage.warning('未找到目标数据所在页面')
    return
  }

  jumpStore.setJump({
    targetCollection: row.collection,
    targetRecordId: row.recordId,
    jumpType: 'reference',
    sourcePageId: targetPageId,
  })

  router.push({ path: targetMenu.path })
}
</script>

<style scoped lang="scss">
.workflow-inbox {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 16px;
  box-sizing: border-box;
}

.inbox-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.inbox-title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}

.inbox-table {
  flex: 1;
  min-height: 0;
}
</style>
