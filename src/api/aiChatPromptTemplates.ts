import { get, post, put, del } from '@/utils/request'
import type { AiChatPromptTemplate } from '@/types/aiChatBatch'

export function listTemplates() {
  return get<AiChatPromptTemplate[]>('/ai/chat/prompt-templates')
}

export function createTemplate(name: string, content: string) {
  return post<AiChatPromptTemplate>('/ai/chat/prompt-templates', { name, content })
}

export function updateTemplate(id: string, name: string, content: string) {
  return put<AiChatPromptTemplate>(`/ai/chat/prompt-templates/${id}`, { name, content })
}

export function deleteTemplate(id: string) {
  return del<void>(`/ai/chat/prompt-templates/${id}`)
}
