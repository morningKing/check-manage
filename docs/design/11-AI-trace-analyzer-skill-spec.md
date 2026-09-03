# trace-analyzer Skill 详细实现方案

> 本文档是 [11-AI执行轨迹分析.md](./11-AI执行轨迹分析.md) 的子文档，详细定义 `trace-analyzer` Skill 的 Prompt 设计、MCP 工具接口、工具调用流程和输入输出格式。

## 1. Skill 概述

`trace-analyzer` 是一个 OpenCode Skill，运行在 OpenCode Agent 内部，通过调用 MCP 工具获取执行轨迹数据，分析失败根因，生成优化建议。

**核心理念**：Skill 只是一份 SKILL.md 指令文件，告诉 Agent "怎么做"；真正的数据获取由 MCP 工具完成；分析推理由 LLM 完成。

```
用户: "分析会话 sess_xxx 为什么失败了"
  │
  ▼
OpenCode Agent 读取 trace-analyzer/SKILL.md
  │
  ▼
Agent 按 SKILL.md 指令依次调用:
  ① analyze_trace MCP 工具 → 获取结构化 trace
  ② query_sessions MCP 工具 → 获取同类成功案例
  ③ 推理分析（LLM 内部完成）
  ④ save_artifact MCP 工具 → 输出分析报告
  │
  ▼
Agent 返回分析结论 + 优化建议
```

## 2. SKILL.md 完整内容

