<template>
  <div class="page-config-list">
    <!-- 工具栏 -->
    <div class="toolbar">
      <div class="toolbar-row">
        <el-input
          v-model="searchKeyword"
          placeholder="搜索名称 / 描述 / API 端点"
          clearable
          :prefix-icon="Search"
          size="default"
          class="toolbar-search"
        />
      </div>

      <div class="toolbar-row toolbar-row-filters">
        <el-select
          v-model="projectFilter"
          size="default"
          class="toolbar-filter"
          placeholder="项目"
        >
          <el-option label="全部项目" value="__all__" />
          <el-option label="⚠ 未引用" value="__orphan__" />
          <el-option
            v-for="p in projectMenus"
            :key="p.id"
            :label="p.name"
            :value="p.id"
          />
        </el-select>

        <el-select v-model="sortBy" size="default" class="toolbar-filter" placeholder="排序">
          <el-option label="更新时间↓" value="updatedDesc" />
          <el-option label="名称 A→Z" value="nameAsc" />
          <el-option label="字段数↓" value="fieldsDesc" />
          <el-option label="创建时间↓" value="createdDesc" />
        </el-select>

        <el-select v-model="viewMode" size="default" class="toolbar-filter" placeholder="视图">
          <el-option label="按项目分组" value="grouped" />
          <el-option label="扁平列表" value="flat" />
        </el-select>
      </div>

      <div class="toolbar-row toolbar-row-actions">
        <el-checkbox
          :model-value="allVisibleSelected"
          :indeterminate="someVisibleSelected"
          @change="handleToggleSelectAll"
        >
          全选 ({{ filteredConfigs.length }})
        </el-checkbox>
        <el-button type="primary" size="small" @click="$emit('add')">
          <el-icon><Plus /></el-icon>
          新增
        </el-button>
      </div>
    </div>

    <!-- 列表区 -->
    <div class="list-body">
      <el-empty
        v-if="filteredConfigs.length === 0"
        :description="searchKeyword || projectFilter !== '__all__' ? '无匹配结果' : '暂无页面配置'"
        :image-size="80"
      />

      <!-- 分组视图 -->
      <el-collapse
        v-else-if="viewMode === 'grouped'"
        v-model="expandedGroups"
        class="grouped-list"
      >
        <el-collapse-item
          v-for="group in groupedConfigs"
          :key="group.key"
          :name="group.key"
        >
          <template #title>
            <span class="group-title">
              <el-icon v-if="group.key === '__orphan__'" class="orphan-icon"><Warning /></el-icon>
              {{ group.name }}
              <span class="group-count">({{ group.items.length }})</span>
            </span>
          </template>
          <div class="group-items">
            <PageConfigListItem
              v-for="config in group.items"
              :key="config.id"
              :config="config"
              :active="modelValue === config.id"
              :selected="selectedIds.has(config.id)"
              :is-orphan="orphanIds.has(config.id)"
              @click="handleSelect(config.id)"
              @toggle-select="(v) => handleToggleSelect(config.id, v)"
              @duplicate="handleDuplicate(config)"
              @delete="handleDelete(config)"
            />
          </div>
        </el-collapse-item>
      </el-collapse>

      <!-- 扁平视图 -->
      <div v-else class="flat-list">
        <PageConfigListItem
          v-for="config in filteredConfigs"
          :key="config.id"
          :config="config"
          :active="modelValue === config.id"
          :selected="selectedIds.has(config.id)"
          :is-orphan="orphanIds.has(config.id)"
          @click="handleSelect(config.id)"
          @toggle-select="(v) => handleToggleSelect(config.id, v)"
          @duplicate="handleDuplicate(config)"
          @delete="handleDelete(config)"
        />
      </div>
    </div>

    <!-- 批量操作栏 -->
    <transition name="slide-up">
      <div v-if="selectedIds.size > 0" class="batch-bar">
        <span class="batch-info">已选 {{ selectedIds.size }} 项</span>
        <div class="batch-actions">
          <el-button size="small" @click="handleBatchExport">
            <el-icon><Download /></el-icon>
            导出 JSON
          </el-button>
          <el-button type="danger" size="small" @click="handleBatchDeleteConfirm">
            <el-icon><Delete /></el-icon>
            批量删除
          </el-button>
          <el-button size="small" link @click="clearSelection">取消</el-button>
        </div>
      </div>
    </transition>

    <!-- 删除确认（单项 + 批量复用） -->
    <ConfirmDialog
      v-model="confirmVisible"
      :title="confirmTitle"
      :message="confirmMessage"
      type="danger"
      confirm-text="删除"
      @confirm="handleConfirmDelete"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Search, Warning, Delete, Download } from '@element-plus/icons-vue'
