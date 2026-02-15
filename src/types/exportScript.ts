export interface ExportScript {
  id: string
  name: string
  description: string
  language: string
  script: string
  outputFormat: string
  scope: 'page' | 'row'
  createdAt: string
  updatedAt: string
}
