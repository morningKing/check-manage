import crypto from "node:crypto"
import { config } from "./config.js"

/** 与 Flask auth.create_token 同构的 payload 子集（我们只关心这三个字段） */
export interface JwtPayload {
  userId?: string
  username?: string
  role?: string
  exp?: number
}

function base64UrlDecode(part: string): Buffer {
  return Buffer.from(part.replace(/-/g, "+").replace(/_/g, "/"), "base64")
}

export type TokenCheck = { ok: true; payload: JwtPayload } | { ok: false; reason: string }

/**
 * 验签 Flask 签发的 HS256 JWT（手写校验，避免引入 jsonwebtoken 依赖），
 * 失败时给出**可排障的具体原因**（缺失/格式/签名不匹配/过期）——日志与
 * 客户端提示都直接用这个 reason。
 * 浏览器 WebSocket 握手无法带 Authorization 头，这里校验的是
 * @hocuspocus/provider 握手消息里的 token 字段（前端传 authStore 的 JWT）。
 */
export function verifyTokenDetailed(token: string | undefined | null): TokenCheck {
  if (!token) return { ok: false, reason: "令牌缺失" }
  const parts = token.split(".")
  if (parts.length !== 3) return { ok: false, reason: "令牌格式不是 JWT" }
  const [head, payload, signature] = parts
  const expected = crypto
    .createHmac("sha256", config.jwtSecret)
    .update(`${head}.${payload}`)
    .digest("base64url")
  const given = Buffer.from(signature)
  const wanted = Buffer.from(expected)
  if (given.length !== wanted.length || !crypto.timingSafeEqual(given, wanted)) {
    return { ok: false, reason: "签名不匹配（Node 与 Flask 的 JWT_SECRET 是否一致？）" }
  }
  try {
    const parsed = JSON.parse(base64UrlDecode(payload).toString("utf8")) as JwtPayload
    if (typeof parsed.exp === "number" && parsed.exp * 1000 < Date.now()) {
      const ago = Math.round((Date.now() - parsed.exp * 1000) / 60000)
      return { ok: false, reason: `登录令牌已过期（${ago} 分钟前，请重新登录）` }
    }
    if (typeof parsed.userId !== "string" || !parsed.userId) {
      return { ok: false, reason: "令牌缺少 userId" }
    }
    return { ok: true, payload: parsed }
  } catch {
    return { ok: false, reason: "令牌 payload 解析失败" }
  }
}

/** 兼容薄封装（只关心是否通过时用） */
export function verifyToken(token: string | undefined | null): JwtPayload | null {
  const result = verifyTokenDetailed(token)
  return result.ok ? result.payload : null
}

export interface DocumentAccessResult {
  exists: boolean
  allowed: boolean
  /** 拒绝/异常的具体原因（通过时为 null），直接进日志与客户端提示 */
  note: string | null
}

/**
 * 向 Flask 核对「这个用户能否打开这篇文档」：
 * 文档必须存在且 status='active'，且用户是属主（admin 角色由 JWT 声明，Flask 端
 * 只认属主与 admin 二选一）。access 端点 404/403/网络异常一律视为拒绝（fail-closed），
 * 但 note 里区分出「上游 403（内部 token 不匹配）」与「文档不存在/非属主」——
 * 这两种原因的修复动作完全不同。
 */
export async function checkDocumentAccess(
  documentName: string,
  user: JwtPayload,
): Promise<DocumentAccessResult> {
  const url =
    `${config.apiUrl}/suggest-editor/internal/documents/${encodeURIComponent(documentName)}/access` +
    `?userId=${encodeURIComponent(user.userId ?? "")}`
  try {
    const res = await fetch(url, { headers: { "X-Internal-Token": config.internalToken } })
    if (res.status === 403) {
      return { exists: false, allowed: false, note: "Flask 内部端点 403（MCP_INTERNAL_TOKEN 两侧不一致或未配置）" }
    }
    if (!res.ok) {
      return { exists: false, allowed: false, note: `Flask 内部端点 HTTP ${res.status}` }
    }
    const body = (await res.json()) as { exists?: boolean; allowed?: boolean }
    if (body.allowed === true) return { exists: true, allowed: true, note: null }
    if (body.exists === false) return { exists: false, allowed: false, note: "文档不存在或已删除" }
    return { exists: true, allowed: false, note: "当前用户不是文档属主" }
  } catch (error) {
    return {
      exists: false,
      allowed: false,
      note: `无法连接 Flask（${config.apiUrl}）：${error instanceof Error ? error.message : String(error)}`,
    }
  }
}
