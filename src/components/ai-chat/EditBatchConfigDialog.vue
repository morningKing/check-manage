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
        <div v-for="(c, i) in gateChecks" :key="i" class="gate-card" :data-test="`gate-row-${i}`">
          <!-- 行模式(2026-10-11 spec §8.4):终态期望=账本终态核对;过程检查=执行前/后拦工具调用 -->
          <div class="gate-card__line">
            <ElRadioGroup v-model="c.mode" size="small" data-test="gate-row-mode">
              <ElRadioButton value="post">终态期望</ElRadioButton>
              <ElRadioButton value="checkpoint">过程检查</ElRadioButton>
            </ElRadioGroup>
            <template v-if="c.mode === 'checkpoint'">
              <ElRadioGroup v-model="c.timing" size="small" data-test="gate-row-timing">
                <ElRadioButton value="before">执行前</ElRadioButton>
                <ElRadioButton value="after">执行后</ElRadioButton>
              </ElRadioGroup>
              <span class="gate-card__label">次数上限</span>
              <ElInputNumber v-model="c.max_checks" :min="1" :max="20" controls-position="right"
                             data-test="gate-row-max-checks" style="flex:0 0 110px" />
            </template>
          </div>
          <div class="gate-card__line">
            <ElInput v-model="c.name" placeholder="名称,如: 克隆目标仓库" style="flex:1" />
            <ElSelect v-model="c.tool" placeholder="工具" style="flex:0 0 110px"
                      filterable allow-create default-first-option>
              <ElOption v-for="t in gateTools" :key="t" :label="t" :value="t" />
            </ElSelect>
            <ElInputNumber v-if="c.mode !== 'checkpoint'" v-model="c.min_count" :min="1" :max="99" controls-position="right"
                           style="flex:0 0 100px" />
            <ElButton link type="danger" @click="gateChecks.splice(i, 1)"
                      :disabled="gateChecks.length <= 1">删除</ElButton>
          </div>
          <!-- 过程检查条件(§8.4):prompt 命中后注入模型;可选终态兜底断言(账本核对) -->
          <template v-if="c.mode === 'checkpoint'">
            <ElInput v-model="c.prompt" type="textarea" :rows="2"
                     maxlength="2000" show-word-limit
                     placeholder="检查提示词（命中后注入给模型）"
                     data-test="gate-row-prompt" />
            <ElCheckbox v-model="c.hasAssert" data-test="gate-row-has-assert">终态兜底断言</ElCheckbox>
            <div v-if="c.hasAssert" class="gate-card__line">
              <ElInput v-model="c.assert_tool" placeholder="断言工具（如 read）"
                       data-test="gate-row-assert-tool" />
              <ElInput v-model="c.assert_pattern" placeholder="断言 args 正则（如 docs/kb/）"
                       data-test="gate-row-assert-pattern" />
              <ElInputNumber v-model="c.assert_min_count" :min="1" controls-position="right"
                             data-test="gate-row-assert-min-count" style="flex:0 0 100px" />
            </div>
          </template>
          <ElInput v-model="c.args_pattern" placeholder="参数正则,如: git clone\s+\S*acme/inspector" />
          <ElInput v-model="c.subagents"
                   placeholder="适用子代理(可选,逗号分隔;留空=对所有子代理生效)" />
        </div>
        <div class="row__inline">
          <ElButton link data-test="gate-add" @click="gateChecks.push(emptyCheck())">+ 加一条期望</ElButton>
          <ElCheckbox v-model="gateRetry" data-test="gate-retry" style="margin-left:12px">自动修正（不过门时带缺失明细在原会话续跑修复）</ElCheckbox>
        </div>
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
         ElCheckbox, ElMessage, ElRadioGroup, ElRadioButton } from 'element-plus'
import { listAgents, listModels } from '@/api/aiChat'
import type { AgentInfo, ModelInfo } from '@/api/aiChat'
import { listGatePolicies } from '@/api/gatePolicies'
import type { GatePolicy } from '@/api/gatePolicies'
import { useAiChatBatchesStore } from '@/stores/aiChatBatches'
import type { AiChatBatch } from '@/types/aiChatBatch'

const props = defineProps<{ modelValue: boolean; batch: AiChatBatch }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'saved'): void }>()
const store = useAiChatBatchesStore()
const agents = ref<AgentInfo[]>([])
const models = ref<ModelInfo[]>([])
const agent = ref<string>('')
const model = ref<string>('')
const name = ref<string>('')
const prompt = ref<string>('')
const provisionRepo = ref<string>('')
const provisionRef = ref<string>('')
const saving = ref(false)
const reuseAgents = ref<string[]>([])
const subagentList = ref<AgentInfo[]>([])

