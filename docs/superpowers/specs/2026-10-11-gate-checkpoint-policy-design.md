# 动作门禁 · 过程检查（checkpoint）与门禁策略库 — 设计

日期：2026-10-11 ｜ 状态：已评审（brainstorming 定稿，待出实施计划）
集成位置：动作门禁（分册 `docs/design/ai/05-动作门禁与审批.md`）· 批任务创建/编辑 · 管理页 AI 能力组

## 1. 背景与目标

动作门禁现有两种时机：**pre 阻断**（PreToolUse deny list，"不允许做的别做"）与
**post 终态核对**（账本/效果断言 + verifier 判官，"该做的做了没"）。两者都回答不了
**"做的时机对不对 / 做得好不好"**——检查与纠偏只能等回合结束，模型蒙混过关要等
gate_retry 修正轮才被拉回。

本设计补上第三种时机：**过程检查（checkpoint）**——监控某个动作（如 write 某文件），
命中时在**执行前/执行后**把检查提示词注入给模型（如"写之前确认已读知识库"），
纠偏环路从"回合级"缩短到"动作级"。已与需求方对齐的三个关键决策：

1. **命中即检查，检查次数可配**——同一动作会重复发生（写脚本→改脚本），每条规则
   带注入预算 `max_checks`，预算内每次命中都注入，耗尽后放行不再注入（防冗余）；
2. **前后都要**——before（执行前拦截注入）与 after（执行后立即检查纠偏）都做；
3. **过程 + 终态双保险**——checkpoint 规则可带终态断言，模型无视提示词蒙混过关时
   终态门禁照样 fail，走既有 gate_retry 修正轮。

同时解决配置复用问题：门禁规则目前只活在批定义/模板里，每个任务都要重配。
新增**门禁策略库**（gate policy）——命名的规则集（deny/post/checkpoint 混装），
批任务引用即生效，一次配置处处复用。

## 2. 非目标

- 不做全局默认策略自动挂载——违背"门禁只约束登记过的事项"，隐式给未配门禁的批
  上门禁会误伤既有任务；引用必须显式；
- 不做策略版本快照——期望行（`action_expectations`）本身就是登记时刻的规则快照，
  策略后改不重写已登记任务（含终态批），天然免版本化；
- `assert_spec` 一期只做**账本型**断言（tool+args_pattern+min_count）；文件内容级
  断言（如"包含结论段"）需扩 effect_spec 内容原语，登记为后续增强；
- 入口 C（MCP `register_action_check`）与入口 E（AI 提炼）一期不扩展 checkpoint
  能力——保持"模型不能增删干预规则"的红线面不扩大；
- after 侧为**提示词注入**（prompt 必填），不做机器可验的效果断言——文件内容级
  原语（如"包含结论段"）需扩 effect_spec，登记为后续增强；after 的机器校验
  缺位由终态 assert 兜底补齐。

## 3. 数据模型与迁移

### 3.1 `action_expectations` 增列（checkpoint 行承载）

`mode` 列现为 `VARCHAR(10)` 无 CHECK 约束（`2026_09_26_action_gate_mode.py`），
新增取值 **`'checkpoint'`**（恰好 10 字符，无需改列宽）。checkpoint 行新增列：

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `prompt` | TEXT NULL | 检查提示词（注入给模型的内容）。checkpoint 行必填，1..2000 字（对齐 verifier rubric 上限口径） |
| `timing` | VARCHAR(8) NOT NULL DEFAULT 'before' | `before` / `after`。一条规则一个时机；前后两种检查各配一条规则 |
| `max_checks` | INT NOT NULL DEFAULT 1 | 注入预算，1..20。同一规则同一登记周期内最多注入 N 次 |
| `fired_count` | INT NOT NULL DEFAULT 0 | 已注入次数（原子累加；重新登记时重置 0，见 §6.3） |
| `assert_spec` | JSONB NULL | 终态兜底断言：`{"tool": str, "args_pattern": str(PG), "min_count": int≥1}`。NULL = 纯过程提醒，终态跳过 |

