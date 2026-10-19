<template>
  <div class="suggest-doc-page">
    <div v-if="loadError" class="suggest-doc-error">
      <el-empty :description="loadError">
        <el-button type="primary" @click="router.push('/suggest-editor')">返回文档列表</el-button>
      </el-empty>
    </div>
    <SuggestEditorCore
      v-else-if="doc"
      :key="doc.id"
      ref="coreRef"
      :doc-id="doc.id"
      :token="auth.token"
      @synced="onSynced"
    >
      <template #title>
        <el-button link @click="router.push('/suggest-editor')">
          <el-icon><ArrowLeft /></el-icon>
        </el-button>
        <el-input
          v-model="titleDraft"
          class="se-doc-title-input"
          size="small"
          maxlength="200"
          @change="commitTitle"
        />
      </template>
      <template #actions>
        <el-button size="small" @click="rulesVisible = true">规则</el-button>
        <el-button size="small" @click="vocabVisible = true">词表</el-button>
        <el-button
          v-if="auth.can('admin.suggest_editor')"
          size="small"
          :loading="savingTemplate"
          @click="saveAsTemplate"
        >
          存为模板
        </el-button>
      </template>
    </SuggestEditorCore>
    <div v-else class="suggest-doc-loading" v-loading="true" element-loading-text="加载文档…" />
    <SeTextAssetDialog v-model:visible="vocabVisible" :asset="docVocabAsset" />
    <!-- 检查规则（本文档属主）：仅本文档生效，保存后重载本地规则 -->
    <RuleManagerDialog
      v-model:visible="rulesVisible"
      :scope="doc ? { kind: 'document', docId: doc.id } : { kind: 'global' }"
      @saved="coreRef?.reloadRules()"
    />
  </div>
</template>

<script setup lang="ts">
/**
 * 智能写作 · 文档编辑页：
 * - 挂载即调 open 心跳（最近打开排序）；
 * - synced 后若正文为空且文档选了模板 → 一次性 setContent 灌入模板（模板初始化在客户端做，
 *   服务端无需引入 HTML→Yjs 转换）；
 * - 标题改动 blur/回车时 PATCH 保存；管理员可把当前文档另存为模板。
 */
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import SuggestEditorCore from '@/components/suggest-editor/SuggestEditorCore.vue'
import SeTextAssetDialog, { type SeTextAssetApi } from '@/components/suggest-editor/SeTextAssetDialog.vue'
import RuleManagerDialog from '@/components/suggest-editor/RuleManagerDialog.vue'
import * as Y from 'yjs'
import type { Editor } from '@tiptap/core'
import { computed } from 'vue'
import { useAuthStore } from '@/stores/auth'
import {
  createTemplate,
  getDocument,
  getTemplateById,
  getVocabulary,
  importVocabulary,
  openDocument,
  renameDocument,
  saveVocabulary,
  vocabularyExportUrl,
} from '@/api/suggestEditor'
import type { SuggestDocument } from '@/types/suggestEditor'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const doc = ref<SuggestDocument | null>(null)
const loadError = ref('')
const titleDraft = ref('')
const coreRef = ref<InstanceType<typeof SuggestEditorCore> | null>(null)
const savingTemplate = ref(false)
const vocabVisible = ref(false)
const rulesVisible = ref(false)

/** 本文档词表（优先于全局词表；属主维护） */
const docVocabAsset = computed<SeTextAssetApi>(() => ({
  title: '本文档词表（优先于全局词表）',
  load: async () => (await getVocabulary({ kind: 'document', docId: docId })).content,
  save: (content) => saveVocabulary({ kind: 'document', docId }, content),
  importFile: (file, mode) => importVocabulary({ kind: 'document', docId: docId }, file, mode),
  exportUrl: () => vocabularyExportUrl({ kind: 'document', docId: docId }),
  hint: [
    '一行一个词条：<code>词条</code>／<code>词条|别名</code>／片段占位符 <code>${1:默认值}</code>；<code>#</code> 开头为注释。',
    '只对本文档生效；保存后任意一次编辑（或重新打开文档）即热生效。',
  ].join(''),
  placeholder: '综上所述\n人工智能|AI',
}))

/** 模板只灌一次（多客户端同开时先到者写，其余看到 Yjs 同步来的内容自然跳过） */
let templateApplied = false

const docId = String(route.params.docId ?? '')

onMounted(async () => {
  try {
    const { document } = await getDocument(docId)
    doc.value = document
    titleDraft.value = document.title
    await openDocument(docId)
  } catch {
    loadError.value = '文档不存在或没有权限访问'
  }
})

async function onSynced(): Promise<void> {
  if (templateApplied || !doc.value?.templateId) return
  const core = coreRef.value
  const ydoc: Y.Doc | undefined = core?.ydoc
  const editor: Editor | undefined = core?.editor
  if (!ydoc || !editor) return
  // 只在「确实空」时灌模板（比如刷新页面恢复的旧文档虽带 templateId 也不该重灌）
  if (ydoc.getXmlFragment('default').length > 0) {
    templateApplied = true
    return
  }
  try {
    const tpl = await getTemplateById(doc.value.templateId)
    if (tpl && tpl.contentHtml) {
      editor.commands.setContent(tpl.contentHtml)
    }
  } catch {
    /* 模板取不到（可能已被删除）：按空白文档继续 */
  } finally {
    templateApplied = true
  }
}

async function commitTitle(): Promise<void> {
  const title = titleDraft.value.trim()
  if (!doc.value || !title || title === doc.value.title) return
  const { document } = await renameDocument(doc.value.id, title)
  doc.value = document
  titleDraft.value = document.title
  ElMessage.success('标题已保存')
}

async function saveAsTemplate(): Promise<void> {
  const editor: Editor | undefined = coreRef.value?.editor
  if (!editor) return
  const res = await ElMessageBox.prompt('模板名称', '把当前文档存为模板', {
    inputValue: doc.value?.title ?? '',
    inputPattern: /\S/,
    inputErrorMessage: '名称不能为空',
  })
  const name = (res as { value?: string }).value?.trim()
  if (!name) return
  savingTemplate.value = true
  try {
    await createTemplate({
      name,
      description: `来自文档「${doc.value?.title ?? ''}」`,
      contentHtml: editor.getHTML(),
    })
    ElMessage.success('模板已保存，可在「新建文档」时选用')
  } finally {
    savingTemplate.value = false
  }
}
</script>

<style scoped lang="scss">
.suggest-doc-page {
  /* 精确填满内容区（标签栏高度变化时不再出现外层滚动把头部挤走）；
     正文滚动只发生在编辑器内部（.se-editor-host） */
  height: 100%;
  min-height: 0;
  background: #fff;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  overflow: hidden;
}

.suggest-doc-error,
.suggest-doc-loading {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

:deep(.se-doc-title-input) {
  width: 260px;

  .el-input__wrapper {
    box-shadow: none;
    background: transparent;

    .el-input__inner {
      font-weight: 600;
    }
  }
}
</style>
