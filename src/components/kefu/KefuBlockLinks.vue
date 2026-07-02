<template>
  <div class="kefu-block-links">
    <a v-for="(it, i) in safeItems" :key="i" class="link-card" :href="it.url" target="_blank" rel="noopener noreferrer">
      <span v-if="it.icon" class="ico">{{ it.icon }}</span><span class="lbl">{{ it.label }}</span>
    </a>
  </div>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { isSafeUrl } from './urlSafe'
const props = defineProps<{ config: { items?: { icon?: string; label: string; url: string }[] } }>()
const safeItems = computed(() => (props.config.items || []).filter(it => it.label && isSafeUrl(it.url)))
</script>
