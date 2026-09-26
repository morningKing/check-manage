# AI 能力全量测试（目录索引）

本目录是 check-manage 平台 **AI 能力全量测试**的完整归档：方案、用例、执行报告、缺陷修复、单测效率优化报告与测试证据。全部 Playwright 测试套件与测试中间件随仓库提交，供后续全量 AI 回归直接复用。

## 文档清单

| 文档 | 内容 |
|------|------|
| [01-测试方案.md](./01-测试方案.md) | 范围（九大 AI 能力族）、维度（功能/健壮性/可靠性/安全性/效率）、环境、三层策略 |
| [02-AI功能测试用例.md](./02-AI功能测试用例.md) | ~90 条用例（TC-CHAT/BATCH/SCAN/API/MCP/MEM/GOV/MISC/SUB/PERF） |
| [03-测试执行报告.md](./03-测试执行报告.md) | 三层测试执行结果与证据 |
| [04-缺陷记录与修复.md](./04-缺陷记录与修复.md) | D1–D7 修复 + Accepted 刻意行为清单 |
| [05-单测效率优化报告.md](./05-单测效率优化报告.md) | 前后端单测提速（前后对照留证） |
| [06-AI Harness 实现审查报告.md](./06-AI Harness 实现审查报告.md) | P0/P1/P2 三份 spec 的实现审查：8 项严重 + 14 项中等缺陷、验收逐项对照、测试缺口与修复顺序 |
| [07-审查缺陷修复报告.md](./07-审查缺陷修复报告.md) | 对 06 号缺陷的逐项处置（H1–H8 / M 层）与全量验证证据（修复方出具） |
| [08-审查缺陷修复复核报告.md](./08-审查缺陷修复复核报告.md) | 对 07 号处置的独立复核：H/M 层逐条判定、修复引入的新缺陷、报告失真点、运行态缺口与回修清单 |
| evidence/ | 关键截图与结果留证 |

## 可复用测试资产（随仓库提交）

| 资产 | 位置 | 运行方式 |
|------|------|----------|
| 全量 AI E2E 套件（5 个 spec + 共享助手） | `e2e/ai-full/` | `npx playwright test e2e/ai-full/` |
| API 层/安全回归（pytest，真实 DB 链路） | `server/tests/test_batch_standalone_prompt_retry.py` 等 10 个新文件 | `npm run test:server` |
| 前端 AI 组件/Store 测试 | `src/components/ai-chat/__tests__/BatchGroup.test.ts` 等 4 个新文件 | `npm run test` |
| 既有 AI E2E（冒烟/批任务/审计/子代理等 19 个 spec） | `e2e/*.spec.ts` | `npm run test:e2e` |

## 环境要求（复用套件时）

1. Postgres（`server/config.py`）；2. MCP Server :3003（`npm run mcp`）；3. OpenCode :4096；
4. Flask :3002（`npm run server`，**改后端代码必须手动重启**）；5. Vite :5173（`npm run dev`）。
`npm run dev:all` 一键拉起后三件套。E2E 登录态 `e2e/.auth/admin.json`（admin/admin123）。

## 已知并行限制

- 后端 pytest 含与 dev 后端共享真实 DB 的集成用例（认领/对账器会触碰全局 running 行），
  **不能与 dev 后端以外的进程级并行混跑**；`npm run test:server:fast`（xdist loadfile）仅建议在
  无 dev 后端、且接受偶发顺序敏感用例抖动时使用，详见 05 号文档。
- Playwright `workers=1` 是刻意配置（全部 spec 共享 admin 账号与活跃 AI 会话），不要调大。
