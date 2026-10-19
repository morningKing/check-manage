import { Server } from "@hocuspocus/server"
import { checkDocumentAccess, verifyTokenDetailed } from "./auth.js"
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

/** 统一带时间戳的日志（排障时与 proxy 控制台/浏览器控制台三处对齐时间线） */
function log(scope: string, message: string): void {
  console.log(`${new Date().toISOString()} [${scope}] ${message}`)
}

const server = new Server({
  port: config.port,

  async onConnect({ documentName, socketId }) {
    log("ws", `连接建立 doc=${documentName} socket=${socketId}（等待鉴权）`)
  },

  async onAuthenticate({ token, documentName, connectionConfig, socketId }) {
    // WS 握手鉴权：JWT 由 Flask 签发（HS256，共享密钥），这里自行验签后
    // 再向 Flask 核对文档归属。任何一步不过都直接拒绝连接（fail-closed），
    // 拒绝原因写入服务端日志，同时随错误消息回到浏览器（前端顶栏会显示）。
    const check = verifyTokenDetailed(token)
    if (!check.ok) {
      log("ws", `鉴权拒绝 doc=${documentName} socket=${socketId}：${check.reason}`)
      throw new Error(check.reason)
    }
    const user = check.payload
    const access = await checkDocumentAccess(documentName, user)
    if (!access.allowed) {
      log("ws", `鉴权拒绝 doc=${documentName} socket=${socketId} user=${user.userId}：${access.note ?? "未知原因"}`)
      throw new Error(access.note ?? "无权访问该文档")
    }
    if (user.role === "guest") connectionConfig.readOnly = true
    log("ws", `鉴权通过 doc=${documentName} socket=${socketId} user=${user.userId} role=${user.role ?? "-"}`)
  },

  async onDisconnect({ documentName, socketId, clientsCount }) {
    log("ws", `连接关闭 doc=${documentName} socket=${socketId}（该文档剩余客户端 ${clientsCount}）`)
  },

  async onLoadDocument({ documentName, document }) {
    // 从数据库回灌历史（首次打开为空）；同时取回「已分析块」缓存，避免重启后重复分析
    const analyzedHashes = await loadDocInto(documentName, document)
    registerDocument(documentName, document, analyzedHashes)
    resetAssistantState(documentName)
    resumePendingCommands(documentName) // 上次没跑完的改写命令续跑
    await syncVocabulary(documentName, document) // 词表：文件 → Y.Map("vocabulary")（供客户端即时补全）
    warmUpSessions(documentName) // 提前拉起 agent 进程（分析与续写两条会话）
    log("doc", `文档加载 doc=${documentName}（历史状态与已分析缓存已回灌）`)
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
    try {
      await saveDocFrom(documentName, document, getAnalyzedHashes(documentName))
    } catch (error) {
      log("store", `保存失败 doc=${documentName}：${error instanceof Error ? error.message : String(error)}`)
      throw error
    }
  },

  async afterUnloadDocument({ documentName }) {
    disposeDocument(documentName)
    log("doc", `文档卸载 doc=${documentName}（agent 会话已回收，状态已落库）`)
  },
})

await applyPlatformModelDefaults()
await server.listen()

const agentLabel = config.agentKind === "opencode" ? "opencode acp" : "mock-agent（内置，无需模型 key）"
const modelLabel = config.model || "（未配置，用 opencode 全局配置）"
log("server", `Hocuspocus 已启动：ws://127.0.0.1:${config.port}`)
log("server", `agent：${agentLabel}　模型：${modelLabel}`)
log("server", `Flask：${config.apiUrl}（内部状态仓库，X-Internal-Token 鉴权）`)
log(
  "server",
  config.internalToken
    ? `内部通道 token：已配置（len=${config.internalToken.length}）`
    : "内部通道 token：未配置 —— 鉴权核对与状态落库都会 403（检查 server/.env 的 MCP_INTERNAL_TOKEN 后重启本服务）",
)
log("server", `数据目录：${config.dataDir}（ws/ agent 工作区；文档正文与建议在数据库）`)
