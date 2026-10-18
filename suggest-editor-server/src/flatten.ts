import { createHash } from "node:crypto"
import * as Y from "yjs"

export interface Segment {
  /** 在扁平化文本中的起止下标 [start, end) */
  start: number
  end: number
  /** 该片段对应的 Y.XmlText 节点（正文里一个连续文本节点） */
  yText: Y.XmlText
}

/** 顶层块：用于「只分析变动块」与块级缓存（内容 hash 不变则永不重复分析） */
export interface BlockInfo {
  start: number
  end: number
  text: string
  hash: string
}

export interface FlattenedDoc {
  text: string
  segments: Segment[]
  blocks: BlockInfo[]
}

const BLOCK_SEPARATOR = "\n\n"

function hashText(text: string): string {
  // 内容指纹（用于脏块检测与块级缓存），非安全用途
  return createHash("sha256").update(text, "utf8").digest("hex").slice(0, 16)
}

/**
 * 把 Y.XmlFragment（Tiptap Collaboration 的默认字段 "default"）扁平化为纯文本，
 * 同时给出：
 * - segments：扁平文本下标 → Y.XmlText 节点（用于把引文锚成相对位置）
 * - blocks：顶层块的文本 + 内容 hash（用于脏块检测与块级缓存）
 *
 * 只覆盖 StarterKit 常用节点；blockquote 等按普通块处理（不额外加前缀），
 * 保证 segment 下标与文本严格一致。跨格式（跨 Y.XmlText）的 quote 无法锚定，会被跳过。
 */
export function flattenFragment(fragment: Y.XmlFragment): FlattenedDoc {
  let text = ""
  const segments: Segment[] = []
  const blocks: BlockInfo[] = []

  const appendYText = (yText: Y.XmlText) => {
    const value = yText.toString()
    if (!value) return
    segments.push({ start: text.length, end: text.length + value.length, yText })
    text += value
  }

  const appendRaw = (value: string) => {
    text += value
  }

  const appendInline = (element: Y.XmlElement) => {
    for (const child of element.toArray()) {
      if (child instanceof Y.XmlText) appendYText(child)
      else if (child instanceof Y.XmlElement) {
        if (child.nodeName === "hardBreak") appendRaw("\n")
        else appendInline(child)
      }
    }
  }

  const endBlock = () => {
    if (!text.endsWith(BLOCK_SEPARATOR)) appendRaw(BLOCK_SEPARATOR)
  }

  const appendListItem = (item: Y.XmlElement) => {
    for (const child of item.toArray()) {
      if (child instanceof Y.XmlText) appendYText(child)
      else if (child instanceof Y.XmlElement) {
        if (child.nodeName === "paragraph") appendInline(child)
        else appendBlock(child) // 嵌套列表等
      }
    }
    if (!text.endsWith("\n")) appendRaw("\n")
  }

  const appendBlock = (element: Y.XmlElement) => {
    switch (element.nodeName) {
      case "heading": {
        const level = Number(element.getAttribute("level") ?? 1)
        appendRaw("#".repeat(Math.min(Math.max(level, 1), 6)) + " ")
        appendInline(element)
        endBlock()
        break
      }
      case "bulletList": {
        for (const item of element.toArray()) {
          if (item instanceof Y.XmlElement && item.nodeName === "listItem") {
            appendRaw("- ")
            appendListItem(item)
          }
        }
        endBlock()
        break
      }
      case "orderedList": {
        let index = 1
        for (const item of element.toArray()) {
          if (item instanceof Y.XmlElement && item.nodeName === "listItem") {
            appendRaw(`${index++}. `)
            appendListItem(item)
          }
        }
        endBlock()
        break
      }
      case "codeBlock": {
        appendRaw("```\n")
        appendInline(element)
        appendRaw("\n```")
        endBlock()
        break
      }
      case "horizontalRule": {
        appendRaw("---")
        endBlock()
        break
      }
      default: {
        // paragraph / blockquote / 未知块
        appendInline(element)
        endBlock()
      }
    }
  }

  for (const child of fragment.toArray()) {
    const start = text.length
    if (child instanceof Y.XmlElement) appendBlock(child)
    else if (child instanceof Y.XmlText) appendYText(child)
    const end = text.length
    if (end > start) {
      const blockText = text.slice(start, end)
      blocks.push({ start, end, text: blockText, hash: hashText(blockText) })
    }
  }

  return { text, segments, blocks }
}

export interface QuoteLocation {
  segment: Segment
  /** 相对该 Y.XmlText 的起止偏移 */
  offsetFrom: number
  offsetTo: number
  /** 在扁平文本中的绝对起止（调试用） */
  flatStart: number
  flatEnd: number
}

function tryFind(
  flattened: FlattenedDoc,
  candidate: string,
  taken: Set<number>,
  from: number,
  limit: number,
): QuoteLocation | null {
  let at = flattened.text.indexOf(candidate, from)
  while (at !== -1 && at + candidate.length <= limit) {
    if (!taken.has(at)) {
      const end = at + candidate.length
      const segment = flattened.segments.find((s) => s.start <= at && end <= s.end)
      if (segment) {
        return {
          segment,
          offsetFrom: at - segment.start,
          offsetTo: end - segment.start,
          flatStart: at,
          flatEnd: end,
        }
      }
    }
    at = flattened.text.indexOf(candidate, at + 1)
  }
  return null
}

/**
 * 在扁平文本中定位 quote（含空白规整化的兜底），返回可锚定的位置。
 * - preferred：优先在这些文本区间（通常是本次分析的脏块）内查找
 * - 跳过已占用的出现位置（taken：已存在建议的扁平起点集合）
 * - 只接受落在单个 Y.XmlText 内的命中（跨格式片段无法用单个相对位置表达）
 */
export function locateQuote(
  flattened: FlattenedDoc,
  quote: string,
  taken: Set<number> = new Set(),
  preferred: Array<{ start: number; end: number }> = [],
): QuoteLocation | null {
  const candidates = [quote, quote.trim()].filter((q, i, arr) => q.length > 0 && arr.indexOf(q) === i)

  for (const candidate of candidates) {
    for (const range of preferred) {
      const hit = tryFind(flattened, candidate, taken, range.start, range.end)
      if (hit) return hit
    }
    const hit = tryFind(flattened, candidate, taken, 0, flattened.text.length)
    if (hit) return hit
  }
  return null
}
