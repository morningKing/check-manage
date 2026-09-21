# AI 批任务能力总览与创新点

> 定位:总结 AI 批任务子系统的特色能力与创新机制,作为对外说明与架构讲解的
> 配套文档。机制细节分别见《AI批任务容灾与恢复机制.md》(容灾)与
> 《AI子任务动作账本与到位门禁设计.md》(账本与门禁);本文只讲"有什么、
> 为什么独特",不重复实现细节。
>
> 代码基线:`server/utils/batch_engine.py`、`server/utils/chat_persist.py`、
> `server/utils/agent_ledger.py`、`server/utils/action_check_extractor.py`、
> `mcp-server/tools/`、`e2e/ai-full/`。

---

## 1. 一句话总定位

> 把 LLM 的概率性执行,用确定性基础设施——**账本、对账、门禁、看门狗、
> 状态机**——封装成可无人值守、可审计、可恢复、可治理的生产级批处理能力;
> AI 负责单步的智能,系统负责全流程的必然。

能力分层:

```text
任务定义(批任务 / 模板 / 开放 API / AI 定时扫描)
   │  action_checks 期望(入口 A/B) + 暂存文件 + 预置仓库
   ▼
调度层 ──── DB 即账本:FOR UPDATE SKIP LOCKED 认领,状态全在库里
   ├─ 派发前   期望登记 · 长期记忆注入 · 会话工作区准备(可克隆预置仓库)
   ├─ 执行     OpenCode 子会话(主/子代理,每会话独立工作区)
   │            ├─ 看门狗:停滞检测 / 工具卡死检测 / question 自动拒绝
   │            └─ 三通路收敛持久化:REST 轮询 ∥ 浏览器 SSE ∥ 后台监听器
   ├─ 终态     动作账本核对(到位门禁)→ 通过 | 定向重跑(可选) | failed+缺失明细
   └─ 容灾     运行中对账器 · 会话恢复 · 孤儿清扫 · 白名单自动重试
   ▼
观测   实时视图(不依赖 SSE) · 嵌套子代理轨迹气泡 · 执行审计 · SkillOpt 采集
开放   Open API(密钥隔离 + HMAC 回调) · 定时扫描回写业务数据
```

---

## 2. 创新点

### 2.1 任务容灾与恢复机制——"DB 即账本"的三防线体系

全部状态持久化在数据库,调度用 `FOR UPDATE SKIP LOCKED` 从库里认领工作,
内存清空不影响任务定义与状态。进程/整机崩溃后约 10 秒内,运行中对账器把
残账按实际状态分流:能续跑的原地续跑、会话真丢的带准确原因失败、OpenCode
不可达时不批量误杀;再加派发期会话恢复(重建会话+注入历史摘要)与扫描记录
孤儿清扫兜底。详见《AI批任务容灾与恢复机制.md》。

*锚点:`batch_engine._claim_pending_sessions` / `_reconcile_stale_running` /
`_recover_session` / `sweep_orphans`。*

### 2.2 任务对账与门禁机制——从"模型自汇报"到"账本事实"

每次工具调用落成动作账本(`agent_tool_calls`,幂等键 + 条件更新防写放大);
"哪些动作必须发生"以期望形式登记(**四个入口**:批定义、模板、MCP 工具、
管理端),子任务终态由服务端代码逐条机械核对——**tree 作用域把子代理的动作
计入根会话**,不过门落失败并写明缺失明细,账本异常记 inconclusive 不静默
放行。效果断言(file/db_record)在动作级之外叠加"做成了没有"的验证。
详见《AI子任务动作账本与到位门禁设计.md》。

*锚点:`agent_ledger.check_session_gate` / `batch_engine._check_action_gate`,
幂等键 `(oc_session_id, part_id)`。*

### 2.3 无人值守防呆三件套——"没有人可问"是第一约束

批任务子会话天然无人值守,系统把它当机制问题而非提示词问题:

- 模型调用 question 工具(等待人类输入)被**自动拒绝**,轮询检测立即放行,
  不会挂死等一个永远不来的人;
