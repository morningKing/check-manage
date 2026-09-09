/**
 * AI Chat Pinia store.
 *
 * Holds the multi-session chat state for the full-page assistant: session list,
 * per-session messages, live streaming text + reasoning, and pending uploads.
 * OpenCode sends part snapshots keyed by part id (not deltas), so we upsert
 * parts by id and only render parts belonging to an assistant message.
 */

import { defineStore } from 'pinia'
import {
  createSession, listSessions, renameSession as apiRenameSession,
  closeSession as apiCloseSession, reopenSession as apiReopenSession,
  deleteSession as apiDeleteSession, clearSession as apiClearSession,
  getMessages, sendMessage, uploadFile, uploadSkill, listFiles, getChanges, getMcpServices,
  getCommands, postCommand, abortSession, deleteFromMessage, compactSession as apiCompactSession,
  getLspFormatter,
  getPendingQuestion, replyQuestion, rejectQuestion,
  createEventStream,
  type AiMessage, type AiContentPart, type AiFile, type ChangedFile, type McpServer,
  type LspServerStatus, type FormatterStatus,
  type PaletteCommand, type StreamStatus, type AgentInfo, type QuestionRequest,
} from '@/api/aiChat'
import { parseAgentMentions } from '@/utils/agentMentions'
import { computeUsage, EMPTY_USAGE, type SessionUsage } from '@/utils/aiUsage'

interface SessionMeta {
  id: string
  title: string
  status?: string
}

interface PendingAttachment {
  name: string
  path: string
}

/** 运行中插话的排队项：回合结束后按序自动发出（见 _drainQueue）。 */
interface QueuedMessage {
  localId: string
  content: string
  paths: string[]
}

interface State {
  sessions: SessionMeta[]
  activeSessionId: string | null
  messages: Record<string, AiMessage[]>
  streaming: Record<string, boolean>
  reasoning: Record<string, string>
  thinking: Record<string, boolean>
  attachments: Record<string, PendingAttachment[]>
  /**
   * Per-session model preference for the composer dropdown.
   * Empty string / missing key → use backend default (OPENCODE_MODEL env).
   * Persisted in localStorage by AiChatView (`check-manage:ai-chat:model:<sid>`).
   */
  modelBySession: Record<string, string>
  /**
   * Per-session agent preference for the composer dropdown.
   * Empty string / missing key → use backend default agent.
   * Persisted in localStorage (`check-manage:ai-chat:agent:<sid>`).
   */
  agentBySession: Record<string, string>
  /** Cached list of subagents fetched by AiChatView. Used to resolve @ mentions on send. */
  subagents: AgentInfo[]
  outputs: Record<string, AiFile[]>
  /** 用户上传到会话 uploads/ 目录的文件（与 agent 产出分开列示，实时刷新）。 */
  uploads: Record<string, AiFile[]>
  changes: Record<string, ChangedFile[]>
  paletteItems: Record<string, { commands: PaletteCommand[]; skills: PaletteCommand[] }>
  streamStatus: Record<string, StreamStatus>
  // Concurrent uploads (file + skill) used to clobber a single boolean; this is
  // a refcount so the spinner only clears when ALL in-flight uploads finish.
  uploadingCount: number
  /**
   * OpenCode's built-in interactive multi-choice tool, currently blocking the
   * turn on a user answer. Deliberately NOT part of `messages[sid]` content —
   * it's live turn state, driven by the dedicated `question.*` SSE events (see
   * _handleEvent) plus a REST rehydrate call on session open (SSE is fire-once
   * and would otherwise lose a question asked while disconnected).
   */
  pendingQuestion: Record<string, QuestionRequest | null>
  /**
   * 运行中插话队列（TUI 的 mid-turn queueing）：streaming 时发送的消息不调
   * 接口，先挂在这里并渲染成带「排队中」标记的本地气泡；当前回合 session.idle
   * 后逐条发出。drain 点有二：session.idle 事件、SSE (重)连 open（补偿断线期
   * 错过的 idle）。
   */
  queuedBySession: Record<string, QueuedMessage[]>
  /**
   * 每会话 token 用量（F1 上下文水位线/成本感知）：由消息 meta 计算，
   * 会话打开 / 回合结束重算，message.updated 流式中就地刷新 contextTokens。
   */
  usageBySession: Record<string, SessionUsage>
  _stream: { close(): void } | null
}

