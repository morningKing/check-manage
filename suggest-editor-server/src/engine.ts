import * as Y from "yjs"
import { config } from "./config.js"
import { flattenFragment, locateQuote, type BlockInfo } from "./flatten.js"
import { startAgentSession, type AgentSession } from "./agent.js"
import {
  ensureCompletionWorkspace,
  ensureWorkspace,
  readChecklistEntries,
  readRulePrompts,
  writeSnapshot,
} from "./workspace.js"
import { syncVocabulary } from "./vocabulary.js"
import { withInternalWrite } from "./internal.js"
import type { Suggestion } from "./types.js"

/** 服务端自写 Y.Doc 时使用的 transaction origin，onChange 据此过滤回环 */
export const SUGGEST_ORIGIN = { source: "local", engine: "suggest-editor" } as const

const SUGGESTIONS_MAP = "suggestions"
const ASSISTANT_MAP = "assistant"
const COMMANDS_MAP = "commands"
const FRAGMENT_NAME = "default"
const MAX_ANALYZED_HASHES = 500
const COMMAND_TTL_MS = 10 * 60_000
const COMPLETION_TIMEOUT_MS = 45_000

type CommandStatus = "pending" | "running" | "done" | "error" | "superseded"

/** 用户发起的改写任务（选中 → 右键 → 一句话指令 / 美化） */
interface RewriteCommand {
  id: string
  kind: "rewrite"
  instruction: string
  quote: string
  relFrom?: Uint8Array
  relTo?: Uint8Array
  status: CommandStatus
  replacement?: string
  suggestionId?: string
  error?: string
  createdAt: number
  updatedAt: number
}

/** 光标处的续写联想（ghost text）请求，按客户端隔离 */
interface CompleteCommand {
  id: string
  kind: "complete"
  clientId: number
  context: string
  status: CommandStatus
  replacement?: string
  error?: string
  createdAt: number
  updatedAt: number
}

type CommandValue = RewriteCommand | CompleteCommand

/** 简单的异步互斥：ACP 会话是回合制，同一会话不能并发跑两轮 */
function createMutex(): <T>(task: () => Promise<T>) => Promise<T> {
  let tail: Promise<unknown> = Promise.resolve()
  return function lock<T>(task: () => Promise<T>): Promise<T> {
    const result = tail.then(task, task)
    tail = result.then(
      () => undefined,
      () => undefined,
    )
    return result
  }
}

interface DocState {
  name: string
  ydoc: Y.Doc
  /** 分析 / 改写共用的重会话 */
  session: AgentSession | null
  sessionPromise: Promise<AgentSession> | null
  /** 续写联想专用会话（独立进程 + 独立工作区，不带工具） */
  completionSession: AgentSession | null
  completionSessionPromise: Promise<AgentSession> | null
  timer: NodeJS.Timeout | null
  commandTimer: NodeJS.Timeout | null
  running: boolean
  pending: boolean
  processingCommands: boolean
  analyzedHashes: Set<string>
  analysisLock: <T>(task: () => Promise<T>) => Promise<T>
  completionLock: <T>(task: () => Promise<T>) => Promise<T>
}

const states = new Map<string, DocState>()

export function registerDocument(name: string, ydoc: Y.Doc, analyzedHashes: string[] = []): void {
  const existing = states.get(name)
  if (existing && existing.ydoc === ydoc) return
  if (existing) {
    if (existing.timer) clearTimeout(existing.timer)
    if (existing.commandTimer) clearTimeout(existing.commandTimer)
    existing.session?.dispose()
    existing.completionSession?.dispose()
  }
  states.set(name, {
    name,
    ydoc,
    session: null,
    sessionPromise: null,
    completionSession: null,
    completionSessionPromise: null,
    timer: null,
    commandTimer: null,
    running: false,
    pending: false,
    processingCommands: false,
    analyzedHashes: new Set(analyzedHashes),
    analysisLock: createMutex(),
    completionLock: createMutex(),
  })
}

/** 已分析块 hash（onStoreDocument 落盘用，跨重启复用缓存） */
export function getAnalyzedHashes(name: string): string[] {
  return [...(states.get(name)?.analyzedHashes ?? [])]
}

