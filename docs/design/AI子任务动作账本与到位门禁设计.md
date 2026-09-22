# AI 子任务动作账本与到位门禁设计

> 目标问题:多 subagent 长任务中,**关键动作是否真的发生**无法被保证(todo/prompt 是概率手段)。
> 本方案用确定性的基础设施回答三类到位标准:某脚本**是否被执行**、某仓库**是否被克隆**、
> 某知识文件**是否被读取**——不依赖 prompt、不依赖模型自觉、无需按场景定制代码。
>
> 代码基线:`server/utils/batch_engine.py`、`server/routes/ai_chat.py`、
> `server/migrations/`、`mcp-server/tools/`。

---

## 1. 结论(先说方案)

**把 agent 的每一次工具调用落成账本(`agent_tool_calls` 表),"到位标准"表达为账本上的
匹配期望(`action_expectations` 表),在子任务终态由代码核对,不过门就按既有状态机处置。**

- 账本采集:零新增 API 调用——批任务 worker 递归拉取子代理消息的既有路径
  (`_write_subtask` 拿到的 `child_messages`)与交互 SSE 监听器都在流经 tool part,
  从"看过就丢"改为"落账可查"。
- 到位原语:一条期望 = `(工具名, 参数正则, 要求状态, 最少次数)`,纯粹是数据;
  三个示例到位标准是同一个原语的三行 SQL,新增场景零开发。
- 判定全程无模型参与:核对是一条 SELECT,处置走既有状态机
  (标记失败 + 准确原因),重跑与否可配置。

设计依据:读文件、执行脚本这类动作**不改变世界终态**(读完文件世界无变化),
动作级"到位"唯一通用的验证对象是**动作事件流本身**。这与业界 agent observability
(tool call trace 账本)与"完成门 / Stop-hook gate"是同一模式。

---

## 2. 目标与非目标

**目标**

1. 通用:任何"动作是否发生"类到位标准,只需登记一条期望数据,不写定制代码。
2. 必然:核对由服务端确定性代码执行,模型无法省略、无法以声明代替证据。
3. 兼容:复用既有采集路径、subtask 归属、批任务状态机与迁移机制,不新增外部依赖。

**非目标(M1 不做,见 §12 分期)**

- 不验证动作的**效果/质量**(脚本跑成没跑成、总结写得好不好)——那是效果断言层,
  与动作层正交,M3 另立原语。
- 不拦截工具调用(只读账本 + 事后门禁,不做 PreToolUse 阻断)。
- 不做面向 kefu 公开身份的任何口子。

---

## 3. 总体架构

```
 OpenCode serve (:4096)
   │  消息/parts(REST 轮询 + SSE 事件,两条既有通路)
   ▼
┌────────────────────────────────────────────────────────────┐
│ 采集层(改造点,一次建设)                                     │
│  批任务:batch_engine._write_subtask(child_messages 递归)     │
│  交互:  ai_chat.py SSE 监听 + 晚挂监听的 API 快照 backfill    │
│  统一入口:utils/agent_ledger.record_tool_parts(...)          │
│  幂等键:(oc_session_id, part_id)  ON CONFLICT 升级状态       │
└──────────────┬─────────────────────────────────────────────┘
               ▼
   agent_tool_calls(动作账本:谁、在哪个会话、调了什么工具、参数、成功?)
               ▲
               │ 一条 SELECT/期望
┌──────────────┴─────────────────────────────────────────────┐
│ 门禁层(核对执行器 action_gate.check(...))                    │
│  action_expectations:(工具, 参数正则, 要求状态, 最少次数)      │
│  触发点:批 worker 子任务终态(拉模式,确定性)                   │
│         交互 session.idle(M2)                               │
└──────────────┬─────────────────────────────────────────────┘
               ▼
   处置(既有状态机,无模型参与)
   通过 → 期望标 passed(留证据计数与时间)
   不过 → 子任务 failed,error_message='action_gate: <名> …'
          (默认升级人工;可选 gate_retry 定向重跑,M2)
```

---

## 4. 数据模型

