import fs from "node:fs/promises"
import path from "node:path"
import * as Y from "yjs"
import { config } from "./config.js"
import { workspaceDir } from "./workspace.js"
import { withInternalWrite } from "./internal.js"

/**
 * 词表补全的数据源（服务端侧）：
 * 每个文档一个 `词表.txt`（用户可直接编辑），服务端解析后写入文档的
 * `Y.Map("vocabulary")`（key=词条，value=别名数组）——随 Y.Doc 同步给所有客户端，
 * 并随文档一起持久化。客户端用它做「词表优先」的即时补全。
 */
export const VOCABULARY_MAP = "vocabulary"

/** 服务端写词表用的 origin（onChange 会忽略，避免触发分析） */
const VOCAB_ORIGIN = { source: "local", vocabulary: true } as const

export const DEFAULT_VOCABULARY = `# 补全词表（每行一个词条，可直接编辑本文件，保存后再输入一个字符即生效）
#
# 用法：
#   词条                  输入前缀即联想出完整词条（Tab 接受）
#   词条|别名              输入别名会把别名替换成词条本体（适合缩写 / 术语）
#   片段                 词条正文里写 \${1:默认值} / \${2}：接受后光标选中占位符，Tab 逐个跳转
#   # 开头的行是注释，空行忽略
#
# 匹配方式（都无需额外配置）：
#   前缀      「综上」→ 综上所述
#   模糊      智应 → 人工【智】能【应】用（子序列，命中字符高亮）
#   拼音首字母 rgzn → 人工智能
#   别名      AI → 人工智能
#
# 注意：片段是「词条正文」，不要写成 词条|片段 的形式（半角 | 是别名分隔符）；
#       片段正文里如需竖线请用全角 ｜。
#
# 示例（可按你的项目替换）：
综上所述
与此同时
值得注意的是
换言之
简而言之
具体来说
从长远看
在此基础上
总的来说
首先
其次
最后
人工智能|AI
大语言模型|LLM
知识库
实时协作
协同编辑
端到端
迭代
复盘
闭环
对齐
落地
主题：\${1:议题} 时间：\${2:日期} 结论：\${3:待补充}
本周完成：\${1:事项}；下周计划：\${2:事项}；风险：\${3:待补充}
`

export interface VocabularyEntry {
  text: string
  aliases: string[]
}

export function vocabularyPath(docName: string): string {
  return path.join(workspaceDir(docName), "词表.txt")
}

/** 全局词表：对所有文档生效（文档自己的 词表.txt 优先级更高） */
export function globalVocabularyPath(): string {
  return path.join(config.dataDir, "词表-全局.txt")
}

const GLOBAL_VOCABULARY_TEMPLATE = `# 全局词表：这里的词条对所有文档生效（文档自己的 词表.txt 优先级更高）
# 格式与 词表.txt 相同：词条 / 词条|别名 / 片段（词条正文里写 \${1:默认值}）
`

export async function ensureGlobalVocabularyFile(): Promise<string> {
  const file = globalVocabularyPath()
  try {
    await fs.access(file)
  } catch {
    await fs.mkdir(path.dirname(file), { recursive: true })
    await fs.writeFile(file, GLOBAL_VOCABULARY_TEMPLATE, "utf8")
  }
  return file
}

async function readVocabularyFile(file: string): Promise<VocabularyEntry[]> {
  try {
    return parseVocabulary(await fs.readFile(file, "utf8"))
  } catch {
    return []
  }
}

export async function ensureVocabularyFile(docName: string): Promise<string> {
  const file = vocabularyPath(docName)
  try {
    await fs.access(file)
  } catch {
    await fs.mkdir(path.dirname(file), { recursive: true })
    await fs.writeFile(file, DEFAULT_VOCABULARY, "utf8")
  }
  return file
}

export function parseVocabulary(content: string): VocabularyEntry[] {
  const entries: VocabularyEntry[] = []
  for (const rawLine of content.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line || line.startsWith("#")) continue
    const parts = line.split("|").map((part) => part.trim()).filter(Boolean)
    const [text, ...aliases] = parts
    if (!text) continue
    entries.push({ text, aliases })
    if (entries.length >= 500) break
  }
  return entries
}

export function readVocabularyMap(ydoc: Y.Doc): Map<string, string[]> {
  const out = new Map<string, string[]>()
  ydoc.getMap(VOCABULARY_MAP).forEach((value, key) => {
    out.set(key, Array.isArray(value) ? value.map(String) : [])
  })
  return out
}

function vocabulariesEqual(current: Map<string, string[]>, next: VocabularyEntry[]): boolean {
  if (current.size !== next.length) return false
  for (const entry of next) {
    const value = current.get(entry.text)
    if (!value || value.length !== entry.aliases.length) return false
    if (entry.aliases.some((alias, index) => value[index] !== alias)) return false
  }
  return true
}

/**
 * 读取「全局词表 + 文档词表」并同步进文档（内容无变化时不做任何写入）。
 * 合并顺序：文档词表（更具体，优先）→ 全局词表（对所有文档生效）；重名时保留先出现的。
 * 在文档加载时与每次分析前调用，因此编辑词表后只要有任意编辑就会生效。
 */
export async function syncVocabulary(docName: string, ydoc: Y.Doc): Promise<number> {
  const docFile = await ensureVocabularyFile(docName)
  await ensureGlobalVocabularyFile()

  const docEntries = await readVocabularyFile(docFile)
  const globalEntries = await readVocabularyFile(globalVocabularyPath())

  const seen = new Set<string>()
  const entries: VocabularyEntry[] = []
  for (const entry of [...docEntries, ...globalEntries]) {
    if (seen.has(entry.text)) continue
    seen.add(entry.text)
    entries.push(entry)
  }

  const current = readVocabularyMap(ydoc)
  if (vocabulariesEqual(current, entries)) return entries.length

  withInternalWrite(() => {
    ydoc.transact(() => {
      const map = ydoc.getMap(VOCABULARY_MAP)
      for (const key of current.keys()) map.delete(key)
      for (const entry of entries) map.set(entry.text, entry.aliases)
    }, VOCAB_ORIGIN)
  })

  return entries.length
}
