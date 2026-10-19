/**
 * 智能写作（suggest-editor）API
 *
 * 文档与写作模板的 CRUD；Yjs 二进制状态不走这里——它由编辑器经
 * /suggest-ws WebSocket 直连 Node 服务同步，Flask 内部端点只对 Node 开放。
 */

import { get, post, patch, put, del, authParam } from '@/utils/request'
import type { SuggestDocument, SuggestTemplate } from '@/types/suggestEditor'

export function listDocuments() {
  return get<{ documents: SuggestDocument[] }>('/suggest-editor/documents')
}

export function getDocument(docId: string) {
  return get<{ document: SuggestDocument }>(`/suggest-editor/documents/${docId}`)
}

export function createDocument(payload: { title: string; templateId?: string }) {
  return post<{ document: SuggestDocument }>('/suggest-editor/documents', payload)
}

export function renameDocument(docId: string, title: string) {
  return patch<{ document: SuggestDocument }>(`/suggest-editor/documents/${docId}`, { title })
}

export function deleteDocument(docId: string) {
  return del<{ ok: boolean }>(`/suggest-editor/documents/${docId}`)
}

/** 编辑器打开文档时调用一次，刷新「最近打开」排序 */
export function openDocument(docId: string) {
  return post<{ ok: boolean }>(`/suggest-editor/documents/${docId}/open`)
}

export function listTemplates() {
  return get<{ templates: SuggestTemplate[] }>('/suggest-editor/templates')
}

/** 编辑页用：取单个模板（后端只有列表端点，取回后按 id 查找） */
export async function getTemplateById(templateId: string): Promise<SuggestTemplate | null> {
  const { templates } = await listTemplates()
  return templates.find((tpl) => tpl.id === templateId) ?? null
}

export function createTemplate(payload: { name: string; description?: string; contentHtml: string }) {
  return post<{ template: SuggestTemplate }>('/suggest-editor/templates', payload)
}

export function updateTemplate(
  templateId: string,
  payload: { name?: string; description?: string; contentHtml?: string },
) {
  return patch<{ template: SuggestTemplate }>(`/suggest-editor/templates/${templateId}`, payload)
}

export function deleteTemplate(templateId: string) {
  return del<{ ok: boolean }>(`/suggest-editor/templates/${templateId}`)
}

/* ------------------------------- 词表（全局 + 文档级） ------------------------------- */

export type VocabularyScope = { kind: 'global' } | { kind: 'document'; docId: string }

function vocabBase(scope: VocabularyScope): string {
  return scope.kind === 'global'
    ? '/suggest-editor/vocabularies/global'
    : `/suggest-editor/documents/${scope.docId}/vocabulary`
}

export function getVocabulary(scope: VocabularyScope) {
  return get<{ content: string }>(vocabBase(scope))
}

export function saveVocabulary(scope: VocabularyScope, content: string) {
  return put<{ ok: boolean; total: number }>(vocabBase(scope), { content })
}

/** mode: append=按词条去重追加；replace=整文件覆盖 */
export function importVocabulary(
  scope: VocabularyScope,
  file: File,
  mode: 'append' | 'replace',
) {
  const form = new FormData()
  form.append('mode', mode)
  form.append('file', file)
  return post<{ ok: boolean; total: number; added: number; skipped: number }>(
    `${vocabBase(scope)}/import`,
    form,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  )
}

/** 导出下载链接（<a href> 直下，鉴权走 ?access_token=） */
export function vocabularyExportUrl(scope: VocabularyScope): string {
  return `/api${vocabBase(scope)}/export${authParam()}`
}

/* ------------------------- 检查要点（全局，admin） ------------------------- */

export function getChecklist() {
  return get<{ content: string }>('/suggest-editor/checklists/global')
}

export function saveChecklist(content: string) {
  return put<{ ok: boolean; total: number }>('/suggest-editor/checklists/global', { content })
}

export function importChecklist(file: File, mode: 'append' | 'replace') {
  const form = new FormData()
  form.append('mode', mode)
  form.append('file', file)
  return post<{ ok: boolean; total: number; added: number; skipped: number }>(
    '/suggest-editor/checklists/global/import',
    form,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  )
}

export function checklistExportUrl(): string {
  return `/api/suggest-editor/checklists/global/export${authParam()}`
}
