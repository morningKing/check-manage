# AI Chat P2 工具摘要、Prompt 模板与长期记忆 Spec

**版本**：v1.0  
**状态**：待评审  
**日期**：2026-09-16  
**优先级**：P2  
**范围**：工具调用可读性、Prompt 模板和长期记忆  
**本阶段**：仅定义开发规格，不实施代码修改

---

## 1. 范围

本 Spec 只包含以下两项优化：

1. 工具调用增加用户可读摘要。
2. Prompt 模板和长期记忆强化。

不包含命令面板、能力发现、快捷帮助、Agent/Skill 发现、MCP 服务发现或批任务操作入口改造。

## 2. 背景问题

### 2.1 工具调用

当前 `ToolCallBubble.vue` 主要展示：

- 工具名称。
- 工具标题。
- 执行耗时。
- 原始输入 JSON。
- 原始结果 JSON。

普通用户难以快速理解：

- AI 正在做什么。
- 操作对象是什么。
- 调用是否成功。
- 结果对当前任务有什么影响。

相关文件：

- `src/components/ai-chat/ToolCallBubble.vue`
- `src/stores/aiChat.ts`
- `src/components/ai-chat/MessageItem.vue`

### 2.2 Prompt 模板

当前已有 Prompt 模板管理能力，但用户使用成本较高：

- 模板列表缺少搜索和分类能力。
- 模板没有最近使用和收藏语义。
- 模板加载失败容易被显示成空列表。
- 创建批任务或发送消息时，模板使用入口不够统一。
- 模板内容和名称缺少更明确的预览。

相关文件：

- `src/components/ai-chat/PromptTemplateManager.vue`
- `src/api/aiChat.ts`
- `src/stores/aiChat.ts`
- `server/utils/prompt_template.py`
- `server/routes/ai_chat_prompt_templates.py`

### 2.3 长期记忆

当前已有长期记忆查看、添加和删除能力，但还需要强化：

- 记忆列表缺少搜索和分页体验。
- 记忆来源、创建时间和更新时间不够清晰。
- 加载失败容易被显示成“暂无记忆”。
- 删除操作缺少撤销或明确反馈。
- 手动添加记忆和 AI 自动提取记忆的来源没有区分。
- 记忆内容缺少详情查看和编辑能力。

相关文件：

- `src/components/ai-chat/MemoryManager.vue`
- `src/api/aiChat.ts`
- `server/routes/ai.py`
- `server/utils/memory.py`

## 3. 建设目标

### 3.1 工具摘要目标

- 工具调用默认显示自然语言摘要。
- 原始输入和结果仍可展开查看。
- 工具运行、成功、失败和取消状态清晰可见。
- 已知工具使用确定性规则生成摘要。
- 未知工具使用通用摘要，不影响渲染。
- 摘要不泄露敏感参数。

### 3.2 模板和记忆目标

- 用户可以更快找到和复用 Prompt 模板。
- 用户可以区分常用、收藏和最近使用模板。
- 模板加载失败时显示可操作错误。
- 用户可以搜索、查看、添加和删除长期记忆。
- 用户可以识别记忆来源和创建时间。
- 记忆删除后有明确反馈，必要时支持撤销。

## 4. 非目标

- 不使用 LLM 二次生成工具摘要。
- 不修改 MCP 工具实际执行逻辑。
- 不删除原始工具参数和返回值。
- 不新增工具权限模型。
- 不改变记忆检索和注入算法。
- 不改变 Prompt 模板的所有权规则。
- 不改变 AI 自动提取长期记忆的触发时机。

## 5. 工具调用用户可读摘要

### 5.1 默认展示结构

工具调用折叠状态显示：

```text
[图标] 查询数据页“订单” · 24 条结果       1.2s  已完成
```

展开后显示：

```text
操作
查询数据页“订单”，关键词为“供应商”

结果
找到 24 条记录

原始详情
输入 JSON
结果 JSON
```

### 5.2 工具状态文案

| 状态 | 展示文案 |
|---|---|
| `pending` | 等待执行 |
| `running` | 正在执行 |
| `completed` | 已完成 |
| `error` | 执行失败 |
| `cancelled` | 已取消 |
| `permission` | 等待确认 |
| 未知状态 | 状态未知 |

状态不能只通过颜色或图标表达，必须同时提供文字或可访问标签。

### 5.3 摘要生成方式

摘要必须在前端根据工具名称、输入和结果通过确定性规则生成，不调用模型。

建议新增：

