<template>
  <el-dialog
    :model-value="visible"
    :title="asset.title"
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
      :placeholder="asset.placeholder"
    />
    <div class="vocab-hint" v-html="asset.hint" />

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
      <a :href="asset.exportUrl()" data-testid="asset-export">
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
 * 通用「文本资产」管理对话框：编辑 + 导入文件（追加去重/覆盖）+ 导出下载。
 * 词表（全局/文档级）与检查要点共用——真源都是服务端文件，这里只是管理面。
 */
import { ref } from 'vue'
import { ElMessage } from 'element-plus'

export interface SeTextAssetApi {
  title: string
  load: () => Promise<string>
  save: (content: string) => Promise<{ total: number }>
  importFile: (file: File, mode: 'append' | 'replace') => Promise<{
    total: number
    added: number
    skipped: number
  }>
  exportUrl: () => string
  /** 格式与生效时机说明（可含 <code> 等内联标签） */
  hint: string
  placeholder?: string
}

const props = defineProps<{
  visible: boolean
  asset: SeTextAssetApi
}>()

const emit = defineEmits<{ (e: 'update:visible', v: boolean): void }>()

const content = ref('')
const loading = ref(false)
const saving = ref(false)
const importMode = ref<'append' | 'replace'>('append')
const fileInput = ref<HTMLInputElement | null>(null)

async function onOpen(): Promise<void> {
  loading.value = true
  try {
    content.value = await props.asset.load()
  } catch {
    ElMessage.error(`${props.asset.title}读取失败`)
  } finally {
    loading.value = false
  }
}

async function onSave(): Promise<void> {
  saving.value = true
  try {
    const { total } = await props.asset.save(content.value)
    ElMessage.success(`${props.asset.title}已保存（${total} 条）`)
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
    const r = await props.asset.importFile(file, importMode.value)
    ElMessage.success(`导入完成：新增 ${r.added}，跳过 ${r.skipped}，共 ${r.total} 条`)
    content.value = await props.asset.load()
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

  :deep(code) {
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
