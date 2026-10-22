/**
 * 智能写作（suggest-editor）类型定义
 */

export interface SuggestDocument {
  id: string
  title: string
  ownerUserId: string
  templateId: string | null
  status: 'active' | string
  createdAt: string | null
  updatedAt: string | null
  lastOpenedAt: string | null
  /** 导入 Word 的初始正文（一次性；应用后服务端清空） */
  seedContentHtml?: string | null
}

export interface SuggestTemplate {
  id: string
  name: string
  description: string | null
  /** Tiptap HTML（StarterKit+TaskList 覆盖的节点），新建文档时 setContent 应用 */
  contentHtml: string
  isBuiltin: boolean
  sortOrder: number
  updatedAt: string | null
}
