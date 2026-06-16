# AI 定时任务（定时 AI 数据流水线）使用与设计文档

> 适用版本：`feat/custom-roles-rbac` 分支起。配套规格/计划见
> `docs/superpowers/specs/2026-06-03-scheduled-ai-row-processor-design.md`、
> `docs/superpowers/plans/2026-06-03-scheduled-ai-row-processor.md`。

## 1. 这是什么

一个**通用范式**，不是单一的「审核」功能：

> 按调度周期**扫描某个数据页**，把每条待处理记录连同其**字段数据 + 附件文档**作为上下文交给一个 AI 会话，AI 按**提示词（可引用某个 skill）**工作，产出**结构化 JSON**，流水线解析后**回写到该记录的指定列**并流转状态。

同一个引擎可配置出不同用途：方案审核、需求扫描分析、自动分类/打标、信息抽取补全、合规检查……差异**全部在配置里**。「方案审核」只是一个配置实例。

**执行复用现有 AI 批任务引擎**：每次扫描 = 一个批任务，每条记录 = 一个子会话，可在「批任务」面板点进去看每个 AI 的完整对话与结果（3 并发 / 超时 / 重试）。

入口：「系统配置 → 数据工具 → AI 定时任务」(`/admin/ai-scan-tasks`)，需 `admin.ai_scan` 权限。

## 2. 核心机制：状态字段三态流转

每个任务在目标数据页上选一个**状态字段**，用它驱动进度并防止重复处理：

```
待处理(pendingValue) ──认领──▶ 处理中(runningValue) ──AI完成──▶ 已处理(doneValue)
                                                    └──失败/解析不出──▶ 处理失败(failedValue)
```

- **认领是原子的**（`FOR UPDATE SKIP LOCKED` + 一次 UPDATE），并发扫描/多进程都不会重复挑同一条。
- 扫描**只挑「待处理」**，所以 `处理失败` 的记录不会被自动重试——它显式停下等人工排查；重试 = 把其状态改回待处理，或在批任务面板点「重试失败」。
- `pendingValue` 留空表示「匹配 该字段为空/未设置 的记录」。

## 3. 配置一个任务

在「AI 定时任务」页点「新建任务」，填写：

| 配置项 | 说明 |
|--------|------|
| **名称 / 启用** | 任务名；停用后调度器跳过它 |
| **数据页(collection)** | 要扫描的数据页的 collection 名（如 `inspection-case`） |
| **分支** | 默认 `main` |
| **状态字段** | 记录里用于三态流转的字段名（如 `审核状态`） |
| **待处理/处理中/已处理/失败值** | 四个状态取值（后三个有默认：处理中/已处理/处理失败） |
| **调度间隔(分钟)** | 多久扫一次（默认 15） |
| **每次最多条数** | 单次扫描最多认领多少条（默认 20；worker 仍 3 并发逐步消化） |
| **候选过滤(JSON)** | 可选，MongoDB 风格过滤，叠加在「待处理」之上（如 `{"优先级":"高"}`）。⚠️ **用记录的原始字段名，不是显示标签** |
| **提示词** | 你的操作指令，可在里面引用要用的 skill（如「用方案审核 skill 审核」）。系统会**自动在末尾追加「输出 JSON 契约」**，你不用自己写格式 |
| **字段映射** | 一行行 `AI JSON 键 → 回写到的列 (必填?)`。它既决定回写哪些列，也决定自动追加的 JSON 契约长什么样 |

填完点「保存」。「立即运行」可手动触发一次扫描用于测试。

### 「方案审核」配置示例
- 数据页：`方案审核表`
- 状态字段：`审核状态`，待处理值留空
- 提示词：「请阅读 uploads/record.md 中的方案信息（附件在 uploads/attachments/），用方案审核 skill 审核，给出结论和意见。」
- 字段映射：`结论 → 审核结论 (必填)`、`意见 → 审核意见`

