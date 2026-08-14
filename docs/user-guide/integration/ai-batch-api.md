# AI 批任务对外 API

## 1. 概述

这套接口把「AI 批任务」（详见 `docs/user-guide/ai/`）能力开放给外部系统：提交一批文件 + 一段 prompt，平台为**每个文件**开一个隔离的 AI 会话去处理，你之后轮询批任务状态，处理完成后一次性取回每个文件各自的处理结果。

典型场景：外部系统有一堆文档/表格需要 AI 逐个分析或改写，不想接入 OpenCode，也不想在界面上手工创建批任务。

**Base URL：**

```
http://<host>:<port>/api/v1/ai-batches
```

生产环境经 `proxy.py` 反向代理，端口为 `8080`（即 `http://<host>:8080/api/v1/ai-batches`）。

> 后端蓝图内部注册在 `/v1/ai-batches`（不带 `/api` 前缀），是因为 `proxy.py` / Vite 的公网契约统一是「`/api/X` → 后端 `/X`」，会自动剥掉 `/api`。**外部调用方一律使用带 `/api` 前缀的路径**，即本文档给出的 `/api/v1/ai-batches/...`。

**数据格式：** 除上传接口是 `multipart/form-data` 外，其余接口请求体/响应体均为 `application/json`。

**认证方式：** 通过 `X-API-Key` 请求头传递 API Key，与 [open-api.md](./open-api.md) 的数据集合接口共用同一套密钥体系和请求头。

**与 open-api.md 的关系：** `/api/v1` 这个 Base Path 下同时挂了两类接口——[open-api.md](./open-api.md) 描述的数据集合读写接口，以及本文档描述的 AI 批任务接口（`/api/v1/ai-batches/*`）。两者共用 API Key 鉴权机制，但**权限维度不同**：数据集合接口按「页面是否开放 API 访问」授权；AI 批任务接口按「密钥是否绑定用户」授权（见下一节），且所有批任务严格按创建它的密钥隔离。

---

## 2. 前提：密钥必须绑定用户（新建密钥）

AI 批任务会以某个用户的身份运行 AI 会话、占用其额度、把任务记录关联到该用户。因此密钥必须**绑定创建者**（`owner_user_id`）才能调用这套接口。

⚠️ **本功能上线前创建的存量密钥没有绑定关系**（`owner_user_id` 为空），拿它们调用任何一个 `/api/v1/ai-batches/*` 接口都会返回：

```json
{ "error": "该密钥未绑定用户，请在密钥管理中重新创建" }
```

HTTP 状态码为 `403`。**解决办法：去密钥管理页重新创建一个新密钥**（新建的密钥会自动绑定当前登录用户），旧密钥无法通过编辑补上绑定关系。

新建路径：管理员登录系统 → 「系统配置 → 数据工具 → Open API」→ 「创建 API Key」。新建的密钥同时可用于本文档的批任务接口和 `open-api.md` 的数据集合接口。

---

## 3. 完整流程（可直接复制运行）

```bash
API_KEY="cm_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"   # 换成密钥管理页新建的密钥
BASE_URL="http://localhost:8080/api/v1/ai-batches"

# 1. 上传文件（一次可传多个，字段名固定为 files）
curl -s -X POST \
  -H "X-API-Key: $API_KEY" \
  -F "files=@./report1.pdf" \
  -F "files=@./report2.docx" \
  "$BASE_URL/uploads" | tee /tmp/upload.json | jq

# 响应形如：
# {
#   "files": [
#     { "name": "report1.pdf",  "path": "batch-staging/<userId>/<uploadId>/report1.pdf" },
#     { "name": "report2.docx", "path": "batch-staging/<userId>/<uploadId>/report2.docx" }
#   ]
# }
# path 是不透明字符串，原样透传给下一步即可，不要自行拼接或修改。

# 2. 用上一步返回的 files 数组创建批任务
FILES=$(jq -c '.files' /tmp/upload.json)
curl -s -X POST \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"季度报告分析\",\"prompt\":\"请总结每份文档的核心结论，并列出风险点。\",\"files\":$FILES}" \
  "$BASE_URL" | tee /tmp/create.json | jq

# 响应形如：{ "batchId": "b1c2...", "status": "pending", "total": 2 }
BATCH_ID=$(jq -r '.batchId' /tmp/create.json)

# 3. 轮询状态直到进入终态（completed / partial / failed）
while true; do
  STATUS=$(curl -s -H "X-API-Key: $API_KEY" "$BASE_URL/$BATCH_ID" | jq -r '.status')
  echo "当前状态: $STATUS"
  case "$STATUS" in
    completed|partial|failed) break ;;
  esac
  sleep 10
done

# 4. 取回每个文件的处理结果
curl -s -H "X-API-Key: $API_KEY" "$BASE_URL/$BATCH_ID/results" | jq
```