迁移 `2026_10_11_gate_checkpoint_policy.py`：幂等 `ADD COLUMN IF NOT EXISTS` 五列；
存量行无需回填（mode 仍 'post'/'pre'，新列只在 checkpoint 行上有语义）。
`(scope_id, mode)` 窄索引已覆盖 checkpoint 热路径（pre-check/post-check 均按
`scope_id + mode` 探测），无需新索引。

### 3.2 `gate_policies`（门禁策略库）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT PK | `gpol_` + 随机后缀（平台 id 惯例） |
| `name` | TEXT NOT NULL UNIQUE | 全局唯一（一期不做项目命名空间） |
| `description` | TEXT | 用途说明 |
| `checks` | JSONB NOT NULL | 规则集，deny / post / checkpoint 三种 mode 混装，整体过 `validate_checks`（不带病入库） |
| `enabled` | BOOLEAN NOT NULL DEFAULT TRUE | 停用开关：停用后对**后续登记**不生效，已登记期望不受影响 |
| `created_by` / `created_at` / `updated_at` | | 审计 |

### 3.3 `ai_chat_batches.gate_policy_id`

nullable TEXT 列，应用层校验（不加 FK RESTRICT——终态批的历史引用允许随策略删除，
期望行快照已生效）。`prompt_templates` 一期不加列（模板继续携带自带 checks，
不与策略混用——避免双源合并的口径膨胀）。

## 4. 判定流

### 4.1 before 侧（扩展现有 `pre_check_tool`，插件零改动）

`pre_check_tool(scope_id, tool, args_text)`（`agent_ledger.py:529`）判定顺序：

1. **deny 优先**：`mode='pre'` 命中（tool 相等 + `re.search(args_pattern)`）→
   `{allow: false, reason: 门禁规则「name」拦截…}`（现行为不变，优先级最高）；
2. **checkpoint 注入**：`mode='checkpoint' AND timing='before'` 且 tool 相等 +
   args_pattern 命中的行中，按登记序（`created_at, id`）取第一条**有预算**的，
   原子消耗预算：

   ```sql
   UPDATE action_expectations SET fired_count = fired_count + 1
   WHERE id = %s AND fired_count < max_checks
   RETURNING id
   ```

   行锁保证并发工具调用的计数安全。消耗成功 →
   `{allow: false, reason: prompt, rules: n}`——插件抛错、模型看到检查提示词、
   合规后重试；无行返回（预算耗尽）→ 该规则跳过，尝试下一条；全部无预算 →
   `{allow: true}` 放行不弹。

- **agent 定向注入**：checkpoint 行带 `subagents` 名单时，注入按**动作归属
  agent** 过滤——后端反查动作所在会话的 agent 名（`ai_chat_subtasks.agent`，
  根会话自身动作为 NULL），名单内的子代理动作才注入，名单外（含根会话自身）
  不注入、不消耗预算；无名单的行保持树级全量。agent 名**按需反查**（仅当候选
  行中存在带名单者才查，热路径无名单时零额外查询）。名单口径与终态 assert
  一致（§6.1：名单外不计入）——注入与断言同一张名单说话；
- `rules` 返回值语义扩展：deny 行 + checkpoint 行的匹配总数——保持插件
  `rules=0` 短路缓存语义正确（只有 checkpoint 规则时也不被缓存跳过）；
- fail-open 三层原样保留（插件超时/非 2xx 放行、服务端查询异常放行留痕、缺参放行）；
- 树作用域沿用：期望登记在根作用域，`_resolve_scope_id` 把子代理 OC 会话归属
  根会话，子代理动作同样受检。

### 4.2 after 侧（新端点 `POST /ai/gate/internal/post-check`）

- 入参/鉴权/鉴权失败语义与 `/pre-check` 同形：`{sessionId, tool, argsText}` +
  `X-Internal-Token`；scope 映射复用 `_resolve_scope_id`；
