# AI 联动功能正确性测试套件——判别力登记表 + 全量回归证据

- **套件名**：AI 联动功能正确性测试套件（会话管理 v2 / SkillOpt 拟合 / 轨迹分析，16 例 e2e）
- **分支**：`feat/ai-linked-correctness-tests`（head `8e3d38f`，16 例全部落地）
- **基线**：`main@92ff541`（分支自该提交创建；基线上 main 自有套件全绿，但三个新功能域零 e2e 覆盖——下表所有判别力点在基线上均处于「缺陷潜伏、无测试可抓」状态）
- **日期**：2026-10-06
- **Spec**：`docs/superpowers/specs/2026-10-06-ai-linked-features-correctness-testing-design.md`（§2–§4 各表加粗项即本表判别力点来源）
- **Plan**：`docs/superpowers/plans/2026-10-06-ai-linked-correctness-tests.md`
- **测试落位**：`e2e/ai-full/ai-session-admin-v2.spec.ts`（5 例）、`e2e/ai-full/ai-skillopt-fit.spec.ts`（6 例）、`e2e/ai-full/ai-trace-analysis.spec.ts`（5 例）

---

## 1. 判别力登记表

「基线结果」= 该判别力点在基线 `main@92ff541` 上的状态；「验证方式」= 用例抓取该判别力点的具体手段（三通道：Playwright UI 真实链路 / API 契约 / `db_exec.py` DB 断言）。

### 1.1 会话管理 v2（`ai-session-admin-v2.spec.ts`，spec §2）

| 用例号 | 判别力点（预期抓取的缺陷） | 基线结果 | 验证方式 |
|---|---|---|---|
| TC-SESS-01 | **source_type SQL 计算列优先级**（`kefu` > `scan` > `api_batch` > `batch` > `regular`）：批行带 `api_key_id` 时必须归 `api_batch` 而不得被 `sourceType=batch` 命中——若 CASE 分支顺序错，api_batch 行会跨类泄漏；**kind 过滤 SQL**（默认隐藏 `trace_analysis`，`kind=all`/`kind=trace_analysis` 三态）；status 过滤精确性（closed 种子被 completed 排除、closed 单命中，防过滤 no-op 空洞通过）与分页 total 一致性；非法参数 400 中文契约（「无效状态」「无效来源类型」） | main 全绿但零覆盖：v2 列表端点无任何筛选正确性测试，计算列优先级错误将无测试可抓 | DB 直插 7 条自造会话（5 类 source 各 1 + trace_analysis 1 + regular closed 1，同随机 keyword 收敛种子集），逐类 `sourceType=<t>&status=completed` 断言返回 id 数组 `toEqual([目标id])` 精确单命中；`kind` 三态行数/包含性断言；status=completed&kind=all 断言精确排除 closed 种子、status=closed&kind=all 单命中；非法 status/sourceType 断言 400 + error 子串 |
| TC-SESS-02 | **关键词全消息搜索的 EXISTS jsonb 路径**：keyword 必须命中消息正文（jsonb 数组内层 text），而非仅标题——若 SQL 只 LIKE 标题或 jsonb 路径写错，正文命中为空 | main 全绿但零覆盖：v2 关键词搜索无正文级断言 | 种子两条同前缀会话，仅一条的 `messages` 正文含 needle（标题不含）；keyword=needle 断言精确单命中 + total=1；keyword=无匹配串断言 `items` 空数组 |
| TC-SESS-03 | **详情抽屉数据装配**（基本信息 sid/标题、对话历史消息渲染、文件按 outputs 分组）与**下载端点路径守卫**（`commonpath().startswith(ws)` 契约，Windows 反斜杠/正斜杠分隔符一致性）——装配漏字段或守卫误判 400 均被抓 | main 全绿但零覆盖：v2 详情抽屉 UI 与 files/download 无端到端断言 | 种子会话 + 临时工作区写入 `outputs/report.md`（内容指纹串）；UI 走真实链路打开抽屉断言三 tab 渲染（标题全等、消息文本可见、文件名 exact 可见）；API 通道 fetch download 断言 200 且响应体与种子内容全等 |
| TC-SESS-04 | **v2 归档入口 → archive 联动**（UI 归档后列表徽标「已归档」）、**oplog 留痕**（operation_logs 精确到 action=`update`、description=`归档会话（admin）`）、**批控会话 BATCH_SESSION_CONTROLLED 409**（running 批子会话拒绝归档）——漏留痕或批控放行均被抓 | main 全绿但零覆盖：v2 入口归档与批控 409 无测试 | UI 真实链路：行下拉「归档」→ 确认框 → `el-message 已归档` → 查询后徽标断言；DB 断言 oplog 行 `[["update","归档会话（admin）"]]` 全等；API 通道对 `seedRunningChild` 造的批控 running 子会话 POST archive 断言 409 + `error.code==='BATCH_SESSION_CONTROLLED'` |
| TC-SESS-05 | **v2 系列权限装饰器先于存在性检查**：guest 对 v2 列表/详情/消息/文件/归档全 403，且对**不存在的会话 id 也 403 而非 404**——若存在性检查先于 `require_permission`，会向未授权者泄露资源存在性（404 信息泄露） | main 全绿但零覆盖：v2 系列权限矩阵无测试 | `secondUser` 造 guest token，逐一打 5 个端点断言 403；再用自造不存在 id 打详情断言仍 403（精确区分 403/404，杜绝多状态接受） |

