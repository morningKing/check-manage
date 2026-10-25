<template>
  <div class="plugin-toggle-table">
    <el-alert
      v-if="loadError"
      type="warning"
      :closable="false"
      show-icon
      title="插件清单加载失败"
      :description="loadError"
    />
    <div v-else-if="loading" class="plugin-toggle-table__loading">加载中…</div>
    <el-table v-else :data="plugins" border>
      <el-table-column label="插件">
        <template #default="{ row }">
          <div class="plugin-toggle-table__name">{{ row.name }}</div>
          <div class="plugin-toggle-table__desc">{{ row.description }}</div>
        </template>
      </el-table-column>
      <el-table-column prop="version" label="版本" width="90" />
      <el-table-column label="状态" width="110" align="center">
        <template #default="{ row }">
          <el-switch
            :model-value="row.enabled"
            :disabled="busyId === row.id"
            @change="(v: string | number | boolean) => toggle(row, Boolean(v))"
          />
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
/**
 * 功能插件开关表（plugin-architecture spec §7）。
 *
 * 乐观更新 + 失败回滚；切换成功后 pluginsStore.refresh()——让同页其它
 * 已挂路由的守卫在下次导航拿到新启用集（Task 4 carry：refresh 不与
 * 首载 in-flight 的 ensureLoaded 并发，本组件只在远端成功后调用）。
 */
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getPlugins, setPluginEnabled, type PluginInfo } from '@/api/plugins'
import { usePluginsStore } from '@/stores/plugins'

const plugins = ref<PluginInfo[]>([])
const loading = ref(false)
const loadError = ref('')
const busyId = ref('')

onMounted(load)

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    plugins.value = (await getPlugins()) ?? []
  } catch (e: unknown) {
    const data = (e as { response?: { data?: { error?: string } } })?.response?.data
    loadError.value = (typeof data?.error === 'string' && data.error) || '请稍后重试'
  } finally {
    loading.value = false
  }
}

async function toggle(row: PluginInfo, next: boolean) {
  if (busyId.value) return
  const prev = row.enabled
  row.enabled = next          // 乐观翻转
  busyId.value = row.id
  try {
    await setPluginEnabled(row.id, next)
    await usePluginsStore().refresh()
  } catch (e: unknown) {
    row.enabled = prev        // 回滚
    const data = (e as { response?: { data?: { error?: string } } })?.response?.data
    ElMessage.error((typeof data?.error === 'string' && data.error) || '切换失败，请重试')
  } finally {
    busyId.value = ''
  }
}
</script>

<style scoped>
.plugin-toggle-table__name { font-weight: 600; }
.plugin-toggle-table__desc { font-size: 12px; color: var(--el-text-color-secondary); }
</style>
