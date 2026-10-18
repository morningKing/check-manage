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
}))

import { get, post, patch, del } from '@/utils/request'
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
