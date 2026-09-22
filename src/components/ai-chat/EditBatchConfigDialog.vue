<template>
  <ElDialog :model-value="modelValue" title="编辑批任务配置" width="640px"
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

    <div class="row">
      <label>动作门禁 <span class="hint">（可选 · 子任务终态逐条核对账本；只对未完成子任务生效，已完成子任务的历史核对结果不变）</span></label>
      <ElCheckbox v-model="gateEnabled" data-test="gate-enabled">启用动作门禁</ElCheckbox>
      <template v-if="gateEnabled">
        <div v-for="(c, i) in gateChecks" :key="i" class="gate-card" :data-test="`gate-row-${i}`">
          <div class="gate-card__line">
            <ElInput v-model="c.name" placeholder="名称,如: 克隆目标仓库" style="flex:1" />
            <ElSelect v-model="c.tool" placeholder="工具" style="flex:0 0 110px"
                      filterable allow-create default-first-option>
              <ElOption v-for="t in gateTools" :key="t" :label="t" :value="t" />
            </ElSelect>
            <ElInputNumber v-model="c.min_count" :min="1" :max="99" controls-position="right"
                           style="flex:0 0 100px" />
            <ElButton link type="danger" @click="gateChecks.splice(i, 1)"
                      :disabled="gateChecks.length <= 1">删除</ElButton>
          </div>
          <ElInput v-model="c.args_pattern" placeholder="参数正则,如: git clone\s+\S*acme/inspector" />
          <ElInput v-model="c.subagents"
                   placeholder="适用子代理(可选,逗号分隔;留空=对所有子代理生效)" />
        </div>
        <div class="row__inline">
          <ElButton link data-test="gate-add" @click="gateChecks.push(emptyCheck())">+ 加一条期望</ElButton>
          <span class="hint">
            保存后对未完成子任务立即生效；已完成子任务的历史核对结果保持不变。
          </span>
        </div>
      </template>
    </div>

    <template #footer>
      <ElButton @click="$emit('update:modelValue', false)">取消</ElButton>
      <ElButton type="primary" :loading="saving" @click="save">保存</ElButton>
    </template>
  </ElDialog>
</template>
<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElDialog, ElSelect, ElOption, ElButton, ElInput, ElInputNumber,
         ElCheckbox, ElMessage } from 'element-plus'
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

// 动作门禁(设计 §5.2 入口 A 的编辑面):预填批任务上已保存的期望
const gateEnabled = ref(false)
const gateChecks = ref<Array<{ name: string; tool: string; args_pattern: string; min_count: number; subagents: string }>>([])
const gateTools = ['bash', 'read', 'write', 'edit', 'grep', 'glob', 'task']
function emptyCheck() {
  return { name: '', tool: 'bash', args_pattern: '', min_count: 1, subagents: '' }
}

function prefill() {
  agent.value = props.batch.agent || ''
  model.value = props.batch.model || ''
  provisionRepo.value = props.batch.provision_repo || ''
  provisionRef.value = props.batch.provision_ref || ''
  // 门禁预填:批定义上的 action_checks(创建时或编辑时保存的)
  const checks = (props.batch as any).action_checks as
    Array<{ name: string; tool: string; args_pattern: string; min_count?: number; subagents?: string[] }> | null
  gateEnabled.value = Array.isArray(checks) && checks.length > 0
  gateChecks.value = (checks || []).map(c => ({
    name: c.name, tool: c.tool || 'bash',
    args_pattern: c.args_pattern, min_count: c.min_count ?? 1,
    subagents: (c.subagents || []).join(', '),
  }))
  if (gateEnabled.value && gateChecks.value.length === 0) gateChecks.value = [emptyCheck()]
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
    let action_checks: Array<{ name: string; tool: string; args_pattern: string; min_count: number; subagents?: string[] }> | null = null
    if (gateEnabled.value) {
      action_checks = gateChecks.value
        .filter(c => c.name.trim() && c.args_pattern.trim())
        .map(c => {
          const row: any = { name: c.name.trim(), tool: c.tool || 'bash',
            args_pattern: c.args_pattern.trim(), min_count: c.min_count || 1 }
          const subs = (c.subagents || '').split(/[,，\s]+/).map((x: string) => x.trim()).filter(Boolean)
          if (subs.length) row.subagents = subs
          return row
        })
      if (action_checks.length === 0) {
        ElMessage.warning('已启用动作门禁,但没有任何一条完整的期望(需要名称与参数正则)')
        return
      }
    }
    await store.updateBatchConfig(props.batch.id, {
      agent: agent.value || null, model: model.value || null,
      provision_repo: provisionRepo.value.trim() || null,
      provision_ref: provisionRef.value.trim() || null,
      action_checks: gateEnabled.value ? action_checks : [],
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
.row__inline { display: flex; gap: 8px; align-items: center; }
.hint { color: var(--el-text-color-placeholder); font-size: 11px; }
.gate-card {
  display: flex; flex-direction: column; gap: 6px;
  padding: 8px 10px; margin-bottom: 8px;
  border: 1px solid var(--el-border-color-lighter); border-radius: 6px;
  background: var(--el-fill-color-lighter);
}
.gate-card__line { display: flex; gap: 6px; align-items: center; }
</style>
