# 智能客服 — 热门问题 / 自助服务面板 设计文档

- 状态：已通过 brainstorming 评审，待实现计划
- 日期：2026-07-01
- 依赖：智能客服 Phase 1（`kefu_instances`、`kefu_public_bp`、`kefu_admin_bp`、`admin.kefu`、匿名 visitor_id）——见 `docs/superpowers/specs/2026-06-30-smart-customer-service-design.md`

## 1. 背景与目标

为智能客服增加**热门问题**与**自助服务面板**：访客进入客服链接后，除了与 AI 对话，还能在一个自助面板里浏览管理员策划的常见问题、直接看到预写答案（无需消耗 Agent），未解决时一键转 AI 深入。管理员可完全自定义热问列表。

### 能力范围（v1）

- **热问 CRUD（管理员自定义）**：每条 = 问题 + Markdown 预写答案 + 可选分类标签 + 排序 + 启停。
- **点击量统计**：系统记录每条热问被点击次数，管理员参考热度手动调序（**手动策划为主，点击量仅参考**）。
- **访客自助面板**：对话为主、面板作抽屉；点击热问内联展开 Markdown 答案（**混合模式**：预写答案优先，「没解决？问 AI」升级把问题发进对话）。
- **访客侧最小全页**：新增公开页 `/kefu/:slug` 承载对话 + 自助抽屉。

### 明确不做（v1 排除）

- 自动按提问频次计算「热度」（仅手动策划 + 点击量参考）。
- 可嵌入悬浮 widget（仍属 Phase 3）。
- 分类的层级/树（仅**平铺 + 单个可选分类标签**，客户端按标签筛选）。
- 转人工（Phase 2）。

## 2. 锁定的关键决策

| 维度 | 决策 |
|------|------|
| 点击行为 | 混合：预写 Markdown 答案优先，「没解决？问 AI」升级转对话 |
| 热度来源 | 管理员手动策划 + 系统记录 `click_count` 供参考调序 |
| 面板结构 | 平铺列表 + 每条可选**分类标签**，客户端按标签/文本筛选 |
| 答案格式 | Markdown（与聊天渲染一致） |
| 数据模型 | **独立表 `kefu_faq_items`**（非 JSONB；点击自增 + 逐条排序/CRUD 需要真表） |
| 归属 | 每条热问属于某个 `kefu_instances`（per-instance） |
| 交付 | 后端 + 管理端编辑器 + 访客全页 + 自助抽屉，端到端可用 |
| 访客布局 | 对话为主，自助面板作侧栏抽屉 |
| 访客形态 | 仅独立全页 `/kefu/:slug`；可嵌入 widget 留 Phase 3 |

## 3. 数据模型

### 3.1 新表 `kefu_faq_items`

```sql
CREATE TABLE IF NOT EXISTS kefu_faq_items (
  id           VARCHAR(100) PRIMARY KEY,
  instance_id  VARCHAR(100) NOT NULL REFERENCES kefu_instances(id) ON DELETE CASCADE,
  question     TEXT NOT NULL,
  answer       TEXT NOT NULL,              -- Markdown 源码
  category     VARCHAR(100),               -- 可空：单个分类标签
  sort_order   INTEGER NOT NULL DEFAULT 0, -- 升序展示
  click_count  INTEGER NOT NULL DEFAULT 0,
  enabled      BOOLEAN NOT NULL DEFAULT true,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kefu_faq_instance
  ON kefu_faq_items(instance_id, sort_order);
```

`ON DELETE CASCADE`：删除客服实例连带删除其热问。迁移随 `migrate_kefu.py` 幂等追加（与 init_db 平行）。

## 4. 公开 API（`kefu_public_bp`，无 JWT，匿名 + 限速）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/kefu/i/<slug>/faq` | 返回该实例 `enabled=true` 的热问列表（`id, question, answer, category`），按 `sort_order` 升序。一次拉全（含答案），访客端即时展开、无额外请求。实例不存在→404；停用（`enabled=false`）→返回空列表（面板隐藏）。 |
| POST | `/kefu/i/<slug>/faq/<id>/click` | 点击量 `click_count + 1`，返回 204。轻量埋点：经**独立**的点击限速桶（key 前缀 `faqclick:`，独立于消息桶，避免浏览 FAQ 挤占对话额度）防刷；id 不属于该实例或已停用→静默 204（不泄露存在性，不报错）。 |

> 归属校验：click 端点校验 `faq.instance_id` 属于 `slug` 指向的实例。`click_count` 自增用参数化 `UPDATE ... SET click_count = click_count + 1`（并发安全）。

## 5. 管理 API（`kefu_admin_bp`，`@require_permission('admin.kefu')`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/admin/kefu/instances/<iid>/faq` | 列出该实例全部热问（含 `click_count`、`enabled`），按 `sort_order`。 |
| POST | `/admin/kefu/instances/<iid>/faq` | 新建（`question` 必填、`answer` 必填、`category`/`sort_order`/`enabled` 可选）。 |
| PATCH | `/admin/kefu/instances/<iid>/faq/<id>` | 改字段（question/answer/category/enabled/sort_order，按需传入）。 |
| DELETE | `/admin/kefu/instances/<iid>/faq/<id>` | 删除。 |
| PATCH | `/admin/kefu/instances/<iid>/faq/reorder` | 批量排序：body `{ order: [id1, id2, ...] }`，按数组下标写 `sort_order`。 |

