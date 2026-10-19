<template>
  <div class="suggest-list-page">
    <el-card shadow="never" class="suggest-list-card">
      <template #header>
        <div class="suggest-list-header">
          <div>
            <h3 class="suggest-list-title">智能写作</h3>
            <div class="suggest-list-subtitle">
              协作编辑器：停笔自动分析、改写与续写联想；文档与建议自动保存，随时恢复。
            </div>
          </div>
          <div class="suggest-list-actions">
            <el-button v-if="auth.can('admin.suggest_editor')" @click="checklistVisible = true">
              检查要点
            </el-button>
            <el-button v-if="auth.can('admin.suggest_editor')" @click="vocabVisible = true">
              词表管理
            </el-button>
            <el-button v-if="auth.can('admin.suggest_editor')" @click="openTemplateManager">
              模板管理
            </el-button>
            <el-button type="primary" @click="openCreateDialog">新建文档</el-button>
          </div>
        </div>
      </template>

      <el-table v-loading="loading" :data="documents" style="width: 100%">
        <el-table-column prop="title" label="标题" min-width="220">
          <template #default="{ row }">
            <el-link type="primary" @click="openDoc(row.id)">{{ row.title }}</el-link>
          </template>
        </el-table-column>
        <el-table-column label="模板" width="140">
          <template #default="{ row }">
            <span>{{ templateName(row.templateId) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" width="180">
          <template #default="{ row }">{{ formatTime(row.updatedAt) }}</template>
        </el-table-column>
        <el-table-column label="最近打开" width="180">
          <template #default="{ row }">{{ formatTime(row.lastOpenedAt) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openDoc(row.id)">打开</el-button>
            <el-button link type="primary" @click="promptRename(row)">重命名</el-button>
            <el-button link type="danger" @click="confirmDelete(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="还没有文档，点「新建文档」开始写作" />
        </template>
      </el-table>
    </el-card>

    <!-- 新建文档（选模板） -->
    <el-dialog v-model="createVisible" title="新建文档" width="640px">
      <el-form label-width="70px">
        <el-form-item label="标题">
          <el-input v-model="createForm.title" placeholder="未命名文档" maxlength="200" />
        </el-form-item>
        <el-form-item label="模板">
          <div class="template-grid">
            <div
              v-for="tpl in templates"
              :key="tpl.id"
              class="template-card"
              :class="{ 'is-selected': createForm.templateId === tpl.id }"
              @click="createForm.templateId = tpl.id"
            >
              <div class="template-card-name">{{ tpl.name }}</div>
              <div class="template-card-desc">{{ tpl.description || '—' }}</div>
            </div>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- 词表管理（admin）：全局词表对所有文档生效 -->
    <SeTextAssetDialog v-model:visible="vocabVisible" :asset="vocabAsset" />
    <!-- 检查要点（admin）：AI 分析时逐条核对的检查清单，注入分析 prompt -->
    <SeTextAssetDialog v-model:visible="checklistVisible" :asset="checklistAsset" />

    <!-- 模板管理（admin.suggest_editor） -->
    <el-dialog v-model="templateManagerVisible" title="写作模板管理" width="860px">
      <div class="template-manager-bar">
        <el-button type="primary" size="small" @click="openTemplateEdit(null)">新建模板</el-button>
        <span class="template-manager-hint">
          模板正文是 Tiptap HTML（h1/h2/p/ul/ol/taskList/blockquote/codeBlock…），新建文档时原样灌入编辑器。
        </span>
      </div>
      <el-table :data="templates" size="small">
        <el-table-column prop="name" label="名称" width="160" />
        <el-table-column prop="description" label="描述" min-width="200" />
        <el-table-column label="内置" width="80">
          <template #default="{ row }">{{ row.isBuiltin ? '是' : '否' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openTemplateEdit(row)">编辑</el-button>
            <el-button link type="danger" @click="confirmDeleteTemplate(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-dialog v-model="templateEditVisible" :title="templateEditForm.id ? '编辑模板' : '新建模板'" width="680px" append-to-body>
        <el-form label-width="70px">
          <el-form-item label="名称">
            <el-input v-model="templateEditForm.name" maxlength="100" />
          </el-form-item>
          <el-form-item label="描述">
            <el-input v-model="templateEditForm.description" maxlength="200" />
          </el-form-item>
          <el-form-item label="正文 HTML">
            <el-input
              v-model="templateEditForm.contentHtml"
              type="textarea"
              :rows="10"
              placeholder="<h1>标题</h1><p>正文…</p>"
            />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="templateEditVisible = false">取消</el-button>
          <el-button type="primary" :loading="templateSaving" @click="submitTemplateEdit">保存</el-button>
        </template>
      </el-dialog>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * 智能写作 · 文档列表：文档管理 + 从模板新建 + 管理员模板管理。
 */
import { computed, onActivated, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import SeTextAssetDialog, { type SeTextAssetApi } from '@/components/suggest-editor/SeTextAssetDialog.vue'
import {
  createDocument,
  createTemplate,
  deleteDocument,
  deleteTemplate,
  listDocuments,
  listTemplates,
  renameDocument,
  updateTemplate,
  checklistExportUrl,
  getChecklist,
  getVocabulary,
  importChecklist,
  importVocabulary,
  saveChecklist,
  saveVocabulary,
  vocabularyExportUrl,
} from '@/api/suggestEditor'
import type { SuggestDocument, SuggestTemplate } from '@/types/suggestEditor'

const router = useRouter()
const auth = useAuthStore()

const loading = ref(false)
const documents = ref<SuggestDocument[]>([])
const templates = ref<SuggestTemplate[]>([])

const createVisible = ref(false)
const creating = ref(false)
const createForm = ref<{ title: string; templateId: string | null }>({ title: '', templateId: null })

const vocabVisible = ref(false)
const checklistVisible = ref(false)

const VOCAB_HINT = [
  '一行一个词条：<code>词条</code>／<code>词条|别名</code>（输入别名替换成词条）／片段占位符 <code>${1:默认值}</code>；<code>#</code> 开头为注释。',
  '全局词表对所有文档生效，文档词表优先（编辑页「词表」按钮维护）；',
  '保存后任意一次编辑（或重新打开文档）即热生效。',
].join('')

const CHECKLIST_HINT = [
  '每行一条检查要点，AI 分析文档时逐条核对（命中才提建议，reason 注明要点名）；<code>#</code> 开头为注释。',
  '面向技术文档：业务与数据描述、逻辑与流程、一致性与可执行性等均可定义。',
  '生效时机：保存后新分析的段落即按新要点查；已分析段落命中块级缓存不重查，存量全文重查请重新触发编辑。',
].join('')

const vocabAsset = computed<SeTextAssetApi>(() => ({
  title: '全局词表（对所有文档生效）',
  load: async () => (await getVocabulary({ kind: 'global' })).content,
  save: (content) => saveVocabulary({ kind: 'global' }, content),
  importFile: (file, mode) => importVocabulary({ kind: 'global' }, file, mode),
  exportUrl: () => vocabularyExportUrl({ kind: 'global' }),
  hint: VOCAB_HINT,
  placeholder: '综上所述\n人工智能|AI\n主题：${1:议题} 时间：${2:日期}',
}))

const checklistAsset = computed<SeTextAssetApi>(() => ({
  title: '检查要点（AI 分析检查清单）',
  load: async () => (await getChecklist()).content,
  save: saveChecklist,
  importFile: importChecklist,
  exportUrl: checklistExportUrl,
  hint: CHECKLIST_HINT,
  placeholder: '数据口径一致：同一指标的名称、单位、口径是否一致\n因果成立：结论是否由前文推导得出',
}))

const templateManagerVisible = ref(false)
const templateEditVisible = ref(false)
const templateSaving = ref(false)
const templateEditForm = ref<{ id: string | null; name: string; description: string; contentHtml: string }>({
  id: null,
  name: '',
  description: '',
  contentHtml: '',
})

const templateName = computed(() => (templateId: string | null) => {
  if (!templateId) return '—'
  return templates.value.find((tpl) => tpl.id === templateId)?.name ?? '已删除模板'
})

function formatTime(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

async function refresh(): Promise<void> {
  loading.value = true
  try {
    const [docs, tpls] = await Promise.all([listDocuments(), listTemplates()])
    documents.value = docs.documents
    templates.value = tpls.templates
  } finally {
    loading.value = false
  }
}

// keep-alive：回到列表页时刷新（文档在编辑器里改名/新建后能看到最新状态）
onActivated(() => {
  void refresh()
})

void refresh()

function openDoc(docId: string): void {
  void router.push(`/suggest-editor/${docId}`)
}

function openCreateDialog(): void {
  createForm.value = { title: '', templateId: null }
  createVisible.value = true
}

async function submitCreate(): Promise<void> {
  creating.value = true
  try {
    const { document } = await createDocument({
      title: createForm.value.title.trim() || '未命名文档',
      templateId: createForm.value.templateId ?? undefined,
    })
    createVisible.value = false
    void router.push(`/suggest-editor/${document.id}`)
  } finally {
    creating.value = false
  }
}

async function promptRename(doc: SuggestDocument): Promise<void> {
  const res = await ElMessageBox.prompt('新的标题', '重命名文档', {
    inputValue: doc.title,
    inputPattern: /\S/,
    inputErrorMessage: '标题不能为空',
  })
  const value = (res as { value?: string }).value?.trim()
  if (!value) return
  await renameDocument(doc.id, value)
  ElMessage.success('已重命名')
  void refresh()
}

async function confirmDelete(doc: SuggestDocument): Promise<void> {
  await ElMessageBox.confirm(`删除后正文、建议与工作区文件都会清空，且不可恢复。确定删除「${doc.title}」？`, '删除文档', {
    type: 'warning',
    confirmButtonText: '删除',
  })
  await deleteDocument(doc.id)
  ElMessage.success('已删除')
  void refresh()
}

/* ------------------------------- 模板管理 ------------------------------- */

function openTemplateManager(): void {
  templateManagerVisible.value = true
}

function openTemplateEdit(tpl: SuggestTemplate | null): void {
  templateEditForm.value = tpl
    ? { id: tpl.id, name: tpl.name, description: tpl.description ?? '', contentHtml: tpl.contentHtml }
    : { id: null, name: '', description: '', contentHtml: '' }
  templateEditVisible.value = true
}

async function submitTemplateEdit(): Promise<void> {
  const form = templateEditForm.value
  if (!form.name.trim()) {
    ElMessage.warning('模板名称不能为空')
    return
  }
  templateSaving.value = true
  try {
    if (form.id) {
      await updateTemplate(form.id, {
        name: form.name.trim(),
        description: form.description.trim(),
        contentHtml: form.contentHtml,
      })
    } else {
      await createTemplate({
        name: form.name.trim(),
        description: form.description.trim(),
        contentHtml: form.contentHtml,
      })
    }
    templateEditVisible.value = false
    ElMessage.success('已保存')
    const tpls = await listTemplates()
    templates.value = tpls.templates
  } finally {
    templateSaving.value = false
  }
}

async function confirmDeleteTemplate(tpl: SuggestTemplate): Promise<void> {
  await ElMessageBox.confirm(`删除模板「${tpl.name}」？已用它创建的文档不受影响。`, '删除模板', {
    type: 'warning',
  })
  await deleteTemplate(tpl.id)
  ElMessage.success('已删除')
  const tpls = await listTemplates()
  templates.value = tpls.templates
}
</script>

<style scoped lang="scss">
.suggest-list-page {
  height: 100%;
}

.suggest-list-card {
  height: 100%;
  display: flex;
  flex-direction: column;

  :deep(.el-card__body) {
    flex: 1;
    overflow: auto;
  }
}

.suggest-list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.suggest-list-title {
  margin: 0;
  font-size: 16px;
}

.suggest-list-subtitle {
  margin-top: 4px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.suggest-list-actions {
  display: flex;
  gap: 8px;
  flex: none;
}

.template-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
  width: 100%;
}

.template-card {
  padding: 10px 12px;
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  cursor: pointer;
  transition: border-color 0.15s, background-color 0.15s;

  &:hover {
    border-color: var(--el-color-primary-light-5);
  }

  &.is-selected {
    border-color: var(--el-color-primary);
    background-color: var(--el-color-primary-light-9);
  }
}

.template-card-name {
  font-weight: 600;
  font-size: 13px;
}

.template-card-desc {
  margin-top: 4px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.5;
}

.template-manager-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.template-manager-hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
