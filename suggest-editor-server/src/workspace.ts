import fs from "node:fs/promises"
import path from "node:path"
import { config } from "./config.js"

export function workspaceDir(docName: string): string {
  const safe = docName.replace(/[^a-zA-Z0-9._-]+/g, "_").slice(0, 80) || "doc"
  return path.join(config.dataDir, "ws", safe)
}

const AGENTS_MD = `# 工作区纪律

你是文档写作教练，工作区里的 \`document.md\` 是要分析的文档快照。

- 只读文件，**禁止修改任何文件**、禁止执行 shell 命令。
- 自动分析任务：必须通过 \`emit_suggestions\` 工具提交结构化建议（不要只在回复里写文本）。
- 用户发起的改写任务（提示词里带 \`<<<ORIGINAL>>>\`）：按【改写指令】改写原文，通过 \`emit_rewrite\` 提交改写后的完整文本。
- quote 必须从 document.md 里**逐字复制**，以便在原文中精确检索定位。
`

const COACH_MD = `---
description: 写作优化教练：分析文档并以结构化建议的形式提交修改，绝不直接改文件
mode: primary
permission:
  edit: deny
  bash: deny
  webfetch: deny
  websearch: deny
---
你是一名严格的文档写作教练。按提示词分两种任务：

## 任务一：自动分析（默认）
1. 读取 document.md（若提示词指定了范围，只看该范围）。
2. 找出值得修改的问题：冗余啰嗦、表述歧义、逻辑跳跃、语病与错别字、语气不一致等。
3. 逐条调用 emit_suggestions 提交建议：
   - quote 从原文逐字复制（含标点），不要给字符偏移量；
   - replacement 给出可直接替换的完整文本，纯删除用空字符串；
   - reason 一句话讲清为什么；
   - 最多 5 条，按严重度排序（error > warn > info）。

## 任务二：用户改写（提示词含 <<<ORIGINAL>>> ... <<<END>>>）
1. 严格按【改写指令】改写标记之间的原文，保持原意与语言风格。
2. 调用 emit_rewrite 提交改写后的**完整文本**（不要调用 emit_suggestions）。
3. 只提交一段结果。

绝不修改任何文件，绝不执行命令。
`

const EMIT_TOOL_TS = `import { tool } from "@opencode-ai/plugin"

export default tool({
  description: "提交文档优化建议（不要直接编辑文件）",
  args: {
    suggestions: tool.schema
      .array(
        tool.schema.object({
          quote: tool.schema.string().describe("原文中被建议修改的精确片段（逐字复制）"),
          replacement: tool.schema.string().describe("替换后的文本；纯删除时为空字符串"),
          reason: tool.schema.string().describe("一句话说明问题"),
          severity: tool.schema.enum(["info", "warn", "error"]).describe("严重度"),
        }),
      )
      .describe("按严重度排序的建议列表"),
  },
  async execute(args) {
    // 原样回传：结果会出现在 tool_call_update 的 rawOutput 里，作为后端解析兜底
    return JSON.stringify({ ok: true, count: args.suggestions.length, suggestions: args.suggestions })
  },
})
`

const EMIT_REWRITE_TOOL_TS = `import { tool } from "@opencode-ai/plugin"

export default tool({
  description: "提交用户发起的改写任务的结果（替代 emit_suggestions）",
  args: {
    replacement: tool.schema.string().describe("改写后的完整文本（可直接替换原文）"),
    reason: tool.schema.string().optional().describe("一句话说明改写思路"),
  },
  async execute(args) {
    return JSON.stringify({ ok: true, replacement: args.replacement, reason: args.reason ?? "" })
  },
})
`

/** 续写专用工作区：不提供任何工具，agent 只能直接输出文本 */
const COMPLETE_AGENTS_MD = `# 续写引擎

你是文字续写引擎：根据提示词里 \`<<<CONTEXT>>>\` 与 \`<<<END>>>\` 之间的上下文，
只输出「接下来的一小段文字」。

- 一句话以内（≤40 字），与上下文语气一致；
- 不要重复上下文，不要解释，不要加引号，不要调用任何工具。
`

function opencodeConfig(model = config.model): string {
  // 只收紧「会写文件 / 跑命令 / 联网」的权限；不要用 "*": "ask"，
  // 那会让自定义工具（emit_suggestions / emit_rewrite）也走人工授权流程。
  const permission: Record<string, string> = {
    edit: "deny",
    bash: "deny",
    webfetch: "deny",
    websearch: "deny",
  }
  const cfg: Record<string, unknown> = {
    $schema: "https://opencode.ai/config.json",
    permission,
  }
  if (model) cfg.model = model
  return JSON.stringify(cfg, null, 2) + "\n"
}

async function writeIfChanged(file: string, content: string): Promise<void> {
  try {
    const current = await fs.readFile(file, "utf8")
    if (current === content) return
  } catch {
    /* not exists */
  }
  await fs.writeFile(file, content, "utf8")
}

/** 为文档准备 opencode 工作区（AGENTS.md / opencode.json / 自定义 agent / 自定义工具） */
export async function ensureWorkspace(docName: string): Promise<string> {
  const dir = workspaceDir(docName)
  await fs.mkdir(path.join(dir, ".opencode", "agents"), { recursive: true })
  await fs.mkdir(path.join(dir, ".opencode", "tools"), { recursive: true })
  await writeIfChanged(path.join(dir, "AGENTS.md"), AGENTS_MD)
  await writeIfChanged(path.join(dir, "opencode.json"), opencodeConfig())
  await writeIfChanged(path.join(dir, ".opencode", "agents", "coach.md"), COACH_MD)
  await writeIfChanged(path.join(dir, ".opencode", "tools", "emit_suggestions.ts"), EMIT_TOOL_TS)
  await writeIfChanged(path.join(dir, ".opencode", "tools", "emit_rewrite.ts"), EMIT_REWRITE_TOOL_TS)
  return dir
}

/** 续写专用工作区（与主工作区隔离：没有工具、只有续写纪律；可用更快的模型） */
export async function ensureCompletionWorkspace(docName: string): Promise<string> {
  const dir = path.join(workspaceDir(docName), "complete")
  await fs.mkdir(dir, { recursive: true })
  await writeIfChanged(path.join(dir, "AGENTS.md"), COMPLETE_AGENTS_MD)
  await writeIfChanged(path.join(dir, "opencode.json"), opencodeConfig(config.completionModel))
  return dir
}

/** 每次分析前把最新快照写入工作区，供 agent 读取 */
export async function writeSnapshot(docName: string, text: string): Promise<void> {
  const dir = await ensureWorkspace(docName)
  await fs.writeFile(path.join(dir, "document.md"), text, "utf8")
}
