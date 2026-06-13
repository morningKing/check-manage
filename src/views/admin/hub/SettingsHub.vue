<template>
  <div class="settings-hub">
    <aside class="hub-rail">
      <div class="hub-rail__title">设置中心</div>
      <nav class="hub-rail__nav">
        <RouterLink
          v-for="c in categories"
          :key="c.id"
          :to="`/admin/${c.id}`"
          class="hub-rail__item"
          :class="{ active: activeCategory === c.id }"
        >
          <el-icon><component :is="iconOf(c.icon)" /></el-icon>
          <span>{{ c.label }}</span>
        </RouterLink>
      </nav>
    </aside>
    <section class="hub-main">
      <header class="hub-main__head">
        <span class="hub-crumb">设置中心</span>
        <el-icon class="hub-crumb__sep"><ArrowRight /></el-icon>
        <span class="hub-crumb hub-crumb--current">{{ currentLabel }}</span>
      </header>
      <div class="hub-main__body">
        <router-view />
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import * as ElIcons from '@element-plus/icons-vue'
import { ArrowRight } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores'
import { filterCatalog } from './settingsCatalog'

const route = useRoute()
const auth = useAuthStore()

const categories = computed(() => filterCatalog(auth.can))

const activeCategory = computed(
  () => (route.meta.categoryId as string) || route.path.split('/')[2] || ''
)
const currentLabel = computed(
  () => categories.value.find(c => c.id === activeCategory.value)?.label || ''
)

function iconOf(name: string) {
  return (ElIcons as Record<string, any>)[name] || ElIcons.Setting
}
</script>

<style scoped lang="scss">
.settings-hub { display: flex; height: 100%; min-height: 0; }

.hub-rail {
  width: 200px; flex-shrink: 0;
  border-right: 1px solid var(--el-border-color-lighter);
  background: var(--app-shell-bg, #f7f8fa);
  display: flex; flex-direction: column;
}
.hub-rail__title {
  padding: 14px 16px 8px; font-size: 13px; font-weight: 600;
  color: var(--el-text-color-secondary);
}
.hub-rail__nav { display: flex; flex-direction: column; padding: 4px 8px; gap: 2px; }
.hub-rail__item {
  display: flex; align-items: center; gap: 8px;
  padding: 7px 10px; border-radius: 6px;
  font-size: 13px; color: var(--el-text-color-regular);
  text-decoration: none; border-left: 2px solid transparent;
  transition: background-color .15s, color .15s;
  &:hover { background: var(--el-fill-color-light); color: var(--el-text-color-primary); }
  &.active {
    background: var(--app-shell-active-bg, #eceef5);
    color: var(--el-text-color-primary); font-weight: 500;
    border-left-color: var(--el-color-primary);
  }
}

.hub-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.hub-main__head {
  display: flex; align-items: center; gap: 6px;
  padding: 12px 16px; border-bottom: 1px solid var(--el-border-color-lighter);
  font-size: 13px; color: var(--el-text-color-secondary);
}
.hub-crumb__sep { font-size: 12px; }
.hub-crumb--current { color: var(--el-text-color-primary); font-weight: 500; }
.hub-main__body { flex: 1; min-height: 0; overflow: auto; padding: 16px; }

:global(html.dark) .hub-rail { background: var(--app-shell-bg); }
</style>
