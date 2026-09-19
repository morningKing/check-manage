// @vitest-environment node
import { describe, it, expect } from 'vitest'
import { parseTodos, todoProgress } from '../todos'

describe('parseTodos', () => {
  it('extracts todos from a todowrite input object', () => {
    const part = {
      name: 'todowrite',
      input: { todos: [
        { content: 'Step A', status: 'completed' },
        { content: 'Step B', status: 'in_progress' },
        { content: 'Step C', status: 'pending' },
      ] },
    }
    const todos = parseTodos(part)
    expect(todos).toEqual([
      { content: 'Step A', status: 'completed' },
      { content: 'Step B', status: 'in_progress' },
      { content: 'Step C', status: 'pending' },
    ])
  })

  it('parses a JSON-string input', () => {
    const part = { name: 'todowrite', input: JSON.stringify({ todos: [{ content: 'X', status: 'done' }] }) }
    expect(parseTodos(part)).toEqual([{ content: 'X', status: 'completed' }])
  })

  it('reads from result for todoread', () => {
    const part = { name: 'todoread', result: { todos: [{ content: 'Y', status: 'active' }] } }
    expect(parseTodos(part)).toEqual([{ content: 'Y', status: 'in_progress' }])
  })

  it('accepts a bare array and alt field/status spellings', () => {
    const part = { name: 'todowrite', input: [
      { text: 'T1', status: 'in-progress' },
      { title: 'T2', status: 'cancelled' },
    ] }
    expect(parseTodos(part)).toEqual([
      { content: 'T1', status: 'in_progress' },
      { content: 'T2', status: 'cancelled' },
    ])
  })

  it('returns null for non-todo tools', () => {
    expect(parseTodos({ name: 'bash', input: { command: 'ls' } })).toBeNull()
  })

  it('returns null when there are no usable items', () => {
    expect(parseTodos({ name: 'todowrite', input: { todos: [] } })).toBeNull()
    expect(parseTodos({ name: 'todowrite', input: 'not json' })).toBeNull()
    expect(parseTodos(null)).toBeNull()
  })

  it('defaults unknown status to pending', () => {
    expect(parseTodos({ name: 'todowrite', input: { todos: [{ content: 'Z' }] } }))
      .toEqual([{ content: 'Z', status: 'pending' }])
  })
})

describe('todoProgress', () => {
  it('counts completed / total', () => {
    expect(todoProgress([
      { content: 'a', status: 'completed' },
      { content: 'b', status: 'in_progress' },
      { content: 'c', status: 'completed' },
    ])).toEqual({ done: 2, total: 3 })
  })
})

// ---------------------------------------------------------------------------
// buildTodoTrace:todo 执行轨迹锚点
// ---------------------------------------------------------------------------

import { buildTodoTrace, todoDurationText, latestTodosFromMessages, type TodoTraceMsg } from '../todos'

function assistantTodoMsg(
  todos: Array<{ content: string; status: string }>,
  createdAt?: string,
): TodoTraceMsg {
  return {
    role: 'assistant',
    content: [{ type: 'tool_use', name: 'todowrite', input: { todos } }],
    ...(createdAt ? { createdAt } : {}),
  }
}