/** 文档加载/重启后复位状态条，避免残留「正在分析…」 */
export function resetAssistantState(name: string): void {
  const state = states.get(name)
  if (!state) return
  setAssistantState(state.ydoc, { state: "idle", hint: null, error: null })
}

export function scheduleAnalysis(name: string, delay = config.debounceMs): void {
  const state = states.get(name)
  if (!state) return
  if (state.timer) clearTimeout(state.timer)
  state.timer = setTimeout(() => {
    state.timer = null
    void run(state)
  }, delay)
}

export function scheduleCommands(name: string, delay = 150): void {
  const state = states.get(name)
  if (!state) return
  if (state.commandTimer) clearTimeout(state.commandTimer)
  state.commandTimer = setTimeout(() => {
    state.commandTimer = null
    void processCommands(state)
  }, delay)
}

/** 文档加载后的会话预热：把「进程启动 + 配置加载」成本提前到用户打字期间 */
export function warmUpSessions(name: string): void {
  const state = states.get(name)
  if (!state) return
  void getSession(state).catch(() => {})
  void getCompletionSession(state).catch(() => {})
}

/**
 * 重启/重连后的工作区恢复（续跑上次没做完的事）：
 * - 改写命令是文档级的，pending 的照常续跑（结果照旧以建议卡片落地）；
 * - 续写联想命令是按客户端 clientID 隔离的，重载后客户端换了新的 Y.Doc/clientID，
 *   旧请求永远无人认领，直接标记 superseded 回收，防止白白跑一轮 LLM。
 */
export function resumePendingCommands(name: string): void {
  const state = states.get(name)
  if (!state) return
  const map = state.ydoc.getMap(COMMANDS_MAP)
  const staleCompletions: string[] = []
  let hasPendingRewrite = false
  map.forEach((raw, id) => {
    const value = raw as CommandValue | undefined
    if (!value || value.status !== "pending") return
    if (value.kind === "rewrite") hasPendingRewrite = true
    else staleCompletions.push(id)
  })
  for (const id of staleCompletions) markCommand(state, id, { status: "superseded" })
  if (hasPendingRewrite) scheduleCommands(name, 500)
}

export function disposeDocument(name: string): void {
  const state = states.get(name)
  if (!state) return
  if (state.timer) clearTimeout(state.timer)
  if (state.commandTimer) clearTimeout(state.commandTimer)
  state.session?.dispose()
  state.completionSession?.dispose()
  states.delete(name)
}

/* ------------------------------- 基础工具 ------------------------------- */

function rememberHash(state: DocState, hash: string): void {
  state.analyzedHashes.add(hash)
  while (state.analyzedHashes.size > MAX_ANALYZED_HASHES) {
    const oldest = state.analyzedHashes.values().next().value
    if (oldest === undefined) break
    state.analyzedHashes.delete(oldest)
  }
}

function setAssistantState(ydoc: Y.Doc, patch: Record<string, unknown>): void {
  withInternalWrite(() => {
    ydoc.transact(() => {
      const map = ydoc.getMap(ASSISTANT_MAP)
      for (const [key, value] of Object.entries(patch)) {
        if (value === undefined || value === null) map.delete(key)
        else map.set(key, value)
      }
    }, SUGGEST_ORIGIN)
  })
}

function suggestionEntries(ydoc: Y.Doc): Array<{ id: string; quote: string; replacement: string }> {
  const out: Array<{ id: string; quote: string; replacement: string }> = []
  ydoc.getMap(SUGGESTIONS_MAP).forEach((raw, id) => {
    const value = raw as { quote?: unknown; replacement?: unknown }
    if (typeof value.quote === "string") {
      out.push({ id, quote: value.quote, replacement: String(value.replacement ?? "") })
    }
  })
  return out
}

function stripFences(text: string): string {
  return text
    .replace(/^\s*```[a-zA-Z]*\s*/m, "")
    .replace(/```\s*$/m, "")
    .trim()
}

