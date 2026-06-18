/**
 * 菜单级导出页面
 *
 * 职责：
 * - 树形展示菜单结构（三级）
 * - 支持勾选多个菜单
 * - 显示每个菜单下的页面数量和记录数
 * - 选择导出脚本（可覆盖菜单绑定）
 * - 执行导出并下载 ZIP
 */
<template>
  <div class="menu-export-page">
    <el-row :gutter="20" class="full-height">
      <!-- 左侧：菜单树选择 -->
      <el-col :span="8">
        <el-card class="tree-card" v-loading="treeLoading">
          <template #header>
            <div class="card-header">
              <span>选择导出菜单</span>
              <el-button type="primary" size="small" @click="handleSelectAll">
                全选
              </el-button>
            </div>
          </template>

          <el-tree
            ref="treeRef"
            :data="menuTree"
            :props="treeProps"
            node-key="id"
            show-checkbox
            default-expand-all
            highlight-current
            @check-change="handleCheckChange"
          >
            <template #default="{ data }">
              <div class="tree-node">
                <el-icon v-if="data.icon" class="node-icon">
                  <component :is="data.icon" />
                </el-icon>
                <span class="node-label">{{ data.name }}</span>
                <el-tag
                  v-if="data.exportScriptName"
                  size="small"
                  type="success"
                  class="script-tag"
                >
                  {{ data.exportScriptName }}
                </el-tag>
              </div>
            </template>
          </el-tree>

          <el-empty
            v-if="!treeLoading && menuTree.length === 0"
            description="暂无可导出的数据菜单"
          />
        </el-card>
      </el-col>

      <!-- 右侧：导出预览和操作 -->
      <el-col :span="16">
        <el-card class="preview-card">
          <template #header>
            <div class="card-header">
              <span>导出预览</span>
              <el-button
                type="primary"
                :disabled="selectedMenuIds.length === 0"
                :loading="exportLoading"
                @click="handleExport"
              >
                <el-icon><Download /></el-icon>
                执行导出
              </el-button>
            </div>
          </template>

          <!-- 分支选择 -->
          <div class="script-select-section">
            <el-alert
              type="info"
              :closable="false"
              show-icon
              title="导出脚本专项专用：每个数据页将使用其绑定的导出脚本，未绑定脚本的页面会被跳过。"
              style="margin-bottom: 12px;"
            />
            <el-form label-width="100px">
              <el-form-item label="分支">
                <el-select v-model="branchId" style="width: 300px">
                  <el-option
                    v-for="b in branchOptions"
                    :key="b.id"
                    :label="b.name"
                    :value="b.id"
                  />
                </el-select>
                <div class="form-tip">导出该分支下的数据（默认主分支）</div>
              </el-form-item>
            </el-form>
          </div>

          <!-- 预览信息 -->
          <div v-if="previewData" class="preview-section">
            <el-divider content-position="left">
              导出统计 (共 {{ previewData.totalRecords.toLocaleString() }} 条记录)
            </el-divider>

            <el-collapse v-model="activeCollapse">
              <el-collapse-item
                v-for="menu in previewData.menus"
                :key="menu.menuId"
                :name="menu.menuId"
              >
                <template #title>
                  <div class="collapse-title">
                    <span class="menu-name">{{ menu.menuName }}</span>
                    <el-tag size="small" type="info" class="record-tag">
                      {{ menu.totalRecords.toLocaleString() }} 条
                    </el-tag>
                    <el-tag
                      v-if="menu.boundScript"
                      size="small"
                      type="success"
                      class="script-tag"
                    >
                      {{ menu.boundScript.name }}
                    </el-tag>
                  </div>
                </template>

                <el-table :data="menu.pages" size="small" border>
                  <el-table-column prop="pageName" label="页面名称" />
                  <el-table-column prop="collection" label="Collection" width="200" />
                  <el-table-column prop="recordCount" label="记录数" width="120" align="right">
                    <template #default="{ row }">
                      {{ row.recordCount.toLocaleString() }}
                    </template>
                  </el-table-column>
                </el-table>
              </el-collapse-item>
            </el-collapse>
          </div>

          <el-empty
            v-else-if="selectedMenuIds.length === 0"
            description="请选择要导出的菜单"
          />

          <el-empty
            v-else-if="previewLoading"
            description="正在加载预览信息..."
          />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
/**
 * ExportTab 组件脚本
 */
import { ref, computed, watch, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Download } from '@element-plus/icons-vue'
import {
  getAvailableExportMenus,
  previewMenuExport,
  executeMenuExport,
} from '@/api/menu'
import { useProjectBranches } from '@/composables/useProjectBranches'
import type { MenuItem, MenuExportPreview } from '@/types'

// ==================== Refs ====================

