<template>
  <ElDialog :model-value="modelValue" title="编辑批任务配置" width="640px"
            @update:model-value="$emit('update:modelValue', $event)" @open="prefill">
    <div class="row">
      <label>批任务名称 <span class="hint">（必填 · ≤200 字符，保存后侧栏分组头同步更新）</span></label>
      <ElInput v-model="name" maxlength="200" show-word-limit data-test="edit-batch-name" />
    </div>
    <div class="row">
      <label>提示词 <span class="hint">（必填 · 对「待运行 / 重试 / 重新执行」的子任务生效，运行中与已完成的子任务不受影响）</span></label>
      <ElInput v-model="prompt" type="textarea" :rows="5" :autosize="{ minRows: 5, maxRows: 14 }"
               data-test="edit-batch-prompt" />
    </div>
    <div class="row">
      <label>Agent <span class="hint">（留空=默认，可手填项目 Agent 名）</span></label>
      <ElSelect v-model="agent" placeholder="使用 OpenCode 默认 Agent"
                clearable filterable allow-create default-first-option>
        <ElOption v-for="a in agents" :key="a.name" :label="a.name" :value="a.name" />
      </ElSelect>
    </div>
    <div class="row">
      <label>模型 <span class="hint">（可选 · 可多选：多选保存后「待运行 / 重试 / 重新执行」的子任务按顺序轮询重新分配；运行中与已完成的子任务不受影响。留空=默认）</span></label>
      <ElSelect v-model="selectedModels" multiple placeholder="使用默认模型" clearable filterable
                data-test="edit-model-select">
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
      <label>并发上限 <span class="hint">（可选 · 本批同时执行的子任务数，留空=跟随全局并发；保存立即生效，运行中的批次下一个调度周期按新值认领）</span></label>
      <ElInputNumber v-model="maxConcurrency" :min="1" :max="100" :step="1"
                     :value-on-clear="null" placeholder="留空=跟随全局并发"
                     data-test="edit-max-concurrency" style="width: 220px" />
    </div>

    <div class="row">
      <label>子代理会话复用 <span class="hint">（可选 · 勾选的子代理在本任务内跨轮次续跑同一子会话，保留历史上下文；气泡展开可分段查看）</span></label>
      <ElSelect v-model="reuseAgents" multiple filterable clearable
                placeholder="选择需要复用会话的子代理（留空=不启用）" style="width:100%">
        <ElOption v-for="a in subagentList" :key="a.name" :label="a.name" :value="a.name" />
      </ElSelect>
    </div>

    <div class="row">
      <label>动作门禁 <span class="hint">（可选 · 子任务终态逐条核对账本；只对未完成子任务生效，已完成子任务的历史核对结果不变）</span></label>
      <div class="row__inline">
        <ElCheckbox v-model="gateEnabled" data-test="gate-enabled">启用动作门禁</ElCheckbox>
        <ElSelect v-model="gatePolicyId" data-test="gate-policy-select"
                  clearable placeholder="引用门禁策略（可选）"
                  :disabled="!gateEnabled" style="width: 240px">
          <ElOption v-for="p in policies" :key="p.id" :value="p.id"
                    :label="`${p.name}（${p.checks.length} 条规则）`" />
        </ElSelect>
      </div>
      <template v-if="gateEnabled">
        <!-- 规则行编辑(2026-10-11 spec §8.4):与批创建对话框/管理页门禁策略
             共用 GateCheckRows(同一套字段与提交归一,见该组件 serializeChecks) -->
        <GateCheckRows v-model="gateChecks">
          <template #footer>
            <ElCheckbox v-model="gateRetry" data-test="gate-retry" style="margin-left:12px">自动修正（不过门时带缺失明细在原会话续跑修复）</ElCheckbox>
          </template>
        </GateCheckRows>
        <div class="row__inline">
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
import { listGatePolicies } from '@/api/gatePolicies'
import type { GatePolicy } from '@/api/gatePolicies'
import GateCheckRows from '@/components/admin/GateCheckRows.vue'
import type { GateCheckEditRow } from '@/components/admin/GateCheckRows.vue'
import { emptyEditRow, prefillEditRows, serializeChecks } from '@/components/admin/GateCheckRows.vue'
import { useAiChatBatchesStore } from '@/stores/aiChatBatches'
import type { AiChatBatch } from '@/types/aiChatBatch'