```markdown
---
name: trace-analyzer
description: 分析 AI 会话执行轨迹，诊断失败根因，给出优化建议
---

# 执行轨迹分析专家

你是平台 AI 系统的执行轨迹分析专家。你的任务是分析指定会话的完整执行轨迹，
找出"跑偏"的根因，并给出可操作的优化建议。

## 何时触发

当用户说以下内容时使用此技能：
- "分析会话 xxx"
- "为什么这个任务失败了"
- "帮我看看这个执行轨迹"
- "诊断这个扫描任务"
- "这个批任务为什么有问题"

## 分析流程

严格按以下步骤执行，不要跳步：

### 第一步：获取轨迹数据

调用 `analyze_trace` 工具，传入用户提供的 session_id。

```
analyze_trace(session_id="sess_xxx", include_subtasks=true)
```

从返回的结构化数据中理解：
- 会话来源（交互/批任务/扫描/行操作）
- 使用的 Agent 和 Model
- 工具调用序列（每个工具的名称、输入、输出、状态、耗时）
- 子代理委托链（哪些任务被分发给了子代理）
- 性能指标（总耗时、Token 消耗、成本）
- 错误信息（如果有）
- 工作区文件变更

### 第二步：审查工具调用序列

逐个检查工具调用，识别以下异常模式：

| 模式 | 检测方法 | 含义 |
|------|----------|------|
| 工具循环 | 连续 3+ 次相同工具 + 相同输入 | Agent 陷入了死循环 |
| 无效调用 | 工具返回错误但 Agent 继续调用 | Agent 未正确处理错误 |
| 工具选择错误 | 用了复杂工具做简单任务 | Agent 对工具能力理解有误 |
| 超时 | durationMs > 30000 | 工具执行过慢，可能卡住 |
| 空输出 | status=completed 但 result 为空 | 工具执行了但没有产出 |

### 第三步：审查推理过程

如果 trace 中包含 reasoning tokens，检查：
- 是否有矛盾的判断（先说"应该用 A"，后来又说"应该用 B"）
- 是否有错误的假设（引用了不存在的集合或字段）
- 是否有遗漏的关键信息（用户说了但 Agent 没注意到的约束）

### 第四步：审查子代理委托

如果存在子代理（subtask_use），检查：
- 子代理是否正确完成了任务
- 子代理的结果是否被父代理正确使用
- 是否有子代理死循环（嵌套深度 > 3）
- 子代理的 prompt 是否足够清晰

### 第五步：对比成功案例（可选）

如果用户提供了参考信息或你知道同类任务的成功模式，调用 `query_sessions` 查找历史成功案例：

```
query_sessions(
  source_type="scan",
  status="completed",
  limit=5,
  created_after="2026-08-01"
)
```

对比失败案例和成功案例的差异：
- 工具调用序列有什么不同？
- Agent 的推理路径有什么不同？
- 输出结果有什么不同？

### 第六步：生成诊断结论

综合以上分析，输出结构化的诊断结论：

```json
{
  "root_cause": "一句话描述根因",
  "failure_chain": [
    "步骤1: Agent 做了什么",
    "步骤2: 为什么这导致了问题",
    "步骤3: 最终如何表现为失败"
  ],
  "suggestions": [
    {
      "type": "prompt_rewrite",
      "target": "ai_scan_tasks:task_xxx:prompt_template",
      "description": "当前 prompt 未指定输出格式，建议追加...",
      "effort": "low",
      "impact": "high",
      "auto_applicable": false,
      "suggested_value": "追加的文本"
    }
  ],
  "confidence": 0.85,
  "evidence_summary": "关键证据摘要"
}
```

## 可用工具

### analyze_trace

获取指定会话的结构化执行轨迹。

**参数**：
- `session_id`（必填）：会话 ID
- `include_subtasks`（可选，默认 true）：是否包含子代理轨迹
- `include_reasoning`（可选，默认 false）：是否包含 reasoning tokens（可能很长）

**返回**：
```json
{
  "session": {
    "id": "sess_xxx",
    "user_id": "user_xxx",
    "status": "failed",
    "error_message": "JSON 解析失败...",
    "batch_id": "batch_xxx",
    "scan_task_id": null,
    "agent": "build",
    "model": "qwen-plus",
    "created_at": "2026-09-02T10:00:00Z",
    "last_active_at": "2026-09-02T10:05:30Z"
  },
  "source": {
    "type": "batch",
    "id": "batch_xxx",
    "name": "数据导入批任务"
  },
  "performance": {
    "total_duration_ms": 330000,
    "total_tokens_input": 45000,
    "total_tokens_output": 12000,
    "total_cost": 0.0234,
    "message_count": 8,
    "tool_call_count": 12
  },
  "tool_calls": [
    {
      "seq": 1,
      "name": "read",
      "input": {"file_path": "/workspace/uploads/data.csv"},
      "result": "file content...",
      "status": "completed",
      "duration_ms": 1200
    },
    {
      "seq": 2,
      "name": "query_collection",
      "input": {"collection": "orders", "filter": {"status": "pending"}},
      "result": {"data": [...], "total": 50},
      "status": "completed",
      "duration_ms": 3400
    }
  ],
  "subtasks": [
    {
      "id": "ses_child_xxx",
      "agent": "explore",
      "description": "分析数据结构",
      "status": "completed",
      "prompt": "请分析 uploads/data.csv 的列结构...",
      "tool_call_count": 3,
      "duration_ms": 45000
    }
  ],
  "messages_summary": [
    {
      "role": "user",
      "text_preview": "请处理这个数据文件...",
      "seq": 1
    },
    {
      "role": "assistant",
      "text_preview": "我来分析这个数据文件...",
      "tool_calls": ["read", "query_collection"],
      "seq": 2
    }
  ],
  "files_changed": [
    {"path": "outputs/result.json", "status": "added"}
  ]
}
```

### query_sessions

查询历史会话，支持多维过滤。

**参数**：
- `source_type`（可选）：`interactive` | `batch` | `scan` | `row_action` | `open_api`
- `status`（可选）：`completed` | `failed` | `active`
- `agent`（可选）：Agent 名称
- `scan_task_id`（可选）：扫描任务 ID
- `batch_id`（可选）：批任务 ID
- `keyword`（可选）：标题/错误消息关键词
- `created_after`（可选）：起始时间
- `created_before`（可选）：结束时间
- `limit`（可选，默认 10）：返回数量

**返回**：
```json
{
  "sessions": [
    {
      "id": "sess_xxx",
      "status": "completed",
      "source_type": "scan",
      "agent": "build",
      "error_message": null,
      "duration_ms": 120000,
      "tokens_total": 25000,
      "cost": 0.0123,
      "tool_call_count": 8,
      "created_at": "2026-09-01T15:00:00Z",
      "last_message_preview": "处理完成，已更新 5 条记录"
    }
  ],
  "total": 42
}
```

### save_artifact

将分析报告保存为文件（已有工具，无需新增）。

**参数**：
- `filename`：文件名（如 `trace-analysis-sess_xxx.md`）
- `content`：文件内容

## 注意事项

1. **不要编造数据**：所有分析必须基于 `analyze_trace` 返回的真实数据，不要猜测
2. **不要修改业务数据**：分析过程是只读的，不要调用会修改数据的工具
3. **控制 token 消耗**：如果 trace 数据很长，先分析摘要（messages_summary），只在需要时请求完整内容
4. **输出中文**：分析报告和建议使用中文，因为平台用户是中文环境
5. **结构化输出**：最终结论必须是 JSON 格式，方便平台解析和存储
```

