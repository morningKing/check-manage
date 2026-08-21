# AI 单会话对外 API

## 1. 概述

这套接口是 [ai-batch-api.md](./ai-batch-api.md)（AI 批任务）的轻量兄弟资源：给一句 prompt（可选带上几个文件），创建一个独立的 AI 会话，轮询状态直到拿到回答。

典型场景：外部系统只是想"问一句话、要一个答案"（可能附带几份要 AI 读的文档），用批任务的"每个文件拆一个子会话"模型包一层很别扭——单会话就是所有文件进同一个 AI 会话，不拆分。

两者**共用同一套执行引擎**（后端内部：单会话就是一个没有父批任务、`batch_id` 为空的子会话，由批任务同一个 worker 认领执行），所以：
- 并发上限、超时/僵死检测、失败信号（`status='failed'` + `error`）跟批任务子会话完全一致。
- agent/model 的可选值发现直接复用批任务的 `GET /api/v1/ai-batches/agents`、`GET /api/v1/ai-batches/models`，本文档不重复列。
- 认证方式、密钥必须绑定用户、错误响应格式，均与 `ai-batch-api.md` 第 2 节、第 7 节相同，这里不重复。

**Base URL：**

```
http://<host>:<port>/api/v1/ai-sessions
```

生产环境经 `proxy.py` 反向代理，端口为 `8080`。

## 2. 范围（重要，请先读完再设计集成流程）

**只有三个端点：创建、查状态、取消。** 没有 list、没有 delete、没有 continue（追加一轮对话）、没有 retry。

- 创建之后**没有 SSE 推送**，轮询 `GET /api/v1/ai-sessions/{sessionId}` 是唯一的完成信号获取方式（跟批任务一样）。
- 一个单会话只能问一句话、拿一个答案；需要多轮对话、需要重跑、需要事后管理（列表/删除）的场景，请用批任务 API（可以只传一个文件，或者等这几个能力后续补上单会话版本）。
- **可选支持文件**（`body.files`，见 §3.1）：所有随附文件进入**同一个** AI 会话的工作区，供这一轮 prompt 读取；不像批任务那样按文件拆分成多个子会话。没有独立的"单会话专属"上传端点——暂存复用批任务已有的 `POST /api/v1/ai-batches/uploads`（见下方 §3.1 示例），谁消费这些暂存文件（批任务还是单会话）跟上传接口本身无关。仍然**没有文件下载/工作区浏览端点**——文件只是喂给这一轮 AI 的输入，不像批任务那样有 `/sessions/{childId}/files` 这类事后查看产出文件的能力。

