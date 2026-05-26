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

let _streamingAssistantMsgId: Record<string, string | null> = {}

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
      _streamingAssistantMsgId[meta.id] = null
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
      _streamingAssistantMsgId[sid] = null
      await sendMessage(sid, content)
    },

    async closeSession(id: string) {
      this._closeStream()
      await deleteSession(id)
      this.sessions = this.sessions.filter(s => s.id !== id)
      delete this.messages[id]
      delete this.streaming[id]
      delete _streamingAssistantMsgId[id]
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
      switch (event) {
        case 'message.part.delta':
          this._appendAssistantDelta(sid, data?.text ?? '')
          break
        case 'tool.use':
          this._appendAssistantPart(sid, {
            type: 'tool_use',
            name: data?.name,
            input: data?.input,
            result: data?.result,
          })
          break
        case 'message.finished':
          this.streaming[sid] = false
          _streamingAssistantMsgId[sid] = null
          break
        case 'error':
          this.streaming[sid] = false
          break
      }
    },

    _appendAssistantDelta(sid: string, text: string) {
      const list = this.messages[sid] ?? (this.messages[sid] = [])
      let id = _streamingAssistantMsgId[sid]
      if (!id) {
        id = 'streaming_' + Date.now()
        _streamingAssistantMsgId[sid] = id
        list.push({ id, role: 'assistant', content: [{ type: 'text', text: '' }] })
      }
      const msg = list[list.length - 1]
      const lastPart = msg.content[msg.content.length - 1] as AiContentPart
      if (lastPart && lastPart.type === 'text') {
        lastPart.text += text
      } else {
        msg.content.push({ type: 'text', text })
      }
    },

    _appendAssistantPart(sid: string, part: AiContentPart) {
      const list = this.messages[sid] ?? (this.messages[sid] = [])
      let id = _streamingAssistantMsgId[sid]
      if (!id) {
        id = 'streaming_' + Date.now()
        _streamingAssistantMsgId[sid] = id
        list.push({ id, role: 'assistant', content: [] })
      }
      list[list.length - 1].content.push(part)
    },
  },
})
