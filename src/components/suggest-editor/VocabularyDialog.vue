<template>
  <el-dialog
    :model-value="visible"
    :title="title"
    width="720px"
    :close-on-click-modal="false"
    @update:model-value="(v: boolean) => emit('update:visible', v)"
    @open="onOpen"
  >
    <el-input
      v-model="content"
      v-loading="loading"
      type="textarea"
      :rows="14"
      class="vocab-textarea"
      :placeholder="PLACEHOLDER"
    />
    <div class="vocab-hint">
      一行一个词条：<code>词条</code>／<code>词条|别名</code>（输入别名替换成词条）／
      片段占位符 <code>${'{'}1:默认值{'}'}</code>；<code>#</code> 开头为注释。
      全局词表对所有文档生效，文档词表优先；保存后任意一次编辑（或重新打开文档）即生效。
    </div>

    <div class="vocab-actions">
      <el-radio-group v-model="importMode" size="small">
        <el-radio-button value="append">追加（去重）</el-radio-button>
        <el-radio-button value="replace">覆盖</el-radio-button>
      </el-radio-group>
      <input
        ref="fileInput"
        type="file"
        accept=".txt,text/plain"
        style="display: none"
        @change="onFileChosen"
      />
      <el-button size="small" @click="fileInput?.click()">导入文件…</el-button>
      <a :href="vocabularyExportUrl(scope)" data-testid="vocab-export">
        <el-button size="small">导出</el-button>
      </a>
      <span class="vocab-spacer" />
      <el-button size="small" @click="emit('update:visible', false)">关闭</el-button>
      <el-button type="primary" size="small" :loading="saving" @click="onSave">保存</el-button>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
/**
 * 词表管理对话框（全局 / 文档级两用）：
 * 后台配置（直接编辑词条文本）+ 导入文件（追加去重 / 覆盖）+ 导出下载。
 * 词表真源是磁盘文件（Node 侧热同步），这里只是它的管理面。
 */
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getVocabulary,
  importVocabulary,
  saveVocabulary,
  vocabularyExportUrl,
  type VocabularyScope,
} from '@/api/suggestEditor'

const props = defineProps<{
  visible: boolean
  scope: VocabularyScope
}>()

const emit = defineEmits<{ (e: 'update:visible', v: boolean): void }>()

const PLACEHOLDER =
  '综上所述\n人工智能|AI\n主题：${1:议题} 时间：${2:日期}\n# 以 # 开头的行是注释'

const title = computed(() =>
  props.scope.kind === 'global' ? '全局词表（对所有文档生效）' : '本文档词表',
)

const content = ref('')
const loading = ref(false)
const saving = ref(false)
const importMode = ref<'append' | 'replace'>('append')
const fileInput = ref<HTMLInputElement | null>(null)

async function onOpen(): Promise<void> {
  loading.value = true
  try {
    const { content: text } = await getVocabulary(props.scope)
    content.value = text
  } catch {
    ElMessage.error('词表读取失败')
  } finally {
    loading.value = false
  }
}

async function onSave(): Promise<void> {
  saving.value = true
  try {
    const { total } = await saveVocabulary(props.scope, content.value)
    ElMessage.success(`词表已保存（${total} 个词条）`)
  } finally {
    saving.value = false
  }
}

async function onFileChosen(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = '' // 允许连续选同一个文件
  if (!file) return
  try {
    const r = await importVocabulary(props.scope, file, importMode.value)
    ElMessage.success(`导入完成：新增 ${r.added}，跳过 ${r.skipped}，共 ${r.total} 条`)
    const fresh = await getVocabulary(props.scope)
    content.value = fresh.content
  } finally {
    input.value = ''
  }
}
</script>

<style scoped lang="scss">
.vocab-textarea :deep(.el-textarea__inner) {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12.5px;
  line-height: 1.7;
}

.vocab-hint {
  margin-top: 8px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.7;

  code {
    padding: 0 4px;
    background: var(--el-fill-color-light);
    border-radius: 3px;
  }
}

.vocab-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
}

.vocab-spacer {
  flex: 1;
}
</style>
