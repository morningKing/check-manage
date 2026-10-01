# SkillOpt 任务拟合设计——执行轨迹 vs 定义步骤的拟合度（图标呈现）

- 日期：2026-10-01
- 状态：已评审（brainstorming 产出，待实现计划）
- 相关：`server/utils/skillopt.py`、`server/utils/execution_audit.py`、`src/views/admin/AiSkillOpt.vue`

## 1. 背景与问题

现有 SkillOpt 只回答「技能被调用了没有、几次」（`ai_skill_invocations` 聚合 +
confirmed/inferred 证据分级），没有**任务维度**的视图，也不回答「执行轨迹是否
符合 skill/agent 定义中声明的步骤流程」。

本设计新增**任务拟合**能力：以任务会话（attempt）为维度，把每个任务的实际执行
轨迹与当前 skill/agent 定义中声明的步骤对照，得出步骤级拟合度并以图标呈现在
管理页。

## 2. 决策记录

- **步骤来源**：frontmatter 声明式步骤（人工可维护、判定确定性高）；
- **AI 步骤生成器纳入 v1**：小模型从定义正文生成步骤建议，人工确认后回写
  frontmatter（内容与结构契约互补）；
- **拟合判定**：规则对照（贪心顺序匹配），确定性、零 LLM 成本；
- **呈现位置**：管理页 SkillOpt「任务拟合」视图（调试/治理信息不进 AI 会话页）；
- **计算数据流**：attempt 收敛时同步计算 + 落库（定义按 content_hash 冻结历史）；
  否决查询时现算（慢、定义改版追溯失真）与异步 worker（毫秒级计算不需要）。

## 3. 数据模型

### 3.1 定义侧：frontmatter `fit` 块（SKILL.md / agent md 可选字段）

```yaml
---
description: 数据拉取技能
fit:
  steps:
    - id: clone
      name: 克隆目标仓库
      expect:
        - tool: bash
          args_pattern: 'git clone'   # 入参正则（PG ~ 口径，re.search 语义）
    - id: inspect
      name: 检查结构
      expect:
        - tool: glob
    - id: report
      name: 写报告
      expect:
        - tool: write
          args_pattern: 'outputs?/'
---
```

- 无 `fit` 块的定义不参与拟合；
- `expect` 缺省的步骤记 skipped（不计入 score 分母）；
- `args_pattern` 登记校验沿用动作门禁的 PG 正则口径。

### 3.2 结果表 `ai_skill_fit_results`（新表，DDL 进 db_schema/）

```sql
CREATE TABLE IF NOT EXISTS ai_skill_fit_results (
  id            VARCHAR(100) PRIMARY KEY,
  attempt_id    VARCHAR(100) NOT NULL
                REFERENCES ai_execution_attempts(id) ON DELETE CASCADE,
  session_id    VARCHAR(100) NOT NULL,
  def_kind      VARCHAR(30) NOT NULL,       -- skill | agent
  def_name      VARCHAR(300) NOT NULL,
  def_hash      VARCHAR(64),                -- 定义版本指纹（改版不追溯历史）
  steps_total   INTEGER NOT NULL DEFAULT 0,
  steps_hit     INTEGER NOT NULL DEFAULT 0,
  score         INTEGER NOT NULL DEFAULT 0, -- 0-100
  status        VARCHAR(20) NOT NULL DEFAULT 'unknown',
                -- fit | partial | diverged | no_trace | parse_error
  per_step      JSONB NOT NULL DEFAULT '[]',
                -- [{id,name,status:'hit'|'miss'|'skipped',
                --   evidence:[{tool,args,occurredAt}]}]
  computed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ai_skill_fit_attempt_idx
  ON ai_skill_fit_results(attempt_id);
CREATE INDEX IF NOT EXISTS ai_skill_fit_def_idx
  ON ai_skill_fit_results(def_name, computed_at DESC);
```

### 3.3 解析缓存

frontmatter 解析结果按 `content_hash` 进进程内 LRU 缓存（`_fit_parse_cache`）：
定义文件被清理后仍可按 hash 命中；yaml 解析失败 → 该定义 `parse_error`（结果行
status=parse_error、日志留痕）。

## 4. 拟合引擎（`utils/skill_fit.py`，纯函数 + 薄 I/O）

`compute_attempt_fit(attempt_id) -> list[dict]`：

1. **定义发现**：读 `ai_execution_manifests` 中该 attempt 的 kind∈(skill, agent)
   行 → 按 path 读定义文件（缺失则按 hash 查解析缓存）→ 解析 frontmatter
   `fit.steps`（无 fit 块的定义跳过）。
2. **轨迹序列**：该 attempt 时窗（attempt.started_at ~ finished_at）内的工具调用
   时序 `[(tool, args_text, occurred_at)]`——来源 `agent_tool_calls`
   （root_session_id = attempt.session_id）；skill 加载事件来自
   `ai_execution_events(type='skill.invoke')`，作为轨迹首元素参与匹配
   （expect 可声明 `tool: skill`）。
3. **贪心顺序匹配**：指针扫描轨迹，按步骤序逐个寻找首个满足
   `tool 相等 AND (无 args_pattern OR args_text ~ pattern)` 的调用 → 命中
   （hit，记录证据调用）并推进指针；轨迹耗尽仍未命中 → miss。
   同一步骤声明多条 expect 时为 **OR 语义**（任一条命中即该步 hit，
   证据取首个命中的调用）。
