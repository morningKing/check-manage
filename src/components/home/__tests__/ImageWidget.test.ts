import { describe, it, expect, beforeAll } from 'vitest'
import { mount } from '@vue/test-utils'
import ImageWidget from '../ImageWidget.vue'

beforeAll(() => {
  localStorage.setItem('check-manage:token', JSON.stringify('test-token'))
})

describe('ImageWidget', () => {
  it('renders the image with authed data-files url', () => {
    const w = mount(ImageWidget, {
      props: { content: { imageUrl: '/api/data-files/abc/download' }, title: '截图' },
    })
    const img = w.find('img.image-widget__img')
    expect(img.exists()).toBe(true)
    expect((img.element as HTMLImageElement).src).toContain('access_token=')
    expect((img.element as HTMLImageElement).alt).toBe('截图')
  })

  it('shows empty state when no imageUrl', () => {
    const w = mount(ImageWidget, { props: { content: { imageUrl: '' } } })
    expect(w.find('.image-widget__empty').exists()).toBe(true)
    expect(w.text()).toContain('未配置图片')
  })

  it('wraps image in link when content.link set', () => {
    const w = mount(ImageWidget, {
      props: { content: { imageUrl: '/api/data-files/abc/download', link: 'https://example.com' } },
    })
    expect(w.find('a.image-widget__img, a:has(img)').exists() || w.find('a').exists()).toBe(true)
    expect(w.find('a').attributes('href')).toBe('https://example.com')
  })
})
