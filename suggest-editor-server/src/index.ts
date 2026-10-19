import { Server } from "@hocuspocus/server"
import { checkDocumentAccess, verifyToken } from "./auth.js"
import { applyPlatformModelDefaults, config } from "./config.js"
import {
  disposeDocument,
  getAnalyzedHashes,
  registerDocument,
  resetAssistantState,
  resumePendingCommands,
  scheduleAnalysis,
  scheduleCommands,
  SUGGEST_ORIGIN,
  warmUpSessions,
} from "./engine.js"
import { loadDocInto, saveDocFrom } from "./storage.js"
import { syncVocabulary } from "./vocabulary.js"

const server = new Server({
  port: config.port,

  async onAuthenticate({ token, documentName, connectionConfig }) {
    // WS 握手鉴权：JWT 由 Flask 签发（HS256，共享密钥），这里自行验签后
    // 再向 Flask 核对文档归属。任何一步不过都直接拒绝连接（fail-closed）。
    const user = verifyToken(token)
    if (!user) throw new Error("未认证：登录令牌缺失或已过期，请重新登录")
    const access = await checkDocumentAccess(documentName, user)
    if (!access.allowed) throw new Error("无权访问该文档")
    if (user.role === "guest") connectionConfig.readOnly = true
  },

  async onLoadDocument({ documentName, document }) {
    // 从数据库回灌历史（首次打开为空）；同时取回「已分析块」缓存，避免重启后重复分析
    const analyzedHashes = await loadDocInto(documentName, document)
    registerDocument(documentName, document, analyzedHashes)
    resetAssistantState(documentName)
    resumePendingCommands(documentName) // 上次没跑完的改写命令续跑
    await syncVocabulary(documentName, document) // 词表：文件 → Y.Map("vocabulary")（供客户端即时补全）
    warmUpSessions(documentName) // 提前拉起 agent 进程（分析与续写两条会话）
  },

  async onChange({ documentName, document, transactionOrigin }) {
    // 忽略建议引擎 / 存储层自己的写入（suggestions、assistant、以及从数据库回灌），
    // 避免「分析 → 写回 → 再分析」的回环
    if (transactionOrigin === SUGGEST_ORIGIN) return
    if (
      transactionOrigin !== null &&
      typeof transactionOrigin === "object" &&
      (transactionOrigin as { source?: string }).source === "local"
    ) {
      return
    }
    registerDocument(documentName, document)
    scheduleAnalysis(documentName)
    scheduleCommands(documentName) // 用户发起的改写/续写命令（写入 commands map 触发）
  },

  async onStoreDocument({ documentName, document }) {
    // Hocuspocus 会做防抖；这里把文档二进制 + 已分析块 hash 存回数据库
    await saveDocFrom(documentName, document, getAnalyzedHashes(documentName))
  },

  async afterUnloadDocument({ documentName }) {
    disposeDocument(documentName)
  },
})

await applyPlatformModelDefaults()
await server.listen()

const agentLabel = config.agentKind === "opencode" ? "opencode acp" : "mock-agent（内置，无需模型 key）"
const modelLabel = config.model || "（未配置，用 opencode 全局配置）"
console.log(`[server] Hocuspocus 已启动：ws://127.0.0.1:${config.port}`)
console.log(`[server] agent：${agentLabel}　模型：${modelLabel}`)
console.log(`[server] Flask：${config.apiUrl}（内部状态仓库，X-Internal-Token 鉴权）`)
console.log(`[server] 数据目录：${config.dataDir}（ws/ agent 工作区；文档正文与建议在数据库）`)
