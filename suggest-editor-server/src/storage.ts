import * as Y from "yjs"
import { config } from "./config.js"

/**
 * 文档持久化（数据库版，经 Flask 内部端点中转）：
 * - 存：Y.encodeStateAsUpdate 的二进制（base64）+ 已分析块的 hash 列表
 *   → PUT /suggest-editor/internal/documents/<id>/state（X-Internal-Token 鉴权）
 * - 载：同端点 GET 回灌，origin 带 source:"local" 以便 onChange 忽略（加载不算用户编辑）
 *
 * 数据库存的是整份 Y.Doc（正文 + suggestions/commands/vocabulary/assistant 各 map），
 * 所以建议、词表、改写命令都会随文档一起恢复；接口与旧文件版保持同形。
 */
export const STORAGE_ORIGIN = { source: "local", storage: "db" } as const

const MAX_HASHES = 500

interface StoredState {
  state?: string | null
  analyzedHashes?: string[]
}

function stateUrl(documentName: string): string {
  return `${config.apiUrl}/suggest-editor/internal/documents/${encodeURIComponent(documentName)}/state`
}

function internalHeaders(extra: Record<string, string> = {}): Record<string, string> {
  return { "Content-Type": "application/json", "X-Internal-Token": config.internalToken, ...extra }
}

/** 读取历史文档并回灌到 ydoc；返回上次已分析过的块 hash 列表 */
export async function loadDocInto(name: string, ydoc: Y.Doc): Promise<string[]> {
  try {
    const res = await fetch(stateUrl(name), { headers: internalHeaders() })
    if (!res.ok) {
      console.warn(`[storage] 读取文档 ${name} 失败：HTTP ${res.status}`)
      return []
    }
    const body = (await res.json()) as StoredState
    if (body.state) {
      const buffer = Buffer.from(body.state, "base64")
      if (buffer.byteLength > 0) {
        Y.applyUpdate(ydoc, new Uint8Array(buffer), STORAGE_ORIGIN)
      }
    }
    if (Array.isArray(body.analyzedHashes)) {
      return body.analyzedHashes.filter((hash) => typeof hash === "string").slice(-MAX_HASHES)
    }
  } catch (error) {
    console.warn(`[storage] 读取文档 ${name} 异常：`, error instanceof Error ? error.message : error)
  }
  return []
}

/** 把当前文档状态与已分析块 hash 存回数据库 */
export async function saveDocFrom(name: string, ydoc: Y.Doc, analyzedHashes: string[]): Promise<void> {
  const update = Y.encodeStateAsUpdate(ydoc)
  const res = await fetch(stateUrl(name), {
    method: "PUT",
    headers: internalHeaders(),
    body: JSON.stringify({
      state: Buffer.from(update).toString("base64"),
      analyzedHashes: analyzedHashes.slice(-MAX_HASHES),
    }),
  })
  if (!res.ok) {
    throw new Error(`保存文档 ${name} 失败：HTTP ${res.status}`)
  }
}
