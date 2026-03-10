<template>
  <div class="kanban-board">
    <div
      v-for="column in orderedColumns"
      :key="String(column.value)"
      class="kanban-column"
    >
      <div class="column-header">
        <span class="column-title">{{ column.label }}</span>
        <el-badge :value="columnDataMap[String(column.value)]?.length || 0" type="info" />
      </div>
      <draggable
        class="column-cards"
        :list="columnDataMap[String(column.value)] || []"
        group="kanban"
        item-key="id"
        ghost-class="ghost"
        @change="(evt: any) => onDragChange(evt, String(column.value))"
      >
        <template #item="{ element }">
          <div
            class="kanban-card"
            @click="emit('card-click', element)"
          >
            <div
              v-if="cardColorField && element[cardColorField]"
              class="card-color-bar"
              :style="{ backgroundColor: getColorForValue(element[cardColorField]) }"
            />
            <div class="card-title">{{ getFieldDisplay(element, cardTitle) }}</div>
            <div
              v-for="fieldName in cardFields"
              :key="fieldName"
              class="card-field"
            >
              <span class="card-field-label">{{ getFieldLabel(fieldName) }}:</span>
              <span class="card-field-value">{{ getFieldDisplay(element, fieldName) }}</span>
            </div>
          </div>
        </template>
      </draggable>
      <div
        v-if="!columnDataMap[String(column.value)]?.length"
        class="column-empty"
      >
        暂无数据
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, watch, ref } from 'vue'
import draggable from 'vuedraggable'

interface GroupOption {
  label: string
  value: string | number | boolean
}

interface Props {
  data: any[]
  groupField: string
  groupOptions: GroupOption[]
  cardTitle: string
  cardFields: string[]
  fields: any[]
  columnOrder?: string[]
  cardColorField?: string
  searchKeyword?: string
}

const props = withDefaults(defineProps<Props>(), {
  columnOrder: () => [],
  cardColorField: '',
  searchKeyword: '',
})

const emit = defineEmits<{
  'card-move': [recordId: string, newGroupValue: string]
  'card-click': [record: any]
}>()

const colorMap: Record<string, string> = {
  high: '#F56C6C', urgent: '#F56C6C', '紧急': '#F56C6C', '高': '#F56C6C',
  medium: '#E6A23C', '中': '#E6A23C', '一般': '#E6A23C',
  low: '#67C23A', '低': '#67C23A',
}

const orderedColumns = computed<GroupOption[]>(() => {
  if (props.columnOrder && props.columnOrder.length > 0) {
    const optionMap = new Map(props.groupOptions.map(o => [String(o.value), o]))
    const ordered: GroupOption[] = []
    for (const val of props.columnOrder) {
      const opt = optionMap.get(val)
      if (opt) {
        ordered.push(opt)
        optionMap.delete(val)
      }
    }
    for (const opt of optionMap.values()) {
      ordered.push(opt)
    }
    return ordered
  }
  return props.groupOptions
})

// Mutable column data map for vuedraggable
const columnDataMap = ref<Record<string, any[]>>({})

function rebuildColumnData() {
  const map: Record<string, any[]> = {}
  for (const col of props.groupOptions) {
    map[String(col.value)] = []
  }
  const keyword = props.searchKeyword?.toLowerCase() || ''
  for (const record of props.data) {
    // Filter by search keyword
    if (keyword) {
      const fieldsToSearch = [props.cardTitle, ...props.cardFields]
      const matched = fieldsToSearch.some(fn => {
        const val = record[fn]
        if (val == null) return false
        return formatFieldValue(fn, val).toLowerCase().includes(keyword)
      })
      if (!matched) continue
    }
    const groupVal = String(record[props.groupField] ?? '')
    if (!map[groupVal]) map[groupVal] = []
    map[groupVal].push(record)
  }
  columnDataMap.value = map
}

watch(
  () => [props.data, props.searchKeyword, props.groupField, props.groupOptions],
  rebuildColumnData,
  { immediate: true, deep: true }
)

function onDragChange(evt: any, columnValue: string) {
  if (evt.added) {
    const record = evt.added.element
    if (record?.id) {
      emit('card-move', record.id, columnValue)
    }
  }
}

function getFieldLabel(fieldName: string): string {
  const field = props.fields.find((f: any) => f.fieldName === fieldName)
  return field?.label || fieldName
}

function getFieldDisplay(record: any, fieldName: string): string {
  const val = record[fieldName]
  if (val == null) return '-'
  return formatFieldValue(fieldName, val)
}

function formatFieldValue(fieldName: string, value: any): string {
  const field = props.fields.find((f: any) => f.fieldName === fieldName)
  if (!field) return String(value)

  if (['select', 'radio'].includes(field.controlType) && Array.isArray(field.options)) {
    const opt = field.options.find((o: any) => String(o.value) === String(value))
    if (opt) return opt.label
  }

  if (['date', 'datetime'].includes(field.controlType) && typeof value === 'string' && value.length >= 10) {
    try {
      const d = new Date(value)
      if (!isNaN(d.getTime())) {
        if (field.controlType === 'datetime') {
          return d.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
        }
        return d.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })
      }
    } catch { /* fall through */ }
  }

  if (Array.isArray(value)) return value.join(', ')
  return String(value)
}

function getColorForValue(value: any): string {
  return colorMap[String(value).toLowerCase()] || '#409EFF'
}
</script>

<style scoped>
.kanban-board {
  display: flex;
  overflow-x: auto;
  gap: 16px;
  padding: 16px;
  min-height: 400px;
}
.kanban-column {
  display: flex;
  flex-direction: column;
  min-width: 280px;
  max-width: 320px;
  background: #f5f7fa;
  border-radius: 8px;
  flex-shrink: 0;
}
.column-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  font-weight: 600;
  border-bottom: 1px solid #e4e7ed;
}
.column-title {
  font-size: 14px;
  color: #303133;
}
.column-cards {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px;
  overflow-y: auto;
  max-height: calc(100vh - 300px);
  min-height: 80px;
}
.column-empty {
  text-align: center;
  padding: 20px 0;
  color: #c0c4cc;
  font-size: 13px;
}
.kanban-card {
  position: relative;
  background: #fff;
  border-radius: 6px;
  padding: 12px;
  padding-left: 15px;
  cursor: pointer;
  box-shadow: 0 1px 3px rgba(0,0,0,0.1);
  transition: box-shadow 0.2s;
}
.kanban-card:hover {
  box-shadow: 0 3px 8px rgba(0,0,0,0.18);
}
.card-color-bar {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  border-radius: 6px 0 0 6px;
}
.card-title {
  font-weight: 500;
  margin-bottom: 8px;
  font-size: 14px;
  color: #303133;
  word-break: break-all;
}
.card-field {
  font-size: 12px;
  color: #909399;
  margin-bottom: 4px;
  display: flex;
  gap: 4px;
}
.card-field-label { color: #606266; flex-shrink: 0; }
.card-field-value { word-break: break-all; }
.ghost { opacity: 0.5; background: #e3f2fd; border-radius: 6px; }
</style>
