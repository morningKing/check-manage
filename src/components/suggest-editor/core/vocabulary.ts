import pinyin from "tiny-pinyin"
import * as Y from "yjs"

export interface VocabularyEntry {
  text: string
  aliases: string[]
  source: "shared" | "builtin"
}

/** 片段占位符（相对「接受时插入的文本」的偏移） */
export interface SnippetStop {
  start: number
  end: number
}

export interface CompletionCandidate {
  /** 列表里显示的完整词条 */
  label: string
  /** 附加说明（如别名来源） */
  detail?: string
  source: "词表" | "文档" | "AI"
  /** Tab/Enter 接受时插入的文本 */
  insert: string
  /** 接受前需要删除的字符数（模糊/别名/首字母命中时为已输入前缀长度） */
  replaceLength: number
  /** label 中被命中的字符下标（用于高亮） */
  positions: number[]
  /** 匹配方式（详情面板展示） */
  matchKind: MatchKind
  /** 实际参与匹配的前缀 / 别名 / 首字母串（详情面板展示） */
  matchedText?: string
  /** 文档词出现次数（详情面板展示） */
  frequency?: number
  /** 词条别名（详情面板展示） */
  aliases?: string[]
  /** 片段占位符（接受后可用 Tab 逐个跳转） */
  stops?: SnippetStop[]
  /** 排序分（越大越靠前） */
  score: number
  /** 词表内顺序（同分时按用户定义的优先级） */
  order: number
}

export type MatchKind = "前缀" | "模糊" | "别名" | "首字母" | "全部"

/** 一个「词」的组成字符：中日韩汉字 / 字母 / 数字 / 下划线 / 连字符（标识符如 cfg-get-board 视为一个词） */
const WORD_CHAR = /[\p{Script=Han}\p{L}\p{N}_-]/u
/** 词的形式检查：整串都必须是「词字符」（过滤掉含空格的 n-gram） */
const isWordLike = (text: string): boolean => [...text].every((char) => WORD_CHAR.test(char))
/** 词边界（用于「词首匹配」加权） */
const WORD_SEPARATOR = /[\s\-_./\\，。、：；（）()【】[\]·]/
/** 含汉字（用于决定是否需要算拼音首字母） */
const HAS_CJK = /[\p{Script=Han}]/u
/** 只有 ASCII 字母的前缀才尝试拼音首字母匹配（rgzn → 人工智能） */
const ASCII_PREFIX = /^[A-Za-z]+$/

const MAX_PREFIX_SCAN = 12

/** 内置词表：服务端词表为空/未命中时的兜底（书面语常用表达） */
export const BUILTIN_LINES = [
  "综上所述",
  "与此同时",
  "值得注意的是",
  "换言之",
  "简而言之",
  "具体来说",
  "从长远看",
  "在此基础上",
  "总的来说",
  "需要指出的是",
  "人工智能|AI",
  "大语言模型|LLM",
  "实时协作",
  "协同编辑",
  "端到端",
]

export function parseVocabularyLines(lines: string[], source: VocabularyEntry["source"]): VocabularyEntry[] {
  const entries: VocabularyEntry[] = []
  for (const raw of lines) {
    const line = raw.trim()
    if (!line || line.startsWith("#")) continue
    const [text, ...aliases] = line
      .split("|")
      .map((part) => part.trim())
      .filter(Boolean)
    if (!text) continue
    entries.push({ text, aliases, source })
  }
  return entries
}

/** 合并后的词表：服务端同步来的共享词表在前（优先级更高），内置词表兜底在后 */
export function readVocabulary(ydoc: Y.Doc): VocabularyEntry[] {
  const entries: VocabularyEntry[] = []
  ydoc.getMap("vocabulary").forEach((value, key) => {
    entries.push({ text: key, aliases: Array.isArray(value) ? value.map(String) : [], source: "shared" })
  })
  entries.push(...parseVocabularyLines(BUILTIN_LINES, "builtin"))
  return entries
}

