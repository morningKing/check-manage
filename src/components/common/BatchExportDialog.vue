<template>
  <el-dialog
    v-model="visible"
    title="批量导出"
    width="600px"
    :close-on-click-modal="false"
    destroy-on-close
    @open="loadData"
  >
    <div v-loading="loading">
      <el-alert
        v-if="exportablePages.length === 0 && !loading"
        title="暂无可批量导出的页面"
        description="只有绑定了页面级导出脚本的数据页才能参与批量导出。"
        type="info"
        show-icon
        :closable="false"
      />
      <div v-else class="batch-export-list">
        <div
          v-for="page in exportablePages"
          :key="page.id"
          class="page-group"
        >
          <div class="page-name">{{ page.name }}</div>
          <el-checkbox-group v-model="selectedTasks[page.id]">
            <el-checkbox
              v-for="script in getPageScripts(page)"
              :key="script.id"
              :value="script.id"
            >
              {{ script.name }}
              <el-tag size="small" type="info" class="format-tag">{{ script.outputFormat }}</el-tag>
            </el-checkbox>
          </el-checkbox-group>
        </div>
      </div>
    </div>
    <template #footer>
      <el-button @click="visible = false" :disabled="exporting">取消</el-button>
      <el-button
        type="primary"
        :loading="exporting"
        :disabled="totalSelected === 0"
        @click="handleExport"
      >
        导出{{ totalSelected > 0 ? ` (${totalSelected} 个脚本)` : '' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { usePageConfigStore } from '@/stores'
import { getExportScripts, executeBatchExport } from '@/api/exportScript'
import type { PageConfig } from '@/types'
import type { ExportScript } from '@/types'
import type { BatchExportTask } from '@/api/exportScript'

const props = defineProps<{
  modelValue: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const pageConfigStore = usePageConfigStore()
const allScripts = ref<ExportScript[]>([])
const loading = ref(false)
const exporting = ref(false)
const selectedTasks = ref<Record<string, string[]>>({})

const exportablePages = computed(() => {
  return pageConfigStore.pageConfigs.filter(
    (p) => p.exportScripts && p.exportScripts.length > 0
  )
})

const totalSelected = computed(() => {
  return Object.values(selectedTasks.value).reduce(
    (sum, ids) => sum + ids.length, 0
  )
})

function getPageScripts(page: PageConfig): ExportScript[] {
  const ids = page.exportScripts || []
  return allScripts.value.filter((s) => ids.includes(s.id))
}

async function loadData() {
  loading.value = true
  try {
    allScripts.value = await getExportScripts()
    const tasks: Record<string, string[]> = {}
    for (const page of exportablePages.value) {
      tasks[page.id] = [...(page.exportScripts || [])]
    }
    selectedTasks.value = tasks
  } catch {
    ElMessage.error('加载导出脚本失败')
  } finally {
    loading.value = false
  }
}

async function handleExport() {
  const tasks: BatchExportTask[] = []
  for (const page of exportablePages.value) {
    const scriptIds = selectedTasks.value[page.id] || []
    const collection = page.id.replace('page-', '')
    for (const scriptId of scriptIds) {
      tasks.push({ scriptId, collection })
    }
  }
  if (tasks.length === 0) return

  exporting.value = true
  try {
    await executeBatchExport(tasks)
    ElMessage.success('批量导出成功')
    visible.value = false
  } catch (e: any) {
    ElMessage.error(e.message || '批量导出失败')
  } finally {
    exporting.value = false
  }
}
</script>

<style scoped lang="scss">
.batch-export-list {
  max-height: 400px;
  overflow-y: auto;
}

.page-group {
  padding: 12px 0;

  & + .page-group {
    border-top: 1px solid #ebeef5;
  }
}

.page-name {
  font-weight: 600;
  font-size: 14px;
  color: #303133;
  margin-bottom: 8px;
}

.el-checkbox-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-left: 4px;
}

.format-tag {
  margin-left: 6px;
}
</style>