- 判定 `post_check_tool(scope_id, tool, args_text)`（新，`agent_ledger.py`）：
  `mode='checkpoint' AND timing='after'` 命中（tool + args_pattern）→ 同款原子
  预算消耗 → `{inject: true, prompt, rules: n}`；预算耗尽/无规则 →
  `{inject: false, rules: n}`；`subagents` 名单的 agent 定向注入语义与 before
  侧一致（§4.1：名单内子代理动作才注入，名单外含根会话不消耗预算）；
- fail-open：服务端查询异常 → `{inject: false}` + 留痕；插件侧不可达/超时/非 2xx
  → 不注入（动作照常完成，终态 assert 兜底）；
- `rules=0` 短路缓存 30s（插件侧，与 pre-check 同款——无规则是常态，热路径零开销）。

### 4.3 插件改动（`baize-trace.js`，`skillopt.py` PLUGIN_JS）

- **before 钩子：零改动**——check 注入复用 deny 的抛错通道（reason 内容变成
  检查提示词而已），插件不感知规则种类；
- **after 钩子（新增）**：每次工具调用完成后 POST `/post-check`（3s 超时、
  `rules=0` 30s 缓存、失败静默放行），`inject=true` 时向模型注入 prompt。
  注入机制由**探针**定夺（§5）；
- 插件文件变化 → 需重启 OC serve（既有口径，部署注意见 §10）。

## 5. after 注入方式探针（实施第一步，spike）

`tool.execute.after` 在本项目已有两个生产插件使用（baize-subagent-reuse 解析输出、
baize-tool-timeout 看门狗），但**注入语义未证**。探针用最小插件/临时分支测三件事：

| # | 实验 | 判定 |
| --- | --- | --- |
| T1 | after 钩子内 `throw` → 模型看到的工具结果是错误还是原结果 | 若结果变为错误 → 可用（与 deny 同款强语义：动作已执行但模型被要求立即重做） |
| T2 | after 钩子内改写 `output.result`（尾部追加提示词）→ 模型是否看到追加内容 | 若生效 → 可用（非阻断式：结果 + 检查提示同时呈现，体验更好） |
| T3 | T1/T2 均不成立 | 退化路径：`timing='after'` 的规则降级在 **before 时机**注入（`pre_check_tool` 同时探测 after 行，env 开关 `AI_GATE_AFTER_DEGRADE=1` 控制），时机略早但纠偏能力保留 |

探针结论写入实现计划第一步的产出，插件按结论实现其一。**探针代码为一次性验证，
不入库**（临时插件文件 + 手工会话验证）。

## 6. 终态兜底（双保险）

### 6.1 核对

`check_session_gate`（`agent_ledger.py:646`）对 checkpoint 行：

- 带 `assert_spec`：按账本断言核对——完全复用 tool 型计数路径（tree/session 作用域、
  `require_state='completed'`、`subagents` 定向 JOIN、PG `~` 正则口径），命中 ≥
  `min_count` 即 passed；`subagents` 名单口径与注入侧一致（§4.1——同一张名单
  约束两种时机：名单外子代理不注入、不计入断言）；结果回写期望行
  `last_status/last_checked_at/last_evidence`
  （与 post 行同址，审计口径一致）；
- 无 `assert_spec`：跳过（纯过程提醒，不参与门禁判定）；
- `fired_count` 不参与对错判定——预算只管注入频率。

### 6.2 与 gate_retry 的联动（零新机制）

模型无视提示词蒙混过关 → 终态 assert fail → 门禁 failed → 既有 gate_retry
（批级开关预算内）continue 续跑、注入带缺失明细的修正提示（`gate_failure_message`
对 checkpoint 行渲染：`名(需 assert描述, 实际命中 x/y)`）→ 原 agent 补齐 → 复核。
修正轮派发时期望重新登记 → `fired_count` 重置 → 修正期过程检查照常生效。

