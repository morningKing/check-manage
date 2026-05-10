/**
 * 快捷入口 Widget
 *
 * 显示链接列表，支持 router-link 和 action（如 batchExport）
 * 批量导出 action 时显示 BatchExportDialog
 */
<template>
  <el-card>
    <template #header>
      <div class="card-header">
        <span>{{ title || '快捷入口' }}</span>
      </div>
    </template>
    <div class="quick-links">
      <template v-for="link in content.links" :key="link.name">
        <router-link v-if="link.path && !link.action" :to="link.path" class="quick-link">
          <el-icon><component :is="getIcon(link.icon)" /></el-icon>
          <span>{{ link.name }}</span>
        </router-link>
        <div v-else class="quick-link" @click="handleAction(link.action)">
          <el-icon><component :is="getIcon(link.icon)" /></el-icon>
          <span>{{ link.name }}</span>
        </div>
      </template>
    </div>
    <BatchExportDialog v-model="batchExportVisible" />
  </el-card>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { Menu, Files, Download, Setting, Document, DataLine } from '@element-plus/icons-vue'
import { BatchExportDialog } from '@/components/common'
import type { WidgetContentMap } from '@/types'

defineProps<{
  content: WidgetContentMap['quick-links']
  title?: string
}>()

const batchExportVisible = ref(false)

/**
 * 获取图标组件
 */
function getIcon(iconName: string) {
  const iconMap: Record<string, any> = {
    Menu,
    Files,
    Download,
    Setting,
    Document,
    DataLine,
  }
  return iconMap[iconName] || Document
}

/**
 * 处理 action
 */
function handleAction(action?: string) {
  if (action === 'batchExport') {
    batchExportVisible.value = true
  }
}
</script>

<style scoped lang="scss">
.card-header {
  display: flex;
  align-items: center;
  font-weight: 600;
}

.quick-links {
  display: flex;
  gap: 16px;

  .quick-link {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
    padding: 20px 32px;
    background-color: #f5f7fa;
    border-radius: 8px;
    color: #606266;
    text-decoration: none;
    transition: all 0.3s ease;
    cursor: pointer;

    &:hover {
      background-color: #ecf5ff;
      color: #409eff;
      transform: translateY(-2px);
    }

    .el-icon {
      font-size: 32px;
    }

    span {
      font-size: 14px;
    }
  }
}
</style>