### 1.2 SkillOpt 拟合（`ai-skillopt-fit.spec.ts`，spec §3）

| 用例号 | 判别力点（预期抓取的缺陷） | 基线结果 | 验证方式 |
|---|---|---|---|
| TC-FIT-01 | **拟合三态判定规则**（`hits==total → fit`、`hits==0 → diverged`、中间 → partial）与分数（100/50/0）；**`uq_skill_fit_attempt_def` 幂等 upsert**（重复 recompute 不产生重复行）；**def 版本自动登记**（recompute 时 manifest 定义入版本时间线）；perStep 明细序；definition-summary 聚合；404 | main 全绿但零覆盖：拟合引擎端到端无测试 | `seedFitAttempt` 造三种命中组合（2/2、1/2、0/2）→ recompute 逐个断言三态 + score 精确值；重复 recompute 后列表仍 3 行（幂等）；详情 perStep 状态数组 `toEqual(['hit','miss'])`；summary 聚合 tasks=3；不存在 attempt 404；UI 主从布局 + 「重新计算」toast |
| TC-FIT-02 | **preview 贪心匹配契约**（steps_total/steps_hit/score/status 结构化返回）与 **400 形状族**（缺 id / expect 非数组 / 非法正则 `(unclosed` / 缺 attemptId）+ 404；**不落库**（预览不得产生拟合行）——预览误写库或校验漏形状均被抓 | main 全绿但零覆盖：preview 端点无契约测试 | API 通道：合法 steps 断言 200 + `toMatchObject({steps_total:2, steps_hit:2, score:100, status:'fit'})`；随断言 `skill-fit?defName=` 列表 0 行（不落库）；四种坏形状逐一断言 400；不存在 attempt 404 |
| TC-FIT-03 | **apply 写盘 → sha256 归档正文自洽**（新版本 contentHash == 写盘 bytes 的 sha256、archived=true 回读归档正文、label=`AI步骤优化 <当日>`）；**非法正则 400 且不写盘**（先校验后写盘次序）；**路径越界 400**（`_path_in_allowed_roots` 守卫）；**回滚写目标版本归档内容且零虚假版本行**（文件 bytes 恰为 v2 归档 bytes + 时间线仍 3 行）——写盘与归档脱节、回滚造伪版本均被抓 | main 全绿但零覆盖：回写/归档/回滚链路无测试 | API + fs + sha256 三方互证：apply 前后读盘 bytes 断言变化且含 marker；版本时间线 2 行且按新 sha256 命中 archived 版本；非法正则 apply 后盘面 bytes 逐字节不变；`C:/Windows/system32/evil.md` 400；第二次 apply 造 v3 后回滚 v2：盘面 bytes 恰等于 v2 归档 bytes（sha256 相等）且时间线仍 3 行（零虚假回滚版本）；未归档版本（content=NULL）回滚 400 |
| TC-FIT-04 | **runtime 事件幂等 upsert 不被降级**（重复上报仍 1 行且 `source='runtime'` 不回落）、**idle 收敛不改写既有 outcome**（session.idle 不得把 completed 行改坏）、**internal token 鉴权**（错 token 403）——幂等键错或降级逻辑倒挂均被抓 | main 全绿但零覆盖：采集链路无幂等断言 | `X-Internal-Token`（读自 server/.env MCP_INTERNAL_TOKEN）POST runtime-events 同一事件两次 → DB 断言仍 1 行且 source 列 `toBe('runtime')`；POST session.idle 后 outcome 仍 `toBe('completed')`；skill-analytics 断言 `runtime_confirmed===1`；wrong-token 403 |
| TC-FIT-05 | **反馈端点存活与 effect 追踪闭环**：POST feedback applied 落库（feedbacks 列表行 action=`applied`）→ GET effect 返回 `tracked` 且 before 7 天窗口包含种子调用——**已抓到 BUG-1 + GAP-2（红=证据，见 §2）**：当前 POST feedback 100% TypeError→500（BUG-1）；即使修复，INSERT 永不写 applied_at/before_metrics 而 effect 端点以其为门闩（GAP-2） | main 全绿但零覆盖：P2 效果追踪半接线状态无任何测试探测；本分支 `8e3d38f` 上红即证据 | 种子 attempt/invocation（90 分钟前 completed）/diagnosis → POST feedback（action=applied，appliedValue 带 skillName）断言 200 → feedbacks 列表行断言 → effect 断言 `status==='tracked'` + `before.invocations>=1`；非法 action 400、诊断不存在 404。**红=产品真实缺口，不做断言弱化** |
| TC-FIT-06 | **generate/diagnose 真链路**（LLM 可用后跑）：steps 数组结构、diagnosis.cause 五值枚举、oplog 双留痕（generate 按定义路径 / diagnose 按结果行 id）；LLM 不可用时 502 预检 skip 留证 | main 全绿但零覆盖 | @llm 标注 + 502 预检：generate 502 即 `test.skip`；链路 generate→recompute→diagnose 断言结构 + oplog 条数（详见 §3） |

