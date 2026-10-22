<template>
  <el-dialog
    :model-value="visible"
    title="导入 Word"
    width="560px"
    :close-on-click-modal="false"
    @update:model-value="(v: boolean) => emit('update:visible', v)"
  >
    <div class="import-body">
      <input type="file" accept=".docx" @change="onFile" />
      <p class="import-hint">
        支持 .docx（≤20MB、≤200 张图、≤2000 块）。标题/段落/列表/表格/图片/基础格式结构化导入；
        字体、颜色、页眉页脚、目录等版式不保证。不支持的图片格式（如 EMF/WMF 公式图）自动跳过，不中断导入。
      </p>
      <p v-if="stage" class="import-stage">{{ stage }}</p>
      <p v-if="error" class="import-error">{{ error }}</p>
    </div>
    <template #footer>
      <el-button @click="emit('update:visible', false)">取消</el-button>
      <el-button type="primary" :loading="busy" :disabled="!file" @click="start">开始导入</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { DocxImportError, parseDocx, replaceImagePlaceholders } from './core/docx-import'
import {
  createDocument,
  deleteDocument,
  setDocumentSeed,
  uploadAttachment,
} from '@/api/suggestEditor'

defineProps<{ visible: boolean }>()
const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void
  (e: 'imported', docId: string): void
}>()

const file = ref<File | null>(null)
const busy = ref(false)
const stage = ref('')
const error = ref('')

function onFile(event: Event): void {
  file.value = (event.target as HTMLInputElement).files?.[0] ?? null
  error.value = ''
}

async function start(): Promise<void> {
  const selected = file.value
  if (!selected) return
  busy.value = true
  error.value = ''
  let createdId = ''
  try {
    stage.value = '解析文档…'
    const parsed = await parseDocx(selected)
    stage.value = '创建文档…'
    const title = selected.name.replace(/\.docx$/i, '').slice(0, 200) || '导入文档'
    const { document } = await createDocument({ title })
    createdId = document.id

    const urlById = new Map<string, string>()
    const warnings = [...parsed.warnings]
    for (let i = 0; i < parsed.images.length; i += 1) {
      stage.value = `上传图片 ${i + 1}/${parsed.images.length}…`
      const image = parsed.images[i]
      try {
        const { attachment } = await uploadAttachment(
          document.id,
          new File([image.blob], image.filename, { type: image.blob.type }),
        )
        urlById.set(image.id, attachment.url)
      } catch {
        // 单张上传失败（格式/超限/网络）不中断导入：占位符会在替换时连同空 figure 一起移除
        warnings.push(`图片 ${image.filename} 上传失败，已跳过`)
      }
    }

    stage.value = '保存内容…'
    await setDocumentSeed(document.id, replaceImagePlaceholders(parsed.html, urlById))
    if (warnings.length) {
      ElMessage.warning(`导入完成，${warnings.length} 条提示（不支持的样式/图片已跳过）`)
    }
    emit('imported', document.id)
    emit('update:visible', false)
  } catch (e) {
    const msg = e instanceof DocxImportError
      ? e.message
      : (e as { response?: { data?: { error?: string } } }).response?.data?.error || '导入失败'
    error.value = msg
    if (createdId) await deleteDocument(createdId).catch(() => {})
  } finally {
    busy.value = false
    stage.value = ''
  }
}
</script>

<style scoped lang="scss">
.import-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.import-hint {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.import-stage {
  margin: 0;
  color: var(--el-color-primary);
  font-size: 13px;
}

.import-error {
  margin: 0;
  color: var(--el-color-danger);
  font-size: 13px;
}
</style>
