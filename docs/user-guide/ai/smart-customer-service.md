# 智能客服（Smart Customer Service）Phase 1 使用文档

> 适用阶段：Phase 1（后端 API）已上线；Stage ②（访客自助页 `/kefu/<slug>`）已交付。可嵌入 widget（`kefu-widget.js`）和 `KefuManager.vue` 管理界面属后续阶段（Phase 3），尚未上线。
>
> 配套规格见 `docs/superpowers/specs/`（智能客服设计文档）。

---

## 1. 这是什么

智能客服是一个**面向公开访客的、无需登录**的对话入口，独立于内部管理系统的 JWT 鉴权体系。

核心特点：

- **公开匿名**：访客通过 `X-Visitor-Id` 头标识身份，无需账号或 JWT Token。
- **OpenCode Agent 驱动**：每次对话都在 OpenCode 运行时内运行，可调用 MCP 工具读取系统数据页。
- **RBAC 只读钳制**：Agent 运行在固定 bot 用户（`kefu-bot`）身份下，其角色 `kefu-guest` 的 `default_page_access='none'`——默认看不到任何数据页，只有将 `kefu-guest` 加入数据页**菜单的 `roles` 列表**后，Agent 才可读该数据页，且全程只读。
- **安全护栏**：每个会话工作区注入不可覆盖的系统边界声明（AGENTS.md），防止越权。
- **多实例**：可为不同场景（售前、售后、技术支持……）创建多个实例，通过 `slug` 区分。

Phase 1 后端已暴露全套 JSON API（见第 3 节）；Stage ② 已交付访客自助全页（见第 9.5 节）。可嵌入 `kefu-widget.js` 和 `KefuManager.vue` 管理界面将在 **Phase 3** 落地。

---

## 2. 前置条件

- 当前用户有 `admin.kefu` 权限（在 `/admin/roles` 为对应角色开启该能力开关）。
- 后端（Flask）+ OpenCode（`opencode serve`）+ MCP 服务 均已运行。
- 已获取管理员 JWT Token：

```bash
TOKEN=$(curl -s -X POST localhost:3002/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}' | python -c "import sys,json;print(json.load(sys.stdin)['token'])")
```

---

## 3. 创建客服实例

**API：** `POST /admin/kefu/instances`（需 `admin.kefu` 权限）

### 3.1 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `slug` | string | 是 | URL 标识符，小写字母/数字/连字符，1–64 字符（如 `presale`）；全局唯一 |
| `name` | string | 是 | 实例显示名称（如 `售前客服`） |
| `agent` | string | 否 | 指定 OpenCode Agent 名称；留空 = 使用全局默认 |
| `model` | string | 否 | 指定模型（如 `anthropic/claude-3-5-sonnet-20241022`）；留空 = 全局默认 |
| `system_prompt` | string | 否 | 实例的人设与业务边界说明；会拼接在不可覆盖的安全护栏之后 |
| `welcome_message` | string | 否 | 访客打开对话时展示的欢迎语 |
| `guided_questions` | array of string | 否 | 引导性预设问题列表，前端用于快速提问按钮 |
| `branding` | object | 否 | 品牌定制（颜色、Logo 等，前端 Phase 3 使用） |
| `rate_limit` | object | 否 | 限速配置，见第 5 节 |
| `enabled` | boolean | 否 | 默认 `true`；置为 `false` 后访客建会话返回 403 |

### 3.2 curl 示例

```bash
# 创建一个售前客服实例
curl -s -X POST localhost:3002/admin/kefu/instances \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "slug": "presale",
    "name": "售前客服",
    "system_prompt": "你是公司售前助手，负责解答产品功能、定价和部署方式相关问题。不要讨论竞品。",
    "welcome_message": "你好！我是售前助手，有任何购买或评估问题欢迎提问 😊",
    "guided_questions": ["支持私有化部署吗？", "有哪些定价套餐？", "怎么申请试用？"],
    "rate_limit": {"perMinute": 10, "perDay": 200}
  }'
```

