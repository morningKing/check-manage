<template>
  <ElDrawer v-model="visible" title="执行合规审计" size="720px" @open="load">
    <div v-loading="loading" class="exec-audit">
      <ElAlert v-if="error" type="error" :closable="false" :title="error" />
      <template v-else>
        <!-- ── 执行尝试 ── -->
        <section class="sec">
          <h4>执行尝试 ({{ attempts.length }})</h4>
          <ElTable v-if="attempts.length" :data="attempts" size="small">
            <ElTableColumn prop="attemptNo" label="#" width="46" />
            <ElTableColumn prop="operation" label="操作" width="86" />
            <ElTableColumn label="Agent" min-width="140">
              <template #default="{ row }">
                <span>{{ row.effectiveAgent || '（未知）' }}</span>
                <span class="muted"> · {{ row.agentResolution }}</span>
              </template>
            </ElTableColumn>
            <ElTableColumn label="Model" min-width="150">
              <template #default="{ row }">
                <span>{{ row.effectiveModel || '（未知）' }}</span>
                <span class="muted"> · {{ row.modelResolution }}</span>
              </template>
            </ElTableColumn>
            <ElTableColumn label="状态" width="96">
              <template #default="{ row }">
                <ElTag size="small" :type="statusTagType(row.status)">{{ row.status }}</ElTag>
              </template>
            </ElTableColumn>
            <ElTableColumn label="Prompt" width="110">
              <template #default="{ row }">
                <ElLink v-if="row.effectivePromptHash" type="primary" @click="showPrompt(row.id)">
                  查看快照
                </ElLink>
                <span v-else class="muted">—</span>
              </template>
            </ElTableColumn>
          </ElTable>
          <p v-else class="muted">暂无执行尝试记录（该会话可能创建于审计功能上线前）。</p>
        </section>

        <!-- ── Agent/Skill Manifest ── -->
        <section v-if="manifests.length" class="sec">
          <h4>本次可见的 Agent / Skill 定义</h4>
          <ElTable :data="manifests" size="small">
            <ElTableColumn prop="kind" label="类型" width="80" />
            <ElTableColumn prop="name" label="名称" min-width="140" />
            <ElTableColumn prop="source" label="来源" width="120" />
            <ElTableColumn label="Hash" width="100">
              <template #default="{ row }">
                <span class="mono">{{ shortHash(row.contentHash) }}</span>
              </template>
            </ElTableColumn>
            <ElTableColumn label="已注入" width="70">
              <template #default="{ row }">
                <ElTag size="small" :type="row.injected ? 'success' : 'info'">
                  {{ row.injected ? '是' : '否' }}
                </ElTag>
              </template>
            </ElTableColumn>
          </ElTable>
        </section>

        <!-- ── 轨迹分析历史（与原会话的关联） ── -->
        <section v-if="analyses.length" class="sec">
          <h4>轨迹分析历史 ({{ analyses.length }})</h4>
          <p class="muted">以下分析会话由本会话触发，可随时打开回看。</p>
          <div v-for="a in analyses" :key="a.id" class="analysis-row">
            <ElTag size="small" :type="a.status === 'completed' ? 'success'
              : a.status === 'failed' ? 'danger' : 'warning'">{{ a.status }}</ElTag>
            <span class="mono">{{ a.analysis_session_id }}</span>
            <span class="muted">{{ fmtTime(a.created_at) }}</span>
            <ElLink type="primary" @click="openAnalysis(a.analysis_session_id)">打开会话</ElLink>
          </div>
        </section>

        <!-- ── 结构化诊断报告 ── -->
        <template v-if="report">
          <section class="sec">
            <h4>数据完整性</h4>
            <ElProgress :percentage="Math.round((report.data_completeness?.score ?? 0) * 100)"
                        :stroke-width="10" />
            <ul v-if="report.data_completeness?.limitations?.length" class="limits">
              <li v-for="(l, i) in report.data_completeness?.limitations" :key="i">
                {{ l }}
              </li>
            </ul>
          </section>

          <section class="sec">
            <h4>执行契约审计
              <ElTag v-if="report.contract" size="small" class="ml8"
                     :type="report.contract.status === 'complete' ? 'success' : 'warning'">
                {{ report.contract.status }}
              </ElTag>
            </h4>
            <p v-if="!report.contract || report.contract.status === 'unknown_due_to_missing_data'"
               class="muted">
              {{ report.contract?.reason || '该执行的 Skill 未定义结构化执行契约，无法审计步骤遗漏。' }}
            </p>
            <template v-else>
              <ElTable :data="report.contract.steps || []" size="small">
                <ElTableColumn prop="step_id" label="步骤" min-width="120" />
                <ElTableColumn label="契约" width="60">
                  <template #default="{ row }">
                    <ElTag size="small" :type="row.expected ? 'warning' : 'info'">
                      {{ row.expected ? '必需' : '可选' }}
                    </ElTag>
                  </template>
                </ElTableColumn>
                <ElTableColumn label="Todo" width="60">
                  <template #default="{ row }">
                    <ElTag size="small" :type="row.declared_by_agent ? 'success' : 'info'">
                      {{ row.declared_by_agent ? '已声明' : '未声明' }}
                    </ElTag>
                  </template>
                </ElTableColumn>
                <ElTableColumn label="证据" width="70">
                  <template #default="{ row }">
                    <ElTag size="small" :type="row.observed ? 'success' : 'danger'">
                      {{ row.observed ? '有' : '无' }}
                    </ElTag>
                  </template>
                </ElTableColumn>
                <ElTableColumn label="结论" width="170">
                  <template #default="{ row }">
                    <ElTag size="small" :type="stepTagType(row.status)">{{ stepLabel(row.status) }}</ElTag>
                  </template>
                </ElTableColumn>
                <ElTableColumn prop="reason" label="说明" min-width="160" show-overflow-tooltip />
              </ElTable>
            </template>
          </section>

          <section v-if="report.tool_failures?.length" class="sec">
            <h4>工具失败 ({{ report.tool_failures.length }})</h4>
            <div v-for="(f, i) in report.tool_failures" :key="i" class="failure-card">
              <div class="failure-head">
                <span class="mono">{{ f.tool }}</span>
                <ElTag size="small" type="danger">{{ f.failure_type }}</ElTag>
              </div>
              <div v-if="f.result_preview" class="failure-body">{{ f.result_preview }}</div>
              <div v-if="f.recovery" class="failure-recovery">
                恢复：{{ f.recovery.attempted ? '尝试了' : '未尝试' }}
                <template v-if="f.recovery.same_input_retry">相同参数重试</template>
                <template v-if="f.recovery.strategy_changed">、调整策略</template>
                · {{ f.recovery.recovered ? '已恢复' : '未恢复' }}
              </div>
            </div>
          </section>

          <section v-if="report.declared_plan?.declared_steps?.length" class="sec">
            <h4>Agent 声明计划（Todo）</h4>
            <p class="muted">仅代表 Agent 自述计划，不作为执行事实。</p>
            <ul class="plan-list">
              <li v-for="(s, i) in report.declared_plan.declared_steps" :key="i">
                <ElTag size="small" :type="s.status === 'completed' ? 'success'
                  : s.status === 'in_progress' ? 'warning' : 'info'">{{ s.status }}</ElTag>
                {{ s.content }}
              </li>
            </ul>
          </section>

          <section v-if="report.execution?.prompt" class="sec">
            <h4>Prompt 快照</h4>
            <div class="mono small">
              raw: {{ shortHash(report.execution.prompt.raw_hash) }} ·
              effective: {{ shortHash(report.execution.prompt.effective_hash) }}
              ({{ report.execution.prompt.effective_len ?? '—' }} chars)
            </div>
            <p class="muted">完整 Prompt 内容默认脱敏；需「AI 执行 Prompt 查看」权限且开启明文存储。</p>
          </section>
        </template>
        <p v-else-if="!loading" class="muted">尚无可生成的审计报告（无已完成的执行尝试）。</p>

        <!-- ── Prompt 查看弹层 ── -->
        <ElDialog v-model="promptVisible" title="Prompt 快照" width="640px">
          <template v-if="promptInfo">
            <p class="mono small">raw: {{ shortHash(promptInfo.rawHash) }} ·
               effective: {{ shortHash(promptInfo.effectiveHash) }}
               ({{ promptInfo.effectiveLen ?? '—' }} chars)</p>
            <p v-if="!promptInfo.plaintextAvailable" class="muted">
              当前快照仅保存 hash 与增强项（明文存储未开启或内容已脱敏）。增强项：
              <code>{{ JSON.stringify(promptInfo.augmentations) }}</code>
            </p>
            <pre v-else class="prompt-pre">{{ promptInfo.effectivePrompt }}</pre>
          </template>
        </ElDialog>
      </template>
    </div>
  </ElDrawer>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElDrawer, ElTable, ElTableColumn, ElTag, ElAlert, ElProgress,
         ElLink, ElDialog } from 'element-plus'
