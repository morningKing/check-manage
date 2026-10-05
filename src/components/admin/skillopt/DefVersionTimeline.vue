<template>
  <div class="vt">
    <p v-if="!displayVersions.length" class="muted">该定义暂无版本记录。</p>
    <ElTimeline v-else>
      <ElTimelineItem v-for="v in displayVersions" :key="v.id"
                      :timestamp="fmtTime(v.firstSeenAt)" placement="top">
        <div class="vt__head">
          <ElInput v-model="labelDrafts[v.id]" size="small" class="vt__label"
                   placeholder="版本标注，回车保存" @change="saveLabel(v)" />
          <span class="mono" :title="v.contentHash ?? ''">{{ shortHash(v.contentHash) }}</span>
          <ElButton link size="small" type="primary" @click="emit('generate', v)">
            生成步骤
          </ElButton>
          <ElButton link size="small" :disabled="!v.archived"
                    @click="openContent(v)">内容</ElButton>
          <ElButton link size="small" :disabled="!v.archived || !prevOf(v)"
                    @click="openDiff(v)">对比</ElButton>
          <ElButton link size="small" type="warning" :disabled="!v.archived"
                    @click="rollback(v)">回滚</ElButton>
        </div>
        <div class="vt__metrics">
          任务 {{ v.tasks }} · 均分 {{ v.avgScore ?? '—' }} · 拟合率 {{ fmtRate(v.fitRate) }}
          <ElTag v-if="v.divergedCount" size="small" type="danger">偏离 {{ v.divergedCount }}</ElTag>
          <ElTag v-if="v.partialCount" size="small" type="warning">部分 {{ v.partialCount }}</ElTag>
          <span v-if="v.tasks > 0 && !v.divergedCount && !v.partialCount" class="muted">无偏离</span>
        </div>
        <div v-if="hasDelta(v)" class="vt__deltas">
          <span>较上一版：</span>
          <span v-if="v.deltaScore != null" :class="deltaNumClass(v.deltaScore)">
            均分 {{ fmtDeltaNum(v.deltaScore) }}
          </span>
          <span v-if="v.deltaFitRate" :class="deltaClass(v.deltaFitRate)">
            拟合率 {{ fmtDelta(v.deltaFitRate) }}
          </span>
          <span v-if="v.deltaDiverged != null && v.deltaDiverged !== 0"
                :class="divergeDeltaClass(v.deltaDiverged)">
            偏离 {{ v.deltaDiverged > 0 ? '+' : '' }}{{ v.deltaDiverged }}
          </span>
        </div>
      </ElTimelineItem>
    </ElTimeline>
    <ElDialog v-model="contentDialog.visible"
              :title="`版本内容 — ${contentDialog.label}`" width="720px">
      <div v-loading="contentDialog.loading">
        <pre class="vt__content">{{ contentDialog.content }}</pre>
      </div>
    </ElDialog>
    <ElDialog v-model="diffDialog.visible"
              :title="`版本对比 — ${diffDialog.title}`" width="860px">
      <div v-loading="diffDialog.loading">
        <DefContentDiff v-if="!diffDialog.loading" :diff="diffDialog.diff" />
      </div>
    </ElDialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import {
  ElTimeline, ElTimelineItem, ElInput, ElButton, ElTag, ElMessage,
  ElDialog, ElMessageBox,
} from 'element-plus'
import DefContentDiff from './DefContentDiff.vue'
import {
  listSkillDefVersions, updateSkillDefVersion, getSkillDefVersionContent,
  compareSkillDefVersions, rollbackSkillDefVersion,
} from '@/api/aiSkills'
import type { SkillDefVersion } from '@/api/aiSkills'

const props = defineProps<{ defKind: string; defName: string }>()
const emit = defineEmits<{ (e: 'generate', v: SkillDefVersion): void }>()

interface VersionView extends SkillDefVersion {
  deltaScore: number | null
  deltaFitRate: number | null
  deltaDiverged: number | null
}
const versions = ref<VersionView[]>([])
const labelDrafts = ref<Record<string, string>>({})

const displayVersions = computed(() => [...versions.value].reverse())

/** 序列守卫：切定义后慢返回的旧响应不得覆盖当前列表 */
let loadSeq = 0
async function load() {
  const seq = ++loadSeq
  try {
    const res = await listSkillDefVersions({
      defKind: props.defKind, defName: props.defName,
    })
    const list: VersionView[] = (res.versions || []).map(v => ({
      ...v, deltaScore: null, deltaFitRate: null, deltaDiverged: null,
    }))
    // 后端 ORDER BY first_seen_at：组内旧 → 新；Δ = 相邻版本差值
    for (let i = 1; i < list.length; i++) {
      const prev = list[i - 1]
      const cur = list[i]
      cur.deltaScore = prev.avgScore != null && cur.avgScore != null
        ? Math.round((cur.avgScore - prev.avgScore) * 10) / 10 : null
      cur.deltaFitRate = prev.fitRate != null && cur.fitRate != null
        ? cur.fitRate - prev.fitRate : null
      cur.deltaDiverged = cur.divergedCount - prev.divergedCount
    }
    if (seq !== loadSeq) return
    versions.value = list
    for (const v of list) labelDrafts.value[v.id] = v.versionLabel ?? ''
  } catch { /* 全局 toast 已提示 */ }
}
watch([() => props.defKind, () => props.defName], () => {
  versions.value = []
  void load()
}, { immediate: true })