## 3. MCP 工具实现规范

### 3.1 `analyze_trace` 工具

**文件**：`mcp-server/tools/analyze_trace.py`

```python
"""Tool: analyze_trace — 从平台 DB 构建指定会话的结构化执行轨迹。

只读查询，不修改任何数据。返回会话元数据、工具调用序列、子代理树、
性能指标、文件变更等。
"""

import json
import mcp.types as types
from db import get_db
from context import ToolContext

NAME = "analyze_trace"

TOOL = types.Tool(
    name=NAME,
    description=(
        "获取指定 AI 会话的结构化执行轨迹，包括工具调用序列、子代理委托链、"
        "性能指标（耗时/Token/成本）、错误信息、工作区文件变更。"
        "用于分析会话失败原因、诊断执行问题。"
        "参数: session_id(必填), include_subtasks(默认true), include_reasoning(默认false)。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "会话 ID（sess_ 开头）",
            },
            "include_subtasks": {
                "type": "boolean",
                "description": "是否包含子代理轨迹",
                "default": True,
            },
            "include_reasoning": {
                "type": "boolean",
                "description": "是否包含 reasoning tokens（可能很长）",
                "default": False,
            },
        },
        "required": ["session_id"],
        "additionalProperties": False,
    },
)


def handle(input: dict, ctx: ToolContext) -> dict:
    """构建指定会话的结构化执行轨迹"""
    session_id = input["session_id"]
    include_subtasks = input.get("include_subtasks", True)
    include_reasoning = input.get("include_reasoning", False)

    with get_db() as conn:
        cur = conn.cursor()

        # 1. 会话元数据
        cur.execute("""
            SELECT s.id, s.user_id, s.title, s.status, s.error_message,
                   s.batch_id, s.scan_task_id, s.source_record_id,
                   s.agent, s.model, s.created_at, s.last_active_at,
                   b.name AS batch_name, b.agent AS batch_agent,
                   st.name AS scan_task_name
            FROM ai_chat_sessions s
            LEFT JOIN ai_chat_batches b ON b.id = s.batch_id
            LEFT JOIN ai_scan_tasks st ON st.id = s.scan_task_id
            WHERE s.id = %s
        """, (session_id,))
        session_row = cur.fetchone()
        if not session_row:
            return {"error": f"会话 {session_id} 不存在"}

        session = _dict_from_row(session_row, [
            "id", "user_id", "title", "status", "error_message",
            "batch_id", "scan_task_id", "source_record_id",
            "agent", "model", "created_at", "last_active_at",
            "batch_name", "batch_agent", "scan_task_name"
        ])

        # 2. 触发来源
        source = _resolve_source(session)

        # 3. 所有消息（解析 content JSONB）
        cur.execute("""
            SELECT id, role, content, meta, created_at
            FROM ai_chat_messages
            WHERE session_id = %s
            ORDER BY created_at
        """, (session_id,))
        message_rows = cur.fetchall()

        tool_calls = []
        messages_summary = []
        total_duration = 0
        total_tokens_in = 0
        total_tokens_out = 0
        total_cost = 0.0

        for row in message_rows:
            msg = _dict_from_row(row, ["id", "role", "content", "meta", "created_at"])
            content = msg["content"] or []
            meta = msg["meta"] or {}

            if msg["role"] == "assistant":
                # 提取工具调用
                for part in content:
                    if part.get("type") == "tool_use":
                        tc = {
                            "name": part.get("name", ""),
                            "input": part.get("input"),
                            "result": _truncate(part.get("result"), 500),
                            "status": part.get("status", ""),
                            "duration_ms": part.get("durationMs", 0),
                        }
                        if not include_reasoning:
                            tc.pop("result", None)  # 默认不返回完整 result
                        tool_calls.append(tc)
                    elif part.get("type") == "reasoning" and include_reasoning:
                        tool_calls.append({
                            "name": "_reasoning",
                            "input": None,
                            "result": _truncate(part.get("text"), 1000),
                            "status": "completed",
                            "duration_ms": 0,
                        })

                # 聚合性能指标
                total_duration += meta.get("durationMs", 0)
                total_tokens_in += meta.get("tokensInput", 0)
                total_tokens_out += meta.get("tokensOutput", 0)
                total_cost += meta.get("cost", 0)

            # 消息摘要
            text_preview = ""
            for part in content:
                if part.get("type") == "text" and part.get("text"):
                    text_preview = part["text"][:100]
                    break
            tool_names = [p["name"] for p in content if p.get("type") == "tool_use"]

            messages_summary.append({
                "role": msg["role"],
                "text_preview": text_preview,
                "tool_calls": tool_names if tool_names else None,
            })

        # 4. 子代理轨迹
        subtasks = []
        if include_subtasks:
            cur.execute("""
                SELECT id, agent, description, status, error_message,
                       prompt, created_at, completed_at
                FROM ai_chat_subtasks
                WHERE root_session_id = %s
                ORDER BY created_at
            """, (session_id,))
            for st_row in cur.fetchall():
                st = _dict_from_row(st_row, [
                    "id", "agent", "description", "status", "error_message",
                    "prompt", "created_at", "completed_at"
                ])
                # 子代理的消息统计
                cur.execute("""
                    SELECT COUNT(*) AS msg_count,
                           SUM((meta->>'durationMs')::int) AS duration,
                           SUM((meta->>'tokensInput')::int +
                               (meta->>'tokensOutput')::int) AS tokens
                    FROM ai_chat_subtask_messages
                    WHERE subtask_id = %s
                """, (st["id"],))
                stats = cur.fetchone()
                st["message_count"] = stats[0] or 0
                st["duration_ms"] = stats[1] or 0
                st["tokens_total"] = stats[2] or 0
                subtasks.append(st)

        # 5. 工作区文件变更
        cur.execute("""
            SELECT path, status
            FROM ai_chat_session_files
            WHERE session_id = %s
            ORDER BY path
        """, (session_id,))
        files_changed = [_dict_from_row(r, ["path", "status"]) for r in cur.fetchall()]

    return {
        "session": session,
        "source": source,
        "performance": {
            "total_duration_ms": total_duration,
            "total_tokens_input": total_tokens_in,
            "total_tokens_output": total_tokens_out,
            "total_cost": round(total_cost, 6),
            "message_count": len(message_rows),
            "tool_call_count": len(tool_calls),
        },
        "tool_calls": tool_calls,
        "subtasks": subtasks,
        "messages_summary": messages_summary,
        "files_changed": files_changed,
    }


def _resolve_source(session: dict) -> dict:
    if session.get("scan_task_id"):
        return {"type": "scan", "id": session["scan_task_id"],
                "name": session.get("scan_task_name", ""),
                "record_id": session.get("source_record_id")}
    if session.get("batch_id"):
        return {"type": "batch", "id": session["batch_id"],
                "name": session.get("batch_name", "")}
    if session.get("api_key_id"):
        return {"type": "open_api", "id": session["api_key_id"]}
    return {"type": "interactive"}


def _dict_from_row(row, keys: list) -> dict:
    return {k: row[i] for i, k in enumerate(keys)}


def _truncate(text, max_len: int):
    if not text:
        return text
    s = str(text)
    return s[:max_len] + "..." if len(s) > max_len else s
```

