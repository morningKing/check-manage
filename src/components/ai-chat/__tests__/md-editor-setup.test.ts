/**
 * md-editor-setup 配置形状测试（KaTeX 告警/乱码修复）。
 *
 * 缺陷链路：未注册 katex 实例时 md-editor-v3 挂载即从 CDN 拉
 * katex@0.16.33；默认行内数学定界符含单个 `$`——正文里相隔两个 `$` 的
 * 中文被整段吃成公式渲染成乱码（貌似编码错误），且 KaTeX 默认
 * strict='warn' 对数学环境中的中文逐条打 "LaTeX-incompatible input and
 * strict mode is set to 'warn'" 控制台警告。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('md-editor-v3', () => ({ config: vi.fn() }))
vi.mock('mermaid', () => ({ default: { __mermaid: true } }))
vi.mock('echarts', () => ({ default: { __echarts: true }, init: vi.fn() }))
vi.mock('katex', () => ({ default: { renderToString: (s: string) => `<span>math:${s}</span>` } }))

import { config } from 'md-editor-v3'
import katex from 'katex'
import '../md-editor-setup'

// md-editor-v3 的 config 参数是 DeepPartial 可选形状——测试直接按 any 断言
const cfg: any = vi.mocked(config).mock.calls[0][0]

describe('md-editor-setup katex 配置', () => {
  beforeEach(() => vi.clearAllMocks())

  it('注册本地 katex 实例（阻止 CDN 自动拉取 katex@0.16.33）', () => {
    expect(cfg.editorExtensions.katex.instance).toBe(katex)
  })

  it('katexConfig 覆盖 strict:false（默认 warn 会对中文数学环境刷控制台警告）', () => {
    expect(cfg.katexConfig({ throwOnError: false, displayMode: true }))
      .toMatchObject({ throwOnError: false, displayMode: true, strict: false })
  })

  it('行内数学定界符去掉单个 $（正文 $…$ 之间的中文不再被吃成公式）', () => {
    const plugins = [
      { type: 'image', plugin: () => {}, options: { figcaption: true } },
      { type: 'katex', plugin: () => {}, options: { katexRef: { value: katex } } },
    ]
    const out = cfg.markdownItPlugins(plugins as any, { editorId: 'test' })
    const katexPlugin = out.find((p: any) => p.type === 'katex') as any
    expect(katexPlugin.options.inlineDelimiters).toEqual([
      { open: '$$', close: '$$' },
      { open: '\\(', close: '\\)' },
    ])
    // 不含单 $ 定界符
    expect(katexPlugin.options.inlineDelimiters.some(
      (d: any) => d.open === '$')).toBe(false)
    // 非 katex 插件原样透传（同一引用，不被改写）
    expect(out.find((p: any) => p.type === 'image')).toBe(plugins[0])
    // block 定界符默认保留（$$…$$ 与 \[…\]）
    expect(katexPlugin.options.blockDelimiters).toBeUndefined()
  })
})
