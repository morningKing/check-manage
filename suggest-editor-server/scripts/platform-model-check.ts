/**
 * 平台默认模型兜底路径验证（一次性探针）：
 *   AGENT_MODEL= DATA_DIR=<tmp> npx tsx scripts/platform-model-check.ts
 * 预期：日志出现「采用平台后台默认模型：…」，且新生成的工作区 opencode.json
 * 的 model 字段 = 平台 AI 配置页的默认模型。
 */
import { applyPlatformModelDefaults, config } from "../src/config.js"
import { ensureWorkspace } from "../src/workspace.js"
import fs from "node:fs/promises"
import path from "node:path"

async function main() {
  console.log("[check] env AGENT_MODEL =", JSON.stringify(process.env.AGENT_MODEL))
  await applyPlatformModelDefaults()
  console.log("[check] 生效模型 config.model =", JSON.stringify(config.model))
  console.log("[check] 续写模型 config.completionModel =", JSON.stringify(config.completionModel))

  const dir = await ensureWorkspace("platform-model-probe")
  const raw = await fs.readFile(path.join(dir, "opencode.json"), "utf8")
  const cfg = JSON.parse(raw) as { model?: string }
  console.log("[check] 工作区 opencode.json model =", JSON.stringify(cfg.model))
  if (!config.model || cfg.model !== config.model) {
    console.error("[check] FAIL：工作区模型与生效模型不一致")
    process.exit(1)
  }
  console.log("[check] PASS")
  await fs.rm(dir, { recursive: true, force: true })
}

main().catch((err) => {
  console.error("[check] FAIL:", err)
  process.exit(1)
})
