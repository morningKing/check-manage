# AI 单会话对外 API

## 1. 概述

这套接口是 [ai-batch-api.md](./ai-batch-api.md)（AI 批任务）的轻量兄弟资源：给一句 prompt，不需要文件，创建一个独立的 AI 会话，轮询状态直到拿到回答。

典型场景：外部系统只是想"问一句话、要一个答案"，用批任务的"至少一个文件"模型包一层很别扭。

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

**只有两个端点：创建 + 查状态。** 没有 list、没有 delete、没有 continue（追加一轮对话）、没有 retry。

- 创建之后**没有 SSE 推送**，轮询 `GET /api/v1/ai-sessions/{sessionId}` 是唯一的完成信号获取方式（跟批任务一样）。
- 一个单会话只能问一句话、拿一个答案；需要多轮对话、需要重跑、需要事后管理（列表/删除）的场景，请用批任务 API（可以只传一个文件，或者等这几个能力后续补上单会话版本）。
- 不支持文件输入/输出——单会话没有工作区文件的概念，`prompt` 就是全部输入，回答文本就是全部输出。

## 3. 端点参考

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/ai-sessions` | 创建一个单会话 |
| GET | `/api/v1/ai-sessions/{sessionId}` | 查询状态，完成后拿回答 |

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
  "title": ""
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `prompt` | string | 是 | 下发给 AI 会话的指令，最长 20000 字符（跟批任务的 prompt 上限一致） |
| `agent` | string | 否 | 指定 OpenCode agent 名称；留空/不传使用系统默认。可选值见 `GET /api/v1/ai-batches/agents` |
| `model` | string | 否 | 指定模型（`<providerID>/<modelID>` 格式）；留空/不传使用系统默认。可选值见 `GET /api/v1/ai-batches/models` |
| `title` | string | 否 | 会话标题；不传默认"新会话" |

**响应 — 201**

```json
{ "sessionId": "b1c2d3e4-...", "status": "pending" }
```

**错误响应**

| HTTP 状态码 | 触发条件 |
|-------------|---------|
| 400 | `prompt` 缺失/为空；`prompt` 超过 20000 字符 |
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
  "error": null
}
```

| 字段 | 说明 |
|------|------|
| `status` | `pending`（排队中）/ `running`（AI 处理中）/ `completed`（已完成）/ `failed`（失败） |
| `output` | **只在 `status='completed'` 时非 null**——运行中或失败的会话可能已经落库半截文本，不设这道门会把截断的输出当成结果返回。跟批任务 `results[].output` 同一个安全门。 |
| `error` | **只在 `status='failed'` 时非 null**，可读的失败原因（比如指定了一个 OpenCode 里不存在的 agent） |

**错误响应**

| HTTP 状态码 | 触发条件 |
|-------------|---------|
| 404 | `sessionId` 不存在，或存在但不是用这把密钥创建的（不区分这两种情况，均返回 404，防止探测他人会话是否存在） |

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
  if [ "$STATUS" = "completed" ] || [ "$STATUS" = "failed" ]; then
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
