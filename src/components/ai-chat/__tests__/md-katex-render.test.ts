/**
 * KaTeX 渲染行为测试（真实 md-editor-v3 + 本地 katex 实例）。
 *
 * 判别缺陷：默认行内定界符含单个 `$` 时，「价格 $5 与 $6 之间」中
 * “5 与”会被吃成行内公式（KaTeX 数学模式里的中文 → 乱码 + strict 警告）。
 * 修复后：单 $ 不再是定界符（原文照排），`$$…$$`/`\(…\)` 正常渲染，
 * 且数学环境含中文时不再打 LaTeX-incompatible 控制台警告（strict:false）。
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { MdPreview } from 'md-editor-v3'
// 副作用注册：本地 mermaid/echarts/katex 实例 + 定界符/strict 配置
import '../md-editor-setup'

function render(md: string): string {
  const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
  try {
    const wrapper = mount(MdPreview, {
      props: { modelValue: md, editorId: 'katex-test', language: 'zh-CN' },
    })
    return wrapper.html()
  } finally {
    warnSpy.mockRestore()
  }
}

afterEach(() => { vi.restoreAllMocks() })

describe('markdown 数学渲染（KaTeX）', () => {
  it('单个 $ 的正文照排：$ 之间的中文不再被吃成公式', () => {
    const html = render('价格 $5 与 $6 之间，另见 `$HOME` 变量。')
    expect(html).toContain('$5 与 $6')
    // 不产生任何 katex 行内公式节点
    expect(html).not.toContain('katex-inline')
    expect(html).not.toContain('katex-html')
  })

  it('$$…$$ 块级与 \\(…\\) 行内公式正常渲染（本地实例，data-processed）', () => {
    const html = render('公式：\\(E=mc^2\\)\n\n$$\\int_0^1 x\\,dx = \\frac{1}{2}$$')
    expect(html).toContain('data-processed')
    expect(html).toContain('katex')
    expect((html.match(/katex/g) ?? []).length).toBeGreaterThanOrEqual(2)
  })

  it('数学环境含中文不再打 LaTeX-incompatible 警告（strict:false）', () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    try {
      mount(MdPreview, {
        props: {
          modelValue: '$$总分 = \\alpha + 中文说明$$',
          editorId: 'katex-test-cn', language: 'zh-CN',
        },
      })
    } finally { warnSpy.mockRestore() }
    const hit = warnSpy.mock.calls.some(args =>
      String(args[0] ?? '').includes('LaTeX-incompatible'))
    expect(hit).toBe(false)
  })
})