```text
src/components/ai-chat/tool-renderers/
  registry.ts
  types.ts
  generic.ts
  checkManage.ts
  builtin.ts
```

每个注册项包含：

- 工具名称匹配规则。
- 显示名称。
- 输入摘要函数。
- 结果摘要函数。
- 敏感字段列表。
- 是否允许展示原始输入。

### 5.4 首批工具摘要

| 工具 | 摘要示例 |
|---|---|
| `query_collection` | 查询数据页“订单” |
| `list_collections` | 查看可用数据页 |
| `read_upload` | 读取文件“report.pdf” |
| `save_artifact` | 生成文件“result.xlsx” |
| `run_python` | 执行 Python 任务 |
| `memory_search` | 搜索长期记忆 |
| `memory_add` | 添加长期记忆 |
| `memory_delete` | 删除长期记忆 |
| `bash` | 执行命令“npm test” |
| `read` | 读取文件“src/app.ts” |
| `write` | 写入文件“output.md” |
| `edit` | 修改文件“src/app.ts” |
| `glob` | 查找匹配文件 |
| `grep` | 搜索文件内容 |
| `task` | 委托子代理处理任务 |

工具名称带 `check-manage_` 前缀时，先去除前缀再匹配。

### 5.5 未知工具降级

未知工具必须显示：

```text
调用工具“tool-name”
```

如果存在 OpenCode 提供的标题，则追加目标：

```text
调用工具“tool-name” · target
```

未知工具仍然必须支持展开查看原始输入和结果。

### 5.6 结果摘要

结果摘要优先表达数量、文件名、状态和错误原因：

| 结果类型 | 展示规则 |
|---|---|
| 数组 | 显示“找到 N 项” |
| 对象包含 `count` | 显示数量 |
| 对象包含文件路径 | 显示文件名，不显示完整内部路径 |
| 字符串 | 显示首行，最多 120 字符 |
| 错误对象 | 显示错误首行和“查看详情” |
| 空结果 | 显示“执行完成，无返回结果” |
| 超大结果 | 显示摘要，原始结果可展开 |

摘要解析失败时降级为：

```text
执行完成
```

### 5.7 流式更新

工具调用可能分阶段到达：

1. 工具开始时显示工具名称和“正在执行”。
2. 获取输入后补充操作目标。
3. 工具完成后更新结果摘要和耗时。
4. 工具失败后保留操作目标并显示错误摘要。
5. 事件乱序时以最终工具状态为准。

摘要更新不能创建重复卡片或改变消息顺序。

### 5.8 敏感信息处理

摘要和原始详情均需脱敏：

- `token`
- `access_token`
- `apiKey`
- `api_key`
- `password`
- `secret`
- `authorization`
- `cookie`
- `headers.Authorization`

脱敏示例：

```text
sk-****abcd
```

原始 JSON 使用文本渲染，不使用未经清洗的 `v-html`。

### 5.9 组件要求

改造 `ToolCallBubble.vue`：

- 默认显示摘要而不是 JSON。
- 原始输入和结果放入详情区域。
- 使用按钮或可访问的折叠组件替代可点击 `div`。
- 支持鼠标、键盘和触屏展开/收起。
- 增加 `aria-expanded` 和 `aria-controls`。
- 保留现有工具名称、标题、耗时和状态能力。

## 6. Prompt 模板强化

### 6.1 模板字段

在现有模板字段基础上增加或确认：

| 字段 | 说明 |
|---|---|
| `id` | 模板 ID |
| `name` | 模板名称 |
| `content` | Prompt 内容 |
| `description` | 模板用途说明 |
| `tags` | 分类标签，可选 |
| `isFavorite` | 是否收藏 |
| `useCount` | 使用次数 |
| `lastUsedAt` | 最近使用时间 |
| `createdAt` | 创建时间 |
| `updatedAt` | 更新时间 |

如不希望立即修改数据库，可先将收藏和最近使用保存在用户级前端偏好；但 `useCount` 和 `lastUsedAt` 应最终由后端记录。

### 6.2 模板列表

模板管理器增加：

- 名称和内容搜索。
- 标签筛选。
- 全部、收藏、最近使用筛选。
- 按最近使用时间、创建时间排序。
- 空列表状态。
- 加载失败状态和重试按钮。
- 模板预览。
- 一键应用到输入框。
- 一键应用到批任务 Prompt。

### 6.3 模板使用流程

普通会话：

1. 用户打开模板选择器。
2. 搜索或选择模板。
3. 预览模板内容。
4. 点击“插入”或“应用”。
5. 模板内容写入输入框，用户仍可修改。