function hasDelta(v: VersionView) {
  return v.deltaScore != null || (v.deltaFitRate != null && v.deltaFitRate !== 0)
    || (v.deltaDiverged != null && v.deltaDiverged !== 0)
}

async function saveLabel(v: SkillDefVersion) {
  const draft = (labelDrafts.value[v.id] ?? '').trim()
  if (draft === (v.versionLabel ?? '')) return
  try {
    await updateSkillDefVersion(v.id, { versionLabel: draft || null })
    ElMessage.success('版本标注已保存')
    await load()
  } catch { /* 全局 toast 已提示 */ }
}

// ── 版本归档动作（spec §6）：内容预览 / 相邻对比 / 前滚式回滚 ───────────

const contentDialog = ref<{ visible: boolean; loading: boolean; label: string
  ; content: string }>({ visible: false, loading: false, label: '', content: '' })
const diffDialog = ref<{ visible: boolean; loading: boolean; title: string
  ; diff: string }>({ visible: false, loading: false, title: '', diff: '' })

/** displayVersions 新→旧；v 的「上一版」= 列表中其后第一个已归档版本 */
function prevOf(v: VersionView): VersionView | undefined {
  const idx = displayVersions.value.findIndex(x => x.id === v.id)
  return displayVersions.value.slice(idx + 1).find(x => x.archived)
}

async function openContent(v: VersionView) {
  contentDialog.value = { visible: true, loading: true,
    label: v.versionLabel || v.defName, content: '' }
  try {
    const res = await getSkillDefVersionContent(v.id)
    contentDialog.value.content = res.content
  } catch {
    contentDialog.value.visible = false   // 全局 toast 已提示
  } finally {
    contentDialog.value.loading = false
  }
}

async function openDiff(v: VersionView) {
  const prev = prevOf(v)
  if (!prev) return
  diffDialog.value = { visible: true, loading: true, title: '', diff: '' }
  try {
    const res = await compareSkillDefVersions({ fromId: prev.id, toId: v.id })
    diffDialog.value.title =
      `${prev.versionLabel || prev.defName}@${shortHash(prev.contentHash)}` +
      ` → ${v.versionLabel || v.defName}@${shortHash(v.contentHash)}`
    diffDialog.value.diff = res.diff
  } catch {
    diffDialog.value.visible = false
  } finally {
    diffDialog.value.loading = false
  }
}

async function rollback(v: VersionView) {
  try {
    await ElMessageBox.confirm(
      '将把该版本归档的正文原样写回当前定义文件（下次执行起生效）。确定回滚？',
      '回滚确认', { type: 'warning', confirmButtonText: '回滚',
                    cancelButtonText: '取消' })
  } catch { return }
  try {
    await rollbackSkillDefVersion(v.id)
    ElMessage.success('已回滚：正文已写回定义文件')
    await load()
  } catch { /* 全局 toast 已提示 */ }
}

// ── 格式化 helpers（从父组件就近迁入）─────────────────────────────────

function fmtRate(v: number | null | undefined) {
  return v == null ? '—' : Math.round(v * 100) + '%'
}
function fmtDelta(v: number | null | undefined) {
  if (v == null) return '—'
  return (v > 0 ? '+' : '') + Math.round(v * 100) + '%'
}
function deltaClass(v: number | null | undefined) {
  if (v == null || v === 0) return ''
  return v > 0 ? 'delta-up' : 'delta-down'
}
function fmtDeltaNum(v: number | null | undefined) {
  if (v == null) return '—'
  if (v === 0) return '0'
  return (v > 0 ? '↑ +' : '↓ ') + Math.round(Math.abs(v) * 10) / 10
}
function deltaNumClass(v: number | null | undefined) {
  if (v == null || v === 0) return ''
  return v > 0 ? 'delta-up' : 'delta-down'
}
/** 偏离数变化的语义配色：变多=坏（红），变少=好（绿） */
function divergeDeltaClass(dd: number | null | undefined) {
  if (dd == null || dd === 0) return ''
  return dd > 0 ? 'delta-down' : 'delta-up'
}
function shortHash(h?: string | null) {
  return h ? h.slice(0, 10) + '…' : '—'
}
function fmtTime(v?: string | null) {
  return v ? new Date(v).toLocaleString() : '—'
}
</script>

<style scoped>
.vt__head { display: flex; align-items: center; gap: 8px; }
.vt__label { width: 180px; }
.vt__metrics, .vt__deltas {
  font-size: 12.5px; margin-top: 4px;
  display: flex; gap: 8px; flex-wrap: wrap; align-items: center;
}
.mono { font-family: monospace; font-size: 12px; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }
.delta-up { color: var(--el-color-success); font-weight: 600; }
.delta-down { color: var(--el-color-danger); font-weight: 600; }
.vt__content {
  margin: 0; max-height: 60vh; overflow: auto;
  font-family: monospace; font-size: 12px; line-height: 1.5; white-space: pre-wrap;
  word-break: break-all;
}
</style>
