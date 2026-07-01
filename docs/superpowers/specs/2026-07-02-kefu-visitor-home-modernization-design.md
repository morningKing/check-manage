# 智能客服访客主页现代化 — 设计文档

- 状态：已通过 brainstorming 评审，待实现计划
- 日期：2026-07-02
- 依赖：智能客服 Stage ②（访客页 `KefuChatPage.vue`、`kefuPublic.ts`、`KefuSelfServicePanel.vue`、`kefu_instances`、热问 FAQ）——见 `docs/superpowers/specs/2026-07-01-kefu-self-service-faq-design.md`

## 1. 背景与目标

把访客客服页从「对话 + FAQ 抽屉」升级为**现代化布局**：左对话为主、右侧常驻一列**管理员自定义的自助服务区块**，欢迎区展示可点击的**提示气泡**。手机窄屏右栏收进抽屉。目标是让访客一进来就看到清晰的自助入口，减少对话轮次，同时全部内容管理员可配。

## 2. 锁定的关键决策

| 维度 | 决策 |
|------|------|
| 提示气泡来源 | 复用现有 `kefu_instances.guided_questions`（已存在，访客页尚未渲染）；管理端补一个编辑器 |
| 自助区块数据 | `kefu_instances` 新增 JSONB 列 `panel_blocks`（有序数组），**非新表** |
| 区块类型 | `links`（快捷入口）、`faq`（热点问题，复用现有 FAQ 数据）、`richtext`（公告/富文本）、`contact`（联系方式） |
| FAQ 区块 | 复用 Stage①/② 的热问数据与点击埋点，不为区块单独建 FAQ |
| 桌面布局 | 两栏：左对话主区 + 右自助区块列常驻；提示气泡在首屏欢迎区（发首条消息后淡出） |
| 移动布局 | 右栏隐藏，顶部「🗂 自助服务」按钮唤出抽屉，装同样的区块（复用现有抽屉交互） |
| 交付 | 分两阶段：① 后端 `panel_blocks` 配置 + 管理端编辑器；② 前端两栏重构 + 区块渲染 + 气泡 |
| 分支 | 叠加在 Stage ② 分支 `feat/kefu-self-service-faq` 之上 |

### 明确不做（v1 排除）
- 区块级点击统计（仅 FAQ 复用现有埋点）。
- 区块类型的插件化扩展框架（仅固定 4 种）。
- 可嵌入 widget（仍属后续）。
- 逐字流式对话（Stage ② 已定 YAGNI）。

## 3. 数据模型

### 3.1 `kefu_instances` 增列（幂等迁移）
```sql
ALTER TABLE kefu_instances ADD COLUMN IF NOT EXISTS panel_blocks JSONB NOT NULL DEFAULT '[]'::jsonb;
```
随 `server/migrate_kefu.py` 的 `_SQL` 幂等追加，并在 `server/init_db.py` 平行加入 `kefu_instances` 建表列。

### 3.2 `guided_questions`（已存在，复用）
`kefu_instances.guided_questions`（JSONB 字符串数组）已存在于表与 `_row_to_instance`。访客页把它渲染成提示气泡。

### 3.3 `panel_blocks` 结构
有序数组，每块：
```jsonc
{
  "id": "blk_xxx",            // 前端生成的稳定 id（用于 v-for key / 排序）
  "type": "links|faq|richtext|contact",
  "title": "快捷入口",         // 区块标题（可空则用类型默认名）
  "enabled": true,
  "config": { ... }           // 见 §4，按 type
}
```

## 4. 区块类型与 config

| type | config 结构 | 访客侧渲染 |
|------|------------|-----------|
| `links` | `{ items: [{ icon?: string, label: string, url: string }] }` | 快捷入口卡片；点击 `url` 在新标签打开（`target=_blank` `rel=noopener noreferrer`）。`url` 仅允许 `http(s):`/相对路径（前端校验，拒 `javascript:` 等）。 |
| `faq` | `{ limit?: number }`（默认 5） | 复用现有 FAQ：取该实例 `enabled` 热问 top-N（按 sort_order），点击内联展开 Markdown 答案 + 触发 `POST /kefu/i/<slug>/faq/<id>/click` 埋点 + 「问 AI」升级 |
| `richtext` | `{ markdown: string }` | `MdPreview` 渲染（与聊天一致，md-editor-v3 已 sanitize） |
| `contact` | `{ phone?: string, email?: string, hours?: string, wechat?: string }` | 联系方式列表（有值才显示对应行） |

> 后端对 `panel_blocks` 只做**基本结构校验**（是数组、每项有合法 `type`）；细粒度内容（如 url 协议）由前端管理端 + 访客端双重把关。

## 5. 后端

### 5.1 迁移
§3.1 的 `ALTER`（+ init_db 平行）。

### 5.2 `kefu_repo`
- `_row_to_instance` 增加 `panel_blocks` 字段（读表新列）。
- `update_instance` 白名单加入 `guided_questions`（已支持）与 `panel_blocks`（JSON 序列化写入）。
- 公开配置 `get_instance_by_slug` 返回值含 `guided_questions` 与 `panel_blocks`（供访客页）。

### 5.3 公开端点
`GET /kefu/i/<slug>`（`kefu_public._public_config`）返回值**新增** `panel_blocks`（`guided_questions` 已在返回中）。FAQ 数据仍走既有 `GET /kefu/i/<slug>/faq`。

