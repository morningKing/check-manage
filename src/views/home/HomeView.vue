/**
 * 首页视图
 *
 * 职责：
 * - 动态渲染首页区块组件
 * - 从 systemConfigStore 获取可见区块配置
 * - 支持快捷入口的批量导出功能
 */
<template>
  <div class="home-view">
    <template v-for="widget in visibleWidgets" :key="widget.id">
      <component
        :is="getWidgetComponent(widget.widgetType)"
        :content="widget.content"
        :title="widget.title"
        class="widget-wrapper"
      />
    </template>
  </div>
</template>

<script setup lang="ts">
/**
 * HomeView 组件脚本
 *
 * 动态渲染首页区块，支持配置化管理
 */
import { computed, onMounted } from 'vue'
import { useSystemConfigStore, usePageConfigStore } from '@/stores'
import { widgetComponentMap } from '@/components/home'
import type { WidgetType } from '@/types'

// ==================== Store ====================

const systemConfigStore = useSystemConfigStore()
const pageConfigStore = usePageConfigStore()

// ==================== 计算属性 ====================

/**
 * 可见区块列表
 */
const visibleWidgets = computed(() => systemConfigStore.visibleWidgets)

// ==================== 方法 ====================

/**
 * 根据区块类型获取对应组件
 */
function getWidgetComponent(type: WidgetType) {
  return widgetComponentMap[type]
}

// ==================== 生命周期 ====================

onMounted(async () => {
  await systemConfigStore.initialize()
  if (pageConfigStore.pageConfigs.length === 0) {
    await pageConfigStore.fetchPageConfigs()
  }
})
</script>

<style scoped lang="scss">
.home-view {
  padding: 0;
}

.widget-wrapper {
  margin-bottom: 20px;

  &:last-child {
    margin-bottom: 0;
  }
}
</style>