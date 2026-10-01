# SkillOpt 任务拟合设计——执行轨迹 vs 定义步骤的拟合度（图标呈现）

- 日期：2026-10-01
- 状态：已实现（2026-10-01）
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
  diagnosis     JSONB,                      -- 偏差诊断（手动触发，见 §5b）
                -- {cause, suggestions[], revised_steps, analyzed_at,
                --  trace_sig}
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

### 3.4 版本实体 `ai_skill_def_versions`（新表）

content_hash 是版本指纹且已贯穿全部结果（manifests/invocations/fit_results），
但它是匿名指纹——版本需要人可读的实体：

```sql
CREATE TABLE IF NOT EXISTS ai_skill_def_versions (
  id             VARCHAR(100) PRIMARY KEY,
  def_kind       VARCHAR(30) NOT NULL,      -- skill | agent
  def_name       VARCHAR(300) NOT NULL,
  content_hash   VARCHAR(64) NOT NULL,
  version_label  VARCHAR(200),              -- 人工命名，如「v3 增加校验步骤」
  note           TEXT,
  first_seen_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (def_kind, def_name, content_hash)
);
```

- 版本注册：拟合计算/清单扫描遇到新 hash 时自动 upsert（first_seen_at 首次
  观测时间）；`version_label`/`note` 由人工在 UI 设置；
- 版本时间线：按 first_seen_at 排序即该定义的演进序列；
- 生成器/手动回写 frontmatter 后的新 hash 自动注册为新版本（AI 优化的
  落地动作天然产生版本切换点）。

## 4. 拟合引擎（`utils/skill_fit.py`，纯函数 + 薄 I/O）

`compute_attempt_fit(attempt_id) -> list[dict]`：

1. **定义发现**：读 `ai_execution_manifests` 中该 attempt 的 kind∈(skill, agent)
   行 → 按 path 读定义文件（缺失则按 hash 查解析缓存）→ 解析 frontmatter
   `fit.steps`（无 fit 块的定义跳过）。
2. **轨迹序列**：该 attempt 时窗（attempt.started_at ~ finished_at）内的工具调用
   时序 `[(tool, args_text, occurred_at)]`——来源 `agent_tool_calls`
   （root_session_id = attempt.session_id，**含全部子代理的调用**，账本落账
   时已带 root+subtask 双标识）。skill 加载即 `tool='skill'` 的账本调用
   （统一事实源；`ai_execution_events` 的 skill.invoke 事件仅作参考）。
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

## 5b. 偏差诊断（手动触发，2026-10-01 增补）

对有偏差的结果行（status=partial/diverged），管理页提供「分析偏差」按钮：

- **输入**：定义全文（fit.steps + 正文）、拟合 per_step 证据、miss 步骤
  前后的实际工具调用上下文、任务 prompt；
- **输出（结构化 JSON，存结果行 `diagnosis` 列）**：
  - `cause`：偏差原因分类——`definition_stale`（流程已变，步骤该更新）/
    `step_redundant`（定义多余）/`order_deviation`（做了但次序不对）/
    `model_noncompliance`（模型未遵循，可能 prompt 不足）/`environment`
    （工具报错导致）；
  - `suggestions[]`：每类原因的具体修改建议（改定义/改 prompt/改环境）；
  - `revised_steps`：修订后的 steps 草案——可直接送入 §5 生成器的
    编辑-回写流程落地；
- **触发**：仅手动（结果行按钮）。按 `(attempt_id, def_hash, 轨迹签名)`
  缓存——同签名重复点击不重复调用 LLM；重新拟合（轨迹变化）后缓存失效；
- **通道与失败语义**：AI 设置通道（同 §5 生成器），失败 502、不落库。
- 二期方向：按 def_name 聚合多次偏离——「该技能 80% 的任务在同一步骤
  偏离」→ 定义改进优先级排序。

### 5c. 子代理 skill 调用的采集修复（2026-10-01 增补）

**现状缺口（代码级核实）**：OC 事件里 subagent 内 part 的 sessionID 是子代理
自己的会话 id（非父会话）；`skillopt._platform_session_id` 只查
`ai_chat_sessions` → 子代理 id 无行 → `record_runtime_skill_event` 静默丢弃
——**`ai_skill_invocations` 的 confirmed 采集收不到 subagent 的 skill 调用**
（`ai_execution_events` 的 skill.invoke 同缺）。

修复（skillopt 采集层）：
- `_platform_session_id` 落空时回退查 `ai_chat_subtasks`
  （id = OC 会话 id → root_session_id），命中则归属根会话，并在
  `ai_skill_invocations` 新增可空列 `subtask_id` 标注子代理归属（含索引）；
- `record_runtime_skill_event` 尝试取该子代理自己的 attempt
  （execution_audit，source_type='subagent'），取不到再退根 attempt；
