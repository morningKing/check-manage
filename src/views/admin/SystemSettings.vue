/**
 * 系统设置管理页面
 *
 * 管理系统配置和首页区块
 * - Tab 1: 基本设置（系统名称、简称、Logo）
 * - Tab 2: 首页配置（区块管理、自由拖拽/缩放网格布局）
 */
<template>
  <div class="system-settings">
    <el-tabs v-model="activeTab" type="border-card" :before-leave="handleTabBeforeLeave">
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

          <el-divider content-position="left">登录页文案</el-divider>
          <el-form-item label="登录页标题">
            <el-input
              v-model="loginTitle"
              placeholder="留空则使用系统名称"
              maxlength="200"
              show-word-limit
              clearable
            />
          </el-form-item>
          <el-form-item label="登录页副标题">
            <el-input
              v-model="loginSubtitle"
              type="textarea"
              :rows="2"
              placeholder="可选，欢迎语 / 提示，显示在标题下方"
              maxlength="300"
              show-word-limit
            />
          </el-form-item>
          <el-form-item label="登录页页脚">
            <el-input
              v-model="loginFooter"
              type="textarea"
              :rows="2"
              placeholder="可选，版权 / 备案等，显示在登录框底部"
              maxlength="500"
              show-word-limit
            />
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
                <el-dropdown-item command="chart" divided>
                  图表区块
                </el-dropdown-item>
                <el-dropdown-item command="todo">
                  我的待办
                </el-dropdown-item>
                <el-dropdown-item command="activity">
                  最近动态
                </el-dropdown-item>
                <el-dropdown-item command="announcement">
                  公告
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

        <template v-else>
          <div class="layout-toolbar">
            <el-button
              type="success"
              :disabled="!layoutDirty"
              :loading="savingLayout"
              @click="handleSaveLayout"
            >
              <el-icon><Check /></el-icon>
              保存布局
            </el-button>
            <span v-if="layoutDirty" class="layout-dirty-hint">有未保存的修改</span>
          </div>

          <HomeLayoutEditor
            :widgets="widgetsList"
            @edit="handleEditWidget"
            @delete="handleDeleteWidget"
            @toggle="handleWidgetChange"
            @layout-change="handleLayoutChange"
          />
        </template>

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
import { Plus, ArrowDown, Check } from '@element-plus/icons-vue'
import { useSystemConfigStore } from '@/stores'
import WidgetEditDialog from './components/WidgetEditDialog.vue'
import HomeLayoutEditor from '@/components/admin/HomeLayoutEditor.vue'
import type { WidgetConfig, WidgetType, WidgetLayoutUpdateItem } from '@/types'

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
// 登录页文案
const loginTitle = ref('')
const loginSubtitle = ref('')
const loginFooter = ref('')

// 区块列表（网格编辑）
const widgetsList = ref<WidgetConfig[]>([])
const editDialogVisible = ref(false)
const currentWidget = ref<WidgetConfig | null>(null)
const pendingLayout = ref<WidgetLayoutUpdateItem[]>([])
const layoutDirty = ref(false)
const savingLayout = ref(false)

// ==================== 常量 ====================

const WIDGET_DEFAULT_TITLES: Record<string, string> = {
  welcome: '欢迎卡片',
  stats: '统计概览',
  'quick-links': '快捷入口',
  'system-info': '系统说明',
  'custom-markdown': 'Markdown区块',
  'data-card': '数据卡片',
  'quick-form': '快速录入',
  chart: '图表',
  todo: '我的待办',
  activity: '最近动态',
  announcement: '公告'
}

// ==================== 方法 ====================

function getDefaultTitle(type: WidgetType): string {
  return WIDGET_DEFAULT_TITLES[type] || type
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
      logoUrl: (logoUrl.value || '').trim() || null,
      loginTitle: (loginTitle.value || '').trim() || null,
      loginSubtitle: (loginSubtitle.value || '').trim() || null,
      loginFooter: (loginFooter.value || '').trim() || null
    })
    ElMessage.success('保存成功')
  } catch {
    ElMessage.error('保存失败')
  } finally {
    saving.value = false
  }
}

function handleLayoutChange(items: WidgetLayoutUpdateItem[]) {
  pendingLayout.value = items
  layoutDirty.value = true
}

async function handleSaveLayout() {
  if (!layoutDirty.value) return
  savingLayout.value = true
  try {
    await systemConfigStore.updateLayout(pendingLayout.value)
    widgetsList.value = [...systemConfigStore.widgets]
    layoutDirty.value = false
    ElMessage.success('布局已保存')
  } catch {
    ElMessage.error('布局保存失败')
  } finally {
    savingLayout.value = false
  }
}

async function handleTabBeforeLeave(_activeName: string, oldActiveName: string): Promise<boolean> {
  if (oldActiveName === 'widgets' && layoutDirty.value) {
    try {
      await ElMessageBox.confirm('布局有未保存的修改，确定要离开吗？', '提示', { type: 'warning' })
      layoutDirty.value = false
      return true
    } catch {
      return false
    }
  }
  return true
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

const WIDGET_DEFAULT_CONTENT: Record<string, Record<string, any>> = {
  'custom-markdown': { markdown: '' },
  'quick-form': { targetCollection: '', buttonLabel: '快速录入' },
  'data-card': { dataSource: { collection: '' }, displayType: 'count' },
  chart: { collection: '', chartType: 'bar', groupField: '', limit: 20 },
  todo: { limit: 5 },
  activity: { limit: 8 },
  announcement: { title: '公告', body: '', level: 'info', closable: false },
}

type CreatableWidgetType = 'custom-markdown' | 'data-card' | 'quick-form' | 'chart' | 'todo' | 'activity' | 'announcement'

async function handleAddWidget(type: CreatableWidgetType) {
  try {
    const defaultContent = WIDGET_DEFAULT_CONTENT[type] || {}

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
    loginTitle.value = systemConfigStore.systemConfig?.loginTitle || ''
    loginSubtitle.value = systemConfigStore.systemConfig?.loginSubtitle || ''
    loginFooter.value = systemConfigStore.systemConfig?.loginFooter || ''
    // 配置页需要全部区块（含未启用），否则取消勾选后刷新该区块会从列表消失、无法再启用
    await systemConfigStore.fetchWidgets(true)
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

.layout-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.layout-dirty-hint {
  font-size: 12px;
  color: var(--el-color-warning);
}
</style>