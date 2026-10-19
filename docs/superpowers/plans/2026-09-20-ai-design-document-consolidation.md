# AI 设计文档统一 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (recommended) or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将分散的 AI 设计文档统一到 `docs/design/09-AI智能助手.md`，删除三份重复文档，保留专题迁移入口，并补齐五张可复用 SVG 示意图。

**Architecture:** 以 `09-AI智能助手.md` 作为唯一权威文档，按能力域组织 Chat、批任务、流水线、执行审计/轨迹、容灾和运行时内容；专题源文档保留为可追溯入口并在顶部声明迁移关系。SVG 作为 `docs/design/assets/` 下的自包含设计资产，由主文档按章节引用。

**Tech Stack:** Markdown、相对链接、SVG/XML、PowerShell/Python 只读校验脚本、Git。

**Spec:** `docs/superpowers/specs/2026-09-20-ai-design-document-consolidation.md`

## Global Constraints

- 仅修改 `docs/design` 文档/索引/SVG 与本规格/计划，不改业务代码。
- 只删除 `11-AI-trace-analyzer-skill-spec.md`、`11-AI执行轨迹分析.md`、`12-AI-Chat优化需求文档.md`。
- `archive/ai-chat-design.md` 与 `archive/inspection-skill-design.md` 保留；根目录专题文档保留。
- `09-AI智能助手.md` 是 AI 设计的唯一权威来源；旧规划不能伪装成已实现功能。
- 新 SVG 不依赖外部图片、脚本或字体，引用路径必须存在。
- 删除后不能留下指向三份文件的 Markdown 链接。

---

### Task 1: 完成主文档统一结构

**Files:**
- Modify: `docs/design/09-AI智能助手.md`
- Reference: `docs/superpowers/specs/2026-09-20-ai-design-document-consolidation.md`
- Source: `docs/design/11-AI-trace-analyzer-skill-spec.md`
- Source: `docs/design/11-AI执行轨迹分析.md`
- Source: `docs/design/12-AI-Chat优化需求文档.md`
- Source: `docs/design/13-AI批任务实时进度与编排设计.md`
- Source: `docs/design/AI执行审计能力说明.md`
- Source: `docs/design/AI批任务容灾与恢复机制.md`
- Source: `docs/design/AI能力总览与长任务稳定性.md`
- Source: `docs/design/OpenCode运行时依赖与部署.md`
- Source: `docs/design/archive/ai-chat-design.md`
- Source: `docs/design/archive/inspection-skill-design.md`

**Interfaces:**
- Produces: `09-AI智能助手.md` 中稳定的章节链接和 SVG 引用，供 README、专题迁移横幅和后续用户指南引用。

- [ ] **Step 1: 建立章节映射清单**

将源内容映射到以下固定章节：总览/架构、运行时、数据模型、Chat 体验、批任务、扫描流水线、轨迹审计、容灾恢复、记忆/接口/安全、验收/演进。删除过时的独立编号和重复的背景叙述。

- [ ] **Step 2: 重写主文档头部和总览**

保留现有域⑨定位和浏览器→Flask→OpenCode→MCP 边界，增加“唯一权威文档”说明、已实现/规划状态标记规则，以及 AI 能力总览表。

- [ ] **Step 3: 合并运行时、数据模型和 Chat 能力**

纳入 OpenCode v1.15.1 部署事实、全局/工作区 Skill 与 Agent 生效语义；纳入执行审计 Attempt/Prompt/Manifest/Event/Contract/Step/Diagnosis 表；将 F1–F10 按“当前实现/规划”分类，保留验收约束但移除已证实过时的 `total_tokens`、`ai_chat_audit_log` 等假设。

- [ ] **Step 4: 合并批任务、扫描、轨迹和容灾流程**

纳入 DB version + SSE、动态并发、优先级、暂停/恢复；扫描 JSON 契约与 `jsonb_set` 回写；`trace-analyzer` 的只读 MCP 调用、证据分级、子代理递归和建议安全边界；三层恢复防线、看门狗、自动重试和孤儿清扫。

- [ ] **Step 5: 补充接口、边界、验收与来源索引**

整理当前接口和规划接口，明确 owner/RBAC、unknown_due_to_missing_data、分析不阻塞主链路、Flask 唯一记忆写入等约束；在末尾列出已迁移源文档和专题入口。

- [ ] **Step 6: 运行 Markdown 结构检查**

检查标题层级、重复标题、悬空相对链接和五个 SVG 预期引用；发现问题在本任务内修复。

---

### Task 2: 新增 AI 能力 SVG 资产

**Files:**
- Create: `docs/design/assets/ai-chat-context.svg`
- Create: `docs/design/assets/ai-batch-orchestration.svg`
- Create: `docs/design/assets/ai-trace-audit.svg`
- Create: `docs/design/assets/ai-resilience.svg`
- Create: `docs/design/assets/ai-opencode-runtime.svg`
- Modify: `docs/design/09-AI智能助手.md`

**Interfaces:**
- Produces: 五个稳定的相对 SVG 资源路径；主文档每张图只引用一次或在相关章节复用。

- [ ] **Step 1: 编写 Chat 上下文 SVG**

绘制用户输入、@文件/Agent、ContextStatusBar、Flask SSE、OpenCode、消息持久化和通知的流程；标出文本附件内联与二进制工具读取分支。

- [ ] **Step 2: 编写批任务编排 SVG**

绘制 BatchWorker 写 DB version、SSE generator、前端 store、降级轮询，以及 priority/paused/concurrency 认领谓词。