const props = defineProps<{ modelValue: boolean; batch: AiChatBatch }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'saved'): void }>()
const store = useAiChatBatchesStore()
const agents = ref<AgentInfo[]>([])
const models = ref<ModelInfo[]>([])
const agent = ref<string>('')
// 多模型（2026-10-27）：回显批 models（旧批无列表回落 [model] 单值兼容）；
// 保存显式传（哨兵幂等），null=回到单模型
const selectedModels = ref<string[]>([])
const name = ref<string>('')
const prompt = ref<string>('')
const provisionRepo = ref<string>('')
const provisionRef = ref<string>('')
const saving = ref(false)
const reuseAgents = ref<string[]>([])
const subagentList = ref<AgentInfo[]>([])
// 批并发上限（2026-10-27）：null=不限（跟随全局并发）；保存始终显式传，
// 后端幂等 upsert
const maxConcurrency = ref<number | null>(null)

// 动作门禁(设计 §5.2 入口 A 的编辑面):预填批任务上已保存的期望
// 行编辑形态与提交归一见共享组件 GateCheckRows(行类型/serializeChecks 同源)
const gateEnabled = ref(false)
const gateRetry = ref(false)
const gateChecks = ref<GateCheckEditRow[]>([])
// 引用门禁策略(2026-10-11 spec §8.2):回填批详情上的引用;下拉拉取失败静默
// (空下拉不阻塞表单)。gatePolicyIdInitial/gateEnabledInitial 记录打开时的值——
// 保存时只对改动传显式键(哨兵语义)
const policies = ref<GatePolicy[]>([])
const gatePolicyId = ref<string>('')
const gatePolicyIdInitial = ref<string>('')
const gateEnabledInitial = ref(false)
function loadPolicies() {
  listGatePolicies().then(r => { policies.value = r.policies })
    .catch(() => { /* 静默:策略下拉为空不阻塞表单 */ })
}

function prefill() {
  name.value = props.batch.name || ''
  prompt.value = props.batch.prompt || ''
  agent.value = props.batch.agent || ''
  selectedModels.value = Array.isArray((props.batch as any).models)
    ? ((props.batch as any).models as string[])
    : ((props.batch as any).model ? [(props.batch as any).model as string] : [])
  provisionRepo.value = props.batch.provision_repo || ''
  provisionRef.value = props.batch.provision_ref || ''
  reuseAgents.value = Array.isArray((props.batch as any).subagent_reuse)
    ? ((props.batch as any).subagent_reuse as string[]) : []
  maxConcurrency.value = (props.batch as any).max_concurrency ?? null
  // 引用门禁策略(spec §8.2):批详情里有 gate_policy_id 就选中;初值留作
  // 「未动不传」比对基线
  gatePolicyIdInitial.value = (props.batch as any).gate_policy_id || ''
  gatePolicyId.value = gatePolicyIdInitial.value
  // 门禁预填:批定义上的 action_checks(创建时或编辑时保存的)——
  // pre/checkpoint/verifier 行 round-trip(共享 prefillEditRows,raw 只挂 pre 行)
  const checks = (props.batch as any).action_checks as
    Array<Record<string, any>> | null
  gateEnabled.value = Array.isArray(checks) && checks.length > 0
  gateEnabledInitial.value = gateEnabled.value
  gateRetry.value = (props.batch as any).gate_retry === true
  gateChecks.value = prefillEditRows(checks)
  if (gateEnabled.value && gateChecks.value.length === 0) gateChecks.value = [emptyEditRow()]
  loadPolicies()
}

