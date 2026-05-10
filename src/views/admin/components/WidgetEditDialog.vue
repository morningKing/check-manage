<template>
  <el-dialog
    v-model="visible"
    :title="dialogTitle"
    width="600px"
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
        <el-input
          v-model="form.content.markdown"
          type="textarea"
          :rows="8"
          placeholder="Markdown 内容"
        />
      </el-form-item>
    </el-form>

    <!-- data-card 类型 -->
    <el-form v-if="widget?.widgetType === 'data-card'" label-width="80px">
      <el-form-item label="数据集合">
        <el-input
          v-model="form.content.dataSource.collection"
          placeholder="如：task-calendar"
        />
      </el-form-item>
      <el-form-item label="分支ID">
        <el-input
          v-model="form.content.dataSource.branchId"
          placeholder="可选，指定数据分支"
        />
      </el-form-item>
      <el-form-item label="数据过滤">
        <el-input
          v-model="filterStr"
          type="textarea"
          :rows="2"
          placeholder="可选，JSON 格式过滤条件，如：{\"status\":\"active\"}"
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
        <el-input
          v-model="columnsStr"
          placeholder="字段名逗号分隔，如：taskName,status"
        />
      </el-form-item>
      <el-form-item label="标题字段">
        <el-input v-model="form.content.titleField" placeholder="如：taskName" />
      </el-form-item>
      <el-form-item label="点击跳转">
        <el-switch v-model="form.content.linkToDetail" />
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
import { ref, computed, watch } from 'vue'
import type { WidgetConfig } from '@/types'

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
    'data-card': '数据卡片'
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

// data-card 的 columns 字符串
const columnsStr = computed({
  get: () => {
    const columns = form.value.content?.columns as string[] | undefined
    return columns ? columns.join(',') : ''
  },
  set: (val: string) => {
    if (!form.value.content) form.value.content = {}
    form.value.content.columns = val.split(',').map(s => s.trim()).filter(Boolean)
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

// 监听 widget 变化，初始化表单
watch(
  () => props.widget,
  (w) => {
    if (w) {
      form.value = {
        title: w.title || '',
        content: JSON.parse(JSON.stringify(w.content || {})),
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
.stats-item-row,
.link-item-row {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
}
</style>