4. **评分**：score = round(steps_hit / (steps_hit + steps_miss) × 100)
   （分母含 miss，不含 skipped）；status：全 hit → `fit`；部分 → `partial`；
   全 miss 且轨迹非空 → `diverged`；轨迹为空 → `no_trace`。
5. **落库**：upsert 结果行（attempt_id + def_name 冲突时覆盖）。

幂等：attempt 收敛钩子与手动重算可重复执行，结果以最后一次为准。

## 5. AI 步骤生成器（v1 含）

- **生成**：`POST /ai/skillopt/definitions/steps/generate`
  body `{kind, name, path}`——服务端读定义文件全文，按 `fit.steps` JSON Schema
  约束调用小模型（AI 设置通道，同 action_check_extractor 模式：系统提示约束
  只输出 JSON、temperature 低、失败 502），返回步骤建议（**不落库**，仅返回
  给前端预览）。生成结果按 content_hash 进进程内缓存。
- **预览试算**：前端拿到建议后可在保存前选择一个历史 attempt 做
  「若应用这套步骤」的试算（复用 compute 的匹配纯函数，不落库）。
- **回写**：`POST /ai/skillopt/definitions/steps/apply`
  body `{kind, path, steps}`——解析原 frontmatter → 合并 `fit.steps`（保留其余
  frontmatter 字段与正文）→ 写回文件（幂等；写失败 500）。回写后该定义
  content_hash 变化，历史拟合结果不追溯。
- **失效**：定义 hash 变化后旧生成结果视为过期，UI 提示重新生成。

## 6. API（admin 权限，挂现有 skillopt 路由组）

| 端点 | 说明 |
|---|---|
| `GET /ai/skillopt/fits?limit&sessionId&defName` | 拟合结果列表（时间倒序；含 per_step 摘要） |
| `GET /ai/skillopt/fits/<attempt_id>` | 步骤级明细（该 attempt 全部定义的拟合结果） |
| `POST /ai/skillopt/fits/<attempt_id>/recompute` | 手动重算（幂等） |
| `POST /ai/skillopt/definitions/steps/generate` | AI 生成步骤建议（见 §5） |
| `POST /ai/skillopt/definitions/steps/apply` | 确认回写 frontmatter（见 §5） |

计算入口：attempt 收敛钩子（`collect_skill_invocations` 调用点旁追加
`skill_fit.compute_attempt_fit(attempt_id)`，best-effort 异常吞掉）。

## 7. UI（`AiSkillOpt.vue` 新增「任务拟合」主 tab）

- **任务拟合列表**：每行 = 一次 attempt（时间 / agent / 来源类型 /
  定义名 ×N）+ **拟合图标**：分段点阵（每步一个圆点：绿=hit、红=miss、
  灰=skipped）+ score 数字 + 状态徽标（拟合/部分拟合/偏离/无轨迹/解析失败）。
- **行展开**：步骤明细表（步骤名 / 状态图标 / 证据工具调用摘要 tool+args 截断
  + 时间）；`recompute` 按钮；跳转该会话的 Attempt 链入口。
- **定义步骤管理**：定义清单（从 manifests 聚合）每行「生成步骤」按钮 →
  建议编辑对话框（可增删改步骤与 expect）→ 试算预览 → 保存回写。
- 空态：无 fit 声明的定义与无轨迹的任务均有明确文案，不假装能拟合。

## 8. 错误处理

| 场景 | 行为 |
|---|---|
| frontmatter yaml 解析失败 | 结果行 status=parse_error，日志留痕，跳过该定义 |
| 定义文件不存在 | 按 hash 查解析缓存；缓存也无 → 不出结果行（列表不显示） |
| attempt 无任何轨迹 | status=no_trace（灰图标，列表可见） |
| 判定期 expect 正则非法 | 登记校验（apply 时 validate_pg_regex 同口径）挡在写入前 |
| 收敛钩子计算异常 | best-effort 吞掉（与 collect_skill_invocations 同策略），手动重算兜底 |

## 9. 测试策略

- **单测 `tests/test_skill_fit.py`**：frontmatter 解析（合法/缺 fit/yaml 坏）；
  贪心匹配（全中/部分/乱序→miss/args_pattern 过滤/skipped 不计分）；score 与
  status 边界；缓存命中（文件删除后按 hash 命中）。
- **路由测试**：fits 列表/明细的 admin 权限与形状；recompute 幂等。
- **生成器测试**：打桩 AI 通道（schema 约束与失败 502 路径）；apply 的
  frontmatter 合并（保留原字段与正文）与正则校验。
- **真实链路手工验证**：跑一个带 fit 声明的技能的批任务 → 收敛后管理页看
  图标与步骤明细。

## 10. 非目标（YAGNI）

- 不做跨 attempt 的定义质量趋势分析（二期）；
- 不做执行轨迹的自动重排对齐（edit distance）——v1 贪心顺序匹配足够；
- 不把拟合图标放回 AI 会话页（调试/治理信息留在管理页）；
- 不支持 agent 委派子代理的跨层轨迹合并（子代理有自己的 attempt 与清单，
  天然按层呈现）。