/** 取光标前最后一个「词」的前缀（连续汉字/字母/数字），最多 12 个字符 */
export function tokenPrefix(tail: string, maxLength = MAX_PREFIX_SCAN): string {
  const chars = [...tail]
  let start = chars.length
  while (start > 0 && WORD_CHAR.test(chars[start - 1])) start -= 1
  return chars.slice(Math.max(start, chars.length - maxLength)).join("")
}

/** 触发补全所需的最小前缀长度：ASCII（含连字符）词 1 个字符起，中日韩 2 个字符起 */
export function minPrefixLength(prefix: string): number {
  return /^[A-Za-z0-9_-]+$/.test(prefix) ? 1 : 2
}

export function shouldTrigger(prefix: string): boolean {
  return prefix.length >= minPrefixLength(prefix) && prefix.length > 0
}

/* ------------------------------- 片段占位符 ------------------------------- */

/** VS Code 风格：`${1:主题}` / `${2}`；用于词表里的「片段」条目 */
const SNIPPET_PATTERN = /\$\{(\d+)(?::([^}]*))?\}/g

/**
 * 解析片段占位符：返回去掉语法的纯文本 + 按编号排序的占位符区间（相对纯文本的偏移）。
 * 没有占位符时原样返回。
 */
export function parseSnippet(text: string): { plain: string; stops: SnippetStop[] } {
  const matches = text.match(SNIPPET_PATTERN)
  if (!matches || matches.length === 0) return { plain: text, stops: [] }

  const ordered: Array<{ index: number; start: number; end: number }> = []
  let plain = ""
  let cursor = 0

  for (const match of text.matchAll(SNIPPET_PATTERN)) {
    const at = match.index ?? 0
    plain += text.slice(cursor, at)
    const start = plain.length
    plain += match[2] ?? ""
    ordered.push({ index: Number(match[1]), start, end: plain.length })
    cursor = at + match[0].length
  }
  plain += text.slice(cursor)

  ordered.sort((a, b) => a.index - b.index)
  return { plain, stops: ordered.map(({ start, end }) => ({ start, end })) }
}

/* ------------------------------- 拼音首字母 ------------------------------- */

const initialsCache = new Map<string, string>()

/** 拼音首字母（带缓存）：人工智能 → rgzn。不含汉字的词条返回空串（无需拼音匹配）。 */
export function pinyinInitials(text: string): string {
  const cached = initialsCache.get(text)
  if (cached !== undefined) return cached

  let result = ""
  if (HAS_CJK.test(text)) {
    try {
      if (pinyin.isSupported()) {
        result = pinyin
          .convertToPinyin(text, " ", true)
          .split(/\s+/)
          .map((syllable) => syllable.match(/[a-z]/)?.[0] ?? "")
          .join("")
      }
    } catch {
      result = ""
    }
  }

  if (initialsCache.size > 5000) initialsCache.clear()
  initialsCache.set(text, result)
  return result
}

/* ------------------------------- 匹配与打分 ------------------------------- */

/**
 * VS Code 风格的模糊匹配打分：
 * - 顺序子序列匹配（`智应` 能命中 `人工智能应用`）；
 * - 连续匹配、词首匹配（开头 / 分隔符后 / camelCase 边界）加分；
 * - 跳过的字符扣分；整段前缀命中额外加成；短标签优先。
 * 返回命中位置（用于高亮）与分数。
 */
