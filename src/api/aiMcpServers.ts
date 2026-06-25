import service, { get } from '@/utils/request'

export interface McpServer {
  id: string
  name: string
  type: 'remote' | 'local'
  url: string
  command: string[]
  headers: Record<string, string>
  environment: Record<string, string>
  enabled: boolean
}

export type McpServerInput = Omit<McpServer, 'id'>

export async function listMcpServers(): Promise<McpServer[]> {
  const r = await get<{ servers: McpServer[] }>('/ai/mcp-servers')
  return r.servers
}

export async function createMcpServer(body: McpServerInput): Promise<McpServer> {
  const { data } = await service.post('/ai/mcp-servers', body)
  return data
}

export async function updateMcpServer(id: string, body: McpServerInput): Promise<McpServer> {
  const { data } = await service.put(`/ai/mcp-servers/${id}`, body)
  return data
}

export async function deleteMcpServer(id: string): Promise<void> {
  await service.delete(`/ai/mcp-servers/${id}`)
}
