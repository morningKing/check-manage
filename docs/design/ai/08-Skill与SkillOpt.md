# 08 · Skill 与 SkillOpt

> 上游：[00-总览与功能清单](./00-总览与功能清单.md) · [01-总体架构与运行时](./01-总体架构与运行时.md) · [02-对话助手](./02-对话助手.md) · [03-批任务与执行引擎](./03-批任务与执行引擎.md) · [05-动作门禁与审批](./05-动作门禁与审批.md) · [06-执行审计与轨迹分析](./06-执行审计与轨迹分析.md)
> 边界声明：Skill **调用采集**（baize-trace 插件上报 runtime/confirmed、收敛启发 heuristic/inferred、`ai_skill_invocations` 落库、事件分层保留）**归分册 06**（§4.5），本册只写其治理消费面（skill-analytics 聚合与版本 delta）与任务拟合层；`utils/skillopt.py` 的 `tool.execute.before` 门禁钩子与 `GATE_TIMEOUT_MS`/短路缓存等门禁常量**归分册 05**（05 册 §2.1）；OpenCode 全局目录、重启编排与「无 CRUD API/无热重载/重启生效」口径**以分册 01 §2.5/§4.4 为准**，本册不重复展开重启链；会话内技能 zip 上传端点与命令面板**归分册 02**，本册只写其共用校验契约（`skill_upload.py`）；AI 设置（`ai_settings`/`get_ai_settings`）**归分册 10**。行号以本 worktree 实际代码为准。

## 1. 模块职责

本册覆盖平台的**技能（Skill）资产体系与 SkillOpt 治理层**。技能在平台里存在三个层次：**会话技能**（单个会话工作区 `<workspace>/.opencode/skills/<name>/`，OpenCode 按 directory 发现）、**全局技能库**（平台中心存储 `<AI_WORKSPACE_ROOT>/global-skills/<name>/` + `global_skills` 元数据，管理员 zip 上传、自动注入每个新会话）、**运行时技能**（OpenCode 全局配置目录 `<OPENCODE_GLOBAL_DIR>/skills/<name>/`，serve 启动时加载、重启生效）。SkillOpt 在其上做两层治理：**规则层**把定义 frontmatter 声明的 `fit.steps` 步骤契约与 `agent_tool_calls` 账本的实际轨迹做贪心匹配，得出任务级拟合度（纯规则、无 LLM）；**AI 层**提供步骤生成/回写/试算预览/偏差诊断四个能力（LLM 只产出建议与草案，不触碰执行侧）。

按职责分五块：

| 职责 | 说明 | 证据 |
| --- | --- | --- |
| 全局技能库 | zip 上传校验入库、启停/描述编辑、删除（盘+库）、文件浏览/只读预览；内置技能随启动种子与刷新 | `server/routes/ai_skills.py`（144 行，7 端点）；`server/utils/global_skills.py`（553 行） |
| 运行时技能管理面 | OC 全局 skills/agents 文件 CRUD、zip 安装、平台技能发布（publish）、辅助文件在线编辑、重启/应用（重启链归 01） | `server/routes/ai_opencode_admin.py`（406 行）；`server/utils/opencode_global.py:321-455,533-743` |
| 会话技能安装 | 会话工作区 zip 安装（前端命令面板消费），校验契约被三处复用 | `server/utils/skill_upload.py`（132 行）；端点归 02（`ai_chat.py:1507-1524`） |
| 任务拟合规则层 | attempt 收敛钩子触发：定义发现 → 轨迹装载 → 贪心匹配 → 结果/版本落库；管理端幂等重算 | `server/utils/skill_fit.py`（375 行）；钩子 `chat_persist.py:708-711`、`batch_engine.py:3078,3098,3129,3249` |
| 拟合 AI 层与治理面 | generate/apply/preview/diagnose 四能力；聚合/版本 delta/定义清单/偏离模式/定义版本标注治理端点 | `server/utils/skill_fit_ai.py`（424 行）；`server/routes/ai_session_admin.py:661-1320`（skill 治理族） |

## 2. 模块组成

### 2.1 后端

| 组件 | 职责 | 证据 |
| --- | --- | --- |
| `routes/ai_skills.py` | 蓝图 `ai_skills`（前缀 `/ai/skills`，`ai_skills.py:29`），7 端点全 `require_permission('admin.ai_settings')`，写操作记 `log_operation`；注册 `app.py:513` | `ai_skills.py:46,53,62,93,112,126,137` |
| `utils/global_skills.py` | 全局技能盘上存储 + DB 元数据的唯一操作层：zip 校验/安装、CRUD、文件遍历/只读读取（zip-slip 防御）、OC 全局目录同步注入、内置技能种子 | `global_skills.py:21-27`（常量）、`:52-167`（校验安装）、`:293-425`（同步注入）、`:447-553`（种子刷新） |
| `routes/ai_opencode_admin.py` | 蓝图 `ai_opencode_admin`（前缀 `/ai/opencode`，`:36-37`）；权限按运行时治理规范七键分拆（模块头 `:9-22`）：read/skill_write/agent_write/runtime_publish/runtime_apply/runtime_restart/runtime_force；注册 `app.py:514` | `ai_opencode_admin.py:50-226`（overview/skills 族）、`:327-406`（runtime/apply/restart——机制归 01） |
| `utils/opencode_global.py` | OC 全局配置区唯一写层：`install_skill_zip_bytes` 复用 `skill_upload.extract_skill_zip` 契约（`:375-419`）、`publish_platform_skill` 平台技能复制（`:435-455`）、`write_skill`/`read_skill`/`merged_skills` 文件视图 ∪ serve 活视图（活视图口径归 01） | `opencode_global.py:321,336,375,435,533,743` |
| `utils/skill_upload.py` | 技能 zip 校验契约（错误码 `SkillUploadError`）：5 MiB / 200 条目 / SKILL.md frontmatter `name` / zip-slip 防御 / 单顶层目录剥离 / name 回退 zip 文件名；被会话上传与 OC 全局安装两处复用 | `skill_upload.py:19-22,25-38,56-132`；复用 `opencode_global.py:389` |
| `utils/skill_fit.py` | 拟合规则层（模块头 `:1-7`：**判定为纯规则（无 LLM）**）：frontmatter 解析（带 content_hash 缓存）、两段式贪心匹配、编排（定义发现→轨迹→匹配→upsert+版本注册） | `skill_fit.py:35-92`（解析）、`:114-195`（匹配）、`:284-352`（编排） |
| `utils/skill_fit_ai.py` | 拟合 AI 能力层：`_llm_json` 共用 AI 设置通道（`get_ai_settings`/`get_http_session`，失败 RuntimeError → 路由层 502）；generate/apply/diagnose/preview 四能力 | `skill_fit_ai.py:71-105`（通道）、`:126-144,149-201,324-383,388-424`（四能力） |
| `routes/ai_session_admin.py`（skill 治理族） | 双蓝图之一 `ai_execution_admin`（前缀 `/ai/chat/admin`，`:447-448`）承载 skill 治理族，全 `admin.ai_chat_admin`：调用聚合/版本 delta、建议反馈（采集归 06）、拟合结果族、定义版本、偏离模式、AI 步骤族、诊断 | `ai_session_admin.py:642-839,844-1320` |
| `utils/skillopt.py` | Skill 调用采集与分层保留——**机制归分册 06**；本册只引用其 `ai_skill_invocations` 作为聚合数据源 | `skillopt.py:36-87,130-231,399-416`；06 册 §4.5 |
| 迁移 | `global_skills` 主 DDL；`ai_skill_invocations`/`ai_suggestion_feedback`（P2）；`ai_skill_fit_results`/`ai_skill_def_versions`/`invocations.subtask_id`（拟合） | `server/db_schema/global_skills.py:3-13`；`server/migrations/2026_09_18_skillopt_p2.py:21-59`；`server/migrations/2026_10_01_skill_fit_tables.py:32-70` |