// 动作门禁(设计 §5.2 入口 A 的编辑面):预填批任务上已保存的期望
const gateEnabled = ref(false)
const gateRetry = ref(false)
// 2026-10-11 spec §8.4:mode=checkpoint 的过程检查行——timing/prompt/max_checks/
// hasAssert/assert_* 是行内部编辑字段,提交前 serializeChecks 剥离(不落 payload)
type GateCheckMode = 'post' | 'pre' | 'checkpoint'
type GateCheckRow = {
  name: string; tool: string; args_pattern: string; min_count: number; subagents: string
  mode: GateCheckMode; timing: 'before' | 'after'; prompt: string
  max_checks: number; hasAssert: boolean
  assert_tool: string; assert_pattern: string; assert_min_count: number
}
const gateChecks = ref<GateCheckRow[]>([])
const gateTools = ['bash', 'read', 'write', 'edit', 'grep', 'glob', 'task']
function emptyCheck(): GateCheckRow {
  return { name: '', tool: 'bash', args_pattern: '', min_count: 1, subagents: '',
           mode: 'post', timing: 'before', prompt: '', max_checks: 1,
           hasAssert: false, assert_tool: '', assert_pattern: '', assert_min_count: 1 }
}
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
  model.value = props.batch.model || ''
  provisionRepo.value = props.batch.provision_repo || ''
  provisionRef.value = props.batch.provision_ref || ''
  reuseAgents.value = Array.isArray((props.batch as any).subagent_reuse)
    ? ((props.batch as any).subagent_reuse as string[]) : []
  // 引用门禁策略(spec §8.2):批详情里有 gate_policy_id 就选中;初值留作
  // 「未动不传」比对基线
  gatePolicyIdInitial.value = (props.batch as any).gate_policy_id || ''
  gatePolicyId.value = gatePolicyIdInitial.value
  // 门禁预填:批定义上的 action_checks(创建时或编辑时保存的)
  const checks = (props.batch as any).action_checks as
    Array<{ name: string; tool: string; args_pattern: string; min_count?: number;
            subagents?: string[]; mode?: string; timing?: string; prompt?: string;
            max_checks?: number;
            assert_spec?: { tool: string; args_pattern: string; min_count: number } | null }> | null
  gateEnabled.value = Array.isArray(checks) && checks.length > 0
  gateEnabledInitial.value = gateEnabled.value
  gateRetry.value = (props.batch as any).gate_retry === true
  gateChecks.value = (checks || []).map(c => ({
    name: c.name, tool: c.tool || 'bash',
    args_pattern: c.args_pattern, min_count: c.min_count ?? 1,
    subagents: (c.subagents || []).join(', '),
    // 过程检查行(§8.4)按 mode 分流回填;post/pre 行恢复编辑缺省(hasAssert 按
    // assert_spec 有无展开)
    mode: c.mode === 'checkpoint' ? 'checkpoint' : 'post',
    timing: c.timing === 'after' ? 'after' : 'before',
    prompt: c.prompt || '',
    max_checks: c.max_checks ?? 1,
    hasAssert: !!c.assert_spec,
    assert_tool: c.assert_spec?.tool || '',
    assert_pattern: c.assert_spec?.args_pattern || '',
    assert_min_count: c.assert_spec?.min_count ?? 1,
  }))
  if (gateEnabled.value && gateChecks.value.length === 0) gateChecks.value = [emptyCheck()]
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

/** 提交归一(2026-10-11 spec §8.4):剥离行内部字段,checkpoint 行 mode 键齐——
 * post 行保持既有提交契约 {name, tool, args_pattern, min_count[, subagents]};
 * checkpoint 行 = {name, mode:'checkpoint', check_type:'tool', tool, args_pattern,
 *   timing, prompt, max_checks[, assert_spec][, subagents]} */
function serializeChecks(rows: GateCheckRow[]): any[] {
  return rows.map((c) => {
    const subs = (c.subagents || '').split(/[,，\s]+/).map(x => x.trim()).filter(Boolean)
    if (c.mode === 'checkpoint') {
      const row: any = { name: c.name.trim(), mode: 'checkpoint', check_type: 'tool',
        tool: c.tool || 'bash', args_pattern: c.args_pattern.trim(),
        timing: c.timing === 'after' ? 'after' : 'before',
        prompt: (c.prompt || '').trim(), max_checks: c.max_checks || 1 }
      if (c.hasAssert) {
        row.assert_spec = { tool: c.assert_tool.trim(),
          args_pattern: c.assert_pattern.trim(), min_count: c.assert_min_count || 1 }
      }
      if (subs.length) row.subagents = subs  // 子代理定向:只有这些子代理的动作参与核对
      return row
    }
    const row: any = { name: c.name.trim(), tool: c.tool || 'bash',
      args_pattern: c.args_pattern.trim(), min_count: c.min_count || 1 }
    if (subs.length) row.subagents = subs
    return row
  })
}

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
      action_checks = serializeChecks(
        gateChecks.value.filter(c => c.name.trim() && c.args_pattern.trim()))
      if (action_checks.length === 0) {
        ElMessage.warning('已启用动作门禁,但没有任何一条完整的期望(需要名称与参数正则)')
        return
      }
    }
    const body = {
      name: name.value.trim(),
      prompt: prompt.value.trim(),
      agent: agent.value || null, model: model.value || null,
      provision_repo: provisionRepo.value.trim() || null,
      provision_ref: provisionRef.value.trim() || null,
      action_checks: gateEnabled.value ? action_checks : [],
      gate_retry: gateRetry.value,
      subagent_reuse: reuseAgents.value,
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
