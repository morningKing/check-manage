import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'

// MarkdownView wraps md-editor (heavy in jsdom). Mock the module so it never
// loads md-editor; the stub just echoes the `text` prop it receives so we can
// assert exactly what FileDiffView hands it.
vi.mock('../MarkdownView.vue', () => ({
  default: { props: ['text'], template: '<div class="md-stub">{{ text }}</div>' },
}))

import FileDiffView from '../FileDiffView.vue'

const DIFF = `--- a/a.txt
+++ b/a.txt
@@ -1,3 +1,3 @@
 line1
-line2
+CHANGED
 line3
`

describe('FileDiffView', () => {
  it('renders side-by-side change rows for a modified diff', () => {
    const w = mount(FileDiffView, { props: { status: 'modified', diff: DIFF, truncated: false } })
    expect(w.findAll('.diff-row').length).toBe(3)
    expect(w.find('.diff-cell--del').text()).toContain('line2')
    expect(w.find('.diff-cell--add').text()).toContain('CHANGED')
  })

  it('renders added-file content as a code block with language from the extension', () => {
    const w = mount(FileDiffView, {
      props: { status: 'added', content: 'export const x = 1', truncated: false, filename: 'a.ts' },
    })
    const md = w.find('.md-stub').text()
    expect(md).toContain('```ts')
    expect(md).toContain('export const x = 1')
    expect(w.find('.diff-row').exists()).toBe(false)
  })

  it('uses a longer fence when the content contains backticks', () => {
    const w = mount(FileDiffView, {
      props: { status: 'added', content: 'a\n```\nb', truncated: false, filename: 'x.md' },
    })
    // inner ``` (3) forces the outer fence to 4 backticks
    expect(w.find('.md-stub').text()).toContain('````md')
  })

  it('emits an empty-language fence when filename has no extension', () => {
    const w = mount(FileDiffView, {
      props: { status: 'added', content: 'plain', truncated: false, filename: 'README' },
    })
    expect(w.find('.md-stub').text()).toMatch(/^```\nplain\n```$/)
  })

  it('shows a truncation note when truncated', () => {
    const w = mount(FileDiffView, {
      props: { status: 'added', content: 'x', truncated: true, filename: 'x.txt' },
    })
    expect(w.find('.diff-truncated').exists()).toBe(true)
  })

  it('shows a deleted placeholder for deleted status', () => {
    const w = mount(FileDiffView, { props: { status: 'deleted', truncated: false } })
    expect(w.find('.diff-empty').text()).toContain('已删除')
    expect(w.find('.diff-row').exists()).toBe(false)
  })

  it('shows a generic placeholder for null status', () => {
    const w = mount(FileDiffView, { props: { status: null, truncated: false } })
    expect(w.find('.diff-empty').text()).toContain('没有差异')
  })
})
