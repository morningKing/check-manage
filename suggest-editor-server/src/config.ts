import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const here = path.dirname(fileURLToPath(import.meta.url))

/**
 * 读取 Flask 侧的 server/.env，让两个进程共享同一份部署配置
 * （JWT_SECRET / MCP_INTERNAL_TOKEN / OPENCODE_* 等）。
 * 与 python-dotenv 的 override=False 语义一致：真实环境变量永远赢。
 */
function loadServerDotEnv(): void {
  const envPath = path.resolve(here, "../../server/.env")
  try {
    const raw = fs.readFileSync(envPath, "utf8")
    for (const line of raw.split(/\r?\n/)) {
      const trimmed = line.trim()
      if (!trimmed || trimmed.startsWith("#")) continue
      const eq = trimmed.indexOf("=")
      if (eq <= 0) continue
      const key = trimmed.slice(0, eq).trim()
      const value = trimmed.slice(eq + 1).trim().replace(/^["']|["']$/g, "")
      if (!(key in process.env)) process.env[key] = value
    }
  } catch {
    /* 无 server/.env：全部走进程环境变量（dev:all 场景常见） */
  }
}
loadServerDotEnv()

/** 内置 mock agent 的入口（AGENT_KIND=mock 时使用） */
export const MOCK_AGENT_PATH = path.resolve(here, "../mock-agent.mjs")

export type AgentKind = "mock" | "opencode"

const agentKind: AgentKind = process.env.AGENT_KIND === "opencode" ? "opencode" : "mock"

/**
 * 把已知的 opencode 安装目录前置到 PATH。
 * 这样启动子进程时用的是固定的字面量命令名（opencode / opencode.exe），
 * 不存在任何运行时可变的「命令字符串」，也不经过 shell。
 * （Windows 上 npm 全局安装的 opencode 是 .cmd 包装器，无法直接 exec，
 *   因此这里定位到其背后的 opencode.exe 所在目录。）
 */
function prepareAgentPath(): void {
  const prefixes: string[] = []
  if (process.env.OPENCODE_DIR) prefixes.push(process.env.OPENCODE_DIR)
  if (process.platform === "win32" && process.env.APPDATA) {
    prefixes.push(path.join(process.env.APPDATA, "npm", "node_modules", "opencode-ai", "bin"))
    prefixes.push(path.join(process.env.APPDATA, "npm"))
  }
  if (process.env.HOME) prefixes.push(path.join(process.env.HOME, ".opencode", "bin"))
  const present = prefixes.filter((dir) => {
    try {
      return fs.existsSync(dir)
    } catch {
      return false
    }
  })
  if (present.length > 0) {
    process.env.PATH = [...present, process.env.PATH ?? ""].join(path.delimiter)
  }
}
prepareAgentPath()

/**
 * Flask 未设置 JWT_SECRET 时用的公开默认值（server/config.py 同款）。
 * 只影响本地开发；与 Flask 保持同一把钥匙，WS 握手验签才能通过。
 */
const JWT_DEV_DEFAULT = "dev-only-insecure-jwt-secret-do-not-use-in-production"

const configuredJwtSecret = (process.env.JWT_SECRET ?? "").trim()
if (!configuredJwtSecret) {
  console.warn(
    "[config] 未设置 JWT_SECRET，WS 鉴权使用源码里的公开默认值 —— 仅限本地开发，" +
      "且必须与 Flask 侧配置一致，否则编辑器连接会被拒绝。",
  )
}

export const config = {
  /** WebSocket 端口（Hocuspocus） */
  port: Number(process.env.PORT ?? 1234),
  /** 用户停止输入多久后触发分析 */
  debounceMs: Number(process.env.SUGGEST_DEBOUNCE_MS ?? 800),
  /** 单轮 agent 分析的超时（真 opencode 首次调用含进程启动，实测可达 ~70s，故给足余量） */
  analyzeTimeoutMs: Number(process.env.AGENT_TIMEOUT_MS ?? 150_000),
  /** 建议保留时长（毫秒），过期自动清理 */
  suggestionTtlMs: Number(process.env.SUGGEST_TTL_MS ?? 30 * 60_000),
  /** agent 工作区与词表的磁盘根目录（文档正文/建议本体在数据库，这里只有派生文件） */
  dataDir: process.env.DATA_DIR ?? path.resolve(here, "../.data"),
  /** Flask 后端地址（文档元数据 + Yjs 状态的持久化通道） */
  apiUrl: (process.env.SE_API_URL ?? "http://127.0.0.1:3002").replace(/\/+$/, ""),
  /** 调 Flask 内部端点用的共享密钥（与 MCP_INTERNAL_TOKEN 同源；空 = 所有内部调用都会 403） */
  internalToken: (process.env.MCP_INTERNAL_TOKEN ?? "").trim(),
  /** WS 握手验签用的 JWT 密钥（与 Flask server/config.py 同源） */
  jwtSecret: configuredJwtSecret || JWT_DEV_DEFAULT,
  /**
   * agent 类型（只支持两种，均为本文件固定的启动方式）：
   * - mock：内置确定性规则 agent，无需模型 key，先把链路跑通；
   * - opencode：本机安装的 opencode，以官方 ACP 模式（`opencode acp`）接入。
   */
  agentKind,
  /** 传给 opencode 的模型（可选，写入工作区 opencode.json） */
  model: process.env.AGENT_MODEL ?? "",
  /** 续写联想单独指定模型（可选）：联想对延迟敏感，建议指向更快的模型 */
  completionModel: process.env.AGENT_MODEL_COMPLETE ?? process.env.AGENT_MODEL ?? "",
}
