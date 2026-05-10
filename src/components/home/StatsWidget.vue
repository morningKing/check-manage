/**
 * 统计卡片 Widget
 *
 * 使用 el-row/el-col 展示统计卡片（每项 span=8）
 * 统计值从 menuStore 和 pageConfigStore 计算
 */
<template>
  <el-row :gutter="20">
    <el-col v-for="item in content.items" :key="item.type" :span="8">
      <el-card class="stat-card">
        <div class="stat-content">
          <el-icon :class="['stat-icon', getIconClass(item.type)]">
            <component :is="getIcon(item.icon)" />
          </el-icon>
          <div class="stat-info">
            <div class="stat-value">{{ getStatValue(item) }}</div>
            <div class="stat-label">{{ item.label }}</div>
          </div>
        </div>
      </el-card>
    </el-col>
  </el-row>
</template>

<script setup lang="ts">
import { Document, Files, Setting, DataLine } from '@element-plus/icons-vue'
import { useMenuStore, usePageConfigStore } from '@/stores'
import type { WidgetContentMap, StatsItem } from '@/types'

defineProps<{
  content: WidgetContentMap['stats']
}>()

const menuStore = useMenuStore()
const pageConfigStore = usePageConfigStore()

/**
 * 获取统计值
 */
function getStatValue(item: StatsItem): number {
  switch (item.type) {
    case 'menuCount':
      return menuStore.menuList.length
    case 'pageCount':
      return pageConfigStore.pageConfigs.length
    case 'fieldCount':
      return pageConfigStore.pageConfigs.reduce((total, config) => {
        return total + config.fields.length
      }, 0)
    case 'recordCount':
      // recordCount 需要指定 collection，暂不支持
      return 0
    default:
      return 0
  }
}

/**
 * 获取图标组件
 */
function getIcon(iconName: string) {
  const iconMap: Record<string, any> = {
    Document,
    Files,
    Setting,
    DataLine,
  }
  return iconMap[iconName] || Document
}

/**
 * 获取图标样式类
 */
function getIconClass(type: string): string {
  const classMap: Record<string, string> = {
    menuCount: 'stat-icon-primary',
    pageCount: 'stat-icon-success',
    fieldCount: 'stat-icon-warning',
    recordCount: 'stat-icon-info',
  }
  return classMap[type] || 'stat-icon-primary'
}
</script>

<style scoped lang="scss">
.stat-card {
  .stat-content {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 8px 0;
  }

  .stat-icon {
    font-size: 48px;
    padding: 12px;
    border-radius: 12px;

    &.stat-icon-primary {
      color: #409eff;
      background-color: #ecf5ff;
    }

    &.stat-icon-success {
      color: #67c23a;
      background-color: #f0f9eb;
    }

    &.stat-icon-warning {
      color: #e6a23c;
      background-color: #fdf6ec;
    }

    &.stat-icon-info {
      color: #909399;
      background-color: #f4f4f5;
    }
  }

  .stat-info {
    .stat-value {
      font-size: 32px;
      font-weight: 600;
      color: #303133;
    }

    .stat-label {
      font-size: 14px;
      color: #909399;
      margin-top: 4px;
    }
  }
}
</style>