function sanitizeCompletion(text: string): string {
  return stripFences(text)
    .replace(/^["'「『]+|["'」』]+$/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 60)
}

/** 清理：过期 / 锚点已死 / 原文已不存在的建议 */
function pruneSuggestions(ydoc: Y.Doc, flatText: string): void {
  const map = ydoc.getMap(SUGGESTIONS_MAP)
  const now = Date.now()
  const toDelete: string[] = []
  map.forEach((raw, id) => {
    const value = raw as { createdAt?: unknown; relFrom?: unknown; quote?: unknown }
    const expired = typeof value.createdAt === "number" && now - value.createdAt > config.suggestionTtlMs
    const gone = typeof value.quote === "string" && !flatText.includes(value.quote)
    let dead = false
    if (value.relFrom instanceof Uint8Array) {
      try {
        dead = Y.createAbsolutePositionFromRelativePosition(Y.decodeRelativePosition(value.relFrom), ydoc) === null
      } catch {
        dead = true
      }
    } else {
      dead = true
    }
    if (expired || gone || dead) toDelete.push(id)
  })
  if (toDelete.length === 0) return
  withInternalWrite(() => {
    ydoc.transact(() => {
      for (const id of toDelete) map.delete(id)
    }, SUGGEST_ORIGIN)
  })
}

/**
 * 把 agent 返回的 quote 锚回 CRDT：定位 → 相对位置 → 写入 Y.Map。
 * dirtyHashes：本次分析涉及的块，定位时优先在这些块内查找。
 * options.extra：附加到建议条目上的字段（如 kind:"rewrite"）。
 * options.allowOverlap：允许锚在已被其它建议占用的位置（用户主动发起的改写需要）。
 * 返回实际创建的建议 id 列表。
 */
function applySuggestions(
  ydoc: Y.Doc,
  suggestions: Suggestion[],
  dirtyHashes: Set<string>,
  options: { extra?: Record<string, unknown>; allowOverlap?: boolean } = {},
): string[] {
  const { extra = {}, allowOverlap = false } = options
  const flat = flattenFragment(ydoc.getXmlFragment(FRAGMENT_NAME))
  pruneSuggestions(ydoc, flat.text)

  const preferred = flat.blocks.filter((block) => dirtyHashes.has(block.hash))
  const map = ydoc.getMap(SUGGESTIONS_MAP)
  const existingKeys = new Set(suggestionEntries(ydoc).map((s) => `${s.quote}\u0000${s.replacement}`))
  const taken = new Set<number>()
  for (const entry of suggestionEntries(ydoc)) {
    const at = flat.text.indexOf(entry.quote)
    if (at >= 0) taken.add(at)
  }

  const analyzedAt = Y.encodeStateVector(ydoc)
  const createdIds: string[] = []

  withInternalWrite(() => {
    ydoc.transact(() => {
      for (const suggestion of suggestions) {
        const key = `${suggestion.quote}\u0000${suggestion.replacement}`
        if (existingKeys.has(key)) continue
        const location = locateQuote(
          flat,
          suggestion.quote,
          allowOverlap ? new Set<number>() : taken,
          preferred,
        )
        if (!location) continue // 跨格式片段 / quote 已不存在：跳过
        existingKeys.add(key)
        if (!allowOverlap) taken.add(location.flatStart)

        const relFrom = Y.encodeRelativePosition(
          Y.createRelativePositionFromTypeIndex(location.segment.yText, location.offsetFrom),
        )
        const relTo = Y.encodeRelativePosition(
          Y.createRelativePositionFromTypeIndex(location.segment.yText, location.offsetTo, -1),
        )
        const id = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
        map.set(id, {
          quote: suggestion.quote,
          replacement: suggestion.replacement,
          reason: suggestion.reason,
          severity: suggestion.severity,
          relFrom,
          relTo,
          analyzedAt,
          createdAt: Date.now(),
          ...extra,
        })
        createdIds.push(id)
      }
    }, SUGGEST_ORIGIN)
  })

  return createdIds
}

/** 找到已有的同款建议（quote + replacement 相同） */
function findExistingSuggestion(ydoc: Y.Doc, quote: string, replacement: string): string | null {
  let found: string | null = null
  ydoc.getMap(SUGGESTIONS_MAP).forEach((raw, id) => {
    if (found) return
    const value = raw as { quote?: unknown; replacement?: unknown }
    if (value.quote === quote && String(value.replacement ?? "") === replacement) found = id
  })
  return found
}

/* ------------------------------- 会话管理 ------------------------------- */

function getSession(state: DocState): Promise<AgentSession> {
  if (state.session) return Promise.resolve(state.session)
  if (!state.sessionPromise) {
    state.sessionPromise = (async () => {
      const dir = await ensureWorkspace(state.name)
      const session = startAgentSession(dir, {
        onStatus: (text) => setAssistantState(state.ydoc, { state: "analyzing", hint: text }),
      })
      state.session = session
      return session
    })()
  }
  return state.sessionPromise
}

/** 续写专用会话：独立工作区（无自定义工具），独立进程 */
function getCompletionSession(state: DocState): Promise<AgentSession> {
  if (state.completionSession) return Promise.resolve(state.completionSession)
  if (!state.completionSessionPromise) {
    state.completionSessionPromise = (async () => {
      const dir = await ensureCompletionWorkspace(state.name)
      const session = startAgentSession(dir, {})
      state.completionSession = session
      return session
    })()
  }
  return state.completionSessionPromise
}

async function withTimeout<T>(promise: Promise<T>, ms: number, onTimeout: () => void): Promise<T> {
  let timer: NodeJS.Timeout | undefined
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      onTimeout()
      reject(new Error(`agent 超时（${ms}ms）`))
    }, ms)
  })
  try {
    return await Promise.race([promise, timeout])
  } finally {
    if (timer) clearTimeout(timer)
  }
}

