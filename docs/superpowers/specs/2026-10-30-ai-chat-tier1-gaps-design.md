# AI 会话页第一档差距补齐设计（消息反馈 / 会话导出 / 图片输入）

- 日期：2026-10-30
- 来源：同日「AI 会话页 vs Claude 差距分析」研究（spike），结论分档后用户批准做第一档
- 对标口径：claude.ai + Claude Code
- 状态：已实施（2026-10-31 SDD 10 任务 + 终审修复波收口；实施记录见 docs/superpowers/plans/2026-10-30-ai-chat-tier1-gaps.md）

## 0. 背景与范围

差距分析产出三档清单，本设计只覆盖第一档三项（原四项中「Artifact 版本回看」经代码核实
**已存在**——`ArtifactPreview.vue:42-47` 有版本下拉切换，从消息历史累积版本，剔除）：

| 项 | 内容 | 决策记录 |
|----|------|----------|
| A | 消息点赞/点踩 | 存储用独立小表 |
| B | 会话导出 | 仅 Markdown 格式 |
| C | 图片输入 | spike 失败即砍掉整个功能 |

**明确不做**（第二/三档或范围外）：消息分支（C3）、Plan Mode（K1）、checkpoint rewind（K2）、
Artifact 版本 diff、反馈 reason 输入框、管理端反馈聚合报表、批任务子会话图片派发。

## Feature A：消息点赞/点踩

### A.1 存储

新表 `ai_chat_message_feedback`：

```sql
CREATE TABLE IF NOT EXISTS ai_chat_message_feedback (
    message_id VARCHAR(100) PRIMARY KEY
        REFERENCES ai_chat_messages(id) ON DELETE CASCADE,
    session_id VARCHAR(100) NOT NULL REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
    rating     SMALLINT NOT NULL CHECK (rating IN (1, -1)),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chat_feedback_sess ON ai_chat_message_feedback(session_id);
```

- assistant 消息打分；无行 = 未评分。取消评分 = 删除行。
- 落点三处：`server/db_schema/core.py`（CREATE TABLE）+ `server/migrations/` 新迁移文件
  （存量库）+ `app.py` 迁移注册块（**双注册**，init_db 自动扫描不覆盖生产重启路径）。

### A.2 API

`POST /api/ai/chat/sessions/<sid>/messages/<mid>/feedback`

- body：`{"rating": 1 | -1 | 0}`，`0` 表示清除（删行）。
- 校验：`login_required` + `_load_session_for_user`（会话属主）；mid 必须属于 sid 且
  `role='assistant'`（仅助手消息可评）；rating 必须是 1/-1/0。
- 幂等：重复同值 upsert；响应 `{"messageId": mid, "rating": <最终值>}`。
- 审计：普通用户会话内操作，不走 `log_operation`（那是管理端点约定）。

### A.3 前端

- **读取**：`get_messages` 改为 `LEFT JOIN ai_chat_message_feedback`，assistant 消息响应体
  多带 `rating` 字段（无反馈为 null）。单次查询无 N+1。批子会话消息走独立管理端点
  （`ai_batch_admin.py:165` → `get_subtask_messages`），属只读场景**不加** rating——评分
  只发生在主聊天页（范围外登记，不做）。
- **UI**：消息悬浮操作行（copy/edit/regenerate 一排，`AiChatView.vue:1766` 附近）加 👍/👎
  两个图标钮。语义：未评→评；点击已激活的→清除；点另一个→切换。乐观更新，失败回滚+
  ElMessage 报错。aria-label 与 data-test 齐备（E2E 用）。
- store：`aiChat.ts` 增加 `setFeedback(mid, rating)` action，本地乐观改 `messages` 缓存。

### A.4 与 SkillOpt 的关系（v1 不接线）

v1 只落库。轨迹分析/SkillOpt 拟合要消费反馈属后续独立决策——表结构已带 session_id，将来
JOIN 即可。**本设计不包含**拟合口径改动。

### A.5 测试

- pytest：feedback API（权限 403/404、role 校验、rating 校验、幂等、0 清除）；
  get_messages JOIN 后 rating 字段形状。
- 组件测试：toggle 语义（未评→1→清除→-1）、乐观更新与失败回滚。
- E2E（真实链路）：mock LLM 栈发一轮 → 点赞 → 刷新页面 rating 保留 → 切点踩 → 取消。

## Feature B：会话导出 Markdown

### B.1 后端

`GET /api/ai/chat/sessions/<sid>/export.md`

- 认证：`login_required` + 会话属主；查询参数认证走既有 `authParam()`（同
  `downloadFileUrl`，`aiChat.ts:559`）以支持直链下载。
- 新渲染函数 `render_export_markdown(sid)`（放 `ai_chat.py`，与
  `render_history_block` 同文件但**不复用它**——那是 LLM 恢复用的有损渲染，人读归档要保留
  工具调用细节）：

  ```
  # <会话标题>
  > 导出时间：YYYY-MM-DD HH:mm ｜ 消息数：N

  ## 🧑 用户
  （text parts 直出；@提及保持原样）

  ## 🤖 助手
  （text parts 直出，Markdown 保真）
  <details><summary>🔧 tool_name</summary>

  ```json
  （args 摘要）
  ```
  </details>

  （file parts → `- 📎 <name>`；image parts → `- 🖼️ <name>`；subtask parts → 折叠块）
  ```