### 1.3 轨迹分析（`ai-trace-analysis.spec.ts`，spec §4）

| 用例号 | 判别力点（预期抓取的缺陷） | 基线结果 | 验证方式 |
|---|---|---|---|
| TC-TRACE-01 | **MCP 禁用 fail-closed 零残留**（会话/诊断双零）：内置 MCP 被禁用时 analyze 必须 409 拒绝且**不留下任何分析会话行/诊断行**——若先创建后校验，禁用态会产生孤儿数据 | main 全绿但零覆盖：fail-closed 分支无零残留断言 | API 断言 `PUT /ai/mcp-servers/internal {enabled:false}` → analyze 409 + error 含「内置 MCP 已被禁用」；DB 断言 `轨迹分析: <sid>` 会话行 0 + diagnoses 行 0（双零）；finally 恢复开关 enabled=true |
| TC-TRACE-02 | **MCP 不可达 502 先于任何落库**：连接拒绝必须以 502 显式失败且不落半成品——吞错落 pending 行即被抓 | main 全绿但零覆盖 | 进程级确定性触发：`restartBackend({MCP_SERVER_URL:'http://127.0.0.1:1'})`（死端口）→ analyze 502 + error 含「MCP 服务不可用」→ DB 断言分析会话行 0；finally 无参 `restartBackend()` 恢复 env |
| TC-TRACE-03 | **派发失败三处残留**：OpenCode 会话创建失败时——① diagnosis 行必须落 `failed` 且 error_message 含同串；② 无孤儿分析会话行；③ 工作区快照差集空（ai-workspaces 树内 `sess_<12hex>` 形态目录零新增）——三处任一漏清理均被抓 | main 全绿但零覆盖 | `restartBackend({OPENCODE_BASE_URL:'http://127.0.0.1:1'})` → analyze 502 + error 含「OpenCode 会话创建失败」；DB 断言 diagnosis status=`failed` + error_message 子串；分析会话行 0；`listSessWorkspacePaths` 前后快照 `Set.difference` 断言空集；finally 恢复 |
| TC-TRACE-04 | **分析历史 snake_case / 轮询 camelCase / report 契约形状**：会话维度历史 `analysis_session_id`（snake_case、created_at DESC）；状态轮询 `analysisId/targetSessionId/analysisSessionId`（camelCase）；报告已完成行回 JSONB summary、pending 行无 summary；v2 kind 三态对分析会话同样生效；404——两套大小写契约错位即被抓 | main 全绿但零覆盖 | 种子 completed + pending 两诊断（自造分析会话行）；v2 列表 kind 三态断言；`/analyses` 断言 id 序 + snake_case 字段 `toMatchObject`；`/analyses/<id>` 断言 camelCase `toMatchObject` + 不存在 id 404；`/report` completed 断言 `report.summary==='e2e-seed'`、pending 断言 `summary===undefined` |
| TC-TRACE-05 | **分析闭环 completed + 报告结构**（LLM 可用后跑）：真实会话 UI 发消息 → analyze → 轮询至 `completed` → 报告 keys>0 → 分析会话行 kind=`trace_analysis` → 审计抽屉「轨迹分析历史」出现该行；LLM 不可用时 502 预检 skip 留证 | main 全绿但零覆盖 | @llm 标注 + generate 探活预检（502 即 skip）；全链路 UI + API + DB 三通道（详见 §3） |