export function fuzzyScore(label: string, prefix: string): { score: number; positions: number[] } | null {
  if (!prefix) return { score: 0, positions: [] }

  const lowerLabel = label.toLowerCase()
  const lowerPrefix = prefix.toLowerCase()
  const positions: number[] = []
  let score = 0
  let cursor = 0
  let previousMatch = -2

  for (const char of lowerPrefix) {
    const found = lowerLabel.indexOf(char, cursor)
    if (found === -1) return null

    const previousChar = label[found - 1] ?? ""
    const currentChar = label[found] ?? ""
    const camelBoundary =
      found > 0 && previousChar === previousChar.toLowerCase() && currentChar !== currentChar.toLowerCase()
    const isWordStart = found === 0 || WORD_SEPARATOR.test(previousChar) || camelBoundary

    score += 1
    if (found === previousMatch + 1) score += 4
    if (isWordStart) score += 6
    score -= Math.min(found - cursor, 6) * 0.5

    positions.push(found)
    previousMatch = found
    cursor = found + 1
  }

  const contiguousPrefix = positions.every((position, index) => position === index)
  if (contiguousPrefix) score += 20
  score -= label.length * 0.2

  return { score, positions }
}

/**
 * 汇总匹配到的候选（VS Code 式词表补全）：
 *   1. 共享词表 → 内置词表：前缀 / 模糊（子序列）匹配，词条顺序 = 用户定义的优先级；
 *   2. 别名：输入别名（缩写、拼音首字母等）→ 整体替换成词条本体；
 *   3. 拼音首字母：`rgzn` → 人工智能（只对 ASCII 前缀尝试，结果带缓存）；
 *   4. 文档自学习词：当前文档里出现过的说法。
 * mode="prefix" 时只保留连续前缀命中（用于「先精确后模糊」的两段式搜索）；
 * prefix 为空时（Ctrl+Space）返回全部候选的开头部分。
 */