### 6.3 预算重置语义

`register_session_expectations`（幂等覆盖登记）与 `sync_batch_expectations`
（编辑后非终态替换）在写入 checkpoint 行时把 `fired_count` 重置 0——与核对状态
重置 pending 同址同事务。跨回合（同一子任务多轮）预算**不**按轮重置：只有
重新登记（派发/重试/编辑同步）才重置，避免模型用"多触发几轮"耗干预算语义。

## 7. 登记入口与校验扩展

### 7.1 `validate_checks` 扩展（`agent_ledger.py:292`）

checkpoint 规则条目形状：

```json
{
  "name": "写报告前读知识库",
  "mode": "checkpoint",
  "check_type": "tool",
  "tool": "write",
  "args_pattern": "outputs/report",
  "timing": "before",
  "prompt": "写入报告前必须已读取知识库 docs/kb/ 并确认要点已纳入，未读请先读再重试。",
  "max_checks": 2,
  "assert_spec": {"tool": "read", "args_pattern": "docs/kb/", "min_count": 1}
}
```

校验规则：`mode='checkpoint'` → check_type 必须 `'tool'`；`prompt` 必填 1..2000
（before/after 一视同仁）；`timing ∈ {before, after}` 默认 before；`max_checks`
1..20 默认 1；`assert_spec` 可选，结构 = tool 型字段（tool ≤50 字、args_pattern 过
`re.compile` + `validate_pg_regex` 双口径、min_count ≥1）；`subagents` / `apply_to`
对 checkpoint 行同样有效——`apply_to` 在登记层过滤子任务，`subagents` 在
**注入与终态断言**两处按 agent 名定向（同一张名单两种时机，见 §4.1/§6.1）。

### 7.2 入口矩阵

| 入口 | checkpoint 支持 | 说明 |
| --- | --- | --- |
| A 批创建/编辑 `action_checks` | ✅ | JSONB 承载列无 schema 变化，校验后落列 |
| B 模板携带 | ✅ | 同上，派发回退语义不变 |
| C MCP `register_action_check` | ❌ 一期不扩 | schema 只暴露 post tool 型（既有红线面不扩大） |
| D 管理员补挂 attach | ✅ | 走 `validate_checks` 自动支持 |
| E AI 提炼 extract | ❌ 一期不扩 | 提炼只出 post 建议；checkpoint 靠人配（提示词是业务语义，提炼价值存疑） |

## 8. 门禁策略库（配置复用）

### 8.1 端点（`server/routes/ai_gate_policies.py` 新建，前缀 `/ai/chat/gate-policies`）

| 端点 | 鉴权 | 语义 |
| --- | --- | --- |
| `GET /` | login | 策略列表（`?enabled=true` 过滤；返回全量字段含 checks——策略规模小，不做概要投影） |
| `POST /` | admin | 创建：name/description/checks/enabled；checks 过 `validate_checks`（400 不带病入库） |
| `GET /<id>` | login | 详情（编辑回填） |
| `PATCH /<id>` | admin | 更新：name/description/enabled 常规更新；`checks` **显式传入才更新**（哨兵语义，与批编辑一致） |
| `DELETE /<id>` | admin | 存在**非终态**批引用（`ai_chat_batches.gate_policy_id` 且子任务 status 非终态）→ 409 提示先解绑/等终态；仅终态引用或无引用 → 物理删除（期望行快照已生效，历史不受影响） |

写路径走 `log_operation` 留痕。`gate_policy_repo.py`（新）承载 CRUD 与引用计数。

### 8.2 批引用与合并（改 `_fetch_action_checks`，`batch_engine.py:1740`）

- 批创建/编辑 body 增 `gatePolicyId`（nullable）：创建/编辑时校验存在且
  enabled（失效引用 400）；编辑显式传 `null` 解绑（哨兵语义：不传不动）；