---

## 2. 产品发现登记（修复前 commit `8e3d38f` 上红 = 判别力证据）

### BUG-1：反馈端点 `log_operation` 缺第 5 个位置参数 → POST feedback 100% TypeError→500

- **位置**：`server/routes/ai_session_admin.py:712-713`
- **症状**：`suggestion_feedback` 调用 `log_operation('update', 'ai_suggestion_feedback', suggestion_id, f'SkillOpt 建议反馈 {action}（诊断 {diagnosis_id}）')` 只传 4 个位置参数；而签名（`server/utils/operation_log.py:83`）为 `log_operation(action, target_type, target_id, target_name, description, field_changes=None, branch_id=None)`——第 5 个位置参数 `description` 缺失，调用即抛 `TypeError: log_operation() missing 1 required positional argument: 'description'` → 请求 500。语义上第 4 参位置被描述文本占据，`target_name`/`description` 两参错位。
- **影响**：POST `/ai/chat/admin/analyses/<id>/suggestions/<id>/feedback` 在任何合法请求体下 100% 失败，SkillOpt P2 建议反馈功能完全不可用。
- **判别路径**：TC-FIT-05 第一步即 POST feedback——在修复前 commit `8e3d38f` 上该用例红（500 ≠ 200），红即证据。

### GAP-2：feedback INSERT 永不写 `applied_at`/`before_metrics`，effect 端点却以其为门闩 → 效果追踪半接线

