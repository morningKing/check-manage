# 智能客服 人工接管（Phase 2）— 子项目 A：后端 设计

**日期**: 2026-07-04
**分支**: feat/kefu-self-service-faq
**范围**: **纯后端**。实现人工接管的状态机、消息路由、访客/管理端接口与审计。前端（访客「转人工」按钮 + 接管期轮询、管理端接管控制台）属**子项目 B**；SSE 实时合并属后续。实时送达在本阶段用**轮询**（前端 B 实现），后端只负责存取与状态。

## 背景

Phase 2「转人工/人工接管」是多子系统特性。DB 已预留 `ai_chat_sessions.needs_human` / `human_takeover`（`init_db.py:456-457`）。当前 `kefu_public.send_message` 每条消息都派给 OpenCode（AI）。本子项目实现：访客发起转人工 → 管理员接管（消息不再走 AI，改人工回复）→ 释放（回到 AI），全后端可用 pytest 测。

## 数据模型

`ai_chat_sessions`（列已存在，除下述新增）：
- `needs_human BOOLEAN NOT NULL DEFAULT false` — 访客已请求人工（待接管队列的标记）。
- `human_takeover BOOLEAN NOT NULL DEFAULT false` — 已被管理员接管；为真时消息不派 AI。
- **新增** `human_agent_id VARCHAR(100)`（可空）— 当前接管的管理员 `userId`，供队列展示与审计。`init_db.py` 幂等 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`。

人工回复存入 `ai_chat_messages`：`role='assistant'`（访客侧无缝当作客服回复），`content=[{'type':'text','text':<回复>}]`，`meta={'author':'human','agent_user_id':<userId>}`——靠 `meta.author` 区分人工/AI。

## 状态流转

```
访客转人工     request-human → needs_human=true
管理员接管     takeover      → human_takeover=true, human_agent_id=<admin>, needs_human=false
管理员回复     messages(POST)→ 插入 assistant 消息（meta.author=human）
管理员释放     release       → human_takeover=false, human_agent_id=null（回到 AI）
```
幂等：request-human 重复调仍 needs_human=true；takeover/release 重复调结果一致。

## 接口

### 访客（`kefu_public_bp`，url_prefix `/kefu`，X-Visitor-Id 归属校验）

- `POST /kefu/sessions/<sid>/request-human`
  - 用 `load_kefu_session(sid, vid)` 校验归属（不匹配 → 404）。
  - `set_needs_human(sid, True)`；返回 `200 {'needsHuman': True}`。

### 管理端（`kefu_admin_bp`，url_prefix `/admin/kefu`，`@require_permission('admin.kefu')`）

- `GET /admin/kefu/sessions?instance=<iid>&needs_human=1&takeover=1&status=active`
  - 返回会话队列：`[{id, kefu_instance_id, visitor_id, needs_human, human_takeover, human_agent_id, status, created_at, last_active_at, last_message}]`；`last_message` 为该会话最近一条消息的纯文本预览（截断）。过滤参数全可选（缺省不过滤；`status` 缺省 `active`）。
- `GET /admin/kefu/sessions/<sid>/messages`
  - 管理员视角取完整对话（**不校验 visitor**）：`get_kefu_session_admin(sid)` 命中且是 kefu 会话则返回 `{messages: [...]}`，否则 404。
- `POST /admin/kefu/sessions/<sid>/takeover`
  - `get_kefu_session_admin(sid)` 不存在 → 404。`takeover_session(sid, g.current_user['userId'])`。`log_operation('takeover','kefu_session',sid,None,'接管客服会话')`。返回 `200 {'humanTakeover': True}`。
- `POST /admin/kefu/sessions/<sid>/release`
  - 会话不存在 → 404。`release_session(sid)`。`log_operation('release','kefu_session',sid,None,'释放客服会话')`。返回 `200 {'humanTakeover': False}`。
- `POST /admin/kefu/sessions/<sid>/messages`（人工回复）
  - body `{content}`；`content` 空 → 400。会话不存在 → 404。**仅接管中允许**：`human_takeover` 为假 → `409 {'error':'会话未处于人工接管状态'}`。`insert_human_message(sid, content, g.current_user['userId'])` → 返回 `201 {'messageId': <id>}`。

## 消息路由改造（`routes/kefu_public.py` `send_message`）

`load_kefu_session` **扩展返回**：在元组末尾追加 `human_takeover`（现有索引 0..5 不变，新增索引 6），避免破坏 `send_message`/`events` 现有解包。

在 `send_message` 中，存完访客的 user 消息之后、调用 OpenCode 之前插入：

```python
    if sess[6]:  # human_takeover：不派 AI，等人工回复；访客轮询取回复
        return jsonify({'messageId': msg_id, 'humanTakeover': True}), 202
    # 否则：现有 OpenCode 派发路径（完全不变）
