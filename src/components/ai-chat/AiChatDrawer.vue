<script setup lang="ts">
import { computed, watch } from 'vue'
import { ElDrawer, ElButton, ElEmpty } from 'element-plus'
import { useAiChatStore } from '@/stores/aiChat'
import MessageList from './MessageList.vue'
import ChatInput from './ChatInput.vue'

const store = useAiChatStore()

const open = computed({
  get: () => store.drawerOpen,
  set: (v: boolean) => store.toggleDrawer(v),
})

const sid = computed(() => store.activeSessionId)
const messages = computed(() => (sid.value ? store.messages[sid.value] ?? [] : []))
const streaming = computed(() => (sid.value ? !!store.streaming[sid.value] : false))

async function onSend(text: string) {
  if (!sid.value) {
    await store.startNewSession()
  }
  await store.sendUserMessage(text)
}

async function startFresh() {
  await store.startNewSession()
}

watch(() => store.drawerOpen, async (v) => {
  // Auto-start a session the first time the drawer is opened, for a friendlier first run
  if (v && !sid.value) {
    try {
      await store.startNewSession()
    } catch {
      // surfaces via per-action error UI later; M1 swallows here so the drawer still opens
    }
  }
})
</script>

<template>
  <ElDrawer
    v-model="open"
    title="AI 助手"
    direction="rtl"
    size="480px"
    :destroy-on-close="false"
  >
    <div class="ai-drawer">
      <div v-if="!sid" class="ai-drawer__empty">
        <ElEmpty description="尚未开启会话">
          <ElButton type="primary" @click="startFresh">开启新会话</ElButton>
        </ElEmpty>
      </div>
      <template v-else>
        <MessageList :messages="messages" />
        <ChatInput :disabled="streaming" @send="onSend" />
      </template>
    </div>
  </ElDrawer>
</template>

<style scoped lang="scss">
.ai-drawer {
  display: flex;
  flex-direction: column;
  height: 100%;
  &__empty { display: flex; align-items: center; justify-content: center; height: 100%; }
}
</style>
