# AI 会话撤销/重做 0-LLM 真实链路 E2E 探针（undo-probe）

> 目的：端到端验证「AI 会话撤销/重做」的产品级闭环。把**模型大脑换成确定性
> 脚本**（`undo_stub_llm.py`，OpenAI 兼容端点），其余链路全部为真：
> 平台后端（Flask）→ `opencode serve`（真 OC，revert/unrevert/快照）→
> 回合持久化监听 → stash/reverts 表 → SSE 平台事件总线。
> 「撤销后模型还看不看得到被撤销的对话」由 stub 的请求全文日志（
> `stub-requests.jsonl`）取证，100% 确定、可重复回归，不受模型情绪影响。

## 1. 运行方式（本机，全隔离栈）

```bash
python e2e/manual/undo-probe/undo_e2e.py            # 起栈→跑四场景→自动判定→拆栈
python e2e/manual/undo-probe/undo_e2e.py --keep     # 保留栈人工排查（自行 taskkill 树清理）
```

成功输出：逐条 `✓` 断言 + 末行 **ALL GREEN**；退出码 0=全绿 / 1=有失败。

栈与 dev（3002/4096）零共享，跑前强制检查三个探针端口空闲（被占用即拒绝启动）：

| 组件 | 端口 / 标识 | 说明 |
| --- | --- | --- |
| stub LLM | :3197 | OpenAI 兼容脚本化端点（`undo_stub_llm.py`） |
| 平台后端 | :3094 | 真 Flask 后端（`server/app.py`），蓝图无 /api 前缀 |
| oc serve | :4198 | 真 OpenCode serve（OC_EXE 读 `server/.env` 的 `OPENCODE_BIN`） |
| 数据库 | `casemanage_undoprobe` | 每轮 drop+create+init_db 全新隔离库 |
| OC 全局/数据/工作区 | `.dev-ai-workspaces/undo-e2e/<时间戳>/` | 仓内、被根 .gitignore 覆盖、每轮独立子目录 |

前置：`server/.env` 可用（DB 口令、`OPENCODE_BIN`）；本机装有
python + `requests` + `psycopg2` + `python-dotenv`；OC 隔离全局目录的
`node_modules` 首跑从 `.dev-ai-workspaces/gateprobe/xdg-config/opencode/`
拷贝（已装好，无需网络）。

## 2. 断言三件套（undo-redo spec §3）

| # | 语义 | 在哪些断言里落地 |
| --- | --- | --- |
| ① 上下文真回退 | 撤销后模型视角**看不到**被撤轮次（发给 stub 的请求全文取证） | A：T3 请求含 T1、不含 T2；C：rebuild 后 T3 请求带「此前对话摘要」历史重放 |
| ② 文件物理还原 | OC git 快照按步还原/重做文件 | A：undo 后 a.txt=v1、b.txt/c.txt 缺失；B：redo 后 a.txt=v2、b.txt=b-new |
| ③ 跨标签页收敛 + rebuild 降级 + 重做/提交语义 | 平台 DB 权威变更即时扇出；锚失效降级重建；撤销可重做、提交即关闭重做窗口 | A：双 SSE 连接收 `session.revert`、GET messages 带 revert 态、发送后 revert 清空、提交后 redo 409；B：消息窗口恢复含 u2、redo 后 revert 清空；C：rebuild 模式判定；D：批子会话 409 |

## 3. 四个场景

stub 提示词带标记 `[[UNDO-PROBE:<TAG>]]`，stub 按标记执行固定脚本：

```
T1：write a.txt=v1
T2：write a.txt=v2 → write b.txt=b-new → bash echo>c.txt → write outputs/o.txt（被 .gitignore 忽略）
T3：纯文本（撤销后上下文取证轮）
SEED：场景 C 换绑 OC 会话后种的首条 user 消息（只为让 OC 会话非空）
```

- **A 原生撤销**：T1→T2→`undo-preview`（messageCount≥2 / fileCapability=full）→
  两条 SSE 连接挂上→`DELETE /messages/<u2>` 撤销→文件还原+双连接收
  `session.revert`+GET messages 带 revert 态→发送 T3（提交）→stub 日志证明
  上下文含 T1 不含 T2→revert 清空→提交后 redo 409 `NOTHING_TO_REDO`。
- **B 重做全恢复**：undo 后文件/消息裁掉 → `POST /redo` → 文件恢复 v2/b-new、
  消息窗口含 u2、revert 清空。
