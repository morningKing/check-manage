<template>
  <ElDialog
    :model-value="modelValue"
    @update:model-value="$emit('update:modelValue', $event)"
    title="新建批任务" width="640px"
  >
    <div class="batch-create">
      <div class="row">
        <label>批任务名</label>
        <ElInput v-model="name" data-test="name" placeholder="给这批任务起个名" />
      </div>

      <div class="row">
        <label>模板</label>
        <div class="row__inline">
          <ElSelect v-model="selectedTemplateId"
                    placeholder="可选: 从已保存模板填充"
                    clearable
                    @change="onPickTemplate"
                    @visible-change="(v: boolean) => v && loadTemplates()">
            <ElOption v-for="t in templates" :key="t.id" :label="t.name" :value="t.id" />
          </ElSelect>
          <ElButton link @click="handleManageTemplates">管理模板</ElButton>
        </div>
      </div>

      <div class="row">
        <label>Prompt</label>
        <ElInput v-model="prompt" type="textarea" :rows="6"
                 data-test="prompt"
                 placeholder="例如: 根据上传的指导书开发巡检用例…" />
      </div>

      <div class="row">
        <label>Agent <span style="color:var(--el-text-color-placeholder);font-size:11px">（可选）</span></label>
        <ElSelect v-model="selectedAgent" placeholder="使用 OpenCode 默认 Agent" clearable>
          <ElOption v-for="a in agents" :key="a.name" :label="a.name" :value="a.name">
            <span>{{ a.name }}</span>
            <span v-if="a.description" style="color:#909399;font-size:11px;margin-left:6px">{{ a.description }}</span>
          </ElOption>
        </ElSelect>
      </div>

      <div class="row row--inline">
        <ElCheckbox v-model="saveAsTemplate">保存为新模板</ElCheckbox>
        <ElInput v-if="saveAsTemplate"
                 v-model="templateName" placeholder="模板名" style="max-width: 240px;" />
      </div>

      <div class="row">
        <label>文件 ({{ stagedFiles.length }} / 50)</label>
        <ElUpload
          :auto-upload="false" multiple :show-file-list="false"
          @change="onPick" class="upload">
          <ElButton>+ 选择文件...</ElButton>
        </ElUpload>
        <ul class="files">
          <li v-for="f in stagedFiles" :key="f.path">
            <span>{{ f.name }}</span>
            <ElButton link size="small" @click="removeFile(f)">移除</ElButton>
          </li>
          <li v-for="f in uploading" :key="`u-${f.id}`" class="files__uploading">
            <span>{{ f.name }} ({{ f.progress }}%)</span>
          </li>
          <li v-for="f in failed" :key="`f-${f.id}`" class="files__failed">
            <span>{{ f.name }} — {{ f.error }}</span>
            <ElButton link size="small" @click="removeFailed(f)">移除</ElButton>
          </li>
        </ul>
      </div>
    </div>

    <template #footer>
      <ElButton @click="$emit('update:modelValue', false)">取消</ElButton>
      <ElButton type="primary" data-test="create-btn"
                :disabled="!canCreate" :loading="submitting"
                @click="submit">创建</ElButton>
    </template>
  </ElDialog>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import {
  ElDialog, ElInput, ElSelect, ElOption, ElCheckbox, ElButton, ElUpload,
  ElMessage,
} from 'element-plus'
import { stagingUpload, createBatch } from '@/api/aiChatBatches'
import { listTemplates, createTemplate } from '@/api/aiChatPromptTemplates'
import { listAgents } from '@/api/aiChat'
import type { AgentInfo } from '@/api/aiChat'
import type { AiChatBatchDetail, AiChatPromptTemplate, StagedFile } from '@/types/aiChatBatch'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'created', detail: AiChatBatchDetail): void
  (e: 'manageTemplates'): void
}>()

