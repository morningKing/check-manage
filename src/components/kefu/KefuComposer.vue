<!-- src/components/kefu/KefuComposer.vue -->
<template>
  <div class="composer">
    <div v-if="pending.length" class="composer__pending">
      <span v-for="(p, i) in pending" :key="i" class="composer__chip">
        📎 {{ p.name }} <b @click="emit('removePending', i)">✕</b>
      </span>
    </div>
    <div class="composer__row" :class="{ 'is-focused': focused }">
      <button class="composer__attach" type="button" title="上传文件" @click="fileInput?.click()">
        <el-icon><Paperclip /></el-icon>
      </button>
      <input ref="fileInput" type="file" multiple class="composer__file" @change="onFileChange" />
      <el-input
        class="composer__input"
        :model-value="draft"
        type="textarea"
        :autosize="{ minRows: 1, maxRows: 5 }"
        resize="none"
        placeholder="输入你的问题…"
        @update:model-value="emit('update:draft', $event)"
        @keydown.enter="onEnter"
        @focus="focused = true"
        @blur="focused = false" />
      <button class="composer__send" type="button" :disabled="!canSend" title="发送" @click="emit('send')">
        <el-icon><Promotion /></el-icon>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { Paperclip, Promotion } from '@element-plus/icons-vue'

const props = defineProps<{ draft: string; pending: { name: string; path: string }[]; sending: boolean }>()
const emit = defineEmits<{
  (e: 'update:draft', value: string): void
  (e: 'pickFiles', files: File[]): void
  (e: 'removePending', index: number): void
  (e: 'send'): void
}>()

const fileInput = ref<HTMLInputElement | null>(null)
const focused = ref(false)
const canSend = computed(() => (props.draft.trim().length > 0 || props.pending.length > 0) && !props.sending)

function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files && input.files.length) emit('pickFiles', Array.from(input.files))
  input.value = ''
}

function onEnter(e: KeyboardEvent) {
  if (e.isComposing) return          // IME candidate selection — not a submit
  if (e.shiftKey) return             // Shift+Enter = newline
  e.preventDefault()
  if (!canSend.value) return
  emit('send')
}
</script>

<style scoped>
.composer { padding: 12px 16px 16px; }
.composer__pending { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
.composer__chip {
  display: inline-flex; align-items: center; gap: 4px; font-size: 12px;
  background: var(--el-fill-color-light, #f5f7fa); border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-radius: 12px; padding: 2px 8px; color: var(--el-text-color-regular, #606266);
}
.composer__chip b { cursor: pointer; font-weight: normal; color: var(--el-text-color-secondary, #909399); }
.composer__chip b:hover { color: var(--el-color-danger, #f56c6c); }
.composer__row {
  display: flex; align-items: flex-end; gap: 8px;
  border: 1px solid var(--el-border-color, #dcdfe6); border-radius: 14px;
  padding: 6px 6px 6px 10px; background: var(--el-bg-color, #fff);
  transition: border-color .15s ease, box-shadow .15s ease;
}
.composer__row.is-focused {
  border-color: var(--kefu-accent, #4f6ef2);
  box-shadow: 0 0 0 3px var(--kefu-accent-soft, #eef1fe);
}
.composer__file { display: none; }
.composer__attach {
  flex-shrink: 0; width: 34px; height: 34px; border-radius: 9px; cursor: pointer;
  background: none; border: none; color: var(--el-text-color-secondary, #909399);
  display: inline-flex; align-items: center; justify-content: center; font-size: 18px;
}
.composer__attach:hover { background: var(--el-fill-color-light, #f5f7fa); color: var(--kefu-accent, #4f6ef2); }
.composer__input { flex: 1; }
/* el-input textarea: borderless, transparent, no inner shadow — the row is the frame */
.composer__input :deep(.el-textarea__inner) {
  box-shadow: none !important; background: transparent !important; padding: 6px 2px; resize: none;
}
.composer__send {
  flex-shrink: 0; width: 34px; height: 34px; border-radius: 50%; cursor: pointer; border: none;
  background: var(--kefu-accent, #4f6ef2); color: var(--kefu-accent-contrast, #fff);
  display: inline-flex; align-items: center; justify-content: center; font-size: 17px;
  transition: background .15s ease;
}
.composer__send:hover:not(:disabled) { background: var(--kefu-accent-hover, #3f5fe0); }
.composer__send:disabled { background: var(--el-fill-color-dark, #e6e8eb); color: var(--el-text-color-disabled, #c0c4cc); cursor: not-allowed; }
</style>
