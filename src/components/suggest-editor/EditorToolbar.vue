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

        <el-dropdown trigger="click" @command="(size: string) => applyFontSize(size)">
          <span class="se-bubble-btn" title="字号">字号</span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="">默认</el-dropdown-item>
              <el-dropdown-item v-for="size in FONT_SIZES" :key="size" :command="size">{{ size }}</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-dropdown trigger="click" @command="(color: string) => applyColor(color)">
          <span class="se-bubble-btn" title="文字颜色">色</span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="">默认</el-dropdown-item>
              <el-dropdown-item v-for="color in TEXT_COLORS" :key="color" :command="color">
                <span class="se-color-dot" :style="{ background: color }" />{{ color }}
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-dropdown trigger="click" @command="(color: string) => applyHighlight(color)">
          <span class="se-bubble-btn" title="高亮">亮</span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="">清除</el-dropdown-item>
              <el-dropdown-item v-for="color in HIGHLIGHT_COLORS" :key="color" :command="color">
                <span class="se-color-dot" :style="{ background: color }" />{{ color }}
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <button v-for="align in ALIGNMENTS" :key="align.value" type="button"
          class="se-bubble-btn" :class="{ 'is-active': editor.isActive({ textAlign: align.value }) }"
          :title="align.label" @click="applyAlign(align.value)">{{ align.icon }}</button>
        <input ref="imageInput" type="file" accept="image/png,image/jpeg,image/gif,image/webp"
          style="display: none" @change="onImageChosen" />
        <button type="button" class="se-bubble-btn" title="插入图片" @click="openImagePicker">图片</button>

        <span class="se-bubble-sep" />

        <!-- 撤销/重做提为独立按钮（原来藏在「⋯」里，发现成本高）；走 Yjs UndoManager 命令 -->
        <button
          type="button"
          class="se-bubble-btn"
          aria-label="撤销 (Ctrl+Z)"
          title="撤销 (Ctrl+Z)"
          :disabled="!canUndo()"
          @mousedown.prevent
          @click="editor.chain().focus().undo().run()"
        >↶</button>
        <button
          type="button"
          class="se-bubble-btn"
          aria-label="重做 (Ctrl+Y)"
          title="重做 (Ctrl+Y)"
          :disabled="!canRedo()"
          @mousedown.prevent
          @click="editor.chain().focus().redo().run()"
        >↷</button>

        <!-- AI 改写：对选中文字弹出与右键相同的改写菜单（Alt+A 同效） -->
        <button
          type="button"
          class="se-bubble-btn se-ai-btn"
          aria-label="AI 改写"
          title="AI 改写选中文字（美化、扩写、精简、纠正语病、翻译…；Alt+A 或右键）"
          :disabled="!hasSelection()"
          @mousedown.prevent
          @click="emit('ai-rewrite')"
        >AI 改写</button>

        <span class="se-bubble-sep" />

        <!-- 低频/上下文操作收进「⋯」（行内代码、链接、清除格式、快捷键、表格行列操作） -->
        <el-dropdown trigger="click" @command="onMoreCommand">
          <span class="se-bubble-btn" title="更多格式与操作">⋯</span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="continuePara">AI 续写一段 (Ctrl+J)</el-dropdown-item>
              <el-dropdown-item command="fullCheck">AI 检查全文</el-dropdown-item>
              <el-dropdown-item command="summary">AI 总结全文…</el-dropdown-item>
              <el-dropdown-item command="code" divided>行内代码</el-dropdown-item>
              <el-dropdown-item command="link">链接…</el-dropdown-item>
              <el-dropdown-item command="clear">清除格式</el-dropdown-item>
              <el-dropdown-item command="spellcheck" divided>
                {{ spellcheckOn ? '拼写检查：开（点击关闭）' : '拼写检查：关（点击开启）' }}
              </el-dropdown-item>
              <el-dropdown-item command="print">打印 / 导出 PDF…</el-dropdown-item>
              <el-dropdown-item command="shortcuts" divided>快捷键…</el-dropdown-item>
              <template v-if="editor.isActive('table')">
                <el-dropdown-item command="rowBefore" divided>上方插入行</el-dropdown-item>
                <el-dropdown-item command="rowAfter">下方插入行</el-dropdown-item>
                <el-dropdown-item command="rowDelete">删除行</el-dropdown-item>
                <el-dropdown-item command="colBefore">左侧插入列</el-dropdown-item>
                <el-dropdown-item command="colAfter">右侧插入列</el-dropdown-item>
                <el-dropdown-item command="colDelete">删除列</el-dropdown-item>
                <el-dropdown-item command="merge">合并单元格</el-dropdown-item>
                <el-dropdown-item command="split">拆分单元格</el-dropdown-item>
                <el-dropdown-item command="headerRow">切换表头行</el-dropdown-item>
                <el-dropdown-item command="tableDelete" divided>删除表格</el-dropdown-item>
              </template>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 编辑器格式工具段（与顶栏合并为单行，Word / Google Docs 式常驻）：
 * 高频格式控件直接可见；撤销/重做是独立图标按钮（Yjs UndoManager 命令）；
 * 「AI 改写」对选中文字发起改写（菜单与右键一致）；低频/上下文操作收进「⋯」。
 * 所有格式按钮 @mousedown.prevent：点按钮不丢编辑器选区。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { Editor } from '@tiptap/core'
