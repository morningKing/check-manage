<script setup lang="ts">
import { ref, computed } from 'vue'
import { ElIcon } from 'element-plus'
import { Document } from '@element-plus/icons-vue'
import { isImageFile } from '@/utils/artifacts'

const props = defineProps<{ name: string; src: string }>()
const failed = ref(false)
const showImage = computed(() => isImageFile(props.name) && !failed.value)
</script>

<template>
  <a
    v-if="showImage"
    class="chat-file__img" :href="src" target="_blank" rel="noopener noreferrer"
  >
    <img :src="src" :alt="name" @error="failed = true" />
    <span class="chat-file__caption">{{ name }}</span>
  </a>
  <div v-else class="file-chip">
    <ElIcon><Document /></ElIcon><span>{{ name }}</span>
  </div>
</template>

<style scoped lang="scss">
.chat-file__img {
  display: inline-block;
  max-width: 100%;
  margin: 2px 0 6px;
  text-decoration: none;
}
.chat-file__img img {
  display: block;
  max-width: 100%;
  max-height: 360px;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
}
.chat-file__caption {
  display: block;
  margin-top: 2px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
// Scoped copy — AiChatView's .file-chip is shared with .attach-chip there and can't be removed.
.file-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  margin: 2px 4px 6px 0;
  background: var(--el-fill-color-light);
  border-radius: 4px;
  font-size: 13px;
}
</style>