没有完成回调（webhook）、没有 SSE 推送，**只能轮询**。建议轮询间隔 5~15 秒，进入 `completed`/`partial`/`failed` 任一终态后停止轮询并调用 `/results`。

---

## 4. 端点参考

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/ai-batches/uploads` | 上传文件到暂存区，拿到创建批任务要用的 `files` 数组 |
| POST | `/api/v1/ai-batches` | 创建批任务（每个文件对应一个子任务/AI 会话） |
| GET | `/api/v1/ai-batches` | 分页列出本密钥创建的所有批任务 |
| GET | `/api/v1/ai-batches/{batchId}` | 查询单个批任务的状态与进度 |
| GET | `/api/v1/ai-batches/{batchId}/results` | 取回每个文件的处理结果 |
| DELETE | `/api/v1/ai-batches/{batchId}` | 删除批任务（不可逆） |
| POST | `/api/v1/ai-batches/{batchId}/retry-failed` | 把批任务中失败的子任务重置为待处理，交由后台重跑 |
| POST | `/api/v1/ai-batches/{batchId}/append` | 向已有批任务追加文件（任何状态都可追加） |
| GET | `/api/v1/ai-batches/{batchId}/file-records` | 获取每个子会话被自动记录的新增/修改文件 |
| POST | `/api/v1/ai-batches/{batchId}/import` | 把子会话工作区里被记录过的文件按需导入系统 data_files |

以上全部接口都要求请求头携带 `X-API-Key`，且密钥必须已绑定用户（见第 2 节），否则返回 401/403。

---

### 4.1 上传文件

```
POST /api/v1/ai-batches/uploads
X-API-Key: cm_xxx
Content-Type: multipart/form-data

