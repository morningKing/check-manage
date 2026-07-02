<template>
  <div class="kefu-service-column">
    <section v-for="b in visibleBlocks" :key="b.id" class="svc-block">
      <h3 class="svc-title">{{ b.title || defaultTitle(b.type) }}</h3>
      <KefuBlockLinks v-if="b.type==='links'" :config="b.config" />
      <KefuBlockFaq v-else-if="b.type==='faq'" :items="faqItems" :config="b.config"
        @click="(id:string)=>emit('faqClick',id)" @escalate="(q:string)=>emit('escalate',q)" />
      <KefuBlockRichtext v-else-if="b.type==='richtext'" :config="b.config" />
      <KefuBlockContact v-else-if="b.type==='contact'" :config="b.config" />
    </section>
  </div>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import KefuBlockLinks from './KefuBlockLinks.vue'
import KefuBlockFaq from './KefuBlockFaq.vue'
import KefuBlockRichtext from './KefuBlockRichtext.vue'
import KefuBlockContact from './KefuBlockContact.vue'
import type { PanelBlock, KefuFaqItem } from '@/api/kefuPublic'

const props = defineProps<{ blocks: PanelBlock[]; faqItems: KefuFaqItem[] }>()
const emit = defineEmits<{ (e: 'faqClick', id: string): void; (e: 'escalate', q: string): void }>()

const visibleBlocks = computed(() => (props.blocks || []).filter(b => b.enabled !== false))

function defaultTitle(t: string) {
  return ({ links: '快捷入口', faq: '热点问题', richtext: '公告', contact: '联系我们' } as any)[t] || ''
}
</script>
<style scoped>
.kefu-service-column { display: flex; flex-direction: column; gap: 12px; }
.svc-block {
  background: var(--el-bg-color, #fff); border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-radius: 12px; padding: 14px 16px;
}
.svc-title {
  margin: 0 0 10px; font-size: 13px; font-weight: 600;
  color: var(--el-text-color-secondary, #909399);
}
</style>
