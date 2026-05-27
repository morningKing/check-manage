/**
 * AI Chat Pinia store.
 *
 * State shape per spec §5.4. M1 only handles a single active session
 * (no SessionList), but the state is already keyed by sessionId so M2
 * can add multi-session without rewrite.
 */

import { defineStore } from 'pinia'
import {
  createSession, deleteSession, getMessages, sendMessage, createEventStream,
  type AiMessage, type AiContentPart,
} from '@/api/aiChat'

interface AiSessionMeta {
  id: string
  title: string
  workspacePath: string
}

interface State {
  sessions: AiSessionMeta[]
  activeSessionId: string | null
  messages: Record<string, AiMessage[]>
  streaming: Record<string, boolean>
  drawerOpen: boolean
  _stream: { close(): void } | null
}

// Per-session streaming bookkeeping. OpenCode sends text parts as full
// snapshots keyed by part id (not deltas), so we upsert parts by id and only
// render parts that belong to an assistant message (spec §12.4).
let _streamingAssistantMsgId: Record<string, string | null> = {}
let _assistantMsgIds: Record<string, Set<string>> = {}
let _partIndexById: Record<string, Record<string, number>> = {}

export const useAiChatStore = defineStore('aiChat', {
  state: (): State => ({
    sessions: [],
    activeSessionId: null,
    messages: {},
    streaming: {},
    drawerOpen: false,
    _stream: null,
  }),

  actions: {
    toggleDrawer(open?: boolean) {
      this.drawerOpen = open ?? !this.drawerOpen
    },

    async startNewSession(projectMenuId?: string) {
      const meta = await createSession(projectMenuId)
      this.sessions.push(meta)
      this.activeSessionId = meta.id
      this.messages[meta.id] = []
      this.streaming[meta.id] = false
      this._resetStreamState(meta.id)
      const history = await getMessages(meta.id)
      this.messages[meta.id] = history.messages
      this._openStream(meta.id)
    },

    async sendUserMessage(content: string) {
      if (!this.activeSessionId) throw new Error('no active session')
      const sid = this.activeSessionId
      const userMsg: AiMessage = {
        id: 'local_' + Date.now(),
        role: 'user',
        content: [{ type: 'text', text: content }],
      }
      this.messages[sid].push(userMsg)
      this.streaming[sid] = true
      this._resetStreamState(sid)
      await sendMessage(sid, content)
    },

    async closeSession(id: string) {
      this._closeStream()
      await deleteSession(id)
      this.sessions = this.sessions.filter(s => s.id !== id)
      delete this.messages[id]
      delete this.streaming[id]
      this._resetStreamState(id)
      if (this.activeSessionId === id) this.activeSessionId = null
    },

    _openStream(sid: string) {
      this._closeStream()
      this._stream = createEventStream(sid, {
        onEvent: ({ event, data }) => this._handleEvent(sid, event, data as any),
        onError: () => { /* api layer handles reconnect; UI banner in M2 */ },
      })
    },

    _closeStream() {
      this._stream?.close()
      this._stream = null
    },

    _handleEvent(sid: string, event: string, data: any) {
      // `data` is the OpenCode event `properties` object (the Flask proxy
      // strips the {type, properties} envelope to just properties).
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
          if (part?.type === 'text' && _assistantMsgIds[sid]?.has(part?.messageID)) {
            this._upsertAssistantTextPart(sid, part.id, part.text ?? '')
          }
          break
        }
        case 'session.idle':
          this.streaming[sid] = false
          this._resetStreamState(sid)
          break
        case 'session.error':
          this.streaming[sid] = false
          break
      }
    },

    _resetStreamState(sid: string) {
      _streamingAssistantMsgId[sid] = null
      _assistantMsgIds[sid] = new Set()
      _partIndexById[sid] = {}
    },

    _upsertAssistantTextPart(sid: string, partId: string, text: string) {
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
        msg.content.push({ type: 'text', text })
      } else {
        // OpenCode resends the full snapshot for a part; replace, don't append.
        ;(msg.content[existing] as Extract<AiContentPart, { type: 'text' }>).text = text
      }
    },
  },
})
