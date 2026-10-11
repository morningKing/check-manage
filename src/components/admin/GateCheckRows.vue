<template>
  <div class="gate-rows">
    <div v-for="(c, i) in modelValue" :key="i" class="gate-card" :data-test="`gate-row-${i}`">
      <!-- 行模式(2026-10-11 spec §8.4):终态期望=账本终态核对;拦截(pre)=执行前
           deny 拦断;过程检查=执行前/后拦工具调用。pre 仅回填既有数据时出现,
           语义只能透传不能改写(deny 降级成 post=静默放行) -->
      <div v-if="c.check_type !== 'verifier'" class="gate-card__line">
        <ElRadioGroup v-model="c.mode" size="small" data-test="gate-row-mode">
          <ElRadioButton value="post">终态期望</ElRadioButton>
          <ElRadioButton value="pre">拦截</ElRadioButton>
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
        <!-- 类型选择仅 post 行暴露:pre/checkpoint 行固定 tool 型
             (后端 mode=pre/checkpoint 只支持 tool 行) -->
        <ElSelect v-if="c.mode === 'post'" v-model="c.check_type" style="flex:0 0 132px">
          <ElOption label="工具调用断言" value="tool" />
          <ElOption label="判官核对" value="verifier" />
        </ElSelect>
        <ElInput v-model="c.name" placeholder="名称,如: 克隆目标仓库" style="flex:1" />
        <template v-if="c.check_type !== 'verifier'">
          <ElSelect v-model="c.tool" placeholder="工具" style="flex:0 0 110px"
                    filterable allow-create default-first-option>
            <ElOption v-for="t in gateTools" :key="t" :label="t" :value="t" />
          </ElSelect>
          <ElInputNumber v-if="c.mode !== 'checkpoint'" v-model="c.min_count" :min="1" :max="99" controls-position="right"
                         style="flex:0 0 100px" />
        </template>
        <ElButton link type="danger" @click="removeRow(i)"
                  :disabled="modelValue.length <= 1">删除</ElButton>
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
      <ElInput v-if="c.check_type === 'verifier'" v-model="c.rubric" type="textarea" :rows="3"
               placeholder="判定要点（≤2000 字，写给判官核对）" />
      <ElInput v-if="c.check_type === 'verifier'" v-model="c.subagents"
               placeholder="适用子代理(可选,逗号分隔;留空=核对任务会话整体,填写=只核对这些子代理的会话与动作)" />
      <template v-else>
        <ElInput v-model="c.args_pattern" placeholder="参数正则,如: git clone\s+\S*acme/inspector" />
        <ElInput v-model="c.subagents"
                 placeholder="适用子代理(可选,逗号分隔,如: general, explore;留空=对所有子代理生效)" />
      </template>
    </div>
    <div class="gate-rows__footer">
      <ElButton link data-test="gate-add" @click="addRow">+ 加一条期望</ElButton>
      <!-- 父级提示(自动修正开关/核对语义说明)挂这里,与加号同行 -->
      <slot name="footer" />
    </div>
  </div>
</template>

<script lang="ts">
/**
 * 门禁规则行编辑（2026-10-11 spec §8.4）——批创建对话框 / 批编辑对话框 /
 * 管理页门禁策略编辑器三处共用的同一套行形态，避免多份实现漂移。
 *
 * 行内部字段（mode 编辑态的 timing/prompt/max_checks/hasAssert/assert_*）不落
 * payload，提交前 serializeChecks 剥离；pre 行 round-trip 保真（raw 展开透传）。
 */
export type GateCheckEditMode = 'post' | 'pre' | 'checkpoint'

export interface GateCheckEditRow {
  name: string
  check_type: 'tool' | 'verifier'
  tool: string
  args_pattern: string
  min_count: number
  subagents: string
  rubric: string
  mode: GateCheckEditMode
  timing: 'before' | 'after'
  prompt: string
  max_checks: number
  hasAssert: boolean
  assert_tool: string
  assert_pattern: string
  assert_min_count: number
  /** 回填留存的原始行（仅 pre 透传分支读，post/checkpoint 白名单重建不读它） */
  raw?: Record<string, unknown> | null
}

export function emptyEditRow(): GateCheckEditRow {
  return { name: '', check_type: 'tool', tool: 'bash', args_pattern: '',
           min_count: 1, subagents: '', rubric: '',
           mode: 'post', timing: 'before', prompt: '', max_checks: 1,
           hasAssert: false, assert_tool: '', assert_pattern: '', assert_min_count: 1 }
}

/** 回填已保存的规则（API 归一后的形状）为编辑行。
 *
 * 行模式 round-trip：checkpoint/pre 原样保留（post 为缺省）——pre 是 deny
 * 拦截型规则，回填显示成 post 会在保存时静默改写语义；raw 留存原始行，
 * serializeChecks 对 pre 行透传（apply_to 等编辑面未建模的键不丢）。
 * verifier 行的 rubric 装在 effect_spec 里（validate_checks 归一形状）。 */
