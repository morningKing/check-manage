import { z } from "zod"

/** agent 必须按这个 schema 提交建议（见 .opencode/tools/emit_suggestions.ts） */
export const SuggestionSchema = z.object({
  quote: z.string().min(1).describe("原文中被建议修改的精确片段（逐字复制，便于在文档中检索定位）"),
  replacement: z.string().describe("替换后的文本；纯删除时为空字符串"),
  reason: z.string().min(1).describe("一句话说明问题与理由"),
  severity: z.enum(["info", "warn", "error"]).default("info"),
})
export type Suggestion = z.infer<typeof SuggestionSchema>

export const SuggestionsPayloadSchema = z.object({
  suggestions: z.array(SuggestionSchema).max(20),
})
export type SuggestionsPayload = z.infer<typeof SuggestionsPayloadSchema>

/** agent 对「用户发起的改写任务」的输出契约（emit_rewrite 工具） */
export const RewritePayloadSchema = z.object({
  replacement: z.string(),
  reason: z.string().optional().default(""),
})
export type RewritePayload = z.infer<typeof RewritePayloadSchema>

/** 存进 Y.Map("suggestions") 的完整条目（含锚点） */
export interface StoredSuggestion extends Suggestion {
  id: string
  /** Y.encodeRelativePosition 的结果：锚住被建议的文本范围 */
  relFrom: Uint8Array
  relTo: Uint8Array
  /** 生成该建议时的文档 state vector（版本守卫用） */
  analyzedAt: Uint8Array
  createdAt: number
}
