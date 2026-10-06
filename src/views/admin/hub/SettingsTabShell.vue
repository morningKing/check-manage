<template>
  <el-tabs v-if="visibleTabs.length" v-model="activeTab" class="settings-tab-shell">
    <el-tab-pane v-for="t in visibleTabs" :key="t.id" :name="t.id" :label="t.label" lazy>
      <keep-alive>
        <component :is="components[t.id]" />
      </keep-alive>
    </el-tab-pane>
  </el-tabs>
  <el-empty v-else description="没有可用的功能页签" />
</template>

<script setup lang="ts">
import { computed, ref, watch, defineAsyncComponent } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { findSettingsItem, itemPerms } from '@/views/admin/hub/settingsCatalog'
import { useAuthStore } from '@/stores/auth'

const props = defineProps<{ itemId: string }>()
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const tabs = computed(() => findSettingsItem(props.itemId)?.tabs ?? [])
const visibleTabs = computed(() => tabs.value.filter(t => itemPerms(t.perm).some(k => auth.can(k))))

// defineAsyncComponent 包装必须对同一 tab 恒定（setup 期一次构建），
// 否则 keep-alive 永远命中不了缓存、切 tab 即重挂载重取数。
const components: Record<string, ReturnType<typeof defineAsyncComponent>> = {}
for (const t of tabs.value) components[t.id] = defineAsyncComponent(t.component)

function normalize(tab: unknown): string {
  const id = typeof tab === 'string' ? tab : ''
  if (visibleTabs.value.some(t => t.id === id)) return id
  return visibleTabs.value[0]?.id ?? ''
}
const activeTab = ref(normalize(route.query.tab))

// 浏览器前进/后退或外部跳转带 ?tab= 时跟随
watch(() => route.query.tab, v => { activeTab.value = normalize(v) })
// 页签切换回写地址栏（replace 不留历史），保证深链可收藏
watch(activeTab, v => {
  if (route.query.tab !== v) router.replace({ query: { ...route.query, tab: v } })
})
</script>
