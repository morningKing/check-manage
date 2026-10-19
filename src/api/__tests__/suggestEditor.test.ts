/**
 * suggestEditor API tests：端点形状与 HTTP 动词/URL 对齐。
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/utils/request', () => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  del: vi.fn(),
  authParam: vi.fn(() => '?access_token=tok'),
}))

import { get, post, put, patch, del } from '@/utils/request'
import {
  listDocuments,
  getDocument,
  createDocument,
  renameDocument,
  deleteDocument,
  openDocument,
  listTemplates,
  getTemplateById,
  createTemplate,
  updateTemplate,
  deleteTemplate,
  getVocabulary,
  saveVocabulary,
  importVocabulary,
  vocabularyExportUrl,
} from '../suggestEditor'

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(get).mockResolvedValue({ documents: [], templates: [] })
  vi.mocked(post).mockResolvedValue({ document: null, template: null, ok: true })
  vi.mocked(patch).mockResolvedValue({ document: null, template: null })
  vi.mocked(del).mockResolvedValue({ ok: true })
})

describe('suggestEditor documents api', () => {
  it('lists documents via GET', async () => {
    await listDocuments()
    expect(get).toHaveBeenCalledWith('/suggest-editor/documents')
  })

  it('creates document with title and optional template', async () => {
    await createDocument({ title: '周报', templateId: 'se-tpl-weekly' })
    expect(post).toHaveBeenCalledWith('/suggest-editor/documents', {
      title: '周报',
      templateId: 'se-tpl-weekly',
    })

    await createDocument({ title: '空白' })
    expect(post).toHaveBeenCalledWith('/suggest-editor/documents', { title: '空白' })
  })

  it('renames via PATCH and deletes via DELETE', async () => {
    await renameDocument('doc-1', '新标题')
    expect(patch).toHaveBeenCalledWith('/suggest-editor/documents/doc-1', { title: '新标题' })

    await deleteDocument('doc-1')
    expect(del).toHaveBeenCalledWith('/suggest-editor/documents/doc-1')
  })

  it('opens document (last-opened heartbeat) and fetches one', async () => {
    await openDocument('doc-9')
    expect(post).toHaveBeenCalledWith('/suggest-editor/documents/doc-9/open')

    await getDocument('doc-9')
    expect(get).toHaveBeenCalledWith('/suggest-editor/documents/doc-9')
  })
})

describe('suggestEditor templates api', () => {
  it('lists templates and finds by id', async () => {
    vi.mocked(get).mockResolvedValue({
      templates: [
        { id: 'se-tpl-blank', name: '空白文档', contentHtml: '', isBuiltin: true, sortOrder: 10 },
      ],
    })
    const { templates } = await listTemplates()
    expect(templates).toHaveLength(1)
    const found = await getTemplateById('se-tpl-blank')
    expect(found?.name).toBe('空白文档')
    expect(await getTemplateById('nope')).toBeNull()
  })

  it('creates/updates/deletes templates (admin surface)', async () => {
    await createTemplate({ name: 't', description: 'd', contentHtml: '<p>x</p>' })
    expect(post).toHaveBeenCalledWith('/suggest-editor/templates', {
      name: 't',
      description: 'd',
      contentHtml: '<p>x</p>',
    })

    await updateTemplate('se-tpl-weekly', { name: '周报v2' })
    expect(patch).toHaveBeenCalledWith('/suggest-editor/templates/se-tpl-weekly', { name: '周报v2' })

    await deleteTemplate('se-tpl-weekly')
    expect(del).toHaveBeenCalledWith('/suggest-editor/templates/se-tpl-weekly')
  })
})

describe('suggestEditor vocabulary api', () => {
  it('global scope: get/save hit /suggest-editor/vocabularies/global', async () => {
    await getVocabulary({ kind: 'global' })
    expect(get).toHaveBeenCalledWith('/suggest-editor/vocabularies/global')

    await saveVocabulary({ kind: 'global' }, '词条')
    expect(put).toHaveBeenCalledWith('/suggest-editor/vocabularies/global', { content: '词条' })
  })

  it('document scope: paths embed docId', async () => {
    await getVocabulary({ kind: 'document', docId: 'doc-1' })
    expect(get).toHaveBeenCalledWith('/suggest-editor/documents/doc-1/vocabulary')

    await saveVocabulary({ kind: 'document', docId: 'doc-1' }, '词条|别名')
    expect(put).toHaveBeenCalledWith('/suggest-editor/documents/doc-1/vocabulary', {
      content: '词条|别名',
    })
  })

  it('import posts multipart form with mode and file', async () => {
    const file = new File(['词条'], '词表.txt', { type: 'text/plain' })
    await importVocabulary({ kind: 'global' }, file, 'replace')
    expect(post).toHaveBeenCalledTimes(1)
    const [url, form, config] = vi.mocked(post).mock.calls[0]
    expect(url).toBe('/suggest-editor/vocabularies/global/import')
    expect(form).toBeInstanceOf(FormData)
    expect((form as FormData).get('mode')).toBe('replace')
    expect((form as FormData).get('file')).toBe(file)
    expect(config).toMatchObject({ headers: { 'Content-Type': 'multipart/form-data' } })

    await importVocabulary({ kind: 'document', docId: 'doc-9' }, file, 'append')
    expect(vi.mocked(post).mock.calls[1][0]).toBe('/suggest-editor/documents/doc-9/vocabulary/import')
  })

  it('export url carries /api prefix and access_token', () => {
    expect(vocabularyExportUrl({ kind: 'global' }))
      .toBe('/api/suggest-editor/vocabularies/global/export?access_token=tok')
    expect(vocabularyExportUrl({ kind: 'document', docId: 'doc-1' }))
      .toBe('/api/suggest-editor/documents/doc-1/vocabulary/export?access_token=tok')
  })
})
