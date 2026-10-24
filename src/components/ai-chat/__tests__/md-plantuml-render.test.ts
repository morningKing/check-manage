/**
 * MarkdownView PlantUML 渲染测试（真实 MarkdownView + md-editor）。
 *
 * ```plantuml```/```puml``` 围栏被预处理成指向 PlantUML 服务器的 <img>
 * （~h 十六进制编码），服务不可达时降级为「源码 + 自建服务配置」卡；
 * 既有 ```svg``` 预处理与非图代码块行为不受影响。
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import MarkdownView from '../MarkdownView.vue'
import { getPlantumlServer, plantumlHex } from '../plantuml'
import { STORAGE_KEYS } from '@/utils/storage'

const DIAGRAM = '@startuml\nBob -> Alice: 你好\n@enduml'

function pumlText(lang = 'plantuml', body = DIAGRAM) {
  return `前文说明\n\n\`\`\`${lang}\n${body}\n\`\`\`\n\n后文说明`
}

beforeEach(() => localStorage.removeItem(STORAGE_KEYS.AI_CHAT_PLANTUML_SERVER))

describe('MarkdownView PlantUML 渲染', () => {
  it('```plantuml``` 围栏 → 指向服务器的 ~h hex <img>，前后文照常渲染', () => {
    const wrapper = mount(MarkdownView, { props: { text: pumlText() } })
    const img = wrapper.find('.markdown-view__puml-img')
    expect(img.exists()).toBe(true)
    expect(img.attributes('src'))
      .toBe(`${getPlantumlServer()}/svg/~h${plantumlHex(DIAGRAM)}`)
    const html = wrapper.html()
    expect(html).toContain('前文说明')
    expect(html).toContain('后文说明')
    // 源码不再以代码块形式重复出现
    expect(html).not.toContain('Bob -&gt; Alice')
  })

  it('```puml``` 别名与大小写变体同样识别', () => {
    for (const lang of ['puml', 'PlantUML', 'PUML']) {
      const wrapper = mount(MarkdownView, { props: { text: pumlText(lang) } })
      expect(wrapper.find('.markdown-view__puml-img').exists(), `lang=${lang}`).toBe(true)
      // 不 unmount：md-editor 的异步 replaceMermaid 在 nextTick 续体里读模板
      // ref，unmount 后 ref 为 null 会产生 unhandled rejection（同 md-katex
      // 测试的纪律——wrapper 留活到文件结束）。
    }
  })

  it('```svg``` 预处理不受影响（data:image/svg+xml base64）', () => {
    const text = '```svg\n<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>\n```'
    const wrapper = mount(MarkdownView, { props: { text } })
    const img = wrapper.find('.markdown-view__svg:not(.markdown-view__puml-img)')
    expect(img.exists()).toBe(true)
    expect(img.attributes('src')).toMatch(/^data:image\/svg\+xml;base64,/)
  })

  it('普通代码块与复合语言标签不误提取（无服务器 img）', () => {
    for (const lang of ['python', 'svgbob']) {
      const wrapper = mount(MarkdownView, {
        props: { text: `\`\`\`${lang}\nprint('hi')\n\`\`\`` },
      })
      expect(wrapper.find('.markdown-view__puml-img').exists(), `lang=${lang}`).toBe(false)
      expect(wrapper.find('.markdown-view__svg').exists(), `lang=${lang}`).toBe(false)
      // 同上：不 unmount（md-editor 异步续体纪律）
    }
  })

  it('流式半截围栏（未闭合）不产生 img，内容以代码块展示', () => {
    const wrapper = mount(MarkdownView, {
      props: { text: '画图：\n\n```plantuml\n@startuml\nBob -> Alice' },
    })
    expect(wrapper.find('.markdown-view__puml-img').exists()).toBe(false)
    expect(wrapper.find('.markdown-view__puml-err').exists()).toBe(false)
    expect(wrapper.html()).toContain('@startuml')
  })

  it('加载失败 → 降级卡（源码 + 自建服务输入）；改地址重试恢复 img', async () => {
    const wrapper = mount(MarkdownView, { props: { text: pumlText() } })
    // 服务不可达：img error → 降级卡
    await wrapper.find('.markdown-view__puml-img').trigger('error')
    await nextTick()
    const err = wrapper.find('.markdown-view__puml-err')
    expect(err.exists()).toBe(true)
    expect(err.text()).toContain('PlantUML 渲染失败')
    expect(err.text()).toContain('@startuml')
    // 输入框预填当前生效服务地址
    const input = err.find('input')
    expect((input.element as HTMLInputElement).value).toBe(getPlantumlServer())
    // 改为内网自建服务并重试 → img 恢复且指向新地址，覆盖写入 localStorage
    await input.setValue('http://intranet-plantuml:8080/plantuml/')
    await err.find('.markdown-view__puml-retry').trigger('click')
    await nextTick()
    expect(wrapper.find('.markdown-view__puml-err').exists()).toBe(false)
    const img = wrapper.find('.markdown-view__puml-img')
    expect(img.exists()).toBe(true)
    expect(img.attributes('src'))
      .toBe('http://intranet-plantuml:8080/plantuml/svg/~h' + plantumlHex(DIAGRAM))
    expect(JSON.parse(localStorage.getItem(STORAGE_KEYS.AI_CHAT_PLANTUML_SERVER)!))
      .toBe('http://intranet-plantuml:8080/plantuml/')
  })
})