let _streamingAssistantMsgId: Record<string, string | null> = {}
let _assistantMsgIds: Record<string, Set<string>> = {}
let _partIndexById: Record<string, Record<string, number>> = {}
let _reasoningByPart: Record<string, Record<string, string>> = {}
// messageID -> 真实子会话 id（来自 tool:'task' part 的 state.metadata.sessionId）
let _toolChildByMsg: Record<string, Record<string, string>> = {}
// messageID -> subtask part 的 part id（用于同消息去重/回补子会话 id）
let _subtaskPartByMsg: Record<string, Record<string, string>> = {}
// 新建会话是异步的（先走 createSession API 才切 activeSessionId）；期间用户
// 点发送会用旧 activeSessionId 把消息发进上一个会话。记录进行中的创建，
// sendUserMessage 先等它落定。
let _pendingNewSession: Promise<string> | null = null

export const useAiChatStore = defineStore('aiChat', {
  state: (): State => ({
    sessions: [],
    activeSessionId: null,
    messages: {},
    streaming: {},
    reasoning: {},
    thinking: {},
    attachments: {},
    modelBySession: {},
    agentBySession: {} as Record<string, string>,
    subagents: [] as AgentInfo[],
    outputs: {},
    uploads: {},
    changes: {} as Record<string, ChangedFile[]>,
    paletteItems: {} as Record<string, { commands: PaletteCommand[]; skills: PaletteCommand[] }>,
    streamStatus: {} as Record<string, StreamStatus>,
    uploadingCount: 0,
    pendingQuestion: {},
    queuedBySession: {} as Record<string, QueuedMessage[]>,
    usageBySession: {} as Record<string, SessionUsage>,
    _stream: null,
  }),

  getters: {
    activeMessages(state): AiMessage[] {
      return state.activeSessionId ? state.messages[state.activeSessionId] ?? [] : []
    },
    isStreaming(state): boolean {
      return state.activeSessionId ? !!state.streaming[state.activeSessionId] : false
    },
    activeAttachments(state): PendingAttachment[] {
      return state.activeSessionId ? state.attachments[state.activeSessionId] ?? [] : []
    },
    activeOutputs(state): AiFile[] {
      return state.activeSessionId ? state.outputs[state.activeSessionId] ?? [] : []
    },
    activeUploads(state): AiFile[] {
      return state.activeSessionId ? state.uploads[state.activeSessionId] ?? [] : []
    },
    activeChanges(state): ChangedFile[] {
      return state.activeSessionId ? state.changes[state.activeSessionId] ?? [] : []
    },
    activeStreamStatus(state): StreamStatus {
      return state.activeSessionId ? state.streamStatus[state.activeSessionId] ?? 'closed' : 'closed'
    },
    uploading(state): boolean {
      return state.uploadingCount > 0
    },
    activePendingQuestion(state): QuestionRequest | null {
      return state.activeSessionId ? state.pendingQuestion[state.activeSessionId] ?? null : null
    },
    activeUsage(state): SessionUsage {
      return state.activeSessionId
        ? state.usageBySession[state.activeSessionId] ?? { ...EMPTY_USAGE }
        : { ...EMPTY_USAGE }
    },
  },

  actions: {
    async loadSessions() {
      const { sessions } = await listSessions()
      this.sessions = sessions.map(s => ({ id: s.id, title: s.title, status: s.status }))
    },

    async startNewSession(projectMenuId?: string) {
      const pending = (async () => {
        const meta = await createSession(projectMenuId)
        this.sessions.unshift({ id: meta.id, title: meta.title })
        this.activeSessionId = meta.id
        this.messages[meta.id] = []
        this.streaming[meta.id] = false
        this.attachments[meta.id] = []
        this._resetStreamState(meta.id)
        const history = await getMessages(meta.id)
        this.messages[meta.id] = history.messages
        this._recomputeUsage(meta.id)
        this.loadPaletteItems(meta.id)
        this._openStream(meta.id)
        return meta.id
      })()
      _pendingNewSession = pending
      try {
        return await pending
      } finally {
        if (_pendingNewSession === pending) _pendingNewSession = null
      }
    },

    async openSession(id: string, opts: { stream?: boolean } = {}) {
      // Always (re)load palette items so a transient backend hiccup during the
      // first load doesn't leave the "/" dropdown empty forever — earlier path
      // only ran this once and the early-return then masked the failure.
      this.loadPaletteItems(id)
      if (this.activeSessionId === id && this.messages[id]) return
      this.activeSessionId = id
      this.attachments[id] = this.attachments[id] ?? []
      this.streaming[id] = this.streaming[id] ?? false
      this._resetStreamState(id)
      const history = await getMessages(id)
      this.messages[id] = history.messages
      this._recomputeUsage(id)
      this.loadFiles(id)
      this.loadChanges(id)
      this.loadPaletteItems(id)
      this.loadPendingQuestion(id)
      // Batch children are driven by the worker and viewed via polling
      // (reloadMessages). Opening an SSE stream for them would let the live
      // _upsertAssistantPart write into the poll-replaced message array at stale
      // indices — corrupting/blanking the tool bubbles. So poll-only here.
      if (opts.stream === false) this._closeStream()
      else this._openStream(id)
    },

    // Re-fetch the persisted messages for `id` and adopt them. Used to live-poll
    // a running batch child (whose work is persisted incrementally server-side
    // but isn't pushed over SSE). No-op if the session is no longer active or is
    // streaming live (interactive sessions update via SSE, not polling).
    async reloadMessages(id: string) {
      if (this.activeSessionId !== id || this.streaming[id]) return
      try {
        const history = await getMessages(id)
        // Never let a transient short/empty poll wipe what's already rendered —
        // the conversation only grows server-side (idempotent upsert), so a
        // shorter result is a hiccup, not a real shrink. (This is why the
        // bubbles could momentarily vanish during a live batch run.)
        if (history.messages.length >= (this.messages[id]?.length ?? 0)) {
          this.messages[id] = history.messages
          this._recomputeUsage(id)
        }
      } catch { /* non-fatal */ }
    },

    async loadFiles(id: string) {
      try {
        const { files } = await listFiles(id)
        // 产出文件 surfaces everything the agent generated: files written to
        // outputs/ AND files written directly under the workspace root.
        this.outputs[id] = files.filter(f => f.dir !== 'uploads')
        // 用户上传的输入文件（uploads/）单独成组，上传后实时可见。
        this.uploads[id] = files.filter(f => f.dir === 'uploads')
      } catch { /* non-fatal */ }
    },

    async loadChanges(id: string): Promise<boolean> {
      try {
        const { changes, ok } = await getChanges(id)
        // A failed/incomplete scan returns ok=false with a possibly-empty list.
        // Never let that wipe a panel that already has entries — keep the last
        // good list so a transient git error doesn't make changes "disappear".
        if (ok === false && (this.changes[id]?.length ?? 0) > 0) return false
        this.changes[id] = changes
        return ok !== false
      } catch { /* non-fatal: keep existing list */ return false }
    },

    async loadPaletteItems(id: string) {
      try {
        const { commands, skills } = await getCommands(id)
        this.paletteItems[id] = { commands, skills }
      } catch { /* non-fatal; palette shows builtin only */ }
    },
    isOpencodeCommand(id: string, name: string): boolean {
      const n = name.toLowerCase()
      return (this.paletteItems[id]?.commands ?? []).some((c) => c.name.toLowerCase() === n)
    },
    async runCommand(id: string, name: string, args: string) {
      const shown = '/' + name + (args ? ' ' + args : '')
      ;(this.messages[id] ?? (this.messages[id] = [])).push({
        id: 'local_' + Date.now(), role: 'user', content: [{ type: 'text', text: shown }],
      })
      this.streaming[id] = true
      this.thinking[id] = true
      this._resetStreamState(id)
      await postCommand(id, name, args)
    },

    async abortStreaming() {
      const sid = this.activeSessionId
      if (!sid || !this.streaming[sid]) return
      try { await abortSession(sid) } catch { /* SSE.idle clears state regardless */ }
      // optimistic UI: clear locally; session.idle event will re-affirm
      this.streaming[sid] = false
      this.thinking[sid] = false
    },

    async loadPendingQuestion(id: string) {
      try {
        const { data } = await getPendingQuestion(id)
        this.pendingQuestion[id] = data
      } catch { /* best-effort: don't block session open on this */ }
    },

    async answerPendingQuestion(sid: string, answers: string[][]) {
      const q = this.pendingQuestion[sid]
      if (!q) return
      await replyQuestion(sid, q.id, answers)
      // optimistic: don't wait for the question.replied echo (also handles it,
      // for the multi-tab case, but the local answerer sees it clear now).
      this.pendingQuestion[sid] = null
    },

    async rejectPendingQuestion(sid: string) {
      const q = this.pendingQuestion[sid]
      if (!q) return
      await rejectQuestion(sid, q.id)
      this.pendingQuestion[sid] = null
    },

    async showMcpServices() {
      const sid = this.activeSessionId
      if (!sid) return
      let servers: McpServer[] = []
      try {
        const res = await getMcpServices(sid)
        servers = res.error ? [] : res.servers
      } catch { /* leave empty; the block renders 无法获取 */ }
      ;(this.messages[sid] ?? (this.messages[sid] = [])).push({
        id: 'mcp_' + Date.now(),
        role: 'assistant',
        content: [{ type: 'mcp_services', servers }],
      })
    },

    async showLspFormatter() {
      const sid = this.activeSessionId
      if (!sid) return
      let lsp: LspServerStatus[] = []
      let formatters: FormatterStatus[] = []
      let error: string | undefined
      try {
        const res = await getLspFormatter(sid)
        lsp = res.lsp
        formatters = res.formatters
      } catch (e) {
        error = '无法获取（OpenCode 不可用）'
      }
      ;(this.messages[sid] ?? (this.messages[sid] = [])).push({
        id: 'lsp_' + Date.now(),
        role: 'assistant',
        content: [{ type: 'lsp_formatter', lsp, formatters, error }],
      })
    },

    appendMessage(id: string, msg: AiMessage) {
      ;(this.messages[id] ?? (this.messages[id] = [])).push(msg)
    },

    /** 从消息 meta 重算会话用量（上下文水位线/累计 token）。幂等。 */
    _recomputeUsage(sid: string) {
      this.usageBySession[sid] = computeUsage(this.messages[sid])
    },

    async renameSession(id: string, title: string) {
      await apiRenameSession(id, title)
      const s = this.sessions.find(x => x.id === id)
      if (s) s.title = title
    },

    async sendUserMessage(content: string) {
      if (_pendingNewSession) {
        try { await _pendingNewSession } catch { /* 创建失败就用当前会话继续 */ }
      }
      if (!this.activeSessionId) throw new Error('no active session')
      const sid = this.activeSessionId
      const pending = this.attachments[sid] ?? []
      const parts: AiContentPart[] = []
      if (content) parts.push({ type: 'text', text: content })
      for (const a of pending) parts.push({ type: 'file', name: a.name, path: a.path })

      const localId = 'local_' + Date.now()
      const paths = pending.map(a => a.path)
      this.attachments[sid] = []
      // 运行中插话（TUI mid-turn queueing）：当前回合没结束就不调发送接口，
      // 挂进队列等 session.idle 后自动发出；气泡先渲染，带「排队中」标记。
      if (this.streaming[sid]) {
        this.messages[sid].push({ id: localId, role: 'user', content: parts, queued: true })
        ;(this.queuedBySession[sid] ?? (this.queuedBySession[sid] = [])).push({ localId, content, paths })
        return
      }
      this.messages[sid].push({ id: localId, role: 'user', content: parts })
      this._beginTurn(sid)
      await this._transmitUserMessage(sid, content, paths, localId)
    },

    _beginTurn(sid: string) {
      this.streaming[sid] = true
      this.reasoning[sid] = ''
      this.thinking[sid] = true
      this._resetStreamState(sid)
    },

    async _transmitUserMessage(sid: string, content: string, paths: string[], localId: string) {
      // Per-session model preference, persisted in localStorage by AiChatView.
      // Empty string → backend falls back to OPENCODE_MODEL config (which itself
      // may be empty, in which case OpenCode picks its own default).
      const model = this.modelBySession[sid] || ''
      const agent = this.agentBySession[sid] || ''
      const known = new Set(this.subagents.map((a) => a.name))
      const agentMentions = parseAgentMentions(content, known)
      const { messageId } = await sendMessage(sid, content, paths, model, agent, agentMentions)
      // adopt the real DB id so Edit/Retry can target this row server-side
      const msg = this.messages[sid].find((m) => m.id === localId)
      if (msg && messageId) msg.id = messageId
    },

    _drainQueue(sid: string) {
      if (this.streaming[sid]) return
      const queue = this.queuedBySession[sid]
      if (!queue?.length) return
      const item = queue.shift()!
      // 排队气泡转为正常发送：去掉标记、复用本地 id（sendMessage 成功后仍会被
      // 替换成真实 DB id，与普通发送一致）。
      const msg = this.messages[sid]?.find((m) => m.id === item.localId)
      if (msg) delete msg.queued
      this._beginTurn(sid)
      this._transmitUserMessage(sid, item.content, item.paths, item.localId)
        .catch(() => { /* 发送失败：错误由 interceptor 提示，气泡保留为普通消息 */ })
    },

    async deleteFromMessage(id: string, msgId: string) {
      try { await deleteFromMessage(id, msgId) } catch { /* still trim locally */ }
      const arr = this.messages[id] ?? []
      const idx = arr.findIndex((m) => m.id === msgId)
      if (idx >= 0) this.messages[id] = arr.slice(0, idx)
      this._recomputeUsage(id)  // 被删回合的 token 不再计入累计
      this.streaming[id] = false
      this.thinking[id] = false
    },

    async retryUserMessage(msgId: string) {
      const sid = this.activeSessionId
      if (!sid) return
      const target = (this.messages[sid] ?? []).find((m) => m.id === msgId)
      if (!target) return
      const text = target.content.find((p) => p.type === 'text')?.text ?? ''
      await this.deleteFromMessage(sid, msgId)
      if (text) await this.sendUserMessage(text)
    },

    async uploadAttachment(file: File) {
      if (!this.activeSessionId) throw new Error('no active session')
      const sid = this.activeSessionId
      this.uploadingCount++
      try {
        const res = await uploadFile(sid, file)
        ;(this.attachments[sid] ?? (this.attachments[sid] = [])).push({ name: res.name, path: res.path })
        // 实时刷新文件抽屉：新上传的文件立即出现在「上传文件」分组里。
        await this.loadFiles(sid)
      } finally {
        this.uploadingCount--
      }
    },

    async uploadSkill(file: File): Promise<{ name: string; path: string }> {
      const sid = this.activeSessionId
      if (!sid) throw new Error('no active session')
      this.uploadingCount++
      try {
        const res = await uploadSkill(sid, file)
        await this.loadPaletteItems(sid)
        return res
      } finally { this.uploadingCount-- }
    },

    removeAttachment(path: string) {
      const sid = this.activeSessionId
      if (!sid) return
      this.attachments[sid] = (this.attachments[sid] ?? []).filter(a => a.path !== path)
    },

    async jumpToSession(sessionId: string): Promise<boolean> {
      if (!this.sessions.find(s => s.id === sessionId)) {
        await this.loadSessions()
      }
      const target = this.sessions.find(s => s.id === sessionId)
      if (!target) return false
      await this.openSession(target.id)
      return true
    },

    /**
     * Update the composer's selected model for a session and persist to
     * localStorage so it survives reloads / device switches.
     */
    setSessionModel(sessionId: string, model: string) {
      this.modelBySession[sessionId] = model
      try {
        const key = `check-manage:ai-chat:model:${sessionId}`
        if (model) localStorage.setItem(key, model)
        else localStorage.removeItem(key)
      } catch { /* private mode etc. */ }
    },

    /**
     * Hydrate `modelBySession[id]` from localStorage. Called when a session
     * is opened so the composer dropdown reflects the previously-chosen
     * model on reload.
     */
    hydrateSessionModel(sessionId: string) {
      if (this.modelBySession[sessionId] !== undefined) return
      try {
        const stored = localStorage.getItem(`check-manage:ai-chat:model:${sessionId}`)
        if (stored) this.modelBySession[sessionId] = stored
      } catch { /* ignore */ }
    },

    /** Update the composer's selected agent for a session; persist to localStorage. */
    setSessionAgent(sessionId: string, agent: string) {
      this.agentBySession[sessionId] = agent
      try {
        const key = `check-manage:ai-chat:agent:${sessionId}`
        if (agent) localStorage.setItem(key, agent)
        else localStorage.removeItem(key)
      } catch { /* private mode etc. */ }
    },

    /** Hydrate `agentBySession[id]` from localStorage when a session is opened. */
    hydrateSessionAgent(sessionId: string) {
      if (this.agentBySession[sessionId] !== undefined) return
      try {
        const stored = localStorage.getItem(`check-manage:ai-chat:agent:${sessionId}`)
        if (stored) this.agentBySession[sessionId] = stored
      } catch { /* ignore */ }
    },

    async closeSession(id: string) {
      await apiCloseSession(id)
      if (this.activeSessionId === id) {
        this._closeStream()
        this.activeSessionId = null
      }
      const s = this.sessions.find(x => x.id === id)
      if (s) s.status = 'closed'
      this.streaming[id] = false
    },

    async reopenSession(id: string) {
      await apiReopenSession(id)
      const s = this.sessions.find(x => x.id === id)
      if (s) s.status = 'active'
      this.streaming[id] = false
    },

    async deleteSession(id: string) {
      await apiDeleteSession(id)
      if (this.activeSessionId === id) {
        this._closeStream()
        this.activeSessionId = null
      }
      this.sessions = this.sessions.filter(x => x.id !== id)
      delete this.messages[id]
      delete this.streaming[id]
      delete this.queuedBySession[id]
      delete this.usageBySession[id]
    },

    async clearSession(id: string) {
      // 清空会话：后端删历史 + 清空并重建工作区 + 新建 OpenCode 会话（重置上下文），
      // 会话仍留在列表里且可立即继续。重置本地缓存的全部 per-session 状态。
      await apiClearSession(id)
      this.messages[id] = []
      this.outputs[id] = []
      this.uploads[id] = []
      this.changes[id] = []
      this.attachments[id] = []
      this.streaming[id] = false
      this.thinking[id] = false
      this.usageBySession[id] = { ...EMPTY_USAGE }  // 上下文重置，用量归零
      this.queuedBySession[id] = []  // 上下文已整体重置，排队的插话一并作废
      const s = this.sessions.find(x => x.id === id)
      if (s) s.status = 'active'
      // 旧 SSE 绑定的是已删的 OpenCode 会话，活跃会话需重连到新上下文。
      if (this.activeSessionId === id) {
        this._resetStreamState(id)
        this._openStream(id)
      }
    },

    async compactSession(id: string, model?: string) {
      // 上下文压缩（TUI 的 /compact）：后端在后台线程调 OpenCode summarize，
      // 请求立即返回；压缩 agent 的总结作为普通 assistant 回合经 SSE 流式到达，
      // 结束时 session.idle 走既有收尾（loadFiles/_reloadPersisted/_drainQueue）。
      const res = await apiCompactSession(id, model)
      this._beginTurn(id)
      return res
    },

    _openStream(sid: string) {
      this._closeStream()
      this._stream = createEventStream(sid, {
        onEvent: ({ event, data }) => this._handleEvent(sid, event, data as any),
        onError: () => { /* api layer handles reconnect */ },
        onStatus: (s) => {
          this.streamStatus[sid] = s
          // 断线期间可能错过 session.idle；SSE（重）连上时补一发 drain，
          // 否则运行中排的队会一直挂着不发送。
          if (s === 'open') this._drainQueue(sid)
        },
      })
    },

    _closeStream() {
      this._stream?.close()
      this._stream = null
    },

    _handleEvent(sid: string, event: string, data: any) {
      switch (event) {
        case 'message.updated': {
          const info = data?.info
          if (info?.role === 'assistant' && info?.id) {
            ;(_assistantMsgIds[sid] ?? (_assistantMsgIds[sid] = new Set())).add(info.id)
            // 已完成的消息快照自带 token —— 回合还在流式进行时就地刷新
            // 「当前上下文占用」，让水位线随每步推进实时变化；累计口径等
            // idle 落库后由 _reloadPersisted → _recomputeUsage 统一重算。
            const tok = info.tokens
            if (info.time?.completed && tok && (tok.input != null || tok.output != null)) {
              const cur = this.usageBySession[sid] ?? { ...EMPTY_USAGE }
              this.usageBySession[sid] = {
                ...cur,
                contextTokens: (tok.input ?? 0) + (tok.output ?? 0),
              }
            }
          }
          break
        }
        case 'message.part.updated': {
          const part = data?.part
          if (!part || !_assistantMsgIds[sid]?.has(part?.messageID)) break
          if (part.type === 'text') {
            this._upsertAssistantPart(sid, part.id, { type: 'text', text: part.text ?? '' })
          } else if (part.type === 'reasoning') {
            this.thinking[sid] = true
            this._upsertReasoning(sid, part.id, part.text ?? '')
          } else if (part.type === 'tool') {
            const st = part.state || {}
            // 委托子代理的 task 工具：metadata 里一有真实子会话 id 就渲染成
            // subtask_use 气泡（自然语言委托只有这个 part，没有 subtask part）。
            const childSid = st.metadata?.sessionId
            if (part.tool === 'task' && childSid) {
              ;(_toolChildByMsg[sid] ?? (_toolChildByMsg[sid] = {}))[part.messageID] = childSid
              const subPartId = _subtaskPartByMsg[sid]?.[part.messageID]
              if (subPartId) {
                // /command 路径已经有 subtask part 气泡了——只修正它的子会话 id
                //（subtask part 自己的 sessionID 是父会话），不再加第二个气泡。
                const list = this.messages[sid] ?? []
                const msg = list.length ? list[list.length - 1] : undefined
                const idx = _partIndexById[sid]?.[subPartId]
                const cur = msg && idx !== undefined ? msg.content[idx] : null
                if (cur && cur.type === 'subtask_use') {
                  this._upsertAssistantPart(sid, subPartId, { ...cur, subtaskId: childSid })
                }
              } else {
                const inp = st.input || {}
                this._upsertAssistantPart(sid, part.id, {
                  type: 'subtask_use',
                  subtaskId: childSid,
                  agent: inp.subagent_type ?? null,
                  description: inp.description ?? null,
                  status: st.status === 'completed' ? 'completed'
                    : st.status === 'error' ? 'failed' : 'running',
                })
              }
              break
            }
            // MCP / built-in tool call — render inline as a collapsible card.
            const tt = st.time || {}
            this._upsertAssistantPart(sid, part.id, {
              type: 'tool_use',
              name: part.tool || 'tool',
              title: st.title,
              status: st.status,
              input: st.input,
              result: st.output ?? st.result,
              durationMs: (tt.start && tt.end) ? Math.max(0, tt.end - tt.start) : undefined,
            })
          } else if (part.type === 'subtask') {
            // 委托子代理——先渲染一个占位气泡（agent + description），状态给
            // 默认值 'running' 即可：这只是"让用户立刻看到委托发生了"，真正的
            // 状态与内容由 SubtaskBubble 展开时向 REST 端点现取现查（跟顶层
            // 消息持久化后 status 会被刷新是同一个道理——SSE 这里只负责"存在
            // 性"的实时提示，不负责权威状态）。注意 part.sessionID 是父会话，
            // 真实子会话 id 优先用同消息 tool:'task' part 已给出的值。
            ;(_subtaskPartByMsg[sid] ?? (_subtaskPartByMsg[sid] = {}))[part.messageID] = part.id
            this._upsertAssistantPart(sid, part.id, {
              type: 'subtask_use',
              subtaskId: _toolChildByMsg[sid]?.[part.messageID] ?? part.sessionID,
              agent: part.agent ?? null,
              description: part.description ?? null,
              status: 'running',
            })
          }
          break
        }
        case 'session.idle':
          this.streaming[sid] = false
          this.thinking[sid] = false
          this._resetStreamState(sid)
          this.pendingQuestion[sid] = null  // defensive: a finished turn can't still have one pending
          this.loadFiles(sid)  // surface any files the agent wrote to outputs/
          this.loadChanges(sid)
          this._reloadPersisted(sid)  // converge on server-persisted turn (incl. tool calls)
          // 回合真正结束，补发运行期间排队的插话（若有）。
          this._drainQueue(sid)
          break
        case 'session.error':
          this.streaming[sid] = false
          this.thinking[sid] = false
          break
        // OpenCode's built-in interactive multi-choice tool ("question").
        // Verified live (2026-08-21): the turn stays non-idle (streaming/
        // thinking) while a question is pending — the underlying tool call
        // sits in 'running' state via the normal message.part.updated stream
        // in parallel; we don't special-case that part here (kept generic,
        // matching whatever the answered-tool card ends up showing).
        case 'question.asked':
          this.pendingQuestion[sid] = data as QuestionRequest
          break
        case 'question.replied':
        case 'question.rejected':
          if (this.pendingQuestion[sid]?.id === data?.requestID) {
            this.pendingQuestion[sid] = null
          }
          break
      }
    },

    async _reloadPersisted(sid: string) {
      // After a turn finishes, replace the in-memory (streamed) messages with the
      // server-persisted version so a session that was switched away-and-back
      // mid-stream ends up with the full answer incl. tool calls. Guards:
      //  - skip if a new turn already started (don't clobber a live stream)
      //  - only adopt if it has at least as many messages (a persistence race
      //    could briefly lag behind; never drop the complete in-memory turn)
      try {
        const history = await getMessages(sid)
        const current = this.messages[sid]?.length ?? 0
        if (!this.streaming[sid] && history.messages.length >= current) {
          this.messages[sid] = history.messages
          this._recomputeUsage(sid)  // 本回合 meta 已落库 → 状态条数值刷新
        }
      } catch { /* non-fatal: keep the in-memory copy */ }
    },

    _resetStreamState(sid: string) {
      _streamingAssistantMsgId[sid] = null
      _assistantMsgIds[sid] = new Set()
      _partIndexById[sid] = {}
      _reasoningByPart[sid] = {}
      _toolChildByMsg[sid] = {}
      _subtaskPartByMsg[sid] = {}
    },

    _upsertReasoning(sid: string, partId: string, text: string) {
      // OpenCode 有时把一段连续推理拆成很多个短小的 reasoning part（各自
      // 独立 id，每个只有一两个 token），不是复用同一个 id 增量续写这段话。
      // 按 partId 分开存、值直接拼接展示——用 '\n' 分隔会把一句连续的话拆成
      // 一大堆换行（"思考过程一个 token 一行"），这些 part 本就是同一段话
      // 被拆碎的片段，拼接顺序靠 Object.values 天然保留插入序即可。
      const map = _reasoningByPart[sid] ?? (_reasoningByPart[sid] = {})
      map[partId] = text
      this.reasoning[sid] = Object.values(map).join('')
    },

    _upsertAssistantPart(sid: string, partId: string, partData: AiContentPart) {
      // Upsert a part by its OpenCode part id so text/tool parts render in
      // arrival order and snapshots replace (not append) in place.
      const list = this.messages[sid] ?? (this.messages[sid] = [])
      let msgId = _streamingAssistantMsgId[sid]
      if (!msgId) {
        msgId = 'streaming_' + Date.now()
        _streamingAssistantMsgId[sid] = msgId
        _partIndexById[sid] = {}
        list.push({ id: msgId, role: 'assistant', content: [] })
      }
      const msg = list[list.length - 1]
      const idxMap = _partIndexById[sid] ?? (_partIndexById[sid] = {})
      const existing = idxMap[partId]
      if (existing === undefined) {
        idxMap[partId] = msg.content.length
        msg.content.push(partData)
      } else {
        msg.content[existing] = partData
      }
    },
  },
})
