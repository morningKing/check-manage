# 批任务子会话启动耗时日志（batch boot 系列）：慢启动取证与分流

适用：所有部署实例（尤其生产）｜首次定位：2026-11-06｜相关提交：`b131f695`
（feat(batch): 启动链路分段耗时日志——batch boot 系列）

## 1. 背景与症状

「批创建→系统提示词上屏」慢启动（生产体感曾达 ~60s）此前无从取证：
子会话要么静默 pending，要么直接进入 OC 链路，中间每一步耗时不可见。
`b131f695` 在批引擎 claim 与 `_run_one` 全链路埋了分段计时，把启动拆成
**排队 + 逐段执行** 两半，慢在哪一截直接从日志读出。

## 2. 日志落点

- 文件：**`<server目录>/ai-chat.log`**（默认），可被环境变量
  `AI_CHAT_LOG_FILE` 覆盖；logger 名 `utils.batch_engine`。
- 级别：逐段行与汇总行是 **INFO**，无需任何配置；`total > 5s` 时汇总行
  自动升 **WARN** 并追加 `(slow)` 后缀。
- 滚动：默认 10MB × 5 备份（`AI_CHAT_LOG_MAX_BYTES` /
  `AI_CHAT_LOG_BACKUP_COUNT` 可调）。**慢启动案例可能已滚进
  `.1`~`.5` 备份，取证要带通配**。
- 生产前提：代码含 `b131f695`（2026-11-06 之后重启过后端即有）。

## 3. 取证命令

```bash
cd <server目录>

# 每个子会话一条汇总行（启动耗时直接读 total=）
grep -h "batch boot" ai-chat.log* | grep summary

# 只看慢的（>5s 自动带 (slow) 标记）
grep -h "batch boot" ai-chat.log* | grep "(slow)"

# 全量（含逐段行），配合 sid 定位单个子会话
grep -h "batch boot" ai-chat.log* | grep <sid前8位>

# 按批定位
grep -h "batch boot" ai-chat.log* | grep <batch_id>
```

## 4. 三类日志行

### 4.1 排队行（claim 时打一次）

```
batch boot sid=<sid> step=queue waited=50s seq=1 batch=<batch_id>
```

- `waited=`：**会话创建 → worker claim** 的排队时长。
- 并发槽满 / 预算封锁时子会话静默 pending，「批建了半天没动静」的
  第一嫌疑就是这一段。

### 4.2 逐段行（claim 后每个阶段各一行）

```
batch boot sid=<sid> step=ctx ms=0
batch boot sid=<sid> step=prompt_build ms=0
...
batch boot sid=<sid> step=send ms=109
```

| 段 | 含义 | 常见慢因 |
|---|---|---|
| `ctx` | 批上下文装配 + 门禁期望登记 | — |
| `prompt_build` | prompt 组装 + 子代理复用指令 | 记忆检索 |
| `prepare_workspace` | 工作区准备（continue 复用既有工作区） | 磁盘 IO |
| `provision` | 工作区 git clone 供项目级 agent/skill | 网络 / 仓库大 |
| `skills` | 全局技能注入（best-effort，失败不挡启动） | — |
| `agent_check` | agent 可用性 fail-fast 校验 | OC 链路 |
| `create_session` | OC create_session 建会话 | OC 服务状态 |
| `audit` | 执行审计 attempt 落账 | — |
| `user_persist` | 子会话行落库——**此刻系统提示词已上屏** | — |
| `send` | prompt 发给 OC | OC / 模型链路 |

### 4.3 汇总行（send 后打一次）

```
batch boot sid=<sid> summary total=3688ms oc=ses_xxx ctx=15ms prompt_build=422ms \
  prepare_workspace=250ms provision=0ms skills=0ms agent_check=0ms \
  create_session=1796ms audit=30ms user_persist=0ms send=1140ms
```

- `total=` = **claim → prompt 发出** 的启动耗时（即启动本身花了多久）。
- `oc=` 该子会话对应的 OC 会话 ID，可与 OC 侧日志对齐。
- `>5s` 升 WARN + `(slow)`。

## 5. 怎么读：用户体感 = 排队 + 启动

同一个 `sid` 的排队行与汇总行相加即为用户体感的完整启动时长：

