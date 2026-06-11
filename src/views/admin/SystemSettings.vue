/**
 * 系统设置管理页面
 *
 * 管理系统配置和首页区块
 * - Tab 1: 基本设置（系统名称、简称、Logo）
 * - Tab 2: 首页配置（区块管理、拖拽排序）
 */
<template>
  <div class="system-settings">
    <el-tabs v-model="activeTab" type="border-card">
      <!-- 基本设置 Tab -->
      <el-tab-pane label="基本设置" name="basic">
        <el-form
          ref="configFormRef"
          label-width="100px"
          class="settings-form"
          v-loading="loading"
        >
          <el-form-item label="系统名称" required>
            <el-input
              v-model="systemName"
              placeholder="用于首页标题、浏览器标题"
              maxlength="200"
              show-word-limit
            />
          </el-form-item>
          <el-form-item label="系统简称" required>
            <el-input
              v-model="systemShortName"
              placeholder="用于侧边栏Logo"
              maxlength="50"
              show-word-limit
            />
          </el-form-item>
          <el-form-item label="Logo图片">
            <el-input
              v-model="logoUrl"
              placeholder="可选，Logo URL"
              clearable
            />
            <div class="form-tip">
              Logo 图片 URL，如：https://example.com/logo.png
            </div>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="saving" @click="handleSaveConfig">
              保存设置
            </el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- 馅页配置 Tab -->
      <el-tab-pane label="首页配置" name="widgets">
        <div class="widgets-toolbar">
          <el-dropdown @command="handleAddWidget">
            <el-button type="primary">
              <el-icon><Plus /></el-icon>
              新增区块
              <el-icon class="el-icon--right"><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="custom-markdown">
                  Markdown 区块
                </el-dropdown-item>
                <el-dropdown-item command="data-card">
                  数据卡片
                </el-dropdown-item>
                <el-dropdown-item command="quick-form">
                  快速录入表单
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>

        <div v-if="widgetsLoading" class="widgets-loading">
          <el-skeleton :rows="3" animated />
        </div>

        <div v-else-if="widgetsList.length === 0" class="widgets-empty">
          <el-empty description="暂无首页区块配置" />
        </div>

        <draggable
          v-else
          v-model="widgetsList"
          item-key="id"
          handle=".drag-handle"
          animation="200"
          @end="handleDragEnd"
          class="widgets-list"
        >
          <template #item="{ element }">
            <div class="widget-item">
              <el-icon class="drag-handle"><Rank /></el-icon>
              <el-switch
                v-model="element.enabled"
                @change="handleWidgetChange(element)"
              />
              <span class="widget-title">
                {{ element.title || getDefaultTitle(element.widgetType) }}
              </span>
              <el-tag size="small" :type="getTagType(element.widgetType)">
                {{ getWidgetTypeLabel(element.widgetType) }}
              </el-tag>
              <div class="widget-actions">
                <el-button
                  type="primary"
                  link
                  size="small"
                  @click="handleEditWidget(element)"
                >
                  <el-icon><Edit /></el-icon>
                </el-button>
                <el-button
                  v-if="isCustomWidget(element)"
                  type="danger"
                  link
                  size="small"
                  @click="handleDeleteWidget(element)"
                >
                  <el-icon><Delete /></el-icon>
                </el-button>
              </div>
            </div>
          </template>
        </draggable>

        <WidgetEditDialog
          v-model="editDialogVisible"
          :widget="currentWidget"
          @save="handleSaveWidget"
        />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
/**
 * SystemSettings 组件脚本
 *
 * 功能：
 * 1. 系统配置管理（名称、简称、Logo）
 * 2. 首页区块管理（增删改、拖拽排序）
 */
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, ArrowDown, Rank, Edit, Delete } from '@element-plus/icons-vue'
import draggable from 'vuedraggable'
import { useSystemConfigStore } from '@/stores'
import WidgetEditDialog from './components/WidgetEditDialog.vue'
import type { WidgetConfig, WidgetType } from '@/types'

// ==================== Store ====================

const systemConfigStore = useSystemConfigStore()

// ==================== State ====================

const activeTab = ref('basic')
const loading = ref(false)
const saving = ref(false)
const widgetsLoading = ref(false)

// 系统配置表单
const systemName = ref('')
const systemShortName = ref('')
const logoUrl = ref('')

// 区块列表（可拖拽）
const widgetsList = ref<WidgetConfig[]>([])
const editDialogVisible = ref(false)
const currentWidget = ref<WidgetConfig | null>(null)

// ==================== 常量 ====================

const WIDGET_TYPE_LABELS: Record<string, string> = {
  welcome: '欢迎卡片',
  stats: '统计概览',
  'quick-links': '快捷入口',
  'system-info': '系统说明',
  'custom-markdown': 'Markdown',
  'data-card': '数据卡片',
  'quick-form': '快速录入'
}

const WIDGET_DEFAULT_TITLES: Record<string, string> = {
  welcome: '欢迎卡片',
  stats: '统计概览',
  'quick-links': '快捷入口',
  'system-info': '系统说明',
  'custom-markdown': 'Markdown区块',
  'data-card': '数据卡片',
  'quick-form': '快速录入'
}

// ==================== 方法 ====================

function getDefaultTitle(type: WidgetType): string {
  return WIDGET_DEFAULT_TITLES[type] || type
}

