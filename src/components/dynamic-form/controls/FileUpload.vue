/**
 * 文件上传控件
 *
 * 职责：
 * - 渲染文件上传组件
 * - 支持多文件上传
 * - 支持文件类型和大小限制
 * - Mock 模式下模拟上传
 */
<template>
  <el-upload
    v-model:file-list="fileList"
    :action="uploadAction"
    :multiple="true"
    :limit="5"
    :on-exceed="handleExceed"
    :on-success="handleSuccess"
    :on-remove="handleRemove"
    :before-upload="beforeUpload"
    :disabled="field.disabled"
    :http-request="mockUpload"
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
 * Mock 模式：
 * - 由于没有真实后端，使用自定义上传方法模拟
 * - 文件转换为 Base64 或 ObjectURL 存储
 */
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Upload } from '@element-plus/icons-vue'
import type { UploadFile, UploadRequestOptions } from 'element-plus'
import type { FieldConfig, UploadFile as UploadFileInfo } from '@/types'
import { v4 as uuidv4 } from 'uuid'

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
 * 上传地址（Mock 模式不使用）
 */
const uploadAction = ref('/api/upload')

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
 * Mock 上传方法
 *
 * 将文件转换为 ObjectURL 模拟上传成功
 */
function mockUpload(options: UploadRequestOptions): Promise<void> {
  return new Promise((resolve) => {
    const file = options.file
    const url = URL.createObjectURL(file)

    // 模拟上传延迟
    setTimeout(() => {
      const uploadedFile: UploadFileInfo = {
        uid: uuidv4(),
        name: file.name,
        url: url,
        size: file.size,
        type: file.type
      }

      const currentFiles = props.modelValue || []
      emit('update:modelValue', [...currentFiles, uploadedFile])

      if (options.onSuccess) {
        options.onSuccess({ url })
      }
      resolve()
    }, 500)
  })
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