### 3.2 `query_sessions` 工具

**文件**：`mcp-server/tools/query_sessions.py`

```python
"""Tool: query_sessions — 查询历史会话，支持多维过滤。

只读查询，供 trace-analyzer Skill 查找同类成功/失败案例做对比分析。
"""

import mcp.types as types
from db import get_db
from context import ToolContext

NAME = "query_sessions"

TOOL = types.Tool(
    name=NAME,
    description=(
        "查询历史 AI 会话，支持按来源类型、状态、Agent、关键词等过滤。"
        "用于查找同类任务的成功/失败案例做对比分析。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "source_type": {
                "type": "string",
                "enum": ["interactive", "batch", "scan", "row_action", "open_api"],
                "description": "按来源类型过滤",
            },
            "status": {
                "type": "string",
                "enum": ["completed", "failed", "active", "cancelled"],
                "description": "按状态过滤",
            },
            "agent": {
                "type": "string",
                "description": "按 Agent 名称过滤",
            },
            "scan_task_id": {
                "type": "string",
                "description": "按扫描任务 ID 过滤",
            },
            "batch_id": {
                "type": "string",
                "description": "按批任务 ID 过滤",
            },
            "keyword": {
                "type": "string",
                "description": "标题或错误消息关键词",
            },
            "created_after": {
                "type": "string",
                "description": "起始时间（ISO 8601）",
            },
            "created_before": {
                "type": "string",
                "description": "结束时间（ISO 8601）",
            },
            "limit": {
                "type": "integer",
                "description": "返回数量（默认 10，最大 50）",
                "default": 10,
            },
        },
        "required": [],
        "additionalProperties": False,
    },
)


def handle(input: dict, ctx: ToolContext) -> dict:
    """查询历史会话"""
    conditions = []
    params = []

    # 来源类型过滤
    if input.get("source_type"):
        st = input["source_type"]
        if st == "scan":
            conditions.append("s.scan_task_id IS NOT NULL")
        elif st == "batch":
            conditions.append("s.batch_id IS NOT NULL AND s.scan_task_id IS NULL")
        elif st == "open_api":
            conditions.append("s.api_key_id IS NOT NULL")
        elif st == "interactive":
            conditions.append("s.batch_id IS NULL AND s.api_key_id IS NULL")

    # 状态过滤
    if input.get("status"):
        conditions.append("s.status = %s")
        params.append(input["status"])

    # Agent 过滤
    if input.get("agent"):
        conditions.append("(s.agent = %s OR b.agent = %s)")
        params.extend([input["agent"], input["agent"]])

    # 扫描任务过滤
    if input.get("scan_task_id"):
        conditions.append("s.scan_task_id = %s")
        params.append(input["scan_task_id"])

    # 批任务过滤
    if input.get("batch_id"):
        conditions.append("s.batch_id = %s")
        params.append(input["batch_id"])

    # 关键词搜索
    if input.get("keyword"):
        conditions.append(
            "(s.title ILIKE %s OR s.error_message ILIKE %s OR s.last_message_preview ILIKE %s)"
        )
        kw = f"%{input['keyword']}%"
        params.extend([kw, kw, kw])

    # 时间范围
    if input.get("created_after"):
        conditions.append("s.created_at >= %s")
        params.append(input["created_after"])
    if input.get("created_before"):
        conditions.append("s.created_at <= %s")
        params.append(input["created_before"])

    # 只返回有消息的会话（排除空会话）
    conditions.append("s.status IN ('completed', 'failed', 'cancelled')")

    where = " AND ".join(conditions) if conditions else "1=1"
    limit = min(input.get("limit", 10), 50)

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT s.id, s.status, s.agent, s.error_message, s.created_at,
                   s.last_active_at, s.last_message_preview, s.scan_task_id,
                   s.batch_id, s.api_key_id,
                   b.agent AS batch_agent, b.name AS batch_name,
                   COUNT(m.id) AS msg_count,
                   SUM((m.meta->>'durationMs')::int) AS total_duration,
                   SUM((m.meta->>'tokensInput')::int +
                       (m.meta->>'tokensOutput')::int) AS total_tokens,
                   SUM((m.meta->>'cost')::numeric) AS total_cost
            FROM ai_chat_sessions s
            LEFT JOIN ai_chat_batches b ON b.id = s.batch_id
            LEFT JOIN ai_chat_messages m ON m.session_id = s.id AND m.role = 'assistant'
            WHERE {where}
            GROUP BY s.id, s.status, s.agent, s.error_message, s.created_at,
                     s.last_active_at, s.last_message_preview, s.scan_task_id,
                     s.batch_id, s.api_key_id, b.agent, b.name
            ORDER BY s.created_at DESC
            LIMIT %s
        """, params + [limit])

        sessions = []
        for row in cur.fetchall():
            source_type = "interactive"
            if row[8]:  # scan_task_id
                source_type = "scan"
            elif row[7]:  # batch_id
                source_type = "batch"
            elif row[9]:  # api_key_id
                source_type = "open_api"

            sessions.append({
                "id": row[0],
                "status": row[1],
                "agent": row[2] or row[11],  # session.agent or batch.agent
                "error_message": _truncate(row[3], 200),
                "source_type": source_type,
                "created_at": row[4].isoformat() if row[4] else None,
                "duration_ms": row[13] or 0,
                "tokens_total": row[14] or 0,
                "cost": float(row[15] or 0),
                "message_count": row[12] or 0,
                "last_message_preview": _truncate(row[6], 100),
            })

    return {"sessions": sessions, "total": len(sessions)}


def _truncate(text, max_len: int):
    if not text:
        return text
    s = str(text)
    return s[:max_len] + "..." if len(s) > max_len else s
```

