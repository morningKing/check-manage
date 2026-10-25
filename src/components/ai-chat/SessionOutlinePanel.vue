<script setup lang="ts">
/**
 * 会话大纲面板（spec 2026-10-25-session-outline）：列出会话内全部用户输入
 * （后端 /turns 投影 + 父视图本地补尾），点击条目由父视图定位到对应消息。
 * 纯展示组件——数据合并/跳转/scroll-spy 都在 AiChatView。
 */
import { computed } from 'vue'
import type { SessionTurn } from '@/api/aiChat'

const props = defineProps<{
  turns: SessionTurn[]
  /** scroll-spy 跟随高亮的回合 seq（视口顶部所在回合） */
  activeSeq: number | null
  /** 点击定位后的脉冲目标（2.4s 自动清除，与消息流 .msg--located 同步） */
  locatedId: string | null
  /** /turns 加载失败（保留旧列表 + 行内重试） */
  error: boolean
}>()

const emit = defineEmits<{
  (e: 'locate', t: SessionTurn): void
  (e: 'retry'): void
  (e: 'collapse'): void
}>()

function fmtTime(iso?: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  const startOfToday = new Date().setHours(0, 0, 0, 0)
  if (d.getTime() >= startOfToday) return hm
  const md = `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  return `${md} ${hm}`
}

const truncated = computed(() => props.turns.length >= 500)
</script>

<template>
  <div class="outline" data-test="session-outline">
    <div class="outline__head">
      <span class="outline__title">会话大纲</span>
      <span class="outline__count">{{ turns.length }}</span>
      <button class="outline__collapse" type="button" title="收起大纲"
              aria-label="收起大纲" data-test="outline-collapse" @click="emit('collapse')">‹</button>
    </div>
    <div v-if="error" class="outline__error" data-test="outline-error">
      大纲加载失败
      <button type="button" data-test="outline-retry" @click="emit('retry')">重试</button>
    </div>
    <div v-else-if="!turns.length" class="outline__empty">暂无输入</div>
    <div v-else class="outline__list">
      <button
        v-for="t in turns" :key="t.id" type="button" class="outline__item"
        :class="{ 'is-active': t.seq != null && t.seq === activeSeq, 'is-located': t.id === locatedId }"
        data-test="outline-item" :data-turn-id="t.id" :title="t.preview"
        @click="emit('locate', t)"
      >
        <span class="outline__time">{{ fmtTime(t.createdAt) }}</span>
        <span class="outline__preview">{{ t.local ? '（发送中）' : t.preview }}</span>
      </button>
      <div v-if="truncated" class="outline__truncated">仅显示前 500 条</div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.outline { display: flex; flex-direction: column; height: 100%; overflow: hidden; }
.outline__head {
  display: flex; align-items: center; gap: 6px;
  padding: 8px 10px; font-size: 12px; color: var(--el-text-color-secondary);
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.outline__title { font-weight: 600; }
.outline__count { color: var(--el-text-color-placeholder); }
.outline__collapse {
  margin-left: auto; border: none; background: none; cursor: pointer;
  color: var(--el-text-color-secondary); font-size: 14px; line-height: 1;
  &:hover { color: var(--el-color-primary); }
}
.outline__list { overflow-y: auto; flex: 1; }
.outline__item {
  display: block; width: 100%; text-align: left; border: none; background: none;
  padding: 6px 10px; cursor: pointer; border-left: 2px solid transparent;
  &:hover { background: var(--el-fill-color-light); }
  &.is-active { border-left-color: var(--el-color-primary); background: var(--el-fill-color); }
  &.is-located { background: var(--el-color-primary-light-9); }
}
.outline__time { display: block; font-size: 11px; color: var(--el-text-color-placeholder); }
.outline__preview {
  display: block; font-size: 12px; color: var(--el-text-color-primary);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.outline__error, .outline__empty, .outline__truncated {
  padding: 10px; font-size: 12px; color: var(--el-text-color-secondary);
}
.outline__error button { margin-left: 6px; }
</style>