### 2.2 前端

| 组件 | 职责 | 证据 |
| --- | --- | --- |
| `views/admin/AiSkillManager.vue` | 「技能广场」：全局技能列表/上传 zip/描述编辑/启停开关/删除/文件浏览与只读预览；内嵌 MCP 服务卡片 | `AiSkillManager.vue:11-41,47-105` |
| `views/admin/AiSkillOpt.vue` | 「拟合优化」双 Tab：`fit` 任务拟合主从视图（左栏定义清单 + 概览/结果/版本/偏离三子 Tab）与 `invocations` 调用聚合（runtime 确认/启发推断分列、版本 Δ、建议效果） | `AiSkillOpt.vue:11-50,53-141` |
| `components/admin/skillopt/`（六组件） | `FitDefinitionList`（定义清单）、`DefOverview`（概览卡片）、`FitResultsPanel`（结果表+展开明细+重算/诊断）、`DefVersionTimeline`（版本时间线+标注编辑+Δ）、`DefPatternsPanel`（偏离模式表，含全局视图复用）、`StepGeneratorDialog`（生成/编辑/试算/回写对话框） | `src/components/admin/skillopt/`（53-343 行） |
| `components/admin/SkillFitBadge.vue` | 拟合徽章：perStep 圆点（hit 绿/miss 红/其余灰）+ 得分 + 状态标签（fit/partial/diverged/no_trace/parse_error 中文化） | `SkillFitBadge.vue:29-38,44-48` |
| `api/aiSkills.ts` | `/ai/skills` 与 `/ai/chat/admin` 两族的类型与请求封装（fit 族 11 个函数） | `aiSkills.ts:3,59,162-212` |
| 入口挂载 | 管理设置中心「AI 技能」项：`square`（AiSkillManager，`admin.ai_settings`）+ `fit`（AiSkillOpt，`admin.ai_chat_admin`）双 Tab；旧路径 `/admin/ai-skillopt` 别名重定向到 `/admin/ai-skills?tab=fit` | `src/views/admin/hub/settingsCatalog.ts:80-87,180`；壳 `hub/AiSkillHub.vue:1-6` |

## 3. 数据模型

**`global_skills`**（`server/db_schema/global_skills.py:3-13`）——全局技能库元数据，盘上目录 `<AI_WORKSPACE_ROOT>/global-skills/<name>/`（`global_skills_root`，`global_skills.py:30-31`）为内容本体：

| 列 | 类型/默认 | 用途 |
| --- | --- | --- |
| `id` | VARCHAR(100) PK | 上传安装 `gs-<uuid12>`（`global_skills.py:152`）；内置种子 `str(uuid.uuid4())`（`:547`） |
| `name` | VARCHAR(100) NOT NULL UNIQUE | 技能名，须匹配 `^[A-Za-z0-9_-]{1,64}$`（`SKILL_NAME_RE`，`:21`）；与盘上目录名一致 |
| `description` | TEXT DEFAULT '' | 上传表单或 SKILL.md frontmatter 提取（种子路径截 200 字符，`:520`） |
| `enabled` | BOOLEAN DEFAULT TRUE | 禁用后不再注入新会话，且同步时清理 OC 全局目录残留（`:357-371`） |
| `uploaded_by` | VARCHAR(100) REFERENCES users ON DELETE SET NULL | 上传者（种子行为 NULL）；列表 LEFT JOIN users 取 `uploader_name`（`:175-177`） |
| `file_size` | INT DEFAULT 0 | 安装时遍历目录累计字节数（`:143-146`） |
| `created_at` / `updated_at` | TIMESTAMPTZ | |

**`ai_skill_invocations`**（`server/migrations/2026_09_18_skillopt_p2.py:21-39`）——Skill 调用记录（`source: runtime|heuristic`、`evidence_level: confirmed|inferred`、`outcome`、`UNIQUE(attempt_id, skill_name, skill_hash)` 幂键；`subtask_id` 子代理归属列由拟合迁移补加 `2026_10_01_skill_fit_tables.py:67-70`）。**采集机制归分册 06**，本册把它作为 skill-analytics 聚合（§5.3）与版本 delta 的数据源。

**`ai_suggestion_feedback`**（`2026_09_18_skillopt_p2.py:46-59`）——建议接受/拒绝/应用五 action 与 `before_metrics`/`after_metrics` 快照；端点（feedback POST、effect 前后 7 天对比）已由 06 册登记（`ai_session_admin.py:701-727,730-773`），本册不重复展开。

