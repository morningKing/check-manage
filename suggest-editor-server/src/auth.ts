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

/**
 * 验签 Flask 签发的 HS256 JWT（手写校验，避免引入 jsonwebtoken 依赖）：
 * header.payload 的 HMAC-SHA256 与签名段恒时比较 + exp 过期检查。
 * 浏览器 WebSocket 握手无法带 Authorization 头，这里校验的是
 * @hocuspocus/provider 握手消息里的 token 字段（前端传 authStore 的 JWT）。
 */
export function verifyToken(token: string | undefined | null): JwtPayload | null {
  if (!token) return null
  const parts = token.split(".")
  if (parts.length !== 3) return null
  const [head, payload, signature] = parts
  const expected = crypto
    .createHmac("sha256", config.jwtSecret)
    .update(`${head}.${payload}`)
    .digest("base64url")
  const given = Buffer.from(signature)
  const wanted = Buffer.from(expected)
  if (given.length !== wanted.length || !crypto.timingSafeEqual(given, wanted)) return null
  try {
    const parsed = JSON.parse(base64UrlDecode(payload).toString("utf8")) as JwtPayload
    if (typeof parsed.exp === "number" && parsed.exp * 1000 < Date.now()) return null
    if (typeof parsed.userId !== "string" || !parsed.userId) return null
    return parsed
  } catch {
    return null
  }
}

export interface DocumentAccessResult {
  exists: boolean
  allowed: boolean
}

/**
 * 向 Flask 核对「这个用户能否打开这篇文档」：
 * 文档必须存在且 status='active'，且用户是属主（admin 角色由 JWT 声明，Flask 端
 * 只认属主与 admin 二选一）。access 端点 404/403/网络异常一律视为拒绝（fail-closed）。
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
    if (!res.ok) return { exists: false, allowed: false }
    const body = (await res.json()) as { allowed?: boolean }
    return { exists: true, allowed: body.allowed === true }
  } catch {
    return { exists: false, allowed: false }
  }
}
