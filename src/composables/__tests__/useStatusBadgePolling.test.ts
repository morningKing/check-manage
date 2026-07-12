import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { ref } from 'vue'
import { useStatusBadgePolling } from '../useStatusBadgePolling'
import type { FieldConfig } from '@/types'

function statusField(pollIntervalSec?: number): FieldConfig {
  return {
    id: 'f1', label: '状态', fieldName: 'status', controlType: 'statusBadge',
    required: false, order: 1,
    statusBadgeConfig: {
      pollIntervalSec,
      options: [
        { value: 'pending', label: '待处理', icon: 'Clock' },
        { value: 'processing', label: '处理中', icon: 'Loading', animated: true },
        { value: 'done', label: '已完成', icon: 'CircleCheck', terminal: true },
      ],
    },
  }
}

describe('useStatusBadgePolling', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('没有 statusBadge 字段时不调度定时器', () => {
    const onRefresh = vi.fn()
    const { evaluateAndSchedule } = useStatusBadgePolling({ fields: ref([]), onRefresh })
    evaluateAndSchedule([{ id: '1' }])
    vi.advanceTimersByTime(10000)
    expect(onRefresh).not.toHaveBeenCalled()
  })

  it('所有行都是终态时不调度定时器', () => {
    const onRefresh = vi.fn()
    const { evaluateAndSchedule } = useStatusBadgePolling({ fields: ref([statusField(5)]), onRefresh })
    evaluateAndSchedule([{ id: '1', status: 'done' }])
    vi.advanceTimersByTime(10000)
    expect(onRefresh).not.toHaveBeenCalled()
  })

  it('存在非终态行时按配置的间隔调度一次刷新', async () => {
    const onRefresh = vi.fn().mockResolvedValue(undefined)
    const { evaluateAndSchedule } = useStatusBadgePolling({ fields: ref([statusField(5)]), onRefresh })
    evaluateAndSchedule([{ id: '1', status: 'processing' }])
    vi.advanceTimersByTime(4999)
    expect(onRefresh).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(onRefresh).toHaveBeenCalledTimes(1)
  })

  it('多个 statusBadge 字段时取最小的轮询间隔', () => {
    const onRefresh = vi.fn().mockResolvedValue(undefined)
    const fields = ref([statusField(20), statusField(5)])
    const { evaluateAndSchedule } = useStatusBadgePolling({ fields, onRefresh })
    evaluateAndSchedule([{ id: '1', status: 'processing' }])
    vi.advanceTimersByTime(5000)
    expect(onRefresh).toHaveBeenCalledTimes(1)
  })

  it('stop() 清除已调度的定时器', () => {
    const onRefresh = vi.fn()
    const { evaluateAndSchedule, stop } = useStatusBadgePolling({ fields: ref([statusField(5)]), onRefresh })
    evaluateAndSchedule([{ id: '1', status: 'processing' }])
    stop()
    vi.advanceTimersByTime(10000)
    expect(onRefresh).not.toHaveBeenCalled()
  })

  it('再次调用 evaluateAndSchedule 会先清掉上一个定时器（不重复触发）', () => {
    const onRefresh = vi.fn().mockResolvedValue(undefined)
    const { evaluateAndSchedule } = useStatusBadgePolling({ fields: ref([statusField(5)]), onRefresh })
    evaluateAndSchedule([{ id: '1', status: 'processing' }])
    vi.advanceTimersByTime(3000)
    evaluateAndSchedule([{ id: '1', status: 'processing' }]) // 重新调度，从 0 开始计时
    vi.advanceTimersByTime(3000)
    expect(onRefresh).not.toHaveBeenCalled()
    vi.advanceTimersByTime(2000)
    expect(onRefresh).toHaveBeenCalledTimes(1)
  })
})