import { usePageConfigStore, useMenuStore } from '@/stores'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import PageConfigListItem from './PageConfigListItem.vue'
import type { PageConfig } from '@/types'

const props = defineProps<{
  modelValue: string | null
  configs: PageConfig[]
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string | null): void
  (e: 'add'): void
}>()

const pageConfigStore = usePageConfigStore()
const menuStore = useMenuStore()

// ==================== State ====================

const searchKeyword = ref('')
const projectFilter = ref<string>('__all__')
const sortBy = ref<'updatedDesc' | 'nameAsc' | 'fieldsDesc' | 'createdDesc'>('updatedDesc')
const viewMode = ref<'grouped' | 'flat'>('grouped')
const selectedIds = ref<Set<string>>(new Set())
const expandedGroups = ref<string[]>([])

const confirmVisible = ref(false)
const confirmTitle = ref('删除确认')
const confirmMessage = ref('')
const pendingDelete = ref<{ mode: 'single'; config: PageConfig } | { mode: 'batch'; ids: string[] } | null>(null)

// ==================== 项目反查映射 ====================

/** pageId → 引用该 page 的 projectId 集合（一个页面可能被多个数据菜单引用） */
const pageIdToProjectIds = computed(() => {
  const map = new Map<string, Set<string>>()
  for (const m of menuStore.menuList) {
    // data类型菜单通过parent_id指向所属项目（project_id字段可能为空）
    if (m.menuType === 'data' && m.pageId && m.parentId) {
      if (!map.has(m.pageId)) map.set(m.pageId, new Set())
      map.get(m.pageId)!.add(m.parentId)
    }
  }
  return map
})

/** 所有项目菜单（menuType='project'） */
const projectMenus = computed(() =>
  menuStore.menuList
    .filter((m) => m.menuType === 'project')
    .sort((a, b) => (a.order || 0) - (b.order || 0))
)

const projectIdToName = computed(() => {
  const map = new Map<string, string>()
  for (const p of projectMenus.value) {
    map.set(p.id, p.name)
  }
  return map
})

/** 孤立配置（未被任何 data 菜单引用） */
const orphanIds = computed(() => {
  const set = new Set<string>()
  for (const c of props.configs) {
    if (!pageIdToProjectIds.value.has(c.id)) set.add(c.id)
  }
  return set
})

// ==================== 过滤 / 排序 ====================

const filteredConfigs = computed<PageConfig[]>(() => {
  const kw = searchKeyword.value.trim().toLowerCase()
  const pf = projectFilter.value

  let list = props.configs.filter((c) => {
    // 搜索过滤
    if (kw) {
      const hay = [c.name, c.description || '', c.apiEndpoint || ''].join(' ').toLowerCase()
      if (!hay.includes(kw)) return false
    }
    // 项目过滤
    if (pf === '__all__') return true
    if (pf === '__orphan__') return orphanIds.value.has(c.id)
    const pids = pageIdToProjectIds.value.get(c.id)
    return !!pids && pids.has(pf)
  })

  // 排序
  list = [...list].sort((a, b) => {
    switch (sortBy.value) {
      case 'nameAsc':
        return a.name.localeCompare(b.name, 'zh')
      case 'fieldsDesc':
        return (b.fields?.length || 0) - (a.fields?.length || 0)
      case 'createdDesc':
        return (b.createdAt || '').localeCompare(a.createdAt || '')
      case 'updatedDesc':
      default:
        return (b.updatedAt || '').localeCompare(a.updatedAt || '')
    }
  })
  return list
})