### 4.1 动作账本 `agent_tool_calls`

```sql
CREATE TABLE IF NOT EXISTS agent_tool_calls (
    id              BIGSERIAL PRIMARY KEY,
    oc_session_id   VARCHAR(100) NOT NULL,   -- 产生调用的 OpenCode 会话(含子代理会话)
    root_session_id VARCHAR(100),            -- 平台侧根会话 ai_chat_sessions.id(可空)
    subtask_id      VARCHAR(100)             -- 归属子任务(= ai_chat_subtasks.id,
                    REFERENCES ai_chat_subtasks(id) ON DELETE CASCADE),  -- 即子代理 oc 会话 id
    message_id      VARCHAR(100),            -- OpenCode 消息 id
    part_id         VARCHAR(100) NOT NULL,   -- OpenCode part id(幂等键的一半)
    tool            VARCHAR(50)  NOT NULL,   -- bash / read / write / edit / task / ...
    args_text       TEXT,                    -- 工具入参摘要,截断至 8KB
    state           VARCHAR(20),             -- pending / running / completed / error
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_tool_call_part
    ON agent_tool_calls(oc_session_id, part_id);
CREATE INDEX IF NOT EXISTS idx_agent_tool_call_q
    ON agent_tool_calls(oc_session_id, tool, state);
```

- **幂等与状态跃迁**:同一 part 会以 pending → completed 多次到达(SSE 与 REST 快照
  也会重复投递),统一 `ON CONFLICT (oc_session_id, part_id) DO UPDATE`,状态只进不退
  (`completed`/`error` 不被 `pending` 覆盖)。
- **归属**:`ai_chat_subtasks.id` 本身就是子代理的 oc 会话 id(批引擎以 child
  sessionID 建行),所以账本行天然可按子任务过滤,无需额外映射。
- **保留策略**:随既有备份策略;量级为每会话几十~几百行,无需清理任务,
  如需可按 `occurred_at` 定期归档(非本期)。

### 4.2 到位期望 `action_expectations`

```sql
CREATE TABLE IF NOT EXISTS action_expectations (
    id            BIGSERIAL PRIMARY KEY,
    scope_type    VARCHAR(20)  NOT NULL,    -- 'session' | 'subtask' | 'tree'
    scope_id      VARCHAR(100) NOT NULL,    -- 会话/子任务 id;tree 时为根会话 id
    name          VARCHAR(100) NOT NULL,    -- 人类可读,如 "克隆目标仓库"
    tool          VARCHAR(50)  NOT NULL,    -- 账本 tool 精确匹配
    args_pattern  TEXT         NOT NULL,    -- POSIX 正则,作用于 args_text
    require_state VARCHAR(20)  NOT NULL DEFAULT 'completed',
    min_count     INT          NOT NULL DEFAULT 1,
    last_status   VARCHAR(20),              -- pending / passed / failed
    last_checked_at TIMESTAMPTZ,
    last_evidence INT,                      -- 最近一次核对命中的账本行数
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (scope_type, scope_id, name)
);
```

- 期望是**数据**,不是代码:新增一种到位标准 = 新增一行,无任何开发。
- `args_pattern` 来源受信(批任务定义 / 管理员经 MCP 登记),不做沙箱。
- 核对结果就地更新在期望行上(近期一次),历史审计由执行审计侧承接。

### 4.3 期望的承载字段(入口 A/B)

两张既有表各加一列,均幂等迁移:

```sql
ALTER TABLE ai_chat_batches        ADD COLUMN IF NOT EXISTS action_checks JSONB;
ALTER TABLE ai_chat_prompt_templates ADD COLUMN IF NOT EXISTS action_checks JSONB;
```

- 批任务创建时自身未提供 `action_checks` 且引用了模板 → 回退用模板值;
- dispatcher 派发子任务时,把生效的 checks 落成 `action_expectations` 行
  (source='batch'),此后门禁只读期望表,批/模板原始配置仅作登记来源。

---

## 5. 关键流程

### 5.1 账本采集(改造点)

新增 `server/utils/agent_ledger.py`,唯一入口:

```python
record_tool_parts(oc_session_id, root_session_id, subtask_id, messages)
# 遍历 messages[].parts,type=='tool' → upsert 账本
```

两个调用点,均零新增外部调用:

| 通路 | 触点 | 说明 |
|---|---|---|
| 批任务(主) | `batch_engine._write_subtask` | 该函数本就递归拿到**每个**子代理的全部原始消息;扫描 parts 落账即可。拉模式(REST 轮询全量消息)天然不丢事件 |
| 交互(同步支持) | `ai_chat.py` SSE 监听器 `message.part.updated` 分支 | 已在解析 parts;tool part 顺手落账。晚挂监听的 API 快照 backfill 走同一入口,幂等键保证不重不漏 |

实现第一步:抓一次真实事件样本,固化 tool part 的字段契约
(入参字段、state 取值、part_id 稳定性),作为测试夹具固化(见 §11)。

**采集时序与性能语义**:

- **不是严格实时,是准实时,且刻意不在工具执行链路上**——写入发生在服务端
  消费侧,工具调用本身的延迟零增加。批任务随轮询周期批量入库(拉模式不丢
  事件);交互侧 SSE 事件到达即写(毫秒级),晚挂监听由快照 backfill 补齐。
- **正确性与延迟解耦**:终态门禁核对发生在观察到终态的那个轮次内,同一轮次
  按"落账 → 核对 → 写终态"顺序执行,轮询延迟不影响核对完整性。
- **写放大防护**:轮询会使同一 part 反复到达,upsert 必须带条件更新——
  `ON CONFLICT ... DO UPDATE SET ... WHERE agent_tool_calls.state
  IS DISTINCT FROM EXCLUDED.state`,状态未变不产生行写入;part 状态多为
  pending→completed 一次定型,稳态每轮询零写入。同轮次多行合并单语句插入,
  控制往返次数。
- **失败语义**:账本写入失败不得中断任务流程(best-effort,与子任务落库同一
  连接路径);但账本缺失时门禁结果记为 `inconclusive` 并告警——既不算"未到位"
  也不静默放行。

### 5.2 期望登记:功能入口与时机

期望(账本匹配条件)从哪里设置,按"保证强度"给四个入口:

| 入口 | 载体 | 时机与性质 | 期 |
|---|---|---|---|
| **A. 批任务定义**(主入口) | `ai_chat_batches.action_checks` JSONB;创建批任务 API/对话框配置 | dispatcher 认领派发时写期望行,**先于子任务执行**,确定性 | M1 |
| **B. 模板内嵌**(通用化关键) | `ai_chat_prompt_templates.action_checks` JSONB | 模板作者配置一次,凡从该模板创建的批任务自动携带;批任务自身未配时回退模板值 | M1 |
| **C. MCP 工具**(交互会话动态) | `register_action_check` 工具(admin/developer,不进 kefu 白名单) | primary agent 派发子任务前登记;**模型中介,强度低于 A/B**,仅用于长尾临时需求,关键流程不走此路 | M2 |
| **D. 管理端补挂**(运维兜底) | 管理端 API(后续可挂 UI) | 对运行中/排队中的会话或批任务补挂期望,终态核对时生效;admin 权限 | M2 |

**入口 A/B 的数据形态**(批任务与模板各加一列,创建接口 `ai_chat_batches.py create()`
接受可选字段,缺省回退模板):

```json
"action_checks": [
  { "name": "克隆目标仓库",   "tool": "bash", "args_pattern": "git clone\\s+\\S*acme/inspector" },
  { "name": "读取巡检知识库", "tool": "read", "args_pattern": "docs/knowledge/patrol\\.md" },
  { "name": "执行对账脚本",   "tool": "bash", "args_pattern": "scripts/reconcile\\.py" }
]
```

UI:批任务创建对话框增加"动作门禁"区块——结构化行(名称 / 工具下拉 / 参数正则 /
最少次数),高级模式切 JSON 编辑;工具下拉内置常见模板(执行脚本 / 克隆仓库 /
读取文件),作者填参即可,不必手写正则骨架。

