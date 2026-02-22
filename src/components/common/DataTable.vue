/**
 * 数据表格组件
 *
 * 职责：
 * - 根据字段配置动态渲染表格列
 * - 支持分页、排序
 * - 集成操作按钮（编辑、删除）
 *
 * 特性：
 * - 自动根据 controlType 格式化显示值
 * - 支持自定义操作列
 * - 响应式设计
 */
<template>
  <div class="data-table">
    <!-- 表格 -->
    <el-table
      ref="tableRef"
      :data="data"
      :loading="loading"
      border
      stripe
      highlight-current-row
      @sort-change="handleSortChange"
      @selection-change="handleSelectionChange"
      style="width: 100%"
    >
      <!-- 多选列 -->
      <el-table-column v-if="showSelection" type="selection" width="45" align="center" />

      <!-- 序号列 -->
      <el-table-column type="index" label="序号" width="60" align="center" />

      <!-- 动态渲染数据列 -->
      <el-table-column
        v-for="field in visibleFields"
        :key="field.id"
        :prop="field.fieldName"
        :label="field.label"
        :sortable="isSortable(field)"
        :min-width="getColumnWidth(field)"
        :show-overflow-tooltip="field.controlType !== 'relation'"
      >
        <template #default="{ row }">
          <template v-if="field.controlType === 'relation'">
            <span v-if="!row[`_rel_${field.fieldName}_labels`]?.length">-</span>
            <span v-else class="relation-tags">
              <el-tag
                v-for="item in row[`_rel_${field.fieldName}_labels`].slice(0, 3)"
                :key="item.id"
                size="small"
                class="relation-tag-link"
                @click.stop="handleRelationClick(item.id, field)"
              >{{ item.label }}</el-tag>
              <el-tag
                v-if="row[`_rel_${field.fieldName}_labels`].length > 3"
                size="small"
                type="info"
              >+{{ row[`_rel_${field.fieldName}_labels`].length - 3 }}</el-tag>
            </span>
          </template>
          <span
            v-else-if="field.controlType === 'reference' && row[field.fieldName]"
            class="reference-link"
            @click.stop="handleReferenceClick(row, field)"
          >{{ formatCellValue(row, field) }}</span>
          <span v-else>{{ formatCellValue(row, field) }}</span>
        </template>
      </el-table-column>

      <!-- 操作列 -->
      <el-table-column
        v-if="showActions"
        label="操作"
        :width="150"
        align="center"
        fixed="right"
      >
        <template #default="{ row }">
          <slot name="extra-actions" :row="row" />
          <el-button type="primary" link @click="handleEdit(row)">
            编辑
          </el-button>
          <el-button type="danger" link @click="handleDelete(row)">
            删除
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 分页 -->
    <div v-if="showPagination" class="pagination-container">
      <el-pagination
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        :page-sizes="[10, 20, 50, 100]"
        :total="total"
        layout="total, sizes, prev, pager, next, jumper"
        @size-change="handleSizeChange"
        @current-change="handleCurrentChange"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * DataTable 组件脚本
 *
 * Props：
 * - data: 表格数据
 * - fields: 字段配置列表
 * - loading: 加载状态
 * - showActions: 是否显示操作列
 * - showPagination: 是否显示分页
 * - total: 总记录数
 *
 * Events：
 * - edit: 编辑记录
 * - delete: 删除记录
 * - page-change: 分页变化
 */
import { ref, computed } from 'vue'
import type { FieldConfig, DynamicRecord } from '@/types'

// ==================== Props & Emits ====================

interface Props {
  /** 表格数据 */
  data: DynamicRecord[]
  /** 字段配置列表 */
  fields: FieldConfig[]
  /** 加载状态 */
  loading?: boolean
  /** 是否显示操作列 */
  showActions?: boolean
  /** 是否显示多选列 */
  showSelection?: boolean
  /** 是否显示分页 */
  showPagination?: boolean
  /** 总记录数 */
  total?: number
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
  showActions: true,
  showSelection: false,
  showPagination: true,
  total: 0
})

const emit = defineEmits<{
  (e: 'edit', row: DynamicRecord): void
  (e: 'delete', row: DynamicRecord): void
  (e: 'reference-click', row: DynamicRecord, field: FieldConfig): void
  (e: 'relation-click', relatedRecordId: string, field: FieldConfig): void
  (e: 'page-change', page: number, pageSize: number): void
  (e: 'sort-change', field: string, order: string): void
  (e: 'selection-change', rows: DynamicRecord[]): void
}>()

// ==================== State ====================

/**
 * el-table 实例引用
 */
const tableRef = ref()

/**
 * 当前页码
 */
const currentPage = ref(1)

/**
 * 每页数量
 */
const pageSize = ref(10)

// ==================== 计算属性 ====================

/**
 * 可见字段列表
 *
 * 过滤掉隐藏字段
 */
const visibleFields = computed(() => {
  return props.fields
    .filter((field) => !field.hidden)
    .sort((a, b) => a.order - b.order)
})

// ==================== 方法 ====================

/**
 * 判断字段是否可排序
 */
