import { describe, it, expect } from 'vitest'
import { searchModeTransition } from '../searchMode'

describe('searchModeTransition', () => {
  const base = { hasAiFilter: false, hasMongoQuery: false }

  it('keyword <- ai: 有 AI 筛选时清 AI', () => {
    expect(searchModeTransition('ai', 'keyword', { ...base, hasAiFilter: true }))
      .toStrictEqual({ clearAi: true, clearMongo: false })
  })

  it('keyword <- mongo: 有 Mongo 查询时清 Mongo', () => {
    expect(searchModeTransition('mongo', 'keyword', { ...base, hasMongoQuery: true }))
      .toStrictEqual({ clearAi: false, clearMongo: true })
  })

  it('keyword <- mongo: 无生效查询时无副作用', () => {
    expect(searchModeTransition('mongo', 'keyword', base))
      .toStrictEqual({ clearAi: false, clearMongo: false })
  })

  it('-> ai: 有游离 Mongo 查询且无 AI 筛选时清 Mongo', () => {
    expect(searchModeTransition('keyword', 'ai', { ...base, hasMongoQuery: true }))
      .toStrictEqual({ clearAi: false, clearMongo: true })
  })

  it('-> ai: 已有 AI 筛选则不清 Mongo（AI 复用 activeMongoQuery）', () => {
    expect(searchModeTransition('keyword', 'ai', { hasAiFilter: true, hasMongoQuery: true }))
      .toStrictEqual({ clearAi: false, clearMongo: false })
  })

  it('-> mongo: 有 AI 筛选时清 AI', () => {
    expect(searchModeTransition('ai', 'mongo', { hasAiFilter: true, hasMongoQuery: true }))
      .toStrictEqual({ clearAi: true, clearMongo: false })
  })

  it('-> ai: 从 mongo 直接切换，清掉游离 Mongo 查询', () => {
    expect(searchModeTransition('mongo', 'ai', { ...base, hasMongoQuery: true }))
      .toStrictEqual({ clearAi: false, clearMongo: true })
  })

  it('同模式切换：无副作用', () => {
    expect(searchModeTransition('keyword', 'keyword', base))
      .toStrictEqual({ clearAi: false, clearMongo: false })
  })


})
