# AI 联动功能正确性测试套件设计（轨迹分析 / 技能优化 / 会话管理 v2）

> 日期：2026-10-06 ｜ 状态：已评审通过（聊天内分节确认）
> 背景：`docs/ai-testing/18-新增功能补充测试方案（2026-10-04）.md` G5 规划的 SkillOpt 拟合链路 e2e（TC-FIT-01~04）尚未落地；会话管理 v2 端（`/ai/chat/admin/sessions/v2` 系列）与轨迹分析深水区（分析闭环/fail-closed/失败清理）在 e2e 层为空白。
> 结论：三个功能合计新增 **16 例 e2e**（14 确定性 + 2 例 @llm 冒烟），落位 `e2e/ai-full/` 三个新 spec，分三批实施。

## 0. 总体策略

**断言三通道**（每例按需组合，UI 骨架必走真实链路，对齐 ai-full 惯例）：

1. **UI 真实链路**：Playwright 页面操作（管理页 / 聊天页）
2. **API 契约**：`helpers.ts` 的 `api()` / 直连 `http://127.0.0.1:3002`，断言响应结构与状态码
3. **DB / 文件系统断言**：`e2e/ai-full/batch/db_exec.py` 种子与核对、定义文件读盘比对——状态流转的最终真相在库里，仅看 UI 判别力不足

**LLM 策略（已确认）**：混合——确定性为主；LLM 依赖路径（轨迹分析报告产出、SkillOpt generate/diagnose）只做 2 例 @llm 冒烟，`testInfo.annotations.push({ type: 'llm' })` 标记（沿用 `batch/resilience.spec.ts:178` 惯例，默认照跑、可 `--grep-invert` 排除）。

**与既有资产的边界（不重复测）**：
- `e2e/execution-audit.spec.ts`：审计抽屉三态、种子契约、触发分析的联动断言（:175）——不重测；本套件 TC-TRACE-04 只补「分析历史 / 报告端点契约」
- `e2e/ai-governance-audit.spec.ts`：admin 归档 → reopen 403 → oplog——不重测；本套件 TC-SESS-04 只测 v2 UI 归档入口与 409 分支
- L1 资产（`test_ai_session_admin_analyze.py` 的 502/409/404、`test_skill_fit*.py`、`test_skillopt*.py`）——不迁移

**判别力与验证流程（用户既定要求）**：
- 每例在 spec 注释与判别力登记表中登记「预期抓取的缺陷」
- 新用例先在 main 当前 commit 跑出全绿基线；后续轮次若测出真 bug，**修复前 commit 上该测试必须失败**，每轮报告独立核对
- 判别力登记表归档 `docs/ai-testing/evidence/`

## 1. 事实基础（设计依据，均已在 main 核实）

| 事实 | 位置 | 对设计的意义 |
|---|---|---|
| analyze 端点 fail-closed 三分支：MCP /health 不可达 502、`internal_mcp_enabled()` 关闭 409、trace-analyzer 技能缺失 409 `TRACE_SKILL_MISSING` | `server/routes/ai_session_admin.py:157-302` | TC-TRACE-01/02 确定性触发 |
| `_fail()` 收敛清理：删 messages/sessions 行、diagnosis 置 failed、清工作区、停 listener | `ai_session_admin.py:238-263` | TC-TRACE-03 三处残留全查 |
| `POST /skill-def-steps/apply` 为**纯机械回写**：steps 由请求体传入（不经 LLM），写盘→bytes 回读 sha256→`register_def_version` 归档正文；`preview` 纯贪心匹配不落库 | `ai_session_admin.py:1371-1439` | TC-FIT-02/03 可零 LLM 确定性测完整链路 |
| `generate` / `diagnose` 打 LLM（502 失败；diagnose 有进程内签名缓存） | `ai_session_admin.py:1344-1460` | 只进 @llm 冒烟 |
| `internal_mcp_enabled` 是后台设置开关（提示语「请在 AI 设置中重新启用」） | `ai_session_admin.py:194-202` | 409 可通过设置 API/DB 确定性翻转 |
| batch 侧已有 `restartBackend`（带 env 重启后端并恢复） | `e2e/ai-full/batch/resilience.spec.ts:180` 使用 | TC-TRACE-02/03 的进程级触发手段 |
| v2 列表 `source_type` 为 SQL 计算列（regular/batch/api_batch/scan/kefu），`kind` 过滤默认隐藏 `trace_analysis` | `server/utils/session_admin_repo.py:25-54` | TC-SESS-01 判别力点 |
| runtime 采集事件上报端点（internal token）与 heuristic 兜底不降级语义 | `server/routes/ai_memory_internal.py:56-71`、`server/utils/skillopt.py:36-87` | TC-FIT-04 |
| rollback 按 hash 一致语义**不产生新版本行** | `ai_session_admin.py:1230-1267` | TC-FIT-03 判别力点 |
| 18 号文档 G5 TC-FIT-01~04 原规划 | `docs/ai-testing/18-新增功能补充测试方案（2026-10-04）.md` §G5 | 本设计 TC-FIT-01~06 为其落地+扩展拆解 |