**`ai_skill_fit_results`**（`2026_10_01_skill_fit_tables.py:32-52`）——attempt 级拟合结果：

| 列 | 类型/默认 | 用途 |
| --- | --- | --- |
| `id` | VARCHAR(100) PK | `fit_<hex12>`（`skill_fit.py:254`）；偏差诊断端点按它定位 |
| `attempt_id` | VARCHAR(100) FK → ai_execution_attempts CASCADE | 拟合维度（一 attempt 多定义多行）；明细端点按它取整组 |
| `session_id` / `def_kind` / `def_name` / `def_hash` | VARCHAR | 会话锚；定义种类（`skill|agent`）、名与内容 hash（来自 `ai_execution_manifests`） |
| `steps_total` / `steps_hit` / `score` | INTEGER | 分母只计 hit/miss/out_of_order（skipped 不计）；`score = round(hits/total*100)`（`skill_fit.py:181-183`） |
| `status` | VARCHAR(20) DEFAULT 'unknown' | `fit/reordered/partial/diverged/no_trace/parse_error`（口径见 §4.3） |
| `per_step` | JSONB DEFAULT '[]' | 逐步骤 `{id,name,status,evidence[{tool,args[:160],occurredAt}]}` |
| `diagnosis` | JSONB NULL | 偏差诊断 `{cause,suggestions[],revised_steps[]}`；重算覆盖时置 NULL（`skill_fit.py:270`） |
| `computed_at` | TIMESTAMPTZ | |

唯一索引 `uq_skill_fit_attempt_def (attempt_id, def_name)` 由运行时幂等 ensure（迁移建表未带该约束，upsert 的 `ON CONFLICT` 依赖它——`skill_fit.py:24-28,200-211`）。

**`ai_skill_def_versions`**（`2026_10_01_skill_fit_tables.py:55-64`）——定义版本实体：`UNIQUE (def_kind, def_name, content_hash)`；`version_label`/`note` 管理员可读化标注（PATCH 端点，`ai_session_admin.py:1123-1150`）；`first_seen_at` 只记首次（compute 遇新 hash upsert DO NOTHING，`skill_fit.py:236-248`）。

**定义 frontmatter 契约**（无表，文件的 YAML frontmatter）：

```yaml
---
name: my-skill            # SKILL.md 必备，目录名一致性（OC 侧强制，01 册 §4.4）
description: …            # OC 加载必需，缺失被静默过滤（opencode_global.py:17-19）
fit:                      # 可选：SkillOpt 步骤契约（skill/agent 定义均可声明）
  steps:
    - id: clone           # 短英文 slug
      name: 克隆仓库       # 中文短名
      expect:             # 期望工具调用数组（OR 语义，任一命中即该步 hit）
        - tool: bash
          args_pattern: "git clone\\s+\\S+"   # 匹配工具入参拼接文本
---
```

解析与形状校验在 `parse_fit_steps`（`skill_fit.py:35-69`）：无 frontmatter/无 `fit` 块/`steps` 空 → `[]`（不参与拟合）；`fit.steps` 非数组、元素非对象、`expect` 非数组 → `FitParseError`（落 `parse_error` 结果行）。

## 4. 核心流程

### 4.1 三层技能体系与发布链

```
仓库 skills/（内置技能唯一事实源）
      │ ensure_builtin_skills（app.py:504-512 启动种子）
      ▼
全局技能库  <AI_WORKSPACE_ROOT>/global-skills/<name>/  +  global_skills 行
（管理员 zip 上传 ai_skills.py；启停 global_skills.enabled）
      │ sync_platform_skills_to_oc_global（自动注入：新会话/批子会话）
      │ publish_platform_skill（管理员显式发布 ai_opencode_admin.py:151）
      ▼
运行时技能  <OPENCODE_GLOBAL_DIR>/skills/<name>/
      │ serve 重启加载（无热重载——分册 01 §4.4）
      ▼
OpenCode 运行时对会话可见（GET /skill?directory=…；命令面板 ai_chat.py:1698-1719）

会话技能（旁路，单会话作用域）：POST /ai/chat/sessions/:id/skills
      → extract_skill_zip → <workspace>/.opencode/skills/<name>/（归 02）
```

**自动注入链**（`sync_platform_skills_to_oc_global`，`global_skills.py:293-374`）：对每个 `enabled` 的平台技能，在 OC 全局 skills 目录建**符号链接**指向中心存储目录；已就位的链接（realpath 相等）幂等跳过，指向变化的旧链接重建；**Windows 无符号链接权限时复制兜底**，副本写 `.platform-synced` 标记文件——禁用清理只认这个标记，用户自装同名目录不受影响（`:27,335-337,345-354,357-371`）。历史实现往 workspace `.opencode/skills` 注入，但 workspace 一旦存在 `.opencode` 目录，OC 实例引导就会执行 `bun add @opencode-ai/plugin`，慢速网络下把 `create_session` 拖慢到 70s+（2026-09-28 实测：无 `.opencode` 建会话 0.98s，有则 74s+），故改挂 OC 全局 skills 目录——OC 对全局 skills 的发现与 workspace 注入等效（`:300-307` 设计注释）。`inject_global_skills` 保留原签名（`ai_chat.create_session` 与 `batch_engine._prepare_workspace` 两调用点无需改动，`workspace_path` 参数已不使用，`:377-394`）；调用点：交互会话 `ai_chat.py:153-157`（best-effort）、批子会话 `batch_engine.py:1406-1417`（注入结果落 provision 通知，失败不阻断子任务）。

**单技能强制注入**（`inject_single_skill`，`global_skills.py:397-425`）：轨迹分析会话专用——分析会话**不得全量注入**所有启用技能，且 trace-analyzer 缺失/注入失败必须 fail-fast 而非静默降级（`FileNotFoundError`/`OSError` 上抛）；调用点 `ai_session_admin.py:230,296`（机制归 06 §4.4）。

