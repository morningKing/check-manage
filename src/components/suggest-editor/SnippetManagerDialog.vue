<template>
  <el-dialog
    :model-value="visible"
    title="快捷填充模板（/ 菜单）"
    width="860px"
    :close-on-click-modal="false"
    @update:model-value="(v: boolean) => emit('update:visible', v)"
    @open="load"
  >
    <div class="snippet-bar">
      <el-button type="primary" size="small" :disabled="loadFailed" @click="openEdit(null)">
        新建模板
      </el-button>
      <span class="snippet-hint">
        编辑器里输入 <code>/</code> 即列出这些模板；选中后把正文插入光标处，<code>___</code> 占位符会被自动选中，可直接输入替换。
      </span>
    </div>
    <el-alert
      v-if="loadFailed"
      type="error"
      :closable="false"
      show-icon
      title="模板加载失败，暂时不能编辑/保存，避免覆盖服务端已配置的模板"
      class="snippet-alert"
    />

    <el-table v-loading="loading" :data="snippets" size="small" row-key="id">
      <el-table-column prop="name" label="名称" width="150" show-overflow-tooltip />
      <el-table-column prop="description" label="描述" min-width="170" show-overflow-tooltip />
      <el-table-column prop="keywords" label="检索词" width="150" show-overflow-tooltip />
      <el-table-column label="内容预览" min-width="220" show-overflow-tooltip>
        <template #default="{ row }">{{ preview(row.contentHtml) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="120" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" :disabled="loadFailed" @click="openEdit(row)">编辑</el-button>
          <el-button link type="danger" :disabled="loadFailed" @click="confirmRemove(row)">删除</el-button>
        </template>
      </el-table-column>
      <template #empty>
        <el-empty description="还没有快捷填充模板，点「新建模板」添加" :image-size="60" />
      </template>
    </el-table>

    <el-dialog
      v-model="editVisible"
      :title="editForm.id ? '编辑快捷模板' : '新建快捷模板'"
      width="680px"
      append-to-body
    >
      <el-form label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="editForm.name" maxlength="100" placeholder="如：检查逻辑三段式" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input
            v-model="editForm.description"
            maxlength="200"
            placeholder="选模板时显示在名称右侧，一句话说明用途"
          />
        </el-form-item>
        <el-form-item label="检索词">
          <el-input
            v-model="editForm.keywords"
            maxlength="200"
            placeholder="空格分隔的别名/拼音，如：jiance 检查 逻辑"
          />
        </el-form-item>
        <el-form-item label="正文 HTML">
          <el-input
            v-model="editForm.contentHtml"
            type="textarea"
            :rows="10"
            placeholder="<p>检查项：___</p><p>检查方法：___</p><p>检查结论：___</p>"
          />
        </el-form-item>
      </el-form>
      <div class="snippet-edit-hint">
        用 <code>___</code> 标记要填写的位置；支持段落/标题/列表等 Tiptap HTML。
      </div>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>
  </el-dialog>
</template>

<script setup lang="ts">
/**
 * 快捷填充模板管理（admin.suggest_editor）：编辑器 / 菜单里可选的文本模板。
 * 整份保存到服务端全局文件；保存后广播，已打开的编辑器无需刷新即可用新模板。
 */
import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getSnippets, saveSnippets, type TextSnippet } from '@/api/suggestEditor'

defineProps<{ visible: boolean }>()
const emit = defineEmits<{ (e: 'update:visible', v: boolean): void }>()

const loading = ref(false)
const saving = ref(false)
const loadFailed = ref(false)
const snippets = ref<TextSnippet[]>([])

const editVisible = ref(false)
const emptyForm = (): TextSnippet => ({
  id: '',
  name: '',
  description: '',
  keywords: '',
  contentHtml: '',
})
const editForm = ref<TextSnippet>(emptyForm())

async function load(): Promise<void> {
  loading.value = true
  loadFailed.value = false
  try {
    const result = await getSnippets()
    snippets.value = result.snippets
  } catch {
    // 错误提示由请求拦截器统一处理；置位后禁止写入
    loadFailed.value = true
  } finally {
    loading.value = false
  }
}

function preview(contentHtml: string): string {
  return contentHtml
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function openEdit(snippet: TextSnippet | null): void {
  editForm.value = snippet ? { ...snippet } : emptyForm()
  editVisible.value = true
}

function newId(): string {
  return `snip-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`
}

async function persist(next: TextSnippet[]): Promise<void> {
  saving.value = true
  try {
    await saveSnippets(next)
    snippets.value = next
    broadcastChanged()
  } finally {
    saving.value = false
  }
}

async function submitEdit(): Promise<void> {
  if (loadFailed.value) {
    ElMessage.warning('模板加载失败，暂不能保存以避免覆盖服务端模板')
    return
  }
  const form = editForm.value
  if (!form.name.trim()) {
    ElMessage.warning('名称不能为空')
    return
  }
  if (!form.contentHtml.trim()) {
    ElMessage.warning('正文不能为空')
    return
  }
  const record: TextSnippet = {
    id: form.id || newId(),
    name: form.name.trim(),
    description: form.description.trim(),
    keywords: form.keywords.trim(),
    contentHtml: form.contentHtml,
  }
  const next = form.id
    ? snippets.value.map((item) => (item.id === form.id ? record : item))
    : [...snippets.value, record]
  try {
    await persist(next)
    editVisible.value = false
    ElMessage.success('模板已保存，输入 / 即可使用')
  } catch {
    // 错误提示由请求拦截器统一处理
  }
}

async function confirmRemove(snippet: TextSnippet): Promise<void> {
  await ElMessageBox.confirm(`删除快捷模板「${snippet.name}」？编辑器 / 菜单里将不再出现。`, '删除模板', {
    type: 'warning',
  })
  try {
    await persist(snippets.value.filter((item) => item.id !== snippet.id))
    ElMessage.success('已删除')
  } catch {
    // 错误提示由请求拦截器统一处理
  }
}

/** 通知已打开的编辑器重载模板（与规则共用通道，Core 收到后两者都会重载） */
function broadcastChanged(): void {
  try {
    if (typeof BroadcastChannel !== 'undefined') {
      const channel = new BroadcastChannel('suggest-editor-rules')
      channel.postMessage({ type: 'changed' })
      channel.close()
    }
  } catch {
    /* 广播失败不影响保存结果 */
  }
}
</script>

<style scoped lang="scss">
.snippet-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.snippet-hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;

  code {
    padding: 0 3px;
    border-radius: 3px;
    background: var(--el-fill-color);
    font-family: var(--el-font-family-monospace, monospace);
  }
}

.snippet-alert {
  margin-bottom: 10px;
}

.snippet-edit-hint {
  margin-top: -4px;
  color: var(--el-text-color-secondary);
  font-size: 12px;

  code {
    padding: 0 3px;
    border-radius: 3px;
    background: var(--el-fill-color);
    font-family: var(--el-font-family-monospace, monospace);
  }
}
</style>