## 2. Batch 1：`e2e/ai-full/ai-session-admin-v2.spec.ts`（5 例，零 LLM）

种子工具：`db_exec.py` 造多形态会话（`AITEST-` 前缀命名，用后清理）。

| 用例 | 手法 | 关键断言（**粗体**为判别力登记点） |
|---|---|---|
| TC-SESS-01 v2 列表筛选正确性 | 种子 regular/batch/api_batch/scan/kefu 各≥1 + ≥1 archived + ≥1 `kind='trace_analysis'` | sourceType 五类逐个过滤正确；status 筛选正确；owner 筛选（配合 `toolbox.ts` secondUser）；分页 total 与筛选交集一致；**kind 默认隐藏 trace_analysis、`kind=all` 可见**；**source_type 计算列优先级**（同会话多标记时的归类） |
| TC-SESS-02 关键词全消息搜索 | 种子两条消息含独特关键词 | 只返回目标会话；无匹配关键词返回空集 |
| TC-SESS-03 管理端详情抽屉三 tab | 种子消息+子任务+工作区文件；UI 打开抽屉 | 基本信息字段与种子一致；对话历史渲染含**子代理下钻**；文件下载列表出现且**下载内容与种子一致**（Playwright download API 读盘比对） |
| TC-SESS-04 v2 行内归档 | UI 对他人 active 会话点归档 | 列表状态翻转为 archived + oplog 留痕；**对批子会话归档返回 409 `BATCH_SESSION_CONTROLLED`**（UI 错误提示可见） |
| TC-SESS-05 权限边界 | 非 admin token（secondUser） | v2 系列端点全部 403，不泄漏存在性 |

## 3. Batch 2：`e2e/ai-full/ai-skillopt-fit.spec.ts`（6 例，1 例 @llm）

种子工具：扩展 `db_exec.py` 新增 `seedFitAttempt`（attempt/manifests/events 轨迹三态，参考 `e2e/helpers/seed_audit.py` 手法）；临时技能定义写入 `server/ai-workspaces/global-skills/` 下 `AITEST-` 临时目录（路径在 `_path_in_allowed_roots` 白名单内），用后删目录+DB 行。

| 用例 | 手法 | 关键断言（**粗体**为判别力登记点） | 对应 18 号 |
|---|---|---|---|
| TC-FIT-01 拟合三态计算 | 种子 fit/partial/diverged 三种轨迹 → `POST /skill-fit/<attempt_id>/recompute` 确定性触发 | 三态正确落库；**唯一索引 `uq_skill_fit_attempt_def` 重复 recompute 不产生重复行**；`definition-summary` 聚合数一致；UI：拟合列表出现、明细步骤图标/匹配状态、子代理归属列 | TC-FIT-01/02 |
| TC-FIT-02 试算 preview | 手工构造合法 steps 调 `POST /skill-def-steps/preview` | `per_step/steps_total/steps_hit/score/status` 形状与贪心匹配结果正确；steps 非法（缺 id/expect 非数组）400；attempt 不存在 404；**不落库**（recompute 后行数不变） | TC-FIT-04 前半 |
| TC-FIT-03 回写→自动归档→回滚 | 临时技能文件 + 手工 steps 调 `POST /skill-def-steps/apply` | 文件内容真被改写（读盘比对）；`ai_skill_def_versions` 新行 **content 非空且 sha256 与落盘字节自洽**；默认 label `AI步骤优化 <当日>`；UI 时间线出现新版本可预览/diff；**rollback 后文件恢复原内容且不产生虚假新版本行**；path 逃出允许根 400 | TC-FIT-04 后半 |
| TC-FIT-04 调用采集链路 | 直接 `POST /ai/memory/internal/runtime-events`（internal token） | invocations 落库 `source=runtime`；重复上报**不被 heuristic 降级**（upsert ON CONFLICT 语义）；单独种子 heuristic 行不覆盖 runtime 行；`GET /skill-analytics` 聚合正确 | 新增（18号未规划） |
| TC-FIT-05 feedback→effect | 种子结果行 → `POST /analyses/<id>/suggestions/<sid>/feedback {action:'applied'}` | 反馈落库 + before_metrics 快照；`GET /skill-suggestions/<id>/effect` 返回 7 天窗口形状 | 新增 |
| TC-FIT-06 @llm 生成+诊断冒烟 | 对真实定义文件 `generate` → `diagnose`（独立种子结果行规避 diagnose 进程内缓存串扰） | steps 数组结构合法；diagnosis 结构 `{cause, suggestions[], revised_steps}`；oplog 留痕两条（生成/诊断）；**LLM 不可达时 `test.skip()`**（冒烟目的是真链路能通，不是环境守恒） | TC-FIT-03 落地 |

