<script setup lang="ts">
/**
 * Renders a user message's text, turning `@<workspace-file-path>` references
 * into clickable chips that open the file preview.
 *
 * Messages without a file mention render exactly like before (full Markdown);
 * when a file chip is present we lay the text out inline (pre-wrapped) so the
 * pill sits on the same line as the surrounding sentence.
 */
import { computed } from 'vue'
import { ElIcon } from 'element-plus'
import { Document } from '@element-plus/icons-vue'
import { splitMentionSegments, type AiFileLike } from '@/utils/fileMentions'
import MarkdownView from '@/components/ai-chat/MarkdownView.vue'

const props = defineProps<{ text: string; files: AiFileLike[] }>()
const emit = defineEmits<{ (e: 'preview', path: string): void }>()

const knownPaths = computed(() => new Set(props.files.map((f) => f.path)))
const segments = computed(() => splitMentionSegments(props.text || '', knownPaths.value))
// Only switch to the inline (chip) layout when at least one reference resolves
// to a real file — plain/markdown messages keep the original MarkdownView path.
const hasFileChip = computed(() => segments.value.some((s) => s.type === 'file'))
</script>

<template>
  <MarkdownView v-if="!hasFileChip" :text="text" />
  <div v-else class="user-mention-text">
    <template v-for="(seg, i) in segments" :key="i">
      <span v-if="seg.type === 'text'" class="user-mention-text__seg">{{ seg.text }}</span>
      <button
        v-else
        type="button"
        class="file-chip"
        :title="'预览 ' + seg.path"
        :aria-label="'预览文件 ' + seg.path"
        @click="emit('preview', seg.path)"
      >
        <ElIcon class="file-chip__icon"><Document /></ElIcon>
        <span class="file-chip__path">@{{ seg.path }}</span>
      </button>
    </template>
  </div>
</template>

<style scoped>
.user-mention-text { white-space: pre-wrap; word-break: break-word; line-height: 1.7; }
.user-mention-text__seg { white-space: pre-wrap; }
.file-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 100%;
  margin: 0 2px;
  padding: 1px 8px;
  border: 1px solid var(--el-color-primary-light-5);
  border-radius: 999px;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  font-family: var(--el-font-family-mono, monospace);
  font-size: 13px;
  line-height: 1.6;
  vertical-align: baseline;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
}
.file-chip:hover {
  background: var(--el-color-primary-light-8);
  border-color: var(--el-color-primary);
}
.file-chip__icon { font-size: 13px; flex-shrink: 0; }
.file-chip__path { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