批任务：

1. 用户在创建批任务弹窗中打开模板选择器。
2. 选择模板后填充 Prompt。
3. 用户可以继续编辑。
4. 提交批任务时保存模板使用记录。

“应用模板”不能直接发送消息，必须保留用户确认步骤。

### 6.4 模板创建和编辑

编辑弹窗显示：

- 名称。
- 描述。
- 标签。
- Prompt 内容。
- 预览区域。
- 收藏开关。

保存前校验：

- 名称不能为空。
- 内容不能为空。
- 名称长度不超过既有后端限制。
- 内容超过限制时显示剩余可用长度。

### 6.5 模板错误处理

必须区分：


删除模板后显示成功提示。删除失败时不能从本地列表静默移除。

## 7. 长期记忆强化

### 7.1 记忆字段

记忆列表至少展示：

| 字段 | 说明 |
|---|---|
| `id` | 记忆 ID |
| `text` | 记忆内容 |
| `source` | `manual`、`conversation`、`batch` 等来源 |
| `createdAt` | 创建时间 |
| `updatedAt` | 更新时间 |
| `score` | 搜索结果相关度，可选 |
| `metadata` | 脱敏后的来源信息，可选 |

### 7.2 记忆列表

记忆管理器增加：

- 关键词搜索。
- 分页或加载更多。
- 来源筛选。
- 创建时间排序。
- 记忆详情展开。
- 空列表状态。
- 加载失败状态和重试按钮。

搜索只返回当前用户自己的记忆。

### 7.3 手动添加记忆

保留现有手动添加能力，并强化表单：

- 明确“原样保存”和“由 AI 提炼”两种模式。
- 展示当前文本长度和限制。
- 提交中禁用重复提交。
- 成功后定位到新记忆。
- 失败后保留用户输入。

### 7.4 记忆来源展示

来源文案：

| 来源 | 展示文案 |
|---|---|
| `manual` | 手动添加 |
| `conversation` | 对话自动提取 |
| `batch` | 批任务自动提取 |
| 未知来源 | 系统记忆 |

来源信息只用于解释，不允许用户通过来源字段访问其他会话内容。

### 7.5 删除和撤销

删除前显示确认：

```text
删除这条长期记忆？删除后 AI 将不再使用它。
[取消] [删除]
```

删除成功后：

- 从列表移除。
- 显示成功提示。
- 短时间内提供“撤销”入口，或至少提供明确的删除结果。

如果底层存储不支持可靠撤销，本期采用“删除成功提示 + 刷新列表”，不伪造撤销能力。

删除失败时：

- 保留列表项。
- 显示失败原因。
- 提供重试。

### 7.6 记忆加载错误

不得把请求失败渲染成“暂无长期记忆”。页面必须区分：

```text
加载中
暂无长期记忆
加载失败 [重新加载]
```

## 8. API 设计

### 8.1 Prompt 模板

在现有模板 CRUD 接口上增加可选参数：

```text
GET /ai/chat/prompt-templates?q=&tag=&favorite=&sort=&page=&pageSize=
POST /ai/chat/prompt-templates/<id>/favorite
POST /ai/chat/prompt-templates/<id>/use
```

`use` 接口用于记录使用次数和最近使用时间，不执行 Prompt。

如果暂不增加 `use` 接口，前端可以先使用本地记录，但后续必须迁移到后端以支持多端同步。

### 8.2 长期记忆

在现有记忆接口上增加：

```text
GET /ai/memories?q=&source=&page=&pageSize=
GET /ai/memories/<id>
DELETE /ai/memories/<id>
```

新增或确认手动添加接口：

```text
POST /ai/memories
```

请求示例：

```json
{
  "text": "我偏好使用中文回答",
  "infer": false,
  "source": "manual"
}
```

所有查询和删除接口必须在服务端校验当前用户归属，不能仅依赖前端传入的用户 ID。

## 9. 前端改造范围

### 工具摘要

- `src/components/ai-chat/ToolCallBubble.vue`
- `src/components/ai-chat/tool-renderers/registry.ts`
- `src/components/ai-chat/tool-renderers/types.ts`
- `src/components/ai-chat/tool-renderers/generic.ts`
- `src/components/ai-chat/tool-renderers/checkManage.ts`

### Prompt 模板

- `src/components/ai-chat/PromptTemplateManager.vue`
- `src/components/ai-chat/CreateBatchDialog.vue`
- `src/api/aiChat.ts`
- `src/stores/aiChat.ts`