## 3. 端点参考

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/ai-sessions` | 创建一个单会话 |
| GET | `/api/v1/ai-sessions/{sessionId}` | 查询状态，完成后拿回答 |
| POST | `/api/v1/ai-sessions/{sessionId}/cancel` | 请求取消一个单会话 |

### 3.1 创建单会话

```
POST /api/v1/ai-sessions
X-API-Key: cm_xxx
Content-Type: application/json
```

**请求体**

```json
{
  "prompt": "帮我用三句话总结一下敏捷开发的核心理念。",
  "agent": "",
  "model": "",
  "title": "",
  "files": []
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `prompt` | string | 是 | 下发给 AI 会话的指令，最长 20000 字符（跟批任务的 prompt 上限一致） |
| `agent` | string | 否 | 指定 OpenCode agent 名称；留空/不传使用系统默认。可选值见 `GET /api/v1/ai-batches/agents` |
| `model` | string | 否 | 指定模型（`<providerID>/<modelID>` 格式）；留空/不传使用系统默认。可选值见 `GET /api/v1/ai-batches/models` |
| `title` | string | 否 | 会话标题；不传默认"新会话" |
| `files` | array | 否 | 要让 AI 读取的文件，形状与批任务的 `files` 完全一致：`[{"name":..., "path":...}]`。`path` 来自下方"先传文件"步骤的 `POST /api/v1/ai-batches/uploads` 响应，原样传回；不要求这些文件来自同一次 `/uploads` 调用。留空/不传 = 纯文本会话。单次最多 50 个（与批任务 `MAX_FILES_PER_BATCH` 同一个上限），校验规则（路径归属、是否仍存在）与批任务创建接口完全一致 |

**先传文件，再建会话**（不带 `files` 直接跳过这步）：

```bash
API_KEY="cm_xxx"
BASE_URL="http://localhost:8080/api/v1"

# 1. 上传文件到暂存区（跟批任务共用同一个上传接口）
UPLOAD=$(curl -s -X POST -H "X-API-Key: $API_KEY" \
  -F "files=@./report1.pdf" -F "files=@./report2.pdf" \
  "$BASE_URL/ai-batches/uploads")
FILES=$(echo "$UPLOAD" | jq -c '.files')

# 2. 用上一步返回的 files 数组创建单会话
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d "{\"prompt\": \"请总结这两份报告的共同风险点。\", \"files\": $FILES}" \
  "$BASE_URL/ai-sessions"
```

**响应 — 201**

```json
{ "sessionId": "b1c2d3e4-...", "status": "pending" }
```

**错误响应**

| HTTP 状态码 | 触发条件 |
|-------------|---------|
| 400 | `prompt` 缺失/为空；`prompt` 超过 20000 字符；`files` 不是数组；`files` 超过 50 个；某个文件项缺 `name`/`path`；`files[].path` 未通过合法性校验（不属于本密钥所属用户、包含 `..`、格式不对等）；`files[].path` 指向的文件已过期或不存在 |
| 401/403 | 密钥缺失/无效/已停用/未绑定用户 |

### 3.2 查询会话状态

```
GET /api/v1/ai-sessions/{sessionId}
```

**响应 — 200**

```json
{
  "sessionId": "b1c2d3e4-...",
  "status": "completed",
  "title": "新会话",
  "agent": null,
  "model": null,
  "createdAt": "2026-08-18T03:20:00.000Z",
  "lastActiveAt": "2026-08-18T03:20:12.000Z",
  "output": "敏捷开发的核心理念可以概括为三点：……",
  "error": null,
  "files": [],
  "usage": { "durationMs": 8340, "tokensInput": 1200, "tokensOutput": 210, "cost": 0.0041 }
}
```

| 字段 | 说明 |
|------|------|
| `status` | `pending`（排队中）/ `running`（AI 处理中）/ `completed`（已完成）/ `failed`（失败）/ `cancelled`（已被调用方取消，见 3.3） |
| `output` | **只在 `status='completed'` 时非 null**——运行中或失败的会话可能已经落库半截文本，不设这道门会把截断的输出当成结果返回。跟批任务 `results[].output` 同一个安全门。 |
| `error` | **只在 `status='failed'`/`status='cancelled'` 时非 null**，可读的失败原因（`cancelled` 时是固定的"已被调用方取消"说明） |
| `files` | 创建时随附的文件列表，`[{"name": "report1.pdf"}]`；没有文件时为 `[]`。只回显文件名，不回显内部暂存路径 |
| `usage` | 该会话的用量汇总（`durationMs`/`tokensInput`/`tokensOutput`/`cost`），还没有可用数据时为 `null`。仅供成本核算参考，不是精确计费依据 |

**错误响应**

| HTTP 状态码 | 触发条件 |
|-------------|---------|
| 404 | `sessionId` 不存在，或存在但不是用这把密钥创建的（不区分这两种情况，均返回 404，防止探测他人会话是否存在） |

### 3.3 取消会话

```
POST /api/v1/ai-sessions/{sessionId}/cancel
```

请求取消这个会话。**协作式取消，不是立即生效**：还在排队（`pending`）的会话会在下一轮调度时直接标记为 `cancelled`，从未真正开始；正在执行（`running`）的会话会在下一次轮询时（通常几秒内）中止底层 AI 会话，随后标记为 `cancelled`——调用后仍需按 3.2 轮询确认。已终态（`completed`/`failed`/`cancelled`）的会话无法再取消。

**响应 — 200**（字段同 3.2）

```json
{
  "sessionId": "b1c2d3e4-...", "status": "cancelled",
  "title": "新会话", "agent": null, "model": null,
  "createdAt": "2026-08-18T03:20:00.000Z", "lastActiveAt": "2026-08-18T03:20:12.000Z",
  "output": null, "error": "已被调用方取消", "files": [], "usage": null
}
```

**错误响应**

| HTTP 状态码 | 触发条件 |
|-------------|---------|
| 404 | `sessionId` 不存在或不属于本密钥 |
| 409 | 会话已处于终态（`completed`/`failed`/`cancelled`），无法取消 |

## 4. 轮询建议

跟批任务一致：建议轮询间隔 3~10 秒（单会话通常比批任务单个文件更快出结果），进入 `completed`/`failed` 任一终态后停止轮询。

## 5. 示例

```bash
API_KEY="cm_xxx"
BASE_URL="http://localhost:8080/api/v1/ai-sessions"

SESSION_ID=$(curl -s -X POST "$BASE_URL" \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"prompt": "帮我用三句话总结一下敏捷开发的核心理念。"}' \
  | jq -r .sessionId)

while true; do
  RESP=$(curl -s -H "X-API-Key: $API_KEY" "$BASE_URL/$SESSION_ID")
  STATUS=$(echo "$RESP" | jq -r .status)
  if [ "$STATUS" = "completed" ] || [ "$STATUS" = "failed" ] || [ "$STATUS" = "cancelled" ]; then
    echo "$RESP" | jq .
    break
  fi
  sleep 3
done
```

## 6. 常见问题

**Q: 能不能在单会话上继续追问？**

不能，这一版只有创建和查状态。需要多轮对话请用批任务 API 的 `continue` 端点（对子会话继续对话，保留历史上下文）。

**Q: `error` 文案是什么语言？**

中文——跟 `ai-batch-api.md` 同一个"AI 相关对外端点"家族的既有惯例，不是 `open-api.md` 数据集合接口的英文惯例。

**Q: 能不能传文件让 AI 处理？**

能。`body.files` 可选，形状和用法跟批任务的 `files` 一致，先调批任务的 `POST /api/v1/ai-batches/uploads` 拿到 `path`，再传给单会话的创建接口，见 §3.1 的两步示例。区别是批任务按文件拆成 N 个子会话，单会话是所有文件进**同一个** AI 会话——适合"这几份文档要放在一起综合分析"的场景，而不是"每份文档各自独立处理"。

**Q: 传了文件之后还能查看/下载 AI 产出的文件吗？**

不能。单会话没有批任务那套 `/sessions/{childId}/files`/`/files/download` 端点——`files` 只是这一轮的输入，回答只能通过 `output` 文本字段拿到。需要文件产出物管理，请用批任务 API。

**Q: 发起后能取消吗？**

能，见 §3.3。是协作式取消（下一轮调度/轮询时才真正生效），不是同步中断，所以取消请求返回后仍要用 §3.2 轮询确认状态变成 `cancelled`。
