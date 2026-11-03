# 批任务复读看门狗设计（重复输出检测与自动纠正）

## 0. 范围与决策记录

**问题**：弱模型在长回合里会复读——同样的内容逐字反复输出（文本复读），
或同一个工具带完全相同的参数反复调用（工具复读）。现有三道防线全部拦不住：

| 防线 | 为什么拦不住 |
|---|---|
| STALL 看门狗（180s，`batch_engine.py:810`） | 判据是进展签名变化（`_progress_signature`，`:2839`）；复读时文本持续增长，签名每次都变，计时器不断重置 |
| 工具卡死看门狗（900s，`:817`） | 每次新工具调用（哪怕同参）都算进展；且 `_subagent_progressing`（`:4070`）里子代理复读时子会话签名一直变，每次都判「活着」顺延 |
| question 自动拒绝 | 只管提问挂起 |

结果：复读一路烧 token 直到 `SESSION_TIMEOUT_SEC` 硬上限，失败 reason 还是
误导性的「超时」。

**决策记录**（讨论已拍板，2026-11-03）：

1. **判据必须「逐字重复」**而非相似度——长清单/表格类合法输出结构高度重复，
   逐字 ≥3 次在合法输出里几乎不存在，相似度判据必误杀；
2. **触发动作方案 A 起步**：abort 父回合 + reason 留痕 + 走既有 auto-retry。
   方案 B（只 abort 子会话、父级自决）列为后续项，需先在 fake OC 上实测
   OC 对「abort 子会话后父级 task 工具行为」再开；
3. **交互会话警示条为二期**（用户在场、中断按钮已有，批任务无人值守才是
   止血点）；
4. 检测是 `_progress_signature` 之外的**独立旁路**——签名继续服务 stalled
   判定，互不污染；
5. 子代理监控的调用通路与先例均已存在（`_running_task_children` 枚举
   child_sid、`_subagent_progressing` 拉 `get_messages(csid)`、question 扫描
   周期性枚举 sub_sids），本设计只补「拉回来之后做复读打分」。

**非目标**：模型侧治本（换模型 / provider 采样参数）不在本 spec；交互会话
实时警示不在本期；不改 `_progress_signature` 语义。

## 1. 检测算法（纯函数，主会话/子代理共用）

核心打分器 `score_repetition(parts) -> evidence | None`，输入归一化 part 序列
`[('text'|'reasoning', text) | ('tool', name, args_hash)]`，输出命中证据
`{'kind': 'text'|tool名, 'count': n, 'preview': str}` 或 None。主会话从
facade 消息、子代理从 raw 消息各做一层薄适配后共用同一个纯函数（好测）。

### 1.1 文本复读：尾部窗口逐字重复

- 只统计本轮消息（`baseline_ids` 过滤后）；text 与 reasoning part 按消息
  顺序拼接后整体打分（思考流复读同样烧 token）；
- 取末尾 `REPEAT_TAIL=400` 字符为尾块，在末尾 `REPEAT_WINDOW=20000` 窗口内
  `str.count(尾块)`，`count >= 3` 判复读；
- 文本总长 `>= REPEAT_TEXT_MIN=1200`（3×400）才启用，短文本直接跳过；
- 窗口截尾同时保证 `str.count` 的 O(n·m) 有界——检测本身不能变成新卡点。

### 1.2 工具复读：同参重复调用

- `(tool, hash(json.dumps(args, sort_keys=True)))` 计数，本轮内同键
  `count >= REPEAT_TOOL_COUNT=3` 判复读（判据对齐 `perf_analysis.py:822`
  事后报表的既有口径，这次搬到实时）；
- 只统计已落地（completed/error）的调用，pending/running 不计（还没到
  「重复失败/重复成功」的定性时刻）。

### 1.3 定性确认（防误杀的代价控制）

单次命中不算，连续 N 个检测周期都命中才定性——错误 abort + 重试 = 整个
子任务重跑，误杀一次的代价远大于检测本身跑一年：

- 主会话：`REPEAT_CONFIRM=3` 个周期（@2s 轮询 ≈ 6s）；
- 子代理：`REPEAT_CONFIRM_CHILD=2` 个周期（@30s 轮询，节奏本身已慢）。

## 2. 落点

### 2.1 主会话看门狗（`_await_finished` 循环，`batch_engine.py:2727`）

数据已在手（每 2s 的 `list_messages` 既拉既用），零新增 IO：

- 循环内新增状态：`repeat_hits`（连续命中计数）与首次命中时刻；
- 每轮对 baseline 过滤后的消息跑 `score_repetition`，命中且
  `repeat_hits >= REPEAT_CONFIRM` → **先 abort**（对齐 stalled/tool-stuck
  的 2026-11-03 修正语义：判死必须真死，别让 OC 继续空转烧 token）→
  `raise _SessionTimeout(int(now - turn_start), reason='repetition loop',
  diagnosis=...)`；
- 未命中则清零 `repeat_hits`。

### 2.2 子代理看门狗（同循环内新增，question 扫描旁）

- 每 `CHILD_REPEAT_POLL_SEC=30`（与 question 扫描同区块、错峰）：枚举
  `_running_task_children`；对每个 child：
  - **延迟启动**：该 child 首次被观测满 `CHILD_REPEAT_DELAY_SEC=120` 才开始
    拉取（多数子代理几十秒内完成，根本不产生轮询）；
  - **全局并发上限** `CHILD_REPEAT_MAX_CONCURRENT=8`：单 worker 每 tick 最多
    拉 8 个 child，多则留到下个 tick；
  - 拉一次 `get_messages(csid)`，raw 适配后跑同一个打分器；
  - 连续 `REPEAT_CONFIRM_CHILD` 命中 → abort 父回合（方案 A，child_sid 也是
    OC session 但 OC 对 abort 子会话的父级行为未验证）→
    `reason='child repetition loop'`；