- **位置**：INSERT `server/routes/ai_session_admin.py:722-727`（列清单仅 `id, diagnosis_id, suggestion_id, action, applied_value, applied_by`）；effect 门闩 `:740`（SELECT `applied_at, before_metrics`）、`:744-745`（`if not row or not row[0]` → 一律返回 `not_applied`）、`:759`/`:769`（`before.get('skillName')` 过滤调用窗口）。
- **症状**：`ai_suggestion_feedback.applied_at` 无默认值、无触发器（实施时经 information_schema/pg_trigger 实测），INSERT 列清单不含它——applied 反馈落库后 `applied_at` 恒 NULL、`before_metrics` 恒 NULL；而 effect 端点对 `applied_at IS NULL` 一律返回 `not_applied`，7 天窗口对比永远算不出。**即使 BUG-1 修复，「applied → tracked」闭环仍是断的**。
- **影响**：效果追踪（SkillOpt P2 核心卖点）不可用——用户应用建议后永远看不到 before/after 对比。
- **判别路径**：同 TC-FIT-05。BUG-1 修复后该用例仍会红在 `expect(eff.status).toBe('tracked')`——两级判别。

### 修复验证要求

1. **BUG-1 修复后**：TC-FIT-05 的 POST feedback 步骤转绿（200 + feedbacks 列表行可查）。
2. **GAP-2 修复后**：TC-FIT-05 全绿（effect 返回 `tracked` + `before.invocations>=1`）。
3. **GAP-2 修复需代码审查复核 before_metrics 列对位**：TC-FIT-05 的 `>=1` 计数断言**检不出** before_metrics 快照内容本身的列错位/键错位（如 `skillName` 键写错、JSON 序列化形状不符、INSERT 列序与 VALUES 错位）——测试只能证明「窗口有数」，不能证明「快照内容正确」。修复 commit 必须人工核对：INSERT 写入的 before_metrics 结构与 effect 端点读取键（`:759`/`:769` 的 `before.get('skillName')`）逐键一致，且 applied_at 在 applied 分支显式赋值 `NOW()`（或列默认）。

### 修复记录（2026-10-06，分支 `fix/skillopt-feedback-chain`）

- **修复内容**（`server/routes/ai_session_admin.py` `suggestion_feedback`）：
  - BUG-1：`log_operation` 补齐第 5 位置参数——target_name 置 `None`（同文件 `:1416` 回写路由同款惯例），原文本落 description；
  - GAP-2：applied 分支 INSERT 增写 `applied_at = NOW()` 与 `before_metrics = {"skillName": <解析值>}`（JSONB）；appliedValue 契约 = JSON 串 `{"skillName": ...}`（兼容纯文本技能名），解析不出 skillName 时记 null——effect 端 `coalesce(%s, skill_name)` 回退全技能统计（不阻断落库）。
- **人工核对（验证要求 #3）**：INSERT 列序 `(…, applied_at, before_metrics)` 与 VALUES `(…, NOW(), %s::jsonb)` 逐位对齐；`_json.dumps({'skillName': skill_name})` 与 effect 读取键 `before.get('skillName')` 逐键一致。✅
- **验证结果**：
  1. TC-FIT-05 单例：**passed（5.0s）**——POST feedback 200 + effect `tracked` + `before.invocations>=1`（两级判别全通过）；
  2. 三 spec 全量：**14 passed + 2 skipped（4.2m）**——TC-FIT-05 红点消除，仅 TC-FIT-06/TC-TRACE-05 因 LLM 不可达保持 skip；
  3. L1 回归：`test_skill_fit_routes.py + test_skillopt.py + test_skill_fit.py + test_skillopt_collection.py` **50 passed**。
- 该用例自本记录起转为常规回归绿；修复前 commit（`d9d8f01` 及此前）上红 = 判别力证据存档于 §4。

---

## 3. @llm skip 留证

| 用例 | 预检机制 | 今日结果 |
|---|---|---|
| TC-FIT-06（`ai-skillopt-fit.spec.ts:240-266`） | 首步 `POST /skill-def-steps/generate`，502 即 `test.skip(true, 'LLM 不可用（generate 502），冒烟跳过')`（:246） | skip |
| TC-TRACE-05（`ai-trace-analysis.spec.ts:134-207`） | 正式步骤前先打一发 generate 探活（:148-153），502 即 `test.skip(true, 'LLM 不可达（预检 502），冒烟跳过')`；analyze 步骤还有第二道 502 兜底（:175） | skip |

