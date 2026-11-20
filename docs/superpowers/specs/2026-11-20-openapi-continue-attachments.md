# Open API 继续对话带附件（continue files）— 设计与实施记录

日期：2026-11-20　状态：已实施

## 背景与结论

两条「继续对话」通道（Open API `POST /v1/ai-batches/{batchId}/sessions/{childId}/continue`、
接管页 `POST /external/.../session/messages`）原本都只收纯文本 `prompt`，无法给续跑轮次补充材料。
本期把 **Open API continue 端点**扩展出可选 `files` 附件参数，python-client 同步适配；
**接管页明确排除**——它是公开令牌通道，开公开文件上传是新的攻击面，另立项再议。

## 契约

请求体新增可选 `files`：

```json
{ "prompt": "…", "files": [{ "name": "增量.csv", "path": "batch-staging/<owner>/<uploadSession>/<file>" }] }
```

- 完全复用既有暂存基建：`path` 来自 `POST /v1/ai-batches/uploads`（24h TTL，无独立 fileId），
  校验复用 `_validate_files`（形态 / 路径归属当前 key 的 owner / 文件真实存在），
  上限复用 `get_max_files_per_batch()`，错误码与 create/append 一致（400 INVALID_ARGUMENT）。
- `files` 缺省/为 null = 纯文本续跑，旧行为逐字节不变（测试锁定仓储层收到 `files=None`）。
- 202 响应形状不变。

## 数据与执行链路

| 层 | 改动 |
|----|------|
| 存储 | `ai_chat_sessions.continue_files JSONB`（形态同 `input_files`）。迁移三处落：`migrations/2026_11_20_batch_continue_files.py` + `db_schema/ai_scan.py` 镜像 + `app.py` 显式 boot 注册 |
| 仓储 | `batch_repo.continue_child(..., files=None)`：两个 UPDATE 分支（paused-repark / terminal）都写 `continue_files=Json(files) if files else None`——新一轮不带文件时顺带清残留，与 `continue_prompt` 同生命周期。内部路由/接管页调用方零改动（可选参数） |
| 路由 | `session_continue` 解析并校验 `files`，透传给仓储；`log_api_operation` 描述带附件数 |
| worker | ① 抽 `_import_staged_files(ws, paths)`（`_prepare_workspace` 复制循环原样搬出，首跑行为逐字节不变）；② continue 分支消费 `continue_files`：工作区检查通过后复制进**既有**工作区 `uploads/`，`FileNotFoundError` → 落 failed（错误指明附件过期需重新上传），绝不静默发出无附件的 prompt；③ 新增 `_with_continue_files_hint`（措辞区分首轮「本任务的输入文件」：「**本轮**继续对话的附件已放在工作区 uploads/…」），与 `_persist_user_prompt` 同时序生效；④ claim 时清空 UPDATE 改为 `continue_prompt=NULL, continue_files=NULL`（防重发语义对齐） |

## 明确不做

- **接管页 / 内部继续对话路由不带附件**（公开通道攻击面 / UI 无上传入口）。
- 附件导入是复制语义（与首跑一致），不消费暂存文件，依赖 24h TTL 惰性清扫。
- `batch_input_file`（子会话身份列）不动——附件是轮次级概念，不改变 childId 解析。

## 测试

- 路由：`test_open_api_batches_sessions.py::TestSessionContinue` 新增 7 例
  （202 带 files 透传 / 不带 files → None 兼容锁 / 空数组 / 非 list / 超上限 / 他人暂存路径 / 过期不存在）。
- 真库：`TestContinueChild` 新增 2 例（JSONB 落库形状 / 不带 files 清残留）。
- 引擎：`test_batch_engine.py` 新增 2 例（continue 轮附件真复制进 tmp 工作区 + prompt 带提示 + 消费后清空；
  暂存缺失 → failed 且 `send_message` 未被调用）；`test_batch_input_hint.py` 新增 3 例。
- python-client：`test_client_ai.py` 新增带 files 用例（原有用例锁定不带 files 的 body 形态）。
- E2E：`e2e/ai-full/batch/openapi.spec.ts` @llm-light 契约用例（两轻轮：
  首轮拿 OC 会话 + 附件续跑轮；过期/他人路径 400 探针零消耗；/files 实扫
  证明附件进工作区）。注：bogus agent 的 0-LLM 构造不成立——agent fail-fast
  发生在 create_session 之前，未跑过的子任务没有 OC 会话，continue 退化成
  fresh 跑（continue_prompt 被忽略，既有语义）。`retry-reexecute.spec.ts`
  @llm 真链路用例（open API continue 带附件，附件内容进输出）。
