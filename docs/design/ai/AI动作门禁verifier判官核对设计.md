# 动作门禁升级：verifier 判官核对（从「做了」到「做对了」）设计

- 日期：2026-09-29
- 状态：已评审（brainstorming 产出，待实现计划）
- 相关：`docs/design/ai/AI子任务动作账本与到位门禁设计.md`（现行门禁）、`server/utils/agent_ledger.py`、`server/utils/batch_engine.py`、`server/utils/action_check_extractor.py`

## 1. 背景与问题

现行动作门禁（`action_expectations` 三类原语 + `check_session_gate`）只能核对「动作是否做了」：

| 原语 | 现有能力 | 盲区 |
|---|---|---|
| `tool` | 账本中 tool + args 正则命中 ≥min_count 次 | 不看调用结果 |
| `file` | 工作区 glob 命中文件存在 | 不看内容 |
| `db_record` | dynamic_data 集合 + Mongo filter 命中数 | 表达力是查询不是断言 |

业务需要核对「动作是否做对了」：**过程正确**（调用顺序/参数取值/是否报错）与**回复语义**（最终回复是否满足要求要点）。两者都无法穷举为确定性断言。

## 2. 决策记录（brainstorming 结论）

- 判定对象：过程正确 + 回复语义。
- 判定材料：最终回复 + 过程轨迹（不含产物文件全文）。
- 过程正确性 v1 **全部并入判官 rubric**，不加结构化过程原语（调用顺序约束、args JSON 断言等留待后续，schema 不排除）。
- 执行体选型：**方案 B——隐藏 OpenCode verifier 会话**（主动取证：判官可只读地打开工作区文件核对，而不是只看平台喂给它的材料）。否决的方案 A（服务端直调 LLM）与 C（OC 插件内判定）见 brainstorming 记录；A 的材料组装层与本设计共享，未来如需降本可平滑切换执行体。

## 3. 目标与非目标

**目标**
1. 新增第四类门禁期望 `check_type: 'verifier'`：自然语言 rubric，终态核对时由 verifier 会话逐条判定 passed/failed（含理由与证据）。
2. verifier 失败条目驱动既有 `gate_retry` 定向修复闭环（理由即修复提示），机制零改动或最小改动。
3. 故障语义与现有门禁完全一致：核对不可证实 → fail-closed（子任务 failed，`gate_status='inconclusive'`）。

**非目标（YAGNI）**
- 不新增结构化过程原语（顺序约束 / args JSON 断言）。
- 不改账本 schema（v1 判定材料不要求记录工具输出；轨迹用现有 `args_text`/`state`/`occurred_at`）。
- 不做 DB DDL（`check_type` 为代码层枚举校验，`effect_spec` 为 jsonb，新增枚举值无需迁移）。
- verifier 不承载轨迹分析、通知等其他平台职责。

## 4. 总体架构

```
子任务回合收敛
  └─ _run_one 收尾段（现有位置不动）
       ├─ check_session_gate          纯 DB 证据原语（现状，不动）
       ├─ [新增] 有 verifier 期望且账本健康？
       │    └─ 组装材料（回复全文 + 轨迹摘要 + rubric 清单）
       │    └─ utils/verifier.py::run_verifier
       │         create_session(directory=子会话工作区)   ← 主动取证能力来源
       │         send_prompt_async(agent='baize-verifier', model=批 model)
       │         轮询至回合结束（硬超时/停滞 → abort）
       │         解析 JSON 契约 → per-check verdicts
       └─ 合并 verdicts 进 gate results
            ├─ 全部通过 → passed → mark_done
            ├─ verifier 判 failed → gate failed → gate_retry（理由进修复提示）→ 落 failed
            └─ verifier 故障/缺名/非法 → inconclusive → fail-closed（failed）
```

## 5. 登记与 schema