const name = ref('')
const prompt = ref('')
const selectedTemplateId = ref<string | null>(null)
const templates = ref<AiChatPromptTemplate[]>([])
const stagedFiles = ref<StagedFile[]>([])
const saveAsTemplate = ref(false)
const templateName = ref('')
const submitting = ref(false)
const selectedAgent = ref<string>('')
const agents = ref<AgentInfo[]>([])
const uploadSessionId = ref<string>(crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2))

const uploading = ref<{ id: number; name: string; progress: number }[]>([])
const failed = ref<{ id: number; name: string; error: string }[]>([])
let counter = 0

const canCreate = computed(() =>
  name.value.trim() !== '' &&
  prompt.value.trim() !== '' &&
  stagedFiles.value.length > 0 &&
  stagedFiles.value.length <= 50 &&
  !submitting.value
)

async function loadTemplates() {
  try { templates.value = await listTemplates() } catch { /* non-fatal */ }
}

onMounted(async () => {
  await loadTemplates()
  try {
    const r = await listAgents()
    agents.value = [...r.agents, ...r.subagents]
  } catch { /* non-fatal */ }
})

// 每次对话框打开时重新加载模板（确保「管理模板」里新建的内容能立即选到）
watch(() => props.modelValue, async (visible) => {
  if (visible) await loadTemplates()
})

function onPickTemplate(id: string | null) {
  const t = templates.value.find(x => x.id === id)
  if (t) prompt.value = t.content
}

function handleManageTemplates() {
  emit('manageTemplates')
}

async function onPick(file: any) {
  const rawFile = file.raw as File
  if (stagedFiles.value.length + uploading.value.length >= 50) {
    ElMessage.warning('已达到 50 个文件上限'); return
  }
  const id = ++counter
  uploading.value.push({ id, name: rawFile.name, progress: 0 })
  try {
    const staged = await stagingUpload(rawFile, uploadSessionId.value)
    uploading.value = uploading.value.filter(u => u.id !== id)
    stagedFiles.value.push(staged)
  } catch (e: any) {
    uploading.value = uploading.value.filter(u => u.id !== id)
    failed.value.push({ id, name: rawFile.name, error: e?.message || '上传失败' })
  }
}

function removeFile(f: StagedFile) {
  stagedFiles.value = stagedFiles.value.filter(x => x.path !== f.path)
}
function removeFailed(f: { id: number }) {
  failed.value = failed.value.filter(x => x.id !== f.id)
}

async function submit() {
  if (!canCreate.value) return
  submitting.value = true
  try {
    const detail = await createBatch({
      name: name.value.trim(),
      prompt: prompt.value.trim(),
      template_id: selectedTemplateId.value,
      agent: selectedAgent.value || null,
      files: stagedFiles.value,
    })
    if (saveAsTemplate.value && templateName.value.trim()) {
      try {
        await createTemplate(templateName.value.trim(), prompt.value.trim())
      } catch {
        ElMessage.warning('批任务已创建,但模板保存失败')
      }
    }
    emit('created', detail)
    emit('update:modelValue', false)
    reset()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || '创建失败')
  } finally {
    submitting.value = false
  }
}

function reset() {
  name.value = ''
  prompt.value = ''
  selectedTemplateId.value = null
  selectedAgent.value = ''
  stagedFiles.value = []
  saveAsTemplate.value = false
  templateName.value = ''
  uploadSessionId.value = crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2)
  uploading.value = []
  failed.value = []
}
</script>

<style scoped lang="scss">
.batch-create { display: flex; flex-direction: column; gap: 16px; }
.row { display: flex; flex-direction: column; gap: 6px; }
.row label { font-size: 13px; color: var(--el-text-color-secondary); }
.row__inline { display: flex; gap: 8px; align-items: center; }
.row--inline { display: flex; flex-direction: row; gap: 12px; align-items: center; }
.files { list-style: none; padding: 0; margin: 8px 0 0; max-height: 200px; overflow: auto; }
.files li { display: flex; justify-content: space-between; padding: 4px 8px; }
.files__uploading { color: var(--el-text-color-secondary); }
.files__failed { color: var(--el-color-danger); }
.upload :deep(.el-upload-list) { display: none; }
</style>
