<template>
  <div class="import-tab">
    <el-form inline>
      <el-form-item label="项目">
        <el-tree-select
          v-model="selectedMenuId"
          :data="menuTree"
          :props="treeProps"
          node-key="id"
          check-strictly
          placeholder="选择项目"
          style="width: 280px"
          @change="loadPages"
        />
      </el-form-item>
      <el-form-item label="分支">
        <el-input v-model="branchId" style="width: 140px" />
      </el-form-item>
    </el-form>

    <el-table :data="pageRows" border v-loading="pagesLoading">
      <el-table-column prop="pageName" label="数据页" />
      <el-table-column prop="collection" label="Collection" width="180" />
      <el-table-column prop="recordCount" label="现有记录" width="100" align="right" />
      <el-table-column label="导入文件" width="260">
        <template #default="{ row }">
          <input type="file" accept=".xlsx,.xls,.json" @change="(e) => onFile(row, e)" />
          <span v-if="row._file" class="file-name">{{ row._file.name }}</span>
        </template>
      </el-table-column>
    </el-table>

    <div class="actions">
      <el-button type="primary" :loading="running" :disabled="!hasFiles" @click="start">
        开始导入
      </el-button>
    </div>

    <el-dialog v-model="resultVisible" title="导入结果" width="640">
      <el-table :data="results" border size="small">
        <el-table-column prop="collection" label="Collection" />
        <el-table-column prop="created" label="新增" width="80" />
        <el-table-column prop="updated" label="更新" width="80" />
        <el-table-column prop="failed" label="失败" width="80" />
        <el-table-column prop="reResolved" label="补齐引用" width="90" />
        <el-table-column prop="pending" label="未匹配" width="80" />
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { post } from '@/utils/request'
import { getAvailableExportMenus, previewMenuExport } from '@/api/menu'
import { usePageConfigStore } from '@/stores'
import { parseImportFile, parseJsonImportFile } from '@/utils/excel'
import { runBatchImport, type BatchImportPageResult } from '@/composables/useBatchImport'
import type { MenuItem } from '@/types'

interface PageRow {
  pageId: string
  collection: string
  pageName: string
  recordCount: number
  _file?: File
}

const pageConfigStore = usePageConfigStore()
const treeProps = { children: 'children', label: 'name' }
const menuTree = ref<MenuItem[]>([])
const selectedMenuId = ref<string>('')
const branchId = ref('main')
const pageRows = ref<PageRow[]>([])
const pagesLoading = ref(false)
const running = ref(false)
const resultVisible = ref(false)
const results = ref<BatchImportPageResult[]>([])

const hasFiles = computed(() => pageRows.value.some((r) => r._file))

async function loadMenus() {
  menuTree.value = await getAvailableExportMenus()
}

async function loadPages() {
  if (!selectedMenuId.value) return
  pagesLoading.value = true
  try {
    const preview = await previewMenuExport([selectedMenuId.value])
    const pages = preview.menus.flatMap((m) => m.pages)
    pageRows.value = pages.map((p) => ({
      pageId: `page-${p.collection}`,
      collection: p.collection,
      pageName: p.pageName,
      recordCount: p.recordCount,
    }))
  } finally {
    pagesLoading.value = false
  }
}

function onFile(row: PageRow, e: Event) {
  const input = e.target as HTMLInputElement
  row._file = input.files?.[0]
}

async function start() {
  if (!pageConfigStore.pageConfigs.length) await pageConfigStore.fetchPageConfigs()

  running.value = true
  try {
    const withFiles = pageRows.value.filter((r) => r._file)
    const pages: Array<{ pageId: string; collection: string; records: Record<string, any>[] }> = []
    for (const row of withFiles) {
      const fields = pageConfigStore.getPageFields(row.pageId)
      const isJson = row._file!.name.toLowerCase().endsWith('.json')
      const records = isJson
        ? await parseJsonImportFile(row._file!, fields)
        : await parseImportFile(row._file!, fields)
      pages.push({ pageId: row.pageId, collection: row.collection, records })
    }

    const allConfigs = withFiles
      .map((r) => pageConfigStore.getPageConfigById(r.pageId))
      .filter((c): c is NonNullable<typeof c> => Boolean(c))

    results.value = await runBatchImport({ store: pageConfigStore, post, pages, allConfigs })
    resultVisible.value = true
    ElMessage.success('批量导入完成')
  } catch (err: unknown) {
    ElMessage.error((err as Error)?.message || '批量导入失败')
  } finally {
    running.value = false
  }
}

loadMenus()
</script>

<style scoped lang="scss">
.import-tab { padding: 8px; }
.actions { margin-top: 16px; }
.file-name { margin-left: 8px; font-size: 12px; color: #909399; }
</style>