## 4. 工具调用流程

### 4.1 典型场景：扫描任务失败诊断

```
用户: "分析会话 sess_abc123，扫描任务处理失败了"

Agent 内部执行流程:
│
├─ Step 1: 读取 SKILL.md，理解分析流程
│
├─ Step 2: 调用 analyze_trace
│   input:  {"session_id": "sess_abc123", "include_subtasks": true}
│   output: {
│     "session": {"status": "failed", "error_message": "JSON 解析失败", "agent": "build"},
│     "source": {"type": "scan", "name": "订单审核扫描", "record_id": "rec_xyz"},
│     "performance": {"total_duration_ms": 45000, "tool_call_count": 6},
│     "tool_calls": [
│       {"name": "read", "status": "completed", "duration_ms": 500},
│       {"name": "query_collection", "status": "completed", "duration_ms": 2000},
│       {"name": "run_python", "status": "error", "duration_ms": 30000},
│       {"name": "run_python", "status": "error", "duration_ms": 30000},
│       {"name": "run_python", "status": "error", "duration_ms": 30000}
│     ]
│   }
│
├─ Step 3: Agent 分析 tool_calls 序列
│   发现: run_python 连续 3 次 error → 工具循环模式
│   发现: 每次 run_python 耗时 30s → 超时
│
├─ Step 4: 调用 query_sessions 查找同类成功案例
│   input:  {"source_type": "scan", "status": "completed", "limit": 3}
│   output: {
│     "sessions": [
│       {"id": "sess_def456", "status": "completed", "tool_call_count": 4},
│       {"id": "sess_ghi789", "status": "completed", "tool_call_count": 3}
│     ]
│   }
│
├─ Step 5: 调用 analyze_trace 对比成功案例
│   input:  {"session_id": "sess_def456"}
│   output: {
│     "tool_calls": [
│       {"name": "read", "status": "completed"},
│       {"name": "query_collection", "status": "completed"},
│       {"name": "run_python", "status": "completed", "duration_ms": 5000}
│     ]
│   }
│
│   对比发现: 成功案例的 run_python 只需 5s，失败案例用了 30s（超时）
│   推断: 失败案例的 Python 脚本可能有死循环或数据量过大
│
├─ Step 6: Agent 生成诊断结论
│   {
│     "root_cause": "run_python 工具连续 3 次超时，Python 脚本对大数据集做了全表扫描",
│     "failure_chain": [
│       "Agent 读取了数据文件（10MB CSV）",
│       "Agent 用 run_python 执行数据处理脚本",
│       "脚本对每行数据调用 query_collection 做关联查询（N+1 问题）",
│       "30s 超时后 Agent 重试了 3 次，均失败"
│     ],
│     "suggestions": [
│       {
│         "type": "prompt_rewrite",
│         "target": "ai_scan_tasks:task_xxx:prompt_template",
│         "description": "在 prompt 中明确要求'先批量查询所有需要的数据，再处理'，避免 N+1 查询",
│         "effort": "low",
│         "impact": "high"
│       }
│     ],
│     "confidence": 0.90
│   }
│
└─ Step 7: 可选 — 调用 save_artifact 保存详细报告
    input:  {"filename": "trace-analysis-sess_abc123.md", "content": "# 分析报告..."}
```