interface Group {
  key: string
  name: string
  items: PageConfig[]
}

const groupedConfigs = computed<Group[]>(() => {
  const buckets = new Map<string, PageConfig[]>()
  const orphanBucket: PageConfig[] = []

  for (const c of filteredConfigs.value) {
    const pids = pageIdToProjectIds.value.get(c.id)
    if (!pids || pids.size === 0) {
      orphanBucket.push(c)
      continue
    }
    // 一个页面可能属于多个项目，每个都加进去（重复）以便每个项目分组都看得到
    for (const pid of pids) {
      if (!buckets.has(pid)) buckets.set(pid, [])
      buckets.get(pid)!.push(c)
    }
  }

  const groups: Group[] = []
  // 按项目菜单 order 排序
  for (const p of projectMenus.value) {
    const items = buckets.get(p.id)
    if (items && items.length > 0) {
      groups.push({ key: p.id, name: p.name, items })
    }
  }
  // 处理那些有 projectId 但找不到对应项目菜单的孤立桶（数据不一致情况）
  for (const [pid, items] of buckets) {
    if (!projectIdToName.value.has(pid)) {
      groups.push({ key: pid, name: `未知项目（${pid}）`, items })
    }
  }
  if (orphanBucket.length > 0) {
    groups.push({ key: '__orphan__', name: '未关联菜单', items: orphanBucket })
  }
  return groups
})

// 默认展开所有分组
watch(
  groupedConfigs,
  (groups) => {
    if (expandedGroups.value.length === 0 && groups.length > 0) {
      expandedGroups.value = groups.map((g) => g.key)
    } else {
      // 新出现的 group 自动展开
      const known = new Set(expandedGroups.value)
      for (const g of groups) {
        if (!known.has(g.key)) expandedGroups.value.push(g.key)
      }
    }
  },
  { immediate: true }
)

// ==================== 选择 ====================

const visibleIds = computed(() => filteredConfigs.value.map((c) => c.id))

const allVisibleSelected = computed(
  () => visibleIds.value.length > 0 && visibleIds.value.every((id) => selectedIds.value.has(id))
)
const someVisibleSelected = computed(
  () => visibleIds.value.some((id) => selectedIds.value.has(id)) && !allVisibleSelected.value
)

function handleToggleSelectAll(value: boolean | string | number): void {
  const checked = value === true
  const next = new Set(selectedIds.value)
  if (checked) {
    for (const id of visibleIds.value) next.add(id)
  } else {
    for (const id of visibleIds.value) next.delete(id)
  }
  selectedIds.value = next
}

function handleToggleSelect(id: string, checked: boolean): void {
  const next = new Set(selectedIds.value)
  if (checked) next.add(id)
  else next.delete(id)
  selectedIds.value = next
}

function clearSelection(): void {
  selectedIds.value = new Set()
}

// ==================== 单项交互 ====================

function handleSelect(id: string): void {
  emit('update:modelValue', id)
}

async function handleDuplicate(config: PageConfig): Promise<void> {
  try {
    const created = await pageConfigStore.duplicatePageConfig(config.id)
    ElMessage.success(`已复制为「${created.name}」`)
    emit('update:modelValue', created.id)
  } catch (error) {
    ElMessage.error('复制失败')
  }
}

function handleDelete(config: PageConfig): void {
  pendingDelete.value = { mode: 'single', config }
  confirmTitle.value = '删除确认'
  confirmMessage.value = `确定要删除页面配置「${config.name}」吗？删除后相关菜单将失去关联。`
  confirmVisible.value = true
}

function handleBatchDeleteConfirm(): void {
  const ids = Array.from(selectedIds.value)
  if (ids.length === 0) return
  pendingDelete.value = { mode: 'batch', ids }
  confirmTitle.value = '批量删除确认'
  confirmMessage.value = `确定要删除选中的 ${ids.length} 个页面配置吗？删除后相关菜单将失去关联。此操作不可恢复。`
  confirmVisible.value = true
}