## 4. Batch 3：`e2e/ai-full/ai-trace-analysis.spec.ts`（5 例，1 例 @llm）

| 用例 | 手法 | 关键断言（**粗体**为判别力登记点） |
|---|---|---|
| TC-TRACE-01 内置 MCP 禁用 409 | 管理设置 API（或 DB）关闭内置 MCP → UI 点「轨迹分析」→ 恢复设置 | 错误提示含「内置 MCP 已被禁用」语义；**无孤儿 `kind='trace_analysis'` 会话行**（DB 断言） |
| TC-TRACE-02 MCP 不可达 502 | `restartBackend` 带 `MCP_SERVER_URL` 指死端口（**实施时核实该 env 覆盖生效路径**；不可行则降级为停 MCP 进程，效果等同） | 502 提示含 MCP 启动指引；无新会话行 |
| TC-TRACE-03 派发失败清理 | `OPENCODE_BASE_URL` 指死端口重启 → 触发（MCP 健康检查通过、派发必败） | **`ai_execution_diagnoses` 行 status='failed' 且 error_message 落库；无孤儿 sess_ 会话行；工作区目录被清理**——§22 P0-8 三处残留逐一核对；用后恢复 env |
| TC-TRACE-04 分析历史与报告契约 | 种子 `kind='trace_analysis'` 会话 + diagnoses 行 | v2 API 层默认隐藏/`kind=all` 可见；`GET /analyses`（会话维度）列表形状；`GET /admin/analyses/<id>` 状态轮询契约；report 端点空态形状。与 execution-audit:175 分工：那边管「触发后联动」，这边管「历史/报告端点契约」 |
| TC-TRACE-05 @llm 分析闭环 | 真实会话真跑一轮（参照 `execution-audit.spec.ts:30-59` beforeAll 手法）→ 触发轨迹分析 → 轮询至 completed | report JSONB 非空且**结构完整（字段存在，不断言内容语义）**；审计抽屉「轨迹分析历史」出现该行；LLM 不可达时 skip |

## 5. 基础设施增补

- **种子 helper**：`seedSessions`（多形态会话族）、`seedFitAttempt`（轨迹三态），实现于 spec 内或抽到 `e2e/ai-full/helpers.ts`（以不膨胀为度）；复用 `db_exec.py` SQL 桥
- **`restartBackend` 共享化**：从 batch 侧（resilience/toolbox）提升到 batch-helpers 或新 helper 模块，供 Batch 3 使用
- **internal token 获取**：从 server 配置/env 读取（Batch 2 实施时定具体来源）
- **清理纪律**：每例 `finally`/`afterAll` 自清理——DB 行（AITEST 前缀定点删除）、临时文件/目录、env 与设置恢复；对齐「dev 库定点清理」惯例
- **运行环境**：dev 栈（`:3002` + `:5173`），`npm run test:e2e`，workers=1 串行（既有配置不变）

## 6. 实施批次与准出

| 批次 | 内容 | 提交 |
|---|---|---|
| Batch 1 | ai-session-admin-v2（5 例） | 独立 commit |
| Batch 2 | ai-skillopt-fit（6 例）+ seedFitAttempt | 独立 commit |
| Batch 3 | ai-trace-analysis（5 例）+ restartBackend 共享化 | 独立 commit |

每批完成后跑全量 `e2e/ai-full` 回归防串扰。

**准出标准**：
1. 16 例在 main 当前 commit 全绿（2 例 @llm 在 LLM 可用环境下绿；不可达时 skip 需留证）
2. 判别力登记表归档 `docs/ai-testing/evidence/`（每例：预期缺陷 + 验证方式）
3. 全量回归无串扰（既有 ~94 例 + 新增 16 例）
4. 若任一新用例在基线就测出真 bug：登记 → 报告 → 走「修复前测试失败」独立核对流程

## 7. 开放点（不阻塞设计，实施时定）

1. `MCP_SERVER_URL` 是否可被 env 覆盖且 analyze 端点读取的是运行时值——TC-TRACE-02 实施首步核实，不可行走停进程方案
2. internal runtime-events 端点的 token 获取方式——TC-FIT-04 实施首步定
3. diagnose 进程内缓存的用例隔离——TC-FIT-06 用独立种子结果行规避，若仍串扰则该例独立 spec 文件
4. `@llm` 冒烟例的超时上限——参照 resilience（540s/600s），TC-TRACE-05 初定 600s
