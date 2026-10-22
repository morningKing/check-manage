#!/usr/bin/env node
/**
 * 最小 ACP agent（agent 侧实现，官方 SDK）。
 *
 * 不接任何 LLM：用几条确定性规则处理三类任务，让「编辑器 → 后端 → ACP → 结果」
 * 全链路在没有任何模型 key 的情况下可跑通：
 *   1) 自动分析：读工作区 document.md，用 emit_suggestions 提交建议；
 *   2) 用户改写：提示词含 <<<ORIGINAL>>>…<<<END>>>，按规则清理后 emit_rewrite；
 *   3) 续写联想：提示词含 <<<CONTEXT>>>…<<<END>>>，流式返回一小段续写文本。
 *
 * 接入真 opencode 时不需要它：AGENT_KIND=opencode。
 */
import fs from "node:fs/promises"
import path from "node:path"
import { Readable, Writable } from "node:stream"
import * as acp from "@agentclientprotocol/sdk"

const RULES = [
  { re: /非常/, to: "", label: "冗余程度副词，可直接删除", severity: "warn" },
  { re: /而且/, to: "并且", label: "口语化连接词，书面语建议用「并且」", severity: "info" },
  { re: / {2,}/, to: " ", label: "连续空格", severity: "error" },
  { re: /的的/, to: "的", label: "重复助词", severity: "error" },
  { re: /但是/, to: "然而", label: "「但是」偏口语，正式文档可用「然而」", severity: "info" },
]

/**
 * 测试语义表：只在「分析 prompt 里出现了指定规则名/检查要点」且「文档快照里出现了
 * 指定缺陷短语」两个条件同时满足时产出建议。
 *
 * 这个双条件很关键：
 * - 规则未注入时不误报（验证 E2E 确实走了规则 → prompt 注入链路）；
 * - 机械缺陷留给前端本地规则层，这里只模拟语义类判断（mock 不接真模型）。
 *
 * 约束：仅影响 AGENT_KIND=mock；真实 opencode 路径完全不读这张表。
 */
const SEMANTIC_CASES = [
  {
    promptHas: "绝对化表述",
    textHas: "支持所有型号",
    quote: "支持所有型号",
    replacement: "支持 9800M12、9600M12",
    reason: "[绝对化表述] 不要承诺支持所有型号，需列出适用范围",
    severity: "warn",
  },
  {
    promptHas: "指代明确",
    textHas: "该系统",
    quote: "该系统",
    replacement: "本方案中的 9800UPS 系统",
    reason: "[指代明确] “该系统”缺少明确指代",
    severity: "info",
  },
  {
    promptHas: "数据一致性",
    textHas: "总计 10 台",
    quote: "总计 10 台",
    replacement: "总计 12 台",
    reason: "[数据一致性] 总计与明细表 12 台不一致",
    severity: "error",
  },
  {
    promptHas: "回滚方案",
    textHas: "上线步骤",
    quote: "上线步骤",
    replacement: "",
    reason: "[回滚方案] 缺少回滚步骤与触发条件",
    severity: "warn",
  },
  {
    promptHas: "权限与加密",
    textHas: "管理员密码",
    quote: "管理员密码",
    replacement: "管理员密码（加密存储）",
    reason: "[权限与加密] 不得明文描述密码处理方式",
    severity: "error",
  },
]

/** 分析 prompt 是否真的带了检查项（检查规则 aiPrompt / 全局检查要点） */
const HAS_CHECK_SECTION = /检查要点|领域检查规则/

const sessions = new Map()
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

function buildSuggestions(text, promptText = "") {
  const out = []
  // 通用写作规则只在 prompt 带检查项时启用：这样「没有检查项」的文档
  // 完全不会收到 AI 建议（E2E 用这个开关隔离本地规则层与 AI 层）。
  if (HAS_CHECK_SECTION.test(promptText)) {
    for (const rule of RULES) {
      const match = text.match(rule.re)
      if (!match) continue
      out.push({ quote: match[0], replacement: rule.to, reason: rule.label, severity: rule.severity })
      if (out.length >= 5) break
    }
  }
  for (const item of SEMANTIC_CASES) {
    if (out.length >= 5) break
    if (!promptText.includes(item.promptHas)) continue
    if (!text.includes(item.textHas)) continue
    out.push({
      quote: item.quote,
      replacement: item.replacement,
      reason: item.reason,
      severity: item.severity,
    })
  }
  return out
}

function rewriteText(text) {
  let out = text
  for (const rule of RULES) out = out.replace(new RegExp(rule.re.source, "g"), rule.to)
  return out
}

function extractBlock(text, startMarker, endMarker) {
  const start = text.indexOf(startMarker)
  if (start === -1) return null
  const end = text.indexOf(endMarker, start + startMarker.length)
  if (end === -1) return null
  return text.slice(start + startMarker.length, end).trim()
}