/* ------------------------------ 命令（改写/续写） ------------------------------ */

function markCommand(state: DocState, id: string, patch: Partial<CommandValue> & { status?: CommandStatus }): void {
  withInternalWrite(() => {
    state.ydoc.transact(() => {
      const map = state.ydoc.getMap(COMMANDS_MAP)
      const current = map.get(id) as CommandValue | undefined
      if (!current) return
      map.set(id, { ...current, ...patch, updatedAt: Date.now() } as CommandValue)
    }, SUGGEST_ORIGIN)
  })
}

function pruneCommands(state: DocState): void {
  const map = state.ydoc.getMap(COMMANDS_MAP)
  const now = Date.now()
  const toDelete: string[] = []
  map.forEach((raw, id) => {
    const value = raw as CommandValue | undefined
    if (!value) return
    if (value.status === "pending" || value.status === "running") return
    if (now - value.createdAt > COMMAND_TTL_MS) toDelete.push(id)
  })
  if (toDelete.length === 0) return
  withInternalWrite(() => {
    state.ydoc.transact(() => {
      for (const id of toDelete) map.delete(id)
    }, SUGGEST_ORIGIN)
  })
}

/** 取下一个待处理命令；同一客户端的补全只保留最新一条，旧的标记 superseded（省 token） */
function nextPendingCommand(state: DocState): { id: string; value: CommandValue } | null {
  const map = state.ydoc.getMap(COMMANDS_MAP)
  const pending: Array<{ id: string; value: CommandValue }> = []
  map.forEach((raw, id) => {
    const value = raw as CommandValue | undefined
    if (value && value.status === "pending") pending.push({ id, value })
  })
  if (pending.length === 0) return null
  pending.sort((a, b) => a.value.createdAt - b.value.createdAt)

  const completionsByClient = new Map<number, Array<{ id: string; createdAt: number }>>()
  for (const entry of pending) {
    if (entry.value.kind !== "complete") continue
    const list = completionsByClient.get(entry.value.clientId) ?? []
    list.push({ id: entry.id, createdAt: entry.value.createdAt })
    completionsByClient.set(entry.value.clientId, list)
  }
  for (const list of completionsByClient.values()) {
    if (list.length <= 1) continue
    list.sort((a, b) => a.createdAt - b.createdAt)
    for (const stale of list.slice(0, -1)) markCommand(state, stale.id, { status: "superseded" })
  }

  for (const entry of pending) {
    const current = map.get(entry.id) as CommandValue | undefined
    if (current && current.status === "pending") return { id: entry.id, value: current }
  }
  return null
}

