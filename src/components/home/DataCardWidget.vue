/**
 * 数据卡片 Widget
 *
 * 支持三种显示模式：
 * - count: 显示统计数字
 * - list: 卡片列表
 * - table: 简化表格
 *
 * 从 API 获取数据，支持点击跳转详情页
 */
<template>
  <el-card v-loading="loading">
    <template #header>
      <div class="card-header">
        <span>{{ title || '数据卡片' }}</span>
        <el-tag v-if="content.displayType === 'count'" type="info" size="small">
          共 {{ totalCount }} 条
        </el-tag>
      </div>
    </template>

    <!-- Count 模式: 显示统计数字 -->
    <div v-if="content.displayType === 'count'" class="count-display">
      <div class="count-number">{{ displayCount }}</div>
      <div class="count-label">{{ countLabel }}</div>
    </div>

    <!-- List 模式: 卡片列表 -->
    <div v-else-if="content.displayType === 'list'" class="list-display">
      <div
        v-for="item in displayData"
        :key="item.id"
        class="list-item"
        :class="{ clickable: content.linkToDetail }"
        @click="handleItemClick(item)"
      >
        <div class="item-title">{{ getItemTitle(item) }}</div>
        <div v-if="displayColumns.length > 1" class="item-fields">
          <span v-for="col in displayColumns.slice(1)" :key="col" class="item-field">
            <span class="field-label">{{ getFieldLabel(col) }}:</span>
            <span class="field-value">{{ formatFieldValue(item, col) }}</span>
          </span>
        </div>
      </div>
      <el-empty v-if="displayData.length === 0" description="暂无数据" :image-size="60" />
    </div>

    <!-- Table 模式: 简化表格 -->
    <el-table v-else-if="content.displayType === 'table'" :data="displayData" size="small">
      <el-table-column
        v-for="col in displayColumns"
        :key="col"
        :prop="col"
        :label="getFieldLabel(col)"
        min-width="120"
      >
        <template #default="{ row }">
          <span
            v-if="col === displayColumns[0] && content.linkToDetail"
            class="table-link"
            @click="handleItemClick(row)"
          >
            {{ formatFieldValue(row, col) }}
          </span>
          <span v-else>{{ formatFieldValue(row, col) }}</span>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="content.displayType === 'table' && displayData.length === 0" description="暂无数据" :image-size="60" />
  </el-card>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { usePageConfigStore } from '@/stores'
import { get } from '@/utils/request'
import type { WidgetContentMap, DynamicRecord } from '@/types'

const props = defineProps<{
  content: WidgetContentMap['data-card']
  title?: string
}>()

const router = useRouter()
const pageConfigStore = usePageConfigStore()

// 数据状态
const loading = ref(false)
const rawData = ref<DynamicRecord[]>([])
const totalCount = ref(0)

// 计算属性
const dataSource = computed(() => props.content.dataSource)
const displayColumns = computed(() => props.content.columns || [])

// 显示数量（count 模式）
const displayCount = computed(() => totalCount.value)

// 统计标签
const countLabel = computed(() => {
  const collection = dataSource.value.collection
  // pageConfig id 格式为 page-{collection}
  const pageConfig = pageConfigStore.pageConfigs.find(
    (c) => c.id === `page-${collection}`
  )
  return pageConfig?.name || '记录'
})

// 显示数据（list/table 模式）
const displayData = computed(() => {
  const limit = dataSource.value.limit || 10
  return rawData.value.slice(0, limit)
})

/**
 * 获取字段标签
 */
function getFieldLabel(fieldName: string): string {
  const collection = dataSource.value.collection
  const pageConfig = pageConfigStore.pageConfigs.find(
    (c) => c.id === `page-${collection}`
  )
  if (!pageConfig) return fieldName
  const field = pageConfig.fields.find((f) => f.fieldName === fieldName)
  return field?.label || fieldName
}

/**
 * 获取列表项标题
 */
function getItemTitle(item: DynamicRecord): string {
  const titleField = props.content.titleField || displayColumns.value[0]
  return formatFieldValue(item, titleField)
}

/**
 * 格式化字段值
 */
function formatFieldValue(item: DynamicRecord, fieldName: string): string {
  const value = item[fieldName]
  if (value === null || value === undefined) return ''
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

/**
 * 点击跳转详情页
 */
function handleItemClick(item: DynamicRecord) {
  if (!props.content.linkToDetail) return
  const collection = dataSource.value.collection
  router.push(`/dynamic/${collection}?id=${item.id}`)
}

/**
 * 从 API 获取数据
 */
async function fetchData() {
  loading.value = true
  try {
    const collection = dataSource.value.collection
    const params: Record<string, any> = {
      pageSize: dataSource.value.limit || 100
    }

    // 添加 branchId 参数
    if (dataSource.value.branchId) {
      params.branchId = dataSource.value.branchId
    }

    // 添加过滤条件
    if (dataSource.value.filter) {
      params.q = JSON.stringify(dataSource.value.filter)
    }

    const response = await get<{ data: DynamicRecord[]; total: number }>(
      `/${collection}`,
      params
    )

    rawData.value = response.data || []
    totalCount.value = response.total || 0
  } catch (error) {
    console.error('获取数据卡片数据失败:', error)
    rawData.value = []
    totalCount.value = 0
  } finally {
    loading.value = false
  }
}

// 初始化
onMounted(() => {
  fetchData()
})
</script>

<style scoped lang="scss">
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
}

.count-display {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 24px 0;

  .count-number {
    font-size: 48px;
    font-weight: 600;
    color: #409eff;
    line-height: 1.2;
  }

  .count-label {
    font-size: 14px;
    color: #909399;
    margin-top: 8px;
  }
}

.list-display {
  display: flex;
  flex-direction: column;
  gap: 12px;

  .list-item {
    padding: 12px 16px;
    background-color: #f5f7fa;
    border-radius: 8px;
    transition: all 0.3s ease;

    &.clickable {
      cursor: pointer;

      &:hover {
        background-color: #ecf5ff;
        transform: translateX(4px);
      }
    }

    .item-title {
      font-size: 14px;
      font-weight: 500;
      color: #303133;
    }

    .item-fields {
      margin-top: 8px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;

      .item-field {
        font-size: 12px;
        color: #606266;

        .field-label {
          color: #909399;
        }

        .field-value {
          margin-left: 4px;
        }
      }
    }
  }
}

.table-link {
  color: #409eff;
  cursor: pointer;

  &:hover {
    text-decoration: underline;
  }
}
</style>