import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import UserMentionText from '@/components/ai-chat/UserMentionText.vue'
import type { AiFile } from '@/api/aiChat'

const files: AiFile[] = [
  { name: 'data.csv', path: 'uploads/data.csv', dir: 'uploads', size: 1024 },
  { name: 'report.json', path: 'outputs/report.json', dir: 'outputs', size: 512 },
]

// Stub the heavy markdown renderer + element-plus icon; chip layout doesn't need them.
const global = {
  stubs: {
    MarkdownView: { template: '<div class="md-stub" />' },
    ElIcon: { template: '<i class="el-icon-stub" />' },
  },
}

describe('UserMentionText', () => {
  it('renders a known @path as a clickable file chip', () => {
    const w = mount(UserMentionText, { props: { text: '分析 @uploads/data.csv 这个', files }, global })
    const chip = w.find('.file-chip')
    expect(chip.exists()).toBe(true)
    expect(chip.text()).toContain('@uploads/data.csv')
    // surrounding plain text is preserved
    expect(w.text()).toContain('分析')
    expect(w.text()).toContain('这个')
  })

  it('emits preview(path) when a chip is clicked', async () => {
    const w = mount(UserMentionText, { props: { text: '@outputs/report.json 好了', files }, global })
    await w.find('.file-chip').trigger('click')
    expect(w.emitted('preview')![0]).toEqual(['outputs/report.json'])
  })

  it('renders a chip at the start of a message', () => {
    const w = mount(UserMentionText, { props: { text: '@uploads/data.csv', files }, global })
    expect(w.find('.file-chip').exists()).toBe(true)
  })

  it('keeps agent names / unknown paths / emails as plain text (renders MarkdownView, no chip)', () => {
    const w = mount(UserMentionText, {
      props: { text: '问 @build 看 @uploads/missing.csv 联系 a@b.com', files },
      global,
    })
    expect(w.find('.file-chip').exists()).toBe(false)
    expect(w.find('.md-stub').exists()).toBe(true)
  })

  it('falls back to MarkdownView when there is no file chip', () => {
    const w = mount(UserMentionText, { props: { text: '普通消息 *斜体*', files }, global })
    expect(w.find('.file-chip').exists()).toBe(false)
    expect(w.find('.md-stub').exists()).toBe(true)
    expect(w.find('.user-mention-text').exists()).toBe(false)
  })
})
