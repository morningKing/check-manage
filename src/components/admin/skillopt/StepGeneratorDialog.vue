<template>
  <ElDialog :model-value="visible" :title="`生成步骤 — ${defName || '定义'}`"
            width="780px" append-to-body
            @update:model-value="$emit('update:visible', $event)">
    <div class="gen">
      <div class="gen__row">
        <span class="gen__label">定义路径</span>
        <ElInput v-model="gen.path" size="small"
                 placeholder="定义文件绝对路径（服务端读取并解析 fit.steps）" />
        <ElButton size="small" type="primary" :loading="gen.busy" @click="runGenerate">
          生成步骤
        </ElButton>
      </div>
      <p v-if="gen.error" class="gen__err">{{ gen.error }}</p>

      <template v-if="gen.steps.length">
        <ElTable :data="gen.steps" size="small" class="gen__steps">
          <ElTableColumn label="id" width="150">
            <template #default="{ row }"><ElInput v-model="row.id" size="small" /></template>
          </ElTableColumn>
          <ElTableColumn label="名称" min-width="130">
            <template #default="{ row }"><ElInput v-model="row.name" size="small" /></template>
          </ElTableColumn>
          <ElTableColumn label="tool" width="110">
            <template #default="{ row }"><ElInput v-model="row.tool" size="small" /></template>
          </ElTableColumn>
          <ElTableColumn label="args_pattern" min-width="200">
            <template #default="{ row }">
              <ElInput v-model="row.args_pattern" size="small" />
            </template>
          </ElTableColumn>
          <ElTableColumn label="操作" width="70">
            <template #default="{ $index }">
              <ElButton link size="small" type="danger" @click="removeStep($index)">删除</ElButton>
            </template>
          </ElTableColumn>
        </ElTable>
        <ElButton size="small" @click="addStep">添加步骤</ElButton>

        <div class="gen__preview">
          <div class="gen__row">
            <span class="gen__label">试算 attempt</span>
            <ElInput v-model="gen.attemptId" size="small"
                     placeholder="历史 attemptId（可从上方拟合列表复制）" />
            <ElButton size="small" :loading="gen.previewing" @click="runPreview">试算</ElButton>
          </div>
          <div v-if="gen.preview" class="gen__preview-result">
            <SkillFitBadge :steps-total="gen.preview.steps_total"
                           :per-step="gen.preview.per_step"
                           :score="gen.preview.score" :status="gen.preview.status" />
            <ElTable :data="gen.preview.per_step" size="small" class="gen__ps">
              <ElTableColumn prop="name" label="步骤" min-width="140" />
              <ElTableColumn label="状态" width="90">
                <template #default="{ row: ps }">
                  <span class="fit-dot" :class="dotClass(ps.status)" />
                  {{ stepStatusText(ps.status) }}
                </template>
              </ElTableColumn>
              <ElTableColumn label="证据" min-width="220">
                <template #default="{ row: ps }">
                  <span v-if="ps.evidence && ps.evidence.length" class="mono"
                        :title="ps.evidence[0].args">
                    {{ ps.evidence[0].tool }} · {{ trunc(ps.evidence[0].args, 72) }}
                  </span>
                  <span v-else class="muted">—</span>
                </template>
              </ElTableColumn>
            </ElTable>
          </div>
        </div>
      </template>
    </div>

    <template #footer>
      <ElButton size="small" @click="$emit('update:visible', false)">取消</ElButton>
      <ElButton size="small" type="primary" :loading="gen.saving"
                :disabled="!gen.steps.length" @click="saveSteps">保存</ElButton>
    </template>
  </ElDialog>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElDialog, ElTable, ElTableColumn, ElInput, ElButton, ElMessage } from 'element-plus'
import SkillFitBadge from '@/components/admin/SkillFitBadge.vue'
import {
  generateSkillDefSteps, applySkillDefSteps, previewSkillDefSteps,
} from '@/api/aiSkills'
import type { FitStep, FitPreview } from '@/api/aiSkills'

const props = defineProps<{ visible: boolean; defKind: string; defName: string }>()
const emit = defineEmits<{ (e: 'update:visible', v: boolean): void }>()