**编写辅助(配正则的前提是知道参数长什么样)**:

- **账本查询接口**:按批任务/子任务列出实际发生的工具调用(工具、参数原文、状态),
  UI 放在子任务详情/审计面板——作者从真实 `args_text` 里取材写正则;
- **试跑核对**:期望行提供 dry-run——对当前账本立即执行一次核对并返回命中明细,
  保存前即可验证正则是否写得对(命中 0 条时给出去相近调用的提示)。

**AI 自动提炼(入口 A/B 的预填器,把设置成本降为零)**

由一次**独立的提炼调用**(非执行 agent)分析任务文本,产出 `action_checks` 建议,
预填入口 A/B 的表单/列。

**输入面:四层静态配置,下探到 Agent 配置与 Skill 步骤**。四层在派发前全部可得:

| 层 | 来源 | 提炼产出 |
|---|---|---|
| ① 任务文本 | 批 prompt / 模板 content | 任务隐含的必经动作 |
| ② Agent 配置 | 批定义 `agent` 字段、AGENTS.md 项目指导 | 该 agent 的固定动作约束 |
| ③ Skill 清单 | `inject_global_skills` 返回的注入名单(静态可知) | 见下"两层可检验性" |
| ④ Skill 关键步骤 | 各 SKILL.md 正文(skill 即文本,步骤句可静态解析) | 步骤点名的具体动作 |

提炼器做的是 **任务 × 技能的交集**:不是把所有 skill 的所有步骤都变成期望,而是
判断哪些 skill 步骤是本任务的必经动作,只提炼这些。

**Skill 的两层可检验性**:

1. **"是否用了某 skill"本身就是一条期望**。skill 注入在会话工作区
   `.opencode/skills/<name>`,agent 使用 skill 必先经 read 工具读 SKILL.md,
   账本可查:`tool=read, args_pattern='.opencode/skills/trace-analyzer/'`。
2. **skill 内关键步骤是否执行**:从步骤句中点名的具体产物提取
   (脚本路径 / 仓库 URL / 知识文件路径 → bash/read 模式)。配套一条
   **skill 写作规范建议**:关键步骤应写可检验的具体产物(如
   `运行 scripts/reconcile.py`),提炼才能落成精确 pattern——现有 SKILL.md
   已是"严格按以下步骤执行"的 procedural 写法,天然适配。

流水线与三道机械校验:

```
任务文本 ──▶ 提炼器(一次 LLM 调用,JSON schema 约束输出)
              │ ① schema 校验(工具名合法、正则可编译)
              ▼
          ② 历史记账 dry-run:每条 pattern 对同类历史任务的账本试跑,
             命中率过低即标黄提示(正则可能写偏)
              ▼
          ③ 人工一键确认(批任务对话框/模板编辑器预填;可改可删)
              ▼
          登记(source='ai')→ 派发后不可增删,终态机械核对
```

- **定位与边界**:提炼解决的是"设置成本",不承担完整性保证——提炼可能漏
  (覆盖面是概率的),但登记了的期望强度不变。红线:**执行侧无增删期望的
  任何路径**,提炼与执行分离(不同阶段、不同上下文),防止执行者自定检查的
  自证问题。
- **交互会话(M2)**:primary agent 可经 MCP `propose_action_checks` 提炼并登记;
  由于父级登记后子代理**无法注销**,结构性分离仍然成立——强度为
  "登记了必然核对,登记本身依赖模型"(与入口 C 同级),关键流程仍走 A/B。
- 所有 AI 提炼的期望带 `source='ai'` 标记,UI/审计可见其来源。

**时机语义**:门禁核对发生在子任务终态,凡在此之前登记的期望一律生效;
入口 A/B 在派发前由代码写入(不存在漏登窗口),入口 C/D 登记先于完成即计入。
登记本身不触碰模型,入口 C 是唯一的例外,故明确其定位为非关键路径。

**定向能力(消除不相关子代理/子任务的误报)**:

- **子代理级**:检查可声明 `subagents: ["general", …]`——核对只统计这些名称
  子代理(subtask)的工具调用,根会话与不相关子代理的动作不参与;账本记录
  动作归属的 agent 名。
