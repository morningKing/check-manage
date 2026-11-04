# 后端文件结构重构设计（backend restructure）

- 日期：2026-11-03
- 分支：`feat/backend-restructure`（本 spec 与全部重构工作均在此分支）
- 状态：设计定稿，待实施计划

## 1. 背景与目标

`server/utils/` 已膨胀为 **113 个文件、36,792 行**的单一命名空间：批任务引擎、仓库层、
插件模板、调度器、小工具全部混在 `utils` 下，其中 9 个文件超过 1000 行
（最大 `batch_engine.py` 4232 行，`BatchWorker` 单类约 3400 行）。定位代码靠记忆和
全局搜索，维护困难。

**目标**：按业务域垂直分包 + 拆分千行巨文件，**行为零变化**，靠既有测试套件证明。

**非目标**：

- 不改 `routes/` 的文件组织（`ai_chat.py` 等巨路由的拆分另立专项）
- 不重新划分业务逻辑边界、不引入 service 层抽象——只动文件归属与超大文件内部分块
- 不改任何 SQL、算法、协议、日志文案等运行时行为

## 2. 硬约束（用户明确要求）

1. **全程只在特性分支 `feat/backend-restructure` 上工作，main 冻结不动**；
   全部批次测试通过 + 用户确认后，才合回 main。
2. **硬切兼容**：不保留旧 `utils.*` 路径 shim、不设过渡别名层；仓库内全部 import
   （含测试、脚本、mcp-server）随各批迁移同步改到新路径，批内即完整可用，
   最终 `utils/` 整体消失。
3. 重构深度 = 结构重组 + 拆巨文件；routes 只做配套的 import 机械修正。
4. 单分支分批提交（每批一个明确 commit），一次合回。

## 3. 现状盘点（2026-11-03 实测）

| 区域 | 规模 | 备注 |
|---|---|---|
| `server/utils/` | 113 文件 / 36,792 行 | 9 个文件 >1000 行 |
| `server/routes/` | 57 文件 / 23,119 行 | 本次不动结构 |
| 根文件 | app.py 804（含 30+ 段迁移启动块）、init_db.py 1807、proxy.py 1140 | app.py 启动块本批收敛 |
| utils 内部互引 | `operation_log`×40、`batch_repo`×24、`batch_engine`×21 | 全静态引用，无字符串拼模块名 |
| `import utils.*` 站点 | routes 55 文件、tests 178 文件、根文件 4 个、mcp-server 3 文件、scripts 4 文件、utils 内部 | 约 350 文件 / 1000+ 行 import |

**已排除的风险**：沙箱脚本（`script_runner._validate_script`）禁止 `import` 语句、
`__import__` 在 FORBIDDEN_NAMES 中——数据库存量脚本不依赖服务器模块路径，硬切不影响。

## 4. 目标结构（113 文件完整映射）

```
server/
  app.py / config.py / db.py / auth.py / init_db.py / proxy.py   # 根文件保留
  common/            # 横向基础设施
  ai/                # AI 全家桶，7 个子包
  data/              # 数据管道与执行
  projects/          # 项目版本/备份/工作区
  editor/            # 智能写作
  collab/            # 协作域（工作流/行动/客服/webhook）
```

### 4.1 common/（15 文件）

| 原文件 | 行数 | 新路径 |
|---|---|---|
| operation_log.py | 139 | common/operation_log.py |
| permissions.py | 158 | common/permissions.py |
| rbac_guard.py | 12 | common/rbac_guard.py |
| notifier.py | 106 | common/notifier.py |
| api_errors.py | 71 | common/api_errors.py |
| errors.py | 19 | common/errors.py |
| logging_setup.py | 69 | common/logging_setup.py |
| log_tail.py | 94 | common/log_tail.py |
| log_retention.py | 75 | common/log_retention.py |
| rate_limit.py | 33 | common/rate_limit.py |
| upload_limits.py | 62 | common/upload_limits.py |
| filename.py | 58 | common/filename.py |
| zip_unicode.py | 57 | common/zip_unicode.py |
| search_text.py | 28 | common/search_text.py |
| py_runner.py | 96 | common/py_runner.py |

### 4.2 ai/batch/（15 平铺文件 + 4 个拆分子包）