- 派发登记时生效规则 = **策略 checks ∪ 批自身 action_checks**（批自身缺省时
  回退模板，语义不变）；登记顺序：先策略后批侧——同名规则批侧覆盖策略侧
  （`register_session_expectations` 按 name 幂等覆盖，天然实现"更具体者赢"）；
- 策略 `enabled=false`：登记时跳过该策略并留日志；
- 编辑批（换策略/解绑）→ `sync_batch_expectations` 把非终态子任务期望整体
  替换为新的合并集（旧策略规则随之移除，终态批不重写）；
- Open API / 定时扫描建批走同一 `create_batch`，自动支持。

### 8.3 交互会话

一期交互会话不接策略库（交互的期望入口只有 MCP 登记/管理员补挂，痛点在批任务
侧）；管理员补挂（入口 D）已可对交互会话手工挂 checkpoint 规则。

## 9. 前端

| 位置 | 改动 |
| --- | --- |
| 管理页 · AI 能力组 | 新增「门禁策略」入口（settingsCatalog + 路由）：策略列表（name/描述/启用/规则计数/引用批数）+ 编辑器 |
| 策略编辑器 | 复用批对话框门禁卡片规则行组件，新增 checkpoint 行形态：触发工具/args 正则/时机(before/after)/检查提示词/预算/终态断言折叠区 |
| `CreateBatchDialog` / `EditBatchConfigDialog` | 门禁区加「引用策略」下拉（登录用户可读策略列表；可清空解绑）+ 自定义 checks 区不变；checkpoint 规则行同组件复用 |
| `src/api/gatePolicies.ts`（新） | CRUD 封装 |

## 10. 部署注意

- 迁移 `2026_10_11_gate_checkpoint_policy.py` 必须**双注册**（`migrations/` 目录 +
  `app.py` 迁移钩子显式块——生产重启路径只走 app.py 块，既有陷阱）；
- 插件 `PLUGIN_JS` 变化 → 部署后需**重启 OC serve** 才加载新钩子；
- 探针若走 `AI_GATE_AFTER_DEGRADE` 兜底开关，env 需随部署说明同步。

## 11. 测试计划

**单测**（`server/tests/`）：

- `validate_checks`：checkpoint 规范化（timing/max_checks 默认值回填）、非法用例
  （prompt 缺失/超长、timing 枚举外、max_checks 越界、assert_spec 坏正则、
  after+空 prompt）；
- `pre_check_tool`：deny 优先于 checkpoint、预算原子消耗（并发场景两连接竞争）、
  预算耗尽放行、多规则按登记序取第一条有预算的、`rules` 计数含 checkpoint 行、
  `subagents` 定向注入（名单内子代理动作被拦 / 名单外子代理与根会话动作放行
  且不消耗预算 / 无名单行不触发 agent 反查）；
- `post_check_tool` + `/post-check` 端点：鉴权 403、scope 映射（OC 子会话归根）、
  预算、fail-open、`rules=0` 形状；
- `check_session_gate`：assert_spec 兜底 passed/failed、无 assert 跳过、
  subagents 定向、`gate_failure_message` 渲染 checkpoint 条目；
- 登记/同步重置 `fired_count`；策略合并（policy ∪ batch 同名覆盖、解绑后非终态
  替换、enabled=false 跳过、失效引用 400、DELETE 非终态引用 409）。

**探针（spike，先行）**：§5 三实验，产出注入方式结论。

**E2E**（`e2e/ai-full/agent-action-gate.spec.ts` 扩展，真 LLM）：

1. before 检查闭环：配 checkpoint（write 报告前 + assert read 知识库）→ 模型首写
   被拦（检查提示词可见）→ 读 → 重写通过 → 终态 passed；
