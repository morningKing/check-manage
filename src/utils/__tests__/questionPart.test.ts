import { describe, it, expect } from 'vitest'
import { parseQuestionPart } from '../questionPart'

const Q = 'Which pet do you prefer?'
const answered = (ans: string) =>
  `User has answered your questions: "${Q}"="${ans}". You can now continue with the user's answers in mind.`
const RESULT_ONE = answered('Dog')

function part(overrides: Record<string, unknown> = {}) {
  return {
    type: 'tool_use',
    name: 'question',
    status: 'completed',
    input: {
      questions: [
        {
          question: 'Which pet do you prefer?',
          header: 'Pet',
          options: [
            { label: 'Dog', description: 'loyal' },
            { label: 'Cat', description: 'independent' },
          ],
        },
      ],
    },
    result: RESULT_ONE,
    durationMs: 48306,
    ...overrides,
  }
}

describe('parseQuestionPart', () => {
  it('returns null for non-question tools', () => {
    expect(parseQuestionPart({ name: 'bash', input: { questions: [] } })).toBeNull()
    expect(parseQuestionPart(null)).toBeNull()
  })

  it('returns null when there are no usable questions', () => {
    expect(parseQuestionPart({ name: 'question', input: { questions: [] } })).toBeNull()
    expect(parseQuestionPart({ name: 'question', input: {} })).toBeNull()
    expect(parseQuestionPart({ name: 'question', input: { questions: [{ header: 'h' }] } })).toBeNull()
  })

  it('parses questions from the tool input', () => {
    const v = parseQuestionPart(part())
    expect(v).not.toBeNull()
    expect(v!.questions).toHaveLength(1)
    expect(v!.questions[0].header).toBe('Pet')
    expect(v!.questions[0].options).toHaveLength(2)
    expect(v!.status).toBe('answered')
    expect(v!.durationMs).toBe(48306)
  })

  it('highlights the chosen option for a single-select answer', () => {
    const v = parseQuestionPart(part())!
    expect(v!.answers[0].labels).toEqual(['Dog'])
    expect(v!.answers[0].custom).toBeNull()
    expect(v!.answers[0].raw).toBe('Dog')
  })

  it('highlights every chosen option for a multi-select answer', () => {
    const result = answered('Dog, Cat')
    const v = parseQuestionPart(part({ result }))!
    expect(v.answers[0].labels).toEqual(['Dog', 'Cat'])
    expect(v.answers[0].custom).toBeNull()
  })

  it('treats an answer matching no option as a custom ("其他") answer', () => {
    const result = answered('我想养仓鼠')
    const v = parseQuestionPart(part({ result }))!
    expect(v.answers[0].labels).toEqual([])
    expect(v.answers[0].custom).toBe('我想养仓鼠')
  })

  it('recovers a custom answer that itself contains ", "', () => {
    const result = answered('苹果, 橘子')
    const v = parseQuestionPart(part({ result }))!
    expect(v.answers[0].labels).toEqual([])
    expect(v.answers[0].custom).toBe('苹果, 橘子')
  })

  it('does not highlight an option whose label merely appears inside a custom answer', () => {
    const result = answered('Dog looks fine')
    const v = parseQuestionPart(part({ result }))!
    expect(v.answers[0].labels).toEqual([])
    expect(v.answers[0].custom).toBe('Dog looks fine')
  })

  it('splits answers across multiple questions', () => {
    const input = {
      questions: [
        { question: 'Q1?', header: 'H1', options: [{ label: 'A', description: '' }, { label: 'B', description: '' }] },
        { question: 'Q2?', header: 'H2', options: [{ label: 'C', description: '' }] },
      ],
    }
    const result = 'User has answered your questions: "Q1?"="A, B", "Q2?"="C". You can now continue.'
    const v = parseQuestionPart(part({ input, result }))!
    expect(v.answers).toHaveLength(2)
    expect(v.answers[0].labels).toEqual(['A', 'B'])
    expect(v.answers[1].labels).toEqual(['C'])
  })

  it('maps the literal "Unanswered" (skipped question) to 未作答', () => {
    const result = answered('Unanswered')
    const v = parseQuestionPart(part({ result }))!
    expect(v.answers[0].raw).toBeNull()
    expect(v.answers[0].labels).toEqual([])
    expect(v.answers[0].custom).toBeNull()
  })

  it('maps a rejected/aborted tool (status error, no result) to 未作答', () => {
    const v = parseQuestionPart(part({ status: 'error', result: null }))!
    expect(v.status).toBe('unanswered')
    expect(v.answers[0].raw).toBeNull()
  })

  it('keeps running questions in the running state (interactive card owns that phase)', () => {
    const v = parseQuestionPart(part({ status: 'running', result: null }))!
    expect(v.status).toBe('running')
    expect(v.questions).toHaveLength(1)
  })

  it('falls back to the result payload when input is missing', () => {
    const result = JSON.stringify({ questions: [{ question: 'Q?', header: 'H', options: [{ label: 'A', description: '' }] }] })
    const v = parseQuestionPart(part({ input: undefined, result }))!
    expect(v.questions[0].options[0].label).toBe('A')
  })
})
