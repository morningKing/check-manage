import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import QuestionResultCard from '../QuestionResultCard.vue'
import { parseQuestionPart } from '@/utils/questionPart'

const stubs = {
  'el-tag': { template: '<span class="tag"><slot /></span>' },
  'el-icon': { template: '<i><slot /></i>' },
}

const PART = {
  type: 'tool_use' as const,
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
  result: 'User has answered your questions: "Which pet do you prefer?"="Dog". ' +
    'You can now continue with the user\'s answers in mind.',
  durationMs: 48306,
}

describe('QuestionResultCard', () => {
  it('renders the question with the chosen option highlighted', () => {
    const view = parseQuestionPart(PART)!
    const w = mount(QuestionResultCard, { props: { view }, global: { stubs } })
    expect(w.text()).toContain('AI 的提问')
    expect(w.text()).toContain('已回答')
    expect(w.text()).toContain('Which pet do you prefer?')
    expect(w.text()).toContain('48.3s')
    const options = w.findAll('.qr-option')
    expect(options).toHaveLength(2)
    expect(options[0].classes()).toContain('qr-option--checked')
    expect(options[1].classes()).not.toContain('qr-option--checked')
  })

  it('renders a custom ("其他") answer as its own highlighted row', () => {
    const view = parseQuestionPart({ ...PART, result: 'User has answered your questions: "Which pet do you prefer?"="仓鼠". You can now continue.' })!
    const w = mount(QuestionResultCard, { props: { view }, global: { stubs } })
    const checked = w.findAll('.qr-option--checked')
    expect(checked).toHaveLength(1)
    expect(checked[0].text()).toContain('其他：仓鼠')
  })

  it('shows 未作答 for an aborted/rejected question instead of the JSON bubble content', () => {
    const view = parseQuestionPart({ ...PART, status: 'error', result: null })!
    const w = mount(QuestionResultCard, { props: { view }, global: { stubs } })
    expect(w.text()).toContain('未作答')
    expect(w.findAll('.qr-option--checked')).toHaveLength(0)
  })

  it('renders only a waiting hint while the question is pending', () => {
    const view = parseQuestionPart({ ...PART, status: 'running', result: null })!
    const w = mount(QuestionResultCard, { props: { view }, global: { stubs } })
    expect(w.text()).toContain('等待选择')
    expect(w.text()).toContain('AI 正在等待你的选择')
    // no option rows — the interactive card at the thread's end owns that phase
    expect(w.findAll('.qr-option')).toHaveLength(0)
  })
})
