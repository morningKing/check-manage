import { get, post, put, del } from '@/utils/request'

const BASE = '/ai/skills'

export interface GlobalSkill {
  id: string
  name: string
  description: string
  enabled: boolean
  uploadedBy: string | null
  fileSize: number
  createdAt: string | null
  updatedAt: string | null
}

export interface GlobalSkillFile {
  name: string
  path: string
  size: number
}

export function listGlobalSkills() {
  return get<{ skills: GlobalSkill[] }>(BASE)
}

export function getGlobalSkill(id: string) {
  return get<GlobalSkill>(`${BASE}/${id}`)
}

export function updateGlobalSkill(id: string, data: { description?: string; enabled?: boolean }) {
  return put<GlobalSkill>(`${BASE}/${id}`, data)
}

export function deleteGlobalSkill(id: string) {
  return del<{ deleted: boolean }>(`${BASE}/${id}`)
}

export function listSkillFiles(id: string) {
  return get<{ files: GlobalSkillFile[] }>(`${BASE}/${id}/files`)
}

export function readSkillFile(id: string, path: string) {
  return get<{ content: string; truncated: boolean; binary: boolean }>(
    `${BASE}/${id}/files/${encodeURIComponent(path)}`,
  )
}

export function uploadGlobalSkill(file: File, description: string) {
  const form = new FormData()
  form.append('file', file)
  form.append('description', description)
  return post<GlobalSkill>(BASE, form)
}
