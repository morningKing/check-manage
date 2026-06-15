<template>
  <div class="category-view">
    <el-tabs
      v-if="visibleTabs.length > 1"
      v-model="activeTab"
      class="hub-tabs"
      @tab-change="onTabChange"
    >
      <el-tab-pane
        v-for="t in visibleTabs"
        :key="t.id"
        :label="t.label"
        :name="t.id"
      />
    </el-tabs>
    <div class="category-content">
      <KeepAlive :max="8">
        <component :is="currentComponent" v-if="currentComponent" :key="activeTab" />
      </KeepAlive>
      <el-empty v-if="visibleTabs.length === 0" description="无可用功能" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores'
import { SETTINGS_CATALOG, resolveActiveTab } from './settingsCatalog'
import { SETTINGS_TAB_COMPONENTS } from './settingsComponents'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

// 主来源为路由 meta.categoryId（Task 5 路由注入）；split 回退仅作兜底
/** 当前分类 id（路由 meta 注入；回退到路径末段） */
const categoryId = computed(
  () => (route.meta.categoryId as string) || route.path.split('/')[2] || ''
)

/** 当前分类按权限过滤后的可见 tab */
const visibleTabs = computed(() => {
  const cat = SETTINGS_CATALOG.find(c => c.id === categoryId.value)
  if (!cat) return []
  return cat.tabs.filter(t => auth.can(t.perm))
})

const activeTab = ref('')

/** 同步 activeTab：依据 ?tab= 与可见 tab */
watch(
  [visibleTabs, () => route.query.tab],
  () => {
    activeTab.value = resolveActiveTab(
      visibleTabs.value,
      route.query.tab as string | undefined
    )
  },
  { immediate: true }
)

const currentComponent = computed(
  () => (activeTab.value ? SETTINGS_TAB_COMPONENTS[activeTab.value] : null)
)

function onTabChange(name: string | number): void {
  router.replace({ query: { ...route.query, tab: String(name) } })
}
</script>

<style scoped lang="scss">
.category-view { display: flex; flex-direction: column; height: 100%; }
/* 下划线瘦身式 tab，复用上一轮风格基调 */
.hub-tabs {
  :deep(.el-tabs__header) { margin: 0 0 12px; }
  :deep(.el-tabs__nav-wrap::after) { height: 1px; background: var(--el-border-color-lighter); }
}
/* 内容区自身滚动，使上方 tab 头固定不被长内容顶走/覆盖 */
.category-content { flex: 1; min-height: 0; overflow-y: auto; }
</style>
