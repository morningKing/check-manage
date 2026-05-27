<script setup lang="ts">
import { computed } from 'vue'
import { ElButton, ElIcon, ElMessage } from 'element-plus'
import { Document, View, CopyDocument, Download } from '@element-plus/icons-vue'
import { artifactFilename, artifactLabel, downloadText } from '@/utils/artifacts'

const props = defineProps<{ lang: string; code: string; index: number }>()
const emit = defineEmits<{ (e: 'preview'): void }>()

const filename = computed(() => artifactFilename(props.lang, props.index))
const label = computed(() => artifactLabel(props.lang, props.index))
const lineCount = computed(() => props.code.split('\n').length)

async function copy() {
  try {
    await navigator.clipboard.writeText(props.code)
    ElMessage.success('已复制')
  } catch {
    ElMessage.error('复制失败')
  }
}
function download() {
  downloadText(filename.value, props.code)
}
</script>

<template>
  <div class="artifact-card" @click="emit('preview')">
    <div class="artifact-card__icon"><ElIcon :size="22"><Document /></ElIcon></div>
    <div class="artifact-card__meta">
      <div class="artifact-card__name">{{ label }}</div>
      <div class="artifact-card__sub">{{ filename }} · {{ lineCount }} 行</div>
    </div>
    <div class="artifact-card__actions" @click.stop>
      <ElButton size="small" text :icon="View" @click="emit('preview')">预览</ElButton>
      <ElButton size="small" text :icon="CopyDocument" @click="copy">复制</ElButton>
      <ElButton size="small" text :icon="Download" @click="download">下载</ElButton>
    </div>
  </div>
</template>

<style scoped lang="scss">
.artifact-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  margin: 10px 0;
  border: 1px solid var(--el-border-color);
  border-radius: 10px;
  background: var(--el-bg-color-page);
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s;
  &:hover { border-color: var(--el-color-primary); box-shadow: 0 1px 6px rgba(0,0,0,0.06); }
  &__icon { color: var(--el-color-primary); display: flex; }
  &__meta { flex: 1; min-width: 0; }
  &__name { font-weight: 600; font-size: 14px; color: var(--el-text-color-primary); }
  &__sub { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 2px; }
  &__actions { display: flex; gap: 2px; flex-shrink: 0; }
}
</style>
