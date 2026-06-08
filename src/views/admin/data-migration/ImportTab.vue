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
    </el-form>

    <el-upload
      v-if="pageRows.length"
      class="bulk-upload"
      drag
      multiple
      :auto-upload="false"
      :show-file-list="false"
      accept=".xlsx,.xls,.json"
      :on-change="onBulkFile"
    >
      <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
      <div class="el-upload__text">将所有文件拖到此处，或<em>点击一次性选择</em></div>
      <template #tip>
        <div class="el-upload__tip">
          按文件名自动分配到对应数据页（文件名需含 collection 或页名，如 products.xlsx / 产品管理.xlsx），可在下方逐行手动调整。
        </div>
      </template>
    </el-upload>

    <el-table :data="pageRows" border v-loading="pagesLoading">
      <el-table-column prop="pageName" label="数据页" />
      <el-table-column prop="collection" label="Collection" width="180" />
      <el-table-column prop="recordCount" label="现有记录" width="100" align="right" />
      <el-table-column label="导入文件" width="280">
        <template #default="{ row }">
          <el-upload
            :auto-upload="false"
            :show-file-list="false"
            accept=".xlsx,.xls,.json"
            :on-change="(file: UploadFile) => onFile(row, file)"
          >
            <el-button size="small" :icon="Upload">选择文件</el-button>
          </el-upload>
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
import { ElMessage, type UploadFile } from 'element-plus'
import { Upload, UploadFilled } from '@element-plus/icons-vue'
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

function onFile(row: PageRow, uploadFile: UploadFile) {
  row._file = uploadFile.raw
}

/**
 * 按文件名把一个文件归位到某数据页：
 * 优先精确匹配 collection / 页名，否则文件名包含其一。命中返回 true。
 */
function autoAssign(file: File): boolean {
  const base = file.name.replace(/\.[^.]+$/, '').toLowerCase()
  const rows = pageRows.value
  let row = rows.find((r) => r.collection.toLowerCase() === base || r.pageName.toLowerCase() === base)
  if (!row) {
    row = rows.find(
      (r) => base.includes(r.collection.toLowerCase()) || base.includes(r.pageName.toLowerCase()),
    )
  }
  if (row) {
    row._file = file
    return true
  }
  return false
}

// el-upload 的 on-change 对每个文件各触发一次，用短定时器把结果汇总成一次提示
let assignFlushTimer: ReturnType<typeof setTimeout> | null = null
let pendingAssigned = 0
const pendingUnmatched: string[] = []

function onBulkFile(uploadFile: UploadFile) {
  const file = uploadFile.raw
  if (!file) return
  if (!pageRows.value.length) {
    ElMessage.warning('请先选择项目')
    return
  }
  if (autoAssign(file)) pendingAssigned++
  else pendingUnmatched.push(file.name)

  if (assignFlushTimer) clearTimeout(assignFlushTimer)
  assignFlushTimer = setTimeout(flushAssignResult, 80)
}

function flushAssignResult() {
  if (pendingAssigned > 0) ElMessage.success(`已自动分配 ${pendingAssigned} 个文件`)
  if (pendingUnmatched.length > 0) {
    ElMessage.warning(`${pendingUnmatched.length} 个文件未匹配到数据页：${pendingUnmatched.join('、')}`)
  }
  pendingAssigned = 0
  pendingUnmatched.length = 0
}

async function start() {
  if (!pageConfigStore.pageConfigs.length) await pageConfigStore.fetchPageConfigs()

  running.value = true
  results.value = []
  try {
    const withFiles = pageRows.value.filter((r) => r._file)
    const pages: Array<{ pageId: string; collection: string; records: Record<string, any>[] }> = []
    const parseErrors: string[] = []
    for (const row of withFiles) {
      try {
        const fields = pageConfigStore.getPageFields(row.pageId)
        const isJson = row._file!.name.toLowerCase().endsWith('.json')
        const records = isJson
          ? await parseJsonImportFile(row._file!, fields)
          : await parseImportFile(row._file!, fields)
        pages.push({ pageId: row.pageId, collection: row.collection, records })
      } catch {
        parseErrors.push(row.pageName)
      }
    }

    if (pages.length > 0) {
      const allConfigs = pages
        .map((p) => pageConfigStore.getPageConfigById(p.pageId))
        .filter((c): c is NonNullable<typeof c> => Boolean(c))
      results.value = await runBatchImport({ store: pageConfigStore, post, pages, allConfigs })
      resultVisible.value = true
    }

    if (parseErrors.length > 0) {
      ElMessage.warning(`以下文件解析失败，已跳过：${parseErrors.join('、')}`)
    } else {
      ElMessage.success('批量导入完成')
    }
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
.bulk-upload { margin-bottom: 16px; }
.actions { margin-top: 16px; }
.file-name { margin-left: 8px; font-size: 12px; color: #909399; }
</style>