**内置技能种子与刷新**（`ensure_builtin_skills`，`global_skills.py:487-553`，启动接线 `app.py:504-512`）：仓库 `skills/`（当前 `data-ops`、`trace-analyzer` 两个）是内置技能唯一事实源——只插缺失的（按 name），盘上目录在而 DB 行缺时就地复用目录补建行；`_refresh_builtin`（`:447-484`）在仓库版本变化且安装副本未被改动时整体替换并更新 DB——「未被改动」用 `.builtin-hash` 标记判定（`_dir_hash` 跳过标记本身，`:428-444`），无标记的存量安装一律采纳刷新。部署拉代码重启即自带这批技能。

### 4.2 zip 上传与 SKILL.md frontmatter 契约

三条上传路径共用同一校验契约（`skill_upload.py` 定义，错误码 `SkillUploadError`）：

| 校验 | 规则 | 证据 |
| --- | --- | --- |
| 大小/条目 | ≤ 5 MiB、≤ 200 非目录条目 | `skill_upload.py:19-20,67-77`；平台侧 `global_skills.py:22-23,106` |
| SKILL.md 存在 | 根目录 `SKILL.md`，或**单顶层目录且其下有 SKILL.md**（自动剥离该前缀）；其余形状拒绝 `INVALID_SKILL_ZIP` | `skill_upload.py:80-92` |
| name 契约 | frontmatter `name:` 解析（`_parse_name_from_md`），缺失回退 zip 文件名去扩展名；须匹配 `[A-Za-z0-9_-]{1,64}` | `skill_upload.py:41-53,102-106` |
| zip-slip 防御 | `_safe_join` realpath 后必须落在基目录内，逃逸即 `SKILL_ZIP_UNSAFE` | `skill_upload.py:32-38` |
| zip 文件名解码 | 中文等非 UTF-8 条目名经 `decoded_zip_name` 归一（平台侧解包用 `safe_extract_decoded`） | `skill_upload.py:79,117`；`global_skills.py:131` |
| 原子落盘 | 先解到 `.tmp-<random>` 再 `os.rename` 成目标目录，失败清理 | `skill_upload.py:113-130`；`global_skills.py:126-149` |
| 同名冲突 | 会话内已存在 → `SKILL_EXISTS`；OC 全局 → `ALREADY_EXISTS` 409（`overwrite=true` 上传即替换）；平台库 → 盘目录与 DB 行双重查重 | `skill_upload.py:110-111`；`opencode_global.py:396-408`；`global_skills.py:116-124` |

差异点：**平台库安装**（`install_skill_from_zip`，`global_skills.py:100-167`）自建校验（`validate_skill_zip`，`:52-97`）+ DB 记录 + 大小统计；**OC 全局安装**（`install_skill_zip_bytes`，`opencode_global.py:375-419`）刻意**整体复用** `extract_skill_zip`——先解进一次性临时 workspace，再把产出的 `<tmp>/.opencode/skills/<name>` 移入全局目录，保证两处校验契约永不漂移（`:379-388` 注释）。zip 内 SKILL.md 仅 4096 字节窗口内找 frontmatter（`global_skills.py:34-49`；越界 frontmatter 解析不到 name 时回退 zip 文件名）。

### 4.3 拟合规则层：贪心顺序匹配（`skill_fit.py`）

**触发时机**（attempt 收敛钩子，best-effort——拟合失败仅记日志，绝不影响任务收敛）：交互回合收口 `chat_persist.py:708-711`（与 06 册的 `collect_skill_invocations` 挂在同一钩子）；批子会话终态四处 `batch_engine.py:3078`（completed）、`:3098`（failed）、`:3129`（cancelled）、`:3249`（needs_review——CAS 未命中 early-return 之前执行，避免漏掉 miss 路径）。入口 `compute_for_session`（`skill_fit.py:355-375`）现查该会话最新 attempt 后委托 `compute_attempt_fit`；管理端可对任意 attempt 幂等重算（§5.3 recompute）。

**编排 `compute_attempt_fit`**（`:284-352`）四步：

1. **定义发现**：`ai_execution_attempts` 取 attempt（无则返回空），`ai_execution_manifests` 取 `kind IN ('skill','agent')` 的清单行（一个读块取齐 DB 事实，文件 I/O 放事务外，`:299-314`）。
2. **轨迹装载 `_load_trace`**（`:214-233`）：`agent_tool_calls` 账本（05 册）里 `root_session_id = attempt.session_id` 的调用——账本落账自带 root+subtask 双标识，**天然含全部子代理**；时窗 `started_at ~ COALESCE(finished_at, NOW())`；**只取 `state='completed'`**——失败/在途调用不算「按定义执行」，否则会被贪心指针消耗污染后续匹配；按 `occurred_at ASC NULLS LAST, id ASC` 稳定时序。
3. **解析与匹配**：逐清单行按 `path` 读定义文件 `parse_cached`（按 `content_hash` 的 LRU 缓存 256 条——文件被清理后历史拟合的重算依据，`:72-92`）；**无 fit 块 → 不出结果行**；文件缺失且缓存未命中 → 不出结果行（设计 §8）；文件在但解析失败 → `parse_error` 结果行（score=0、per_step 空）；解析成功 → `match_steps(steps, trace)`。
4. **落库**：`_upsert_result` 按 `(attempt_id, def_name)` 冲突覆盖（结果以最后一次为准，`diagnosis` 一并置 NULL 失效，`:251-281`）；`_register_def_version` 对新 `(def_kind, def_name, content_hash)` upsert 定义版本（DO NOTHING，重复 compute 幂等，`:236-248`）。

**两段式匹配 `match_steps`**（`:114-195`，spec §10 非目标②转正）：