- 停滞看门狗(既无新输出也无工具活动)与工具卡死看门狗(同一调用签名冻结
  超时)分层兜底,子代理委托期间看门狗顺延不误杀;
- 可重试失败(provider 瞬断/停滞/卡死)按白名单自动重新排队,预算用尽才
  落 failed。

*锚点:`QUESTION_CHECK_INTERVAL` 自动拒绝、`STALL_TIMEOUT_SEC` /
`TOOL_STALL_TIMEOUT_SEC` / `RETRYABLE_TURN_FAILED` / `_maybe_auto_retry`。*

### 2.4 原地续跑与三态治理——"已完成的不重做"

失败重试与恢复不重开新会话,而是带 continue_prompt 在**原 OpenCode 会话上
续跑**,已有上下文与产出全部保留(自动重试的注入语就是"已完成的部分不要
重做");暂停/中断/继续三态治理——暂停不占失败计数、运行中协作式取消、
非终态删除被 409 保护需显式 stop。配合重试预算实现"失败不终局"。

*锚点:`is_continue` 续跑模式、`AUTO_RETRY_CONTINUE_PROMPT`、
`_mark_paused` / `_mark_cancelled`、非终态删除 409(有 E2E 覆盖)。*

### 2.5 双通道收敛的实时观测——"没有 SSE 也能看直播"

worker 的 REST 轮询顺带把对话增量持久化(按 OpenCode 消息 id 幂等 upsert),
实时视图不依赖浏览器 SSE 是否在线;浏览器 SSE、后台监听器、批 worker
**三条通路写同一行互为备份**;嵌套子代理以轨迹气泡完整展示(递归发现、
task 工具与 subtask part 按 `state.metadata.sessionId` 精确配对);执行审计
全程记录派发/收敛事件;派发前先挂监听(listener-before-dispatch)保证事件
不丢。详见《AI执行审计能力说明.md》。

*锚点:`_persist_progress` / `_persist_conversation` / `chat_persist.apply_event`
/ `persist_subtasks` / `execution_audit`。*

### 2.6 会话级工作区与预置环境——隔离且可复现

每个子会话独立工作区(各自的 OpenCode 配置、AGENTS.md、uploads/outputs),
输入文件暂存后逐会话落位;支持预置仓库克隆(provision_repo/ref),使项目级
Agent/Skill 在会话启动前就绪,克隆失败降级并在会话内提示——**环境本身成为
任务定义的一部分**。

*锚点:`create_session_workspace` / `batch_staging_dir` /
`write_opencode_config` / `inject_global_skills` / provision_* 列。*

### 2.7 AI 自动提炼门禁期望——治理规则的生成成本归零

AI 一次独立调用(非执行 agent)分析任务文本、Agent 配置与 Skill 步骤,产出
可校验的期望建议;经 schema 校验、历史账本 dry-run、人工确认三道机械关卡后
登记,登记后执行侧无增删路径——**AI 只降低设置成本,不稀释保证强度**。
tree 作用域保证 Skill 步骤常由子代理执行时依然可核对。

*锚点:`action_check_extractor.extract_action_checks` / 对话框「AI 提炼」。*

### 2.8 平台化外延——别人系统也能用,业务数据也能驱动

**开放 API:让第三方系统批量使用 AI 批任务,全程不需要人打开网页。**
场景:业务系统有一批文件要 AI 处理(比如每天新增的巡检报告),由它的后端
自动完成。用法三步:管理员发放一把 API Key → 第三方系统拿 Key 调接口
(上传文件、创建批任务)→ 批任务跑完后,平台**主动回调通知**第三方系统
"做完了",通知带 HMAC 签名可验证来源,第三方不用反复来查询。隔离:每把
Key 只能看到自己创建的任务,多家接入互不可见。

**AI 定时扫描任务:让业务数据自动驱动 AI 处理,结果直接写回业务表。**
场景:数据页里持续产生需要 AI 处理的记录(新巡检记录要生成用例、新工单要
分类)。配置一次即可:选定数据页、筛选条件(如"状态 = 待处理")、提示词、
结果回写的目标字段——到点自动挑出待处理记录批量执行,完成后把记录状态改为
"已处理"、AI 结果写入业务字段。中断留下的记录会被自动恢复重试,不丢也不
卡死。

两者合起来:AI 批处理从"人在网页上点出来的功能",变成第三方系统可编排、
业务数据可驱动的平台能力。

*锚点:`routes/open_api_batches.py`(API Key 隔离、callback_url/secret 回
调签名)、`ai_scan_tasks` 调度与 `jsonb_set` 字段回写、`sweep_orphans` 孤儿
恢复。*

### 2.9 Skill 治理闭环(SkillOpt)——技能从"写了就行"到可度量

执行审计之上叠加 Skill 调用采集:runtime 已确认事件与启发式推断双源
(`evidence_level`),`skill_hash` 支撑版本对比;优化建议的接受/拒绝/应用与
**前后指标**落库(`ai_suggestion_feedback`),形成"采集→诊断→建议→验证效果"
的技能优化闭环,配合 trace-analyzer 技能与 `analyze_trace` 工具可交互式归因。

*锚点:`utils/skillopt.py` / `ai_skill_invocations` / `ai_suggestion_feedback`
/ `utils/trace_auditor.py`。*

### 2.10 长期记忆随任务流动

子任务启动时按用户维度检索长期记忆注入 prompt(无人值守也能带着历史偏好
干活);回合结束后被动抽取记忆沉淀;用户亦可主动增删(mem0 + Chroma,按
user_id 隔离)。记忆围绕用户原文组织,批任务样板指令不混入。

*锚点:`_run_one` 中 `search_memory` / `render_memory_block`、
`extract_from_turn`、`memory_search/add/delete` MCP 工具。*

---

## 3. 特色功能一览

| # | 特色 | 一句话 | 主要锚点 |
|---|---|---|---|
| 1 | 容灾与恢复 | DB 即账本,三防线对账,崩溃 10 秒自愈 | `_reconcile_stale_running` 等 |
| 2 | 对账与门禁 | 工具调用账本 + 期望机械核对,tree 含子代理 | `agent_ledger` |
| 3 | 无人值守防呆 | question 自动拒绝 + 双看门狗 + 白名单重试 | `batch_engine` 常量区 |
| 4 | 原地续跑与治理 | continue 同会话不重做;暂停/中断/继续三态 | `AUTO_RETRY_CONTINUE_PROMPT` |
| 5 | 实时观测 | 三通路收敛持久化,无 SSE 也看直播;子代理气泡 | `_persist_conversation` |
| 6 | 工作区与预置环境 | 每子会话隔离,预置仓库使项目 Agent/Skill 就绪 | `create_session_workspace` |
| 7 | AI 提炼期望 | AI 分析任务/Skill 自动生成门禁建议,三道机械校验 | `action_check_extractor` |
| 8 | 开放与定时流水线 | 第三方系统可编排批任务(完成后主动回调通知);AI 定时扫描自动处理业务数据并回写结果 | `open_api_batches` / `ai_scan_tasks` |
| 9 | SkillOpt 闭环 | 双源采集 + 建议效果追踪,技能可度量可优化 | `skillopt` |
| 10 | 长期记忆流动 | 子任务带记忆执行,结果沉淀回记忆 | `search_memory` / `extract_from_turn` |

---

## 4. 质量佐证

- 单元与集成:server 2154 passed(含门禁 22 例:纯函数 + 真实库集成)、
  mcp-server 150 passed;前端 vitest 1198 passed + vue-tsc 干净。
- 真实链路 E2E:`e2e/ai-full/` 覆盖批任务生命周期、子代理轨迹、执行审计、
  开放 API 与动作门禁四场景(UI 阻断 / gate fail / gate pass / 多子代理
  tree 作用域),全部通过并留证 `e2e/screenshots/ai-full/`。
- 一次真实的多子代理运行即可同时验证:账本采集(子代理动作入账)、tree
  作用域核对、页面「门禁 ✓」徽标与终态治理全链路。
