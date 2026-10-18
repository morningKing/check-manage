import { execFile, type ChildProcess } from "node:child_process"
import { PassThrough, Readable, Writable } from "node:stream"
import * as acp from "@agentclientprotocol/sdk"
import { config, MOCK_AGENT_PATH } from "./config.js"
import { RewritePayloadSchema, SuggestionsPayloadSchema, type Suggestion } from "./types.js"

export interface TurnResult {
  suggestions: Suggestion[]
  rewrites: Array<{ replacement: string; reason: string }>
  /** agent 的文本输出（续写任务/调试用） */
  text: string
}

export interface AgentSession {
  /** 跑一轮分析，返回 agent 通过 emit_suggestions 提交的建议 */
  analyze(prompt: string): Promise<TurnResult>
  /** 打断当前一轮（ACP session/cancel） */
  cancel(): void
  /** 关闭连接并回收子进程 */
  dispose(): void
}

/**
 * 启动 agent 子进程（ACP agent，JSON-RPC over stdio）。
 *
 * 命令名是编译期固定的字面量（mock 用 node；opencode 用 opencode / opencode.exe），
 * 参数是字面量数组，全部不经过 shell，也不存在任何命令字符串拼接；
 * opencode 安装目录已在 config 启动时前置到 PATH。
 */
function launchAgentProcess(workspaceDir: string): ChildProcess {
  if (config.agentKind === "opencode") {
    // opencode 官方 ACP 模式：opencode 作为子进程，与本服务通过 stdio 通信
    return process.platform === "win32"
      ? execFile("opencode.exe", ["acp"], { cwd: workspaceDir })
      : execFile("opencode", ["acp"], { cwd: workspaceDir })
  }
  return execFile("node", [MOCK_AGENT_PATH], { cwd: workspaceDir })
}

export function startAgentSession(
  workspaceDir: string,
  hooks: { onStatus?: (text: string) => void } = {},
): AgentSession {
  const child = launchAgentProcess(workspaceDir)
  const label = config.agentKind === "opencode" ? "opencode acp" : "mock-agent"
  child.on("error", (err) => console.error("[agent] 启动失败:", err))
  child.stderr?.on("data", (chunk) => process.stderr.write(`[agent:${label}] ${String(chunk).trimEnd()}\n`))

  // 注意：Node 25 下把 child.stdout（Socket）直接交给 Readable.toWeb 读不出数据，
  // 先 pipe 到 PassThrough 再转 Web 流即可（已实测，两个平台通用且无害）。
  const stdout = new PassThrough()
  child.stdout!.pipe(stdout)

  const stream = acp.ndJsonStream(
    Writable.toWeb(child.stdin!) as WritableStream<Uint8Array>,
    Readable.toWeb(stdout) as ReadableStream<Uint8Array>,
  )

  let clientCtx: acp.ClientContext | null = null
  let activeSession: acp.ActiveSession | null = null
  let sessionResolve!: (session: acp.ActiveSession) => void
  let releaseConnection!: () => void
  const sessionPromise = new Promise<acp.ActiveSession>((resolve) => {
    sessionResolve = resolve
  })
  const connectionHold = new Promise<void>((resolve) => {
    releaseConnection = resolve
  })

  const app = acp
    .client({ name: "suggest-editor" })
    .onRequest(acp.methods.client.session.requestPermission, (ctx) => {
      // 我们只要建议，不要任何文件/命令操作：一律选择「拒绝」
      const reject = ctx.params.options.find((option) => option.kind.startsWith("reject"))
      return reject
        ? ({ outcome: { outcome: "selected", optionId: reject.optionId } } satisfies acp.RequestPermissionResponse)
        : ({ outcome: { outcome: "cancelled" } } satisfies acp.RequestPermissionResponse)
    })

  app
    .connectWith(stream, async (ctx) => {
      clientCtx = ctx
      await ctx.request(acp.methods.agent.initialize, {
        protocolVersion: acp.PROTOCOL_VERSION,
        // 不暴露 fs / terminal 能力：agent 只能用自己工作区里的文件
        clientCapabilities: {},
      })
      activeSession = await ctx.buildSession(workspaceDir).start()
      sessionResolve(activeSession)
      await connectionHold // 保持连接直到 dispose()
    })
    .catch((err) => {
      console.error("[agent] 连接结束/异常:", err instanceof Error ? err.message : err)
    })

  let busy = false

  async function analyze(promptText: string): Promise<TurnResult> {
    if (busy) throw new Error("agent session 正忙（ACP 是回合制，同一 session 只能跑一轮）")
    busy = true
    const session = await sessionPromise
    const suggestions: Suggestion[] = []
    const rewrites: Array<{ replacement: string; reason: string }> = []
    const seen = new Set<string>()
    let text = ""
    try {
      const promptPromise = session.prompt(promptText)
      promptPromise.catch(() => {}) // 结果由 nextUpdate 循环统一消费

      for (;;) {
        const message = await session.nextUpdate()
        if (message.kind === "stop") break
        const update = message.update

        if (update.sessionUpdate === "agent_message_chunk") {
          if (update.content.type === "text") text += update.content.text
        } else if (update.sessionUpdate === "tool_call" || update.sessionUpdate === "tool_call_update") {
          // emit_suggestions 的 structured payload 走 rawInput（tool_call 先到）
          // 或 rawOutput（completed 时兜底）
          const raw = update.rawInput ?? update.rawOutput
          const suggestionPayload = SuggestionsPayloadSchema.safeParse(raw)
          if (suggestionPayload.success) {
            for (const suggestion of suggestionPayload.data.suggestions) {
              const key = `s:${suggestion.quote}\u0000${suggestion.replacement}`
              if (seen.has(key)) continue
              seen.add(key)
              suggestions.push(suggestion)
            }
            hooks.onStatus?.(`agent 提交了 ${suggestions.length} 条建议`)
          }
          const rewritePayload = RewritePayloadSchema.safeParse(raw)
          if (rewritePayload.success) {
            const key = `r:${rewritePayload.data.replacement}`
            if (!seen.has(key)) {
              seen.add(key)
              rewrites.push({ replacement: rewritePayload.data.replacement, reason: rewritePayload.data.reason })
            }
          }
        } else if (update.sessionUpdate === "agent_thought_chunk") {
          hooks.onStatus?.("agent 正在思考…")
        }
      }
      await promptPromise
    } finally {
      busy = false
    }
    return { suggestions, rewrites, text }
  }

  function cancel(): void {
    if (!activeSession || !clientCtx) return
    clientCtx
      .notify(acp.methods.agent.session.cancel, { sessionId: activeSession.sessionId })
      .catch(() => {})
  }

  function dispose(): void {
    try {
      releaseConnection()
      activeSession?.dispose()
    } catch {
      /* ignore */
    }
    setTimeout(() => {
      try {
        child.kill()
      } catch {
        /* ignore */
      }
    }, 50)
  }

  return { analyze, cancel, dispose }
}