成功响应（201）：

```json
{
  "id": "kf_a1b2c3",
  "slug": "presale",
  "name": "售前客服",
  "enabled": true,
  ...
}
```

### 3.3 其他管理接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET`    | `/admin/kefu/instances`        | 列出所有实例 |
| `GET`    | `/admin/kefu/instances/<id>`   | 获取单个实例 |
| `PATCH`  | `/admin/kefu/instances/<id>`   | 更新实例（字段与 POST 一致，按需传入） |
| `DELETE` | `/admin/kefu/instances/<id>`   | 删除实例 |

> **注意**：Phase 3 的 `KefuManager.vue` 管理页（`/admin/kefu`）将提供可视化界面；Phase 1 阶段通过以上 API 操作。

---

## 4. 授予数据页读权限

智能客服 Agent 默认**看不到任何数据页**。要让 Agent 能查询某个业务数据页（如"产品手册"、"常见问题库"），需将 `kefu-guest` 加入该数据页**菜单的 `roles` 列表**——这是 MCP `query_collection` 工具执行的实际鉴权点（见 `mcp-server/tools/query_collection.py:78-81`：工具查询 `SELECT roles FROM menus WHERE page_id='page-'+collection`，若 `ctx.role` 不在该列表则拒绝）。

**授权步骤：**

1. 登录管理端，进入 **系统配置 → 菜单管理**（或直接在侧边栏右键目标数据页菜单项点「编辑」）。
2. 找到目标数据页对应的菜单项，点击「编辑」。
3. 在「可见角色」（roles）字段中，添加 `kefu-guest`。
4. 保存。

重复以上步骤，按需为多个数据页逐一授权。

> **注意 — `default_page_access` 与 MCP 无关**：`kefu-guest` 角色的 `default_page_access='none'` 控制的是 Flask 侧动态数据 CRUD API（`/api/<collection>`）的默认可见范围，与 MCP `query_collection` 的鉴权逻辑相互独立。MCP 数据查询的门卫始终是**菜单 `roles`**，不是 `role_permissions` 表。
>
> **不要授予 `kefu-guest` 写/删权限**；`kefu-guest` 的只读定位不应改变。未在菜单 roles 中授权的数据页，Agent 调用 `query_collection` 时会被 MCP 层硬拒，不会泄露数据。

---

## 5. 限速配置

`rate_limit` 字段为对象，支持以下子字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `perMinute` | number | 每分钟最大请求数（按 `实例ID + visitor_id + IP` 计数）；0 或不填 = 不限 |
| `perDay` | number | 每天最大请求数（同维度计数）；0 或不填 = 不限 |

超限时 API 返回 `429 Too Many Requests`，消息体为 `{"error": "请求过于频繁，请稍后再试"}`。

---

## 6. 访客 API（公开入口）

