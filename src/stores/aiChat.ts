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
  createSession, listSessions, renameSession as apiRenameSession, deleteSession,
  getMessages, sendMessage, uploadFile, uploadSkill, listFiles, getChanges, getMcpServices,
  getCommands, postCommand, abortSession, deleteFromMessage,
  createEventStream,
  type AiMessage, type AiContentPart, type AiFile, type ChangedFile, type McpServer,
  type PaletteCommand, type StreamStatus,
} from '@/api/aiChat'

interface SessionMeta {
  id: string
  title: string
}

interface PendingAttachment {
  name: string
  path: string
}

interface State {
  sessions: SessionMeta[]
  activeSessionId: string | null
  messages: Record<string, AiMessage[]>
  streaming: Record<string, boolean>
  reasoning: Record<string, string>
  thinking: Record<string, boolean>
  attachments: Record<string, PendingAttachment[]>
  outputs: Record<string, AiFile[]>
  changes: Record<string, ChangedFile[]>
  paletteItems: Record<string, { commands: PaletteCommand[]; skills: PaletteCommand[] }>
  streamStatus: Record<string, StreamStatus>
  // Concurrent uploads (file + skill) used to clobber a single boolean; this is
  // a refcount so the spinner only clears when ALL in-flight uploads finish.
  uploadingCount: number
  _stream: { close(): void } | null
}

let _streamingAssistantMsgId: Record<string, string | null> = {}
let _assistantMsgIds: Record<string, Set<string>> = {}
let _partIndexById: Record<string, Record<string, number>> = {}
let _reasoningByPart: Record<string, Record<string, string>> = {}

export const useAiChatStore = defineStore('aiChat', {
  state: (): State => ({
    sessions: [],
    activeSessionId: null,
    messages: {},
    streaming: {},
    reasoning: {},
    thinking: {},
    attachments: {},
    outputs: {},
    changes: {} as Record<string, ChangedFile[]>,
    paletteItems: {} as Record<string, { commands: PaletteCommand[]; skills: PaletteCommand[] }>,
    streamStatus: {} as Record<string, StreamStatus>,
    uploadingCount: 0,
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
    activeChanges(state): ChangedFile[] {
      return state.activeSessionId ? state.changes[state.activeSessionId] ?? [] : []
    },
    activeStreamStatus(state): StreamStatus {
      return state.activeSessionId ? state.streamStatus[state.activeSessionId] ?? 'closed' : 'closed'
    },
    uploading(state): boolean {
      return state.uploadingCount > 0
    },
  },

  actions: {
    async loadSessions() {
      const { sessions } = await listSessions()
      this.sessions = sessions.map(s => ({ id: s.id, title: s.title }))
    },

    async startNewSession(projectMenuId?: string) {
      const meta = await createSession(projectMenuId)
      this.sessions.unshift({ id: meta.id, title: meta.title })
      this.activeSessionId = meta.id
      this.messages[meta.id] = []
      this.streaming[meta.id] = false
      this.attachments[meta.id] = []
      this._resetStreamState(meta.id)
      const history = await getMessages(meta.id)
      this.messages[meta.id] = history.messages
      this.loadPaletteItems(meta.id)
      this._openStream(meta.id)
      return meta.id
    },

    async openSession(id: string) {
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
      this.loadFiles(id)
      this.loadChanges(id)
      this.loadPaletteItems(id)
      this._openStream(id)
    },

    async loadFiles(id: string) {
      try {
        const { files } = await listFiles(id)
        this.outputs[id] = files.filter(f => f.dir === 'outputs')
      } catch { /* non-fatal */ }
    },

    async loadChanges(id: string) {
      try {
        const { changes } = await getChanges(id)
        this.changes[id] = changes
      } catch { /* non-fatal */ }
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

    appendMessage(id: string, msg: AiMessage) {
      ;(this.messages[id] ?? (this.messages[id] = [])).push(msg)
    },

    async renameSession(id: string, title: string) {
      await apiRenameSession(id, title)
      const s = this.sessions.find(x => x.id === id)
      if (s) s.title = title
    },

    async sendUserMessage(content: string) {
      if (!this.activeSessionId) throw new Error('no active session')
      const sid = this.activeSessionId
      const pending = this.attachments[sid] ?? []
      const parts: AiContentPart[] = []
      if (content) parts.push({ type: 'text', text: content })
      for (const a of pending) parts.push({ type: 'file', name: a.name, path: a.path })

      const localId = 'local_' + Date.now()
      this.messages[sid].push({ id: localId, role: 'user', content: parts })
      this.streaming[sid] = true
      this.reasoning[sid] = ''
      this.thinking[sid] = true
      this._resetStreamState(sid)
      const paths = pending.map(a => a.path)
      this.attachments[sid] = []
      const { messageId } = await sendMessage(sid, content, paths)
      // adopt the real DB id so Edit/Retry can target this row server-side
      const msg = this.messages[sid].find((m) => m.id === localId)
      if (msg && messageId) msg.id = messageId
    },

    async deleteFromMessage(id: string, msgId: string) {
      try { await deleteFromMessage(id, msgId) } catch { /* still trim locally */ }
      const arr = this.messages[id] ?? []
      const idx = arr.findIndex((m) => m.id === msgId)
      if (idx >= 0) this.messages[id] = arr.slice(0, idx)
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

    async closeSession(id: string) {
      if (this.activeSessionId === id) this._closeStream()
      await deleteSession(id)
      this.sessions = this.sessions.filter(s => s.id !== id)
      delete this.messages[id]
      delete this.streaming[id]
      delete this.reasoning[id]
      this._resetStreamState(id)
      if (this.activeSessionId === id) this.activeSessionId = null
    },

    _openStream(sid: string) {
      this._closeStream()
      this._stream = createEventStream(sid, {
        onEvent: ({ event, data }) => this._handleEvent(sid, event, data as any),
        onError: () => { /* api layer handles reconnect */ },
        onStatus: (s) => { this.streamStatus[sid] = s },
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
            // MCP / built-in tool call — render inline as a collapsible card.
            const st = part.state || {}
            this._upsertAssistantPart(sid, part.id, {
              type: 'tool_use',
              name: part.tool || 'tool',
              title: st.title,
              status: st.status,
              input: st.input,
              result: st.output ?? st.result,
            })
          }
          break
        }
        case 'session.idle':
          this.streaming[sid] = false
          this.thinking[sid] = false
          this._resetStreamState(sid)
          this.loadFiles(sid)  // surface any files the agent wrote to outputs/
          this.loadChanges(sid)
          break
        case 'session.error':
          this.streaming[sid] = false
          this.thinking[sid] = false
          break
      }
    },

    _resetStreamState(sid: string) {
      _streamingAssistantMsgId[sid] = null
      _assistantMsgIds[sid] = new Set()
      _partIndexById[sid] = {}
      _reasoningByPart[sid] = {}
    },

    _upsertReasoning(sid: string, partId: string, text: string) {
      const map = _reasoningByPart[sid] ?? (_reasoningByPart[sid] = {})
      map[partId] = text
      this.reasoning[sid] = Object.values(map).join('\n')
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
