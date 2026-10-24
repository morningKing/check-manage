/**
 * AI 会话 PlantUML 渲染支撑：围栏语言识别 + PlantUML 服务器图 URL。
 *
 * 与 mermaid/katex/echarts 不同，PlantUML 没有可离线打包的纯 JS 引擎
 * （本体是 Java），所以走通行的「源码编码进 URL → PlantUML 服务器出 SVG」
 * 方案（img 一发即渲染，无需 JS 运行时）。编码用服务器原生支持的 ~h 十六
 * 进制格式（UTF-8 字节的 hex 串）：零依赖、同步可算，md-editor 的分段渲染
 * 依然是纯 computed；代价是 URL 长度约为源码两倍，数 KB 以上的超大图可能
 * 撞服务器 URL 上限——渲染失败时 MarkdownView 的降级卡可改指向自建服务。
 *
 * 服务器地址优先级：localStorage 覆盖（内网自建，如 plantuml/plantuml-server
 * 镜像）→ 构建期 VITE_PLANTUML_SERVER → 官方公共服务。注意图表源码会作为
 * URL 发往该服务器，敏感内容的内网部署应配置自建服务。
 */
import { getStorage, setStorage, removeStorage, STORAGE_KEYS } from '@/utils/storage'

const DEFAULT_SERVER = 'https://www.plantuml.com/plantuml'

/** 识别为 PlantUML 的围栏语言标签（大小写不敏感）。 */
const PUML_LANGS = new Set(['plantuml', 'puml'])

export function isPlantumlLang(lang: string): boolean {
  return PUML_LANGS.has((lang || '').trim().toLowerCase())
}

/** 解析当前生效的 PlantUML 服务器基址（归一掉尾斜杠）。 */
export function getPlantumlServer(): string {
  const override = getStorage<string>(STORAGE_KEYS.AI_CHAT_PLANTUML_SERVER, '').trim()
  const base = override || import.meta.env.VITE_PLANTUML_SERVER || DEFAULT_SERVER
  return base.replace(/\/+$/, '')
}

/** 覆盖 PlantUML 服务器基址；传空白串清除覆盖、回落默认。 */
export function setPlantumlServer(url: string): void {
  const v = url.trim()
  if (v) setStorage(STORAGE_KEYS.AI_CHAT_PLANTUML_SERVER, v)
  else removeStorage(STORAGE_KEYS.AI_CHAT_PLANTUML_SERVER)
}

/** PlantUML ~h 编码：源码 UTF-8 字节流的 hex 串。 */
export function plantumlHex(code: string): string {
  const bytes = new TextEncoder().encode(code)
  let hex = ''
  for (let i = 0; i < bytes.length; i++) hex += bytes[i].toString(16).padStart(2, '0')
  return hex
}

/** 围栏源码 → PlantUML 服务器 SVG 渲染 URL。 */
export function plantumlSvgUrl(code: string): string {
  return `${getPlantumlServer()}/svg/~h${plantumlHex(code)}`
}
