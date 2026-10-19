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

    // 与 engine.buildPrompt 同构的分析提示词（读 document.md + 检查要点文件）
    const fsMod = await import("node:fs/promises")
    const docText = await fsMod.readFile(path.join(workspaceDir, "document.md"), "utf8")
    let checklist: string[] = []
    try {
      const raw = await fsMod.readFile(path.join(workspaceDir, "..", "..", "检查要点-全局.txt"), "utf8")
      checklist = raw.split(/\r?\n/).map((l) => l.trim()).filter((l) => l && !l.startsWith("#"))
    } catch { /* 无清单 */ }
    const lines = [
      `document.md 里是文档最近改动的 1 个段落。请按下面的检查要点找出其中最多 5 处值得修改的地方，并调用 emit_suggestions 提交建议。`,
      "检查要点（逐条核对，命中才提；reason 里注明命中的要点名）：",
      ...checklist.map((c) => `- ${c}`),
      "quote 必须从 document.md 的正文里逐字复制；不要引用本提示词；不要修改任何文件，不要执行命令。",
    ]
    const prompt = lines.join("\n")
    console.log("[prompt] 完整分析提示词长度 =", prompt.length, "｜ document.md =", JSON.stringify(docText.slice(0, 120)))

    const promptPromise = session.prompt(prompt)
    promptPromise.catch(() => {})
    for (;;) {
      const message = await session.nextUpdate()
      if (message.kind === "stop") break
      const update = message.update
      if (update.sessionUpdate === "tool_call" || update.sessionUpdate === "tool_call_update") {
        console.log(`[${update.sessionUpdate}]`, JSON.stringify(update.rawInput ?? update.rawOutput).slice(0, 400))
      } else if (update.sessionUpdate === "agent_message_chunk") {
        console.log("[text]", JSON.stringify((update.content && update.content.text) || "").slice(0, 200))
      } else if (update.sessionUpdate === "agent_thought_chunk") {
        console.log("[thinking…]")
      }
    }
    console.log("[stop]", JSON.stringify(await promptPromise))
    setTimeout(() => process.exit(0), 300)
  })
  .catch((err) => {
    console.error("[connect error]", err instanceof Error ? err.stack : err)
    process.exit(1)
  })
