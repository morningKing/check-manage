import type { useAiChatStore } from '@/stores/aiChat'

export interface FrontendCommand {
  name: string
  description: string
  run: (store: ReturnType<typeof useAiChatStore>) => void | Promise<void>
}

export const FRONTEND_COMMANDS: FrontendCommand[] = [
  { name: 'mcps', description: '列出已配置的 MCP 服务及工具', run: (s) => s.showMcpServices() },
]

export function findFrontendCommand(name: string): FrontendCommand | undefined {
  const n = name.toLowerCase()
  const direct = FRONTEND_COMMANDS.find((c) => c.name === n)
  if (direct) return direct
  if (n === 'mcp') return FRONTEND_COMMANDS.find((c) => c.name === 'mcps')
  return undefined
}

export interface ParsedCommand { name: string; args: string }
export function parseCommandLine(text: string): ParsedCommand | null {
  const t = text.trim()
  if (!t.startsWith('/')) return null
  const sp = t.indexOf(' ')
  if (sp < 0) return { name: t.slice(1), args: '' }
  return { name: t.slice(1, sp), args: t.slice(sp + 1).trim() }
}