import {
  getExecutionAudit, getExecutionPrompt, getSessionAnalyses,
  type ExecutionAttempt, type ExecutionManifest, type ExecutionReport,
  type SessionAnalysis,
} from '@/api/aiSessionAdmin'

const props = defineProps<{ modelValue: boolean; sessionId: string | null }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const loading = ref(false)
const error = ref('')
const attempts = ref<ExecutionAttempt[]>([])
const manifests = ref<ExecutionManifest[]>([])
const report = ref<ExecutionReport | null>(null)
const analyses = ref<SessionAnalysis[]>([])
const promptVisible = ref(false)
const promptInfo = ref<Awaited<ReturnType<typeof getExecutionPrompt>> | null>(null)

async function load() {
  if (!props.sessionId) return
  loading.value = true
  error.value = ''
  try {
    const res = await getExecutionAudit(props.sessionId)
    attempts.value = res.attempts || []
    manifests.value = res.manifests || []
    report.value = res.report || null
    try {
      analyses.value = (await getSessionAnalyses(props.sessionId)).analyses || []
    } catch { /* 分析历史非关键，失败不阻塞 */ }
  } catch (e: unknown) {
    const ax = e as { response?: { data?: { error?: string } }; message?: string }
    error.value = ax?.response?.data?.error || ax?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function showPrompt(attemptId: string) {
  try {
    promptInfo.value = await getExecutionPrompt(props.sessionId!, attemptId)
    promptVisible.value = true
  } catch (e: unknown) {
    const ax = e as { response?: { data?: { error?: string } }; message?: string }
    error.value = ax?.response?.data?.error || ax?.message || 'Prompt 快照获取失败'
  }
}

function shortHash(h?: string | null) {
  return h ? h.slice(0, 10) + '…' : '—'
}
function fmtTime(v?: string | null) {
  return v ? new Date(v).toLocaleString() : '—'
}
function openAnalysis(analysisSessionId: string) {
  window.open(`/ai-chat?session=${analysisSessionId}`, '_blank')
}
function statusTagType(s: string) {
  return s === 'completed' ? 'success' : s === 'failed' ? 'danger'
    : s === 'stopped' ? 'info' : 'warning'
}
function stepTagType(s: string) {
  return s === 'completed_confirmed' ? 'success'
    : s === 'completed_claimed' ? 'warning'
    : s === 'failed' || s === 'missing' || s === 'out_of_order' ? 'danger'
    : 'info'
}
function stepLabel(s: string) {
  return ({
    completed_confirmed: '已完成（有证据）',
    completed_claimed: '声称完成（无证据）',
    missing: '遗漏',
    skipped: '跳过',
    out_of_order: '顺序错误',
    failed: '失败',
    unknown_due_to_missing_data: '数据不足',
  } as Record<string, string>)[s] || s
}

watch(() => props.sessionId, () => {
  attempts.value = []
  manifests.value = []
  report.value = null
  analyses.value = []
})
</script>

<style scoped>
.exec-audit { padding: 4px; }
.sec { margin-bottom: 18px; }
.sec h4 { margin: 0 0 8px; font-size: 14px; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }
.mono { font-family: monospace; font-size: 12px; }
.small { font-size: 12px; }
.ml8 { margin-left: 8px; }
.limits { color: var(--el-text-color-secondary); font-size: 12px; padding-left: 18px; }
.failure-card { border: 1px solid var(--el-color-danger-light-7); border-left: 3px solid var(--el-color-danger);
  border-radius: 6px; padding: 8px 10px; margin-bottom: 8px; font-size: 12px; }
.failure-head { display: flex; align-items: center; gap: 8px; font-weight: 600; }
.failure-body { margin-top: 4px; color: var(--el-text-color-secondary); word-break: break-all;
  max-height: 80px; overflow: auto; white-space: pre-wrap; }
.failure-recovery { margin-top: 4px; color: var(--el-text-color-regular); }
.plan-list { padding-left: 18px; font-size: 13px; }
.plan-list li { margin-bottom: 4px; }
.analysis-row { display: flex; align-items: center; gap: 8px; padding: 6px 0;
  border-bottom: 1px solid var(--el-border-color-lighter); font-size: 12px; }
.prompt-pre { background: var(--el-fill-color-light); padding: 10px; border-radius: 6px;
  white-space: pre-wrap; word-break: break-word; max-height: 400px; overflow: auto;
  font-size: 12px; font-family: monospace; }
</style>
