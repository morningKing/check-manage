<script setup lang="ts">
import { computed } from 'vue'

export interface PaletteItem { kind: 'builtin' | 'command' | 'skill'; name: string; description: string }
const props = defineProps<{ items: PaletteItem[]; activeIndex: number }>()
defineEmits<{ (e: 'select', item: PaletteItem): void }>()

const groupLabel: Record<PaletteItem['kind'], string> = { builtin: '内置', command: '命令', skill: '技能' }
// Flat list keeps activeIndex simple; insert a group header row whenever the kind changes.
const rows = computed(() => {
  const out: { header?: string; item?: PaletteItem; idx: number }[] = []
  let last = ''
  props.items.forEach((item, idx) => {
    if (item.kind !== last) { out.push({ header: groupLabel[item.kind], idx: -1 }); last = item.kind }
    out.push({ item, idx })
  })
  return out
})
</script>

<template>
  <div v-if="items.length" class="command-palette">
    <template v-for="(row, i) in rows" :key="i">
      <div v-if="row.header" class="palette-group">{{ row.header }}</div>
      <div
        v-else
        class="palette-item" :class="{ active: row.idx === activeIndex }"
        @mousedown.prevent="$emit('select', row.item!)"
      >
        <code class="palette-item__name">/{{ row.item!.name }}</code>
        <span class="palette-item__desc">{{ row.item!.description }}</span>
      </div>
    </template>
  </div>
</template>

<style scoped lang="scss">
.command-palette {
  position: absolute; bottom: 100%; left: 0; right: 0; margin-bottom: 6px;
  max-height: 280px; overflow-y: auto;
  background: var(--el-bg-color); border: 1px solid var(--el-border-color);
  border-radius: 8px; box-shadow: var(--el-box-shadow-light); z-index: 10; padding: 4px;
}
.palette-group { padding: 4px 8px; font-size: 12px; color: var(--el-text-color-secondary); }
.palette-item {
  display: flex; align-items: baseline; gap: 8px; padding: 6px 8px;
  border-radius: 6px; cursor: pointer;
  &.active, &:hover { background: var(--el-fill-color); }
}
.palette-item__name { font-family: var(--el-font-family-mono, monospace); }
.palette-item__desc { font-size: 12px; color: var(--el-text-color-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
