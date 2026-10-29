<template>
  <aside class="se-suggest-panel">
    <div class="se-suggest-panel-head">
      <span>建议</span>
      <span class="se-suggest-panel-count">{{ total }}</span>
    </div>

    <div v-if="total === 0" class="se-suggest-panel-empty">
      暂无建议。继续写作，停笔后会本地即时检查 + AI 深度分析。
    </div>

    <SuggestionNote
      v-for="item in ai"
      :key="item.id"
      :item="item"
      :active="activeId === item.id"
      @accept="(v) => emit('accept', v)"
      @ignore="(v) => emit('ignore', v)"
      @rerun="(v) => emit('rerun', v)"
      @focus="(v) => emit('focus', v)"
      @hover="(id) => emit('hover', id)"
    />

    <div v-if="local.length > 0" class="se-suggest-panel-sub">即时检查</div>
    <SuggestionNote
      v-for="item in local"
      :key="item.id"
      :item="item"
      :active="activeId === item.id"
      @accept="(v) => emit('accept', v)"
      @ignore="(v) => emit('ignore', v)"
      @focus="(v) => emit('focus', v)"
      @hover="(id) => emit('hover', id)"
    />
  </aside>
</template>

<script setup lang="ts">
/**
 * Word 式右侧批注栏（自原型的 SuggestionSidebar.tsx 移植到 Vue）：
 * 正文只留标注线与编号标记，建议卡片在这里集中呈现；悬停卡片会加重正文高亮。
 */
import { computed } from 'vue'
import SuggestionNote from './SuggestionNote.vue'
import type { SuggestionItem } from './core/suggestions'

const props = defineProps<{
  ai: SuggestionItem[]
  local: SuggestionItem[]
  activeId: string | null
}>()

const emit = defineEmits<{
  (e: 'accept', item: SuggestionItem): void
  (e: 'ignore', item: SuggestionItem): void
  (e: 'rerun', item: SuggestionItem): void
  (e: 'focus', item: SuggestionItem): void
  (e: 'hover', id: string | null): void
}>()

const total = computed(() => props.ai.length + props.local.length)
</script>
