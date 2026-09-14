export function shouldSyncAiChatSession(next: string | undefined, previous: string | undefined) {
  return Boolean(next && next !== previous)
}