files = <文件1>   # 必填，字段名固定为 files，可重复出现以一次上传多个
```

**响应 — 201**

```json
{
  "files": [
    { "name": "report1.pdf", "path": "batch-staging/u-123/a1b2c3d4e5f6g7h8/report1.pdf" }
  ]
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `files[].name` | string | 经过安全化处理后的文件名（做过特殊字符清理） |
| `files[].path` | string | 不透明的暂存路径，创建批任务时原样传回，不要自行修改；服务端会校验它确实落在本密钥属主的暂存目录下 |

**限制**：单文件最大 20 MB；单次请求所有文件累计最大 100 MB（按上传顺序累加，一旦超限当次请求整体失败，已写入磁盘的部分文件也不会被返回）。另有一道前置门：整个请求体（含 multipart 编码开销）超过 101 MB 时直接返回 **413**，服务端不会解析请求体——所以请自行按 100 MB 分批上传，不要指望超大请求被逐个文件地检查。

**暂存文件的清理规则**（请完整读完再设计集成流程）：

- 暂存目录保留 **24 小时**，按目录的最后修改时间计算，**不看它是否已经被某个批任务引用**。创建批任务时文件不会被搬走——真正的复制发生在后台开始执行该子任务的那一刻。所以「上传 → 24 小时后才创建批任务」和「创建了批任务但 24 小时内一直没轮到执行」这两种情况，暂存文件都可能已经被清掉。前者会在创建时被拦下（返回 400，见 4.2）；后者会让对应子任务执行失败（`failed`，`error` 说明输入文件已不存在）。**结论：拿到 `path` 后尽快创建批任务，不要把 `path` 存起来隔天再用。**
- 清理由下一次调用 `/uploads` 顺带触发，不需要额外操作，也没有独立的定时任务。
- ⚠️ 清理是**按用户维度扫描整个暂存目录**的。API 密钥的暂存空间和该密钥所属用户在**界面上**创建批任务时用的暂存空间是同一棵目录树，因此一次 API 上传触发的清理，也会删掉该用户在界面上传了超过 24 小时、还没拿去创建批任务的暂存文件（反之亦然）。

---

### 4.2 创建批任务

```
POST /api/v1/ai-batches
X-API-Key: cm_xxx
Content-Type: application/json
```

**请求体**

```json
{
  "name": "季度报告分析",
  "prompt": "请总结每份文档的核心结论，并列出风险点。",
  "files": [
    { "name": "report1.pdf", "path": "batch-staging/u-123/a1b2c3d4e5f6g7h8/report1.pdf" }
  ],
  "agent": "",
  "model": ""
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 批任务名称 |
| `prompt` | string | 是 | 下发给每个文件对应 AI 会话的处理指令，最长 20000 字符 |
| `files` | array | 是 | 上一步 `/uploads` 返回的 `files` 数组（或其子集），每项需含 `name` 与 `path`；最多 50 个 |
| `agent` | string | 否 | 指定 OpenCode agent 名称；留空/不传使用系统默认 |
| `model` | string | 否 | 指定模型（`<providerID>/<modelID>` 格式）；留空/不传使用系统默认 |

**响应 — 201**

```json
{ "batchId": "b1c2d3e4-...", "status": "pending", "total": 2 }
```

**错误响应**

| HTTP 状态码 | 触发条件 |
|-------------|---------|
| 400 | `name` 或 `prompt` 缺失；`prompt` 超过 20000 字符；`files` 为空/不是数组；`files` 超过 50 个；某个文件项缺 `name`/`path`；`files[].path` 未通过合法性校验（不属于本密钥所属用户、包含 `..`、格式不对等）；`files[].path` 指向的文件**已过期或不存在**（错误信息形如「文件「xxx.pdf」已过期或不存在，请重新调用 /uploads 上传后再创建批任务」） |
| 413 | 请求体超过 1 MB（本接口是 JSON，正常调用远小于此） |

> `files[].path` 必须是调用 `/uploads` 拿到的路径，且必须落在**本密钥所属用户**的暂存目录下。别的用户的路径、或手工构造/篡改的路径（含 `..` 穿越），一律按非法路径拒绝（400），不会读到别人的文件。
>
> ⚠️ 注意这条校验的粒度是**用户**，不是密钥：同一个用户名下的多把密钥共享同一个暂存空间，用密钥 B 上传得到的 `path`，可以用密钥 A 来创建批任务。跨用户才会被拒绝。详见第 8 节。
>
> 服务端还会检查该路径对应的文件**当前是否真的存在**（不只是格式合法），因此超过 24 小时被清理掉的暂存路径会在这一步直接 400，而不是留下一个注定跑空的批任务。

---

### 4.3 列出批任务

```
GET /api/v1/ai-batches?page=1&pageSize=20
```

| 查询参数 | 类型 | 默认值 | 说明 |
|----------|------|--------|------|
| page | integer | 1 | 页码，从 1 开始 |
| pageSize | integer | 20 | 每页条数，最大 100（超过按 100 处理） |

**响应 — 200**

```json
{
  "items": [
    {
      "batchId": "b1c2d3e4-...",
      "name": "季度报告分析",
      "status": "running",
      "total": 2,
      "done": 1,
      "failed": 0,
      "agent": null,
      "model": null,
      "createdAt": "2026-08-10T03:20:00.000Z",
      "completedAt": null
    }
  ],
  "total": 1
}
```

只会返回**用当前这把密钥创建**的批任务（见第 8 节「隔离说明」）。

---

### 4.4 查询批任务详情

```
GET /api/v1/ai-batches/{batchId}
```

**响应 — 200**（字段同 4.3 `items` 数组元素）

```json
{
  "batchId": "b1c2d3e4-...",
  "name": "季度报告分析",
  "status": "completed",
  "total": 2,
  "done": 2,
  "failed": 0,
  "agent": null,
  "model": null,
  "createdAt": "2026-08-10T03:20:00.000Z",
  "completedAt": "2026-08-10T03:24:00.000Z"
}
```

**错误响应**

| HTTP 状态码 | 触发条件 |
|-------------|---------|
| 404 | `batchId` 不存在，**或存在但不是用这把密钥创建的**（不区分这两种情况，均返回 404，防止探测他人批任务是否存在） |

---

### 4.5 取回处理结果

```
GET /api/v1/ai-batches/{batchId}/results
```

**响应 — 200**

```json
{
  "batchId": "b1c2d3e4-...",
  "status": "completed",
  "results": [
    { "name": "report1.pdf",  "status": "completed", "output": "……AI 的完整回复文本……", "error": null },
    { "name": "report2.docx", "status": "failed",     "output": null, "error": "OpenCode 会话超时" }
  ]
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | string | 批任务整体状态，取值同第 5 节 |
| `results[].name` | string | 文件名，取自创建时传入的 `files[].path` 的最后一段（即 `/uploads` 落盘时用的安全化文件名）。**不是**创建时传入的 `files[].name`——如果你传的 `name` 和 `path` 的文件名部分不一致，这里返回的是后者 |
| `results[].status` | string | 该文件对应子任务的状态：`pending` / `running` / `completed` / `failed` |
| `results[].output` | string \| null | AI 处理结果的完整文本；**该子任务 `status` 不是 `completed` 时恒为 `null`**（服务端按状态设门：执行中的半截文本、以及跑到一半超时失败的残留文本，都不会被返回） |
| `results[].error` | string \| null | 失败原因；非 `failed` 状态恒为 `null` |

`results` 数组按创建时 `files` 的顺序返回，可与请求时的顺序一一对应。批任务未到终态时也能调用本接口，只是部分/全部 `output` 仍是 `null`。

**错误响应**：与 4.4 相同（404 不区分不存在/无权限）。

---

### 4.6 删除批任务

```
DELETE /api/v1/ai-batches/{batchId}
```

**响应 — 200**

```json
{ "deleted": true }
```

**⚠️ 不可逆**：会清理该批任务下每个子任务的 AI 会话工作区，并删除数据库记录（含全部处理结果）。删除前如果还需要结果，请先调用 4.5 取回。

**错误响应**：与 4.4 相同（404）。

---

### 4.7 重试失败的子任务

```
POST /api/v1/ai-batches/{batchId}/retry-failed
```

把该批任务中状态为 `failed` 的子任务重置为 `pending`，交由后台重新处理（保留原有 prompt/agent/model 配置）。

**响应 — 200**

```json
{ "retried": 1 }
```

`retried` 是本次实际重置的子任务数量；如果批任务处于终态但没有任何失败的子任务，返回 `{"retried": 0}`（这不是错误）。

**错误响应**

| HTTP 状态码 | 触发条件 |
|-------------|---------|
| 404 | `batchId` 不存在或不属于本密钥 |
| 409 | 批任务当前处于 `pending`/`running`（非终态），此时不允许重试 |

只有批任务处于终态（`completed` / `partial` / `failed`）才允许调用本接口。

---

### 4.8 向已有批任务追加文件

```
POST /api/v1/ai-batches/{batchId}/append
Content-Type: application/json
```

给一个已经存在的批任务再加几个文件，沿用它原有的 `prompt` / `agent` / `model`。适合「同一批处理任务的输入是陆续到齐的」这种场景 —— 不用为后到的文件另建一个批任务、也就不用在自己这边把多个 `batchId` 拼回一组。

**与 `retry-failed` 不同，本接口不要求批任务处于终态**：`pending` / `running` / `completed` / `partial` / `failed` 任何状态都可以追加。

**请求体**

```json
{
  "files": [
    { "name": "report-03.pdf", "path": "batch-staging/<userId>/<uploadId>/report-03.pdf" }
  ]
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `files` | array | 是 | 先调 `/uploads` 拿到的 `files` 数组（或其子集），每项需含 `name` 与 `path`；单次最多 50 个 |

**响应 — 200**

```json
{ "batchId": "b-1", "status": "running", "total": 6, "appended": 1 }
```

| 字段 | 说明 |
|------|------|
| `total` | 追加**之后**批任务的文件总数 |
| `appended` | 本次追加的文件数 |
| `status` | 追加后的批任务状态，必然回到非终态（见下） |

**追加后发生了什么**

- 新子任务以 `pending` 入队，`batch_seq` 从当前最大值往后续，后台 worker 会自动开始处理，无需再做任何调用；
- 批任务的 `total` 增加，状态**从终态回到 `running`**（或 `pending`，取决于是否已有子任务开始跑）；
- **已完成的子任务不受影响**，不会重跑，它们的结果仍留在 `/results` 里；
- 因此调用 `/results` 时你会同时看到旧的 `completed` 子任务和新的 `pending` 子任务。

> ⚠️ 如果你的集成代码用「批任务进入终态」作为整批处理完成的信号，注意追加会让它**退出终态**。在你自己追加之后，需要重新开始轮询等待新的终态。

**错误响应**

| HTTP 状态码 | 触发条件 |
|-------------|---------|
| 400 | `files` 为空、单次超过 50 个、缺少 `name`/`path`、路径不属于本密钥属主、文件已过期或不存在、或追加后超过单批 50 个文件的上限 |
| 404 | `batchId` 不存在或不属于本密钥 |

**示例**

```bash
# 1) 先把新到的文件传到暂存区
curl -X POST http://localhost:8080/api/v1/ai-batches/uploads \
  -H "X-API-Key: $API_KEY" \
  -F "files=@./report-03.pdf"

# 2) 用返回的 path 追加到已有批任务
curl -X POST http://localhost:8080/api/v1/ai-batches/b-1/append \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"files":[{"name":"report-03.pdf","path":"batch-staging/<userId>/<uploadId>/report-03.pdf"}]}'
```

---

### 4.9 获取子会话文件记录

```
GET /api/v1/ai-batches/{batchId}/file-records
```

获取每个子会话工作区中被自动记录的新增/修改文件。这些记录由系统在扫描会话变更文件时自动维护，即使文件后来被还原或删除，记录仍然保留。

**响应 — 200**

```json
{
  "batchId": "b1c2d3e4-...",
  "status": "completed",
  "results": [
    {
      "name": "report1.pdf",
      "seq": 1,
      "status": "completed",
      "files": [
        {
          "path": "output/analysis.md",
          "status": "added",
          "dataFileId": null,
          "firstSeenAt": "2026-08-10T03:22:00.000Z",
          "lastSeenAt": "2026-08-10T03:24:00.000Z"
        }
      ]
    }
  ]
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `batchId` | string | 批任务 ID |
| `status` | string | 批任务整体状态 |
| `results[].name` | string | 子会话对应的文件名（取自 `batch_input_file` 的 basename） |
| `results[].seq` | integer | 子会话的稳定序号 |
| `results[].status` | string | 子会话状态：`pending` / `running` / `completed` / `failed` |
| `results[].files[].path` | string | 工作区中的相对路径 |
| `results[].files[].status` | string | 文件状态：`added` / `modified` |
| `results[].files[].dataFileId` | string \| null | 如果已导入到系统 `data_files`，这里返回文件 ID |
| `results[].files[].firstSeenAt` | string \| null | 首次发现时间（ISO 8601） |
| `results[].files[].lastSeenAt` | string \| null | 最近一次扫描时间（ISO 8601） |

**错误响应**：与 4.4 相同（404）。

---

### 4.10 导入子会话文件到系统

```
POST /api/v1/ai-batches/{batchId}/import
Content-Type: application/json
```

把子会话工作区里被自动记录过的文件按需导入系统 `data_files`，返回文件 ID。只有被自动记录过的路径才能导入（白名单机制），防止借导入端点把工作区里任意文件搬进系统。

**请求体**

```json
{
  "name": "report1.pdf",
  "paths": ["output/analysis.md", "data/results.csv"]
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 否 | 通过文件名定位子会话（与 `seq` 二选一） |
| `seq` | integer | 否 | 通过序号定位子会话（与 `name` 二选一） |
| `paths` | array | 是 | 要导入的文件路径列表（工作区相对路径），单次最多 100 个 |

**响应 — 200**

```json
{
  "batchId": "b1c2d3e4-...",
  "name": "report1.pdf",
  "seq": 1,
  "results": [
    {
      "path": "output/analysis.md",
      "status": "imported",
      "file": {
        "id": "df-abc123",
        "name": "analysis.md",
        "size": 1234,
        "mimeType": "text/markdown",
        "url": "/api/data-files/df-abc123/download"
      }
    },
    {
      "path": "data/results.csv",
      "status": "existing",
      "file": {
        "id": "df-def456",
        "name": "results.csv",
        "size": 5678,
        "mimeType": "text/csv",
        "url": "/api/data-files/df-def456/download"
      }
    }
  ]
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `results[].path` | string | 导入的文件路径 |
| `results[].status` | string | `imported`（新导入）/ `existing`（已存在，返回原 ID） |
| `results[].file` | object | 文件元数据，结构与 `/uploads` 响应一致 |
| `results[].error` | string \| null | 如果导入失败，这里返回错误信息 |
| `results[].code` | string \| null | 错误码：`NOT_RECORDED`（不在记录中）/ `BAD_PATH`（路径无效）/ `FILE_MISSING`（文件已不存在）/ `TOO_LARGE`（文件过大）/ `IMPORT_FAILED`（导入失败） |

**幂等性**：已导入过的文件再次导入会返回 `existing` 状态和原文件 ID，不会重复入库。

**错误响应**

| HTTP 状态码 | 触发条件 |
|-------------|---------|
| 400 | `paths` 缺失或为空数组；单次超过 100 个路径；`name` 和 `seq` 都未提供；子会话没有工作区 |
| 404 | `batchId` 不存在或不属于本密钥；通过 `name` 或 `seq` 找不到对应的子会话 |

**示例**

```bash
# 1) 查看批任务的文件记录
curl -s -H "X-API-Key: $API_KEY" \
  "http://localhost:8080/api/v1/ai-batches/b-1/file-records" | jq

# 2) 导入第一个子会话的两个文件到系统
curl -s -X POST \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"seq": 1, "paths": ["output/analysis.md", "data/results.csv"]}' \
  "http://localhost:8080/api/v1/ai-batches/b-1/import" | jq
```

---

## 5. 状态说明

### 5.1 批任务状态（`status`）

| 状态 | 含义 |
|------|------|
| `pending` | 已创建，子任务尚未开始处理（全部子任务都还是 `pending`） |
| `running` | 至少一个子任务已开始处理，尚未全部结束 |
| `completed` | 全部子任务都成功完成 |
| `partial` | 全部子任务已结束，但有成功也有失败（部分完成） |
| `failed` | 全部子任务都失败 |

`completed` / `partial` / `failed` 均为终态，不会**自行**再变化。但有两个接口会主动把批任务从终态拉回非终态，调用它们之后需要重新轮询：

- `retry-failed`：把失败的子任务打回 `pending`；
- `append`：追加新文件（新子任务以 `pending` 入队）。

### 5.2 子任务状态（`results[].status`）

| 状态 | 含义 |
|------|------|
| `pending` | 排队中，尚未分配到 AI 会话 |
| `running` | AI 正在处理该文件 |
| `completed` | 处理成功，`output` 已可用 |
| `failed` | 处理失败，`error` 说明原因 |

子任务没有 `partial` 状态——`partial` 只出现在批任务整体状态上。

---

## 6. 限制

| 项目 | 上限 |
|------|------|
| 单批文件数 | 50 个 |
| 单个文件大小 | 20 MB |
| 单次上传（`/uploads`）总大小 | 100 MB |
| `prompt` 长度 | 20000 字符 |
| 列表分页 `pageSize` | 100（超过按 100 处理，不报错） |
| `/uploads` 单次请求体 | 101 MB（含 multipart 编码开销，超出直接 413，不解析请求体） |
| 其余接口（JSON）单次请求体 | 1 MB（超出直接 413） |
| 暂存文件保留时间 | 24 小时（按目录最后修改时间算，与是否已创建批任务无关，详见 4.1） |
| `/import` 单次路径数 | 100 个（超出返回 400） |
| 文件记录保留时间 | 无限期（但依赖会话存在，会话删除时记录级联删除） |

---

## 7. 错误码

### 7.1 统一错误格式

```json
{ "error": "错误描述信息" }
```

> 与 `open-api.md` 描述的数据集合接口不同，本套接口的 `error` 文案为**中文**。

### 7.2 错误码汇总

| HTTP 状态码 | 触发条件 |
|-------------|---------|
| 401 | 请求头缺少 `X-API-Key`（`Missing API key`）/ 密钥不存在（`Invalid API key`）/ 密钥已停用（`API key has been revoked`） |
| 403 | 密钥未绑定用户（存量密钥），见第 2 节 |
| 400 | 上传：未提供文件 / 单文件超 20 MB / 累计超 100 MB（另有一条「文件名无效」是代码里的防御性兜底判断，正常调用不会触发，见下方说明）。创建：`name`/`prompt` 缺失、`prompt` 超长、`files` 为空/超过 50 个/字段缺失、`files[].path` 未通过归属校验、`files[].path` 指向的文件已过期或不存在。列表：`page`/`pageSize` 不是整数。追加：`files` 为空/单次超过 50 个/字段缺失、`files[].path` 未通过归属校验或指向的文件已过期不存在、追加后总数超过单批 50 个的上限 |
| 404 | `batchId` 不存在，或存在但不属于本密钥（不泄漏存在性，一律 404 不用 403） |
| 409 | 对处于非终态（`pending`/`running`）的批任务调用 `retry-failed` |
| 411 | 请求使用了分块传输（`Transfer-Encoding: chunked`）、没有 `Content-Length`；本套接口要求请求体带 `Content-Length` |
| 413 | 请求体超过上限（`/uploads` 为 101 MB，其余 JSON 接口为 1 MB）。这道门在**读取/解析请求体之前**就生效（反向代理与应用层各有一道），因此超大请求不会等到读完文件才报错；服务端会连同 `Connection: close` 一起返回并关闭连接，客户端若仍在发送请求体，可能会看到连接被关闭——请以收到的 413 响应为准，不要重试整包上传，先分批到 100 MB 以内 |

> **关于「文件名无效」**：服务端对上传的原始文件名做安全化处理（去除路径、控制字符、非法字符等）后再落盘；该净化逻辑保证总能产出一个非空的文件名（无可用字符时回落为随机名），因此这条 400 在正常调用下不会触发，只是代码里保留的一处防御性兜底判断，这里列出仅为完整对应错误码。

---

## 8. 隔离说明

⚠️ **两件事的隔离粒度不同，别混为一谈：**

| 对象 | 隔离粒度 | 含义 |
|------|---------|------|
| **批任务**（列表/详情/结果/删除/重试） | **密钥** | 每把密钥只能看到、动到它自己创建的批任务 |
| **暂存文件**（`/uploads` 返回的 `path`） | **用户** | 同一用户名下的多把密钥共享同一个暂存空间，可互相使用对方上传的 `path` |

⚠️ 另外，批任务的密钥隔离是「密钥与密钥之间」的，**不是「API 与界面（UI）之间」**。请仔细阅读下面第二点再做安全假设。

- **批任务：密钥与密钥之间严格隔离**：每把 API Key 只能看到、查询、重试、删除**它自己创建的**批任务；用另一把密钥（哪怕同一个用户名下新建的另一把）调用同样接口，看到的列表也不会包含对方的批任务，查/删/重试对方的 `batchId` 一律返回 404。这层隔离由 `api_key_id` 字段实现，只在走本 API 时生效。
- **同一用户的界面会话对该用户名下所有批任务拥有完整读写权限，与批任务的创建来源（API 还是界面手工创建）无关**：API 创建批任务时，记录会落到该密钥绑定的用户名下；界面侧的批任务列表/操作按钮是按登录用户判断权限的，不区分这批任务是不是自己在界面上点出来的。也就是说，只要能登录这个密钥所绑定的账号，就可以在 AI 助手侧边栏里看到这个批任务，并对它做**完整操作**——查看完整对话、编辑 agent/model、追加文件、重试失败、单条子任务重新执行、乃至整批删除，跟界面里手工创建的批任务没有任何区别。
  - 反过来，界面手工创建的批任务（没有关联密钥）对本 API 不可见——这条方向的隔离是成立的，但只有这一个方向。
  - **实践含义**：这套 API **不提供**"防止批任务被账号所有者本人在界面上动到"这种保护；它防的是"别的密钥/别的账号看不到、动不到"。如果业务上要求 API 创建的批任务不能被界面用户随意删除/重试，需要在密钥绑定的账号权限、或调用方自己的流程上做额外约束，本 API 层面没有这个边界。
- **暂存文件：按用户隔离，不按密钥隔离**。上传接口 `/uploads` 返回的 `files[].path` 绑定的是**密钥所属的用户**（路径形如 `batch-staging/<userId>/<uploadId>/<文件名>`），创建批任务时校验的也是「该路径是否落在本密钥所属用户的暂存目录下」。因此：
  - **跨用户会被拒绝**：拿另一个用户名下密钥上传得到的 `path`（或手工构造别人 userId 的路径、用 `..` 往上跳）来创建批任务，一律 400，读不到别人的文件。
  - **同一用户名下的多把密钥共享暂存空间**：用密钥 B 调 `/uploads` 拿到的 `path`，可以用密钥 A 来创建批任务，**不会**被拒绝。如果你打算「给合作方一把密钥、给内部系统另一把密钥，两边上传的文件互不可见」，**这个假设不成立**——只要两把密钥绑的是同一个用户就做不到。需要这种隔离时，请把两把密钥绑定到**不同的用户**（即用不同账号分别创建密钥）。
  - 同理，第 4.1 节提到的 24 小时暂存清理也是按用户维度扫全目录的，会波及同一用户在界面上传的暂存文件。

---

## 9. 注意事项

1. **异步处理，仅支持轮询**：创建批任务后立即返回 `pending`，没有完成回调（webhook）也没有 SSE 推送。集成方需要自行定时轮询 `GET /{batchId}`，直到 `status` 进入终态（`completed`/`partial`/`failed`）再调用 `/results` 一次性取回全部结果。
2. **只有 `completed` 的子任务才有 `output`**：`results[].status` 不是 `completed` 时（`pending`/`running`/`failed`），`results[].output` 一律是 `null`——服务端按状态设门，执行中产生的半截文本、以及跑到一半超时失败留下的残留文本都不会返回。所以不要在轮询未结束时把 `null` 当成"处理结果为空"；也不要指望从 `failed` 的子任务里捞出部分输出，失败的子任务请用 `retry-failed` 重跑。
3. **删除不可逆**：`DELETE /{batchId}` 会清理 AI 会话工作区并删库，无法恢复；确需保留结果的场景，删除前先调 `/results`。
4. **暂存文件有效期 24 小时**：`/uploads` 返回的 `path` 请尽快用于创建批任务，不要存起来隔天再用。超过 24 小时的暂存文件会被清理，此时创建批任务会因文件不存在被 400 拒绝；如果批任务已创建但排队超过 24 小时才轮到执行，对应子任务会因输入文件已被清理而 `failed`。清理规则的完整说明见 4.1。
5. **`retry-failed` 只重置失败的子任务**：不会重跑已成功的子任务，也不会修改批任务的 prompt/agent/model；如需更换 prompt 重新处理，需要重新走一遍上传 + 创建流程。
6. **`append` 会让批任务退出终态**：追加的新子任务以 `pending` 入队，批任务状态从 `completed`/`partial`/`failed` 回到 `running`。如果你的集成代码把「进入终态」当作整批完成的信号，追加之后要重新开始轮询。已完成的子任务不会被重跑，追加沿用批任务原有的 prompt/agent/model——**追加不能换 prompt**，需要换就新建一个批任务。
7. **文件记录是自动维护的**：`/file-records` 返回的文件列表由系统在扫描会话变更文件时自动记录，不需要手动创建。记录会持久化保存，即使文件后来被还原或删除，记录仍然保留。但记录依赖于会话存在，会话删除时记录会级联删除。
8. **导入是幂等的**：`/import` 端点支持幂等导入，已导入过的文件再次导入会返回 `existing` 状态和原文件 ID，不会重复入库。只有被自动记录过的路径才能导入（白名单机制），防止借导入端点把工作区里任意文件搬进系统。
9. **导入文件大小限制**：导入的文件大小受系统 `data_files` 的限制（单文件 20 MB），超过限制会返回 `TOO_LARGE` 错误。

---

## 10. 常见问题

**Q: 用之前创建的旧密钥调用一直返回 403，什么原因？**

本功能上线前创建的密钥没有绑定用户，去密钥管理页新建一个密钥即可，详见第 2 节。

**Q: 能不能像 open-api.md 里的数据集合接口一样按 `collection` 过滤批任务？**

不能。批任务只按创建它的密钥隔离，列表接口（4.3）没有额外的过滤参数。

**Q: 批任务一直卡在 `running` 不结束怎么办？**

先确认没有子任务卡在 `pending`（说明后台worker尚未处理到）；如果长时间无进展，可联系管理员在系统侧核实 AI 会话是否异常。批任务本身没有超时自动失败的机制暴露给外部 API。

**Q: `retry-failed` 返回 `{"retried": 0}` 算错误吗？**

不算。这表示批任务已是终态但没有失败的子任务可重试（例如已经 `completed`），HTTP 状态码仍是 200。

**Q: 能不能一次创建批任务时传入还没上传过的文件？**

不能。`files[].path` 必须来自 `/uploads` 的返回值（且必须属于本密钥所属用户），不能自行编造路径；服务端还会检查该文件当前确实存在。

**Q: 我们给合作方和内部系统各发了一把密钥（同一个账号创建的），两边上传的文件互相看得到吗？**

看得到。暂存文件是按**用户**隔离的，同一账号名下的多把密钥共享同一个暂存空间，一把密钥上传的 `path` 另一把可以直接拿去创建批任务。需要真正隔离时请用**不同账号**分别创建密钥。批任务本身仍是按密钥隔离的（互相看不到），详见第 8 节。

**Q: 通过 API 创建的批任务，登录界面的账号本人能不能看到、能不能删？**

能看到，也能删——而且能重试、能改 agent/model、能追加文件、能重新执行单条子任务，权限和界面手工创建的批任务完全一样。批任务的密钥隔离只挡「别的密钥/别的账号」，挡不住「密钥所绑定账号本人在界面上的操作」，详见第 8 节。

**Q: `/file-records` 返回的文件列表是实时的吗？**

不是实时的。文件记录是在系统扫描会话变更文件时自动维护的，记录的是历史出现过的文件，即使文件后来被还原或删除，记录仍然保留。如果需要查看当前工作区的实时文件状态，请使用 `/results` 端点。

**Q: `/import` 端点能导入任意文件吗？**

不能。只有被系统自动记录过的文件才能导入（白名单机制），这是为了防止借导入端点把工作区里任意文件搬进系统。如果尝试导入未记录的文件，会返回 `NOT_RECORDED` 错误。

**Q: 导入的文件存储在哪里？**

导入的文件存储在系统的 `data_files` 中，可以通过 `/api/data-files/{fileId}/download` 端点下载。文件 ID 在导入响应中返回。

**Q: 文件记录会占用额外的存储空间吗？**

文件记录只存储文件路径和元数据，不存储文件内容本身，因此占用的存储空间很小。文件内容仍然存储在会话工作区中，直到会话被删除。

**Q: 如果批任务被删除，文件记录会怎样？**

批任务删除时，会话工作区会被清理，但文件记录本身不会立即删除。文件记录依赖于会话存在，如果会话被删除，文件记录会级联删除。但批任务删除不会直接删除文件记录，除非批任务删除导致会话被删除。