2. 预算防冗余：`max_checks=1` 下第二次写不再被拦；
3. 双保险：模型无视提示词硬写 → 终态 fail → gate_retry 修正轮补读 → 复核 passed；
4. after 场景：按探针结论形态验证（写完立即被要求补正 → 重写 → 通过）；
5. 策略复用：创建策略 → 两个批引用 → 各自子任务期望生效；解绑后新派发不再挂；
6. agent 定向：checkpoint 带 `subagents` 名单（如仅"分析师"）→ 名单内子代理的
   写入被拦、名单外子代理/根会话直接放行，终态 assert 只认名单内动作。

**回归**：现有 deny/终态核对/gate_retry/verifier 全绿；后端全量 + 前端全量。

## 12. 设计决策记录

1. **第三种 mode 而非新表**（方案 A）：登记入口/校验/scope 树约束/fail-open/
   批承载/dry-run 全部复用，「门禁」保持一个系统一个概念；方案 B（独立表+引擎）
   需平行复制全套机制且割裂语义，方案 C（插件内存计数）重启丢计数、无审计、
   违背"核对在服务端"红线，均否。
2. **期望行即快照**：登记时把策略 checks 展开成 `action_expectations` 行，策略后改
   不重写已登记任务——策略免版本化、DELETE 只需防护非终态引用。
3. **预算防冗余而非防死循环的附加物**：预算的首要动机是需求方指出的"动作重复
   发生（写脚本→改脚本）导致检查冗余"；防死循环是它的自然副产品。
4. **deny > checkpoint 优先级**：deny 是"不允许"语义，命中即终判；checkpoint
   不应在 deny 命中时浪费预算。
5. **after 规则要求明确检查指令**：纯"请自查"式 after 注入价值低（结果刚进上下文），
   强制 prompt 非空 + 探针定注入机制；机器可验的 after 前置断言（文件内容级）
   留待 effect_spec 扩展。
6. **不接全局默认策略**：门禁只约束登记过的事项是既有红线（05 册 §4.4"无期望 →
   passed"），隐式挂载会静默改变全部既有批任务的行为。
7. **`VARCHAR(10)` 恰好容纳 'checkpoint'**：mode 列宽不动；若未来需要更长的
   mode 值再迁移列宽。
8. **MCP 入口不扩**：checkpoint 提示词是治理语义（干预模型行为），模型自主登记
   干预规则会打破"执行侧无增删期望路径"红线的强度假设；入口 C 保持 post-only。
9. **agent 定向注入与终态断言共用 `subagents` 名单**：一张名单约束两种时机
   （名单外子代理不注入、不计入 assert，根会话自身动作同属名单外）——避免
   "注入一套口径、断言另一套口径"的配置漂移；deny 行为（mode='pre'）一期
   不加名单过滤，保持树级全量，不改变既有 deny 语义。

## 13. 涉及文件清单

**迁移**：`server/migrations/2026_10_11_gate_checkpoint_policy.py`（新；双注册 app.py）

**后端**：`server/utils/agent_ledger.py`（validate_checks 扩展、pre_check_tool 扩展、
post_check_tool 新、check_session_gate assert 核对、登记重置 fired_count、
gate_failure_message 渲染）；`server/routes/ai_gate_internal.py`（post-check 端点）；
`server/utils/skillopt.py`（PLUGIN_JS after 钩子）；`server/utils/gate_policy_repo.py`
（新）；`server/routes/ai_gate_policies.py`（新，app.py 注册）；`server/utils/batch_repo.py`
（gate_policy_id）；`server/utils/batch_engine.py`（_fetch_action_checks 策略合并）；
`server/routes/ai_chat_batches.py`（gatePolicyId 入参校验）

**前端**：`src/api/gatePolicies.ts`（新）；管理页「门禁策略」（settingsCatalog + 路由 +
列表/编辑器组件）；`CreateBatchDialog.vue` / `EditBatchConfigDialog.vue`（策略下拉 +
checkpoint 规则行）

**测试**：`server/tests/test_agent_ledger.py`（或新增 test_gate_checkpoint.py）、
`server/tests/test_gate_policies.py`（新）、`e2e/ai-full/agent-action-gate.spec.ts` 扩展
