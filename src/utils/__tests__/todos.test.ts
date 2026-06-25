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