- `action_checks` 新增 `check_type: 'verifier'`：
  - `name`（必填，≤100 字，沿用现有规则）；
  - `rubric`（必填，判定要点自然语言，≤2000 字；登记即冻结原文，核对时原文进 prompt）；
  - `tool`/`args_pattern`/`min_count` 对该类型无意义：**传入即 400**（校验器明确报错，不带病入库）；落库时 `min_count` 由登记规范化固定为 1（非用户输入）；
  - `scope`/`subagents`/`apply_to` 沿用现有语义（apply_to 定向到子任务；scope 对 verifier 不参与匹配，材料恒为子会话全树轨迹）。
- 落库：`action_expectations.check_type='verifier'`，`effect_spec = {"rubric": ...}`，`args_pattern=NULL`，`min_count=1`。
- `validate_checks` 扩展；`action-checks/extract` 预填器的 system prompt 升级为可建议 verifier 型期望（输出 `rubric` 要点），字段白名单加 `rubric`。

## 6. verifier agent：部署与形态

- 启动时随插件同模式部署：`ensure_verifier_agent(OPENCODE_GLOBAL_DIR)` 写 `<global>/agent/baize-verifier.md`，幂等（内容不变不重写；部署需 OC serve 重载——与插件同一运维口径，重启即生效）。
- frontmatter：
  - `mode: primary`（作为 verifier 会话的主 agent 运行；与批任务主 agent 无关，不参与 `_check_agent` 校验）；
  - 工具白名单只读：`read/grep/list` 开；`write/edit/bash/task/patch` 全关——能取证、不能改工作区、不能再委派。
- system prompt：判官角色；逐条核对材料中的 rubric（按名编号）；每条给 `verdict/reasons/evidence`；证据必须引用实际打开的文件与行或轨迹条目，禁止凭空断言；不修改任何文件；输出规定 JSON 后立即结束。
- 模型：agent 定义不写死，`send_prompt_async` 显式传**批任务配置的 model**（与子任务同款；未来可加批级/系统级覆盖，本期不做）。

## 7. 核对流程与调度

1. 触发点：`_run_one` 收尾段，`check_session_gate`（纯证据）之后、fail-closed/failed/passed 分支之前。
2. 前置条件：登记期望中存在 verifier 型、账本健康（`ledger_healthy`）、子会话有 `opencode_session_id` 与 `workspace_path`；任一不满足 → 对应期望按 inconclusive 处理（fail-closed）。
3. 材料组装（`utils/verifier.py`）：
   - 最终 assistant 回复全文（`ai_chat_messages` 最后一条 assistant，截断上限 8000 字）；
   - 轨迹摘要：`agent_tool_calls` 按时序（含子代理行），每条 `tool + args(截断 200 字) + state + occurred_at`，条数封顶 100；
   - rubric 清单：全部 verifier 期望按名编号；
   - 组装为单个 prompt（含 JSON 契约示例）。
4. `run_verifier`：`create_session(directory=子会话工作区)` → `send_prompt_async` → 轮询（2s 间隔）至回合结束 → 取最后 assistant 消息文本 → 解析 JSON。
5. **一次会话判全部 verifier 期望**：材料按名编号、输出按名拆分——每子会话恒定一次 verifier 调用。

## 8. 预算与防失控

- 轮询硬超时 `AI_BATCH_VERIFIER_TIMEOUT_SEC`（默认 180）；输出停滞 `AI_BATCH_VERIFIER_STALL_SEC`（默认 60）→ 调 OC abort 收场，按 verifier 故障处理。
- 单轮收尾：prompt 末尾强制「JSON 后立即结束」；agent 无 task/write 工具，无法发散、无法改状态、无法递归委派。
- verifier 会话**用完即弃**：不落 `ai_chat_messages`/`ai_chat_subtasks`/账本；OC 侧会话留存由 OpenCode 自身生命周期管理；平台只留 verdict 与证据引用（进 gate 结果与审计事件）。
- 并发形态：verifier 回合在子任务自己的收尾路径同步执行，不新占调度槽位，但延长该子任务收尾时长；N 个子任务同时收敛时最多 N 个 verifier 会话并发，与普通子任务回合同量级。

## 9. 结果契约、状态合并与 gate_retry 联动