| 原文件 | 新路径 |
|---|---|
| batch_events.py | ai/batch/batch_events.py |
| execution_audit.py | ai/batch/execution_audit.py |
| execution_budget.py | ai/batch/execution_budget.py |
| execution_effect.py | ai/batch/execution_effect.py |
| execution_contract.py | ai/batch/execution_contract.py |
| execution_lease.py | ai/batch/execution_lease.py |
| execution_commands.py | ai/batch/execution_commands.py |
| execution_checkpoint.py | ai/batch/execution_checkpoint.py |
| verifier.py | ai/batch/verifier.py |
| oc_watchdog.py | ai/batch/oc_watchdog.py |
| todo_trace.py | ai/batch/todo_trace.py |
| trace_auditor.py | ai/batch/trace_auditor.py |
| run_diagnosis.py | ai/batch/run_diagnosis.py |
| delivery_outbox.py | ai/batch/delivery_outbox.py |
| subtask_repo.py | ai/batch/subtask_repo.py |
| batch_engine.py 4232 | **ai/batch/engine/**（见 §5.1） |
| batch_repo.py 1822 | **ai/batch/repo/**（见 §5.2） |
| agent_ledger.py 1320 | **ai/batch/ledger/**（见 §5.3） |
| perf_analysis.py 1064 | **ai/batch/perf/**（见 §5.4） |

### 4.3 ai/opencode/（7 平铺文件 + global 拆分）

| 原文件 | 新路径 |
|---|---|
| opencode_client.py | ai/opencode/opencode_client.py |
| opencode_ownership.py | ai/opencode/opencode_ownership.py |
| opencode_launch.py | ai/opencode/opencode_launch.py |
| opencode_parts.py | ai/opencode/opencode_parts.py |
| subagent_reuse_plugin.py | ai/opencode/subagent_reuse_plugin.py |
| tool_timeout_plugin.py | ai/opencode/tool_timeout_plugin.py |
| plugin_registry.py | ai/opencode/plugin_registry.py |
| opencode_global.py 1129 | **ai/opencode/opencode_global/ 子包**（聚合 `__init__` 导出全部原符号）：global_paths.py（目录发现：global_dir/skills_dir/agents_dir/skill_roots/find_skill_dir/OPENCODE_GLOBAL_DIR）、global_guard.py（validate_name/resolve_under/_reject_symlink_components/_check_content_size/_check_dir_size）、global_write.py（_atomic_write 及其余写入函数） |

### 4.4 ai/skills/（6 文件）

global_skills.py、skill_fit.py、skill_fit_ai.py、skillopt.py、
skill_candidate_eval.py、skill_upload.py → 同名移入 ai/skills/。

### 4.5 ai/sessions/（14 文件）

| 原文件 | 新路径 |
|---|---|
| chat_persist.py 849 | ai/sessions/chat_persist.py |
| ai_session_repo.py | ai/sessions/ai_session_repo.py |
| session_admin_repo.py | ai/sessions/session_admin_repo.py |
| session_history.py | ai/sessions/session_history.py |
| session_file_import.py | ai/sessions/session_file_import.py |
| session_token.py | ai/sessions/session_token.py |
| ai_message_meta.py | ai/sessions/ai_message_meta.py |
| ai_query.py | ai/sessions/ai_query.py |
| prompt_template.py | ai/sessions/prompt_template.py |
| memory.py | ai/sessions/memory.py |
| takeover_repo.py | ai/sessions/takeover_repo.py |
| mention_files.py | ai/sessions/mention_files.py |
| artifact_store.py | ai/sessions/artifact_store.py |
| status_badge_timeout_scheduler.py | ai/sessions/status_badge_timeout_scheduler.py |

### 4.6 ai/orchestration/、ai/gate/、ai/scan/

| 原文件 | 新路径 |
|---|---|
| orchestration_engine.py 1104 | **ai/orchestration/engine/ 子包**（聚合 `__init__` 导出全部原符号）：runs.py（create/get/list_run + _advance_run 状态机）、steps.py（_launch_agent_step/_run_edges/_render_prompt 等步骤启动）、approvals.py（_open_approval/_open_policy_approval/_abort_timeout_child） |
| orchestration_defs.py | ai/orchestration/orchestration_defs.py |
| approval_repo.py | ai/orchestration/approval_repo.py |
| gate_policy_repo.py | ai/gate/gate_policy_repo.py |
| action_check_extractor.py | ai/gate/action_check_extractor.py |
| ai_scan_engine.py | ai/scan/ai_scan_engine.py |
| ai_scan_repo.py | ai/scan/ai_scan_repo.py |
| ai_scan_scheduler.py | ai/scan/ai_scan_scheduler.py |
| ai_schema_designer.py | ai/scan/ai_schema_designer.py |

### 4.7 data/（15 文件）

mongo_query、script_runner、data_export、export_runner、export_references、
etl_engine、etl_scheduler、field_indexes、field_index_scheduler、
dependency_scheduler、scan_writeback_validator、mcp_servers、mcp_tool_rules、
sequences、page_config_relations → 同名移入 data/。

### 4.8 projects/（5 平铺文件 + 3 个拆分子包）

| 原文件 | 新路径 |
|---|---|
| version.py | projects/version.py |
| branch_lock.py | projects/branch_lock.py |
| workspace.py | projects/workspace.py |
| workspace_changes.py | projects/workspace_changes.py |
| workspace_outputs.py | projects/workspace_outputs.py |
| project_version.py 2267 | **projects/versioning/**（见 §5.5） |
| backup.py 1492 | **projects/backup/**（见 §5.6） |
| cross_project_dependency.py 1122 | **projects/dependencies/**（见 §5.7） |

### 4.9 editor/（4 文件）

suggest_editor_repo、suggest_editor_rules、suggest_editor_rule_ai、
suggest_editor_attachments → 同名移入 editor/。

### 4.10 collab/（14 文件）

workflow、workflow_engine、workflow_repo、row_action_engine、row_action_validate、
row_action_condition、trigger_engine、webhook、webhook_engine、kefu_repo、
kefu_event_bus、kefu_sse_ticket、kefu_guardrail、menu_export → 同名移入 collab/。

### 4.11 清理

- `utils/__init__.py`（0 行）随 utils/ 目录整体消失。
- 每个新包（含子包）建空 `__init__.py`。

### 4.12 补遗（2026-11-05 增补）：utils/runtime/ 子目录

批次 6 收口时发现盘点遗漏的 `utils/runtime/` 子目录（4 文件 423 行，Runtime
Adapter 抽象层，ai-harness-p2 spec §8：`__init__.py`/`base.py`/`opencode_local.py`/
`stub.py`，5 个生产 + 4 个测试 import）→ 整体迁入 **`ai/runtime/`**，消费方
（ai/batch/engine/facade、ai/orchestration/engine/runs、ai/sessions/chat_persist、
routes/ai_chat、相关测试）import 同步改写。总计 117 文件（非正文所称 113）。

## 5. 巨文件拆分方案

统一模式：**「包 + 聚合导出」**——拆出的子模块收进一个包，包的 `__init__.py`
re-export 全部公开符号；调用方永远只 import 包（`from utils.batch_engine import X`
→ `from ai.batch.engine import X`），符号名不变。子包内部互相引用走叶子模块。
拆类一律用 **mixin 单继承**（MRO 平凡），方法**逐字搬移**，`self` 语义零变化。

### 5.1 batch_engine.py (4232) → ai/batch/engine/

已核实的方法分组（行号基于当前 main）：

| 新模块 | 内容（原行号） |
|---|---|
| engine/facade.py | `_OpenCodeFacade`(L93-269)、`get_worker()`、`execution_lease_ttl()`(L276-283) |
| engine/lifecycle.py | `_workspace_root`/`_prepare_workspace`/`_recompute_batch_status`/`_cleanup_batch_oc_sessions`/`_notify_callback`(L288-576) |
| engine/exceptions.py | `_SessionTimeout`/`_format_diagnosis`/`_TurnFailed`/`_SessionCancelled`/`_LeaseLost`/`_SessionPaused`(L580-675) |
| engine/discovery.py | `discover_subtasks`/`subtask_status_from_messages`/`_raw_has_open_delegation`/`gate_participates`/`_require_fencing`(L676-783) |
| engine/worker.py | `BatchWorker` 组成壳：`__init__`/start/stop/租约与监管线程/`_dispatcher_loop`/`_claim_pending_sessions`/`_dispatch_tick`/`_safe_run_one`/`_restart_audit`/`_cancel_pending_requests`（mixin 组合点） |
| engine/runner.py | mixin：`_run_one`(约510行)、工作区准备与回收（`_provision_workspace`/`_record_workspace_files`/`_recover_session`/`_persist_user_prompt` 等）、完成保证（`_await_finished`/`_finish_with_guarantee`/`_progress_signature`/`_stall_diagnosis`/`_snapshot_assistant_ids`） |
| engine/gates.py | mixin：动作门禁（`_fetch_action_checks`/`_register_action_expectations`/`_check_action_gate`/`_maybe_gate_retry`/`_batch_gate_retry_flag`）、委派门禁与复用审计（`_maybe_check_delegation_gate`/`_fetch_subagent_reuse`/`_build_reuse_directive`/`_audit_reuse_violations`/`_deleg_gate_*`/`_check_agent`） |
| engine/subtasks.py | mixin：`_collect_subtasks`/`_write_subtask`/`_persist_conversation`/`_content_from_parts`/`_preview_from`/`_record_memory`/`_persist_provision_notice` |
| engine/transitions.py | mixin：`_resolve_generation`/`_on_transition_miss`/`_mark_done`/`_mark_failed`/`_close_attempt_for_requeue`/`_consume_parent_attempt`/`_notify_child_done`/`_notify_scan` |

worker.py 中组合：`class BatchWorker(WorkerRunnerMixin, WorkerGatesMixin, WorkerSubtasksMixin, WorkerTransitionsMixin)`。
公开符号（`get_worker`、`BatchWorker`、异常类、`discover_subtasks` 等）经
engine/__init__.py 聚合导出，外部可见 API 与今天完全一致。

### 5.2 batch_repo.py (1822) → ai/batch/repo/

| 新模块 | 内容 |
|---|---|
| repo/status.py | `recompute_batch_status_tx` |
| repo/children.py | `transition_child`/`read_child_control_state`/`cancel_child`/`get_child_session`/`reexecute_child`/`continue_child`/`resume_child`/`try_repark_transition` |
| repo/config.py | `validate_subagent_reuse`/`validate_max_concurrency`/`validate_models`/`assign_child_models`/`update_batch_config`/`get_max_files_per_batch` |
| repo/lifecycle.py | `create_batch`/`delete_batch`/`cancel_batch`/`pause_batch`/`resume_batch`/`append_to_batch`/`reset_failed_to_pending`/`_recompute_batch_status_for` |
| repo/queries.py | `list_batches`/`get_batch_detail`/`get_batch_results`/`get_session_usage`/`get_batch_usage`/`get_child_messages`/`_text_from_content` |

### 5.3 agent_ledger.py (1320) → ai/batch/ledger/

| 新模块 | 内容 |
|---|---|
| ledger/extract.py | `args_to_text`/`_start_dt`/`extract_from_parts`/`extract_from_part_map`/`extract_tool_parts` |
| ledger/record.py | `record_messages`/`record_state`/`finalize_interactive_turn`/`_fill_subtask_meta`/`_registered_subtask_ids` |
| ledger/query.py | `list_tool_calls`/`reconcile_task_durations` |

### 5.4 perf_analysis.py (1064) → ai/batch/perf/

| 新模块 | 内容 |
|---|---|
| perf/intervals.py | 区间数学原语：`_union`/`_covered_ms`/`_subtract`/`_intersect`/`coverage_split`/`_to_ms`/`_now_ms`/`_percentile` |
| perf/runs.py | 会话→attempt 链解析：`resolve_session_runs`/`_run_attempt_row`/`_open_conn` 等 |
| perf/report.py | 报告聚合与格式化（其余函数，实施时按 def 清单归位） |

### 5.5 project_version.py (2267) → projects/versioning/

| 新模块 | 内容 |
|---|---|
| versioning/repo.py | 分支/版本 CRUD：`get_project_collections`/`get_user_project_branch`/`set_user_project_branch`/`create_project_version`/`list_project_versions`/`switch_project_branch`/`initialize_project_branch_data`/`get_project_version_detail`/`delete_project_version`/`switch_to_main_project_branch` |
| versioning/diff.py | `load_project_version_data`/`load_project_snapshot_data`/`get_collection_fields`/`compute_collection_diff`/`load_project_version_data_by_type`/`compute_project_version_diff` |
| versioning/merge.py | `merge_project_version`/`merge_project_version_detailed`/`_rollback_merge` |
| versioning/restore.py | `restore_from_project_version`/`get_project_version_delete_impact`/`lock_project_version`/`unlock_project_version`/`lock_main_branch`/`unlock_main_branch` |

### 5.6 backup.py (1492) → projects/backup/

| 新模块 | 内容 |
|---|---|
| backup/io.py | zip 与序列化：`_add_tree_to_zip`/`_restore_tree`/`_add_vector_store_to_zip`/`_restore_vector_store`/`_serialize_value`/`_export_table`/`_export_table_to_file`/`_get_pk_columns`/`_get_fk_dependents_map`/`_ensure_backup_dir`/`_cleanup_stale_backup_tmp`/`get_backup_table_names` |
| backup/service.py | `create_backup`/`restore_backup`/`compute_restore_warnings`/`delete_backup_file` |
| backup/scheduler.py | `get_backup_settings`/`update_backup_settings`/`is_backup_due`/`cleanup_old_backups`/`start_backup_scheduler` |
| backup/factory_reset.py | `factory_reset` |

### 5.7 cross_project_dependency.py (1122) → projects/dependencies/

| 新模块 | 内容 |
|---|---|
| dependencies/repo.py | `create_project_dependency`/`get_project_dependencies`/`get_dependent_projects`/`get_dependency_by_id`/`update_project_dependency`/`delete_project_dependency`/`check_branch_exists` |
| dependencies/scan.py | `scan_dependency_relations`/`save_dependency_relations`/`get_dependency_relations` |
| dependencies/checks.py | `check_data_reachability`/`check_circular_dependency` |

## 6. import 硬切：AST codemod

工具：`tools/refactor/rewrite_utils_imports.py`（随重构入库，可审计、幂等可重跑）。
映射表内置于脚本（dict：旧模块名 → 新模块路径），每批追加。落盘统一走 pathlib
（Mimosa 钩子放行形态）；文件移动用 `git mv` 保留历史。

规则：

1. `from utils.X import a, b` → `from <新包>.X import a, b`（未拆分模块一一对应）
2. `from utils import X, Y` → 拆成多条 `from <新包X> import X`（utils/__init__ 为空，
   此形态的 X 必为模块）
3. 裸 `import utils` + `utils.X` 属性访问（全仓数百处，tests 占大头）：按文件解析
   实际引用的模块集合，改写为显式 `from <新包> import X` 并替换属性引用。
   唯一需要 AST 的规则。
4. 拆分文件：`from utils.batch_engine import X` → `from ai.batch.engine import X`
   （聚合导出，符号不变）。`importlib.reload(utils.page_config_relations)` 等属性
   形式同规则 3 一并落到新路径。

每批改写后机械验证三件套：

1. `python -m compileall server mcp-server` 零错误
2. `grep -rn 'utils\.' server/ mcp-server/ scripts/ --include='*.py'` 零残留
   （排除文档/日志/`.mimosa`）
3. 全量后端单测绿（见 §8 门禁）

## 7. 特殊耦合点处置

| 耦合点 | 处置 | 批次 |
|---|---|---|
| `routes/page_configs.py` `importlib.reload(utils.page_config_relations)` | 规则 3 落到 `data.page_config_relations`；智能写作/页面配置 E2E 覆盖该路径 | 1 |
| `app.py` 30+ 段 `spec_from_file_location` 迁移启动块 | 收敛为一张 `BOOT_MIGRATIONS` 有序列表 + 单循环执行；同一批迁移文件、同一执行顺序、行为等价 | 6 |
| 插件模板部署（`ensure_subagent_reuse_plugin`/`ensure_tool_timeout_plugin`） | 仅改 import 路径；批次内验证生成 .js 与改前 byte 级一致（模板正文内嵌于 py，不依赖 utils 目录路径） | 2 |
| `mcp-server/tools/_server_imports.py` | sys.path append 方案继续有效（新包仍在 server/ 下）；3 个工具文件的 import 由 codemod **随 data/ 批同步改写**（否则批 1~6 之间 mcp-server 断链）；docstring 更新放收尾批 | 1 / 6 |
| `server/scripts/`、`test_scripts/` 4 文件 | 同 codemod | 随所在批次 |
| pytest.ini / conftest / sys.path | 新包均在 server/ 下，rootdir 不变；批次内核实，预计零改动 | 0 |
| `proxy.py` / `init_db.py` | 仅 import 机械改写，不拆分 | 随所在批次 |

## 8. 分批提交计划与门禁

分支纪律：`feat/backend-restructure` 单分支；main 冻结；每批一个明确 commit；
门禁不过不进下一批。

| 批次 | 内容 | 约规模 |
|---|---|---|
| 0 | codemod 工具 + 新包骨架 + `common/` 15 文件（叶子为主，验证机制） | 小 |
| 1 | `data/` + `editor/` + reload 点修复 + mcp-server 工具 import 同步改写 | 19 文件 |
| 2 | `ai/opencode/` + `ai/skills/`（含插件模板 byte 级验证） | 14 文件 |
| 3 | `ai/sessions/` + `ai/gate/` + `ai/scan/` | 20 文件 |
| 4 | `ai/batch/`：engine/repo/ledger/perf 四拆 + 15 平铺文件 | 最险一批 |
| 5 | `ai/orchestration/` + `projects/`（versioning/backup/dependencies 三拆）+ `collab/` | ~25 文件 |
| 6 | app.py 迁移块收敛 + mcp-server docstring 更新 + 删 utils/ 空壳 + 终扫 | 收尾 |

**每批门禁**（全过才 commit）：

1. 机械三件套（§6）
2. 全量后端单测：`server/tests`（casemanage_test 独立测试库，串行 ~2500 例，
   ~160s）。跑前必须停 dev 栈，避免活栈用例互抢。
3. 后端真实启动冒烟（导入链全量走一遍）

**最终门禁**（合回 main 前）：

1. 数据管理 E2E 全量（L2 101 + L3 26）
2. 智能写作 usability E2E 套件
3. AI 套件抽样冒烟（批任务/普通会话各一轮 mock 链路）
4. 真机全栈启动 + 关键页面人工过一遍

## 9. 风险与对策

| # | 风险 | 对策 |
|---|---|---|
| 1 | 裸 `import utils` 属性改写错漏 | AST 解析 + compileall + 全量单测三重兜底；codemod 幂等可重跑 |
| 2 | mixin 拆类丢失装饰器/类属性/staticmethod | 方法逐字搬移不重写；搬移后 diff 审查只允许整块位移；单测兜底 |
| 3 | utils 内部隐蔽循环导入被搬移放大 | 搬移不改变相对依赖；compileall + 启动冒烟即暴露；若暴露按最小边原样保持 |
| 4 | 旧进程残留旧代码干扰验证 | 每批门禁前重启后端栈（405=旧代码教训） |
| 5 | 插件 .js 生成物漂移 | 改前改后 byte 级比对 |
| 6 | 并行会话在别的分支动后端 | main 冻结 + 本分支独占；合回前 rebase 复验门禁 |
| 7 | 改写脚本本身引入风险 | 脚本入库可审计；只改 import 行，diff 审查限定 import 区域 |

## 10. 回退策略

- 分支上逐批 commit：任一批暴露问题，`git revert` 该批即可回到上一批状态。
- main 全程未动，最坏情况弃分支零损失。
- 合回 main 后若发现遗漏：单点 hotfix（结构问题不影响行为，遗漏面只可能是
  个别 import 路径，启动/测试即暴露）。

## 11. 成功标准

1. `server/utils/` 目录消失；§4 映射全部落位；每个新包有 `__init__.py`
2. 全仓运行时代码 `utils.` 引用为零（文档/日志除外）
3. 全量后端单测绿 + 最终门禁四项全过
4. 行为零变化：无 API/日志/SQL/协议改动，diff 审查可证
5. main 仅在用户确认后接收一次合并
