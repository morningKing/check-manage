<template>
  <KefuSelfServicePanel :items="limited" @click="(id:string)=>emit('click',id)" @escalate="(q:string)=>emit('escalate',q)" />
</template>
<script setup lang="ts">
import { computed } from 'vue'
import KefuSelfServicePanel from './KefuSelfServicePanel.vue'
import type { KefuFaqItem } from '@/api/kefuPublic'
const props = defineProps<{ items: KefuFaqItem[]; config: { limit?: number } }>()
const emit = defineEmits<{ (e: 'click', id: string): void; (e: 'escalate', q: string): void }>()
const limited = computed(() => props.items.slice(0, props.config.limit || 5))
</script>
