<script setup lang="ts">
import { ref } from 'vue'
import { ElButton, ElInput } from 'element-plus'

const props = defineProps<{ disabled?: boolean }>()
const emit = defineEmits<{ (e: 'send', text: string): void }>()

const text = ref('')

function send() {
  const t = text.value.trim()
  if (!t || props.disabled) return
  emit('send', t)
  text.value = ''
}

function onKey(e: Event) {
  const ev = e as KeyboardEvent
  if (ev.key === 'Enter' && !ev.shiftKey) {
    ev.preventDefault()
    send()
  }
}
</script>

<template>
  <div class="ai-chat-input">
    <ElInput
      v-model="text"
      type="textarea"
      :rows="3"
      :disabled="disabled"
      placeholder="询问 Agent (Enter 发送, Shift+Enter 换行)"
      @keydown="onKey"
    />
    <div class="ai-chat-input__bar">
      <ElButton type="primary" :disabled="disabled || !text.trim()" @click="send">
        发送
      </ElButton>
    </div>
  </div>
</template>

<style scoped lang="scss">
.ai-chat-input {
  padding: 8px;
  border-top: 1px solid var(--el-border-color-light);
  &__bar { display: flex; justify-content: flex-end; margin-top: 6px; }
}
</style>
