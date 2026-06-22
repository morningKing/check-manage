# 手动补写记忆（默认提炼 + verbatim 开关）— 设计文档

> 状态：设计已与需求方逐项确认。日期：2026-06-22

## 1. 目标

让用户在「我的记忆」抽屉里**手动补写**一条长期记忆，补救被动逐轮抽取的遗漏。默认走 mem0 的 LLM 提炼（`infer=True`）；勾选「原样保存」则 `infer=False`，把原文钉死、不提炼。

## 2. 背景

- 现状 `/ai/memories` 只有 `GET`（列表）和 `DELETE`，没有"添加"。用户唯一的补写途径是让智能体调 MCP `memory_add`，不直接。
- mem0 2.0.7 的 `Memory.add(messages, *, user_id=None, ..., infer: bool = True, ...)` —— 已确认支持 `infer` 参数。`infer=False` 跳过 LLM 事实抽取/去重，**仍做嵌入**直接入库（verbatim）。
- 记忆按 `user_id` 分区；apiKey 缺失时 `get_memory()` 返回 None，整层 no-op。
- 关键认知：检索靠**向量相似度（嵌入）**，与是否过 LLM 提炼无关。verbatim 仍可检索，但宜"短而原子"以保精度；这是设计鼓励用户写一句话事实的原因。

## 3. 关键决策（已确认）

| 决策 | 选择 |
|---|---|
| 默认行为 | `infer=True`（mem0 提炼成简洁事实） |
| verbatim 开关 | 勾选 → `infer=False`，原样保存 |
| 作用域 | 写**当前登录用户**（`user_id`），不接受任意 user id |
| 与启用开关关系 | **不**受启用开关影响（属"记忆管理"动作）；只要配了 apiKey 即可写 |
| 长度上限 | 2000 字符；鼓励短而原子 |
| 落地范围 | 独立分支 `feat/manual-memory-write`，不并入暂缓的 scope-toggle |

## 4. 后端

**`server/utils/memory.py`** 新增：
```python
def add_memory_text(user_id, text, infer=True):
    """手动补写一条记忆。infer=False 为 verbatim（原样、不提炼，仍嵌入）。
    返回 True/False 表示是否写入（mem0 不可用/降级时 False）。"""
    m = get_memory()
    if m is None or not user_id or not text:
        return False
    try:
        _on_mem_thread(lambda: m.add([{'role': 'user', 'content': text}],
                                     user_id=user_id, infer=infer))
        return True
    except Exception as e:
        logger.warning('mem0 manual add failed: %s', e)
        return False
```
- 钉单线程 executor（与既有所有 mem0 调用一致）。
- 返回布尔即可——前端写完**重新拉 `list_memories`** 显示最新列表，不依赖 `add` 的返回形状。

**`server/routes/ai.py`** 新增 `POST /ai/memories`：
```python
@ai_bp.route('/memories', methods=['POST'])
@login_required
def add_my_memory():
    user = g.current_user
    body = request.get_json(silent=True) or {}
    text = (body.get('text') or '').strip()
    verbatim = bool(body.get('verbatim'))
    if not text:
        return jsonify({'error': '内容不能为空'}), 400
    if len(text) > 2000:
        return jsonify({'error': '内容过长（上限 2000 字符）'}), 400
    if get_memory() is None:
        return jsonify({'error': '记忆功能未配置（缺少 API Key 或未启用底层）', 'code': 'MEMORY_UNAVAILABLE'}), 409
    ok = add_memory_text(user['userId'], text, infer=not verbatim)
    if not ok:
        return jsonify({'error': '写入失败'}), 500
    return jsonify({'ok': True, 'memories': list_memories(user['userId'])})
```
- import 处补 `add_memory_text`、`get_memory`、`request`（确认 `request` 已 import）。
- `get_memory()` 判空给出 409 明确错误（区别于"内容非法"的 400）。

## 5. 前端

**`src/api/aiChat.ts`** 新增：
```ts
export function addMemory(text: string, verbatim = false) {
  return post<{ ok: boolean; memories: AiMemory[] }>('/ai/memories', { text, verbatim })
}
```

**`src/components/ai-chat/MemoryManager.vue`**：列表上方加补写区：
- 多行输入（`ElInput type="textarea"`，placeholder 提示"写一句话关键事实，如：负责 PostgreSQL 运维"）。
- 一个开关 `ElSwitch` /复选「原样保存（不提炼）」，旁一行小字：默认会被 AI 提炼成简洁事实；原样保存适合一句话关键事实。
- 「添加」按钮：调 `addMemory(text, verbatim)`，成功后用返回的 `memories` 刷新 `items`、清空输入、`ElMessage.success`；空内容禁用按钮；写入中 loading。
- 失败（含 409）用 `ElMessage.error` 显示后端 `error` 文案。

## 6. 测试

**后端 `server/tests/test_memory_manual_add.py`**（或并入 `test_memory.py`）：
- `add_memory_text` 默认 `infer=True`；`infer=False` 透传（patch `get_memory` 返回 mock，断言 `m.add` 收到的 `infer`）。
- `POST /ai/memories`：空 text → 400；超 2000 → 400；`get_memory()` 为 None → 409；正常 → 200 且 `add_memory_text` 被以当前 user + 正确 infer 调用；`verbatim:true` → `infer=False`。
- 只写当前登录用户（端点不接受 body 里的 user id）。

**前端**：`vue-tsc --noEmit` clean。

## 7. 文档

- `docs/user-guide/ai/long-term-memory.md`：补"手动补写记忆：在『我的记忆』里添加；默认 AI 提炼，勾『原样保存』则不提炼；建议短而原子（利于检索）；与自动抽取互补"。
- `CLAUDE.md` AI 记忆段：记 `POST /ai/memories` + `add_memory_text(infer=)` + verbatim 语义。

## 8. 风险与边界

- **verbatim 检索质量**：原样长文嵌入更糊、无去重——靠 2000 上限 + UI 文案引导"短而原子"缓解；不阻止，但提示。
- **不绕过 mem0**：始终用 mem0 `add(infer=...)`，**不**直接写 chromadb，保持存储形状一致、`search` 的 user_id 过滤可靠。
- **启用开关解耦**：手动写属管理动作，全局/工作空间/用户开关关闭时仍可手动写（只要 apiKey 在）；与"自动注入/抽取被关"不矛盾——用户明确的手动操作应生效。
- **降级**：mem0 不可用一律明确报错（409/500），绝不静默吞。
- **作用域**：单一内聚小特性，单一 spec/plan。