- **第一段——贪心顺序匹配**：单指针 `ptr` 顺序扫描轨迹；对每个步骤，指针推进逐条调用找**首个**满足「`tool` 相等 AND（无 `args_pattern` OR `args_text` ~ pattern）」的 expect（expect 数组 OR 语义，任一命中即该步 hit）；命中即记录证据（tool + args 截 160 字符 + occurredAt）并跳出。**`consumed` 集合**记录已作证据的轨迹位置——每条调用只能归一个步骤，防止「同 expect 连续步骤」场景下一条调用被第一步 hit 后又被第二步的重排扫描重复计入（`:128-131` 注释）；顺序 miss 时**未命中位置不消耗指针**（指针只前进不回退，留给后续步骤）。
- **第二段——重排对齐**：顺序 miss 的步骤，在其 expect 于轨迹**任意未消耗位置**出现时判 `out_of_order`（做了但次序不对，不计 hit），全程未出现才判 `miss`（`:159-180`）。
- **计分与状态口径**（`:181-195`）：分母只计 `hit/miss/out_of_order`（无 expect 的步骤 `skipped` 不计分母）；`score = round(hits/total*100)`；`status`：轨迹空 → `no_trace`；全 hit → `fit`；全做了但次序全错（`ooo == total && hits == 0`）→ `reordered`；一个没 hit → `diverged`；其余 → `partial`。
- **引擎对齐防御 `_regex_match`**（`:99-111`）：匹配引擎是 Python `re`；apply/preview 入口已校验 Python re 合法性，但存量定义/直写库里的模式仍可能 PG 合法而 Python 非法（如 PG 独有断言 `\y`）——编译失败按不匹配处理（同坏模式只 warning 一次防刷日志），绝不抛、不让 compute 中断整个 attempt。

### 4.4 拟合 AI 能力层（`skill_fit_ai.py`）