**环境说明（2026-10-06 实测）**：

- app 侧 LLM 通道配置在 **dev 库 `ai_settings` 表**（`utils/ai_query.py:61` `get_ai_settings` 读取），当前 endpoint 值为 `https://x/v1/chat/completions`——host `x` 不可解析（`nslookup` 无 A 记录，`curl https://x/...` exit 28 超时），故 `utils/skill_fit_ai.py` 的 generate/diagnose 走到 `RuntimeError → 路由层 502`，两例预检双双命中 skip。
- **聊天/批任务链路不受影响**：该链路走 OpenCode 二进制（`server/.env` `OPENCODE_BIN`），与 app 侧直连 LLM 客户端是两条通道——本轮全量回归中依赖 OpenCode 真会话的用例（如 ai-governance-audit 执行审计真会话链路）全绿可佐证。TC-TRACE-05 若非预检 skip，其「OpenCode 建会话 + UI 发消息」步骤本身可工作，卡点只在后续 analyze 的 LLM 收敛。
- 这是**环境配置问题而非产品缺陷**，预检 skip 机制正是为这种环境设计的（唯一允许的 skip 形态，plan Global Constraints）。

**LLM 恢复后重跑命令**（把 `ai_settings.endpoint` 换成可达 host + 有效 api_key 后）：

```bash
npx playwright test e2e/ai-full/ai-skillopt-fit.spec.ts -g "TC-FIT-06"
npx playwright test e2e/ai-full/ai-trace-analysis.spec.ts -g "TC-TRACE-05"
```

---

## 4. 全量回归结果

- 环境：dev 栈四服务（backend :3002 / vite :5173 / OpenCode :4096 / MCP :3003）运行前探活全部存活；`workers: 1` 串行（playwright.config.ts:9）。
- 范围：`e2e/ai-full` 全量（含 batch 子目录既有用例 + 三个新 spec 16 例）。

| 分片 | 命令 | passed | failed | skipped | 汇总行 |
|---|---|---|---|---|---|
| 1/2 | `npx playwright test e2e/ai-full --shard=1/2` | 63 | 1 | 2 | `1 failed`（e2e\ai-full\ai-skillopt-fit.spec.ts:201:1 › TC-FIT-05 反馈→效果追踪：applied 落库 + before 窗口含种子调用 + 400/404）<br>`2 skipped`（TC-FIT-06、TC-TRACE-05）<br>`63 passed (24.5m)` |
| 2/2 | `npx playwright test e2e/ai-full --shard=2/2` | 43 | 1 | 0 | `1 failed`（e2e\ai-full\batch\lifecycle.spec.ts:24:1 › 批任务：暂存上传→创建→BatchGroup 展示→子会话全文→终态治理）<br>`43 passed (35.6m)` |

- 分片 1 唯一失败即 TC-FIT-05（预期红）：`expect(fb.status).toBe(200)` 实得 **500**（ai-skillopt-fit.spec.ts:217）——POST feedback TypeError→500，即 §2 BUG-1 的判别力证据；两个 skip 即 §3 @llm 预检（`ai_settings` endpoint host `x` 不可达 → generate 502 → `test.skip` 留证）。
- 两分片合计：106 passed / 2 failed / 2 skipped，全量 16 例新用例全部落在分片 1 且表现与 §1 预期逐一吻合。

**预期外失败处置记录**：

- **`e2e/ai-full/batch/lifecycle.spec.ts:24 批任务：暂存上传→创建→BatchGroup 展示→子会话全文→终态治理`**（既有 batch 套件成员，非本轮 16 例新用例）。分片 2 内失败：`Test timeout of 600000ms exceeded` 卡在 `page.waitForLoadState('networkidle')`（lifecycle.spec.ts:61，同 shard 日志可见 `SSE 更新延迟 8840ms`——app 侧 SSE 长连接使 networkidle 永不达成，属环境/时序类 flake）。
  - **单次重跑**：`npx playwright test e2e/ai-full/batch/lifecycle.spec.ts -g "批任务：暂存上传→创建"` → **1 passed (29.1s)**（用例本体 27.3s），同机同栈立即转绿。
  - **处置**：判为 flake，记录不修复，未改任何测试/产品代码。