export function prefillEditRows(
  checks: Array<Record<string, any>> | null | undefined,
): GateCheckEditRow[] {
  return (checks || []).map(c => ({
    name: c.name || '',
    check_type: c.check_type === 'verifier' ? 'verifier' : 'tool',
    tool: c.check_type === 'verifier' ? '' : (c.tool || 'bash'),
    args_pattern: c.args_pattern || '',
    min_count: c.min_count ?? 1,
    subagents: (c.subagents || []).join(', '),
    rubric: String(c.effect_spec?.rubric ?? c.rubric ?? ''),
    mode: c.mode === 'checkpoint' ? 'checkpoint' : c.mode === 'pre' ? 'pre' : 'post',
    timing: c.timing === 'after' ? 'after' : 'before',
    prompt: c.prompt || '',
    max_checks: c.max_checks ?? 1,
    hasAssert: !!c.assert_spec,
    assert_tool: c.assert_spec?.tool || '',
    assert_pattern: c.assert_spec?.args_pattern || '',
    assert_min_count: c.assert_spec?.min_count ?? 1,
    ...(c.mode === 'pre' ? { raw: c as Record<string, unknown> } : {}),
  }))
}

/** 提交归一(spec §8.4 + 设计 §5.2):行内部字段不落 payload——按行类型白名单
 * 重建,不完整的行过滤:
 * pre(拦截)行 = 原样透传 {name, mode:'pre', check_type:'tool', tool,
 *   args_pattern, min_count[, subagents][, raw 上的 apply_to 等未建模键]}——
 *   deny 语义不能被编辑改写(mode 键丢了=拦截规则静默降级成终态期望);
 * post/tool 行 = {name, check_type:'tool', tool, args_pattern, min_count[, subagents]}
 * verifier 行 = {name, check_type:'verifier', rubric[, subagents]}
 * checkpoint 行 = {name, mode:'checkpoint', check_type:'tool', tool, args_pattern,
 *   timing, prompt, max_checks[, assert_spec][, subagents]}(mode 键齐) */
export function serializeChecks(rows: GateCheckEditRow[]): any[] {
  const out: any[] = []
  for (const c of rows) {
    const subs = (c.subagents || '').split(/[,，\s]+/).map(x => x.trim()).filter(Boolean)
    if (c.mode === 'pre') {
      // 拦截型行(deny,2026-10-11 spec):round-trip 保真——mode 键原样保留;
      // raw=回填留存的原始行(可能带 apply_to/require_state 等编辑面未建模的键,
      // 原样透传),建模键以 UI 当前值为准覆盖
      if (!c.name.trim() || !c.args_pattern.trim()) continue
      const row: any = { ...(c.raw || {}) }
      row.name = c.name.trim()
      row.mode = 'pre'
      row.check_type = 'tool'
      row.tool = c.tool || 'bash'
      row.args_pattern = c.args_pattern.trim()
      row.min_count = c.min_count || 1
      if (subs.length) row.subagents = subs  // 子代理定向:只有这些子代理的动作参与拦截
      else delete row.subagents  // 编辑面清空 = 取消定向(原始行的 subagents 不保留)
      out.push(row)
      continue
    }
    if (c.check_type === 'verifier') {
      if (c.name.trim() && c.rubric.trim()) {
        const row: any = { name: c.name.trim(), check_type: 'verifier', rubric: c.rubric.trim() }
        if (subs.length) row.subagents = subs  // 判官定向:只核对这些子代理
        out.push(row)
      }
      continue
    }
    if (c.mode === 'checkpoint') {
      // 过程检查行:prompt 必填(命中后注入模型);assert_spec=终态兜底断言
      if (!c.name.trim() || !c.args_pattern.trim() || !(c.prompt || '').trim()) continue
      const row: any = { name: c.name.trim(), mode: 'checkpoint', check_type: 'tool',
        tool: c.tool || 'bash', args_pattern: c.args_pattern.trim(),
        timing: c.timing === 'after' ? 'after' : 'before',
        prompt: c.prompt.trim(), max_checks: c.max_checks || 1 }
      if (c.hasAssert) {
        row.assert_spec = { tool: c.assert_tool.trim(),
          args_pattern: c.assert_pattern.trim(), min_count: c.assert_min_count || 1 }
      }
      if (subs.length) row.subagents = subs  // 子代理定向:只有这些子代理的动作参与核对
      out.push(row)
      continue
    }
    if (!c.name.trim() || !c.args_pattern.trim()) continue
    const row: any = { name: c.name.trim(), check_type: 'tool', tool: c.tool || 'bash',
      args_pattern: c.args_pattern.trim(), min_count: c.min_count || 1 }
    if (subs.length) row.subagents = subs  // 子代理定向:只有这些子代理的动作参与核对
    out.push(row)
  }
  return out
}
</script>

<script setup lang="ts">
import { ElButton, ElCheckbox, ElInput, ElInputNumber, ElOption,
         ElRadioButton, ElRadioGroup, ElSelect } from 'element-plus'

const props = defineProps<{ modelValue: GateCheckEditRow[] }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: GateCheckEditRow[]): void }>()

const gateTools = ['bash', 'read', 'write', 'edit', 'grep', 'glob', 'task']

// 行字段编辑由 v-model 直接改行对象（与父级数组共享同一引用，无需整表回发）；
// 增删改行数才走 update:modelValue
function addRow() {
  emit('update:modelValue', [...props.modelValue, emptyEditRow()])
}
function removeRow(i: number) {
  emit('update:modelValue', props.modelValue.filter((_, idx) => idx !== i))
}
</script>

<style scoped>
.gate-rows { display: flex; flex-direction: column; gap: 8px; width: 100%; }
.gate-rows__footer { display: flex; gap: 8px; align-items: center; }
.gate-card {
  display: flex; flex-direction: column; gap: 6px;
  padding: 8px 10px; border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px; background: var(--el-fill-color-lighter);
}
.gate-card__line { display: flex; gap: 6px; align-items: center; }
.gate-card__label { font-size: 11px; color: var(--el-text-color-secondary); }
</style>
