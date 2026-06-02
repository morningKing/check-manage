/**
 * 文件上传控件
 *
 * 职责：
 * - 渲染文件上传组件
 * - 支持多文件上传
 * - 支持文件类型和大小限制
 * - 调用后端 /api/data-files/upload 真实持久化
 */
<template>
  <el-upload
    v-model:file-list="fileList"
    :multiple="true"
    :limit="5"
    :on-exceed="handleExceed"
    :on-success="handleSuccess"
    :on-remove="handleRemove"
    :on-preview="handlePreview"
    :before-upload="beforeUpload"
    :disabled="field.disabled"
    :http-request="uploadToBackend"
  >
    <el-button type="primary" :disabled="field.disabled">
      <el-icon><Upload /></el-icon>
      点击上传
    </el-button>
    <template #tip>
      <div class="el-upload__tip">
        支持上传任意文件，单个文件不超过 10MB，最多上传 5 个文件
      </div>
    </template>
  </el-upload>
</template>

<script setup lang="ts">
/**
 * FileUpload 组件
 *
 * 基于 Element Plus Upload 组件封装
 * 用于动态表单中的文件上传
 *
 * 文件实际经 POST /api/data-files/upload 落到服务器,
 * JSONB 里只存 {uid=data_files.id, name, url, size, type}。
 * url 是 /api/data-files/<id>/download,显示时需附 ?access_token=。
 */
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Upload } from '@element-plus/icons-vue'
import type { UploadFile, UploadRequestOptions } from 'element-plus'
import type { FieldConfig, UploadFile as UploadFileInfo } from '@/types'
import { uploadDataFile, authedDataFileUrl } from '@/api/dataFiles'

// ==================== Props & Emits ====================

interface Props {
  /** 字段配置 */
  field: FieldConfig
  /** 当前值（文件列表） */
  modelValue: UploadFileInfo[] | null
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: UploadFileInfo[]): void
}>()

// ==================== State ====================

/**
 * 文件列表（Element Plus 格式）
 */
const fileList = ref<UploadFile[]>([])

// ==================== 监听 ====================

/**
 * 监听 modelValue 变化，同步文件列表
 */
watch(
  () => props.modelValue,
  (newValue) => {
    if (newValue) {
      fileList.value = newValue.map((file, index) => ({
        uid: index,
        name: file.name,
        url: file.url,
        status: 'success' as const
      })) as UploadFile[]
    } else {
      fileList.value = []
    }
  },
  { immediate: true }
)

// ==================== 方法 ====================

/**
 * 走真后端上传:落到 data_files 表 + 磁盘,
 * 返回的 url 是 /api/data-files/<id>/download(下载时需附 access_token)。
 */
async function uploadToBackend(options: UploadRequestOptions): Promise<void> {
  try {
    const res = await uploadDataFile(options.file as File)
    const uploadedFile: UploadFileInfo = {
      uid: res.id,
      name: res.name,
      url: res.url,
      size: res.size,
      type: res.mimeType,
    }
    const currentFiles = props.modelValue || []
    emit('update:modelValue', [...currentFiles, uploadedFile])
    if (options.onSuccess) options.onSuccess(res)
  } catch (err: any) {
    if (options.onError) options.onError(err)
    ElMessage.error(err?.message || '上传失败')
    throw err
  }
}

/**
 * 点文件名预览/下载:JSONB 里的 url 是裸路径,
 * 拼上 token 后浏览器才能拿到 401 不阻塞的响应。
 */
function handlePreview(file: UploadFile): void {
  if (!file.url) return
  window.open(authedDataFileUrl(file.url), '_blank')
}

/**
 * 上传前验证
 */
function beforeUpload(file: File): boolean {
  const isLt10M = file.size / 1024 / 1024 < 10
  if (!isLt10M) {
    ElMessage.error('上传文件大小不能超过 10MB!')
    return false
  }
  return true
}

/**
 * 文件数量超限处理
 */
function handleExceed(): void {
  ElMessage.warning('最多只能上传 5 个文件')
}

/**
 * 上传成功处理
 */
function handleSuccess(): void {
  ElMessage.success('上传成功')
}

/**
 * 文件移除处理
 */
function handleRemove(file: UploadFile): void {
  const currentFiles = props.modelValue || []
  const updatedFiles = currentFiles.filter((f) => f.uid !== String(file.uid))
  emit('update:modelValue', updatedFiles)
}
</script>

<style scoped lang="scss">
.el-upload__tip {
  color: #909399;
  font-size: 12px;
  margin-top: 8px;
}
</style>