**与预期 outcomes 的偏差**：

- 分片 1：**无**——恰 1 failed（TC-FIT-05 预期红）+ 2 skipped（TC-FIT-06 / TC-TRACE-05 预期 skip）+ 63 passed，与预期 outcomes 完全一致。
- 分片 2：1 例预期外失败（batch/lifecycle，见上——单次重跑转绿，判 flake）；除此之外与预期一致（TC-FIT-05 与两例 @llm skip 均落在分片 1，分片 2 无新用例故 0 skipped）。

### 终审修复后复跑（2026-10-06）

**复跑原因**：终审发现两处测试侧问题并已修复——**F1**（TC-SESS-01 原 `status=completed` 断言在 6 条全 completed 种子上属空洞通过——过滤即使为 no-op 也绿；补种第 7 条 `status='closed'` regular 会话，改为 `status=completed&kind=all` 精确 id 排除断言 + `status=closed&kind=all` 单命中断言，种子计数随之 6→7）；**F2**（TC-TRACE-05 `test.setTimeout` 600s→900s，对齐 660s 真实预算——探针 + assistant 180s + 轮询 480s——留 headroom）。F1 改变了种子计数，**证据必须反映提交后的代码**，故对 16 例新用例所在的分片 1 复跑。

| 分片 | 命令 | passed | failed | skipped | 汇总行 |
|---|---|---|---|---|---|
| 1/2（终审修复后复跑） | `npx playwright test e2e/ai-full --shard=1/2` | 63 | 1 | 2 | `1 failed`（e2e\ai-full\ai-skillopt-fit.spec.ts:201:1 › TC-FIT-05 反馈→效果追踪：applied 落库 + before 窗口含种子调用 + 400/404）<br>`2 skipped`（TC-FIT-06、TC-TRACE-05）<br>`63 passed (21.3m)` |

- 复跑结果与修复前**完全一致**：唯一失败仍为 TC-FIT-05 预期红（`expect(fb.status).toBe(200)` 实得 **500**，§2 BUG-1 判别力证据，不受测试侧修复影响）；TC-SESS-01 收紧后的 status 精确断言转绿，同时证实 v2 列表 status 过滤**非** no-op（closed 种子被 completed 精确排除）。
- **分片 2 不受影响，未复跑**：本次修复仅触及 `ai-session-admin-v2.spec.ts`、`ai-trace-analysis.spec.ts`、`ai-skillopt-fit.spec.ts` 三个文件，与分片 2 的测试文件集合**无交集**（分片 2 不含本轮 16 例新用例），其既有结果继续有效。
- TC-TRACE-05 再次因 LLM 预检 502 skip（`ai_settings` endpoint host `x` 不可达，见 §3）——F2 的 900s 超时改动本次**未被实际执行**（skip 用例不消耗新预算），属纯预算余量修正；待 LLM 恢复后按 §3 命令补跑时生效。
- §1.1 TC-SESS-01 行的「验证方式/判别力点」已随 F1 同步订正（6 条→7 条种子、status 精确断言描述），与本表其余部分保持一致。

---

## 5. 结论

- 16 例新用例中 14 确定性用例全部按预期表现；TC-FIT-05 红 = BUG-1 + GAP-2 的判别力证据（非测试缺陷，断言不弱化）；TC-FIT-06 / TC-TRACE-05 因 app 侧 LLM host 不可达按设计预检 skip 留证。
- 本表即为 plan Task 17 要求的判别力登记产物；修复 BUG-1 / GAP-2 后按 §2 验证要求回归 TC-FIT-05，按 §3 命令补跑两例 @llm。
