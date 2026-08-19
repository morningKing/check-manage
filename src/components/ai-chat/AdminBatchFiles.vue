<template>
  <div class="admin-files">
    <div class="admin-files__bar">
      <span class="admin-files__ident">{{ ident }}</span>
      <div class="admin-files__bar-right">
        <el-checkbox v-if="hasUnimported && !loading" v-model="selectAll"
                     data-testid="select-all">全选</el-checkbox>
        <el-button type="primary" size="small" :disabled="!selectedPaths.length || loading"
                   data-testid="import-btn" @click="onImport">
          导入到 data_files（{{ selectedPaths.length }}）
        </el-button>
      </div>
    </div>

    <el-alert v-if="truncated" type="info" :closable="false" show-icon
              title="仅显示最近 1000 个文件" />

    <div v-if="loading" class="admin-files__hint">加载中…</div>
    <el-empty v-else-if="!files.length" description="该子任务没有产出文件" />
    <el-scrollbar v-else max-height="60vh">
      <div v-for="g in groups" :key="g.dirKey" class="admin-files__group">
        <button class="admin-files__group-head" type="button" @click="toggleGroup(g.dirKey)">
          <ElIcon class="admin-files__chev" :class="{ open: !collapsed[g.dirKey] }"><ArrowRight /></ElIcon>
          <span class="admin-files__groupdir">{{ g.label }}</span>
          <span class="admin-files__groupcount">{{ g.files.length }}</span>
        </button>
        <div v-show="!collapsed[g.dirKey]" class="admin-files__group-body">
          <div v-for="f in g.files" :key="f.path" class="admin-files__row">
            <el-checkbox v-model="selection[f.path]" :disabled="!!f.dataFileId" />
            <img v-if="isImageFile(f.name)" :src="downloadUrl(f.path)" :alt="f.name"
                 class="admin-files__thumb" loading="lazy" />
            <span class="admin-files__name">{{ f.name }}</span>
            <span class="admin-files__size">{{ (f.size / 1024).toFixed(1) }} KB</span>
            <el-button link type="primary" @click="emit('preview', f)">预览</el-button>
            <a class="admin-files__dl" :href="downloadUrl(f.path)" target="_blank" rel="noopener">下载</a>
            <span v-if="f.dataFileId" class="admin-files__flag">已导入</span>
          </div>
        </div>
      </div>
    </el-scrollbar>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive } from 'vue'
import { ArrowRight } from '@element-plus/icons-vue'
import { ElCheckbox, ElButton, ElEmpty, ElIcon, ElScrollbar, ElAlert } from 'element-plus'
import { adminChildFileDownloadUrl, type AdminChildFile } from '@/api/aiBatchAdmin'
import { isImageFile } from '@/utils/artifacts'

const props = defineProps<{
  batchId: string
  sessionId: string
  files: AdminChildFile[]
  loading: boolean
  truncated: boolean
  ident?: string
}>()
const emit = defineEmits<{ preview: [AdminChildFile]; import: [string[]] }>()

const DIR_LABELS: Record<string, string> = {
  outputs: 'outputs/（agent 输出）',
  workspace: '根目录 / 子目录',
  uploads: 'uploads/（用户输入）',
}
const collapsed = reactive<Record<string, boolean>>({})
function toggleGroup(k: string) { collapsed[k] = !collapsed[k] }

const groups = computed(() => {
  const m = new Map<string, AdminChildFile[]>()
  for (const f of props.files) {
    if (!m.has(f.dir)) m.set(f.dir, [])
    m.get(f.dir)!.push(f)
  }
  return Array.from(m.entries())
    .sort((a, _b) => (a[0] === 'outputs' ? -1 : 1))
    .map(([dirKey, files]) => ({ dirKey, files, label: DIR_LABELS[dirKey] || dirKey }))
})

const selection = reactive<Record<string, boolean>>({})
const hasUnimported = computed(() => props.files.some(f => !f.dataFileId))
const selectedPaths = computed(() =>
  props.files.filter(f => selection[f.path] && !f.dataFileId).map(f => f.path))
const selectAll = computed({
  get: () => props.files.filter(f => !f.dataFileId).every(f => selection[f.path]),
  set: (v: boolean) => {
    props.files.filter(f => !f.dataFileId).forEach(f => { selection[f.path] = v })
  },
})

function downloadUrl(path: string) {
  return adminChildFileDownloadUrl(props.batchId, props.sessionId, path)
}
function onImport() { emit('import', selectedPaths.value) }
</script>

<style scoped lang="scss">
.admin-files { display: flex; flex-direction: column; gap: 8px; }
.admin-files__bar { display: flex; justify-content: space-between; align-items: center; }
.admin-files__bar-right { display: flex; align-items: center; gap: 12px; }
.admin-files__hint { color: var(--el-text-color-secondary); padding: 12px 0; }
.admin-files__group-head { display: flex; align-items: center; gap: 6px; padding: 6px 0; background: none; border: none; cursor: pointer; }
.admin-files__chev { transition: transform .2s; }
.admin-files__chev.open { transform: rotate(90deg); }
.admin-files__row { display: flex; align-items: center; gap: 8px; padding: 4px 16px; }
.admin-files__flag { color: var(--el-text-color-secondary); font-size: 12px; margin-left: auto; }
.admin-files__dl { color: var(--el-color-primary); text-decoration: none; }
.admin-files__size { color: var(--el-text-color-secondary); font-size: 12px; }
.admin-files__thumb {
  width: auto;
  max-height: 60px;
  max-width: 80px;
  object-fit: contain;
  border-radius: 4px;
  border: 1px solid var(--el-border-color);
}
</style>