/**
 * PlantUML 渲染支撑单测：围栏语言识别 / ~h hex 编码 / 服务器地址解析。
 *
 * PlantUML 没有可离线打包的 JS 引擎，AI 会话走「源码编码进 URL → PlantUML
 * 服务器出 SVG」。~h 格式要求源码的 UTF-8 字节 hex 串，这里用已知向量钉死
 * 编码正确性（含中文多字节场景），并验证内网自建服务的覆盖链路。
 */
import { describe, it, expect, beforeEach } from 'vitest'
import {
  isPlantumlLang, plantumlHex, plantumlSvgUrl, getPlantumlServer, setPlantumlServer,
} from '../plantuml'
import { STORAGE_KEYS } from '@/utils/storage'

beforeEach(() => localStorage.removeItem(STORAGE_KEYS.AI_CHAT_PLANTUML_SERVER))

describe('isPlantumlLang', () => {
  it('识别 plantuml/puml（大小写不敏感），不误伤其他语言', () => {
    expect(isPlantumlLang('plantuml')).toBe(true)
    expect(isPlantumlLang('PlantUML')).toBe(true)
    expect(isPlantumlLang('puml')).toBe(true)
    expect(isPlantumlLang(' svg ')).toBe(false)
    expect(isPlantumlLang('uml')).toBe(false)
    expect(isPlantumlLang('mermaid')).toBe(false)
    expect(isPlantumlLang('')).toBe(false)
  })
})

describe('plantumlHex', () => {
  it('编码为 UTF-8 字节流的 hex（ASCII 已知向量 + 中文三字节）', () => {
    expect(plantumlHex('A->B')).toBe('412d3e42')
    expect(plantumlHex('中')).toBe('e4b8ad')
    expect(plantumlHex('')).toBe('')
  })
})

describe('plantumlSvgUrl / 服务器地址解析', () => {
  it('默认官方公共服务，URL 形如 {server}/svg/~h{hex}', () => {
    expect(getPlantumlServer()).toBe('https://www.plantuml.com/plantuml')
    expect(plantumlSvgUrl('A->B'))
      .toBe('https://www.plantuml.com/plantuml/svg/~h412d3e42')
  })

  it('localStorage 覆盖服务地址（内网自建），尾斜杠归一', () => {
    setPlantumlServer('http://intranet-plantuml:8080/plantuml/')
    expect(getPlantumlServer()).toBe('http://intranet-plantuml:8080/plantuml')
    // @startuml\nA->B\n@enduml 的完整 UTF-8 hex（startuml 结尾 l=6c 勿漏）
    expect(plantumlSvgUrl('@startuml\nA->B\n@enduml'))
      .toBe('http://intranet-plantuml:8080/plantuml/svg/~h407374617274756d6c0a412d3e420a40656e64756d6c')
  })

  it('传空白串清除覆盖、回落默认', () => {
    setPlantumlServer('http://x/y')
    expect(getPlantumlServer()).toBe('http://x/y')
    setPlantumlServer('  ')
    expect(getPlantumlServer()).toBe('https://www.plantuml.com/plantuml')
  })
})
