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

export async function listMcpServers(): Promise<{ servers: McpServer[]; internal: InternalMcp }> {
  const r = await get<{ servers: McpServer[]; internal: InternalMcp }>('/ai/mcp-servers')
  return r
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

/** 平台内置 MCP（check-manage）的管理视图：AI 设置页与外部服务同卡片管理。 */
export interface InternalMcp {
  name: string
  url: string
  enabled: boolean
}

export interface InternalMcpHealth {
  ok: boolean
  latencyMs?: number
  error?: string
  url: string
}

/** 探测内置 MCP 服务器 /health（含延迟；服务器进程未启动时 ok=false）。 */
export function getInternalMcpHealth(): Promise<InternalMcpHealth> {
  return get<InternalMcpHealth>('/ai/mcp-servers/internal/health')
}

/** 切换内置 MCP 开关：仅影响之后新建/清空的会话工作区。 */
export async function setInternalMcpEnabled(enabled: boolean): Promise<InternalMcp> {
  const { data } = await service.put('/ai/mcp-servers/internal', { enabled })
  return data
}

/** 工具治理（内置 MCP 工具的角色×矩阵）。 */
export interface McpToolRuleInput { role: string; tool_name: string; allowed: boolean }

export interface McpGovernanceTool {
  name: string
  description: string
  access: 'read' | 'write'
  rules: Record<string, boolean>
}

export interface McpGovernanceGroup {
  id: string
  name: string
  order: number
  tools: McpGovernanceTool[]
}

export interface McpGovernance {
  groups: McpGovernanceGroup[]
  roles: { role: string; label: string }[]
}

export function getMcpTools(): Promise<McpGovernance> {
  return get<McpGovernance>('/ai/mcp-tools')
}

export async function putMcpToolRules(
  rules: McpToolRuleInput[],
): Promise<{ updated: number; diff: string }> {
  // request.ts 响应拦截器已解包 response.data —— 直接返回，勿再解构 .data
  return await service.put('/ai/mcp-tool-rules', { rules })
}