onMounted(async () => {
  // Only PRIMARY agents can be a session's agent (a subagent makes OpenCode
  // silently produce nothing → batch hangs). Use @mention to delegate instead.
  try {
    const r = await listAgents()
    agents.value = r.agents
    subagentList.value = r.subagents || []
  } catch { /* non-fatal */ }
  try { models.value = (await listModels()).models } catch { /* non-fatal */ }
  prefill()
})

async function save() {
  if (!name.value.trim()) {
    ElMessage.warning('批任务名称不能为空')
    return
  }
  if (!prompt.value.trim()) {
    ElMessage.warning('提示词不能为空')
    return
  }
  saving.value = true
  try {
    let action_checks: any[] | null = null
    if (gateEnabled.value) {
      // 过程检查行守卫(§8.4,后端 validate_checks 同口径 400,前端先行拦截):
      // 提示词必填——命中后要注入给模型
      if (gateChecks.value.some(c => c.mode === 'checkpoint' && c.name.trim()
          && c.args_pattern.trim() && !(c.prompt || '').trim())) {
        ElMessage.error('过程检查行需要填写检查提示词（命中后注入给模型）')
        return
      }
      // 终态兜底断言守卫:勾选后断言工具与 args 正则必填
      if (gateChecks.value.some(c => c.mode === 'checkpoint' && c.hasAssert
          && (!(c.assert_tool || '').trim() || !(c.assert_pattern || '').trim()))) {
        ElMessage.error('终态兜底断言需要填写断言工具与 args 正则')
        return
      }
      // 归一内部按行类型白名单过滤不完整的行(verifier 行看 rubric,tool 行看
      // args_pattern)——调用方不再按 args_pattern 预筛(会把判官行误删)
      action_checks = serializeChecks(gateChecks.value)
      // policy-only 组合(spec §8.2):空批规则 + 已引用门禁策略 = 合法(后端按
      // 策略 ∪ 空批 checks 合并核对)——仅当既无策略引用又无有效规则时才拦;
      // 该组合下 serializeChecks 空数组归一为 null(批规则空缺,纯策略生效)。
      // gatePolicyId 显式键哨兵语义不变:清空下拉 = 传 null 解绑,未动不传
      if (action_checks.length === 0) {
        if (!gatePolicyId.value) {
          ElMessage.warning('已启用动作门禁,但没有任何一条完整的期望(需要名称与参数正则/判定要点,或引用门禁策略)')
          return
        }
        action_checks = null
      }
    }
    const body = {
      name: name.value.trim(),
      prompt: prompt.value.trim(),
      agent: agent.value || null,
      model: selectedModels.value[0] || null,
      models: selectedModels.value.length ? selectedModels.value : null,
      provision_repo: provisionRepo.value.trim() || null,
      provision_ref: provisionRef.value.trim() || null,
      action_checks: gateEnabled.value ? action_checks : [],
      gate_retry: gateRetry.value,
      subagent_reuse: reuseAgents.value,
      // 并发上限始终显式传（后端幂等窄 upsert）：null=清除上限
      max_concurrency: maxConcurrency.value ?? null,
    }
    // 引用门禁策略(spec §8.2):显式键哨兵语义——清空/换选传新值,关闸传 null
    // 解绑,未动不传。例外:批引用了策略但门禁从未启用过(policy-only,开放
    // API 创建)时无操作直存不解绑——否则每次 innocent save 都会静默甩掉引用。
    // 后端兜底 400:引用不存在/已停用
    const policyNow = gateEnabled.value ? (gatePolicyId.value || null) : null
    const policyTouched = (gatePolicyId.value || null) !== (gatePolicyIdInitial.value || null)
    const gateToggledOff = !gateEnabled.value && gateEnabledInitial.value
    if (policyTouched || gateToggledOff) {
      ;(body as { gatePolicyId?: string | null }).gatePolicyId = policyNow
    }
    await store.updateBatchConfig(props.batch.id, body)
    ElMessage.success('已保存（提示词对未执行子任务生效）')
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
.gate-card__label { font-size: 11px; color: var(--el-text-color-placeholder); }
</style>