function isSortable(field: FieldConfig): boolean | 'custom' {
  // 文本、数字、日期类型支持排序
  const sortableTypes = ['text', 'number', 'date', 'datetime', 'autoTimestamp', 'autoSequence']
  return sortableTypes.includes(field.controlType) ? 'custom' : false
}

/**
 * 获取列宽度
 */
function getColumnWidth(field: FieldConfig): string {
  switch (field.controlType) {
    case 'textarea':
      return '200'
    case 'date':
    case 'datetime':
    case 'autoTimestamp':
      return '180'
    case 'autoSequence':
      return '150'
    case 'select':
    case 'multiSelect':
    case 'reference':
      return '120'
    case 'relation':
      return '200'
    default:
      return '150'
  }
}

/**
 * 格式化单元格值
 *
 * 根据字段控件类型进行格式化显示
 */
function formatCellValue(row: any, field: FieldConfig): string {
  const value = row[field.fieldName]
  if (value === null || value === undefined || value === '') {
    return '-'
  }

  switch (field.controlType) {
    case 'select':
    case 'radio':
      // 查找选项标签
      const option = field.options?.find((opt) => opt.value === value)
      return option?.label || String(value)

    case 'multiSelect':
    case 'checkbox':
      // 多选值转换为标签列表
      if (Array.isArray(value)) {
        return value
          .map((v) => {
            const opt = field.options?.find((o) => o.value === v)
            return opt?.label || String(v)
          })
          .join('、')
      }
      return String(value)

    case 'date':
      // 格式化日期
      return formatDate(value, 'YYYY-MM-DD')

    case 'datetime':
      // 格式化日期时间
      return formatDate(value, 'YYYY-MM-DD HH:mm:ss')

    case 'autoTimestamp':
      return formatDate(value, 'YYYY-MM-DD HH:mm:ss')

    case 'autoSequence':
      return String(value)

    case 'file':
    case 'image':
      // 显示文件数量
      if (Array.isArray(value)) {
        return `${value.length} 个文件`
      }
      return '-'

    case 'relation':
      if (Array.isArray(value)) {
        return value.length === 0 ? '-' : `${value.length} 条关联`
      }
      return String(value)

    case 'reference':
      // 显示父记录的 displayField 值
      const displayVal = row[`_ref_${field.fieldName}_display`]
      return displayVal || String(value)

    default:
      return String(value)
  }
}

/**
 * 简单日期格式化
 */
function formatDate(value: string, format: string): string {
  if (!value) return '-'
  try {
    const date = new Date(value)
    if (isNaN(date.getTime())) return value

    const year = date.getFullYear()
    const month = String(date.getMonth() + 1).padStart(2, '0')
    const day = String(date.getDate()).padStart(2, '0')
    const hours = String(date.getHours()).padStart(2, '0')
    const minutes = String(date.getMinutes()).padStart(2, '0')
    const seconds = String(date.getSeconds()).padStart(2, '0')

    if (format === 'YYYY-MM-DD') {
      return `${year}-${month}-${day}`
    }
    return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`
  } catch {
    return value
  }
}

/**
 * 处理编辑
 */
function handleEdit(row: DynamicRecord): void {
  emit('edit', row)
}

/**
 * 处理删除
 */
function handleDelete(row: DynamicRecord): void {
  emit('delete', row)
}

/**
 * 处理每页数量变化
 */
function handleSizeChange(size: number): void {
  pageSize.value = size
  currentPage.value = 1
  emit('page-change', 1, size)
}

/**
 * 处理页码变化
 */
function handleCurrentChange(page: number): void {
  currentPage.value = page
  emit('page-change', page, pageSize.value)
}

/**
 * 处理排序变化
 */
function handleSortChange({ prop, order }: { prop: string; order: string }): void {
  emit('sort-change', prop, order === 'ascending' ? 'asc' : 'desc')
}

/**
 * 处理引用字段点击
 */
function handleReferenceClick(row: DynamicRecord, field: FieldConfig): void {
  emit('reference-click', row, field)
}

/**
 * 处理关联字段 Tag 点击 — 跳转到关联记录所在页面
 */
function handleRelationClick(relatedRecordId: string, field: FieldConfig): void {
  emit('relation-click', relatedRecordId, field)
}

/**
 * 处理多选变化
 */
function handleSelectionChange(rows: DynamicRecord[]): void {
  emit('selection-change', rows)
}

/**
 * 清除多选
 */
function clearSelection(): void {
  tableRef.value?.clearSelection()
}

// ==================== 暴露 ====================

defineExpose({ tableRef, clearSelection })
</script>

<style scoped lang="scss">
.data-table {
  width: 100%;
}

.reference-link {
  color: #409eff;
  cursor: pointer;

  &:hover {
    text-decoration: underline;
  }
}

.relation-tags {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

.relation-tag-link {
  cursor: pointer;
  transition: all 0.2s;

  &:hover {
    color: #409eff;
    border-color: #409eff;
  }
}

.pagination-container {
  display: flex;
  justify-content: flex-end;
  padding: 16px 0;
}
</style>