所有端点校验 `<id>`/热问归属 `<iid>`；操作写 `operation_logs`（`target_type='kefu_faq_item'`）。

## 6. 前端 — 管理端编辑器

新增 `src/views/admin/KefuManager.vue`（`/admin/kefu`，`admin.kefu` 可见）——本期先落地**实例列表 + 热问编辑器**这一必需切片（完整实例配置 UI 仍可留 Phase 3 增补）：

- 实例选择（下拉/列表）→ 该实例的热问表格。
- 热问表格：问题、分类标签、启停、`click_count`（只读，供参考调序）、拖拽排序。
- 编辑弹窗：`question` 文本、`answer` 用 `MdEditor`（项目已有 md-editor-v3）、`category` 输入、`enabled` 开关。
- 保存调 §5 管理 API；拖拽结束调 reorder。

## 7. 前端 — 访客全页 + 自助抽屉

新增公开路由 `/kefu/:slug`（独立 layout，无鉴权守卫；`src/views/kefu/KefuChatPage.vue` + `src/api/kefuPublic.ts`）：

```
┌───────────────────────────────┐
│  客服名           [🗂 自助服务] │  ← 抽屉开关
│   ...AI 对话消息流 (SSE)...     │
│  [输入框...............] [发送] │
└───────────────────────────────┘
  抽屉（右侧滑出）:
   自助服务
   🔍 过滤框     [全部 | 标签A | 标签B]
   ▸ 如何私有化部署？        ← 点击内联展开
       （Markdown 渲染的预写答案）
       [没解决？问 AI]        ← 升级
```

- 进入时 `GET /kefu/i/<slug>` 取实例配置（欢迎语/引导问/branding），`POST /kefu/i/<slug>/sessions`（localStorage `kefu:visitor_id`）建/续会话，`GET /kefu/i/<slug>/faq` 拉热问。
- 对话：复用 §Phase1 公开 API（`POST .../messages`、SSE `.../events`）；消息渲染用 Markdown（可复用现有 markdown 视图组件）。
- 自助抽屉：`KefuSelfServicePanel.vue` —— 平铺热问，客户端文本过滤 + 分类标签筛选；点击某条 → 内联展开 `MdPreview` 答案 + `POST .../faq/<id>/click`（fire-and-forget）；「没解决？问 AI」→ 关抽屉、把 `question` 作为消息发进对话。
- 匿名凭证：`kefu:visitor_id`（localStorage，无则生成 UUID），所有请求带 `X-Visitor-Id`。

> 说明：访客页对话组件面向**公开匿名**语境，不复用依赖 auth store/Bearer 的内部聊天组件，改用轻量 `kefuPublic.ts`（visitor_id、无 JWT）。这是本期为承载自助抽屉必需的最小 Phase-3 切片。

## 8. 安全

- FAQ 内容为管理员策划的公开文案，匿名只读安全。
- `/faq/<id>/click` 经**独立的点击限速桶**（`faqclick:` 前缀，复用 `RateLimiter` 但独立于消息桶）防止点击量灌水，且不挤占对话额度。
- 管理 CRUD 由 `admin.kefu` 钳制；归属校验防跨实例操作。
- 访客页无鉴权守卫，但仅调用公开 `kefu_public_bp` 端点（攻击面仍收敛在该蓝图）。

## 9. 测试与验收

- **后端 pytest**（`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`）：热问 CRUD + reorder、公开列表（仅 enabled、按序、含答案）、点击自增（并发安全 SQL）、归属/停用过滤、click 限速。
- **前端**：`KefuSelfServicePanel` 过滤/展开/升级逻辑单测（Vitest）。
- **Playwright E2E（必做）**：访客进入 `/kefu/:slug` → 打开自助抽屉 → 按标签筛选 → 点击热问看到 Markdown 答案（且 `click_count` 自增，DB 交叉核对）→「没解决？问 AI」把问题发进对话得到流式回复；管理端编辑器增删改排序。截图存 `.playwright-mcp/`。
- **文档同步**：更新 `docs/user-guide/ai/smart-customer-service.md`（新增「热门问题 / 自助服务面板」小节：如何配置热问、访客如何使用）。

## 10. 实现分阶段（同一 spec，计划分两段）

- **阶段①（后端 + 管理编辑器）**：`kefu_faq_items` 迁移 + 公开/管理 API + 点击统计 + `KefuManager.vue` 热问编辑器。产出可用 curl/pytest + 管理端 Playwright 验证。
- **阶段②（访客页 + 自助抽屉）**：`/kefu/:slug` 全页 + `KefuSelfServicePanel` + 转 AI 升级 + 访客 Playwright 全流程。②依赖①。

## 11. 已知约束 / 未决

- 访客对话页是 Phase 3 的最小前置切片；完整可嵌入 widget、KefuManager 的完整实例配置 UI 仍属 Phase 3。
- `sort_order` 采用整数 + reorder 全量重写；量级（每实例几十条）下无需分数排序等复杂方案（YAGNI）。
- 分支/合并顺序：本特性建立在 Phase 1 之上（`kefu_instances`/公开蓝图），计划阶段确认基于 Phase 1 分支叠加还是待其合并后再起。
