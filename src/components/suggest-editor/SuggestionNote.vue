<template>
  <article
    class="se-suggest-note"
    :class="{ 'is-active': active }"
    :data-source="item.source"
    :data-rule-id="item.ruleId ?? ''"
    :data-mechanism="item.mechanism ?? ''"
    :data-severity="item.severity"
    :data-quote="item.quote"
    :data-notice="item.notice ? 'true' : 'false'"
    :data-suggestion-id="item.id"
    @mouseenter="emit('hover', item.id)"
    @mouseleave="emit('hover', null)"
    @click="emit('focus', item)"
  >
    <div class="se-suggest-note-head">
      <span class="se-suggest-note-badge" :class="`se-suggest-note-badge-${item.source}`">
        {{ SOURCE_LABEL[item.source] }}
      </span>
      <span v-if="item.index !== null" class="se-suggest-note-index">{{ item.index }}</span>
      <span class="se-suggest-note-severity" :class="`se-suggest-note-severity-${item.severity}`">
        {{ SEVERITY_LABEL[item.severity] }}
      </span>
    </div>
    <div class="se-suggest-note-quote">“{{ item.quote }}”</div>
    <div v-if="!item.notice" class="se-suggest-note-replacement">{{ item.replacement ? `→ ${item.replacement}` : '→ 删除' }}</div>
    <div class="se-suggest-note-reason">{{ item.reason }}</div>
    <div class="se-suggest-note-actions">
      <button
        v-if="!item.notice"
        type="button"
        class="se-suggest-btn se-suggest-btn-accept"
        @mousedown.prevent
        @click.stop="emit('accept', item)"
      >
        接受
      </button>
      <button
        v-if="item.source === 'rewrite' && item.instruction"
        type="button"
        class="se-suggest-btn"
        title="用同一指令再改写一次，换一种改法"
        @mousedown.prevent
        @click.stop="emit('rerun', item)"
      >
        换一种
      </button>
      <button
        type="button"
        class="se-suggest-btn se-suggest-btn-ignore"
        @mousedown.prevent
        @click.stop="emit('ignore', item)"
      >
        忽略
      </button>
    </div>
  </article>
</template>

<script setup lang="ts">
import type { SuggestionItem } from './core/suggestions'

defineProps<{
  item: SuggestionItem
  active: boolean
}>()

const emit = defineEmits<{
  (e: 'accept', item: SuggestionItem): void
  (e: 'ignore', item: SuggestionItem): void
  (e: 'rerun', item: SuggestionItem): void
  (e: 'focus', item: SuggestionItem): void
  (e: 'hover', id: string | null): void
}>()

const SOURCE_LABEL: Record<SuggestionItem['source'], string> = {
  ai: 'AI',
  rewrite: '改写',
  local: '即时',
}

const SEVERITY_LABEL: Record<SuggestionItem['severity'], string> = {
  info: '提示',
  warn: '建议',
  error: '错误',
}
</script>
