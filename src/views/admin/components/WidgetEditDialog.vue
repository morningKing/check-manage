<template>
  <el-dialog
    v-model="visible"
    :title="dialogTitle"
    :width="widget?.widgetType === 'custom-markdown' || widget?.widgetType === 'system-info' ? '900px' : '600px'"
    :close-on-click-modal="false"
    @close="handleClose"
  >
    <!-- welcome 类型 -->
    <el-form v-if="widget?.widgetType === 'welcome'" label-width="80px">
      <el-form-item label="标题">
        <el-input v-model="form.content.heading" placeholder="欢迎标题" />
      </el-form-item>
      <el-form-item label="描述">
        <el-input
          v-model="form.content.description"
          type="textarea"
          :rows="3"
          placeholder="欢迎描述"
        />
      </el-form-item>
    </el-form>

    <!-- stats 类型 -->
    <el-form v-if="widget?.widgetType === 'stats'" label-width="80px">
      <el-form-item label="统计项">
        <div
          v-for="(item, idx) in form.content.items"
          :key="idx"
          class="stats-item-row"
        >
          <el-select v-model="item.type" style="width: 120px">
            <el-option label="菜单数量" value="menuCount" />
            <el-option label="页面配置" value="pageCount" />
            <el-option label="字段总数" value="fieldCount" />
            <el-option label="记录数量" value="recordCount" />
          </el-select>
          <el-input v-model="item.label" placeholder="标签" style="width: 100px" />
          <el-select v-model="item.icon" style="width: 100px">
            <el-option label="Document" value="Document" />
            <el-option label="Files" value="Files" />
            <el-option label="Setting" value="Setting" />
            <el-option label="DataLine" value="DataLine" />
            <el-option label="Calendar" value="Calendar" />
          </el-select>
          <el-button
            type="danger"
            link
            @click="form.content.items.splice(idx, 1)"
          >
            删除
          </el-button>
        </div>
        <el-button type="primary" link @click="addStatsItem">
          + 添加统计项
        </el-button>
      </el-form-item>
    </el-form>

    <!-- quick-links 类型 -->
    <el-form v-if="widget?.widgetType === 'quick-links'" label-width="80px">
      <el-form-item label="链接列表">
        <div
          v-for="(link, idx) in form.content.links"
          :key="idx"
          class="link-item-row"
        >
          <el-input v-model="link.name" placeholder="名称" style="width: 100px" />
          <el-input v-model="link.path" placeholder="路径" style="width: 150px" />
          <el-select v-model="link.icon" style="width: 100px">
            <el-option label="Menu" value="Menu" />
            <el-option label="Files" value="Files" />
            <el-option label="Download" value="Download" />
            <el-option label="House" value="House" />
            <el-option label="Document" value="Document" />
          </el-select>
          <el-button
            type="danger"
            link
            @click="form.content.links.splice(idx, 1)"
          >
            删除
          </el-button>
        </div>
        <el-button type="primary" link @click="addLinkItem">
          + 添加链接
        </el-button>
      </el-form-item>
    </el-form>

    <!-- system-info / custom-markdown 类型 -->
    <el-form
      v-if="widget?.widgetType === 'system-info' || widget?.widgetType === 'custom-markdown'"
      label-width="80px"
    >
      <el-form-item label="内容">
        <MdEditor
          v-model="form.content.markdown"
          language="zh-CN"
          :preview="true"
          :style="{ height: '520px' }"
          placeholder="请输入 Markdown 内容..."
        />
      </el-form-item>
    </el-form>

    <!-- data-card 类型 -->
    <el-form v-if="widget?.widgetType === 'data-card'" label-width="80px">
      <el-form-item label="数据集合">
        <el-select
          v-model="form.content.dataSource.collection"
          placeholder="选择数据集合"
          filterable
          clearable
          style="width: 100%"
        >
          <el-option
            v-for="opt in collectionOptions"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="分支ID">
        <el-select
          v-model="form.content.dataSource.branchId"
          placeholder="选择分支"
          clearable
          style="width: 100%"
        >
          <el-option
            v-for="opt in branchOptions"
            :key="opt.id"
            :label="opt.projectName ? `${opt.projectName} - ${opt.name}` : opt.name"
            :value="opt.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="数据过滤">
        <el-input
          v-model="filterStr"
          type="textarea"
          :rows="2"
          placeholder="可选，JSON 格式过滤条件，如：status:active"
        />
      </el-form-item>
      <el-form-item label="显示数量">
        <el-input-number
          v-model="form.content.dataSource.limit"
          :min="1"
          :max="100"
          placeholder="可选"
        />
      </el-form-item>
      <el-form-item label="显示类型">
        <el-radio-group v-model="form.content.displayType">
          <el-radio value="count">统计数</el-radio>
          <el-radio value="list">列表</el-radio>
          <el-radio value="table">表格</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="显示字段">
        <el-select
          v-model="selectedColumns"
          multiple
          filterable
          placeholder="选择要显示的字段"
          style="width: 100%"
        >
          <el-option
            v-for="opt in fieldOptions"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="标题字段">
        <el-select
          v-model="form.content.titleField"
          filterable
          clearable
          placeholder="选择标题字段"
          style="width: 100%"
        >
          <el-option
            v-for="opt in fieldOptions"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="点击跳转">
        <el-switch v-model="form.content.linkToDetail" />
      </el-form-item>
    </el-form>

    <!-- quick-form 类型 -->
    <el-form v-if="widget?.widgetType === 'quick-form'" label-width="90px">
      <el-form-item label="按钮文字" required>
        <el-input v-model="form.content.buttonLabel" placeholder="如：新增巡检记录" />
      </el-form-item>
      <el-form-item label="说明文字">
        <el-input v-model="form.content.description" placeholder="可选，显示在按钮下方" />
      </el-form-item>
      <el-form-item label="图标">
        <IconPicker v-model="form.content.icon" />
      </el-form-item>
      <el-form-item label="关联数据页" required>
        <el-select
          v-model="form.content.targetCollection"
          placeholder="选择目标数据页"
          filterable
          style="width: 100%"
          @change="onQuickFormCollectionChange"
        >
          <el-option
            v-for="opt in collectionOptions"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
        <div class="form-hint">填写后，点击区块将弹出该数据页的录入表单</div>
      </el-form-item>
      <el-form-item label="录入字段">
        <el-select
          v-model="form.content.fields"
          multiple
          filterable
          placeholder="留空 = 录入全部字段（自动字段除外）"
          style="width: 100%"
          :disabled="!form.content.targetCollection"
        >
          <el-option
            v-for="opt in quickFormFieldOptions"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
        <div class="form-hint">指定要录入的字段及顺序（按选择先后展示）；留空则录入该数据页的全部字段</div>
      </el-form-item>
      <el-form-item label="显示字段">
        <el-select
          v-model="form.content.displayField"
          filterable
          clearable
          placeholder="留空 = 自动取第一个文本字段"
          style="width: 100%"
          :disabled="!form.content.targetCollection"
        >
          <el-option
            v-for="opt in quickFormDisplayFieldOptions"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
        <div class="form-hint">区块内「最近 5 条」列表每行展示的字段内容</div>
      </el-form-item>
    </el-form>

    <!-- chart 类型 -->
    <el-form v-if="widget?.widgetType === 'chart'" label-width="90px">
      <el-form-item label="数据页" required>
        <el-select
          v-model="form.content.collection"
          placeholder="选择数据页"
          filterable
          style="width: 100%"
          @change="form.content.groupField = ''"
        >
          <el-option v-for="opt in collectionOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
        </el-select>
      </el-form-item>
      <el-form-item label="分组字段" required>
        <el-select
          v-model="form.content.groupField"
          placeholder="按该字段取值分组计数"
          filterable
          style="width: 100%"
          :disabled="!form.content.collection"
        >
          <el-option v-for="opt in chartFieldOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
        </el-select>
        <div class="form-hint">按该字段的不同取值分组，统计每组的记录数量</div>
      </el-form-item>
      <el-form-item label="图表类型">
        <el-radio-group v-model="form.content.chartType">
          <el-radio value="bar">柱状图</el-radio>
          <el-radio value="pie">饼图</el-radio>
          <el-radio value="line">折线图</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="分组上限">
        <el-input-number v-model="form.content.limit" :min="1" :max="50" />
        <div class="form-hint">最多取前 N 个分组（按数量降序）</div>
      </el-form-item>
    </el-form>

    <!-- todo 类型 -->
    <el-form v-if="widget?.widgetType === 'todo'" label-width="90px">
      <el-form-item label="显示条数">
        <el-input-number v-model="form.content.limit" :min="1" :max="20" />
        <div class="form-hint">展示当前用户工作流待办的前 N 条</div>
      </el-form-item>
    </el-form>

    <!-- activity 类型 -->
    <el-form v-if="widget?.widgetType === 'activity'" label-width="90px">
      <el-form-item label="显示条数">
        <el-input-number v-model="form.content.limit" :min="1" :max="30" />
        <div class="form-hint">展示最近 N 条操作日志（需「操作日志」权限，无权限的角色看到空状态）</div>
      </el-form-item>
    </el-form>

    <!-- announcement 类型 -->
    <el-form v-if="widget?.widgetType === 'announcement'" label-width="90px">
      <el-form-item label="标题">
        <el-input v-model="form.content.title" placeholder="公告标题" />
      </el-form-item>
      <el-form-item label="级别">
        <el-radio-group v-model="form.content.level">
          <el-radio value="info">信息</el-radio>
          <el-radio value="success">成功</el-radio>
          <el-radio value="warning">警告</el-radio>
          <el-radio value="danger">危险</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="正文">
        <MdEditor
          v-model="form.content.body"
          language="zh-CN"
          :preview="false"
          :style="{ height: '260px' }"
          placeholder="支持 Markdown..."
        />
      </el-form-item>
      <el-form-item label="可关闭">
        <el-switch v-model="form.content.closable" />
        <div class="form-hint">开启后用户可关闭该公告（关闭状态记在本地，公告内容更新后重新出现）</div>
      </el-form-item>
    </el-form>

    <!-- 通用配置 -->
    <el-divider content-position="left">显示配置</el-divider>
    <el-form label-width="80px">
      <el-form-item label="区块标题">
        <el-input v-model="form.title" placeholder="可选" />
      </el-form-item>
      <el-form-item label="可见角色">
        <el-checkbox-group v-model="form.visibleRoles">
          <el-checkbox value="admin">管理员</el-checkbox>
          <el-checkbox value="developer">开发者</el-checkbox>
          <el-checkbox value="guest">访客</el-checkbox>
        </el-checkbox-group>
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" @click="handleSave">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
/**
 * WidgetEditDialog 组件
 *
 * 编辑首页区块配置，根据 widgetType 显示不同的表单字段
 */