## 4. 每个 AI 会话拿到什么上下文

对每条认领的记录，流水线在会话工作区准备：
- **`uploads/record.md`** —— 该记录的字段（标签—值），**自动排除**状态字段和被映射的输出列（避免 AI 看到占位/旧值）。
- **`uploads/attachments/`** —— 记录里 file/image 字段上传的真实文档（自动从 `data_files` 拷入；同名文件加 id 前缀防冲突）。

发给 AI 的提示词 = 「系统前言（告知数据在 uploads/）」+「你的提示词」+「系统自动追加的 JSON 输出契约」。

## 5. 回写与失败处理

子会话完成后：
- **成功**：从 AI 回复里提取最后一个 ```json``` 代码块（兜底：最后一个配平的 `{}`），校验必填键齐全 → 一条 `UPDATE` 把映射列写入、状态置「已处理」。
- **解析失败 / 缺必填键 / 子会话失败超时** → 状态置「处理失败」（AI 原始输出仍可在批任务面板查看）。
- **记录中途被删** → 回写命中 0 行，记日志、空操作。

**不卡死保证**：认领后建批任务失败会回滚为待处理；进程崩溃由启动孤儿清扫修复（处理中但无存活子会话的记录重置为待处理）；删除任务前先把其在途「处理中」记录重置为待处理。

## 6. 设计与关键文件

- 数据模型：`ai_scan_tasks` 表（任务配置）+ `ai_chat_sessions` 增列 `scan_task_id`/`source_record_id`（让回写钩子定位记录）。
- 调度：`server/utils/ai_scan_scheduler.py`（APScheduler 每分钟 tick，按 `last_run_at + interval` 判定到期，任务级进程内锁防重叠，`WERKZEUG_RUN_MAIN` 守卫）。
- 引擎：`server/utils/ai_scan_engine.py`（`claim_records` 原子认领、`build_context_dir` 组上下文、`assemble_prompt` 拼提示词+契约、`on_child_finished` 回写、`run_task`、`sweep_orphans`）。
- 复用：`server/utils/batch_engine.py`（`_prepare_workspace` 支持拷贝上下文目录 + 完成钩子 `on_child_finished`，带 Windows 瞬时错误重试）、`server/utils/batch_repo.py`（`create_batch` 盖 `scan_task_id`）。
- 配置 API：`server/routes/ai_scan_tasks.py`（CRUD + run-now，全部 `@require_permission('admin.ai_scan')`）。
- 前端：`src/views/admin/AiScanTaskManager.vue` + `stores/aiScanTask.ts` + `api/aiScanTask.ts` + `types/aiScanTask.ts`。
- API 全程 **camelCase**（读写一致）。

## 7. 注意事项与范围

- **跨表/建记录的副作用**：V1 流水线只做「同一行的结构化回写」。如果要「扫需求表 → 在任务表建条目」这类 1→N 操作，让 AI 在 skill 里经 **MCP** 自己完成；流水线仍负责本行的状态+映射回写。
- **失败不自动重试**：`处理失败` 的记录停下等人工，避免无限重跑。
- 仅按**分钟间隔**调度（无 cron）；V1 只扫 `main` 分支。
- **候选过滤用原始字段名**（非显示标签）。
- 需要 OpenCode 运行时（`opencode serve`）+ MCP 服务可用，子会话才能真正执行。

## 8. 测试与验证

- 后端：`tests/test_ai_scan_engine.py`、`tests/test_ai_scan_writeback.py`、`tests/test_routes_ai_scan_tasks.py`（认领/上下文/提示词/JSON 提取/回写成功+失败/路由+RBAC/camelCase 往返/Windows 拷贝重试）。
- 端到端（Playwright 已实测）：建任务 → 立即运行 → 批任务面板出现批任务 → 记录状态 `待处理→处理中→已处理`，映射列被写入；失败子会话置 `处理失败`。
