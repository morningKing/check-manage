<template>
  <div class="se-toolbar">
    <div class="se-toolbar-inner">
      <template v-if="linkOpen">
        <input
          v-model="linkValue"
          class="se-bubble-link-input"
          placeholder="https://…"
          @keydown.enter="applyLink"
          @keydown.esc="closeLink"
        />
        <button type="button" class="se-bubble-btn" title="确定" @click="applyLink">确定</button>
        <button
          v-if="isActive('link')"
          type="button"
          class="se-bubble-btn"
          title="移除链接"
          @click="editor.chain().focus().unsetLink().run()"
        >
          移除
        </button>
      </template>
      <template v-else>
        <button
          type="button"
          class="se-bubble-btn"
          title="撤销 (Ctrl+Z)"
          :disabled="!canUndo()"
          @click="editor.chain().focus().undo().run()"
        >↶</button>
        <button
          type="button"
          class="se-bubble-btn"
          title="重做 (Ctrl+Y)"
          :disabled="!canRedo()"
          @click="editor.chain().focus().redo().run()"
        >↷</button>

        <span class="se-bubble-sep" />

        <el-select
          :model-value="blockType"
          size="small"
          class="se-block-select"
          title="段落格式"
          @change="applyBlock"
        >
          <el-option v-for="opt in BLOCK_OPTIONS" :key="opt.value" :label="opt.label" :value="opt.value" />
        </el-select>

        <span class="se-bubble-sep" />

        <button
          type="button"
          title="加粗 (Ctrl+B)"
          class="se-bubble-btn"
          :class="{ 'is-active': isActive('bold') }"
          @mousedown.prevent
          @click="editor.chain().focus().toggleBold().run()"
        ><strong>B</strong></button>
        <button
          type="button"
          title="斜体 (Ctrl+I)"
          class="se-bubble-btn"
          :class="{ 'is-active': isActive('italic') }"
          @mousedown.prevent
          @click="editor.chain().focus().toggleItalic().run()"
        ><em>I</em></button>
        <button
          type="button"
          title="下划线 (Ctrl+U)"
          class="se-bubble-btn"
          :class="{ 'is-active': isActive('underline') }"
          @mousedown.prevent
          @click="editor.chain().focus().toggleUnderline().run()"
        ><span style="text-decoration: underline">U</span></button>
        <button
          type="button"
          title="删除线"
          class="se-bubble-btn"
          :class="{ 'is-active': isActive('strike') }"
          @mousedown.prevent
          @click="editor.chain().focus().toggleStrike().run()"
        ><span style="text-decoration: line-through">S</span></button>
        <button
          type="button"
          title="行内代码"
          class="se-bubble-btn"
          :class="{ 'is-active': isActive('code') }"
          @mousedown.prevent
          @click="editor.chain().focus().toggleCode().run()"
        >&lt;&gt;</button>
        <button
          type="button"
          title="链接 (Ctrl+K)"
          class="se-bubble-btn"
          :class="{ 'is-active': isActive('link') }"
          @mousedown.prevent
          @click="openLink"
        >🔗</button>

        <span class="se-bubble-sep" />

        <button
          type="button"
          title="清除格式"
          class="se-bubble-btn"
          @mousedown.prevent
          @click="editor.chain().focus().unsetAllMarks().clearNodes().run()"
        >✕</button>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 编辑器顶部固定格式工具栏（Word / Google Docs 式）：
 * 与纸张同宽居中，常驻可见——格式操作的唯一入口（原选中浮出工具栏已移除，
 * 避免遮挡正文）。撤销/重做走 Collaboration 扩展注册的 Yjs UndoManager 命令。
 * 所有格式按钮 @mousedown.prevent：点按钮不丢编辑器选区。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { Editor } from '@tiptap/core'

const props = defineProps<{ editor: Editor }>()

const BLOCK_OPTIONS = [
  { value: 'paragraph', label: '正文' },
  { value: 'h1', label: '一级标题' },
  { value: 'h2', label: '二级标题' },
  { value: 'h3', label: '三级标题' },
  { value: 'bulletList', label: '无序列表' },
  { value: 'orderedList', label: '有序列表' },
  { value: 'taskList', label: '任务列表' },
  { value: 'blockquote', label: '引用' },
  { value: 'codeBlock', label: '代码块' },
] as const

type BlockValue = (typeof BLOCK_OPTIONS)[number]['value']

/** editor 实例的 isActive/can 不是响应式的——用事务计数驱动重算 */
const tick = ref(0)
const onTransaction = () => {
  tick.value += 1
}
onMounted(() => {
  props.editor.on('transaction', onTransaction)
})
onBeforeUnmount(() => {
  props.editor.off('transaction', onTransaction)
})

function isActive(name: string, attrs?: Record<string, unknown>): boolean {
  tick.value
  return props.editor.isActive(name, attrs)
}
function canUndo(): boolean {
  tick.value
  return props.editor.can().undo()
}
function canRedo(): boolean {
  tick.value
  return props.editor.can().redo()
}

const blockType = computed<BlockValue>(() => {
  tick.value
  const e = props.editor
  if (e.isActive('heading', { level: 1 })) return 'h1'
  if (e.isActive('heading', { level: 2 })) return 'h2'
  if (e.isActive('heading', { level: 3 })) return 'h3'
  if (e.isActive('bulletList')) return 'bulletList'
  if (e.isActive('orderedList')) return 'orderedList'
  if (e.isActive('taskList')) return 'taskList'
  if (e.isActive('blockquote')) return 'blockquote'
  if (e.isActive('codeBlock')) return 'codeBlock'
  return 'paragraph'
})

function applyBlock(value: BlockValue): void {
  const chain = props.editor.chain().focus()
  switch (value) {
    case 'paragraph': chain.setParagraph().run(); break
    case 'h1': chain.toggleHeading({ level: 1 }).run(); break
    case 'h2': chain.toggleHeading({ level: 2 }).run(); break
    case 'h3': chain.toggleHeading({ level: 3 }).run(); break
    case 'bulletList': chain.toggleBulletList().run(); break
    case 'orderedList': chain.toggleOrderedList().run(); break
    case 'taskList': chain.toggleTaskList().run(); break
    case 'blockquote': chain.toggleBlockquote().run(); break
    case 'codeBlock': chain.toggleCodeBlock().run(); break
  }
}

const linkOpen = ref(false)
const linkValue = ref('')

function openLink(): void {
  linkValue.value = (props.editor.getAttributes('link').href as string) ?? ''
  linkOpen.value = true
}

function closeLink(): void {
  linkOpen.value = false
  linkValue.value = ''
}

function applyLink(): void {
  const href = linkValue.value.trim()
  if (!href) {
    props.editor.chain().focus().extendMarkRange('link').unsetLink().run()
  } else {
    props.editor.chain().focus().extendMarkRange('link').setLink({ href }).run()
  }
  closeLink()
}
</script>