四个能力共用一条 AI 设置通道：`_llm_json`（`:71-105`）读 `get_ai_settings()`（未启用/未配 Key/连接失败/HTTP ≥400/回复格式异常/JSON 不可解析 → `RuntimeError`，路由层统一转 **502**），`temperature=0.1`、`max_tokens` 取配置下限 1024；`_extract_json_object`（`:54-68`）容忍 ```` ```json ```` 围栏与前后废话。红线同动作门禁提炼器（05 册）：**只产出建议/草案，本模块没有执行侧路径**（模块头 `:15-17`）。

| 能力 | 输入 → 输出 | 关键机制 | 证据 |
| --- | --- | --- | --- |
| `generate_steps` 生成 | 定义全文（截 8000 字符）→ `fit.steps` 草案 | system 提示词约束：最多 8 步、宁缺勿滥、不虚构定义里不存在的资源、无法用工具调用验证的语义要求不入步；形状校验（缺 id/expect 非数组）拒绝 | `:110-144` |
| `apply_steps` 回写 | steps → 定义文件 frontmatter（幂等覆盖） | **先校验后写盘**：每个 `args_pattern` 过 `validate_pg_regex`（PG `~` 口径，`agent_ledger.py:277`）**且** Python `re.compile` 双校验，任一失败 `ValueError` 且**不写文件**；重解析原 frontmatter 仅合并 `fit.steps` 保留其余字段与正文；`yaml.safe_dump` **width 拉满 100000**——长 pattern 被折叠换行会在重解析时折进空格破坏正则 | `:149-201` |
| `preview_steps` 试算 | steps + 历史 attemptId → 匹配预览 | **纯计算不写任何表**；steps 形状与 Python re 合法性前置校验（400）；attempt 不存在 404；装载轨迹同 §4.3 口径后直接复用 `match_steps` | `:388-424` |
| `diagnose_result` 诊断 | result_id → `{cause, suggestions[], revised_steps}` | 仅 `partial/diverged` 结果可诊断（400）；输入材料按 result_id 从库组装（结果行 + 清单定义全文 + `ai_execution_prompt_snapshots` 任务 prompt + 轨迹尾部 30 条，各材料缺失 best-effort 置空不整体失败）；`cause` 限定五类枚举（`definition_stale/step_redundant/order_deviation/model_noncompliance/environment`，非法值 502）；产出写 `ai_skill_fit_results.diagnosis` 列；**(result_id, def_hash, 轨迹签名)** 进程内 LRU 缓存 128——轨迹签名是 tool+args 序列聚合 hash（不含时间戳），重新拟合后缓存自然失效 | `:206-226,229-383` |

**路径 confinement**（`_path_in_allowed_roots`，`ai_session_admin.py:1207-1224`）：generate/apply 的 `path` realpath 归一后必须落在允许根（AI 工作区根 / 平台全局技能根，显式并列以容忍将来分体部署）内；`commonpath` 相等比较优于 startswith 前缀碰撞，跨盘符等无法共根的情形一律视为逃逸（400）。

### 4.5 治理消费面

- **调用聚合** `GET /skill-analytics`（`ai_session_admin.py:776-839`）：`ai_skill_invocations` 精确行 UNION ALL 「清单 × attempt」存量兜底行（无 invocations 记录的历史数据标 `source='heuristic'`）后按 (name, hash) 聚合，`runtime_confirmed`/`heuristic` **两列分开计数**——UI 永不把推断呈现为确证（06 册口径）；附 `ai_execution_step_results` 步骤统计 Top20。
- **版本 delta** `GET /skill-analytics/versions`（`:661-698`）：同一 skill 不同 `skill_hash` 的完成率对比，按调用数排序算相邻版本 `completion_rate_delta`（P2 效果追踪）。
- **拟合左栏清单** `GET /skill-fit/definition-summary`（`:909-983`）：清单 = `ai_skill_def_versions` ∪ `ai_skill_fit_results` 的 (def_kind, def_name) **并集**——从未跑过任务的新定义也出现在左栏，否则「生成步骤」入口不可达；指标 fitRate = fit 状态行占比（分母只计有拟合结果的任务）。
- **拟合结果族**：列表 `GET /skill-fit`（`?sessionId/defKind/defName` 过滤，limit ≤200，`:869-906`）；明细 `GET /skill-fit/<attempt_id>`（含 perStep/diagnosis，无结果行 404；**附带子代理层 `subagentFits`**——该会话名下 `source_type='subagent'` 且 `parent_attempt_id` 命中的 attempt 拟合按层附上，父层不合并子层数据、各层独立判定，`:986-1038`）；幂等重算 `POST /skill-fit/<attempt_id>/recompute`（委托 `compute_attempt_fit`，`:1041-1056`）；偏差诊断 `POST /skill-fit/<result_id>/diagnose`（§4.4，`:1302-1320`）。
- **定义版本**：时间线 `GET /skill-def-versions`（版本行 LEFT JOIN 拟合聚合，无拟合结果的版本 tasks=0 也出现在时间线；fitRate/partialCount/divergedCount 同口径，`:1059-1120`）；可读化标注 `PATCH /skill-def-versions/<version_id>`（versionLabel/note，显式 null 可清空，`:1123-1150`）。
- **步骤级偏离聚合** `GET /skill-def-patterns`（`:1153-1202`）：`per_step` JSONB `CROSS JOIN LATERAL jsonb_array_elements` 展开后按 (def_kind, def_name, step_id) 聚合 **partial/diverged 两态**拟合的步骤级 miss/out_of_order（`ai_session_admin.py:1179`）——「同一处定义缺陷在多少任务、哪些版本反复出现」的优先级排序；`versionHashes` 为出现过的版本短 hash 列表（去重截 5 个）。

## 5. 关键接口

### 5.1 全局技能库 `/ai/skills`（蓝图 `ai_skills`，全 `admin.ai_settings`）

| 方法/路径 | 行为 | 证据 |
| --- | --- | --- |
| GET `/ai/skills` | 全量列表（LEFT JOIN users 带上传者名，按 name 排序） | `ai_skills.py:46-50` |
| GET `/ai/skills/<id>` | 单条详情，404 兜底 | `ai_skills.py:53-59` |
| POST `/ai/skills` | zip 上传安装（multipart `file` + `description`）；临时文件转交 `install_skill_from_zip`；`ValueError` → 400；记操作日志 | `ai_skills.py:62-90` |
| PUT `/ai/skills/<id>` | 更新 description/enabled（部分更新，二者可缺省） | `ai_skills.py:93-109` |
| DELETE `/ai/skills/<id>` | 先查后删（DB 行 + 盘目录 `shutil.rmtree`） | `ai_skills.py:112-123`；`global_skills.py:226-241` |
| GET `/ai/skills/<id>/files` | 技能内文件清单（os.walk 相对路径，按 path 排序） | `ai_skills.py:126-134`；`global_skills.py:244-263` |
| GET `/ai/skills/<id>/files/<path>` | 文件只读预览：`..` 归一拒绝 + commonpath 包含性检查（zip-slip 防御）；256 KiB 截断标记；NUL 字节判二进制 | `ai_skills.py:137-144`；`global_skills.py:266-290` |

### 5.2 OpenCode 运行时 `/ai/opencode`（蓝图 `ai_opencode_admin`，权限七键分拆）

| 方法/路径 | 权限 | 行为 | 证据 |
| --- | --- | --- | --- |
| GET `/overview` | ai_runtime_read | 全局目录/serve 健康/待生效数/看门狗快照一屏 | `ai_opencode_admin.py:50-71` |
| GET `/skills`、GET `/skills/<name>` | ai_runtime_read | 文件视图 ∪ serve 活视图合并列表 / 单技能详情 | `:78-90`；`opencode_global.py:743,321` |
| POST `/skills` | ai_skill_write | zip 安装（`install_skill_zip_bytes`，overwrite 可选）或 JSON 新建（name+description+body） | `:93-120`；`opencode_global.py:375-419,336` |
| PUT/DELETE `/skills/<name>` | ai_skill_write | 全文/字段改写（changed 才记审计）与删除 | `:123-148` |
| **POST `/skills/publish`** | ai_runtime_publish | **平台技能发布**：body `{skillId, overwrite}` → 查 `global_skills` 行得 name → 把 `<AI_WORKSPACE_ROOT>/global-skills/<name>/` 复制进 OC 全局 skills 目录；缺 SKILL.md 400、frontmatter name 与目录名不一致 400、同名 409（overwrite 整体替换） | `:151-179`；`opencode_global.py:435-455` |
| GET/PUT/DELETE `/skills/<name>/files[/<path>]` | read / skill_write | 辅助文件（脚本/模板）在线编辑；读 256 KiB 上限 | `:184-226`；`opencode_global.py:533` |
| `/agents` 族、`/runtime`、`/runtime/apply`、`/restart` | 七键分拆 | agents 文件 CRUD/启停与重启生效链——**机制归分册 01**（§4.4 重启编排与生效语义） | `:233-406` |

### 5.3 SkillOpt 治理族 `/ai/chat/admin`（蓝图 `ai_execution_admin`，全 `admin.ai_chat_admin`）

| 方法/路径 | 用途 | 证据 |
| --- | --- | --- |
| GET `/skill-analytics` | 调用聚合 v2（runtime/heuristic 分列 + 清单兜底 UNION ALL） | `ai_session_admin.py:776-839` |
| GET `/skill-analytics/versions` | 按 hash 版本 delta | `:661-698` |
| GET `/suggestion-feedbacks`、POST `/analyses/<id>/suggestions/<sid>/feedback`、GET `/skill-suggestions/<sid>/effect` | 建议反馈与前后 7 天效果——**归分册 06**（06 册已登记） | `:642-658,701-727,730-773` |
| GET `/skill-fit` / `GET /skill-fit/definition-summary` / `GET /skill-fit/<attempt_id>` | 拟合列表 / 左栏定义清单 / 明细（含 subagentFits） | `:869-906,909-983,986-1038` |
| POST `/skill-fit/<attempt_id>/recompute` | 幂等重算（attempt 不存在 404） | `:1041-1056` |
| POST `/skill-fit/<result_id>/diagnose` | 偏差诊断（404/400/502 三态） | `:1302-1320` |
| GET `/skill-def-versions`、PATCH `/skill-def-versions/<id>` | 定义版本时间线 / 可读化标注 | `:1059-1120,1123-1150` |
| GET `/skill-def-patterns` | 步骤级偏离聚合（跨版本优先级排序） | `:1153-1202` |
| POST `/skill-def-steps/generate` / `apply` / `preview` | AI 步骤族：读定义交 LLM 生成（400 逃逸/404 读失败/502 AI 失败）；回写（先校验后写盘）；试算预览（纯计算不落库） | `:1227-1251,1254-1278,1281-1299` |

### 5.4 会话技能上传（归 02，本册登记契约）

POST `/ai/chat/sessions/<sid>/skills`（`ai_chat.py:1507-1524`，`write_required`）：`extract_skill_zip(sess[4], f)` 安装进 `<workspace>/.opencode/skills/<name>/`；`SkillUploadError` → 400 带 `code`（`BAD_FILE/SKILL_ZIP_TOO_LARGE/SKILL_ZIP_INVALID/SKILL_ZIP_TOO_MANY_FILES/INVALID_SKILL_ZIP/INVALID_SKILL_NAME/SKILL_ZIP_UNSAFE/SKILL_EXISTS`，`skill_upload.py:25-29,62-111`）。安装后 OpenCode 按 directory 发现，命令面板经 GET `/sessions/<sid>/commands` 带出（`ai_chat.py:1698-1719`）。

## 6. 依赖与协作关系

- **被谁消费**：交互会话与批子会话（自动注入启用的全局技能，§4.1）；轨迹分析会话（单技能强制注入，机制归 06）；聊天命令面板（会话技能 + 运行时技能经 OC `GET /skill` 带出，归 02）；MCP `ai_create_data_page` 等数据工具链依赖 `data-ops` 内置技能的提示词指引（工具本体归 02）；管理前端（技能广场/拟合优化双 Tab）。
- **依赖上游**：`agent_tool_calls` 账本（05 册）——拟合轨迹的唯一事实源；`ai_execution_attempts/manifests/prompt_snapshots`（06 册）——attempt 时窗、定义发现与诊断材料；`ai_skill_invocations`（06 册采集）——聚合数据源；`ai_settings`/`get_http_session`（10 册）——AI 能力通道；`utils/zip_unicode`——zip 条目名解码；`utils/operation_log`——全部写操作留痕。
- **平级协作**：`agent_ledger.validate_pg_regex`（05 册）——apply 的 PG 口径把关；`opencode_launch.global_dir`——注入目标目录解析；`oc_watchdog`/`opencode_ownership`（01 册）——重启编排供方。
- **数据流闭环**（采集 → 治理 → 拟合 → 回写）：插件/启发采集（06 册）落 `ai_skill_invocations` → 治理面聚合成 skill-analytics/版本 delta；同一收敛钩子触发拟合 → `ai_skill_fit_results`/`ai_skill_def_versions` → 管理员查看偏离、AI 诊断出 `revised_steps` → 人工编辑确认后 apply 回写定义文件 → 下一版本 hash 出现在版本时间线，拟合指标随之对比——闭环的每一步落地都是显式人工动作。

## 7. 设计决策

1. **三层技能体系，三种作用域互不混写**：会话技能（工作区内、随工作区回收）、全局技能库（平台中心存储 + DB 元数据、管理员治理）、运行时技能（OC 全局配置目录、serve 启动加载）。三层各有独立管理面（ai_chat / ai_skills / ai_opencode_admin），只有一个单向受控出口（publish / 自动同步），不存在反向回流。
2. **发布链不建镜像表**：OC 全局目录文件是运行时技能唯一事实源，不做镜像 DB（「a mirror would only add a drift risk」，`ai_opencode_admin.py:1-7`）——与 01 册「无 CRUD API/无热重载/重启生效」口径一致；平台技能 publish 是**复制**不是引用，发布后两侧内容各自演化。
3. **自动注入改挂 OC 全局目录**（§4.1）：旧实现往 workspace 写 `.opencode` 触发 OC 引导的 npm 安装，把建会话拖慢 70s+——性能事实驱动存储位置迁移；保留 `inject_global_skills` 原签名使两个调用点零改动。
4. **内置技能以仓库为唯一事实源**：`skills/` 目录 + 启动种子 + `.builtin-hash` 保护管理员改动（改过的安装副本不刷新，仓库与安装一致时幂等跳过）——部署即交付技能，又允许运维就地定制。
5. **拟合结果仅为证据，不作强约束**：判定纯规则无 LLM（`skill_fit.py:6`）；score/status 只进治理视图（`ai_skill_fit_results`），**没有任何执行侧消费**——不会因拟合度低阻断/重试任务；AI 层红线同提炼器：generate/diagnose 只产出草案与建议，apply 回写是管理员显式动作且先校验后写盘。这是本册与 05 册门禁（执行侧强约束）的本质分野。
6. **两段式匹配 + consumed 防重复计入**（§4.3）：顺序 miss 先查任意位置重排（`out_of_order`）再判缺失，避免把「做了但次序不对」误报成 miss；`consumed` 集合保证一条调用只归一个步骤。轨迹只认 `state='completed'`，与 05 册动作门禁 `require_state='completed'` 惯例一致。
7. **args_pattern 双正则口径**：写入口径 PG `~`（`validate_pg_regex`）与匹配引擎 Python `re` 并存是历史现状——apply/preview 双校验把住增量，存量坏模式在匹配时按 miss 降级处理（warning 去重），宁可少算不炸 compute。
8. **降级分级**：无 fit 块/文件缺失 → 不出结果行（不是 0 分）；解析失败 → `parse_error` 行留痕；轨迹空 → `no_trace`——三种「没跑成拟合」在 status 上可分辨，UI 徽章中性呈现（`SkillFitBadge.vue:35-37`）。
9. **`(attempt_id, def_name)` 唯一索引运行时 ensure**（`skill_fit.py:24-28,200-211`）：迁移建表未带该约束而 upsert 依赖之，模块级 once 标志幂等补建（失败不置位下次重试）——兼容存量库免停机迁移。
10. **AI 能力共用 AI 设置通道、失败 502**（`skill_fit_ai.py:3-5`）：与 action_check_extractor 同一条 `get_ai_settings`/`get_http_session` 通道与错误翻译约定，管理端不单独配模型；诊断按 (result_id, def_hash, 轨迹签名) 进程内缓存，同签名重复点击不再产生 LLM 成本。
11. **与旧文档的关系**：`SkillOpt任务拟合设计.md`、`SkillOpt任务拟合页面重设计.md`（2026-10-01）所述机制已按代码吸收进本册 §4.3-§4.5；冲突处以代码与本册为准。前端「页面重设计」落为主从双栏 + 三子 Tab（`AiSkillOpt.vue:20-48`），与旧文档版式描述一致。
12. **观测项**（沿用 06 册口径）：`skill-fit` 明细的 `subagentFits` 依赖 `source_type='subagent'` 的 attempt，而该枚举当前无 `create_attempt` 写入路径（06 册 §7 观测项 10）——查询是前瞻预留；仓库内置技能（`skills/data-ops`、`skills/trace-analyzer`）frontmatter 均无 `fit` 块，拟合体系对内置技能暂无步骤契约，步骤契约的编写属内容运营项而非代码缺口。

## 代码索引

**后端路由**

- `server/routes/ai_skills.py`（144 行）——蓝图 `:29`；列表 `:46`、详情 `:53`、上传 `:62`、更新 `:93`、删除 `:112`、文件清单 `:126`、文件读取 `:137`
- `server/routes/ai_opencode_admin.py`（406 行）——权限七键 `:9-22`；overview `:50`；skills 列表/详情 `:78,84`、创建 `:93`、更新 `:123`、删除 `:140`、**publish `:151`**；辅助文件 `:184-226`；agents 族 `:233-320`；runtime/apply/restart `:327-406`（归 01）
- `server/routes/ai_session_admin.py`（1320 行，skill 治理族）——`list_suggestion_feedbacks :642`、`skill_analytics_versions :661`、`suggestion_feedback :701`、`suggestion_effect :730`、`skill_analytics :776`、`_fit_camel :844`、`list_skill_fits :869`、`skill_fit_definition_summary :909`、`skill_fit_detail :986`、`skill_fit_recompute :1041`、`list_skill_def_versions :1059`、`patch_skill_def_version :1123`、`skill_def_patterns :1153`、`_path_in_allowed_roots :1207`、`skill_def_steps_generate :1227`、`skill_def_steps_apply :1254`、`skill_def_steps_preview :1281`、`skill_fit_diagnose :1302`
- `server/routes/ai_chat.py`——全局技能注入 `:153-157`；会话技能上传 `:1507-1524`；命令面板 commands+skills `:1698-1719`（三处机制归 02）

**后端工具**

- `server/utils/global_skills.py`（553 行）——常量 `:21-27`；`global_skills_root :30`；`validate_skill_zip :52`；`install_skill_from_zip :100`；CRUD `:170-241`；文件清单/读取 `:244-290`；`sync_platform_skills_to_oc_global :293`；`inject_global_skills :377`；`inject_single_skill :397`；`_dir_hash :431`；`_refresh_builtin :447`；`ensure_builtin_skills :487`
- `server/utils/skill_upload.py`（132 行）——常量 `:19-22`；`SkillUploadError :25`；`_safe_join :32`；`_parse_name_from_md :41`；`extract_skill_zip :56`
- `server/utils/skill_fit.py`（375 行）——解析缓存 `:21-28`；`parse_fit_steps :35`；`parse_cached :72`；`_regex_match :99`；`match_steps :114`；`_ensure_uq_index :200`；`_load_trace :214`；`_register_def_version :236`；`_upsert_result :251`；`compute_attempt_fit :284`；`compute_for_session :355`
- `server/utils/skill_fit_ai.py`（424 行）——常量与缓存 `:37-49`；`_extract_json_object :54`；`_llm_json :71`；`_GENERATE_SYSTEM :110`；`generate_steps :126`；`apply_steps :149`；`_DIAGNOSE_SYSTEM :206`；`_load_definition_text :275`；`_load_task_prompt :297`；`_normalize_diagnosis :309`；`diagnose_result :324`；`preview_steps :388`
- `server/utils/opencode_global.py`（1057 行）——`read_skill :321`、`write_skill :336`、`install_skill_zip_bytes :375`、`publish_platform_skill :435`、`write_skill_file :533`、`merged_skills :743`（活视图/重启归 01）
- `server/utils/skillopt.py`（416 行）——采集/保留机制归 06（06 册代码索引）；本册仅消费其表
- `server/utils/agent_ledger.py:277`——`validate_pg_regex`（apply 的 PG 口径把关，机制归 05）

**启动接线**

- `server/app.py:504-512` 内置技能种子；`:513-514` ai_skills/ai_opencode_admin 蓝图注册；`:129-130` ai_session_admin 双蓝图注册

**数据模型**

- `server/db_schema/global_skills.py:3-13`——`global_skills` DDL
- `server/migrations/2026_09_18_skillopt_p2.py:21-39,46-59`——`ai_skill_invocations`/`ai_suggestion_feedback`
- `server/migrations/2026_10_01_skill_fit_tables.py:32-52,55-64,67-70`——`ai_skill_fit_results`/`ai_skill_def_versions`/`invocations.subtask_id`
- `server/utils/skill_fit.py:26-27`——`uq_skill_fit_attempt_def` 运行时 ensure

**前端**

- `src/views/admin/AiSkillManager.vue`（333 行）——列表/上传/编辑/启停/删除/文件预览
- `src/views/admin/AiSkillOpt.vue`（327 行）——fit 主从视图 `:11-50`、invocations 聚合 `:53-141`、全局偏离对话框 `:145-148`
- `src/components/admin/skillopt/`——`FitDefinitionList.vue`（67）、`DefOverview.vue`（56）、`FitResultsPanel.vue`（343：明细展开/重算/诊断/子代理层）、`DefVersionTimeline.vue`（152：标注编辑/Δ）、`DefPatternsPanel.vue`（53：全局视图复用）、`StepGeneratorDialog.vue`（228：生成/试算/回写）
- `src/components/admin/SkillFitBadge.vue`（61 行）——`STATUS_META :29-38`
- `src/api/aiSkills.ts`（212 行）——`/ai/skills` 族 `:22-55`；fit 族 `:57-212`
- `src/views/admin/hub/settingsCatalog.ts:80-87,180`——「AI 技能」入口与 `ai-skillopt` 别名；`src/views/admin/hub/AiSkillHub.vue`——SettingsTabShell 壳

**内置技能内容**（拟合契约的内容侧，非代码）

- `skills/data-ops/SKILL.md`、`skills/trace-analyzer/SKILL.md`——frontmatter 仅 name/description，均无 `fit` 块（见 §7-12）