- 状态存 `child_repeat_state[csid]`（首次观测时刻/上次证据/命中数），与
  既有 `child_sigs` 同生命周期。

### 2.3 失败语义与自动重试（对齐既有三件套）

- `_SessionTimeout.__init__`（`:580`）新增两个 reason 分支的中文文案：
  「检测到模型连续逐字重复输出（复读死循环），已自动中断，请重试该会话」
  （child 版注明发生在子代理）；
- `_is_retryable`（`:3485`）白名单追加 `'repetition loop'`、
  `'child repetition loop'`——可重试类失败，走 `MAX_AUTO_RETRY` 预算；
- `_auto_retry_prompt`（`:3494`）：复读类 reason 在通用尾巴外追加一句
  「注意：不要逐字重复已输出过的内容，从中断处直接继续未完成部分」
  （复读重试不给定向指令大概率再复读一次）；
- 留痕复用 2026-11-03 三渠道：continue_prompt（对话内可见）+ attempt
  error_message + child.retry 批事件，日志行带原因，不新增机制。

### 2.4 诊断留痕（`_stall_diagnosis`，`:2859`）

- 新增 `d['repeat'] = {'source': 'main'|child_sid, 'kind': 'text'|工具名,
  'count': n, 'preview': 80 字预览}`——补上「判定依据不持久化」缺口的
  复读部分；
- `_format_diagnosis`（`:608`）追加渲染 `repeat=child:xxx text×4「预览…」`。

### 2.5 配置（`batch_engine.py:810` 常量区，AI_BATCH_* 命名惯例）

| env | 默认 | 含义 |
|---|---|---|
| `AI_BATCH_REPEAT_DETECT` | 1 | 总开关，0=关闭全部复读检测 |
| `AI_BATCH_REPEAT_CONFIRM` | 3 | 主会话定性确认周期数 |
| `AI_BATCH_REPEAT_TOOL_COUNT` | 3 | 工具同参复读阈值 |
| `AI_BATCH_CHILD_REPEAT_POLL_SEC` | 30 | 子代理轮询节奏 |
| `AI_BATCH_CHILD_REPEAT_DELAY_SEC` | 120 | 子代理监控延迟启动 |
| `AI_BATCH_CHILD_REPEAT_MAX_CONCURRENT` | 8 | 单 worker 每 tick 子代理拉取上限 |

`REPEAT_TAIL/WINDOW/TEXT_MIN/CONFIRM_CHILD` 为类常量不进 env（调参需求
低，控制 env 面积）。无 DB 迁移（reason 与 diagnosis 均为自由字段）。

## 3. 测试

红绿纪律：实现 commit 之前新测试必须红（用户测试要求）；跑全量基线前先停
dev 栈（既有纪律）。

1. **打分器单测（纯函数）**：文本复读×3 命中/×2 不命中；<1200 字短文本
   跳过；100 行结构相似的合法长清单**不**命中（逐字判据防误杀的核心用例）；
   同参工具×3 命中/异参不命中/pending 不计/baseline 消息不计；
2. **主会话集成单测**（stub `list_messages` 递增返回复读 transcript）：
   触发 abort + `_SessionTimeout(reason='repetition loop')`；仅 1 个周期
   命中后恢复正常 → 不触发（确认周期用例）；
3. **子代理集成单测**：延迟启动（delay 窗口内断言 child 的 get_messages
   **零调用**，调用计数 stub）；child 复读 → `reason='child repetition
   loop'` 且 abort 的是父回合；9 个 child 同时在监控 → 单 tick ≤8 次调用；
4. **重试链路单测**：`_is_retryable` 收录两 reason；复读 reason 的
   continue_prompt 带定向尾巴（照抄 2026-11-03 三渠道测试模式）；
5. **E2E（真实链路，fake OC 构造复读流）**：批任务子任务 running → 触发
   复读 → 自动重试 → 重试轮 fake OC 返回正常输出 → completed；批任务页
   事件流含带「复读」原因的 child.retry；构造手法沿用 gate-leak-probe 的
   stub LLM 先例。

## 4. 性能预算

| 项 | 量级 |
|---|---|
| 主会话打分 | 0 新增 IO；20k 窗口 count <1ms/poll；不进 Flask 请求路径 |
| 子代理轮询 | 新增 REST；延迟启动+30s 节奏+每 tick ≤8 上限封顶。典型 10 并发 child×500KB×30s ≈ 170KB/s 解析量，C 层解析持锁短，GIL 可忽略 |
| 交互会话 | 本期零改动 |
| 误杀代价 | 确认周期 + 逐字判据压到接近零；一次误杀 = 一轮重跑，是唯一要防的「性能影响」 |

## 5. 风险与开放点

- **OC 对 abort 子会话的父级行为未知**——方案 B（只 abort child、父级自决
  重派，粒度更细省重试）待 fake OC 实测后二期评估；
- **交互会话警示**（消息流/子代理气泡打「⚠️ 疑似复读」+ 挂现有中断按钮，
  数据挂在 chat_persist 已有流式事件上，零新 IO）二期；
- 子代理轮询上限是**单 worker 口径**，多 worker 部署总量 = worker 数 × cap，
  仍有界（部署并发本身有全局上限）；
- `get_messages` 无分页，transcript 体积是子代理侧主要成本，窗口截尾只管
  打分不管传输——若实测过重，备选「OC 侧加轻量 count 端点」，不在本期；
- compaction 交互：打分只算 baseline 之后的**本轮**消息，压缩总结引用重复
  内容不会误触。
