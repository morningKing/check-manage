/**
 * 任务拟合页的纯展示逻辑（无 Vue / 无网络依赖，便于单测）。
 *
 * 目标：把后端的技术状态码翻译成管理员能直接读懂的结论，
 * 并把"定义 / 运行 / 步骤"三层数据整理成界面需要的形状。
 */
import type { FitPerStep, SkillFitDefinitionSummary, SkillFitDetail, SkillFitRow } from '@/api/aiSkills'

export type Tone = 'good' | 'warn' | 'bad' | 'muted'

export interface StatusMeta {
  label: string
  tone: Tone
  /** 一句话解释：这个结果意味着什么、通常要做什么 */
  hint: string
}

const STATUS_META: Record<string, StatusMeta> = {
  fit: { label: '符合', tone: 'good', hint: '所有步骤都按定义执行' },
  partial: { label: '部分符合', tone: 'warn', hint: '有步骤未执行，可能需要修订定义或检查模型是否跳步' },
  diverged: { label: '偏离', tone: 'bad', hint: '大部分步骤没有按定义执行，优先查看偏差原因' },
  reordered: { label: '顺序不符', tone: 'warn', hint: '步骤都执行了，但先后顺序与定义不一致' },
  no_trace: { label: '无执行记录', tone: 'muted', hint: '该次运行没有可用的工具调用轨迹，无法判定' },
  parse_error: { label: '结果解析失败', tone: 'muted', hint: '无法解析该次运行的轨迹数据' },
  no_steps: { label: '未配置步骤', tone: 'muted', hint: '定义里没有 fit.steps，拟合无从判定' },
  failed: { label: '执行失败', tone: 'bad', hint: '该次任务本身执行失败' },
  inconclusive: { label: '无法判定', tone: 'muted', hint: '证据不足，无法给出结论' },
}

export function statusMeta(status: string): StatusMeta {
  return STATUS_META[status] ?? { label: status || '未知', tone: 'muted', hint: '' }
}

export type HealthKey = 'diverged' | 'partial' | 'unconfigured' | 'ok' | 'idle'

export interface Health {
  key: HealthKey
  label: string
  tone: Tone
  /** 列表排序权重，越小越靠前（需要处理的排前面） */
  rank: number
}

/**
 * 定义的当前健康度，按「最近一次运行」判定（不是历史累计）：
 * 修好定义、再跑一次通过后，标签会自动恢复为正常。
 */
export function classifyHealth(d: SkillFitDefinitionSummary): Health {
  if (!d.tasks) return { key: 'idle', label: '暂无运行', tone: 'muted', rank: 4 }
  switch (d.latestStatus) {
    case 'diverged':
      return { key: 'diverged', label: '偏离', tone: 'bad', rank: 0 }
    case 'partial':
    case 'reordered':
      return { key: 'partial', label: '部分符合', tone: 'warn', rank: 1 }
    case 'no_steps':
      return { key: 'unconfigured', label: '未配置步骤', tone: 'muted', rank: 2 }
    case 'fit':
      return { key: 'ok', label: '正常', tone: 'good', rank: 3 }
    default:
      // no_trace / parse_error / failed 等：不算业务偏离，按正常排序但标注原因
      return { key: 'ok', label: statusMeta(d.latestStatus ?? '').label, tone: 'muted', rank: 3 }
  }
}

const CAUSE_LABELS: Record<string, string> = {
  definition_stale: '定义过时',
  step_redundant: '步骤冗余',
  order_deviation: '顺序偏差',
  model_noncompliance: '模型未遵循',
  environment: '环境异常',
}

export function causeLabel(cause?: string | null): string {
  return (cause && CAUSE_LABELS[cause]) || cause || '—'
}

export function causeTagType(cause?: string | null): 'warning' | 'danger' | 'info' {
  if (cause === 'model_noncompliance') return 'danger'
  if (cause === 'environment') return 'info'
  return 'warning'
}

export function kindLabel(kind: string): string {
  if (kind === 'skill') return '技能'
  if (kind === 'agent') return '代理'
  return kind
}

export function fmtRate(v: number | null | undefined): string {
  return v == null ? '—' : `${Math.round(v * 100)}%`
}

export function fmtTime(v?: string | null): string {
  return v ? new Date(v).toLocaleString() : '—'
}

export function shortHash(h?: string | null): string {
  return h ? `${h.slice(0, 10)}…` : '—'
}

export function trunc(s: string | undefined | null, n: number): string {
  const t = s || ''
  return t.length > n ? `${t.slice(0, n)}…` : t
}

/** 一次运行的未执行步骤名（按定义顺序）：只含 miss，顺序不符另算 */
export function missedStepNames(perStep: FitPerStep[]): string[] {
  return perStep.filter(s => s.status === 'miss').map(s => s.name || s.id)
}

/** 执行了但次序与定义不一致的步骤名 */
export function outOfOrderStepNames(perStep: FitPerStep[]): string[] {
  return perStep.filter(s => s.status === 'out_of_order').map(s => s.name || s.id)
}

export function stepStatusText(status: string): string {
  if (status === 'hit') return '命中'
  if (status === 'miss') return '未执行'
  if (status === 'out_of_order') return '顺序不符'
  if (status === 'skipped') return '跳过'
  return status
}

/** 步骤列（矩阵的行）：取最近一次带步骤明细的运行，保持其定义顺序 */
export interface StepColumn {
  id: string
  name: string
}

export function collectSteps(details: SkillFitDetail[]): StepColumn[] {
  for (const d of details) {
    if (d.perStep && d.perStep.length) {
      return d.perStep.map(s => ({ id: s.id, name: s.name || s.id }))
    }
  }
  return []
}

export type StepState = 'hit' | 'miss' | 'out_of_order' | 'skipped'

/** 指定运行里某步骤的状态；该运行没有此步骤明细时返回 null（显示为空白格） */
export function stepStatusIn(detail: SkillFitDetail, stepId: string): StepState | null {
  const s = detail.perStep?.find(p => p.id === stepId)
  return s ? (s.status as StepState) : null
}

/**
 * 步骤命中率：与后端口径一致——hit 计分，miss 与 out_of_order 同计分母但不计命中，
 * skipped 与无明细不计入。
 */
export function stepHitRate(details: SkillFitDetail[], stepId: string): number | null {
  let hit = 0
  let total = 0
  for (const d of details) {
    const st = stepStatusIn(d, stepId)
    if (st === 'hit') { hit++; total++ } else if (st === 'miss' || st === 'out_of_order') { total++ }
  }
  return total ? hit / total : null
}

/**
 * 运行列表行的摘要：未执行的步骤直接列名，顺序不符的加「(顺序)」标注。
 * 超过 3 步折叠为「等 N 步」。
 */
export function runMissSummary(detail: SkillFitDetail | undefined): string {
  if (!detail || !detail.perStep?.length) return ''
  const items = [
    ...missedStepNames(detail.perStep),
    ...outOfOrderStepNames(detail.perStep).map(n => `${n}(顺序)`),
  ]
  if (!items.length) return ''
  const total = items.length
  return total > 3 ? `${items.slice(0, 3).join('、')} 等 ${total} 步` : items.join('、')
}

/** 行的显示名（代理名优先，缺失时退化为会话短号） */
export function runTitle(row: SkillFitRow): string {
  return row.agent || (row.sessionId ? shortHash(row.sessionId) : '—')
}
