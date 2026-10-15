import { describe, it, expect } from 'vitest'
import {
  classifyHealth, collectSteps, missedStepNames, runMissSummary, stepHitRate, stepStatusIn,
} from '../fitView'
import type { SkillFitDefinitionSummary, SkillFitDetail, FitPerStep } from '@/api/aiSkills'

function def(over: Partial<SkillFitDefinitionSummary> = {}): SkillFitDefinitionSummary {
  return {
    defKind: 'skill', defName: 'demo', tasks: 0, fitRate: null, avgScore: null,
    partialCount: 0, divergedCount: 0, noStepsCount: 0, latestStatus: null,
    versions: 1, latestLabel: null, latestHash: null, lastActivity: null,
    ...over,
  }
}

function step(id: string, status: FitPerStep['status'], name = id): FitPerStep {
  return { id, name, status, evidence: [] }
}

function run(attemptId: string, perStep: FitPerStep[]): SkillFitDetail {
  return {
    id: 'r_' + attemptId, attemptId, sessionId: 's', agent: null, defKind: 'skill',
    defName: 'demo', defHash: null, stepsTotal: perStep.length, stepsHit: 0, score: 0,
    status: 'partial', computedAt: null, perStep, diagnosis: null,
  }
}

describe('classifyHealth', () => {
  it('no runs → idle', () => {
    expect(classifyHealth(def()).key).toBe('idle')
  })
  it('按最近一次运行判定，而不是历史累计', () => {
    // 历史上偏离过 3 次，但最近一次符合 → 正常
    const h = classifyHealth(def({ tasks: 5, divergedCount: 3, latestStatus: 'fit' }))
    expect(h.key).toBe('ok')
  })
  it('最近一次偏离 → diverged（排序最前）', () => {
    const h = classifyHealth(def({ tasks: 2, latestStatus: 'diverged' }))
    expect(h.key).toBe('diverged')
    expect(h.rank).toBe(0)
  })
  it('reordered 归入部分符合', () => {
    expect(classifyHealth(def({ tasks: 1, latestStatus: 'reordered' })).key).toBe('partial')
  })
  it('最近一次 no_steps → 未配置步骤', () => {
    const h = classifyHealth(def({ tasks: 4, noStepsCount: 4, latestStatus: 'no_steps' }))
    expect(h.key).toBe('unconfigured')
    expect(h.label).toBe('未配置步骤')
  })
  it('no_trace 等非业务偏离保持中性标签，不冒充偏离', () => {
    const h = classifyHealth(def({ tasks: 1, latestStatus: 'no_trace' }))
    expect(h.key).toBe('ok')
    expect(h.tone).toBe('muted')
    expect(h.label).toBe('无执行记录')
  })
})

describe('step matrix helpers', () => {
  const r1 = run('a1', [step('clone', 'hit', '克隆仓库'), step('deploy', 'miss', '部署')])
  const r2 = run('a2', [step('clone', 'hit', '克隆仓库'), step('deploy', 'hit', '部署')])
  const r3 = run('a3', [])

  it('collectSteps 取第一个带明细的运行，保持定义顺序与名称', () => {
    expect(collectSteps([r3, r1, r2])).toEqual([
      { id: 'clone', name: '克隆仓库' },
      { id: 'deploy', name: '部署' },
    ])
  })

  it('stepHitRate 只统计有明细的运行，无明细不计入分母', () => {
    expect(stepHitRate([r1, r2, r3], 'deploy')).toBe(0.5)
    expect(stepHitRate([r1, r2, r3], 'clone')).toBe(1)
    expect(stepHitRate([r3], 'clone')).toBeNull()
  })

  it('顺序不符：计入分母但不计命中，与后端口径一致', () => {
    const r4 = run('a5', [step('deploy', 'out_of_order', '部署')])
    expect(stepHitRate([r4, r2], 'deploy')).toBe(0.5)
    expect(runMissSummary(r4)).toBe('部署(顺序)')
  })

  it('stepStatusIn 对缺失步骤返回 null（矩阵里画空白格）', () => {
    expect(stepStatusIn(r3, 'clone')).toBeNull()
    expect(stepStatusIn(r1, 'deploy')).toBe('miss')
  })

  it('missedStepNames / runMissSummary 给出未执行步骤的通俗文案', () => {
    expect(missedStepNames(r1.perStep)).toEqual(['部署'])
    expect(runMissSummary(r1)).toBe('部署')
    expect(runMissSummary(r2)).toBe('')
    expect(runMissSummary(undefined)).toBe('')
    const many = run('a4', ['a', 'b', 'c', 'd'].map(id => step(id, 'miss')))
    expect(runMissSummary(many)).toBe('a、b、c 等 4 步')
  })
})