### 4.2 典型场景：用户在对话中直接调用

```
用户: "帮我分析一下昨天的扫描任务为什么处理失败了"

Agent 内部执行流程:
│
├─ Step 1: 读取 SKILL.md
│
├─ Step 2: 调用 query_sessions 查找最近的失败扫描会话
│   input:  {"source_type": "scan", "status": "failed", "created_after": "2026-09-01", "limit": 5}
│   output: {"sessions": [{"id": "sess_aaa", ...}, {"id": "sess_bbb", ...}]}
│
├─ Step 3: 对每个失败会话调用 analyze_trace
│   逐个分析，汇总发现
│
├─ Step 4: 生成综合诊断报告
│
└─ 输出: "昨天共有 3 个扫描会话失败，主要原因是..."
```

## 5. 注册与部署

### 5.1 MCP 工具注册

在 `mcp-server/tools/__init__.py` 中追加：

```python
from tools import analyze_trace, query_sessions

_TOOLS.update({
    analyze_trace.NAME: (analyze_trace.TOOL, analyze_trace.handle),
    query_sessions.NAME: (query_sessions.TOOL, query_sessions.handle),
})
```

### 5.2 Skill 注册

将 `trace-analyzer` 作为 Global Skill 上传：

1. 创建 `global-skills/trace-analyzer/SKILL.md`（内容见 §2）
2. 通过管理员界面上传，或直接写入磁盘 + 插入 `global_skills` 表
3. `global_skills.py::inject_global_skills()` 会在每个新 session 创建时自动注入

### 5.3 RBAC 配置

在 `mcp-server/rbac.py` 中为新工具配置权限：

```python
# analyze_trace 和 query_sessions 对所有非只读角色可用
"analyze_trace": {"admin", "developer"},
"query_sessions": {"admin", "developer"},
```

## 6. 成本预估

| 场景 | 工具调用次数 | Token 消耗 | 预计耗时 |
|------|------------|-----------|---------|
| 简单分析（单会话，无子代理） | 1-2 次 | 3000-5000 | 10-30s |
| 对比分析（失败 + 2 个成功案例） | 3-5 次 | 6000-10000 | 30-60s |
| 批量分析（5 个失败会话） | 6-10 次 | 10000-20000 | 60-120s |

与直接 LLM API（Layer 2，~800 token/次）相比，OpenCode Skill（Layer 3）的成本约高 5-10 倍，但分析深度和准确性显著更高。
