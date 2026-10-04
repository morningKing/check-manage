/**
 * toolbox 验收 smoke：<90s、0 LLM……除 sleep 用例外——该用例含 1 次轻量
 * LLM 消耗（模型发一条 bash sleep 指令后即结束），其余全为确定性构造。
 */
import { test, expect } from '@playwright/test'
import { cleanupBatch, countByStatus, getDetail, waitFor } from './batch-helpers'
import {
  adminTokenCached, childWorkspace, failFastBatch, readWorkspaceFile,
  sleepBatch, tag, writeWorkspaceFile,
} from './toolbox'

test.setTimeout(120_000)

test('fail-fast：未知 agent 子任务秒级 failed', async () => {
  const tk = await adminTokenCached()
  const bid = await failFastBatch(tk, { files: 2 })
  const detail = await waitFor(async () => {
    const d = await getDetail(tk, bid)
    return (d.sessions ?? []).every((s: any) => s.status === 'failed') ? d : null
  }, 90_000, 'fail-fast 子任务 failed')
  expect(countByStatus(detail)['failed']).toBe(2)
  await cleanupBatch(tk, bid)
})

test('sleep 长任务进入 running 且工作区 fs 可读写', async () => {
  const tk = await adminTokenCached()
  const bid = await sleepBatch(tk, { children: 1, sleepSec: 15 })
  try {
    await waitFor(async () => {
      const d = await getDetail(tk, bid)
      return countByStatus(d)['running'] === 1 ? d : null
    }, 60_000, 'sleep 子任务 running')
    const detail = await getDetail(tk, bid)
    const ws = childWorkspace(detail)
    writeWorkspaceFile(ws, 'outputs/toolbox-probe.txt', `probe-${tag('p')}`)
    expect(readWorkspaceFile(ws, 'outputs/toolbox-probe.txt')).toContain('probe-')
  } finally {
    await cleanupBatch(tk, bid)
  }
})