function getWidgetTypeLabel(type: WidgetType): string {
  return WIDGET_TYPE_LABELS[type] || type
}

function getTagType(type: WidgetType): 'success' | 'warning' | 'info' | '' {
  if (type.startsWith('custom-')) return 'warning'
  if (type === 'welcome') return 'success'
  if (type === 'stats') return 'info'
  return ''
}

function isCustomWidget(widget: WidgetConfig): boolean {
  return widget.id.startsWith('custom-') ||
    widget.widgetType === 'custom-markdown' ||
    widget.widgetType === 'data-card' ||
    widget.widgetType === 'quick-form'
}

// ==================== 事件处理 ====================

async function handleSaveConfig() {
  const name = (systemName.value || '').trim()
  const shortName = (systemShortName.value || '').trim()

  if (!name) {
    ElMessage.warning('系统名称不能为空')
    return
  }
  if (!shortName) {
    ElMessage.warning('系统简称不能为空')
    return
  }

  saving.value = true
  try {
    await systemConfigStore.updateConfig({
      systemName: name,
      systemShortName: shortName,
      logoUrl: (logoUrl.value || '').trim() || null
    })
    ElMessage.success('保存成功')
  } catch {
    ElMessage.error('保存失败')
  } finally {
    saving.value = false
  }
}

async function handleDragEnd() {
  const orders = widgetsList.value.map((w, idx) => ({
    id: w.id,
    order: idx + 1
  }))
  try {
    await systemConfigStore.reorderWidgets(orders)
  } catch {
    ElMessage.error('排序保存失败')
  }
}

async function handleWidgetChange(widget: WidgetConfig) {
  try {
    await systemConfigStore.updateWidgets([{ id: widget.id, enabled: widget.enabled }])
  } catch {
    ElMessage.error('更新失败')
    // 恢复原状态
    widget.enabled = !widget.enabled
  }
}

function handleEditWidget(widget: WidgetConfig) {
  currentWidget.value = widget
  editDialogVisible.value = true
}

async function handleSaveWidget(data: Partial<WidgetConfig>) {
  try {
    await systemConfigStore.updateWidgets([data])
    // 更新本地列表
    const idx = widgetsList.value.findIndex(w => w.id === data.id)
    if (idx >= 0) {
      widgetsList.value[idx] = { ...widgetsList.value[idx], ...data }
    }
    ElMessage.success('保存成功')
  } catch {
    ElMessage.error('保存失败')
  }
}

async function handleAddWidget(type: 'custom-markdown' | 'data-card' | 'quick-form') {
  try {
    const defaultContent = type === 'custom-markdown'
      ? { markdown: '' }
      : type === 'quick-form'
        ? { targetCollection: '', buttonLabel: '快速录入' }
        : { dataSource: { collection: '' }, displayType: 'count' }

    await systemConfigStore.createWidget({
      widgetType: type,
      title: getDefaultTitle(type),
      content: defaultContent,
      visibleRoles: ['admin', 'developer', 'guest']
    })
    // 从 store 获取最新创建的 widget（store 已 push）
    const newWidget = systemConfigStore.widgets[systemConfigStore.widgets.length - 1]
    if (newWidget) {
      widgetsList.value.push(newWidget)
    }
    ElMessage.success('创建成功')
  } catch {
    ElMessage.error('创建失败')
  }
}

async function handleDeleteWidget(widget: WidgetConfig) {
  try {
    await ElMessageBox.confirm(
      `确定删除区块「${widget.title || getDefaultTitle(widget.widgetType)}」？`,
      '删除确认',
      { type: 'warning' }
    )
    await systemConfigStore.removeWidget(widget.id)
    widgetsList.value = widgetsList.value.filter(w => w.id !== widget.id)
    ElMessage.success('删除成功')
  } catch {
    // 用户取消或删除失败
  }
}

// ==================== 生命周期 ====================

onMounted(async () => {
  loading.value = true
  widgetsLoading.value = true

  try {
    await systemConfigStore.initialize()
    systemName.value = systemConfigStore.systemName || ''
    systemShortName.value = systemConfigStore.systemShortName || ''
    logoUrl.value = systemConfigStore.systemConfig?.logoUrl || ''
    widgetsList.value = [...systemConfigStore.widgets].sort((a, b) => a.order - b.order)
  } catch {
    ElMessage.error('加载配置失败')
  } finally {
    loading.value = false
    widgetsLoading.value = false
  }
})
</script>

<style scoped lang="scss">
.system-settings {
  padding: 0;
}

.settings-form {
  max-width: 500px;
}

.form-tip {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}

.widgets-toolbar {
  margin-bottom: 16px;
}

.widgets-loading,
.widgets-empty {
  padding: 20px;
}

.widgets-list {
  .widget-item {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px;
    border: 1px solid var(--el-border-color);
    border-radius: 4px;
    margin-bottom: 8px;
    background: var(--el-bg-color);
    transition: box-shadow 0.2s;

    &:hover {
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
    }

    .drag-handle {
      cursor: grab;
      color: var(--el-text-color-secondary);

      &:active {
        cursor: grabbing;
      }
    }

    .widget-title {
      flex: 1;
      font-weight: 500;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .widget-actions {
      display: flex;
      gap: 4px;
    }
  }
}
</style>