interface GenStepRow {
  id: string
  name: string
  tool: string
  args_pattern: string
  /** 生成结果里首个 expect 之外的项，编辑时保留不丢 */
  extraExpect: FitStep['expect']
}
const gen = ref({
  path: '', steps: [] as GenStepRow[], attemptId: '',
  preview: null as FitPreview | null,
  busy: false, previewing: false, saving: false, error: '',
})
// 每次打开重置对话框内部状态（恢复抽取前 openGenerator 的行为）
watch(() => props.visible, v => {
  if (v) Object.assign(gen.value, {
    path: '', steps: [], attemptId: '', preview: null, error: '',
  })
})

function toGenRow(s: FitStep): GenStepRow {
  const expect = s.expect || []
  const [first, ...rest] = expect
  return {
    id: s.id || '', name: s.name || '',
    tool: first?.tool || '', args_pattern: first?.args_pattern || '',
    extraExpect: rest,
  }
}
function fromGenRow(r: GenStepRow): FitStep {
  const expect: FitStep['expect'] = []
  if (r.tool.trim()) {
    expect.push({
      tool: r.tool.trim(),
      ...(r.args_pattern.trim() ? { args_pattern: r.args_pattern.trim() } : {}),
    })
  }
  expect.push(...r.extraExpect)
  return { id: r.id.trim(), name: r.name.trim() || r.id.trim(), expect }
}
function addStep() {
  gen.value.steps.push({ id: '', name: '', tool: '', args_pattern: '', extraExpect: [] })
}
function removeStep(i: number) {
  gen.value.steps.splice(i, 1)
}
async function runGenerate() {
  if (!gen.value.path.trim()) {
    gen.value.error = '请填写定义文件路径'
    return
  }
  gen.value.error = ''
  gen.value.busy = true
  try {
    const res = await generateSkillDefSteps({
      kind: props.defKind || undefined, path: gen.value.path.trim(),
    })
    gen.value.steps = (res.steps || []).map(toGenRow)
    gen.value.preview = null
  } catch { /* 全局 toast 已提示 */ } finally {
    gen.value.busy = false
  }
}
async function runPreview() {
  if (!gen.value.attemptId.trim()) {
    gen.value.error = '请填写用于试算的历史 attemptId'
    return
  }
  gen.value.error = ''
  gen.value.previewing = true
  try {
    const res = await previewSkillDefSteps({
      steps: gen.value.steps.map(fromGenRow), attemptId: gen.value.attemptId.trim(),
    })
    gen.value.preview = res.preview
  } catch { /* 全局 toast 已提示 */ } finally {
    gen.value.previewing = false
  }
}
async function saveSteps() {
  if (!gen.value.path.trim()) {
    gen.value.error = '请填写定义文件路径'
    return
  }
  gen.value.error = ''
  gen.value.saving = true
  try {
    const res = await applySkillDefSteps({
      path: gen.value.path.trim(), steps: gen.value.steps.map(fromGenRow),
    })
    ElMessage.success(`已回写 ${res.path}`)
    emit('update:visible', false)
  } catch { /* 全局 toast 已提示 */ } finally {
    gen.value.saving = false
  }
}

function trunc(s: string | undefined, n: number) {
  const t = s || ''
  return t.length > n ? t.slice(0, n) + '…' : t
}
function dotClass(status: string) {
  if (status === 'hit') return 'is-hit'
  if (status === 'miss') return 'is-miss'
  return 'is-skipped'
}
function stepStatusText(status: string) {
  if (status === 'hit') return '命中'
  if (status === 'miss') return '未命中'
  if (status === 'skipped') return '跳过'
  return status
}
</script>

<style scoped>
.mono { font-family: monospace; font-size: 12px; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }

.fit-dot {
  display: inline-block; width: 8px; height: 8px; border-radius: 50%;
  margin-right: 4px; vertical-align: middle;
}
.fit-dot.is-hit { background: var(--el-color-success); }
.fit-dot.is-miss { background: var(--el-color-danger); }
.fit-dot.is-skipped { background: var(--el-border-color-darker); opacity: .55; }

.gen__row { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.gen__label { flex: none; width: 72px; font-size: 12.5px; color: var(--el-text-color-secondary); }
.gen__row .el-input { flex: 1; }
.gen__err { color: var(--el-color-danger); font-size: 12.5px; margin: 0 0 8px; }
.gen__steps { margin-bottom: 8px; }
.gen__preview { margin-top: 12px; border-top: 1px dashed var(--el-border-color); padding-top: 10px; }
.gen__preview-result { margin-top: 8px; }
.gen__ps { margin-top: 8px; }
</style>