const treeRef = ref()
const menuTree = ref<MenuItem[]>([])
const branchId = ref('main')
const { branchOptions, loadBranches } = useProjectBranches()
const previewData = ref<MenuExportPreview | null>(null)
const previewLoading = ref(false)
const exportLoading = ref(false)
const activeCollapse = ref<string[]>([])
const treeLoading = ref(false)

// ==================== State ====================

const treeProps = {
  children: 'children',
  label: 'name'
}

// ==================== 计算属性 ====================

const selectedMenuIds = computed(() => {
  return treeRef.value?.getCheckedKeys() || []
})

// ==================== 方法 ====================

/**
 * 处理全选
 */
function handleSelectAll(): void {
  const allIds = getAllMenuIds(menuTree.value)
  treeRef.value?.setCheckedKeys(allIds)
}

/**
 * 获取所有菜单ID
 */
function getAllMenuIds(menus: MenuItem[]): string[] {
  const ids: string[] = []
  menus.forEach(menu => {
    ids.push(menu.id)
    if (menu.children?.length) {
      ids.push(...getAllMenuIds(menu.children))
    }
  })
  return ids
}

/**
 * 处理勾选变化
 */
function handleCheckChange(): void {
  // Preview will be loaded by watcher
}

/**
 * 加载预览数据
 */
async function loadPreview(): Promise<void> {
  if (selectedMenuIds.value.length === 0) {
    previewData.value = null
    return
  }

  previewLoading.value = true
  try {
    const data = await previewMenuExport(selectedMenuIds.value as string[], branchId.value)
    previewData.value = data

    // Auto expand all collapse items
    activeCollapse.value = data.menus.map(m => m.menuId)
  } catch (error: any) {
    ElMessage.error(error.message || '加载预览失败')
  } finally {
    previewLoading.value = false
  }
}

/**
 * 执行导出
 */
async function handleExport(): Promise<void> {
  if (selectedMenuIds.value.length === 0) {
    ElMessage.warning('请选择要导出的菜单')
    return
  }

  exportLoading.value = true
  try {
    const { blob, notice } = await executeMenuExport(
      selectedMenuIds.value as string[],
      branchId.value
    )

    // Download the file
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `菜单导出_${new Date().toISOString().slice(0, 19).replace(/[:-]/g, '')}.zip`
    document.body.appendChild(a)
    a.click()
    window.URL.revokeObjectURL(url)
    document.body.removeChild(a)

    ElMessage.success('导出成功')
    // 有跳过/部分失败时提示（绑定驱动：无绑定脚本的页面被跳过）
    if (notice) {
      ElMessage.warning(notice)
    }
  } catch (error: any) {
    ElMessage.error(error.message || '导出失败')
  } finally {
    exportLoading.value = false
  }
}

// ==================== 侦听器 ====================

// Debounced preview loading
let previewTimer: ReturnType<typeof setTimeout> | null = null
watch(selectedMenuIds, () => {
  if (previewTimer) {
    clearTimeout(previewTimer)
  }
  previewTimer = setTimeout(() => {
    loadPreview()
  }, 300)
})

// 切换分支时重新加载预览（按所选分支统计记录数）
watch(branchId, () => loadPreview())

// ==================== 生命周期 ====================

onMounted(async () => {
  await Promise.all([loadMenuTree(), loadBranches()])
})

/**
 * 加载可导出的菜单树
 */
async function loadMenuTree(): Promise<void> {
  treeLoading.value = true
  try {
    menuTree.value = await getAvailableExportMenus()
  } catch (error: any) {
    ElMessage.error(error.message || '加载菜单失败')
  } finally {
    treeLoading.value = false
  }
}
</script>

<style scoped lang="scss">
.menu-export-page {
  height: 100%;
  padding: 0;
  display: flex;
  flex-direction: column;
}

.full-height {
  height: 100%;
  flex: 1;

  // 确保 el-col 继承高度
  > .el-col {
    height: 100%;
  }
}

// 使用 flex 布局让 card body 可滚动
.tree-card,
.preview-card {
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
}

.tree-node {
  display: flex;
  align-items: center;
  width: 100%;

  .node-icon {
    margin-right: 8px;
    color: #409eff;
  }

  .node-label {
    flex: 1;
  }

  .script-tag {
    margin-left: 8px;
  }
}

.script-select-section {
  margin-bottom: 16px;
}

.form-tip {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}

.preview-section {
  margin-top: 16px;
}

.collapse-title {
  display: flex;
  align-items: center;
  width: 100%;

  .menu-name {
    font-weight: 500;
  }

  .record-tag {
    margin-left: 12px;
  }

  .script-tag {
    margin-left: 8px;
  }
}

:deep(.el-collapse-item__header) {
  height: 48px;
  line-height: 48px;
}
</style>