- **C 锚失效 → rebuild**：探针直接改库把会话的 `opencode_session_id` 换到一个
  有内容的新 OC 会话（模拟「会话恢复换绑过」，**探针特权，生产路径无此操作**）→
  undo 时平台锚不在当前 OC 会话 → 自动降级 rebuild（新 OC 会话 +
  `context_replay_pending`）→ 下次发送前缀注入「此前对话摘要」历史重放，
  stub 日志证明重放到达模型。
- **D 批子会话 409**：种一个 `status='running'` 的批子会话行（含 `ai_chat_batches`
  父行）→ 手工 undo 被 `_execution_controlled` 门禁拒绝 → 409
  `BATCH_SESSION_CONTROLLED`（执行所有权：非终态批子会话由后台 worker 独家驱动）。

## 4. 判定口径与已知取证细节

- **SSE 帧形**：平台帧为 `event: <type>\ndata: <json>`——事件名在 `event:` 行，
  `data:` 行不含类型名，探针按两行解析（`event: session.revert` 才算命中）。
- **stub 的 tag 归类**：stub 取 user 文本里**第一个** `[[UNDO-PROBE:…]]` 标记；
  rebuild 场景的历史重放块把被保留轮次（T1）的标记带进同一条 user 文本，
  T3 请求会被归类为 T1 —— 场景 C 取证不按 `tag=T3` 过滤，按「user 文本同时含
  T3 标记与『此前对话摘要』头」双条件定位。
- **undo 端点响应**：requests/http.client 直连不经前端拦截器，`json.loads`
  得到的就是顶层 `{deleted, revert, messages}`；错误为 `{error:{code,…}}` + 409。
- **后端路由前缀**：蓝图无 `/api` 前缀（`/auth/login`、`/ai/chat/sessions/…`，
  与 gate-leak-probe 同款直连）。

## 5. 排障

沙箱目录 `.dev-ai-workspaces/undo-e2e/<本轮时间戳>/`（每轮独立，失败后保留）：

| 日志 | 看什么 |
| --- | --- |
| `backend.log` | 后端起不来（DB 口令/端口占用/init_db 失败）、undo 5xx、login 失败 |
| `serve.log` | OC serve 起不来（node_modules/端口）、revert 5xx、provider 连接失败 |
| `stub.log` + `stub-requests.jsonl` | stub 是否收到请求、tag/step 判定、**模型视角全文**（断言①的直接证据） |
| `stub-stdout.log` 之外的工作区 | `ws/<uid>/<sessionId>/` 下 a.txt/b.txt/c.txt 的实际状态（断言②的直接证据） |

常见问题：

- **端口占用**：`netstat -ano | findstr "3197 4198 3094"` 找 PID `taskkill /PID <pid> /T /F`。
  探针栈**严禁**复用 dev 栈 3002/4096。
- **undo 返回 409 SESSION_BUSY**：回合监听线程未退干净，稍后重试（探针已在
  wait_turn 后留 1.5s 余量）。
- **`--keep` 残留栈**：手动 `taskkill /PID <backend_pid> /T /F` 等，随后确认端口释放。
- **Windows 文件占用**：OC/node 进程退出有延迟，日志文件句柄由探针持有，
  不做删除式清理即可规避（沙箱按时间戳追加）。

## 6. 与 brief 骨架的实现差异（对题面修正清单）

1. 后端蓝图无 `/api` 前缀，`_api` 直连 `/ai/chat/...` 与 `/auth/login`；登录
   响应字段为 `token`。
2. `ai_chat_batches` INSERT 补 `prompt` NOT NULL 列；会话行无 `kind` 列（已删）。
3. SSE 断言改为解析 `event:` 行（骨架按 data 行 contains 永不命中）。
4. 撤销后 wait_turn 的目标回合数按「剩余行数+新回合」计（A/C 为 2，不是 3）。
5. 场景 C 的 seed 消息用 `SEED` 标记（避免与 T1 取证混淆）；新增 OC 消息轮询等待。
6. 新增 `prepare_db()`（drop+create+init_db，gate-leak-probe 同款）保证每轮全新库。
7. `OPENCODE_BIN` 读 `server/.env`（`_oc_bin`，缺失回退 npm 路径）；serve 额外传
   `OPENCODE_GLOBAL_DIR` 与 `BAIZE_INTERNAL_TOKEN`。
8. 每场景套 `run_scenario` 容错（单场景异常不阻断后续场景取证）。