async function runRewrite(state: DocState, id: string, command: RewriteCommand): Promise<void> {
  const session = await getSession(state)
  const prompt = [
    "请按指令改写下面这段文字。",
    `【改写指令】${command.instruction}`,
    "【原文】",
    "<<<ORIGINAL>>>",
    command.quote,
    "<<<END>>>",
    "要求：保持原意与语言风格，只提交改写后的完整文本（调用 emit_rewrite），不要调用 emit_suggestions，不要修改任何文件。",
  ].join("\n")

  const result = await withTimeout(
    state.analysisLock(() => session.analyze(prompt)),
    config.analyzeTimeoutMs,
    () => session.cancel(),
  )

  const replacement = (result.rewrites[0]?.replacement ?? stripFences(result.text)).trim()
  if (!replacement) {
    markCommand(state, id, { status: "error", error: "agent 未返回改写文本" })
    return
  }

  // 改写结果以「建议」的形式落地：复用已验证的锚定 / 接受 / 忽略链路，永不直接改正文。
  // allowOverlap：用户主动发起的改写允许与已有自动建议重叠锚定。
  const created = applySuggestions(
    state.ydoc,
    [{ quote: command.quote, replacement, reason: `改写：${command.instruction}`, severity: "info" }],
    new Set(),
    { extra: { kind: "rewrite", instruction: command.instruction }, allowOverlap: true },
  )
  if (created.length === 0) {
    // 同款建议已存在（例如用户重复发起同一改写）→ 视为成功，指向已有卡片
    const existing = findExistingSuggestion(state.ydoc, command.quote, replacement)
    if (existing) {
      markCommand(state, id, { status: "done", replacement, suggestionId: existing })
      return
    }
    markCommand(state, id, { status: "error", error: "原文已变化，未能锚定改写结果" })
    return
  }
  markCommand(state, id, { status: "done", replacement, suggestionId: created[0] })
}

async function runCompletion(state: DocState, id: string, command: CompleteCommand): Promise<void> {
  const session = await getCompletionSession(state)
  const prompt = [
    "【续写任务】根据下面的上下文，只输出接下来的一小段文字（一句话以内，≤40 字）。",
    "不要重复上下文，不要解释，不要调用任何工具。",
    "<<<CONTEXT>>>",
    command.context,
    "<<<END>>>",
  ].join("\n")

  const result = await withTimeout(
    state.completionLock(() => session.analyze(prompt)),
    Math.min(config.analyzeTimeoutMs, COMPLETION_TIMEOUT_MS),
    () => session.cancel(),
  )
  const text = sanitizeCompletion(result.text)
  if (!text) {
    markCommand(state, id, { status: "error", error: "agent 未返回续写文本" })
    return
  }
  markCommand(state, id, { status: "done", replacement: text })
}

async function processCommands(state: DocState): Promise<void> {
  if (state.processingCommands) return
  state.processingCommands = true
  try {
    pruneCommands(state)
    for (;;) {
      const next = nextPendingCommand(state)
      if (!next) break
      markCommand(state, next.id, { status: "running" } as Partial<CommandValue>)
      try {
        if (next.value.kind === "rewrite") await runRewrite(state, next.id, next.value)
        else await runCompletion(state, next.id, next.value)
      } catch (error) {
        markCommand(state, next.id, {
          status: "error",
          error: error instanceof Error ? error.message : String(error),
        })
      }
    }
  } finally {
    state.processingCommands = false
  }
}

/* ------------------------------- 自动分析 ------------------------------- */

