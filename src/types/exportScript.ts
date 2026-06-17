export interface ExportScript {
  id: string
  name: string
  description: string
  language: string
  script: string
  outputFormat: string
  scope: 'page' | 'row' | 'menu'
  boundCollection?: string | null
  boundMenuId?: string | null
  createdAt: string
  updatedAt: string
}
