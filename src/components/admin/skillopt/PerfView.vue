<template>
  <div class="perf-view" v-loading="loading">
    <ElAlert v-if="error" type="error" :closable="false" :title="error" />
    <template v-else>
      <!-- 首屏：定义清单 + 慢任务 Top -->
      <div class="perf-landing">
        <aside class="perf-side">
          <div class="perf-side__title">定义</div>
          <div v-if="!defs.length" class="perf-empty">暂无任务数据——执行 agent/skill 任务后自动汇总。</div>
          <div v-for="d in defs" :key="d.defKind + '/' + d.defName"
               class="perf-def-item" :class="{ active: isSel(d) }"
               @click="selectDef(d)">
            <div class="perf-def-item__name">{{ d.defName }}</div>
            <div class="perf-def-item__meta">{{ d.tasks }} 任务 · P50 {{ fmtMs(d.p50Ms) }} · P95 {{ fmtMs(d.p95Ms) }}</div>
          </div>
        </aside>
        <section class="perf-main">
          <PerfSlowTop v-if="!selected" :tasks="slowTasks" @open="openTask" />
          <template v-if="selected">
            <div class="perf-def-head">
              <ElButton link @click="selected = null">← 返回</ElButton>
              <span class="perf-def-head__name">{{ selected.defName }}</span>
              <span class="muted">{{ selected.defKind }}</span>
            </div>
            <div class="perf-cards">
              <div class="perf-card"><div class="num">{{ defTasks.length }}</div><div class="lbl">任务</div></div>
              <div class="perf-card"><div class="num">{{ fmtMs(selected.p50Ms) }}</div><div class="lbl">P50</div></div>
              <div class="perf-card"><div class="num">{{ fmtMs(selected.p95Ms) }}</div><div class="lbl">P95</div></div>
              <div class="perf-card"><div class="num">{{ pct(selected.avgModelRatio) }}</div><div class="lbl">模型占比均值</div></div>
            </div>
            <PerfTrendChart v-if="defTasks.length" :tasks="defTasks" @open="openTask" />
            <ElTable :data="defTasks" size="small" @row-click="(r: any) => openTask(r.attemptId)">
              <ElTableColumn prop="startedAt" label="时间" width="170" />
              <ElTableColumn prop="sourceType" label="来源" width="90" />
              <ElTableColumn prop="status" label="状态" width="100" />
              <ElTableColumn label="墙钟" width="110">
                <template #default="{ row }">{{ fmtMs(row.wallMs) }}</template>
              </ElTableColumn>
              <ElTableColumn label="模型占比" width="100">
                <template #default="{ row }">{{ pct(row.modelRatio) }}</template>
              </ElTableColumn>
              <ElTableColumn prop="tokensIn" label="输入 token" width="110" />
              <ElTableColumn prop="subtaskCount" label="子代理" width="80" />
            </ElTable>
          </template>
        </section>
      </div>
      <!-- 任务下钻 -->
      <PerfTaskDetail v-if="openAttemptId" :attempt-id="openAttemptId"
                      @close="openAttemptId = null" />
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { perfOverview, perfDefTasks, perfSlowTasks, type PerfDefSummary, type PerfTaskEntry } from '@/api/aiSkills'
import PerfSlowTop from './PerfSlowTop.vue'
import PerfTrendChart from './PerfTrendChart.vue'
import PerfTaskDetail from './PerfTaskDetail.vue'
import { fmtMs, pct } from './format'

const loading = ref(false)
const error = ref('')
const defs = ref<PerfDefSummary[]>([])
const slowTasks = ref<PerfTaskEntry[]>([])
const selected = ref<PerfDefSummary | null>(null)
const defTasks = ref<PerfTaskEntry[]>([])
const openAttemptId = ref<string | null>(null)

const isSel = (d: PerfDefSummary) =>
  selected.value?.defKind === d.defKind && selected.value?.defName === d.defName

let defReqSeq = 0
async function selectDef(d: PerfDefSummary) {
  selected.value = d
  const seq = ++defReqSeq
  try {
    const tasks = (await perfDefTasks(d.defKind, d.defName)).tasks
    if (seq === defReqSeq) defTasks.value = tasks   // 快速连点：旧响应不覆盖新选择
  } catch {
    if (seq === defReqSeq) defTasks.value = []
  }
}
function openTask(id: string) { openAttemptId.value = id }

onMounted(async () => {
  loading.value = true
  try {
    const [ov, slow] = await Promise.all([perfOverview(), perfSlowTasks()])
    defs.value = ov.defs
    slowTasks.value = slow.tasks
  } catch (e: any) {
    error.value = e?.message || '加载性能数据失败'
  } finally { loading.value = false }
})
</script>

<style scoped lang="scss">
.perf-landing { display: flex; gap: 16px; }
.perf-side { width: 240px; flex-shrink: 0; border-right: 1px solid var(--el-border-color-lighter); padding-right: 12px; }
.perf-side__title { font-weight: 600; margin-bottom: 8px; }
.perf-def-item { padding: 8px; border-radius: 6px; cursor: pointer; }
.perf-def-item:hover, .perf-def-item.active { background: var(--el-fill-color-light); }
.perf-def-item__meta { font-size: 12px; color: var(--el-text-color-secondary); }
.perf-main { flex: 1; min-width: 0; }
.perf-empty { color: var(--el-text-color-secondary); padding: 24px 0; }
.perf-def-head { display: flex; align-items: center; gap: 8px; margin: 8px 0; }
.perf-def-head__name { font-weight: 600; font-size: 15px; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }
.perf-cards { display: flex; gap: 12px; margin: 12px 0; }
.perf-card { border: 1px solid var(--el-border-color-lighter); border-radius: 8px; padding: 10px 16px; min-width: 110px; }
.perf-card .num { font-size: 18px; font-weight: 600; }
.perf-card .lbl { font-size: 12px; color: var(--el-text-color-secondary); }
</style>