```

即接管中：访客消息照常入库、限速/附件校验照旧，仅**跳过 `send_prompt_async` + `ensure_listener`**。非接管：行为逐字不变。

## 仓库层（`utils/kefu_repo.py` 新增）

- `set_needs_human(sid: str, value: bool = True) -> bool`（rowcount>0）
- `get_kefu_session_admin(sid: str) -> dict | None` — 按 id 载入且 `kefu_instance_id IS NOT NULL`；返回 `{id, kefu_instance_id, visitor_id, needs_human, human_takeover, human_agent_id, status, opencode_session_id, workspace_path}`。
- `takeover_session(sid: str, agent_id: str) -> bool` — set `human_takeover=true, human_agent_id=agent_id, needs_human=false`。
- `release_session(sid: str) -> bool` — set `human_takeover=false, human_agent_id=NULL`。
- `insert_human_message(sid: str, text: str, agent_id: str) -> str` — 插入 `role='assistant'`、`meta={'author':'human','agent_user_id':agent_id}`，返回 `msg_id`。
- `list_kefu_sessions_admin(instance_id=None, needs_human=None, takeover=None, status='active') -> list` — 见上返回结构；参数化 WHERE 过滤 + 关联最近一条消息预览。
- 扩展 `load_kefu_session` 的 SELECT 末尾加 `human_takeover`。

## 审计

takeover / release 经 `log_operation`（自动从 `g.current_user` 取 actor），`target_type='kefu_session'`，`action` 用 `'takeover'` / `'release'`。

## 错误处理

- 访客 request-human：归属不匹配 404；会话不存在 404。
- 管理端：会话不存在/非 kefu 会话 404；人工回复非接管态 409；`content` 空 400；无 `admin.kefu` 403。

## 测试（pytest，沿用本仓库 kefu 路由测试惯例：mock app fixture + `@patch` 仓库函数）

`server/tests/test_kefu_takeover.py`：
- **request-human**：mock `load_kefu_session` 命中 → 调 `set_needs_human(sid, True)`，200；归属不匹配（load 返回 None）→ 404。
- **takeover**：`@patch` `get_kefu_session_admin`（命中）+ `takeover_session` + `log_operation`；断言 `takeover_session(sid, <userId>)` 调用、200；无 `admin.kefu` → 403。
- **release**：类似，断言 `release_session(sid)`、200。
- **人工回复**：会话 `human_takeover=true` → 调 `insert_human_message(sid, content, userId)`，201；`human_takeover=false` → 409、不插消息;`content` 空 → 400。
- **消息路由（核心）**：在 `test_kefu_public_routes` 风格下，mock `load_kefu_session` 返回 `human_takeover=true`（sess[6]=True）+ patch `OpenCodeClient` → 断言 `send_prompt_async` **未被调用**、返回 202；返回 `human_takeover=false` → 断言 `send_prompt_async` **被调用**（保护现有 AI 行为）。
- **队列**：`@patch` `list_kefu_sessions_admin` 返回样例 → 200 + 结构；过滤参数透传。
- **权限**：所有 `/admin/kefu/sessions/*` 端点 dev 角色 → 403。

## 验证

- pytest（含上述路由/状态/权限用例）。
- 后端起本地库，curl 冒烟一条链路：建会话 → `request-human`（needs_human=1）→ `takeover` → 访客 `send_message`（断言 DB 存了 user 消息、未产生 AI 回复）→ 人工 `messages`（assistant+meta.human）→ `release`（human_takeover=0）。事后清理测试会话。
- 纯后端、无 UI → 按 CLAUDE.md 不需 Playwright（前端在子项目 B 验证）。

## 非目标（本子项目不做）

- 任何前端（访客「转人工」按钮 + 接管期轮询、管理端控制台）→ 子项目 B。
- SSE 实时合并（人工消息推进访客 events 流）→ 后续；本阶段访客靠轮询。
- 会话分配/抢占/多客服协作、自动转人工规则（关键词/情绪）→ 不做。

## 文档同步

`docs/user-guide/ai/smart-customer-service.md` 增补「人工接管（后端接口）」小节：状态机、访客 request-human、管理端 takeover/release/人工回复/队列接口与权限；标注前端控制台属后续阶段。