async function handleConfirmDelete(): Promise<void> {
  const op = pendingDelete.value
  if (!op) return

  if (op.mode === 'single') {
    try {
      await pageConfigStore.deletePageConfig(op.config.id)
      ElMessage.success('删除成功')
      // 移除选中
      if (selectedIds.value.has(op.config.id)) {
        const next = new Set(selectedIds.value)
        next.delete(op.config.id)
        selectedIds.value = next
      }
      // 清空当前选中
      if (props.modelValue === op.config.id) {
        emit('update:modelValue', null)
      }
    } catch {
      ElMessage.error('删除失败')
    }
  } else {
    let success = 0
    let failed = 0
    for (const id of op.ids) {
      try {
        await pageConfigStore.deletePageConfig(id)
        success++
      } catch {
        failed++
      }
    }
    if (failed === 0) {
      ElMessage.success(`成功删除 ${success} 项`)
    } else {
      ElMessage.warning(`成功 ${success} 项，失败 ${failed} 项`)
    }
    // 清空选中状态
    selectedIds.value = new Set()
    // 如当前选中项已被删除，清空
    if (props.modelValue && op.ids.includes(props.modelValue)) {
      emit('update:modelValue', null)
    }
  }
  pendingDelete.value = null
}

// ==================== 批量导出 ====================

function handleBatchExport(): void {
  const ids = Array.from(selectedIds.value)
  if (ids.length === 0) return
  const idSet = new Set(ids)
  const subset = props.configs.filter((c) => idSet.has(c.id))
  const blob = new Blob([JSON.stringify(subset, null, 2)], {
    type: 'application/json;charset=utf-8',
  })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
  a.href = url
  a.download = `page-configs-${ts}.json`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
  ElMessage.success(`已导出 ${subset.length} 项`)
}

// ==================== 暴露用于测试 ====================

defineExpose({
  filteredConfigs,
  groupedConfigs,
  orphanIds,
  selectedIds,
  searchKeyword,
  projectFilter,
  sortBy,
  viewMode,
  handleToggleSelectAll,
})
</script>

<style scoped lang="scss">
.page-config-list {
  display: flex;
  flex-direction: column;
  height: 100%;
  position: relative;
}

.toolbar {
  flex-shrink: 0;
  padding-bottom: 8px;
  border-bottom: 1px solid #f0f2f5;
  margin-bottom: 8px;

  .toolbar-row {
    margin-bottom: 6px;

    &:last-child {
      margin-bottom: 0;
    }
  }

  .toolbar-row-filters {
    display: flex;
    gap: 6px;

    .toolbar-filter {
      flex: 1;
      min-width: 0;
    }
  }

  .toolbar-row-actions {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 4px 0;
  }

  .toolbar-search {
    width: 100%;
  }
}

.list-body {
  flex: 1;
  overflow: auto;
  padding-right: 4px;
}

.flat-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.grouped-list {
  border: none;

  :deep(.el-collapse-item__header) {
    padding-left: 4px;
    font-size: 13px;
    color: #606266;
    border-bottom: 1px solid #f0f2f5;
    height: 36px;
    line-height: 36px;
  }

  :deep(.el-collapse-item__content) {
    padding-bottom: 8px;
  }

  :deep(.el-collapse-item__wrap) {
    border-bottom: none;
  }

  .group-title {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-weight: 500;

    .orphan-icon {
      color: #e6a23c;
    }
  }

  .group-count {
    color: #909399;
    font-weight: normal;
    font-size: 12px;
  }

  .group-items {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 4px 0 4px 8px;
  }
}

.batch-bar {
  flex-shrink: 0;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 12px;
  margin-top: 8px;
  background-color: #fdf6ec;
  border: 1px solid #f5dab1;
  border-radius: 4px;

  .batch-info {
    font-size: 13px;
    color: #b88230;
    font-weight: 500;
  }

  .batch-actions {
    display: flex;
    gap: 6px;
    align-items: center;
  }
}

.slide-up-enter-active,
.slide-up-leave-active {
  transition: all 0.2s ease;
}

.slide-up-enter-from,
.slide-up-leave-to {
  opacity: 0;
  transform: translateY(8px);
}
</style>
