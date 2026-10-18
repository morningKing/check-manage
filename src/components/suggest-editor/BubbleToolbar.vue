<template>
  <BubbleMenu :editor="editor" class="se-bubble-toolbar" :options="{ placement: 'top', offset: 8 }" :should-show="shouldShow">
    <template v-if="linkOpen">
      <span class="se-bubble-link-editor">
        <input
          v-model="linkValue"
          autoFocus
          class="se-bubble-link-input"
          placeholder="https://…"
          @keydown.enter="applyLink"
          @keydown.esc="closeLink"
        />
        <button type="button" class="se-bubble-btn" title="确定" @click="applyLink">确定</button>
        <button
          v-if="editor.isActive('link')"
          type="button"
          class="se-bubble-btn"
          title="移除链接"
          @click="editor.chain().focus().unsetLink().run()"
        >
          移除
        </button>
      </span>
    </template>
    <template v-else>
      <button
        type="button"
        title="加粗 (Ctrl+B)"
        class="se-bubble-btn"
        :class="{ 'is-active': editor.isActive('bold') }"
        @click="editor.chain().focus().toggleBold().run()"
      ><strong>B</strong></button>
      <button
        type="button"
        title="斜体 (Ctrl+I)"
        class="se-bubble-btn"
        :class="{ 'is-active': editor.isActive('italic') }"
        @click="editor.chain().focus().toggleItalic().run()"
      ><em>I</em></button>
      <button
        type="button"
        title="下划线 (Ctrl+U)"
        class="se-bubble-btn"
        :class="{ 'is-active': editor.isActive('underline') }"
        @click="editor.chain().focus().toggleUnderline().run()"
      ><span style="text-decoration: underline">U</span></button>
      <button
        type="button"
        title="删除线"
        class="se-bubble-btn"
        :class="{ 'is-active': editor.isActive('strike') }"
        @click="editor.chain().focus().toggleStrike().run()"
      ><span style="text-decoration: line-through">S</span></button>
      <button
        type="button"
        title="行内代码"
        class="se-bubble-btn"
        :class="{ 'is-active': editor.isActive('code') }"
        @click="editor.chain().focus().toggleCode().run()"
      >&lt;&gt;</button>
      <button
        type="button"
        title="链接 (Ctrl+K)"
        class="se-bubble-btn"
        :class="{ 'is-active': editor.isActive('link') }"
        @click="openLink"
      >🔗</button>

      <span class="se-bubble-sep" />

      <button
        v-for="level in ([1, 2, 3] as const)"
        :key="level"
        type="button"
        :title="`${level} 级标题`"
        class="se-bubble-btn"
        :class="{ 'is-active': editor.isActive('heading', { level }) }"
        @click="editor.chain().focus().toggleHeading({ level }).run()"
      >H{{ level }}</button>

      <span class="se-bubble-sep" />

      <button
        type="button"
        title="无序列表"
        class="se-bubble-btn"
        :class="{ 'is-active': editor.isActive('bulletList') }"
        @click="editor.chain().focus().toggleBulletList().run()"
      >•≡</button>
      <button
        type="button"
        title="有序列表"
        class="se-bubble-btn"
        :class="{ 'is-active': editor.isActive('orderedList') }"
        @click="editor.chain().focus().toggleOrderedList().run()"
      >1≡</button>
      <button
        type="button"
        title="任务列表"
        class="se-bubble-btn"
        :class="{ 'is-active': editor.isActive('taskList') }"
        @click="editor.chain().focus().toggleTaskList().run()"
      >☑</button>
      <button
        type="button"
        title="引用"
        class="se-bubble-btn"
        :class="{ 'is-active': editor.isActive('blockquote') }"
        @click="editor.chain().focus().toggleBlockquote().run()"
      >❝</button>
      <button
        type="button"
        title="代码块"
        class="se-bubble-btn"
        :class="{ 'is-active': editor.isActive('codeBlock') }"
        @click="editor.chain().focus().toggleCodeBlock().run()"
      >{ }</button>

      <span class="se-bubble-sep" />

      <button
        type="button"
        title="清除格式"
        class="se-bubble-btn"
        @click="editor.chain().focus().unsetAllMarks().clearNodes().run()"
      >✕</button>
    </template>
  </BubbleMenu>
</template>

<script setup lang="ts">
/**
 * 选中文字后浮出的格式工具栏（自原型的 BubbleToolbar.tsx 移植到 Vue）：
 * 加粗、斜体、下划线、删除线、行内代码、链接 + 标题 H1-H3 + 列表/任务 + 引用/代码块 + 清除格式。
 * mousedown 不阻止默认也不断选区 —— tiptap 的 BubbleMenu 已处理焦点保持。
 */
import { ref } from 'vue'
import { BubbleMenu } from '@tiptap/vue-3/menus'
import type { Editor } from '@tiptap/core'

const props = defineProps<{ editor: Editor }>()

const linkOpen = ref(false)
const linkValue = ref('')

const shouldShow = ({ editor: current }: { editor: Editor }) => !current.isActive('codeBlock')

function closeLink(): void {
  linkOpen.value = false
  linkValue.value = ''
}

function openLink(): void {
  linkValue.value = (props.editor.getAttributes('link').href as string) ?? ''
  linkOpen.value = true
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