- **输出契约**（agent prompt 强制）：`{"results":[{"name","verdict":"passed"|"failed","reasons":["…"],"evidence":"文件:行 / 轨迹摘要"}]}`。
- **解析规则**：按 `name` 对齐登记期望；缺名、`verdict` 非法枚举、JSON 不可解析 → 对应期望 inconclusive（整体 gate inconclusive）；多余条目忽略并日志留痕。
- **合并进 gate results**：verifier 条目 `{name, status, reasons, evidence, kind:'verifier', min_count:1}`；`gate_failure_message` 对 kind='verifier' 分支渲染：`name(判官未通过: reasons 拼接)`。
- **gate_retry 联动（最小改动）**：`_maybe_gate_retry` 的定向修复提示由 `gate_failure_message(gate)` 拼接，verifier reasons 渲染进去后「语义不达标 → 带理由续跑修复」闭环自动成立；预算与续跑机制（共用 `retry_count`）原样复用，天然有界。
- **故障政策（沿用，不发明新语义）**：
  - verifier 基础设施故障（超时/abort/调用异常/解析失败/缺名）→ gate inconclusive → **fail-closed**：子任务 failed，`gate_status='inconclusive'`，审计事件照记——与账本不健康同政策；
  - verifier 正常判定 failed → gate failed → gate_retry（预算内）或落 failed（`gate_status='failed'`，error_message 带判官理由）。
- 审计：`gate.evaluated` 事件 payload 增补 verifier 条目计数与故障标记（best-effort，沿用现有事件通道）。

## 10. extract / dry-run / UI

- **extract 预填器**：可建议 verifier 型期望；人工确认环节照旧（登记仍走人工确认）。
- **dry-run**：`gate/dry-run` 对 verifier 条目显式返回「需真实会话核对，无法预演」标注，不假装跑过。
- **UI**：创建/编辑对话框期望编辑区加类型选择 + rubric 多行输入（extract 预填进表单）；子会话门禁结果展示按 kind 渲染——verifier 条目显示 verdict + reasons（而非命中数）。管理员补挂（attach）走同一 `validate_checks`，自动兼容。

## 11. 测试策略

**路由级/单元（打桩 `opencode_client` 与轮询）**
- `validate_checks` verifier 分支 400 矩阵（缺 rubric / 超长 / 误传 args_pattern 等）；
- 材料组装（回复截断、轨迹封顶、rubric 编号）；
- `run_verifier` 正常 / 超时 / 停滞 abort / JSON 不可解析四路；
- verdict 按名对齐、缺名降级 inconclusive；
- `gate_failure_message` verifier 渲染；
- 集成：verifier 异常 → 子任务 failed（gate_status='inconclusive'）；verifier 判 failed + gate_retry 开 → 带理由续跑。

**e2e（真实链路，复用预置仓库基建）**
- 1 子任务 + verifier 期望（rubric 断言回复含指定标记）：达标 → completed（gate passed）；不达标 → failed 且 error_message 含判官理由；开 gate_retry 时断言定向修复续跑后通过。

## 12. 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `AI_BATCH_VERIFIER_TIMEOUT_SEC` | 180 | verifier 回合轮询硬超时 |
| `AI_BATCH_VERIFIER_STALL_SEC` | 60 | 无新输出停滞判负阈值 |

## 13. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 判官非确定（同一材料两次判定不同） | 温度低（provider 默认）+ JSON 契约 + 证据引用强制 + reasons 留痕可审计；确定性要求高的核对仍可用既有三类原语 |
| verifier 拉长收尾（每子会话 +10~60s） | 硬超时 + 停滞 abort；并发量与普通回合同量级；超时属故障走 fail-closed，不静默 |
| verifier 幻觉证据 | 只读工具集 + evidence 必须引用实际文件/行 + reasons 逐条可人工复核 |
| 判官与修复死循环 | gate_retry 与 auto-retry 共用 `retry_count` 预算，天然有界 |
| 判官材料泄敏（回复/轨迹含敏感内容进 OC 会话） | verifier 会话与子任务同工作区、同部署边界；v1 不跨工作区传递材料 |