- **子任务级(apply_to)**:批任务级检查可声明
  `apply_to: {"batch_seq": [0,2]}` / `{"input_file_glob": "report-*.docx"}`——
  条件不匹配的子任务**不登记**该期望,从源头避免不相关动作被门禁要求。

### 5.3 核对执行

核对执行器 `agent_gate.check(scope_type, scope_id) → [per-expectation 结果]`:

```sql
SELECT COUNT(*) FROM agent_tool_calls
WHERE oc_session_id = :scope_id        -- 子任务即子代理会话 id
  AND tool = :tool
  AND args_text ~ :args_pattern
  AND state  = :require_state;
-- 命中数 >= min_count ⇒ passed,否则 failed
-- scope_type='tree' 时:oc_session_id 改为 IN(根会话 + 该根下全部子代理会话),
-- 一个 JOIN ai_chat_subtasks(root_session_id = :scope_id)圈定子树——skill 步骤
-- 常由子代理执行,skill 类期望必须用 tree 作用域,否则根会域核对会漏检
```

触发点:

| 模式 | 触点 | 性质 |
|---|---|---|
| 批任务 | worker 判定子任务终态后、写 `ai_chat_subtasks` 终态**之前**,内联核对 | 确定性(拉模式已完成判定,与终态写入同一线程) |
| 交互(M2) | `session.idle` 钩子(现有)核对该会话全部期望 | 事件驱动,幂等可重入 |

### 5.4 处置(无模型参与)

- **通过**:期望行置 `passed`(记 evidence 数与时间),流程照常。
- **不过**:子任务 `status='failed'`,`error_message='action_gate: <name> 未满足
  (期望 <tool ~ pattern>,账本命中 0 次)'`——沿用对账器"带准确原因失败"的风格;
  批任务汇总照常聚合,人工在子任务面板直接看到是哪个动作没到位。
- **默认不自动重跑**:整树重派发成本高且可能重复副作用。预留 `gate_retry=1`
  配置(M2 实现):带 `continue_prompt` 定向重跑该子任务——这是全链路唯一
  可能触达模型的环节,且默认关闭。

---

## 6. 通用性论证

| 场景 | 期望行 |
|---|---|
| 脚本被执行 | `tool=bash, args_pattern='scripts/reconcile\.py', state=completed` |
| 仓库被克隆 | `tool=bash, args_pattern='git clone\s+\S*<repo>', state=completed` |
| 知识文件被读取 | `tool=read, args_pattern='docs/knowledge/<file>', state=completed` |

三行同一原语。未来任何"动作是否发生"类标准(是否调用某 MCP 工具、是否写了某文件、
是否派发了某子任务)都是账本里已有或将要有的 tool part,登记即用。

---

## 7. 边界与对策(如实声明)

1. **"被调用"≠"起效"**:本方案语义是动作级(执行过、读过、克隆过),不证明
   结果正确。效果断言(产物文件存在、DB 有行、脚本 exit 0)是 M3 的第二类原语,
   与本层叠加而非互替。
2. **参数匹配是启发式**:极理论上模型可把模式字符串嵌进无害命令骗过正则
   (如 `echo 'git clone ...'`)。正常业务任务不在该威胁模型内;如需强保证,
   M3 对 bash 类期望追加"输出含特征/exit 0"双断言。
3. **时序**:期望必须先于派发登记;批任务为拉模式(轮询全量消息)不丢事件;
   交互侧沿用 listener-before-dispatch + 快照 backfill,幂等键兜底重复投递。
4. **脱敏与截断**:`args_text` 截断 8KB;对命令行中已知形态的密钥值做简单打码
   (与操作日志同策略)。
5. **性能**:写入在服务端消费侧、不在工具执行链路,量级为每工具调用一行
   upsert(重度任务数百行/子任务,批任务万级行),远小于既有子任务消息
   全文 upsert;条件更新防写放大(见 §5.1);核对为索引覆盖的终态一次性查询;
   行典型几百字节(8KB 截断),百万行级对 PG 无压力。

