<template>
  <el-dialog
    v-model="visible"
    title="编辑列配置"
    width="600px"
    :close-on-click-modal="false"
  >
    <div class="column-config">
      <div class="column-toolbar">
        <el-input
          v-model="searchText"
          placeholder="搜索字段..."
          :prefix-icon="Search"
          clearable
          size="small"
          style="flex: 1"
        />
        <el-button size="small" @click="checkAll">全选</el-button>
        <el-button size="small" @click="uncheckAll">全不选</el-button>
      </div>

      <draggable
        v-model="localColumns"
        item-key="fieldId"
        handle=".drag-handle"
        ghost-class="ghost"
        class="column-list"
      >
        <template #item="{ element }">
          <div
            v-show="matchSearch(element.fieldId)"
            class="column-item"
            :class="{ hidden: !element.visible }"
          >
            <el-icon class="drag-handle"><Rank /></el-icon>
            <el-checkbox
              :model-value="element.visible"
              @change="(val: boolean) => toggleVisible(element.fieldId, val)"
            />
            <span class="field-label">{{ getFieldLabel(element.fieldId) }}</span>
            <el-input
              v-if="element.visible"
              :model-value="element.width"
              placeholder="auto"
              size="small"
              style="width: 80px"
              @update:model-value="(val: string) => updateWidth(element.fieldId, val)"
            />
            <span v-else class="width-disabled">-</span>
          </div>
        </template>
      </draggable>
      <div v-if="searchText && filteredCount === 0" class="empty-search">无匹配字段</div>

      <el-divider />

      <div class="config-section">
        <h4>默认排序</h4>
        <div v-for="(sort, index) in localSortConfig" :key="index" class="sort-row">
          <el-select v-model="sort.field" placeholder="选择字段" style="width: 150px">
            <el-option
              v-for="f in sortableFields"
              :key="f.fieldName"
              :label="f.label"
              :value="f.fieldName"
            />
          </el-select>
          <el-select v-model="sort.direction" style="width: 100px">
            <el-option label="升序" value="asc" />
            <el-option label="降序" value="desc" />
          </el-select>
          <el-button text type="danger" @click="removeSort(index)">
            <el-icon><Delete /></el-icon>
          </el-button>
        </div>
        <el-button size="small" @click="addSort">+ 添加排序</el-button>
      </div>

      <div class="config-section">
        <h4>分组字段</h4>
        <el-select v-model="localGroupField" clearable placeholder="不分组" style="width: 200px">
          <el-option
            v-for="f in groupableFields"
            :key="f.fieldName"
            :label="f.label"
            :value="f.fieldName"
          />
        </el-select>
      </div>
    </div>

    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button type="primary" @click="handleSave">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import draggable from 'vuedraggable'
import { Rank, Delete, Search } from '@element-plus/icons-vue'
import type { FieldConfig, ColumnConfigItem, SortConfigItem } from '@/types'

const props = defineProps<{
  fields: FieldConfig[]
}>()

const emit = defineEmits<{
  save: [columns: ColumnConfigItem[], sortConfig: SortConfigItem[], groupField: string | null]
}>()

const visible = ref(false)
const localColumns = ref<ColumnConfigItem[]>([])
const localSortConfig = ref<SortConfigItem[]>([])
const localGroupField = ref<string | null>(null)
const searchText = ref('')

const filteredCount = computed(() =>
  localColumns.value.filter(c => matchSearch(c.fieldId)).length
)

function matchSearch(fieldId: string): boolean {
  if (!searchText.value) return true
  const keyword = searchText.value.toLowerCase()
  const label = getFieldLabel(fieldId).toLowerCase()
  return label.includes(keyword) || fieldId.toLowerCase().includes(keyword)
}

const sortableFields = computed(() =>
  props.fields.filter(f => !['relation', 'file', 'image'].includes(f.controlType))
)

const groupableFields = computed(() =>
  props.fields.filter(f => ['select', 'radio'].includes(f.controlType))
)

function getFieldLabel(fieldId: string): string {
  return props.fields.find(f => f.id === fieldId)?.label || fieldId
}

function toggleVisible(fieldId: string, visible: boolean) {
  const col = localColumns.value.find(c => c.fieldId === fieldId)
  if (col) col.visible = visible
}

function checkAll() {
  localColumns.value.forEach(c => { c.visible = true })
}

function uncheckAll() {
  localColumns.value.forEach(c => { c.visible = false })
}

function updateWidth(fieldId: string, width: string) {
  const col = localColumns.value.find(c => c.fieldId === fieldId)
  if (col) col.width = width || 'auto'
}

function addSort() {
  localSortConfig.value.push({ field: '', direction: 'asc' })
}

function removeSort(index: number) {
  localSortConfig.value.splice(index, 1)
}

function open(columns: ColumnConfigItem[], sortConfig: SortConfigItem[], groupField?: string | null) {
  localColumns.value = columns.map(c => ({ ...c }))
  localSortConfig.value = sortConfig.map(s => ({ ...s }))
  localGroupField.value = groupField || null
  searchText.value = ''
  visible.value = true
}

function close() {
  visible.value = false
}

function handleSave() {
  const validSorts = localSortConfig.value.filter(s => s.field)
  emit('save', localColumns.value, validSorts, localGroupField.value)
  close()
}

defineExpose({ open, close })
</script>

<style scoped>
.column-toolbar { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; }
.column-list { max-height: 300px; overflow-y: auto; }
.column-item { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border: 1px solid #ebeef5; border-radius: 4px; margin-bottom: 4px; background: #fff; transition: all 0.2s; }
.column-item.hidden { opacity: 0.5; }
.column-item.ghost { opacity: 0.5; background: #ecf5ff; }
.drag-handle { cursor: grab; color: #c0c4cc; }
.drag-handle:active { cursor: grabbing; }
.field-label { flex: 1; font-size: 14px; }
.width-disabled { width: 80px; text-align: center; color: #c0c4cc; }
.empty-search { padding: 20px; text-align: center; color: #909399; font-size: 13px; }
.config-section { margin-bottom: 16px; }
.config-section h4 { margin: 0 0 8px 0; font-size: 14px; color: #606266; }
.sort-row { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }
</style>
