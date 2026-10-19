import fs from "node:fs/promises"
import path from "node:path"
import { config } from "./config.js"

export function workspaceDir(docName: string): string {
  const safe = docName.replace(/[^a-zA-Z0-9._-]+/g, "_").slice(0, 80) || "doc"
  return path.join(config.dataDir, "ws", safe)
}

const AGENTS_MD = `# 工作区纪律

你是文档写作教练，\`document.md\` 是要分析的文档快照——**每个任务的提示词都会给出它的完整绝对路径，用它**（不要自行推断路径）。

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
1. 用提示词给出的**完整路径**读取 document.md（若提示词指定了范围，只看该范围）。
2. 按提示词中给出的「检查要点」逐条核对；提示词未给要点时，找通用的写作问题：
   冗余啰嗦、表述歧义、逻辑跳跃、语病与错别字、语气不一致。
3. 逐条调用 emit_suggestions 提交建议：
   - quote 从原文逐字复制（含标点），不要给字符偏移量；
   - replacement 给出可直接替换的完整文本，纯删除用空字符串；
   - reason 一句话讲清为什么（命中检查要点时注明要点名）；
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

/** 每次分析前把最新快照写入工作区，供 agent 读取；返回快照的绝对路径
 * （提示词里直接给绝对路径——ACP 下模型对"项目根"的推断可能把相对路径解析错，
 * 实测有模型拼出双写路径读不到文件、整轮空转）。 */
export async function writeSnapshot(docName: string, text: string): Promise<string> {
  const dir = await ensureWorkspace(docName)
  const file = path.join(dir, "document.md")
  await fs.writeFile(file, text, "utf8")
  return file
}

/* ------------------------------- 检查要点（全局） -------------------------------
 * 技术文档的业务描述/逻辑检查点由管理员在后台维护（真源 = 检查要点-全局.txt），
 * 每轮分析前读取并逐条注入分析 prompt；AGENT_KIND=mock 时不生效（mock 是
 * 确定性规则 agent，不读提示词语义）。改要点只影响之后分析的段落——已分析块
 * 命中块级缓存跳过，存量全文重查需重新触发编辑。 */

/** 检查要点文件（全局，对所有文档生效） */
export function checklistPath(): string {
  return path.join(config.dataDir, "检查要点-全局.txt")
}

export const DEFAULT_CHECKLIST = `# 检查要点：AI 分析时逐条核对（每行一条，# 开头为注释；后台可维护）
# 命中要点的问题请在 reason 里注明对应要点，便于作者定位。
# —— 业务与数据描述 ——
数据口径一致：同一指标在全文中的名称、单位、精度、统计口径是否一致
数量自洽：总数与分项之和、百分比、状态描述前后是否对得上
术语一致：同一概念是否中途换叫法（中英混用、缩写未先定义、新旧系统名混称）
主体与归属明确：责任方、系统模块、操作对象是否写清，避免无主语的「系统会自动…」
—— 逻辑与流程 ——
因果成立：结论是否真的由前文推导得出，有无逻辑跳跃或以偏概全
时序合理：步骤先后、依赖关系、并发与竞态是否交代清楚且顺序正确
边界与例外：空值、超限、失败、回滚、兜底路径是否有着落
条件完备：条件分支是否成对，遗漏的默认分支是否说明
—— 一致性与可执行性 ——
前后文一致：同一流程在不同章节的描述（接口、字段、配置名）是否一致
可执行：操作步骤能否照做（环境、前置条件、预期结果齐全）
安全与权限：涉及权限、数据暴露、外部依赖的描述符合最小权限与合规口径
—— 表达质量 ——
冗余与歧义：删除冗余修饰，消除指代不明的「它/该/上述」
书面规范：口语化连接词、绝对化用语（肯定/绝对/完美）改为审慎表述
`

/** 首次使用时播种默认清单（已存在则不动，后台编辑后以此为准） */
export async function ensureGlobalChecklistFile(): Promise<string> {
  const file = checklistPath()
  try {
    await fs.access(file)
  } catch {
    await fs.mkdir(path.dirname(file), { recursive: true })
    await fs.writeFile(file, DEFAULT_CHECKLIST, "utf8")
  }
  return file
}

/** 读取检查要点（非注释行）；文件缺失时先播种默认清单 */
export async function readChecklistEntries(maxEntries = 40): Promise<string[]> {
  const file = await ensureGlobalChecklistFile()
  const entries: string[] = []
  try {
    const content = await fs.readFile(file, "utf8")
    for (const rawLine of content.split(/\r?\n/)) {
      const line = rawLine.trim()
      if (!line || line.startsWith("#")) continue
      entries.push(line)
      if (entries.length >= maxEntries) break
    }
  } catch {
    /* 读不到就当空清单，prompt 退回通用措辞 */
  }
  return entries
}
