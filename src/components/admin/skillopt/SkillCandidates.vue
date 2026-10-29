<template>
  <div class="cands">
    <div class="cands__bar">
      <ElSelect v-model="status" size="small" style="width: 140px" @change="load">
        <ElOption label="全部状态" value="" />
        <ElOption v-for="s in STATUSES" :key="s" :label="s" :value="s" />
      </ElSelect>
      <span class="cands__hint">
        候选经双集回归（边界改善 ∧ 保留零退化）后才能发布；发布需二次确认，可回滚
      </span>
    </div>
    <div class="cand-list" v-loading="loading">
      <div v-for="c in items" :key="c.id" class="cand-item" :data-status="c.status">
        <button class="cand-head" type="button"
                @click="c._open = !c._open">
          <span class="cand-head__name">
            {{ c.defName }}<span class="cand-head__kind">{{ c.defKind }}</span>
            <ElTag size="small" :type="tagType(c.status)">{{ statusText(c.status) }}</ElTag>
            <ElTag v-if="c.validation?.mode" size="small" type="info">
              {{ c.validation.mode === 'behavioral' ? 'behavioral' : 'mechanical' }}
            </ElTag>
          </span>
          <span class="cand-head__time">{{ c.createdAt }}</span>
        </button>
        <div v-show="c._open" class="cand-body">
          <div class="cand-detail">
            <div v-if="c.fallbackContent" class="cand-detail__warn">
              ⚠ 整块降级（非最小 diff），请人工逐行审阅
            </div>
            <pre v-else class="cand-detail__diff">{{ diffText(c.edits) }}</pre>
            <div class="cand-detail__meta">
              <div><span class="lab">根因</span>{{ c.source?.diagnosisCause || '—' }}</div>
              <div><span class="lab">预期修复</span>{{ c.source?.expectedRepair || '—' }}</div>
              <div>
                <span class="lab">可能受损</span>
                {{ (c.source?.possiblyHarmed || []).join('；') || '—' }}
              </div>
            </div>
            <div v-if="c.validation?.mode === 'behavioral'" class="cand-detail__warn">
              纯正文/非匹配面修改，双集回归不适用——请人工审阅 diff 后决定发布
            </div>
            <div v-else-if="c.validation?.insufficientEvidence" class="cand-detail__warn">
              证据不足（边界 {{ c.validation.boundaryCount }}/保留 {{ c.validation.holdoutCount }}
              未达门槛 2/3），候选继续保留待积累
            </div>
            <table v-else-if="c.validation?.boundary?.length" class="cand-detail__table">
              <thead>
                <tr><th>集合</th><th>任务 attempt</th><th>前</th><th>后</th></tr>
              </thead>
              <tbody>
                <tr v-for="r in c.validation.boundary" :key="'b' + r.attemptId">
                  <td>边界</td><td>{{ shortId(r.attemptId) }}</td>
                  <td>{{ r.before.score }} / {{ r.before.status }}</td>
                  <td>{{ r.after.score }} / {{ r.after.status }}</td>
                </tr>
                <tr v-for="r in c.validation.holdout" :key="'h' + r.attemptId">
                  <td>保留</td><td>{{ shortId(r.attemptId) }}</td>
                  <td>{{ r.before.score }} / {{ r.before.status }}</td>
                  <td>{{ r.after.score }} / {{ r.after.status }}</td>
                </tr>
              </tbody>
            </table>
            <div v-if="c.validation?.rejectReason" class="cand-detail__meta">
              <div><span class="lab">失败原因</span>{{ c.validation.rejectReason }}</div>
            </div>
          </div>
          <div class="cand-ops">
            <ElButton size="small" data-testid="cand-validate"
                      :disabled="c.status !== 'pending'" @click="validate(c)">
              回归验证
            </ElButton>
            <ElButton size="small" type="primary" data-testid="cand-promote"
                      :disabled="c.status !== 'validated'" @click="promote(c)">
              发布
            </ElButton>
            <ElButton size="small" data-testid="cand-reject"
                      :disabled="!['pending', 'validated'].includes(c.status)"
                      @click="reject(c)">
              拒绝
            </ElButton>
          </div>
        </div>
      </div>
      <div v-if="!loading && !items.length" class="cands__empty">暂无候选</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox, ElButton, ElSelect, ElOption, ElTag } from 'element-plus'
import {
  listSkillCandidates, validateSkillCandidate, promoteSkillCandidate, rejectSkillCandidate,
} from '@/api/aiSessionAdmin'

