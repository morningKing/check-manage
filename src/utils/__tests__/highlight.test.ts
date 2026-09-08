import { describe, it, expect } from 'vitest'
import { escapeHtml, highlightHtml } from '../highlight'

describe('escapeHtml', () => {
  it('转义 XSS 相关字符', () => {
    expect(escapeHtml('<script>alert("x")</script>'))
      .toBe('&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;')
  })
  it('空串原样返回', () => {
    expect(escapeHtml('')).toBe('')
  })
})

describe('highlightHtml', () => {
  it('把所有出现的关键词包进 <mark>（大小写不敏感）', () => {
    const html = highlightHtml('JWT 鉴权 jwt 再见', 'jwt')
    expect(html).toBe('<mark>JWT</mark> 鉴权 <mark>jwt</mark> 再见')
  })

  it('中文关键词高亮', () => {
    expect(highlightHtml('讨论鉴权重构方案', '鉴权')).toBe('讨论<mark>鉴权</mark>重构方案')
  })

  it('关键词为空时返回转义后的原文，无 mark', () => {
    expect(highlightHtml('a<b>c', '')).toBe('a&lt;b&gt;c')
    expect(highlightHtml('a<b>c', '   ')).toBe('a&lt;b&gt;c')
  })

  it('未命中时返回转义原文', () => {
    expect(highlightHtml('普通文本', 'xyz')).toBe('普通文本')
  })

  it('关键词与正文都做 HTML 转义，防止 XSS', () => {
    // 正文里的尖括号被转义，不会变成真实标签
    const html = highlightHtml('<img src=x> 命中', '命中')
    expect(html).toContain('&lt;img')
    expect(html).toContain('<mark>命中</mark>')
  })

  it('关键词里的正则元字符按字面量处理', () => {
    expect(highlightHtml('a.c 和 a.c', '.')).toBe('a<mark>.</mark>c 和 a<mark>.</mark>c')
    expect(highlightHtml('100% 完成', '%')).toBe('100<mark>%</mark> 完成')
  })
})
