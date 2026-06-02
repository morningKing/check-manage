/**
 * 图片上传控件
 *
 * 职责：
 * - 渲染图片上传组件
 * - 支持图片预览
 * - 支持多图上传
 * - Mock 模式下模拟上传
 */
<template>
  <el-upload
    v-model:file-list="fileList"
    list-type="picture-card"
    :multiple="true"
    :limit="9"
    :on-exceed="handleExceed"
    :on-preview="handlePreview"
    :on-remove="handleRemove"
    :before-upload="beforeUpload"
    :disabled="field.disabled"
    :http-request="uploadToBackend"
    accept="image/*"
  >
    <el-icon><Plus /></el-icon>
    <template #file="{ file }">
      <!-- ElUpload's default thumbnail uses file.url which is bare;
           inject the auth token so the <img> doesn't 401. -->
      <div class="el-upload-list__item-thumbnail">
        <img :src="authedDataFileUrl(file.url || '')" alt="" />
      </div>
    </template>
  </el-upload>

  <!-- 图片预览对话框 -->
  <el-dialog v-model="previewVisible" title="图片预览" width="600">
    <img :src="previewUrl" alt="预览图片" style="width: 100%" />
  </el-dialog>
</template>

<script setup lang="ts">
/**
 * ImageUpload 组件
 *
 * 基于 Element Plus Upload 组件的图片模式封装
 * 用于动态表单中的图片上传
 *
 * 特性：
 * - 图片卡片展示
 * - 点击预览大图
 * - 支持拖拽排序
 */
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import type { UploadFile, UploadRequestOptions } from 'element-plus'
import type { FieldConfig, UploadFile as UploadFileInfo } from '@/types'
import { uploadDataFile, authedDataFileUrl } from '@/api/dataFiles'

// ==================== Props & Emits ====================

interface Props {
  /** 字段配置 */
  field: FieldConfig
  /** 当前值（图片列表） */
  modelValue: UploadFileInfo[] | null
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: UploadFileInfo[]): void
}>()

// ==================== State ====================

/**
 * 文件列表
 */
const fileList = ref<UploadFile[]>([])

/**
 * 预览对话框可见性
 */
const previewVisible = ref(false)

/**
 * 预览图片URL
 */
const previewUrl = ref('')

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
 * 走真后端上传:落到 data_files 表 + 磁盘,JSONB 存 {uid, name, url, size, type}。
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
 * 上传前验证
 */
function beforeUpload(file: File): boolean {
  const isImage = file.type.startsWith('image/')
  if (!isImage) {
    ElMessage.error('只能上传图片文件!')
    return false
  }

  const isLt5M = file.size / 1024 / 1024 < 5
  if (!isLt5M) {
    ElMessage.error('图片大小不能超过 5MB!')
    return false
  }

  return true
}

/**
 * 文件数量超限处理
 */
function handleExceed(): void {
  ElMessage.warning('最多只能上传 9 张图片')
}

/**
 * 图片预览处理
 */
function handlePreview(file: UploadFile): void {
  previewUrl.value = authedDataFileUrl(file.url || '')
  previewVisible.value = true
}

/**
 * 图片移除处理
 */
function handleRemove(file: UploadFile): void {
  const currentFiles = props.modelValue || []
  const updatedFiles = currentFiles.filter((f) => f.uid !== String(file.uid))
  emit('update:modelValue', updatedFiles)
}
</script>

<style scoped lang="scss">
:deep(.el-upload--picture-card) {
  width: 100px;
  height: 100px;
}

:deep(.el-upload-list--picture-card .el-upload-list__item) {
  width: 100px;
  height: 100px;
}
</style>
