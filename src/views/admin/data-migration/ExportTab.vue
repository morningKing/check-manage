/**
 * 数据导出页面（脚本驱动）
 *
 * 职责：
 * - 列出所有【已绑定】的导出脚本
 * - 直接选脚本导出——脚本已绑定到数据页/菜单，无需再选导出范围
 * - 数据页维度脚本 → 单文件下载；菜单维度脚本 → ZIP 下载
 */
<template>
  <div class="export-by-script-page">
    <el-card class="scripts-card" v-loading="loading">
      <template #header>
        <div class="card-header">
          <span>选择导出脚本</span>
          <div class="header-right">
            <span class="branch-label">分支</span>
            <el-select v-model="branchId" size="small" style="width: 200px">
              <el-option
                v-for="b in branchOptions"
                :key="b.id"
                :label="b.name"
                :value="b.id"
              />
            </el-select>
          </div>
        </div>
      </template>

      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="导出脚本专项专用：每个脚本已绑定到一个数据页或菜单，点「导出」即按其绑定的数据导出，无需再选范围。"
        style="margin-bottom: 16px;"
      />

      <el-table
        v-if="boundScripts.length"
        :data="boundScripts"
        border
        stripe
      >
        <el-table-column prop="name" label="脚本名称" min-width="160" />
        <el-table-column label="类型" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.boundMenuId ? 'warning' : 'success'">
              {{ row.boundMenuId ? '菜单' : '数据页' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="绑定目标" min-width="160">
          <template #default="{ row }">{{ targetLabel(row) }}</template>
        </el-table-column>
        <el-table-column prop="outputFormat" label="输出格式" width="100" />
        <el-table-column prop="description" label="描述" min-width="160" show-overflow-tooltip />
        <el-table-column label="操作" width="120" align="center">
          <template #default="{ row }">
            <el-button
              type="primary"
              size="small"
              :loading="exportingId === row.id"
              @click="handleExport(row)"
            >
              <el-icon><Download /></el-icon>
              导出
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-empty
        v-else-if="!loading"
        description="暂无已绑定的导出脚本，请先在「导出脚本」中创建并绑定到数据页/菜单"
      />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Download } from '@element-plus/icons-vue'
import { getExportScripts, executeExportScript } from '@/api/exportScript'
import { executeMenuExport } from '@/api/menu'
import { useProjectBranches } from '@/composables/useProjectBranches'
import { usePageConfigStore, useMenuStore } from '@/stores'
import type { ExportScript } from '@/types'

const loading = ref(false)
const scripts = ref<ExportScript[]>([])
const branchId = ref('main')
const exportingId = ref<string>('')
const { branchOptions, loadBranches } = useProjectBranches()
const pageConfigStore = usePageConfigStore()
const menuStore = useMenuStore()

const boundScripts = computed(() =>
  scripts.value.filter(s => s.boundCollection || s.boundMenuId))

function targetLabel(s: ExportScript): string {
  if (s.boundMenuId) {
    const m = menuStore.menuList.find(x => x.id === s.boundMenuId)
    return m?.name || s.boundMenuId
  }
  const coll = s.boundCollection || ''
  const cfg = pageConfigStore.pageConfigs.find(c => c.id === `page-${coll}`)
  return cfg?.name || coll
}

async function handleExport(s: ExportScript): Promise<void> {
  exportingId.value = s.id
  try {
    if (s.boundMenuId) {
      // 菜单维度脚本 → 对其绑定菜单整体导出，下载 ZIP
      const { blob, notice } = await executeMenuExport([s.boundMenuId], branchId.value, s.id)
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${s.name}_${new Date().toISOString().slice(0, 19).replace(/[:-]/g, '')}.zip`
      document.body.appendChild(a)
      a.click()
      window.URL.revokeObjectURL(url)
      document.body.removeChild(a)
      ElMessage.success('导出成功')
      if (notice) ElMessage.warning(notice)
    } else {
      // 数据页维度脚本 → 按其绑定 collection 导出，下载单文件（内部完成下载）
      await executeExportScript(s.id, s.boundCollection as string, undefined, branchId.value)
      ElMessage.success('导出成功')
    }
  } catch (error: any) {
    ElMessage.error(error?.message || '导出失败')
  } finally {
    exportingId.value = ''
  }
}

onMounted(async () => {
  loading.value = true
  try {
    const tasks: Promise<unknown>[] = [loadBranches()]
    if (pageConfigStore.pageConfigs.length === 0) tasks.push(pageConfigStore.fetchPageConfigs())
    if (menuStore.menuList.length === 0) tasks.push(menuStore.fetchMenus())
    await Promise.all(tasks)
    scripts.value = await getExportScripts()
  } catch (error: any) {
    ElMessage.error(error?.message || '加载导出脚本失败')
  } finally {
    loading.value = false
  }
})
</script>

<style scoped lang="scss">
.export-by-script-page {
  height: 100%;
}

.scripts-card {
  height: 100%;
  display: flex;
  flex-direction: column;

  :deep(.el-card__body) {
    flex: 1;
    min-height: 0;
    overflow: auto;
  }
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;

  .header-right {
    display: flex;
    align-items: center;
    gap: 8px;

    .branch-label {
      font-size: 13px;
      color: var(--el-text-color-secondary);
    }
  }
}
</style>
