<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import MessageItem from './MessageItem.vue'
import type { AiMessage } from '@/api/aiChat'

const props = defineProps<{ messages: AiMessage[] }>()
const scroller = ref<HTMLElement | null>(null)

watch(() => props.messages.length, async () => {
  await nextTick()
  if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
})
</script>

<template>
  <div ref="scroller" class="ai-message-list">
    <MessageItem v-for="m in messages" :key="m.id" :message="m" />
  </div>
</template>

<style scoped lang="scss">
.ai-message-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}
</style>