### 长期记忆

- `src/components/ai-chat/MemoryManager.vue`
- `src/api/aiChat.ts`
- `src/stores/aiChat.ts`

## 10. 测试要求

### 10.1 工具摘要

- 已知工具生成正确摘要。
- 未知工具正常降级。
- 运行、完成、失败和取消状态正确。
- 数组、对象、字符串和空结果摘要正确。
- 敏感字段正确脱敏。
- 超大结果不会撑爆页面。
- 原始输入和结果仍可展开。
- 鼠标、键盘和触屏均可展开工具详情。

### 10.2 Prompt 模板

- 搜索和标签筛选正确。
- 收藏和取消收藏正确。
- 最近使用排序正确。
- 普通会话应用模板后内容进入输入框。
- 批任务应用模板后内容进入 Prompt 字段。
- 模板内容仍可编辑。
- 加载失败显示重试，而不是空列表。
- 删除失败时模板不会从列表消失。

### 10.3 长期记忆

- 搜索、筛选和分页正确。
- 手动添加成功后可立即看到新记忆。
- 原样保存和 AI 提炼模式正确传参。
- 记忆来源正确展示。
- 删除前确认，删除成功后刷新列表。
- 删除失败保留列表项并提供重试。
- 非本人记忆无法查询或删除。
- 加载失败不显示为“暂无数据”。

### 10.4 Playwright E2E

至少验证：

1. 工具调用默认显示用户可读摘要。
2. 展开工具调用可以查看原始输入和结果。
3. 工具失败时显示可理解的错误摘要。
4. 新建模板并在普通会话中应用。
5. 创建批任务时选择并应用模板。
6. 搜索、收藏和删除模板。
7. 添加一条手动长期记忆。
8. 搜索并删除长期记忆。
9. 模板或记忆接口失败时显示重试入口。

## 11. 实施阶段

### P2.1：工具调用摘要

- 定义摘要类型和注册表。
- 实现首批已知工具摘要。
- 实现通用降级摘要。
- 实现结果摘要和敏感字段脱敏。
- 改造 `ToolCallBubble`。
- 补充单元测试和 E2E。

### P2.2：Prompt 模板强化

- 增加搜索、筛选和预览。
- 增加收藏和最近使用。
- 接入普通会话和批任务应用模板。
- 补充加载、保存和删除错误态。
- 补充单元测试和 E2E。

### P2.3：长期记忆强化

- 增加搜索、分页和来源展示。
- 强化手动添加表单。
- 增加删除确认和结果反馈。
- 增加失败重试。
- 补充所有权测试和 E2E。

## 12. 验收标准

### 工具调用摘要

1. 工具调用默认展示用户可理解的操作摘要。
2. 摘要包含目标、状态、耗时和结果概要。
3. 原始输入和结果仍可展开查看。
4. 未知工具不会导致消息渲染失败。
5. 摘要不会泄露敏感参数。
6. 工具失败时用户能够理解失败原因。

### Prompt 模板

1. 用户可以搜索、筛选、收藏和预览模板。
2. 模板可以应用到普通会话和批任务 Prompt。
3. 应用模板后用户仍可以修改内容。
4. 模板加载、保存和删除失败均有明确反馈和重试入口。
5. 模板使用次数和最近使用时间能够正确更新，或明确采用本地记录方案。

### 长期记忆

1. 用户可以搜索和筛选自己的长期记忆。
2. 用户可以区分手动添加和自动提取来源。
3. 用户可以查看记忆详情并删除记忆。
4. 添加和删除操作有明确成功或失败反馈。
5. 记忆加载失败不会被显示成空列表。
6. 任何用户都无法查询或删除其他用户的记忆。

## 13. 相关文件

### 前端

- `src/components/ai-chat/ToolCallBubble.vue`
- `src/components/ai-chat/PromptTemplateManager.vue`
- `src/components/ai-chat/MemoryManager.vue`
- `src/components/ai-chat/CreateBatchDialog.vue`
- `src/api/aiChat.ts`

### 后端

- `server/routes/ai_chat_prompt_templates.py`
- `server/utils/prompt_template.py`
- `server/routes/ai.py`
- `server/utils/memory.py`

### 测试

- `src/components/ai-chat/__tests__/ToolCallBubble.test.ts`
- `src/components/ai-chat/__tests__/PromptTemplateManager.test.ts`
- `src/components/ai-chat/__tests__/MemoryManager.test.ts`
- `e2e/ai-chat-smoke.spec.ts`
