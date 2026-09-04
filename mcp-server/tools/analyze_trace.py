"""Tool: analyze_trace — 从平台 DB 构建指定会话的结构化执行轨迹。

只读查询，不修改任何数据。返回会话元数据、工具调用序列、子代理树、
性能指标、文件变更等。供 trace-analyzer Skill 调用。
"""

import json
from datetime import datetime

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
        "参数: session_id(必填), include_subtasks(默认true), include_reasoning(默认false),"
        " include_subtask_details(默认false, 返回子代理内部的工具调用序列)。"
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
                "description": "是否包含子代理轨迹（默认 true）",
            },
            "include_reasoning": {
                "type": "boolean",
                "description": "是否包含 reasoning tokens（默认 false，可能很长）",
            },
            "include_subtask_details": {
                "type": "boolean",
                "description": "是否返回子代理内部的工具调用序列（默认 false，仅返回元数据）",
            },
        },
        "required": ["session_id"],
        "additionalProperties": False,
    },
)


class AnalyzeTraceError(Exception):
    pass


def _json_serial(obj):
    """JSON serializer for datetime objects."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def _trunc(text, maxlen: int):
    if not text:
        return text
    s = str(text)
    return s[:maxlen] + "..." if len(s) > maxlen else s


def _resolve_source(session: dict) -> dict:
    """从会话元数据推断触发来源。"""
    if session.get("scan_task_id"):
        return {
            "type": "scan",
            "id": session["scan_task_id"],
            "name": session.get("scan_task_name") or "",
            "record_id": session.get("source_record_id") or "",
        }
    if session.get("batch_id"):
        return {
            "type": "batch",
            "id": session["batch_id"],
            "name": session.get("batch_name") or "",
        }
    if session.get("api_key_id"):
        return {"type": "open_api", "id": session["api_key_id"]}
    return {"type": "interactive"}


def _extract_tool_calls_from_content(content: list, include_reasoning: bool) -> list:
    """从消息 content JSONB 中提取工具调用列表（复用于主会话和子代理）。"""
    tool_calls = []
    for part in (content or []):
        ptype = part.get("type")
        if ptype == "tool_use":
            tc = {
                "name": part.get("name", ""),
                "status": part.get("status", ""),
                "duration_ms": part.get("durationMs", 0),
            }
            tc["input"] = _trunc(
                json.dumps(part.get("input"), ensure_ascii=False), 500
            ) if part.get("input") else None
            result_raw = part.get("result") or part.get("output")
            if result_raw:
                tc["result"] = _trunc(str(result_raw), 300 if not include_reasoning else 1000)
            tool_calls.append(tc)
        elif ptype == "reasoning" and include_reasoning:
            tool_calls.append({
                "name": "_reasoning",
                "input": None,
                "result": _trunc(part.get("text", ""), 1000),
                "status": "completed",
                "duration_ms": 0,
            })
    return tool_calls


# ── 异常检测与打分 ─────────────────────────────────────────────────────

_LOOP_THRESHOLD = 3        # 连续相同调用次数视为循环
_TIMEOUT_MS = 30000        # 超时阈值（毫秒）
_ANOMALY_PENALTY = {       # 每种异常的扣分
    "tool_loop": 15,
    "invalid_retry": 10,
    "timeout": 10,
    "empty_output": 5,
    "unexpected_termination": 10,
    "subtask_failed": 10,
    "no_final_response": 10,
}


def _detect_anomalies(tool_calls: list, subtasks: list, messages_summary: list) -> list:
    """从工具调用序列中检测异常，返回异常列表。"""
    anomalies = []
    real_calls = [tc for tc in tool_calls if tc["name"] != "_reasoning"]

    # ── 工具循环：连续 N 次相同 name + input ──
    for i in range(len(real_calls) - _LOOP_THRESHOLD + 1):
        window = real_calls[i:i + _LOOP_THRESHOLD]
        if (window[0]["name"] == window[1]["name"] == window[2]["name"]
                and window[0].get("input") == window[1].get("input") == window[2].get("input")):
            anomalies.append({
                "type": "tool_loop",
                "severity": "high",
                "tool": window[0]["name"],
                "count": _LOOP_THRESHOLD,
                "message": f"工具 {window[0]['name']} 连续 {_LOOP_THRESHOLD} 次相同调用，疑似死循环",
            })

    # ── 无效重试：error 后相同 name + input ──
    for i in range(len(real_calls) - 1):
        if (real_calls[i]["status"] == "error"
                and real_calls[i + 1]["name"] == real_calls[i]["name"]
                and real_calls[i + 1].get("input") == real_calls[i].get("input")
                and real_calls[i + 1]["status"] == "error"):
            anomalies.append({
                "type": "invalid_retry",
                "severity": "medium",
                "tool": real_calls[i]["name"],
                "message": f"工具 {real_calls[i]['name']} 错误后用相同参数重试",
            })

    # ── 超时 ──
    for tc in real_calls:
        if tc["duration_ms"] and tc["duration_ms"] > _TIMEOUT_MS:
            anomalies.append({
                "type": "timeout",
                "severity": "medium",
                "tool": tc["name"],
                "duration_ms": tc["duration_ms"],
                "message": f"工具 {tc['name']} 执行耗时 {tc['duration_ms']}ms，超过 {_TIMEOUT_MS}ms 阈值",
            })

    # ── 空输出 ──
    for tc in real_calls:
        if tc["status"] == "completed" and not tc.get("result"):
            anomalies.append({
                "type": "empty_output",
                "severity": "low",
                "tool": tc["name"],
                "message": f"工具 {tc['name']} 状态为 completed 但 result 为空",
            })

    # ── 意外终止：最后一条工具调用后没有 assistant text 回复 ──
    if messages_summary and real_calls:
        last_msg = messages_summary[-1]
        if last_msg["role"] != "assistant" or not last_msg.get("text_preview"):
            # 检查最后一条消息是否只有 tool_use 没有 text
            has_text = any(
                m["role"] == "assistant" and m.get("text_preview")
                for m in reversed(messages_summary)
            )
            if not has_text:
                anomalies.append({
                    "type": "unexpected_termination",
                    "severity": "high",
                    "message": "最后一条工具调用后没有 assistant 文本回复，可能意外终止",
                })

    # ── 子代理失败 ──
    for st in subtasks:
        if st.get("status") and st["status"] not in ("completed", "running"):
            anomalies.append({
                "type": "subtask_failed",
                "severity": "high",
                "subtask_id": st["id"],
                "agent": st.get("agent", ""),
                "error": st.get("error_message", ""),
                "message": f"子代理 {st.get('agent', '')}({st['id']}) 失败: {st.get('error_message', '未知')}",
            })

    return anomalies


def _compute_scores(
    tool_calls: list,
    subtasks: list,
    messages_summary: list,
    session_status: str,
    anomalies: list,
    performance: dict,
) -> dict:
    """基于确定性规则计算六维评分（每维 0-100）。"""
    real_calls = [tc for tc in tool_calls if tc["name"] != "_reasoning"]
    error_count = sum(1 for tc in real_calls if tc["status"] == "error")
    total_calls = len(real_calls)

    # ── 1. 任务完成度（30%）──
    if session_status == "completed":
        task_score = 90
    elif session_status == "failed":
        task_score = 20
    elif session_status == "cancelled":
        task_score = 10
    else:
        task_score = 50  # active / other
    # 有最终文本回复加分
    if messages_summary and messages_summary[-1].get("text_preview"):
        task_score = min(100, task_score + 10)

    # ── 2. 工具效率（20%）──
    tool_score = 100
    for a in anomalies:
        if a["type"] in _ANOMALY_PENALTY:
            tool_score -= _ANOMALY_PENALTY[a["type"]]
    tool_score = max(0, tool_score)

    # ── 3. 错误恢复（5%）──
    if error_count == 0:
        resilience_score = 100  # 无错误，满分
    else:
        # 计算错误后是否换了策略
        retry_same = sum(1 for a in anomalies if a["type"] == "invalid_retry")
        resilience_score = max(0, 100 - (retry_same * 30) - (error_count * 10))

    # ── 4. 资源效率（10%）──
    # 基于工具调用数量的简单评估
    if total_calls <= 5:
        resource_score = 95
    elif total_calls <= 10:
        resource_score = 85
    elif total_calls <= 20:
        resource_score = 70
    elif total_calls <= 30:
        resource_score = 50
    else:
        resource_score = 30
    # 超时扣分
    timeout_count = sum(1 for a in anomalies if a["type"] == "timeout")
    resource_score = max(0, resource_score - timeout_count * 10)

    # ── 5. 指令遵循度 + 推理质量（需要 LLM 判断，留默认值）──
    # 这两个维度需要对比 Skill 定义和 reasoning 内容，服务端无法确定性计算
    # 给出基线值，由 SKILL.md 中的 LLM 分析后调整
    adherence_score = None   # 需要 LLM 判断
    reasoning_score = None   # 需要 LLM 判断

    # ── 加权总分（只计算可确定的维度）──
    weights = {
        "task_completion": 0.30,
        "instruction_adherence": 0.20,
        "tool_efficiency": 0.20,
        "reasoning_quality": 0.15,
        "resource_efficiency": 0.10,
        "error_resilience": 0.05,
    }
    computed = {
        "task_completion": task_score,
        "tool_efficiency": tool_score,
        "resource_efficiency": resource_score,
        "error_resilience": resilience_score,
    }
    # 总分只基于可计算维度（归一化到总权重）
    computed_weight_sum = sum(weights[k] for k in computed)
    total = sum(computed[k] * weights[k] / computed_weight_sum for k in computed)

    return {
        "task_completion": {"score": task_score, "weight": weights["task_completion"],
                            "computed": True},
        "instruction_adherence": {"score": adherence_score, "weight": weights["instruction_adherence"],
                                  "computed": False, "note": "需要 LLM 根据 Skill 定义判断"},
        "tool_efficiency": {"score": tool_score, "weight": weights["tool_efficiency"],
                            "computed": True},
        "reasoning_quality": {"score": reasoning_score, "weight": weights["reasoning_quality"],
                              "computed": False, "note": "需要 LLM 根据 reasoning 内容判断"},
        "resource_efficiency": {"score": resource_score, "weight": weights["resource_efficiency"],
                                "computed": True},
        "error_resilience": {"score": resilience_score, "weight": weights["error_resilience"],
                             "computed": True},
        "total": round(total, 1),
        "computed_dimensions": 4,
        "pending_llm_dimensions": 2,
    }


def handle(input: dict, ctx: ToolContext) -> dict:
    """构建指定会话的结构化执行轨迹。"""
    session_id = (input or {}).get("session_id")
    if not session_id:
        raise AnalyzeTraceError("session_id is required")

    include_subtasks = (input or {}).get("include_subtasks", True)
    include_reasoning = (input or {}).get("include_reasoning", False)
    include_subtask_details = (input or {}).get("include_subtask_details", False)

    with get_db() as conn:
        cur = conn.cursor()

        # ── 1. 会话元数据 ──────────────────────────────────────────────
        cur.execute(
            """
            SELECT s.id, s.user_id, s.title, s.status, s.error_message,
                   s.batch_id, s.scan_task_id, s.source_record_id,
                   s.agent, s.model, s.created_at, s.last_active_at,
                   b.name   AS batch_name,   b.agent AS batch_agent,
                   st.name  AS scan_task_name
            FROM ai_chat_sessions s
            LEFT JOIN ai_chat_batches  b  ON b.id  = s.batch_id
            LEFT JOIN ai_scan_tasks    st ON st.id = s.scan_task_id
            WHERE s.id = %s
            """,
            (session_id,),
        )
        row = cur.fetchone()
        if not row:
            raise AnalyzeTraceError(f"会话 {session_id} 不存在")

        cols = [
            "id", "user_id", "title", "status", "error_message",
            "batch_id", "scan_task_id", "source_record_id",
            "agent", "model", "created_at", "last_active_at",
            "batch_name", "batch_agent", "scan_task_name",
        ]
        session = {k: row[i] for i, k in enumerate(cols)}

        # ── 2. 触发来源 ────────────────────────────────────────────────
        source = _resolve_source(session)

        # ── 3. 消息解析（工具调用 + 性能指标）─────────────────────────
        cur.execute(
            """
            SELECT id, role, content, meta, created_at
            FROM ai_chat_messages
            WHERE session_id = %s
            ORDER BY created_at
            """,
            (session_id,),
        )
        msg_rows = cur.fetchall()

        tool_calls = []
        messages_summary = []
        total_duration = 0
        total_tokens_in = 0
        total_tokens_out = 0
        total_cost = 0.0

        for mr in msg_rows:
            msg = {"id": mr[0], "role": mr[1], "content": mr[2], "meta": mr[3]}
            content = msg["content"] or []
            meta = msg["meta"] or {}

            if msg["role"] == "assistant":
                tool_calls.extend(
                    _extract_tool_calls_from_content(content, include_reasoning)
                )
                total_duration += meta.get("durationMs", 0)
                total_tokens_in += meta.get("tokensInput", 0)
                total_tokens_out += meta.get("tokensOutput", 0)
                total_cost += float(meta.get("cost", 0) or 0)

            # 消息摘要
            text_preview = ""
            tc_names = []
            for part in content:
                if part.get("type") == "text" and part.get("text") and not text_preview:
                    text_preview = part["text"][:120]
                if part.get("type") == "tool_use":
                    tc_names.append(part.get("name", ""))
            messages_summary.append({
                "role": msg["role"],
                "text_preview": text_preview,
                "tool_calls": tc_names or None,
            })

        # ── 4. 子代理轨迹 ──────────────────────────────────────────────
        subtasks = []
        if include_subtasks:
            cur.execute(
                """
                SELECT id, agent, description, status, error_message,
                       prompt, created_at, completed_at
                FROM ai_chat_subtasks
                WHERE root_session_id = %s
                ORDER BY created_at
                """,
                (session_id,),
            )
            st_cols = [
                "id", "agent", "description", "status", "error_message",
                "prompt", "created_at", "completed_at",
            ]
            for st_row in cur.fetchall():
                st = {k: st_row[i] for i, k in enumerate(st_cols)}
                # 子代理消息统计
                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt,
                           COALESCE(SUM((meta->>'durationMs')::int), 0) AS dur,
                           COALESCE(SUM(COALESCE((meta->>'tokensInput')::int, 0)
                                       + COALESCE((meta->>'tokensOutput')::int, 0)), 0) AS tok
                    FROM ai_chat_subtask_messages
                    WHERE subtask_id = %s
                    """,
                    (st["id"],),
                )
                stats = cur.fetchone()
                st["message_count"] = stats[0] or 0
                st["duration_ms"] = stats[1] or 0
                st["tokens_total"] = stats[2] or 0
                st["prompt"] = _trunc(st.get("prompt"), 300)

                # 子代理内部工具调用（可选）
                if include_subtask_details:
                    cur.execute(
                        """
                        SELECT role, content
                        FROM ai_chat_subtask_messages
                        WHERE subtask_id = %s
                        ORDER BY seq
                        """,
                        (st["id"],),
                    )
                    st_tool_calls = []
                    st_summary = []
                    for stm in cur.fetchall():
                        stm_content = stm[1] or []
                        if stm[0] == "assistant":
                            st_tool_calls.extend(
                                _extract_tool_calls_from_content(stm_content, include_reasoning)
                            )
                        # 消息摘要
                        st_text = ""
                        st_tc_names = []
                        for p in stm_content:
                            if p.get("type") == "text" and p.get("text") and not st_text:
                                st_text = p["text"][:100]
                            if p.get("type") == "tool_use":
                                st_tc_names.append(p.get("name", ""))
                        st_summary.append({
                            "role": stm[0],
                            "text_preview": st_text,
                            "tool_calls": st_tc_names or None,
                        })
                    st["tool_calls"] = st_tool_calls
                    st["messages_summary"] = st_summary

                subtasks.append(st)

        # ── 5. 工作区文件变更 ──────────────────────────────────────────
        cur.execute(
            """
            SELECT path, status
            FROM ai_chat_session_files
            WHERE session_id = %s
            ORDER BY path
            """,
            (session_id,),
        )
        files_changed = [{"path": fc[0], "status": fc[1]} for fc in cur.fetchall()]

    # ── 6. 异常检测与打分 ──────────────────────────────────────────────
    anomalies = _detect_anomalies(tool_calls, subtasks, messages_summary)
    performance = {
        "total_duration_ms": total_duration,
        "total_tokens_input": total_tokens_in,
        "total_tokens_output": total_tokens_out,
        "total_cost": round(total_cost, 6),
        "message_count": len(msg_rows),
        "tool_call_count": len(tool_calls),
    }
    scores = _compute_scores(
        tool_calls, subtasks, messages_summary,
        session.get("status", ""), anomalies, performance,
    )

    # ── 7. 组装返回 ────────────────────────────────────────────────────
    # 序列化 datetime
    for k in ("created_at", "last_active_at"):
        if isinstance(session.get(k), datetime):
            session[k] = session[k].isoformat()

    return json.dumps(
        {
            "session": session,
            "source": source,
            "performance": performance,
            "tool_calls": tool_calls,
            "subtasks": subtasks,
            "messages_summary": messages_summary,
            "files_changed": files_changed,
            "anomalies": anomalies,
            "scores": scores,
        },
        ensure_ascii=False,
        default=_json_serial,
    )