- [ ] **Step 3: 编写轨迹审计 SVG**

绘制 Attempt → Prompt/Manifest/Event → 消息/子代理轨迹 → 确定性审计 → trace-analyzer Skill → 诊断报告，标出 confirmed/inferred/unknown。

- [ ] **Step 4: 编写容灾 SVG**

绘制 DB 账本、stale-running reconcile、OpenCode session recovery、retry/watchdog 和 scan orphan sweep 的三层防线与终态分流。

- [ ] **Step 5: 编写 OpenCode runtime SVG**

绘制 Flask、workspace、opencode.json、OpenCode serve、global/project Skill/Agent、MCP Server、LLM Provider 和 RBAC token 链路。

- [ ] **Step 6: 在主文档对应章节插入 SVG 引用**

每张图前加一句用途说明，使用 `./assets/<name>.svg` 相对路径，不使用已不存在的 PNG 占位。

- [ ] **Step 7: 解析 SVG XML**

使用 Python 标准库 `xml.etree.ElementTree` 逐个解析五张 SVG，确认 XML 完整、根元素为 `<svg>`、无外部实体/脚本引用。

---

### Task 3: 修复索引和保留文档的迁移入口

**Files:**
- Modify: `docs/design/README.md`
- Modify: `docs/design/13-AI批任务实时进度与编排设计.md`
- Modify: `docs/design/AI执行审计能力说明.md`
- Modify: `docs/design/AI批任务容灾与恢复机制.md`
- Modify: `docs/design/AI能力总览与长任务稳定性.md`
- Modify: `docs/design/OpenCode运行时依赖与部署.md`
- Modify: `docs/design/archive/ai-chat-design.md`
- Modify: `docs/design/archive/inspection-skill-design.md`

**Interfaces:**
- Produces: 所有保留 AI 文档顶部的权威来源链接；README 不再暴露已删除文档入口。

- [ ] **Step 1: 为根目录专题文档添加迁移横幅**

在标题后加入“专题细节已迁移到 `09-AI智能助手.md`；本文保留实施/部署背景和专题验收细节”的引用，不删除原内容。

- [ ] **Step 2: 更新归档文档横幅**

保留原有归档语义，补充“平台 AI 权威设计以 09 为准”，避免归档文档被误读为独立规范。

- [ ] **Step 3: 更新 README 横切关注点和历史文档表**

删除 11/12 已删除文件条目；将批任务、审计、容灾、OpenCode 入口描述为专题迁移入口，并指向 09；保留 archive 表但说明内容已吸收。

- [ ] **Step 4: 全仓库搜索失效引用**

搜索三个已删除文件名、`screenshot.png`、`chart.png` 和旧的独立轨迹链接，逐条替换为 09 章节或专题迁移入口。

---

### Task 4: 删除指定重复文档

**Files:**
- Delete: `docs/design/11-AI-trace-analyzer-skill-spec.md`
- Delete: `docs/design/11-AI执行轨迹分析.md`
- Delete: `docs/design/12-AI-Chat优化需求文档.md`

**Interfaces:**
- Produces: Git 删除记录；所有有效内容已在 Task 1 中可追溯到 09。

- [ ] **Step 1: 确认主文档已包含迁移内容**

在删除前搜索主文档中的 `trace-analyzer`、`analyze_trace`、F1–F10、SSE 编排、执行审计和容灾关键词，确保迁移完成。

- [ ] **Step 2: 删除三份文件**

使用版本控制删除，不删除同目录的其他专题文档或 SVG。

- [ ] **Step 3: 检查 Git diff**

确认删除仅限三份指定文件，未误删 `13` 或 `archive` 文档。

---

### Task 5: 完整性和回归校验

**Files:**
- Test/inspect: `docs/design/**/*.md`, `docs/design/**/*.svg`

- [ ] **Step 1: 校验指定文件不存在**

运行：`test ! -e docs/design/11-AI-trace-analyzer-skill-spec.md && test ! -e docs/design/11-AI执行轨迹分析.md && test ! -e docs/design/12-AI-Chat优化需求文档.md`

预期：退出码 0。

- [ ] **Step 2: 校验已删除文件名无入向引用**

运行：`git grep -n -E '11-AI-trace-analyzer-skill-spec|11-AI执行轨迹分析|12-AI-Chat优化需求文档|screenshot\.png|chart\.png' -- ':!docs/superpowers/plans/*'`

预期：无输出；若历史提交或迁移说明需要提及，改为不形成 Markdown 链接的文字或移除。

- [ ] **Step 3: 校验 Markdown 链接目标**

用 Python 扫描 `docs/design/**/*.md` 的相对 Markdown 链接，忽略 URL、锚点和代码块，确认本地目标文件存在。

- [ ] **Step 4: 校验五张 SVG**

用 `xml.etree.ElementTree.parse` 解析五个新文件，确认根节点、viewBox、文本内容和无外部引用。

- [ ] **Step 5: 审查 diff 与状态**

运行：`git diff --stat`、`git diff -- docs/design/09-AI智能助手.md docs/design/README.md docs/design/assets`、`git status --short`。

预期：主文档、README、五张 SVG、迁移横幅和三份删除文件与规格一致；不出现业务代码改动。

- [ ] **Step 6: 提交前报告事实**

记录删除文件、主文档新章节、SVG 文件名、链接扫描和 XML 解析结果；若任何校验失败，修复后重新运行，不宣称完成。
