<template>
  <el-card :class="['quick-form-widget', $attrs.class]" @click="open" shadow="hover">
    <div class="widget-body">
      <el-icon v-if="content.icon" class="widget-icon">
        <component :is="content.icon" />
      </el-icon>
      <el-icon v-else class="widget-icon"><EditPen /></el-icon>
      <div class="widget-text">
        <div class="widget-label">{{ content.buttonLabel || title || '快速录入' }}</div>
        <div v-if="content.description" class="widget-desc">{{ content.description }}</div>
      </div>
      <el-icon class="widget-arrow"><ArrowRight /></el-icon>
    </div>
  </el-card>

  <el-dialog
    v-model="dialogVisible"
    :title="title || content.buttonLabel || '快速录入'"
    width="600px"
    :close-on-click-modal="false"
    destroy-on-close
  >
    <div v-if="loading" v-loading="true" style="height:160px" />
    <div v-else-if="fields.length === 0" class="empty-hint">
      未找到对应数据页配置，请联系管理员。
    </div>
    <DynamicForm
      v-else
      ref="formRef"
      :fields="fields"
      :show-actions="false"
    />
    <template #footer>
      <el-button @click="dialogVisible = false" :disabled="submitting">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submitForm">确定</el-button>
    </template>
  </el-dialog>
</template>

<script lang="ts">
export default { inheritAttrs: false }
</script>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { EditPen, ArrowRight } from '@element-plus/icons-vue'
import { DynamicForm } from '@/components/dynamic-form'
import { usePageConfigStore } from '@/stores/pageConfig'
import type { QuickFormContent } from '@/types/systemConfig'
import type { FieldConfig } from '@/types'

const props = defineProps<{
  content: QuickFormContent
  title?: string
}>()

const pageConfigStore = usePageConfigStore()
const dialogVisible = ref(false)
const loading = ref(false)
const submitting = ref(false)
const formRef = ref<InstanceType<typeof DynamicForm>>()

const pageId = computed(() => `page-${props.content.targetCollection}`)

const fields = computed<FieldConfig[]>(() => {
  const cfg = pageConfigStore.pageConfigs.find(c => c.id === pageId.value)
  if (!cfg) return []
  // 过滤掉自动生成字段，不需要用户填写
  const usable = cfg.fields.filter(f =>
    f.controlType !== 'autoSequence' &&
    f.controlType !== 'autoTimestamp'
  )
  // 若配置了要录入的字段，则只保留这些字段并按配置顺序展示
  const picked = props.content.fields
  if (picked && picked.length) {
    const byName = new Map(usable.map(f => [f.fieldName, f]))
    return picked
      .map(name => byName.get(name))
      .filter((f): f is FieldConfig => Boolean(f))
  }
  return usable
})

async function open() {
  dialogVisible.value = true
  if (pageConfigStore.pageConfigs.length === 0) {
    loading.value = true
    try {
      await pageConfigStore.fetchPageConfigs()
    } finally {
      loading.value = false
    }
  }
}

async function submitForm() {
  if (!formRef.value) return
  const valid = await formRef.value.validate()
  if (!valid) return
  const data = formRef.value.getFormData()
  submitting.value = true
  try {
    // 经由 store 创建，自动生成 id、填充 autoTimestamp/autoSequence/compositeText、处理关联数据
    await pageConfigStore.addPageData(pageId.value, data)
    ElMessage.success('提交成功')
    dialogVisible.value = false
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || '提交失败')
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped lang="scss">
.quick-form-widget {
  cursor: pointer;
  transition: transform 0.15s;
  &:hover { transform: translateY(-2px); }
}

.widget-body {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 4px 0;
}

.widget-icon {
  font-size: 28px;
  color: var(--el-color-primary);
  flex-shrink: 0;
}

.widget-text {
  flex: 1;
  min-width: 0;
}

.widget-label {
  font-size: 15px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.widget-desc {
  margin-top: 3px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.widget-arrow {
  color: var(--el-text-color-placeholder);
  flex-shrink: 0;
}

.empty-hint {
  color: var(--el-text-color-secondary);
  text-align: center;
  padding: 40px 0;
}
</style>
