# 客服实例管理（管理页新建/编辑/删除）设计

**日期**: 2026-07-03
**分支**: feat/kefu-self-service-faq
**范围**: 纯前端。在「设置中心 → 集成对接 → 智能客服」管理页（`KefuManager.vue`）补齐客服实例的**新建 / 编辑 / 删除**，并把此前无 UI 入口的 **Agent / 模型 / 系统提示词 / 欢迎语 / 品牌 logo** 做成可视化表单。后端 CRUD 全部现成，不改后端。

## 背景与问题

`KefuManager.vue` 目前只能从下拉框**选择已有实例**并编辑其 FAQ / 提示气泡 / 主页区块。没有「新建客服」入口，`api/kefu.ts` 也没有 `createInstance`/`deleteInstance` 绑定。而 `kefu_instances` 的 `agent`/`model`/`system_prompt`/`welcome_message`/`branding` 字段后端可读写、运行时生效（agent/model 每次发消息透传给 OpenCode，system_prompt 拼接固定护栏后写入会话 `AGENTS.md`），却没有任何 UI 表单——只能手搓 REST API。

后端接口（`server/routes/kefu_admin.py`，均 `@require_permission('admin.kefu')`）已具备：
- `GET /admin/kefu/instances`、`POST /admin/kefu/instances`、`GET/PATCH/DELETE /admin/kefu/instances/<iid>`

## 目标

- 管理页可**新建 / 编辑 / 删除**客服实例。
- 新建/编辑表单覆盖实例级设置：slug、名称、启用、欢迎语、Agent、模型、系统提示词、品牌 logo URL。
- Agent/模型下拉复用 AI 助手已有的 `listAgents()`/`listModels()`。

## 非目标（YAGNI）

- 不改后端（CRUD 已存在）。
- 不动现有内联编辑器：提示气泡（`guided_questions`）、主页区块（`panel_blocks`）、FAQ——它们仍在页面内联管理。弹窗只管**实例级设置**，页面内联管**内容**。
- 不加 `rate_limit` 字段（高级，本次不做）。

## 设计

### 1. API 层（`src/api/kefu.ts`）

扩展类型 + 两个新绑定：

```ts
export interface KefuInstanceFull extends KefuInstance {
  agent?: string | null
  model?: string | null
  system_prompt?: string | null
  welcome_message?: string | null
  branding?: Record<string, any>
  guided_questions?: string[]
  panel_blocks?: any[]
}

export function createInstance(data: Partial<KefuInstanceFull> & { slug: string; name: string }) {
  return post<KefuInstance>('/admin/kefu/instances', data)
}
export function deleteInstance(iid: string) {
  return del(`/admin/kefu/instances/${iid}`)
}
```

（`KefuInstance` 已含 `enabled`；`createInstance` 返回创建后的实例，含 `id`。）

### 2. 新组件 `src/components/kefu/KefuInstanceDialog.vue`（新建 + 编辑共用）

**Props**：`{ modelValue: boolean; instance: KefuInstanceFull | null }`（`instance` 为 `null`→新建，否则编辑）
**Emits**：`update:modelValue(v)`、`saved(instance)`

**表单字段**：
| 字段 | 控件 | 说明 |
|---|---|---|
| slug | `ElInput` | 新建时可填；**编辑时 `disabled`**。占位提示「小写字母/数字/连字符」 |
| 名称 name | `ElInput` | 必填 |
| 启用 enabled | `ElSwitch` | 默认 `true` |
| 欢迎语 welcome_message | `ElInput type=textarea` | 可空 |
| Agent | `ElSelect clearable`（`listAgents().agents`，`:value="a.name"`） | 空=默认 Agent；占位「使用 OpenCode 默认 Agent」 |
| 模型 model | `ElSelect clearable filterable`（`listModels().models`，`:value="m.id" :label="m.label"`） | 空=默认模型；`m.id` 形如 `<providerID>/<modelID>` |
| 系统提示词 system_prompt | `ElInput type=textarea` | 可空；下方灰字提示「会与固定护栏拼接后作为客服人设」 |
| 品牌 logo URL | `ElInput` | 存入 `branding.logo`；编辑时从 `instance.branding?.logo` 回填 |

**校验**：
- slug（仅新建）：镜像后端正则 `^[a-z0-9][a-z0-9-]{0,63}$`；name 必填。二者不满足时「保存」按钮 `disabled`。
- 后端为权威：`POST`/`PATCH` 返回 400（slug 非法/name 空）或 409（slug 已存在）时用 `ElMessage.error` 显示后端 `error` 文案。

