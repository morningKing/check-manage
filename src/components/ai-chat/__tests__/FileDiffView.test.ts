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
})