---

## 8. 与既有机制的关系

- **批任务对账器(DR)**:互补不重叠——对账器回答"会话还在不在、该不该续跑",
  门禁回答"动作做没做"。两者共用"DB 即账本 + 终态写准确原因"的哲学。
- **执行审计(analyze_trace)**:账本是审计的原始底料,M3 起审计页可直接读
  `agent_tool_calls` 呈现完整工具轨迹,不再依赖事件回放。
- **子任务体系**:`ai_chat_subtasks` 已带 status/error_message,门禁只是给终态
  写入多了一道前置检查,无新状态机。

---

## 9. 改动清单

| 文件 | 改动 |
|---|---|
| `server/utils/agent_ledger.py` | **新增**:账本 upsert 入口 + 核对执行器 + 期望登记辅助(批 checks→期望行) |
| `server/utils/batch_engine.py` | `_write_subtask` 处调用落账;dispatcher 派发时写期望;子任务终态判定处内联门禁;failed 原因带 `action_gate:` 前缀 |
| `server/routes/ai_chat.py` | SSE 监听器 tool part 分支落账;backfill 快照落账(M2 接 session.idle 门禁) |
| `server/routes/ai_chat_batches.py` | create 接受可选 `action_checks`;账本查询/试跑核对接口(编写辅助);**AI 提炼端点**(调提炼器,预填 action_checks,M1.5) |
| `server/utils/action_check_extractor.py` | **新增**(M1.5):提炼器——schema 约束的 LLM 调用 + ①②两道机械校验 |
| `server/migrations/xxx_agent_action_gate.py` | 账本/期望两表 + `ai_chat_batches`/`ai_chat_prompt_templates` 的 `action_checks` 列,幂等 + 启动钩子注册 |
| 前端批任务创建对话框 | "动作门禁"区块(结构化行 + JSON 高级模式);子任务面板展示账本查询与门禁结果 |
| `mcp-server/tools/register_action_check.py`(M2) | 期望登记工具 + `tools/__init__.py` 注册 |

---

## 10. 测试方案

- **单元**(fake messages/parts,不起 OpenCode):part→账本 upsert 的幂等与状态
  只进不退;核对原语(正则命中/状态不符/次数不足);门禁处置(failed 原文、
  默认不重试)。
- **集成**:用真实事件样本做夹具(见 §11),走 `_write_subtask` 全链路,断言
  账本行与期望结果;批任务端到端:定义带 `action_checks` 的批任务,子代理
  故意不读知识文件 → 子任务 failed 且 error_message 可读。
- **留证**:按既有测试规范,真实链路 Playwright/批任务 E2E + 证据归档可复核。

## 11. 实现前须实测确认的契约

1. tool part 的入参字段(bash 的 command、read 的 path 具体在 `input` 还是
   `state.input` 等)——抓真实样本固化,并转为测试夹具。
2. bash 非 0 退出时 OpenCode part 的 state 表现(决定 M1 是否能区分"执行失败")。
3. part_id 的稳定性(跨 SSE/REST 两次读取是否一致)——幂等键的前提。

## 12. 分期

| 期 | 内容 | 价值 |
|---|---|---|
| **M1** | 账本两表 + 批任务路径采集 + **入口 A/B**(批定义与模板的 `action_checks`、创建接口与对话框)+ 门禁处置(含 **tree 作用域**,skill 类期望依赖)+ 账本查询/试跑接口 | 确定性核心闭环,批任务场景即可用 |
| **M1.5** | **AI 自动提炼**(四层输入面:任务文本/Agent 配置/Skill 清单/Skill 步骤 + 机械校验 + 表单预填),模板保存时同样支持 | 期望设置成本归零 |
| **M2** | 交互会话门禁(session.idle)+ **入口 C/D**(MCP 登记工具、管理端补挂)+ gate_retry + UI 到位状态展示 | 覆盖交互长会话 |
| **M3** | 效果断言原语(文件/DB/exit 0)、审计页复用账本 | 从"动作级"延伸到"效果级" |