function completionFor(context) {
  if (/需要|应该|建议/.test(context)) return "并补充具体的执行步骤与负责人。"
  if (/空格|标点|排版/.test(context)) return "，统一清理后全文会更整洁。"
  if (/[。！？]$/.test(context)) return "下一句先给出结论，再展开论据。"
  return "，并保持前后语气一致。"
}

const agent = {
  async initialize() {
    return { protocolVersion: acp.PROTOCOL_VERSION, agentCapabilities: { loadSession: false } }
  },
  async newSession() {
    const sessionId = crypto.randomUUID()
    sessions.set(sessionId, { aborted: false })
    return { sessionId }
  },
  async authenticate() {
    return {}
  },
  async prompt(params, cx) {
    const session = sessions.get(params.sessionId) ?? { aborted: false }
    session.aborted = false
    sessions.set(params.sessionId, session)
    const sessionId = params.sessionId

    const promptText = (params.prompt ?? [])
      .map((block) => (block && block.type === "text" ? block.text : ""))
      .join("\n")

    // 3) 续写联想
    const completionContext = extractBlock(promptText, "<<<CONTEXT>>>", "<<<END>>>")
    if (completionContext !== null) {
      const completion = completionFor(completionContext)
      await cx.notify(acp.methods.client.session.update, {
        sessionId,
        update: {
          sessionUpdate: "agent_thought_chunk",
          content: { type: "text", text: "续写中…" },
        },
      })
      await sleep(120)
      if (session.aborted) return { stopReason: "cancelled" }
      await cx.notify(acp.methods.client.session.update, {
        sessionId,
        update: {
          sessionUpdate: "agent_message_chunk",
          content: { type: "text", text: completion },
        },
      })
      return { stopReason: "end_turn" }
    }

    // 2) 用户改写
    const rewriteOriginal = extractBlock(promptText, "<<<ORIGINAL>>>", "<<<END>>>")
    if (rewriteOriginal !== null) {
      const replacement = rewriteText(rewriteOriginal)
      const toolCallId = `mock-rewrite-${Date.now()}`
      await sleep(120)
      if (session.aborted) return { stopReason: "cancelled" }
      await cx.notify(acp.methods.client.session.update, {
        sessionId,
        update: {
          sessionUpdate: "tool_call",
          toolCallId,
          title: "提交改写结果",
          status: "in_progress",
          rawInput: { replacement, reason: "mock 改写：按规则清理冗余与空格" },
        },
      })
      await sleep(80)
      await cx.notify(acp.methods.client.session.update, {
        sessionId,
        update: {
          sessionUpdate: "tool_call_update",
          toolCallId,
          status: "completed",
          rawOutput: { ok: true, replacement, reason: "mock 改写：按规则清理冗余与空格" },
        },
      })
      return { stopReason: "end_turn" }
    }

    // 1) 自动分析：读工作区快照
    let text = ""
    try {
      text = await fs.readFile(path.join(process.cwd(), "document.md"), "utf8")
    } catch {
      /* 没有快照就当作空文档 */
    }
    if (!text.trim()) return { stopReason: "end_turn" }

    const suggestions = buildSuggestions(text, promptText)

    await cx.notify(acp.methods.client.session.update, {
      sessionId,
      update: {
        sessionUpdate: "agent_message_chunk",
        content: {
          type: "text",
          text: `已读取文档（${text.length} 字），发现 ${suggestions.length} 处可优化点。`,
        },
      },
    })
    await sleep(120)
    if (session.aborted) return { stopReason: "cancelled" }

    if (suggestions.length > 0) {
      const toolCallId = `mock-${Date.now()}`
      await cx.notify(acp.methods.client.session.update, {
        sessionId,
        update: {
          sessionUpdate: "tool_call",
          toolCallId,
          title: "提交写作优化建议",
          status: "in_progress",
          rawInput: { suggestions },
        },
      })
      await sleep(120)
      await cx.notify(acp.methods.client.session.update, {
        sessionId,
        update: {
          sessionUpdate: "tool_call_update",
          toolCallId,
          status: "completed",
          rawOutput: { ok: true, count: suggestions.length, suggestions },
        },
      })
    }
    return { stopReason: "end_turn" }
  },
  async cancel(params) {
    const session = sessions.get(params.sessionId)
    if (session) session.aborted = true
  },
}

const stream = acp.ndJsonStream(
  Writable.toWeb(process.stdout),
  Readable.toWeb(process.stdin),
)

acp
  .agent({ name: "mock-writing-coach" })
  .onRequest(acp.methods.agent.initialize, (ctx) => agent.initialize(ctx.params))
  .onRequest(acp.methods.agent.session.new, (ctx) => agent.newSession(ctx.params))
  .onRequest(acp.methods.agent.authenticate, (ctx) => agent.authenticate(ctx.params))
  .onRequest(acp.methods.agent.session.prompt, (ctx) => agent.prompt(ctx.params, ctx.client))
  .onNotification(acp.methods.agent.session.cancel, (ctx) => agent.cancel(ctx.params))
  .connect(stream)
