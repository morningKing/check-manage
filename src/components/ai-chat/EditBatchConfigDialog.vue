<template>
  <ElDialog :model-value="modelValue" title="编辑 Agent / 模型" width="460px"
            @update:model-value="$emit('update:modelValue', $event)" @open="prefill">
    <div class="row">
      <label>Agent <span class="hint">（留空=默认，可手填项目 Agent 名）</span></label>
      <ElSelect v-model="agent" placeholder="使用 OpenCode 默认 Agent"
                clearable filterable allow-create default-first-option>
        <ElOption v-for="a in agents" :key="a.name" :label="a.name" :value="a.name" />
      </ElSelect>
    </div>
    <div class="row">
      <label>模型 <span class="hint">（留空=默认）</span></label>
      <ElSelect v-model="model" placeholder="使用默认模型" clearable filterable>
        <ElOption v-for="m in models" :key="m.id" :label="m.label" :value="m.id" />
      </ElSelect>
    </div>
    <div class="row">
      <label>预置仓库 <span class="hint">（可选 · Agent/Skill，克隆进 .opencode/）</span></label>
      <ElInput v-model="provisionRepo" placeholder="git URL（仓库根 = .opencode 内容）" />
      <ElInput v-model="provisionRef" placeholder="分支 / tag / commit（可选）" style="margin-top:6px" />
      <div class="hint" style="margin-top:4px">改动对该批次的「重试 / 重新执行 / 待运行」子任务生效。</div>
    </div>
    <template #footer>
      <ElButton @click="$emit('update:modelValue', false)">取消</ElButton>
      <ElButton type="primary" :loading="saving" @click="save">保存</ElButton>
    </template>
  </ElDialog>
</template>
<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElDialog, ElSelect, ElOption, ElButton, ElInput, ElMessage } from 'element-plus'
import { listAgents, listModels } from '@/api/aiChat'
import type { AgentInfo, ModelInfo } from '@/api/aiChat'
import { useAiChatBatchesStore } from '@/stores/aiChatBatches'
import type { AiChatBatch } from '@/types/aiChatBatch'

const props = defineProps<{ modelValue: boolean; batch: AiChatBatch }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'saved'): void }>()
const store = useAiChatBatchesStore()
const agents = ref<AgentInfo[]>([])
const models = ref<ModelInfo[]>([])
const agent = ref<string>('')
const model = ref<string>('')
const provisionRepo = ref<string>('')
const provisionRef = ref<string>('')
const saving = ref(false)

function prefill() {
  agent.value = props.batch.agent || ''
  model.value = props.batch.model || ''
  provisionRepo.value = props.batch.provision_repo || ''
  provisionRef.value = props.batch.provision_ref || ''
}

onMounted(async () => {
  // Only PRIMARY agents can be a session's agent (a subagent makes OpenCode
  // silently produce nothing → batch hangs). Use @mention to delegate instead.
  try { const r = await listAgents(); agents.value = r.agents } catch { /* non-fatal */ }
  try { models.value = (await listModels()).models } catch { /* non-fatal */ }
  prefill()
})

async function save() {
  saving.value = true
  try {
    await store.updateBatchConfig(props.batch.id, {
      agent: agent.value || null, model: model.value || null,
      provision_repo: provisionRepo.value.trim() || null,
      provision_ref: provisionRef.value.trim() || null,
    })
    ElMessage.success('已保存')
    emit('saved'); emit('update:modelValue', false)
  } catch (e: unknown) {
    const err = e as { response?: { data?: { error?: string } } }
    ElMessage.error(err.response?.data?.error || '保存失败')
  } finally { saving.value = false }
}
</script>
<style scoped>
.row { margin-bottom: 12px; }
.row label { display: block; margin-bottom: 4px; font-size: 13px; }
.hint { color: var(--el-text-color-placeholder); font-size: 11px; }
</style>