**保存逻辑**：
- 组装 payload：`{ slug?, name, enabled, welcome_message, agent: agent||null, model: model||null, system_prompt: system_prompt||null, branding: { ...(instance?.branding||{}), logo: logoUrl } }`（新建含 slug；编辑不传 slug）。
- 新建：`await createInstance(payload)`；编辑：`await updateInstance(instance.id, payload)`。成功后 `emit('saved', result)` + 关闭。
- Agent/模型加载失败不致命（`try/catch`，与 `CreateBatchDialog` 一致）。

### 3. `KefuManager.vue` 改动

- 顶部实例下拉右侧：常驻「**+ 新建客服**」按钮；`activeIid` 有值时额外显示「**编辑**」「**删除**」。
- 「新建」→ 打开 `KefuInstanceDialog`（`instance=null`）。
- 「编辑」→ 先 `getInstance(activeIid)` 拿完整字段，打开 `KefuInstanceDialog`（`instance=完整对象`）。
- 「删除」→ `ElMessageBox.confirm`（提示「将同时删除该客服的所有热问，且不可恢复」）→ `deleteInstance(activeIid)` → `ElMessage.success` → 刷新列表、清空 `activeIid`。
- `KefuInstanceDialog` 的 `@saved`：`loadInstances()`；新建时把 `activeIid` 设为新实例 id 并 `onInstanceChange()`（加载其 FAQ/主页配置）；编辑时保持当前选中并刷新。
- 现有「主页配置」「FAQ 表格」模板与逻辑**保持不变**。

### 4. 数据流

新建/编辑弹窗是受控组件，输入 props、输出 `saved` 事件；`KefuManager` 持有实例列表与选中态并负责刷新。无 SSE、无副作用。删除走确认 → API → 刷新。

### 5. 错误处理

- 网络/后端错误：`ElMessage.error`（优先后端返回的 `error` 文案，否则通用「操作失败」）。
- 删除确认取消：静默返回。
- Agent/模型列表拉取失败：下拉为空但不阻塞（可留空=默认）。

## 组件边界与文件

| 文件 | 职责 | 变更 |
|---|---|---|
| `src/api/kefu.ts` | 类型扩展 + `createInstance`/`deleteInstance` | 改 |
| `src/components/kefu/KefuInstanceDialog.vue` | 新建/编辑实例受控弹窗 | **新增** |
| `src/views/admin/KefuManager.vue` | 新建/编辑/删除入口 + 编排 | 改 |

## 测试

- **新增** `src/components/kefu/__tests__/KefuInstanceDialog.test.ts`：
  - 新建模式：slug 可编辑；填 slug+name 后点保存 → 调 `createInstance`，payload 含 slug/name/agent/model/system_prompt/branding.logo。
  - 编辑模式：slug `disabled`；字段从 `instance` 预填（含 `branding.logo`）；保存 → 调 `updateInstance(id, …)` 且 payload **不含 slug**。
  - 校验：缺 slug 或 name 时保存按钮 `disabled`；非法 slug（大写/空格）禁用。
  - agent/model 留空 → payload 对应字段为 `null`。
  - logo URL ↔ `branding.logo` 双向映射；编辑时保留 `branding` 其它键。
  - （mock `@/api/kefu` 与 `@/api/aiChat` 的 `listAgents`/`listModels`。）
- **现有** `src/views/admin/__tests__/KefuManager.faq.test.ts`：新增子组件需在其 stubs 中加 `KefuInstanceDialog` 极简 stub，保持原有断言全绿。

## 验证（MANDATORY）

Playwright（admin 登录，设置中心→集成对接→智能客服）：
1. 点「+ 新建客服」→ 填 slug（如 `pw-test`）+ 名称 + 欢迎语 + 选一个模型 → 保存 → 列表出现并自动选中。
2. 新开 `/kefu/pw-test` → 页面正常加载、欢迎语正确。
3. 「编辑」→ slug 只读、字段预填；改系统提示词/模型 → 保存成功。
4. 「删除」该临时实例 → 确认 → 列表移除；`/kefu/pw-test` 不再可用。
5. 截图存 `.playwright-mcp/`。事后清理测试实例。

## 文档同步

更新 `docs/user-guide/ai/smart-customer-service.md` §9.2：新增「在管理页新建/编辑/删除客服实例」小节 + 各字段（含 Agent/模型/系统提示词/欢迎语/logo）说明；标注 slug 创建后不可改、删除级联热问。