const STATUSES = ['pending', 'validated', 'rejected', 'promoted', 'expired']
const items = ref<any[]>([])
const loading = ref(false)
const status = ref('')

const TAG: Record<string, string> = {
  pending: 'info', validated: 'success', rejected: 'danger',
  promoted: 'success', expired: 'warning',
}
const STATUS_TEXT: Record<string, string> = {
  pending: '待验证', validated: '回归通过', rejected: '已拒绝',
  promoted: '已发布', expired: '已过期',
}
const tagType = (s: string) => (TAG[s] || 'info') as any
const statusText = (s: string) => STATUS_TEXT[s] || s
const diffText = (edits: any[]) =>
  (edits || []).map(e => `- ${e.old_str}\n+ ${e.new_str}\n# ${e.reason}`).join('\n---\n')
const shortId = (id: string) => String(id).slice(0, 8)

async function load() {
  loading.value = true
  try {
    const resp: any = await listSkillCandidates({ status: status.value || undefined })
    const rows = resp.data?.items ?? resp.items ?? []
    items.value = rows.map((r: any) => ({ ...r, _open: false }))
  } finally {
    loading.value = false
  }
}

async function validate(c: any) {
  const resp: any = await validateSkillCandidate(c.id)
  const v = resp.data?.validation ?? resp.validation
  if (v?.decision === 'insufficient_evidence') {
    ElMessage.warning('证据不足（边界/保留集样本不够），候选继续保留待积累')
  } else if (v?.decision === 'validated' && v?.mode === 'behavioral') {
    ElMessage.success('行为类修改：回归不适用，已标记人工审阅通过，可发布')
  } else if (v?.decision === 'validated') {
    ElMessage.success('回归通过：边界改善 ∧ 保留零退化')
  } else {
    ElMessage.error(`回归未通过：${v?.rejectReason || '未知'}`)
  }
  await load()
}

async function promote(c: any) {
  await ElMessageBox.confirm(
    `发布将回写定义文件「${c.defName}」并登记新版本（版本演进可一键回滚）。`
    + '发布后该定义的其余未决候选将自动过期（基线漂移）。确认发布？',
    '发布候选', { type: 'warning' })
  try {
    await promoteSkillCandidate(c.id)
    ElMessage.success('已发布，新版本见「版本演进」，可随时回滚')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || '发布失败')
  }
  await load()
}

async function reject(c: any) {
  await rejectSkillCandidate(c.id)
  await load()
}

onMounted(load)
</script>

<style scoped>
.cands__bar { display: flex; gap: 12px; align-items: center; margin-bottom: 8px; flex-wrap: wrap; }
.cands__hint { color: var(--el-text-color-secondary); font-size: 12px; }
.cands__empty { color: var(--el-text-color-secondary); padding: 24px; text-align: center; }
.cand-item { border: 1px solid var(--el-border-color-lighter); border-radius: 6px;
  padding: 8px 12px; margin-bottom: 8px; }
.cand-item[data-status='rejected'] { background: var(--el-fill-color-lighter); }
.cand-head { display: flex; gap: 8px; align-items: center; width: 100%;
  background: none; border: none; padding: 0; cursor: pointer; text-align: left; }
.cand-head__name { font-weight: 600; margin-right: auto; display: flex;
  align-items: center; gap: 8px; }
.cand-head__kind { font-size: 11px; color: var(--el-text-color-secondary); font-weight: 400;
  border: 1px solid var(--el-border-color); border-radius: 3px; padding: 0 5px; }
.cand-head__time { color: var(--el-text-color-secondary); font-size: 12px; }
.cand-ops { margin: 8px 0 2px; }
.cand-detail { border-top: 1px dashed var(--el-border-color); padding-top: 8px; margin-top: 8px; }
.cand-detail__warn { color: var(--el-color-warning); font-size: 12px;
  background: var(--el-color-warning-light-9); border: 1px solid var(--el-color-warning-light-5);
  border-radius: 4px; padding: 6px 10px; }
.cand-detail__diff { background: var(--el-fill-color-light); padding: 8px; font-size: 12px;
  white-space: pre-wrap; margin: 6px 0; }
.cand-detail__meta { font-size: 12px; color: var(--el-text-color-regular); margin: 6px 0; }
.cand-detail__meta .lab { color: var(--el-text-color-secondary); display: inline-block; width: 70px; }
.cand-detail__table { font-size: 12px; width: 100%; border-collapse: collapse; }
.cand-detail__table th, .cand-detail__table td { border: 1px solid var(--el-border-color-lighter);
  padding: 4px 8px; text-align: left; }
</style>