import { ref, computed, watch, onMounted } from 'vue'
import { MdEditor } from 'md-editor-v3'
import 'md-editor-v3/lib/style.css'
import type { WidgetConfig } from '@/types'
import { useMenuStore, usePageConfigStore } from '@/stores'
import { getAllBranches, type BranchOption } from '@/api/projectVersion'
import { IconPicker } from '@/components/common'

const props = defineProps<{
  modelValue: boolean
  widget: WidgetConfig | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  'save': [widget: Partial<WidgetConfig>]
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v)
})

const dialogTitle = computed(() => {
  if (!props.widget) return '编辑区块'
  const typeLabels: Record<string, string> = {
    welcome: '欢迎卡片',
    stats: '统计卡片',
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
  return `编辑${typeLabels[props.widget.widgetType] || '区块'}`
})

interface FormData {
  title: string
  content: Record<string, any>
  visibleRoles: string[]
}

// 表单数据
const form = ref<FormData>({
  title: '',
  content: {},
  visibleRoles: ['admin', 'developer', 'guest']
})

// 数据集合选项（从菜单获取，collection 名称从 pageId 推导）
const menuStore = useMenuStore()
const pageConfigStore = usePageConfigStore()

const collectionOptions = computed(() => {
  const dataMenus = menuStore.menuList.filter(m => m.menuType === 'data' && m.pageId)
  return dataMenus.map(m => {
    // pageId 格式为 page-{collection}，如 page-special-record
    // 从 pageId 提取真正的 collection 名称
    const collection = m.pageId?.replace(/^page-/, '') || ''
    return {
      value: collection,
      label: m.name,
      pageId: m.pageId
    }
  })
})

// 当前选中的数据集合对应的字段列表
const fieldOptions = computed(() => {
  const collection = form.value.content?.dataSource?.collection
  if (!collection) return []

  // pageConfig id 格式为 page-{collection}
  const pageConfigId = `page-${collection}`
  const pageConfig = pageConfigStore.pageConfigs.find(p => p.id === pageConfigId)
  if (!pageConfig?.fields) return []

  return pageConfig.fields.map(f => ({
    value: f.fieldName,
    label: f.label || f.fieldName
  }))
})

// chart 区块所选数据页的字段（用于选择分组字段）
const chartFieldOptions = computed(() => {
  const collection = form.value.content?.collection
  if (!collection) return []
  const pageConfig = pageConfigStore.pageConfigs.find(p => p.id === `page-${collection}`)
  if (!pageConfig?.fields) return []
  return pageConfig.fields.map(f => ({ value: f.fieldName, label: f.label || f.fieldName }))
})

// quick-form 目标数据页的可录入字段（排除自动生成字段）
const quickFormFieldOptions = computed(() => {
  const collection = form.value.content?.targetCollection
  if (!collection) return []
  const pageConfig = pageConfigStore.pageConfigs.find(p => p.id === `page-${collection}`)
  if (!pageConfig?.fields) return []
  return pageConfig.fields
    .filter(f => f.controlType !== 'autoSequence' && f.controlType !== 'autoTimestamp')
    .map(f => ({ value: f.fieldName, label: f.label || f.fieldName }))
})

// quick-form 「最近 5 条」可展示字段（含自动字段，便于用编号/合成字段做展示）
const quickFormDisplayFieldOptions = computed(() => {
  const collection = form.value.content?.targetCollection
  if (!collection) return []
  const pageConfig = pageConfigStore.pageConfigs.find(p => p.id === `page-${collection}`)
  if (!pageConfig?.fields) return []
  return pageConfig.fields
    .filter(f => f.controlType !== 'autoTimestamp')
    .map(f => ({ value: f.fieldName, label: f.label || f.fieldName }))
})

// 切换目标数据页时清空已选录入字段 / 展示字段（避免残留旧数据页的字段）
function onQuickFormCollectionChange() {
  form.value.content.fields = []
  form.value.content.displayField = ''
}

// 分支选项
const branchOptions = ref<BranchOption[]>([])

// data-card 的显示字段（多选）
const selectedColumns = computed({
  get: () => {
    const columns = form.value.content?.columns as string[] | undefined
    return columns || []
  },
  set: (val: string[]) => {
    if (!form.value.content) form.value.content = {}
    form.value.content.columns = val
  }
})

// data-card 的 filter 字符串
const filterStr = computed({
  get: () => {
    const filter = form.value.content?.dataSource?.filter
    return filter ? JSON.stringify(filter) : ''
  },
  set: (val: string) => {
    if (!form.value.content) form.value.content = {}
    if (!form.value.content.dataSource) form.value.content.dataSource = {}
    if (val.trim()) {
      try {
        form.value.content.dataSource.filter = JSON.parse(val)
      } catch {
        // 忽略解析错误
      }
    } else {
      form.value.content.dataSource.filter = undefined
    }
  }
})

// 加载分支列表和页面配置
onMounted(async () => {
  try {
    branchOptions.value = await getAllBranches()
  } catch {
    branchOptions.value = [{ id: 'main', name: '主分支' }]
  }

  // 加载页面配置以获取字段列表
  if (pageConfigStore.pageConfigs.length === 0) {
    await pageConfigStore.fetchPageConfigs()
  }
})

// 监听 widget 变化，初始化表单
watch(
  () => props.widget,
  (w) => {
    if (w) {
      const content = JSON.parse(JSON.stringify(w.content || {}))
      // 确保 data-card 有 dataSource 对象
      if (w.widgetType === 'data-card' && !content.dataSource) {
        content.dataSource = { collection: '', branchId: 'main' }
      }
      // 确保 quick-form 有基础结构
      if (w.widgetType === 'quick-form') {
        content.buttonLabel = content.buttonLabel || ''
        content.targetCollection = content.targetCollection || ''
        content.fields = Array.isArray(content.fields) ? content.fields : []
        content.displayField = content.displayField || ''
      }
      // 确保 chart 有基础结构
      if (w.widgetType === 'chart') {
        content.collection = content.collection || ''
        content.chartType = content.chartType || 'bar'
        content.groupField = content.groupField || ''
        content.limit = content.limit || 20
      }
      // todo / activity 仅需 limit
      if (w.widgetType === 'todo') content.limit = content.limit || 5
      if (w.widgetType === 'activity') content.limit = content.limit || 8
      // 确保 announcement 有基础结构
      if (w.widgetType === 'announcement') {
        content.title = content.title || ''
        content.body = content.body || ''
        content.level = content.level || 'info'
        content.closable = content.closable ?? false
      }
      form.value = {
        title: w.title || '',
        content,
        visibleRoles: [...(w.visibleRoles || ['admin', 'developer', 'guest'])]
      }
    }
  },
  { immediate: true }
)

function addStatsItem() {
  if (!form.value.content.items) form.value.content.items = []
  form.value.content.items.push({ type: 'menuCount', label: '', icon: 'Document' })
}

function addLinkItem() {
  if (!form.value.content.links) form.value.content.links = []
  form.value.content.links.push({ name: '', path: '', icon: 'Menu' })
}

function handleSave() {
  emit('save', {
    id: props.widget?.id,
    title: form.value.title,
    content: form.value.content as WidgetConfig['content'],
    visibleRoles: form.value.visibleRoles
  })
  visible.value = false
}

function handleClose() {
  emit('update:modelValue', false)
}
</script>

<style scoped lang="scss">
.form-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}

.stats-item-row,
.link-item-row {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
}
</style>