- 响应头：`Content-Disposition: attachment; filename="<安全化标题>-YYYYMMDD-HHmm.md"`；
  标题做文件名安全化（替换路径分隔符/控制符/空格，strip 长度）。
- 空会话：只输出 header 块，200。
- 长会话：全量导出不截断（归档语义）。

### B.2 前端

`SessionRowActions.vue` 更多菜单加「导出 Markdown」项（图标 Download，放「清空会话」与
「移动到分组」之间），点击 `window.open(exportUrl + authParam)`。

### B.3 测试

- pytest：渲染函数覆盖 text/tool/file/image/subtask 五类 parts；文件名安全化（含路径分隔符、
  emoji 标题、超长标题）；404/403。
- E2E：更多菜单 → 导出 → 下载内容断言（文件以 `# <标题>` 开头、含消息正文）。

## Feature C：图片输入

### C.1 Phase 0 spike（闸门，砍掉即整个 Feature C 砍掉）

对 pinned oc serve 版本实测 `POST /session/{id}/prompt_async` 的 parts 是否接受图片：

- 候选形态 1：`{"type": "file", "mime": "image/png", "url": "data:image/png;base64,..."}`
- 候选形态 2：`{"type": "file", "mime": "image/png", "url": "file://<workspace 内绝对路径>"}`

通过判据：vision 模型回复能描述图片内容。spike 脚本与结果 JSON 落
`docs/ai-testing/evidence/`。**失败 → Feature C 整体砍掉**（用户已确认）。

**结论（2026-10-31 实测，闸门通过）**：两形态均可用——pinned oc serve 1.26 +
`opencode-go/deepseek-v4.1-flash`（`capabilities.input.image=true`，平台默认模型）
对 1×1 红色探测图均正确回复含"红"（dataurl→"红色圆形"，filepath→"纯红色图片"）。
`IMAGE_PART_MODE = dataurl`（自包含、无文件系统路径耦合，用户上传图无需落入 oc
serve 可读目录；filepath 实测亦通过可作 fallback）。证据：
`image-parts-spike-20261031-104211-dataurl.json`、
`image-parts-spike-20261031-104237-filepath.json`（`...103928-dataurl.json` 为
spike 脚本消息信封解析 bug 修复前样本，作废留档）。Task 9/10 继续。

### C.2 后端

- `send_message`（`ai_chat.py`）：attachments 循环里按 mime 分流——文本文件维持既有
  `_read_text_attachment` 内联；图片 mime（`image/png|jpeg|webp|gif` 白名单）→
  `stored_parts` 追加 `{'type': 'image', 'name': ..., 'path': rel}`（**不**文本内联、不进
  search text；`_scan` 现有逻辑已忽略非 text parts，无搜索污染）。
- `opencode_client.send_prompt_async` 扩展新可选参数 `image_parts`（列表，元素
  `{name, path, mime}`）→ OpenCode body parts 追加图片 part（形态按 C.1 spike 结论）。
  不改变既有 text/agent parts 语义，缺省 None 行为完全不变（现有调用方零改动）。
- 消息持久化：`ai_chat_messages.content` 的 parts 原样存 image part，`get_messages` 返回，
  搜索提数不受影响。

### C.3 前端

- 输入区：textarea `paste` 事件（`clipboardData.items` 里的 image）+ 卡片 `drop` 事件 +
  「添加附件或技能」文件选择器 `accept` 增 `image/*`；三种入口统一走既有 `uploadFile`
  （落 `uploads/`）。
- 附件芯片：image mime 显示图片图标（现芯片统一 Document 图标，按 mime 分流图标）。
- 消息渲染：user 消息渲染 image parts 为 `<img>`（复用 workspace 文件预览 URL 形态，同
  ChatFile 预览链路），带点击放大（复用 ArtifactPreview 式遮罩或简单 ElImageViewer）。
- 非视觉模型：OpenCode/上游报错走既有错误卡展示，不做特殊兜底。

### C.4 测试

- pytest：mime 分流（文本照旧内联/图片不内联）、stored_parts 形状、client parts 拼装、
  mime 白名单外拒收。
- E2E（mock LLM 栈）：粘贴/选择图片 → 芯片显示 → 发送 → user 气泡显示图片 →
  网络层断言 prompt parts 含 image part（mock 栈可拦截）。

## 实施顺序与节奏

A → B → C 串行（A 最小先行验证链路，C 的 spike 可在 A/B 进行期间后台跑）。每项独立
提交、独立可回滚；遵循 TDD 与真实链路 E2E 的既有测试要求。

## 风险与开放点

| 风险 | 处置 |
|------|------|
| OpenCode 不收图片 part | C.1 spike 前置闸门，失败砍 C |
| 长会话导出体量大 | 归档语义不截断；若实测超慢再议流式/分块（登记不做） |
| meta/反馈并发 | 反馈独立表，与消息表 meta 写入零交集 |
| 图片消息历史兼容 | image part 类型新增，旧消息无此类型，渲染端按类型分流天然兼容 |
