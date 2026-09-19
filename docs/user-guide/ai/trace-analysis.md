# 轨迹分析（管理员）

> 面向：`admin.ai_chat_admin` 权限 · 关联：[执行审计](./execution-audit.md) · [AI 会话](./assistant.md)

轨迹分析对一次 AI 执行做**全生命周期回放与根因诊断**：Agent/模型/技能配置、
工具调用序列与失败原因、子代理委托链、契约步骤完成度与优化建议。

## 1. 发起分析

1. **设置中心 → AI 会话管理**，找到目标会话；
2. 行「操作」下拉 → **轨迹分析**（或详情抽屉内按钮）；
3. 系统创建分析会话（`kind=trace_analysis`，默认从普通列表隐藏），
   并返回 `analysisId`；分析由 `trace-analyzer` 技能异步执行。

## 2. 与原会话的关联

- 分析会话顶部横幅显示**原会话 ID**（可复制/跳转会话管理）；
- 原会话的**执行审计**抽屉包含「轨迹分析历史」：每次分析的状态、时间与
  「打开会话」入口；
- 管理列表默认隐藏分析会话，勾选「显示轨迹分析会话」可查看（带来源标签）。

## 3. 执行审计（确定性事实）

审计抽屉（目标会话行 → 执行审计）呈现：

| 区块 | 内容 |
|---|---|
| 执行尝试 | 每次 Attempt 的 requested/effective Agent+Model 与解析口径、状态、Prompt hash |
| 定义清单 | 本次可见 Skill/Agent/AGENTS.md 及 SHA256、注入状态 |
| 契约审计 | 契约步骤七态：已完成（有证据）/声称完成（无证据）/遗漏/顺序错误/失败/数据不足 |
| 工具失败 | 失败类型（十类）+ 恢复分析（同参重试/换策略/是否恢复）+ 证据 |
| 声明计划 | Agent Todo 自述步骤（不作为执行事实） |
| 数据完整性 | score + limitations——数据不足时结论自动降级为 unknown |

存在运行中尝试时抽屉每 5 秒自动刷新。

## 4. 证据分级

- **confirmed**：事件/数据库直接证明；
- **inferred**：推断（如 Todo 声称完成但无工具证据 → completed_claimed）；
- **unknown_due_to_missing_data**：无数据不猜测。

## 5. Prompt 与安全

- Prompt 默认只记录 **hash + 增强项**（记忆/附件/@提及）；
- 明文查看需 `admin.ai_execution_prompt_read` 权限并记操作审计；
- 事件明文 payload 保留 30 天、摘要行保留 180 天（自动分层清理）。

## 6. SkillOpt（技能优化）

设置中心 → **SkillOpt 技能优化**：

- **调用聚合**：按技能+版本 hash 统计调用/完成率/失败/runtime 确认占比
  （OpenCode 插件上报为 confirmed，启发式为 inferred）；
- **版本对比**：相邻版本完成率 Δ；
- **建议效果**：应用建议前后 7 天窗口成功率对比。

技能调用的精确证据来自随系统自动安装的 OpenCode 运行时插件
（`plugin/baize-trace.js`，上报 skill load/invoke 到平台内网接口）。
