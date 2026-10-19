/**
 * ACP 连接调试探针：与 src/agent.ts 完全相同的启动路径（execFile opencode.exe + SDK 握手），
 * 但把每一步与子进程 stderr 全部打出来。用法：
 *   npx tsx scripts/acp-debug.ts <workspaceDir>
 */
import "../src/config.js" // 触发与生产完全一致的 PATH 预处理（prepareAgentPath）
import { execFile, type ChildProcess } from "node:child_process"
import { PassThrough, Readable, Writable } from "node:stream"
import * as acp from "@agentclientprotocol/sdk"
import path from "node:path"

const workspaceDir = process.argv[2] ?? process.cwd()
console.log("[env] PATH 前段 =", (process.env.PATH ?? "").split(path.delimiter).slice(0, 3).join(" | "))

const child: ChildProcess = execFile("opencode.exe", ["acp"], { cwd: workspaceDir })
child.on("error", (err) => console.error("[spawn error]", err))
child.on("exit", (code, signal) => console.error("[child exit]", { code, signal }))
child.stderr?.on("data", (c) => process.stdout.write(`[stderr] ${String(c).trimEnd()}\n`))

const stdout = new PassThrough()
child.stdout!.pipe(stdout)

const stream = acp.ndJsonStream(
  Writable.toWeb(child.stdin!) as WritableStream<Uint8Array>,
  Readable.toWeb(stdout) as ReadableStream<Uint8Array>,
)

const app = acp
  .client({ name: "acp-debug" })
  .onRequest(acp.methods.client.session.requestPermission, (ctx) => {
    console.log("[perm request]", JSON.stringify(ctx.params).slice(0, 200))
    const reject = ctx.params.options.find((o) => o.kind.startsWith("reject"))
    return reject
      ? ({ outcome: { outcome: "selected", optionId: reject.optionId } } satisfies acp.RequestPermissionResponse)
      : ({ outcome: { outcome: "cancelled" } } satisfies acp.RequestPermissionResponse)
  })

app
  .connectWith(stream, async (ctx) => {
    console.log("[connected] sending initialize…")
    const init = await ctx.request(acp.methods.agent.initialize, {
      protocolVersion: acp.PROTOCOL_VERSION,
      clientCapabilities: {},
    })
    console.log("[initialized] protocolVersion =", init.protocolVersion, "agentInfo =", JSON.stringify(init.agentInfo))
    const session = await ctx.buildSession(workspaceDir).start()
    console.log("[session]", session.sessionId)
    console.log("[prompt] 分析 document.md …")
    const result = await session.prompt(
      "document.md 是待分析文档。请找出最多 2 处值得修改的地方，并调用 emit_suggestions 提交建议。",
    )
    console.log("[prompt stop]", JSON.stringify(result))
    setTimeout(() => process.exit(0), 500)
  })
  .catch((err) => {
    console.error("[connect error]", err instanceof Error ? err.stack : err)
    process.exit(1)
  })
