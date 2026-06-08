<template>
  <div class="clear-tab">
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
          @change="onProjectChange"
        />
      </el-form-item>
      <el-form-item label="分支">
        <el-select v-model="branchId" style="width: 180px" @change="loadPages">
          <el-option v-for="b in branchOptions" :key="b.id" :label="b.name" :value="b.id" />
        </el-select>
      </el-form-item>
    </el-form>

    <el-table :data="pageRows" border v-loading="pagesLoading" @selection-change="onSelect">
      <el-table-column type="selection" width="48" />
      <el-table-column prop="pageName" label="数据页" />
      <el-table-column prop="collection" label="Collection" width="180" />
      <el-table-column prop="recordCount" label="记录数" width="100" align="right" />
    </el-table>

    <div class="actions">
      <span class="summary" v-if="selected.length">
        将清空 {{ selected.length }} 个页、共 {{ totalRecords }} 条记录
      </span>
      <el-button type="danger" :disabled="!selected.length" @click="confirmClear">
        清空所选
      </el-button>
    </div>

    <el-dialog v-model="confirmVisible" title="确认清空" width="480">
      <p>此操作将永久删除所选 {{ selected.length }} 个数据页（分支 {{ branchId }}）的全部记录，且不可恢复。</p>
      <p>请输入 <b>CLEAR</b> 以确认：</p>
      <el-input v-model="confirmText" placeholder="CLEAR" />
      <template #footer>
        <el-button @click="confirmVisible = false">取消</el-button>
        <el-button type="danger" :disabled="confirmText !== 'CLEAR'" :loading="clearing" @click="doClear">
          确认清空
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { getAvailableExportMenus, previewMenuExport, batchClearCollections } from '@/api/menu'
import { useProjectBranches } from '@/composables/useProjectBranches'
import type { MenuItem } from '@/types'

interface PageRow { collection: string; pageName: string; recordCount: number }

const treeProps = { children: 'children', label: 'name' }
const menuTree = ref<MenuItem[]>([])
const selectedMenuId = ref('')
const branchId = ref('main')
const { branchOptions, loadBranches } = useProjectBranches()
const pageRows = ref<PageRow[]>([])
const pagesLoading = ref(false)
const selected = ref<PageRow[]>([])
const confirmVisible = ref(false)
const confirmText = ref('')
const clearing = ref(false)

const totalRecords = computed(() => selected.value.reduce((s, r) => s + r.recordCount, 0))

async function loadMenus() {
  menuTree.value = await getAvailableExportMenus()
}

// 切换项目：重置分支为主分支、加载该项目的分支选项、刷新页面列表
async function onProjectChange() {
  branchId.value = 'main'
  await loadBranches(selectedMenuId.value)
  await loadPages()
}

async function loadPages() {
  if (!selectedMenuId.value) return
  pagesLoading.value = true
  selected.value = []
  try {
    const preview = await previewMenuExport([selectedMenuId.value], branchId.value)
    pageRows.value = preview.menus.flatMap((m) => m.pages)
  } finally {
    pagesLoading.value = false
  }
}

function onSelect(rows: PageRow[]) {
  selected.value = rows
}

function confirmClear() {
  confirmText.value = ''
  confirmVisible.value = true
}

async function doClear() {
  clearing.value = true
  try {
    const collections = selected.value.map((r) => r.collection)
    const res = await batchClearCollections(collections, branchId.value)
    ElMessage.success(`已清空 ${res.totalDeleted} 条记录`)
    confirmVisible.value = false
    await loadPages()
  } catch (err: unknown) {
    ElMessage.error((err as Error)?.message || '清空失败')
  } finally {
    clearing.value = false
  }
}

loadMenus()
</script>

<style scoped lang="scss">
.clear-tab { padding: 8px; }
.actions { margin-top: 16px; display: flex; align-items: center; gap: 12px; }
.summary { color: #e6a23c; }
</style>