export function gatherCandidates(
  prefix: string,
  entries: VocabularyEntry[],
  docVocabulary: DocumentVocabulary,
  limit = 20,
  options: { mode?: "prefix" | "extended" } = {},
): CompletionCandidate[] {
  const extended = (options.mode ?? "extended") === "extended"
  const normalized = prefix.trim()
  if (normalized && !shouldTrigger(normalized)) return []

  const results: CompletionCandidate[] = []
  const seen = new Set<string>()
  let order = 0

  /** 词条 → 候选（含片段占位符解析：占位符偏移会折算到「实际插入的文本」上） */
  const pushEntry = (rawText: string, kind: MatchKind, meta: Partial<CompletionCandidate>): void => {
    const snippet = parseSnippet(rawText)
    const label = snippet.plain
    const contiguousPrefix = kind === "前缀" || kind === "全部"

    const inserted = contiguousPrefix ? label.slice(normalized.length) : label
    // 前缀命中时只插入剩余部分，占位符偏移要相应左移
    const shifted = contiguousPrefix ? normalized.length : 0
    const stops =
      snippet.stops.length > 0
        ? snippet.stops
            .filter((stop) => stop.end >= shifted)
            .map((stop) => ({ start: stop.start - shifted, end: stop.end - shifted }))
        : undefined

    results.push({
      label,
      source: meta.source ?? "词表",
      insert: inserted,
      replaceLength: contiguousPrefix ? 0 : normalized.length,
      positions: meta.positions ?? [],
      matchKind: kind,
      matchedText: meta.matchedText,
      detail: meta.detail,
      aliases: meta.aliases,
      stops: stops && stops.length > 0 ? stops : undefined,
      score: meta.score ?? 0,
      order: order++,
      ...(meta.frequency !== undefined ? { frequency: meta.frequency } : {}),
    })
  }

  // 1) 词表 / 内置词表
  for (const entry of entries) {
    const label = parseSnippet(entry.text).plain
    if (seen.has(label)) continue // 共享词表与内置词表可能重名：保留先出现（优先级更高）的那个

    const fuzzy = normalized ? fuzzyScore(label, normalized) : { score: 0, positions: [] }
    if (!fuzzy) continue
    if (normalized && label.length <= normalized.length) continue

    const contiguousPrefix = !normalized || fuzzy.positions.every((position, index) => position === index)
    if (!contiguousPrefix && !extended) continue

    seen.add(label)
    pushEntry(entry.text, !normalized ? "全部" : contiguousPrefix ? "前缀" : "模糊", {
      source: "词表",
      positions: fuzzy.positions,
      matchedText: normalized || undefined,
      aliases: entry.aliases,
      score: fuzzy.score + 12, // 词表整体优先于文档词
    })
  }

  // 2) 别名（缩写触发：整体替换）
  if (normalized) {
    for (const entry of entries) {
      const label = parseSnippet(entry.text).plain
      if (seen.has(label)) continue
      const alias = entry.aliases.find(
        (item) => item.length >= normalized.length && item.toLowerCase().startsWith(normalized.toLowerCase()),
      )
      if (!alias) continue
      seen.add(label)
      const aliasFuzzy = fuzzyScore(alias, normalized)
      pushEntry(entry.text, "别名", {
        source: "词表",
        detail: `别名 ${alias}`,
        positions: aliasFuzzy?.positions ?? [],
        matchedText: alias,
        aliases: entry.aliases,
        score: (aliasFuzzy?.score ?? 0) + 14,
      })
    }
  }

  // 3) 拼音首字母（只对 ASCII 前缀尝试）
  if (normalized && extended && ASCII_PREFIX.test(normalized)) {
    for (const entry of entries) {
      const label = parseSnippet(entry.text).plain
      if (seen.has(label)) continue
      const initials = pinyinInitials(label)
      if (!initials || initials.length < normalized.length) continue
      if (!initials.startsWith(normalized.toLowerCase())) continue

      seen.add(label)
      pushEntry(entry.text, "首字母", {
        source: "词表",
        matchedText: initials,
        aliases: entry.aliases,
        score: 12 - label.length * 0.2, // 介于「前缀」与「模糊」之间
      })
    }
  }

  // 4) 文档自学习词
  for (const [word, frequency] of docVocabulary.words()) {
    if (seen.has(word)) continue
    const fuzzy = normalized ? fuzzyScore(word, normalized) : null
    if (normalized && !fuzzy) continue
    if (normalized && word.length <= normalized.length) continue

    const contiguousPrefix =
      !normalized || (fuzzy as { positions: number[] }).positions.every((position, index) => position === index)
    if (normalized && !contiguousPrefix && !extended) continue

    seen.add(word)
    results.push({
      label: word,
      source: "文档",
      insert: contiguousPrefix ? word.slice(normalized.length) : word,
      replaceLength: contiguousPrefix ? 0 : normalized.length,
      positions: normalized ? (fuzzy as { positions: number[] }).positions : [],
      matchKind: !normalized ? "全部" : contiguousPrefix ? "前缀" : "模糊",
      matchedText: normalized || undefined,
      frequency,
      score: (normalized ? (fuzzy as { score: number }).score : 0) - 2,
      order: order++,
    })
  }

  results.sort((a, b) => b.score - a.score || a.order - b.order)
  return results.slice(0, limit)
}

/**
 * 文档自学习词表：两层能力
 * - continuations：「前缀 → 后续片段计数」，用于续写式提示；
 * - words：文档里出现过的「词」（ASCII 词 + 重复出现 ≥2 次的中文最长同频 n-gram），用于候选列表。
 * 文档较大时只学前 20k 字符，词表桶数有上限。
 */
export class DocumentVocabulary {
  private static readonly MAX_PREFIX = 10
  private static readonly MAX_CHUNK = 16
  private static readonly MAX_BUCKETS = 12_000
  private static readonly MAX_WORDS = 4_000
  private static readonly MAX_LEARN_CHARS = 20_000
  private static readonly MIN_GRAM_REPEAT = 2

  private buckets = new Map<string, Map<string, number>>()
  private bucketCount = 0
  private wordCounts = new Map<string, number>()