### 5.4 管理端点
无需新端点：实例 `PATCH /admin/kefu/instances/<iid>` 已存在，扩展白名单即可写 `guided_questions`/`panel_blocks`。后端对 `panel_blocks` 做结构校验（非数组或非法 type → 400）。

## 6. 管理端（扩展 `KefuManager.vue`）

当前 KefuManager 仅有热问编辑器（选实例 → FAQ 表）。补两个编辑区（同一实例上下文）：
- **提示气泡编辑器**：编辑 `guided_questions`（字符串列表：加/删/上下排序）。保存调实例 PATCH。
- **自助区块编辑器**：`panel_blocks` 列表（加/删/上下排序/启停）；点「新增区块」选 type → 按 type 表单：
  - `links`：条目行编辑（icon 可选、label、url）。
  - `faq`：limit 数字。
  - `richtext`：`MdEditor` 编辑 Markdown。
  - `contact`：phone/email/hours/wechat 四输入。
  保存整个 `panel_blocks` 数组调实例 PATCH。

组件拆分：`KefuManager.vue` 保持编排，新增 `KefuBlocksEditor.vue`（区块编辑器，含各 type 子表单），避免 KefuManager 过大。

## 7. 前端重构 `KefuChatPage.vue`

### 7.1 布局
- **桌面**（≥ 992px）：CSS grid 两栏——左对话主区（消息流 + 输入），右 `KefuServiceColumn.vue` 常驻（宽约 320–360px）。
- **移动**（< 992px）：右栏隐藏，头部保留「🗂 自助服务」按钮 → 打开 `el-drawer`，抽屉内放同一个 `KefuServiceColumn`。
- 视觉现代化：区块卡片化（圆角 + 轻阴影 + 留白）、气泡 chip 化；可选主题色取自 `branding.themeColor`（有则用于强调色，无则默认）。

### 7.2 提示气泡
欢迎区渲染 `config.guided_questions` 为一排可点击 chip；点击 = 走 `send(question)`。发出首条消息后欢迎区（含气泡）淡出，只留对话。

### 7.3 自助区块列 `KefuServiceColumn.vue`
- props：`blocks: PanelBlock[]`、`slug`、`faqItems`（FAQ 数据）。
- 遍历 `blocks`（仅 `enabled`），按 `type` 分发到子组件：
  - `KefuBlockLinks.vue`、`KefuBlockFaq.vue`、`KefuBlockRichtext.vue`、`KefuBlockContact.vue`。
- `KefuBlockFaq`：复用/抽取现有 `KefuSelfServicePanel` 的展开 + 埋点 + 「问 AI」逻辑（emit `click(id)`/`escalate(question)` 冒泡到 `KefuChatPage`）。
- 桌面右栏与移动抽屉共用 `KefuServiceColumn`，单一实现两处渲染。

### 7.4 数据流
`KefuChatPage` 挂载时的 `getKefuConfig` 现返回 `guided_questions` + `panel_blocks`；`getKefuFaq` 返回热问。气泡与区块的点击/升级都汇聚到 `KefuChatPage` 的 `send`/`clickKefuFaq`。

## 8. 安全 / 边界
- `links.url` 前端校验仅允许 `http(s):`/相对路径，渲染 `rel="noopener noreferrer" target="_blank"`，防 `javascript:` 注入与 tabnabbing。
- `richtext`/FAQ 答案经 md-editor-v3 sanitize（与聊天一致），无新增裸 HTML 注入面。
- `panel_blocks` 是管理员策划内容，公开只读安全；后端结构校验防脏数据。

## 9. 测试与验收
- **后端 pytest**（`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`）：`panel_blocks` 迁移；`update_instance` 写 `guided_questions`/`panel_blocks`；公开配置返回 `panel_blocks`；PATCH 非法 `panel_blocks` → 400。
- **前端 Vitest**：气泡点击→send；`KefuServiceColumn` 按 type 分发；各 Block 渲染（links 过滤非法 url、faq 展开埋点、richtext/contact 条件渲染）；区块编辑器增删改排序。
- **Playwright E2E（必做）**：匿名进入 `/kefu/:slug` → 桌面两栏（右栏各区块渲染）→ 点气泡发送得回复 → links 新标签、faq 展开+埋点(DB 核对)+转 AI、richtext/contact 显示 → 缩窄到移动宽度确认右栏收进抽屉。管理端在 `/admin/kefu` 配置气泡 + 各类型区块并保存。截图存 `.playwright-mcp/`。
- **文档同步**：更新 `docs/user-guide/ai/smart-customer-service.md`（提示气泡 + 自助区块的配置与访客体验）。

## 10. 实现分阶段（同一 spec，计划分两段）
- **阶段①（后端 + 管理端）**：`panel_blocks` 迁移 + `kefu_repo`/公开配置/PATCH 白名单 + 校验；`KefuManager` 补气泡编辑器 + `KefuBlocksEditor`。产出 pytest + 管理端 Playwright 验证。
- **阶段②（前端访客页重构）**：`KefuChatPage` 两栏 + 提示气泡 + `KefuServiceColumn` + 4 个 Block 子组件 + 移动抽屉。访客端 Playwright 全流程。②依赖①。

## 11. 已知约束 / 未决
- `panel_blocks` 用 JSONB 数组（YAGNI，无区块级统计）；若日后要区块点击分析再拆表。
- 复用 `KefuSelfServicePanel` 的展开/埋点逻辑：实现时若直接复用不便，则抽取一个共享 composable 供 `KefuBlockFaq` 与旧抽屉共用（避免逻辑重复）。
- 主题色 `branding.themeColor` 为可选增强，缺省走默认配色。