function buildPrompt(
  dirty: BlockInfo[],
  avoid: string[],
  checklist: string[],
  snapshotPath: string,
  rulePrompts: Array<{ name: string; prompt: string }>,
): string {
  const lines = [
    `请先用 read 工具读取文件 ${snapshotPath}（绝对路径，里面是文档最近改动的 ${dirty.length} 个段落），再按下面的检查规则与检查要点找出其中最多 5 处值得修改的地方，并调用 emit_suggestions 提交建议。`,
  ]
  if (rulePrompts.length > 0) {
    lines.push("领域检查规则（逐条核对，命中在 reason 中注明规则名）：")
    for (const rule of rulePrompts) lines.push(`- [${rule.name}] ${rule.prompt}`)
  }
  if (checklist.length > 0) {
    lines.push("检查要点（逐条核对，命中才提；reason 里注明命中的要点名）：")
    for (const entry of checklist) lines.push(`- ${entry}`)
  } else {
    lines.push("若无明显问题则少提或不提：冗余啰嗦、表述歧义、逻辑跳跃、语病错别字、语气不一致。")
  }
  lines.push(
    `quote 必须从该文件的正文里逐字复制；不要引用本提示词；不要修改任何文件，不要执行命令。`,
  )
  if (avoid.length > 0) {
    lines.push(`以下片段已有待处理建议，请勿重复：${avoid.slice(0, 10).map((q) => JSON.stringify(q)).join("、")}`)
  }
  return lines.join("\n")
}

async function run(state: DocState): Promise<void> {
  if (state.running) {
    state.pending = true
    return
  }
  state.running = true
  try {
    const flattened = flattenFragment(state.ydoc.getXmlFragment(FRAGMENT_NAME))
    if (!flattened.text.trim()) {
      setAssistantState(state.ydoc, { state: "idle", error: null })
      return
    }

    pruneSuggestions(state.ydoc, flattened.text)

    // 词表可能被用户改过：每次分析前对一遍（内容无变化时零写入）
    await syncVocabulary(state.name, state.ydoc)

    // 只分析「内容 hash 从未分析过」的块：未变块直接跳过（块级缓存）
    const dirty = flattened.blocks.filter(
      (block) => block.text.trim().length > 0 && !state.analyzedHashes.has(block.hash),
    )
    const skipped = flattened.blocks.length - dirty.length

    if (dirty.length === 0) {
      setAssistantState(state.ydoc, {
        state: "idle",
        lastRunAt: Date.now(),
        lastAnalyzedBlocks: 0,
        lastSkippedBlocks: skipped,
        hint: "所有段落都已分析过，命中块级缓存",
        error: null,
      })
      return
    }

    setAssistantState(state.ydoc, {
      state: "analyzing",
      error: null,
      hint: `分析 ${dirty.length} 个变动段落（跳过 ${skipped} 个未变段落）`,
    })

    const session = await getSession(state)
    // 快照只写脏块：不进 prompt 的全文就不花 token
    const snapshotPath = await writeSnapshot(state.name, dirty.map((block) => block.text.trim()).join("\n\n"))

    const avoid = suggestionEntries(state.ydoc).map((s) => s.quote)
    // 检查要点与检查规则每轮分析前现读：后台改完，下一段输入即按新内容查
    const checklist = await readChecklistEntries()
    const rulePrompts = await readRulePrompts(state.name)
    const result = await withTimeout(
      state.analysisLock(() =>
        session.analyze(buildPrompt(dirty, avoid, checklist, snapshotPath, rulePrompts)),
      ),
      config.analyzeTimeoutMs,
      () => session.cancel(),
    )

    const dirtyHashes = new Set(dirty.map((block) => block.hash))
    const applied = applySuggestions(state.ydoc, result.suggestions, dirtyHashes)
    for (const block of dirty) rememberHash(state, block.hash)

    setAssistantState(state.ydoc, {
      state: "idle",
      lastRunAt: Date.now(),
      lastFound: result.suggestions.length,
      lastApplied: applied.length,
      lastAnalyzedBlocks: dirty.length,
      lastSkippedBlocks: skipped,
      hint: null,
      error: null,
    })
  } catch (error) {
    // 会话可能已损坏：丢弃并在下一轮重建
    state.session?.dispose()
    state.session = null
    state.sessionPromise = null
    setAssistantState(state.ydoc, {
      state: "error",
      error: error instanceof Error ? error.message : String(error),
      at: Date.now(),
    })
  } finally {
    state.running = false
    if (state.pending) {
      state.pending = false
      scheduleAnalysis(state.name, 200)
    }
  }
}