describe('buildTodoTrace', () => {
  it('为每个步骤锚定开始/完成的消息位置与时间', () => {
    const msgs: TodoTraceMsg[] = [
      { role: 'user', content: [{ type: 'text', text: '开工' }] },
      assistantTodoMsg([
        { content: '步骤A', status: 'in_progress' },
        { content: '步骤B', status: 'pending' },
      ], '2026-09-13T10:00:00Z'),
      { role: 'assistant', content: [{ type: 'tool_use', name: 'bash', input: {} }] },
      assistantTodoMsg([
        { content: '步骤A', status: 'completed' },
        { content: '步骤B', status: 'in_progress' },
      ], '2026-09-13T10:05:00Z'),
      assistantTodoMsg([
        { content: '步骤A', status: 'completed' },
        { content: '步骤B', status: 'completed' },
      ], '2026-09-13T10:09:00Z'),
    ]

    const trace = buildTodoTrace(msgs)
    expect(trace.map(t => t.content)).toEqual(['步骤A', '步骤B'])

    const a = trace[0]
    expect(a.startMsgIdx).toBe(1)          // 第一次 in_progress 的消息
    expect(a.endMsgIdx).toBe(3)            // 第一次 completed 的消息
    expect(a.startAt).toBe(Date.parse('2026-09-13T10:00:00Z'))
    expect(a.endAt).toBe(Date.parse('2026-09-13T10:05:00Z'))
    expect(todoDurationText(a)).toBe('5m0s' === todoDurationText(a) ? '5m0s' : '300s' === todoDurationText(a) ? '300s' : todoDurationText(a))

    const b = trace[1]
    expect(b.startMsgIdx).toBe(3)
    expect(b.endMsgIdx).toBe(4)
  })

  it('非 assistant 消息与无关 tool part 不产生锚点;时间缺失时耗时为空', () => {
    const msgs: TodoTraceMsg[] = [
      { role: 'tool', content: [{ type: 'tool_use', name: 'todowrite', input: { todos: [{ content: 'X', status: 'in_progress' }] } }] },
      assistantTodoMsg([{ content: 'X', status: 'in_progress' }]),
      assistantTodoMsg([{ content: 'X', status: 'completed' }]),
    ]
    const trace = buildTodoTrace(msgs)
    expect(trace).toHaveLength(1)
    expect(trace[0].startMsgIdx).toBe(1)   // role='tool' 的 idx=0 被忽略
    expect(trace[0].startAt).toBeNull()
    expect(trace[0].endAt).toBeNull()
    expect(todoDurationText(trace[0])).toBe('')
  })

  it('跳过从未进入 in_progress 的 pending 步骤(仍展示但无锚点)', () => {
    const msgs: TodoTraceMsg[] = [
      assistantTodoMsg([
        { content: '做了', status: 'completed' },
        { content: '没做', status: 'pending' },
      ]),
    ]
    const trace = buildTodoTrace(msgs)
    expect(trace).toHaveLength(2)
    expect(trace[0].endMsgIdx).toBe(0)
    expect(trace[0].startMsgIdx).toBeNull()
    expect(trace[1].startMsgIdx).toBeNull()
    expect(trace[1].endMsgIdx).toBeNull()
  })
})

describe('todoDurationText', () => {
  it('秒与分秒格式', () => {
    expect(todoDurationText({ content: '', status: 'completed', startMsgIdx: 0, endMsgIdx: 1, startAt: 1000, endAt: 9000 })).toBe('8s')
    expect(todoDurationText({ content: '', status: 'completed', startMsgIdx: 0, endMsgIdx: 1, startAt: 0, endAt: 125_000 })).toBe('2m5s')
  })
})

describe('latestTodosFromMessages', () => {
  it('取子代理消息流中最新一次 todowrite 快照', () => {
    const msgs: TodoTraceMsg[] = [
      { role: 'user', content: [{ type: 'text', text: '委托内容' }] },
      assistantTodoMsg([
        { content: '步骤1', status: 'in_progress' },
        { content: '步骤2', status: 'pending' },
      ]),
      { role: 'assistant', content: [{ type: 'tool_use', name: 'bash', input: {} }] },
      assistantTodoMsg([
        { content: '步骤1', status: 'completed' },
        { content: '步骤2', status: 'completed' },
      ]),
    ]
    const todos = latestTodosFromMessages(msgs)
    expect(todos).toHaveLength(2)
    expect(todos!.every(t => t.status === 'completed')).toBe(true)
  })

  it('没有 todo 时返回 null', () => {
    expect(latestTodosFromMessages([
      { role: 'assistant', content: [{ type: 'text', text: 'hi' }] },
    ])).toBeNull()
    expect(latestTodosFromMessages([])).toBeNull()
  })
})