```
00:19:51  batch boot sid=3acc7634... step=queue waited=50s
00:19:52  batch boot sid=3acc7634... summary total=140ms
```

→ 体感 ≈ **50 秒**，但慢在**排队**（并发槽/预算），不在启动链路本身。

### 分流口诀

| 现象 | 结论 | 下一步 |
|---|---|---|
| `waited=` 几十秒，`total=` 亚秒 | 慢在排队 | 查并发槽（env `AI_BATCH_*`）、
`ai_batch_worker_leases` 租约是否被孤儿 worker 占坑、批预算配置 |
| `total=` 秒级且有 `(slow)` | 慢在启动链路 | 看同一行哪段 `ms=` 最大：`create_session`/`send` →
OC 链路；`prompt_build` → 记忆检索；`prepare_workspace`/`provision` → 工作区/clone |
| `queue` 行根本没出现 | 子会话从未被 claim | worker 没起来或槽位耗尽，先看
`ai-chat.log` 里 worker 启动与租约日志 |

## 6. 参照值

- dev 固有链路实测 **0.85s**（2026-10-27 批）——生产 `total` 亚秒级即正常。
- 本地真实样例（2026-11-27）：

  ```
  summary total=3688ms  create_session=1796ms send=1140ms   # OC 链路为主，正常波动
  queue waited=50s → total=140ms                          # 典型排队型慢启动
  ```

## 7. 注意事项

- 日志按 sid 对齐：`queue` 行、逐段行、`summary` 行共用同一 `sid`，
  排查单个慢案例时全链串联看。
- `waited` 大而 `total` 小的场景，修复方向在**并发与租约**侧
  （见 `check-manage` 批任务并发/租约相关提交），不在本日志覆盖的启动链路。
- 该埋点只覆盖**首次启动**（含 retry-failed 重跑、continue 续跑的 claim
  链路）；会话运行中途的耗时归因用性能分析页（PerfReport）或
  OC 侧日志。

## 8. 附：工作区三变量口径表（2026-11-28 增补）

生产「`prepare_workspace` 变慢」的排查实锤出三个易混变量，口径如下——
**删历史文件的不是配额，是保留天数；配额数字多大都照样全量遍历**：

| 变量（.env） | 默认 | 干什么 | 设 0 的效果 |
|---|---|---|---|
| `AI_WORKSPACE_QUOTA_MB` | 0=不启用 | 每个子会话派发时 `os.walk` 该用户
整个工作区逐文件求和，超限阻断派发（`_prepare_workspace`） | 跳过检查（也跳过遍历）；
**设超大数字≠跳过**——数字多大都要走完整棵树，只会白白变慢 |
| `AI_WORKSPACE_RETENTION_DAYS` | **30** | 每日 03:17 定时任务
`reclaim_expired_workspaces()` rmtree 终态批次超龄工作区 | `return 0`，
历史全保留——**「历史文件被误删」的真凶是它的默认 30** |
| `AI_SESSION_TTL_HOURS` | 24 | 会话/工作区回收与编排回收器
（`orchestration_engine` 按 ≤0 跳过） | 正确语义：永不回收 |

「历史全保留」生产推荐：QUOTA 留空（默认 0）+ `RETENTION_DAYS=0` +
TTL 保持 0。

### 8.1 附带损伤与修复：TTL=0 曾令 MCP token 签发即过期

`AI_SESSION_TTL_HOURS` 曾同时充当 MCP token 签发/续租的 TTL——设 0 后
token 写库即过期，无人值守批子会话无续租可救，平台 MCP 工具整体 401
（2026-10-27 实锤，账本 `check-manage_*` 调用归零）。

**修复（本批提交）**：拆出独立 `AI_MCP_TOKEN_TTL_HOURS`（默认 24，
0/负值钳制回 24），签发/续租点全部换用：
`routes/ai_chat.py`（创建签发/重置重签/动作续租）、
`utils/batch_engine.py`（批子会话 opencode.json）、
`utils/kefu_repo.py`（客服会话）。`orchestration_engine.py` 保留
`AI_SESSION_TTL_HOURS`（0=跳过回收语义正确）。

回归：`server/tests/test_mcp_token_ttl_config.py`（5 例：0 钳制/负值钳制/
显式生效/缺省 24/TTL=0 下真实签发路径过期时间在未来）。