以下接口**无需鉴权**，通过 `X-Visitor-Id` 头标识访客（Phase 3 前端将自动写入 localStorage 中的匿名 ID）。

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET`  | `/kefu/i/<slug>`                  | 获取实例公开配置（名称、欢迎语、引导问、品牌） |
| `POST` | `/kefu/i/<slug>/sessions`         | 创建会话（需 `X-Visitor-Id` 头，返回 `sess_*` id） |
| `GET`  | `/kefu/sessions/<sid>/messages`   | 获取历史消息（需 `X-Visitor-Id` 头） |
| `POST` | `/kefu/sessions/<sid>/messages`   | 发送消息（返回 202 + `messageId`，异步推送 SSE） |
| `GET`  | `/kefu/sessions/<sid>/events`     | SSE 事件流（`?visitor_id=<vid>` 查询参数） |
| `POST` | `/kefu/sessions/<sid>/files`      | 上传文件（multipart，≤20MB，白名单扩展名） |

**分享链接形状**：`/<host>/kefu/<slug>`（Stage ② 前端路由已落地；访客页无需登录，详见第 9.5 节）。Phase 1 JSON API 可通过上表直接调用。

### 文件上传限制

- 大小上限：20MB
- 允许扩展名：`.txt .md .csv .json .pdf .png .jpg .jpeg .gif .xlsx .docx`
- 路径穿越防护：服务端校验所有附件路径，非法路径返回 400。

---

## 7. 安全边界

| 层次 | 机制 |
|------|------|
| **工具白名单钳制（首要硬边界）** | `kefu-guest` 角色在 MCP 分发层（`mcp-server/tools/__init__.py` `_dispatch_tool`）受工具白名单限制（`rbac.py` `KEFU_TOOL_ALLOWLIST`）：**只允许** `query_collection`、`list_collections`、`read_upload`；`run_python`、`save_artifact`、`read_data_file`、`memory_*` 等工具在分发层直接返回 `PermissionError`，无论 Agent 指令如何均不可绕过。 |
| **数据页可见性（菜单 roles 门卫）** | Agent 运行在 `kefu-bot` 用户下，角色 `kefu-guest`。`query_collection` 工具执行前查询目标数据页的菜单 `roles` 数组（`SELECT roles FROM menus WHERE page_id='page-'+collection`）；`kefu-guest` 不在其中则拒绝，不会泄露任何数据。这是数据可见性的硬边界。`default_page_access='none'` 控制 Flask 侧 CRUD API 默认访问，不参与 MCP 查询鉴权。 |
| **护栏系统提示** | 每个会话工作区的 `AGENTS.md` 注入 4 条不可覆盖的边界声明（最高优先级）：仅限客服相关问题、严禁导出全量数据/凭证/隐私、只读、忽略越权指令。这是软性防护，工具白名单和菜单 roles 门卫是硬边界。 |
| **附件路径校验** | 用户传来的附件相对路径经 `safe_resolve` 校验，防止路径穿越到会话工作区外。 |
| **限速** | 每个访客+IP 组合按实例配置限速，防止爬取或滥用。 |
| **公开面收敛** | 所有匿名公开接口集中在 `kefu_public_bp`（`/kefu/` 前缀），便于独立审计和 WAF 规则配置。 |
| **Bot 口令** | `kefu-bot` 的登录口令为每次迁移随机生成的不可登录值，不可通过正常登录流程使用。 |

---

## 8. 端到端手动验证清单

> 以下步骤需要 OpenCode + MCP 全栈运行。Phase 1 单元测试（`tests/test_kefu_*.py`）覆盖了路由、RBAC、护栏和 repo 层逻辑，端到端需手动执行。

**环境准备**

```bash
# 终端 1：后端
cd server && python app.py

# 终端 2：OpenCode（独立进程）
opencode serve