- 拟合引擎不受此缺口影响（轨迹走账本，本就含子代理）——修复的是 confirmed
  证据的完整性，使「该子代理确实按定义加载了技能」可在子代理粒度确证。

## 6. API（admin 权限，挂现有 skillopt 路由组）

| 端点 | 说明 |
|---|---|
| `GET /ai/skillopt/fits?limit&sessionId&defName` | 拟合结果列表（时间倒序；含 per_step 摘要） |
| `GET /ai/skillopt/fits/<attempt_id>` | 步骤级明细（该 attempt 全部定义的拟合结果） |
| `POST /ai/skillopt/fits/<attempt_id>/recompute` | 手动重算（幂等） |
| `POST /ai/skillopt/definitions/steps/generate` | AI 生成步骤建议（见 §5） |
| `POST /ai/skillopt/definitions/steps/apply` | 确认回写 frontmatter（见 §5） |
| `POST /ai/skillopt/fits/<result_id>/diagnose` | 偏差诊断（手动触发，见 §5b） |
| `GET /ai/skillopt/versions?defKind&defName` | 版本时间线 + 每版本效果聚合 |
| `PATCH /ai/skillopt/versions/<id>` | 设置 version_label / note |

计算入口：attempt 收敛钩子（`collect_skill_invocations` 调用点旁追加
`skill_fit.compute_attempt_fit(attempt_id)`，best-effort 异常吞掉）。

## 7. UI（`AiSkillOpt.vue` 新增「任务拟合」主 tab）

- **任务拟合列表**：每行 = 一次 attempt（时间 / agent / 来源类型 /
  定义名 ×N）+ **拟合图标**：分段点阵（每步一个圆点：绿=hit、红=miss、
  灰=skipped）+ score 数字 + 状态徽标（拟合/部分拟合/偏离/无轨迹/解析失败）。
- **行展开**：步骤明细表（步骤名 / 状态图标 / 证据工具调用摘要 tool+args 截断
  + 时间）；`recompute` 按钮；跳转该会话的 Attempt 链入口。
- **偏差诊断**：partial/diverged 行显示「分析偏差」按钮 → 诊断面板
  （原因分类标签 + 建议 + 修订步骤 diff 预览）→「采纳修订步骤」把
  revised_steps 送入定义步骤编辑-回写流程（§5）。
- **定义步骤管理**：定义清单（从 manifests 聚合）每行「生成步骤」按钮 →
  建议编辑对话框（可增删改步骤与 expect）→ 试算预览 → 保存回写。
- **版本效果对比**（每个定义一个视图）：
  - 版本时间线：横轴时间，版本切换点标注（hash 短 id + 人工 label），
    一眼看到「定义在什么时候改过几次」；
  - 每版本效果卡：该版本期间的任务数、平均拟合 score、拟合率（fit 占比）、
    偏离原因分布（diagnosis.cause 聚合）；
  - **相邻版本对比卡**（v(n) → v(n+1)）：平均分变化 Δ、拟合率变化、
    偏离原因分布迁移——「这次优化到底有没有效果」的直接答案；
    附典型任务示例（改进最大/退步最大的 attempt 链接）。
  - 数据口径：fit_results 按 def_hash 分组聚合（版本冻结保证历史归因
    正确），无拟合结果的任务不计入。
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
- **偏差诊断测试**：打桩 AI 通道（结构化输出解析/失败 502）；诊断缓存
  （同轨迹签名不重复调用；重拟合后失效）。
- **版本测试**：新 hash 自动注册（幂等）；label 设置；版本聚合口径
  （按 def_hash 分组、无拟合结果任务不计入）。
- **真实链路手工验证**：跑一个带 fit 声明的技能的批任务 → 收敛后管理页看
  图标与步骤明细。

## 10. 原非目标（2026-10-01 全部转正，均已实现）

- ~~跨版本偏离原因的深度聚合分析为二期~~ → **已实现**：`GET /ai/chat/admin/skill-def-patterns`（步骤级 miss/错序按 定义×步骤 聚合，输出偏离任务数/涉及版本数/版本 hash/均分，管理页「偏离模式」表呈现）；
- ~~执行轨迹的自动重排对齐~~ → **已实现**：`match_steps` 两段式——贪心顺序 miss 后第二段做重排对齐，expect 存在于轨迹任意未消耗位置判 `out_of_order`（全部错序 → status `reordered`）；同一条调用不可作两步证据（consumed 集）；
- ~~拟合图标不放回 AI 会话页~~ → **已实现（轻量形态）**：owner 端点 `GET /ai/chat/sessions/<sid>/skill-fit`（只回摘要）+ 批状态条轻量徽标（score + 最差状态，只读，点击跳管理页看明细——治理主体仍在管理页）；
- ~~不支持子代理的跨层呈现~~ → **已实现**：拟合明细端点附 `subagentFits`（该会话 subagent attempt 的拟合结果按层附加，父层数据不合并，各层独立判定）。