import { FONT_SIZES, TEXT_COLORS, HIGHLIGHT_COLORS, clearFormatting } from './core/formatting'
import { TABLE_COMMANDS } from './core/table'
import { insertFigure } from './core/figure'
import { uploadAttachment } from '@/api/suggestEditor'

const props = defineProps<{
  editor: Editor
  /** 当前文档 id（图片上传需要） */
  docId: string
}>()

const emit = defineEmits<{
  (e: 'ai-rewrite'): void
  (e: 'open-shortcuts'): void
  (e: 'print'): void
  (e: 'full-check'): void
  (e: 'summary'): void
}>()

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
  applySpellcheck()
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
function hasSelection(): boolean {
  tick.value
  const { from, to } = props.editor.state.selection
  return from !== to
}

/* ---------------- 拼写检查开关（浏览器 spellcheck；中文文档默认关——红波浪线多为噪声） ---------------- */

const SPELLCHECK_KEY = 'check-manage:se:spellcheck'

function readSpellcheck(): boolean {
  try {
    return localStorage.getItem(SPELLCHECK_KEY) === '1'
  } catch {
    return false
  }
}

const spellcheckOn = ref(readSpellcheck())

function applySpellcheck(): void {
  // contenteditable 的 spellcheck 属性浏览器实时生效，直接改 DOM 即可，无需重建编辑器
  props.editor.view.dom.setAttribute('spellcheck', spellcheckOn.value ? 'true' : 'false')
}

function toggleSpellcheck(): void {
  spellcheckOn.value = !spellcheckOn.value
  try {
    localStorage.setItem(SPELLCHECK_KEY, spellcheckOn.value ? '1' : '0')
  } catch {
    /* localStorage 不可用时仅会话内生效 */
  }
  applySpellcheck()
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

const ALIGNMENTS = [
  { value: 'left', label: '左对齐', icon: '⯇' },
  { value: 'center', label: '居中', icon: '≡' },
  { value: 'right', label: '右对齐', icon: '⯈' },
] as const

function applyFontSize(size: string): void {
  if (!size) props.editor.chain().focus().unsetFontSize().run()
  else props.editor.chain().focus().setFontSize(size).run()
}

function applyColor(color: string): void {
  if (!color) props.editor.chain().focus().unsetColor().run()
  else props.editor.chain().focus().setColor(color).run()
}

function applyHighlight(color: string): void {
  if (!color) props.editor.chain().focus().unsetHighlight().run()
  else props.editor.chain().focus().setHighlight({ color }).run()
}

function applyAlign(align: string): void {
  props.editor.chain().focus().setTextAlign(align).run()
}

/** 「⋯」菜单命令分发（低频 + 表格上下文操作） */
function onMoreCommand(command: string): void {
  const editor = props.editor
  const chain = editor.chain().focus()
  switch (command) {
    case 'continuePara': chain.requestParagraphCompletion().run(); break
    case 'fullCheck': emit('full-check'); break
    case 'summary': emit('summary'); break
    case 'code': chain.toggleCode().run(); break
    case 'link': openLink(); break
    case 'clear': clearFormatting(editor); break
    case 'spellcheck': toggleSpellcheck(); break
    case 'print': emit('print'); break
    case 'shortcuts': emit('open-shortcuts'); break
    case 'rowBefore': TABLE_COMMANDS.addRowBefore(editor); break
    case 'rowAfter': TABLE_COMMANDS.addRowAfter(editor); break
    case 'rowDelete': TABLE_COMMANDS.deleteRow(editor); break
    case 'colBefore': TABLE_COMMANDS.addColumnBefore(editor); break
    case 'colAfter': TABLE_COMMANDS.addColumnAfter(editor); break
    case 'colDelete': TABLE_COMMANDS.deleteColumn(editor); break
    case 'merge': TABLE_COMMANDS.mergeCells(editor); break
    case 'split': TABLE_COMMANDS.splitCell(editor); break
    case 'headerRow': TABLE_COMMANDS.toggleHeaderRow(editor); break
    case 'tableDelete': TABLE_COMMANDS.deleteTable(editor); break
  }
}

const imageInput = ref<HTMLInputElement | null>(null)

function openImagePicker(): void {
  imageInput.value?.click()
}

async function onImageChosen(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  try {
    const { attachment } = await uploadAttachment(props.docId, file)
    insertFigure(props.editor, { src: attachment.url, alt: file.name })
  } catch {
    // 错误提示由请求拦截器统一处理
  }
}

defineExpose({ openImagePicker })
</script>