  learn(text: string): void {
    this.buckets.clear()
    this.bucketCount = 0
    this.wordCounts.clear()

    const source = text.slice(0, DocumentVocabulary.MAX_LEARN_CHARS)
    const segments = source
      .split(/[\s。！？；，、：,.!?;:（）()【】[\]]+/)
      .map((segment) => segment.trim())
      .filter((segment) => segment.length >= 2)

    // 1) 续写索引
    for (const segment of segments) {
      for (let start = 0; start < segment.length; start += 1) {
        const maxPrefix = Math.min(DocumentVocabulary.MAX_PREFIX, segment.length - start - 1)
        for (let length = 2; length <= maxPrefix; length += 1) {
          const prefix = segment.slice(start, start + length)
          const chunk = segment.slice(start + length, start + length + DocumentVocabulary.MAX_CHUNK)
          if (!chunk) continue
          let bucket = this.buckets.get(prefix)
          if (!bucket) {
            bucket = new Map()
            this.buckets.set(prefix, bucket)
            this.bucketCount += 1
            if (this.bucketCount >= DocumentVocabulary.MAX_BUCKETS) break
          }
          bucket.set(chunk, (bucket.get(chunk) ?? 0) + 1)
        }
        if (this.bucketCount >= DocumentVocabulary.MAX_BUCKETS) break
      }
    }

    // 2) 词表索引：ASCII 词（像 IDE 一样，文件里出现过的词都能补）
    for (const match of source.matchAll(/[A-Za-z][A-Za-z0-9_-]{2,}/g)) {
      this.bumpWord(match[0])
      if (this.wordCounts.size >= DocumentVocabulary.MAX_WORDS) break
    }

    // 3) 词表索引：重复出现 ≥2 次的中文 n-gram（2~8 字），只保留「最长同频词」
    const counts = new Map<string, number>()
    for (const segment of segments) {
      const chars = [...segment]
      for (let start = 0; start < chars.length; start += 1) {
        const maxLength = Math.min(8, chars.length - start)
        for (let length = 2; length <= maxLength; length += 1) {
          const gram = chars.slice(start, start + length).join("")
          counts.set(gram, (counts.get(gram) ?? 0) + 1)
        }
      }
    }

    const repeated = [...counts.entries()]
      .filter(([gram, count]) => count >= DocumentVocabulary.MIN_GRAM_REPEAT && isWordLike(gram))
      .sort((a, b) => b[0].length - a[0].length) // 长词在前，便于剔除前缀冗余
    const kept: Array<[string, number]> = []
    for (const [gram, count] of repeated) {
      // 若有「同频且更长」的词以它为前缀，则它是冗余子串（如 一致性 ⊂ 一致性校验）
      if (kept.some(([word, wordCount]) => wordCount === count && word.startsWith(gram))) continue
      kept.push([gram, count])
    }
    for (const [gram, count] of kept) {
      this.bumpWordCount(gram, count)
      if (this.wordCounts.size >= DocumentVocabulary.MAX_WORDS) break
    }
  }

  private bumpWord(word: string): void {
    this.bumpWordCount(word, (this.wordCounts.get(word) ?? 0) + 1)
  }

  private bumpWordCount(word: string, count: number): void {
    if (this.wordCounts.size >= DocumentVocabulary.MAX_WORDS && !this.wordCounts.has(word)) return
    this.wordCounts.set(word, Math.max(this.wordCounts.get(word) ?? 0, count))
  }

  /** 文档词及出现次数（候选列表用） */
  words(): IterableIterator<[string, number]> {
    return this.wordCounts.entries()
  }

  /** 续写式提示（本地第 3 层） */
  complete(tail: string): CompletionCandidate | null {
    for (let length = Math.min(tail.length, DocumentVocabulary.MAX_PREFIX); length >= 2; length -= 1) {
      const bucket = this.buckets.get(tail.slice(-length))
      if (!bucket) continue

      let best: string | null = null
      let bestCount = 0
      for (const [chunk, count] of bucket) {
        if (count > bestCount || (count === bestCount && best !== null && chunk.length < best.length)) {
          best = chunk
          bestCount = count
        }
      }
      if (best) {
        return {
          label: best,
          source: "文档",
          insert: best,
          replaceLength: 0,
          positions: [],
          matchKind: "全部",
          score: 0,
          order: 0,
        }
      }
    }
    return null
  }
}
