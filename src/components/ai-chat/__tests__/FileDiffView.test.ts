import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
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

  it('renders added-file content in the lightweight viewer', () => {
    const w = mount(FileDiffView, { props: { status: 'added', content: 'hello\nworld\n', truncated: false } })
    expect(w.find('.diff-added-viewer').text()).toContain('hello')
    expect(w.find('.diff-row').exists()).toBe(false)
  })

  it('shows a truncation note when truncated', () => {
    const w = mount(FileDiffView, { props: { status: 'added', content: 'x', truncated: true } })
    expect(w.find('.diff-truncated').exists()).toBe(true)
  })

  it('shows a deleted placeholder for deleted status', () => {
    const w = mount(FileDiffView, { props: { status: 'deleted', truncated: false } })
    expect(w.find('.diff-empty').text()).toContain('已删除')
    expect(w.find('.diff-row').exists()).toBe(false)
  })

  it('shows a generic placeholder for null status', () => {
    const w = mount(FileDiffView, { props: { status: null, truncated: false } })
    expect(w.find('.diff-empty').text()).toContain('无法预览')
  })

  it('renders one line per added line without blank padding', () => {
    const w = mount(FileDiffView, { props: { status: 'added', content: 'a\nb\nc\n', truncated: false } })
    expect(w.findAll('.diff-added-line').length).toBe(3)
  })
})
