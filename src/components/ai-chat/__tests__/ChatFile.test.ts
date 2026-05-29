import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ChatFile from '@/components/ai-chat/ChatFile.vue'

const stubs = { ElIcon: { template: '<i><slot/></i>' }, Document: true }

describe('ChatFile', () => {
  it('renders an <img> for an image file (clickable to open full)', () => {
    const w = mount(ChatFile, {
      props: { name: 'diagram.svg', src: '/dl?path=diagram.svg' },
      global: { stubs },
    })
    const img = w.find('img')
    expect(img.exists()).toBe(true)
    expect(img.attributes('src')).toBe('/dl?path=diagram.svg')
    expect(w.find('a').attributes('href')).toBe('/dl?path=diagram.svg')
    expect(w.text()).toContain('diagram.svg')
  })

  it('renders a chip for a non-image file', () => {
    const w = mount(ChatFile, {
      props: { name: 'report.pdf', src: '/dl?path=report.pdf' },
      global: { stubs },
    })
    expect(w.find('img').exists()).toBe(false)
    expect(w.find('.file-chip').exists()).toBe(true)
    expect(w.text()).toContain('report.pdf')
  })

  it('falls back to a chip when the image fails to load', async () => {
    const w = mount(ChatFile, {
      props: { name: 'broken.png', src: '/dl?path=broken.png' },
      global: { stubs },
    })
    expect(w.find('img').exists()).toBe(true)
    await w.find('img').trigger('error')
    expect(w.find('img').exists()).toBe(false)
    expect(w.find('.file-chip').exists()).toBe(true)
  })
})
