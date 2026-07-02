// Intentional theme-invariant identity palette (per-agent-name color, like
// Slack/Gmail avatars — stays constant across light/dark).
export const AVATAR_COLORS = ['#5b8def', '#e6795e', '#42b883', '#b06ab3', '#e0913a', '#3aa5c2']

export function avatarInitial(name?: string): string {
  return (name || '客服').trim().charAt(0) || '客'
}

export function avatarColor(name?: string): string {
  const n = name || '客服'
  let h = 0
  for (let i = 0; i < n.length; i++) h = (h * 31 + n.charCodeAt(i)) >>> 0
  return AVATAR_COLORS[h % AVATAR_COLORS.length]
}
