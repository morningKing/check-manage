<!--
 * 公告区块 Widget
 *
 * 高亮的通知横幅：标题 + Markdown 正文 + 级别配色（信息/成功/警告/危险）。
 * 可选「可关闭」——关闭状态按 widget id + 正文摘要记在 localStorage，
 * 公告内容被编辑后会重新出现。
 -->
<template>
  <el-card v-if="!dismissed" :class="['announcement-widget', `level-${level}`]" shadow="never">
    <div class="announcement-head">
      <el-icon class="level-icon"><component :is="levelIcon" /></el-icon>
      <span class="announcement-title">{{ content.title || title || '公告' }}</span>
      <el-icon v-if="content.closable" class="close-btn" title="关闭" @click="dismiss">
        <Close />
      </el-icon>
    </div>
    <MarkdownPreview v-if="content.body" class="announcement-body" :text="content.body" />
  </el-card>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { InfoFilled, SuccessFilled, WarningFilled, CircleCloseFilled, Close } from '@element-plus/icons-vue'
import { MarkdownPreview } from '@/components/common'
import type { WidgetContentMap } from '@/types'

const props = defineProps<{
  content: WidgetContentMap['announcement']
  title?: string
  widgetId?: string
}>()

const level = computed(() => props.content.level || 'info')

const levelIcon = computed(() => ({
  info: InfoFilled,
  success: SuccessFilled,
  warning: WarningFilled,
  danger: CircleCloseFilled,
}[level.value] || InfoFilled))

// 关闭状态：按 widget id + 正文摘要做 key，编辑正文后重新出现
const dismissKey = computed(() =>
  `check-manage:announcement-dismissed:${props.widgetId || props.title || ''}:${(props.content.body || '').slice(0, 32)}`)

const dismissed = ref(props.content.closable ? localStorage.getItem(dismissKey.value) === '1' : false)

function dismiss(): void {
  localStorage.setItem(dismissKey.value, '1')
  dismissed.value = true
}
</script>

<style scoped lang="scss">
.announcement-widget {
  border-left: 4px solid var(--el-color-info);
  :deep(.el-card__body) { padding: 14px 18px; }

  &.level-info { border-left-color: var(--el-color-info); .level-icon { color: var(--el-color-info); } }
  &.level-success { border-left-color: var(--el-color-success); .level-icon { color: var(--el-color-success); } }
  &.level-warning { border-left-color: var(--el-color-warning); .level-icon { color: var(--el-color-warning); } }
  &.level-danger { border-left-color: var(--el-color-danger); .level-icon { color: var(--el-color-danger); } }
}

.announcement-head {
  display: flex;
  align-items: center;
  gap: 8px;

  .level-icon { font-size: 18px; flex-shrink: 0; }
  .announcement-title { font-size: 15px; font-weight: 600; color: var(--el-text-color-primary); flex: 1; min-width: 0; }
  .close-btn { cursor: pointer; color: var(--el-text-color-placeholder); flex-shrink: 0; &:hover { color: var(--el-text-color-primary); } }
}

.announcement-body {
  margin-top: 8px;
  font-size: 14px;
}
</style>