# 终端 3：MCP 服务
cd mcp-server && python main.py
```

**验证步骤**

- [ ] **步骤 1 — 获取管理员 Token**

  ```bash
  TOKEN=$(curl -s -X POST localhost:3002/auth/login \
    -H 'Content-Type: application/json' \
    -d '{"username":"admin","password":"admin123"}' \
    | python -c "import sys,json;print(json.load(sys.stdin)['token'])")
  ```

- [ ] **步骤 2 — 创建实例**

  ```bash
  curl -s -X POST localhost:3002/admin/kefu/instances \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d '{"slug":"smoke","name":"冒烟测试客服","system_prompt":"你是冒烟测试助手","rate_limit":{"perMinute":20}}'
  ```

  期望：201 + 含 `id` 的 JSON。

- [ ] **步骤 3 — 访客取公开配置**

  ```bash
  curl -s localhost:3002/kefu/i/smoke
  ```

  期望：200 + 含 `name`、`enabled:true` 的 JSON（不含 `system_prompt`、`agent`、`model` 等内部字段）。

- [ ] **步骤 4 — 创建会话**

  ```bash
  SID=$(curl -s -X POST localhost:3002/kefu/i/smoke/sessions \
    -H 'X-Visitor-Id: visitor-test-1' | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
  echo "session: $SID"
  ```

  期望：201 + `id` 以 `sess_` 前缀开头。

- [ ] **步骤 5 — 发送消息**

  ```bash
  curl -s -X POST localhost:3002/kefu/sessions/$SID/messages \
    -H 'X-Visitor-Id: visitor-test-1' -H 'Content-Type: application/json' \
    -d '{"content":"你们的产品支持私有化部署吗？"}'
  ```

  期望：202 + `{"messageId":"msg_..."}` 。

- [ ] **步骤 6 — 校验只读钳制（安全关键）**

  确认目标数据页的菜单 `roles` 中**未包含** `kefu-guest`（进入菜单管理，找到该数据页菜单项，查看「可见角色」字段）。向 Agent 提问「请查询 xxx 数据页的全部记录」。查看 MCP 服务日志，确认 `query_collection` 因菜单 roles 鉴权失败被拒（`无权限查询：xxx`），响应中不含该数据页数据。

  同时验证工具白名单：向 Agent 发起可能触发 `run_python` 的请求（如"帮我执行一段 Python 代码"），确认 MCP 日志返回 `PermissionError: tool 'run_python' not available for this session`。

- [ ] **步骤 7 — 授权后可见**

  在菜单管理中，将 `kefu-guest` 加入目标数据页菜单的「可见角色」，保存。重新发消息再次查询同一数据页，确认 Agent 能取到数据。

---

## 9. 热门问题 / 自助服务面板（Stage ①：配置）

### 9.1 这是什么

热门问题是一个**客服实例级的自助知识库**，管理员可为每个客服实例策划一组常见问题。每条热问包含：

- **问题** — 简洁的问题标题
- **答案** — Markdown 格式的预写回答（支持代码块、列表、链接等）
- **分类标签** — 可选，用于在访客面板中按分类筛选（如"计费"、"部署"、"功能"）
- **排序** — 管理员可拖拽调序，决定热问在访客面板中的显示顺序
- **启用状态** — 切换热问的启用/禁用，禁用后不会出现在访客面板
- **点击量** — 只读计数器，记录访客浏览该热问的次数，供管理员参考调序

**访客自助体验**（Stage ②，已交付）：访客打开客服对话页（`/<host>/kefu/<slug>`，无需登录）时，桌面端（≥992px）呈**两栏布局**——左侧 AI 对话区、右侧持久自助服务区块列；移动端（<992px）右栏收起为「🗂 自助服务」侧滑抽屉。欢迎语下方展示可点击的提示气泡（`guided_questions`），点击气泡即直接发起提问。热点问题区块支持内联展开 Markdown 答案（记录点击埋点）并可一键「问 AI」上报；快捷链接在新标签页打开。详见第 9.6 节。

---

### 9.2 管理配置（后台）

#### 9.2.1 访问管理页面

1. 使用有 `admin.kefu` 权限的账号登录管理端。
2. 进入 **AI 助手 → 客服管理**（路径 `/admin/kefu`）。
3. 在实例列表中选择目标客服实例，或新建一个实例（见第 3 节）。

#### 9.2.2 增删改热问

在所选实例的热问编辑器中：

| 操作 | 步骤 |
|------|------|
| **新增热问** | 点击「新增热问」按钮 → 填写问题标题 → 用 Markdown 编辑器撰写答案 → 可选添加分类标签 → 点击「保存」 |
| **编辑热问** | 点击列表中的目标热问行 → 修改标题/答案/标签 → 点击「保存」 |
| **删除热问** | 点击列表中目标热问行的删除图标 → 确认删除 |
| **调整排序** | 在列表中拖拽热问行，松开时自动提交排序变更 |
| **切换启用** | 点击热问行的启用/禁用切换开关 |

#### 9.2.3 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `question` | string | 是 | 问题标题（如"支持私有化部署吗？"） |
| `answer` | string | 是 | Markdown 格式的答案，可包含代码块、列表、表格、链接等 |
| `category` | string | 否 | 分类标签（如"计费"、"部署"、"功能"）；支持自由输入 |
| `sort_order` | number | — | 排序权重，由拖拽操作自动管理 |
| `enabled` | boolean | — | 是否启用，默认 `true`；禁用后不会出现在公开访客面板 |
| `click_count` | number | — | 只读，记录访客点击次数，每次访客查看该热问答案时自动递增 |

---

### 9.3 公开端点

#### 9.3.1 获取热问列表

**API：** `GET /kefu/i/<slug>/faq`

无需鉴权，返回指定客服实例的所有**启用的热问**，按 `sort_order` 排序。

**响应示例（200）：**

```json
{
  "items": [
    {
      "id": "faq_abc123",
      "question": "支持私有化部署吗？",
      "answer": "# 私有化部署\n\n我们提供...",
      "category": "部署"
    },
    {
      "id": "faq_def456",
      "question": "如何申请试用？",
      "answer": "# 试用申请\n\n请填写表单...",
      "category": "计费"
    }
  ]
}
```

**curl 示例：**

```bash
curl -s localhost:3002/kefu/i/presale/faq
```

---

#### 9.3.2 埋点：记录热问点击

**API：** `POST /kefu/i/<slug>/faq/<id>/click`

无需鉴权。访客查看某个热问的答案时调用，自动递增该热问的 `click_count`。

**请求头：**

| 头 | 说明 |
|----|------|
| `X-Visitor-Id` | 访客身份标识（可选，用于点击溯源） |

**响应（204）：**

无响应体。仅用于统计，不返回数据。

**curl 示例：**

```bash
curl -s -X POST localhost:3002/kefu/i/presale/faq/faq_abc123/click \
  -H 'X-Visitor-Id: visitor-test-1'
```

---

### 9.4 管理 API（后台操作）

以下接口需要 `admin.kefu` 权限（见第 2 节获取 Token）。

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/admin/kefu/instances/<instance_id>/faq` | 列出某实例下的所有热问（含禁用的） |
| `POST` | `/admin/kefu/instances/<instance_id>/faq` | 新增热问 |
| `PATCH` | `/admin/kefu/instances/<instance_id>/faq/<id>` | 编辑单个热问 |
| `DELETE` | `/admin/kefu/instances/<instance_id>/faq/<id>` | 删除单个热问 |
| `PATCH` | `/admin/kefu/instances/<instance_id>/faq/reorder` | 批量重新排序（见下例） |

**新增热问示例：**

```bash
curl -s -X POST localhost:3002/admin/kefu/instances/kf_presale/faq \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{
    "question": "支持私有化部署吗？",
    "answer": "# 私有化部署\n\n我们支持私有化部署...",
    "category": "部署",
    "enabled": true
  }'
```

**重新排序示例：**

```bash
# 按新的 ID 顺序提交
curl -s -X PATCH localhost:3002/admin/kefu/instances/kf_presale/faq/reorder \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{
    "order": ["faq_abc123", "faq_def456", "faq_xyz789"]
  }'
```

---

### 9.5 访客主页配置（提示气泡 + 自助区块）

#### 9.5.1 概述

访客进入客服对话页（`/kefu/<slug>`）时看到的首屏布局，由两部分配置组成：

1. **提示气泡**（`guided_questions`）— 快速引导问题列表，以可点击芯片形式展示在欢迎语下方；访客可点击快速提问。
2. **自助服务区块**（`panel_blocks`）— 有序列表，支持 4 种区块类型：
   - `links` — 快捷入口，配置图标/标签/链接
   - `faq` — 热点问题，关联热问库并限制显示条数
   - `richtext` — 公告/富文本，支持 Markdown 渲染
   - `contact` — 联系方式，配置电话/邮箱/营业时间/微信

访客页对这些配置的新版布局渲染属 **Stage ②**（下阶段实现），本阶段（Stage ①）专注后端存储与管理端配置界面。

#### 9.5.2 管理配置

##### 访问管理页面

1. 使用有 `admin.kefu` 权限的账号登录管理端。
2. 进入 **AI 助手 → 客服管理**（路径 `/admin/kefu`）。
3. 在实例列表中选择目标客服实例。
4. 向下滑动到「主页配置」区域。

##### 配置提示气泡（guided_questions）

在「主页配置」中的「提示气泡」编辑框：

| 操作 | 步骤 |
|------|------|
| **新增气泡** | 在文本框中输入一条提示问题 → 回车或点击「加号」按钮 → 新增行出现在列表下方 |
| **编辑气泡** | 点击列表中的气泡，修改文本 → 回车或失焦自动保存 |
| **删除气泡** | 点击气泡行右侧的「×」删除按钮 → 即时移除 |
| **调整顺序** | 拖拽气泡行调整上下位置（未来 Stage ② 访客页按此顺序显示） |

**示例：**

```
提示气泡列表：
[ 支持私有化部署吗？ ]
[ 如何申请试用？ ]
[ 有哪些定价套餐？ ]
```

##### 配置自助区块（panel_blocks）

在「主页配置」中的「自助区块」编辑器：

###### 新增区块

点击「新增区块」按钮，选择类型，根据类型填写字段：

**① links（快捷入口）**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `title` | string | 是 | 区块标题（如"快速入口"） |
| `enabled` | boolean | 是 | 是否启用，默认 `true` |
| `config.items` | array | 是 | 快捷项列表，每项包含： |
| — `icon` | string | 否 | 图标 URL 或 emoji（如 `:📝:` 或 `https://example.com/icon.svg`） |
| — `label` | string | 是 | 链接文本（如"下载手册"） |
| — `url` | string | 是 | 完整链接 URL（如 `https://example.com/manual.pdf`） |

**示例结构：**

```json
{
  "type": "links",
  "title": "快速入口",
  "enabled": true,
  "config": {
    "items": [
      { "icon": "📥", "label": "下载手册", "url": "https://example.com/manual.pdf" },
      { "icon": "💬", "label": "加入社区", "url": "https://example.com/community" }
    ]
  }
}
```

**② faq（热点问题）**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `title` | string | 是 | 区块标题（如"热点问题"） |
| `enabled` | boolean | 是 | 是否启用，默认 `true` |
| `config.limit` | number | 否 | 最多展示几条热问，默认 5；设为 0 = 不限 |

> **关联数据**：此区块会自动关联当前实例下的所有**启用的热问**（通过 `GET /kefu/i/<slug>/faq` 获取），访客页将按 `sort_order` 排序、并限制在 `limit` 条以内。

**示例结构：**

```json
{
  "type": "faq",
  "title": "热点问题",
  "enabled": true,
  "config": {
    "limit": 5
  }
}
```

**③ richtext（公告/富文本）**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `title` | string | 是 | 区块标题（如"平台公告"） |
| `enabled` | boolean | 是 | 是否启用，默认 `true` |
| `config.markdown` | string | 是 | Markdown 格式的内容，支持标题、列表、代码块、表格、链接等 |

**示例结构：**

```json
{
  "type": "richtext",
  "title": "平台公告",
  "enabled": true,
  "config": {
    "markdown": "# 最新更新\n\n## v2.0 已发布\n\n- 新增私有化部署模式\n- 支持企业 SSO\n\n[查看详情](https://example.com/release-notes)"
  }
}
```

**④ contact（联系方式）**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `title` | string | 是 | 区块标题（如"联系我们"） |
| `enabled` | boolean | 是 | 是否启用，默认 `true` |
| `config.phone` | string | 否 | 电话号码（如 `+86-10-1234-5678`） |
| `config.email` | string | 否 | 邮箱地址（如 `support@example.com`） |
| `config.hours` | string | 否 | 营业时间（如 `工作日 9:00-18:00`） |
| `config.wechat` | string | 否 | 微信号或公众号二维码 URL（如 `https://example.com/wechat-qr.jpg`） |

**示例结构：**

```json
{
  "type": "contact",
  "title": "联系我们",
  "enabled": true,
  "config": {
    "phone": "+86-10-1234-5678",
    "email": "support@example.com",
    "hours": "工作日 9:00-18:00",
    "wechat": "https://example.com/wechat-qr.jpg"
  }
}
```

###### 编辑与管理区块

| 操作 | 步骤 |
|------|------|
| **编辑区块** | 点击区块卡片 → 修改字段 → 点击「保存」或失焦自动保存 |
| **删除区块** | 点击区块右上角的删除按钮 → 确认删除 |
| **调整顺序** | 拖拽区块卡片调整上下位置（Stage ② 访客页按此顺序渲染） |
| **启用/禁用** | 点击区块的启用/禁用开关 → 禁用后访客页不显示该区块 |

##### 保存配置

修改提示气泡或自助区块后，点击「主页配置」底部的**「保存主页配置」**按钮 → 前端提交 `PATCH /admin/kefu/instances/<id>`，后端存储 `guided_questions` 和 `panel_blocks` 到 `kefu_instances` 表。

#### 9.5.3 公开配置端点

**API：** `GET /kefu/i/<slug>`

无需鉴权，返回指定客服实例的公开配置，其中包括 `guided_questions` 和 `panel_blocks`。

**响应示例（200）：**

```json
{
  "id": "kf_presale",
  "slug": "presale",
  "name": "售前客服",
  "enabled": true,
  "welcome_message": "你好！我是售前助手...",
  "guided_questions": [
    "支持私有化部署吗？",
    "有哪些定价套餐？"
  ],
  "panel_blocks": [
    {
      "id": "blk_1",
      "type": "links",
      "title": "快速入口",
      "enabled": true,
      "config": {
        "items": [
          { "icon": "📥", "label": "下载手册", "url": "https://example.com/manual.pdf" }
        ]
      }
    },
    {
      "id": "blk_2",
      "type": "faq",
      "title": "热点问题",
      "enabled": true,
      "config": { "limit": 5 }
    },
    {
      "id": "blk_3",
      "type": "richtext",
      "title": "平台公告",
      "enabled": true,
      "config": { "markdown": "# 最新更新\n..." }
    },
    {
      "id": "blk_4",
      "type": "contact",
      "title": "联系我们",
      "enabled": true,
      "config": {
        "phone": "+86-10-1234-5678",
        "email": "support@example.com"
      }
    }
  ]
}
```

**curl 示例：**

```bash
curl -s localhost:3002/kefu/i/presale
```

#### 9.5.4 Stage ① + Stage ② 交付状态

**Stage ① 已交付：**

- ✓ 后端存储：`panel_blocks` JSONB 列 + 校验逻辑
- ✓ 管理 API：PATCH 白名单 + 配置读写
- ✓ 管理端界面：提示气泡编辑器 + 区块编辑器（4 种类型完整表单）
- ✓ 公开端点：`GET /kefu/i/<slug>` 返回 `guided_questions` + `panel_blocks`

**Stage ② 已交付（访客页现代化布局，详见第 9.6 节）：**

- ✓ 桌面端两栏布局（≥992px）：左侧 AI 对话 + 右侧持久自助服务区块列
- ✓ 提示气泡：欢迎语下方芯片式快速提问，点击直接发入对话
- ✓ 区块渲染：4 种子组件分别渲染 links / faq / richtext / contact
- ✓ 移动端（<992px）：右侧列隐藏，改为「🗂 自助服务」侧滑抽屉
- 主题定制（品牌色融合）属后续阶段，尚未实现

---

### 9.6 访客主页（Stage ②，已交付）

访客无需登录，直接通过以下链接访问客服对话页：

```
http://<host>/kefu/<slug>
```

例如：`http://example.com/kefu/presale`（slug 为创建实例时设置的标识符）。

#### 桌面端布局（≥992px）

桌面端呈**两栏布局**：

- **左栏 — AI 对话区**：顶部标题栏显示实例名称；欢迎语（Markdown 渲染）下方展示来自 `guided_questions` 的可点击提示气泡；历史消息流；底部输入框 + 发送按钮。
- **右栏 — 持久自助服务区块列**（340px，固定展开）：按 `panel_blocks` 配置顺序从上到下渲染所有已启用区块：快捷入口 / 热点问题 / 公告 / 联系方式。

> 右栏**仅在至少有一个已启用区块时**渲染；否则为纯对话全屏布局，标题栏也不显示「🗂 自助服务」按钮。

#### 移动端布局（<992px）

右侧服务列隐藏，标题栏出现「🗂 自助服务」按钮（同样仅在有已启用区块时显示）。点击按钮弹出从右侧滑入的抽屉，内容与桌面端右栏相同。

#### 各功能行为说明

| 功能 | 行为 |
|------|------|
| **提示气泡**（`guided_questions`） | 点击任意气泡，该问题文本直接发入 AI 对话（等同于手动输入后点发送） |
| **文件上传** | 点击输入框旁的 📎 按钮，选择文件上传（支持多文件，单个文件 ≤20MB，允许扩展名：`.txt` `.md` `.csv` `.json` `.pdf` `.png` `.jpg` `.jpeg` `.gif` `.xlsx` `.docx`）；上传后文件显示为卡片状标签，悬停可删除；发送消息时附带所有已上传文件路径。AI 助手可通过 `read_upload` 工具读取上传文件的内容，帮助分析或回答相关问题。 |
| **快捷入口链接**（links 区块） | 点击链接在**新标签页**打开（`target="_blank"`） |
| **热点问题**（faq 区块） | 点击问题标题内联展开 Markdown 答案（`MdPreview` 渲染，支持代码块/表格等）；再次点击收起；每次展开自动调用 `POST /kefu/i/<slug>/faq/<id>/click` 记录点击量 |
| **「没解决？问 AI」**（热点问题底部按钮） | 移动端：关闭抽屉 + 将问题文本发入 AI 对话；桌面端：直接将问题发入 AI 对话 |
| **公告**（richtext 区块） | Markdown 渲染，支持标题、列表、代码块、表格、链接等 |
| **联系方式**（contact 区块） | 展示电话、邮箱、营业时间、微信等已配置的联系项 |

**其他注意事项：**

- 对话由 OpenCode Agent 驱动，访客匿名身份通过 `X-Visitor-Id`（自动写入 `localStorage kefu:visitor_id`）标识，无 JWT/登录。
- **点击量统计**：仅用于参考，不影响热问排序或启用状态；管理员可据此调整热问顺序以提升自助转化率。
- **禁用区块/热问的行为**：禁用后不出现在访客页；管理后台仍可见、可重新启用。
- **可嵌入 widget**：独立 `<script>` 嵌入方式（不依赖宿主路由）属 Phase 3，尚未实现。

---

## 10. 后续阶段

- **Phase 2 — 转人工/人工接管**：`needs_human`/`human_takeover` 状态机、人工消息合并 SSE 通道、管理端会话队列与接管/释放、会话级审计。
- **Phase 3 — 前端增强**：可嵌入 `kefu-widget.js`（独立构建 entry + iframe，不依赖宿主路由）、`KefuManager.vue` 管理页（`/admin/kefu` 可视化实例/热问管理）、文件上传入访客对话。（访客全页 `/kefu/:slug` 已在 Stage ② 交付，不重复实现。）
