"""资源预算（ai-harness-p1 spec §9）。

从"观测字段"升级为"判定依据"：批次/用户/API Key 维度的 token、cost、
wall-clock 预算，超限按 on_exceed 动作（warn | drain | abort）。

数据来源：既有 per-message meta（get_session_usage 聚合）+ 子任务行耗时。
判定点：
- claim 前：批次级 max_concurrency 检查（超限不 claim）；
- 子任务终态：累计 usage 落 ai_execution_usage；超 hard 阈值 → 事件 + 动作。

预算行缺省（未配置）= 不限制——预算是显式启用的能力，不是默认约束。
"""
import logging
import time

logger = logging.getLogger(__name__)


def get_budget(scope_type: str, scope_id: str) -> dict | None:
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, max_wall_clock_ms, max_tokens, max_cost, "
                "max_concurrency, on_exceed, enabled FROM ai_execution_budgets "
                "WHERE scope_type = %s AND scope_id = %s AND enabled",
                (scope_type, scope_id),
            )
            row = cur.fetchone()
    if not row:
        return None
    return {'id': row[0], 'maxWallClockMs': row[1], 'maxTokens': row[2],
            'maxCost': row[3], 'maxConcurrency': row[4],
            'onExceed': row[5] or 'drain', 'enabled': row[6]}


def upsert_budget(scope_type: str, scope_id: str, *, max_wall_clock_ms=None,
                  max_tokens=None, max_cost=None, max_concurrency=None,
                  on_exceed='drain', enabled=True) -> str | None:
    import secrets
    bid = 'bud_' + secrets.token_hex(6)
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ai_execution_budgets "
                "  (id, scope_type, scope_id, max_wall_clock_ms, max_tokens, "
                "   max_cost, max_concurrency, on_exceed, enabled) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (scope_type, scope_id) DO UPDATE SET "
                "  max_wall_clock_ms = EXCLUDED.max_wall_clock_ms, "
                "  max_tokens = EXCLUDED.max_tokens, "
                "  max_cost = EXCLUDED.max_cost, "
                "  max_concurrency = EXCLUDED.max_concurrency, "
                "  on_exceed = EXCLUDED.on_exceed, "
                "  enabled = EXCLUDED.enabled "
                "RETURNING id",
                (bid, scope_type, scope_id, max_wall_clock_ms, max_tokens,
                 max_cost, max_concurrency, on_exceed, enabled),
            )
            out = cur.fetchone()[0]
        conn.commit()
    return out


def accumulate_usage(session_id: str, *, batch_id: str | None,
                     usage: dict | None, wall_clock_ms: int = 0,
                     subagents: int = 0) -> None:
    """子任务终态时累计 usage（upsert）。best-effort。"""
    if not usage:
        return
    try:
        from db import get_db
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO ai_execution_usage "
                    "  (session_id, batch_id, tokens_input, tokens_output, "
                    "   cost, wall_clock_ms, subagents) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (session_id) DO UPDATE SET "
                    "  tokens_input = EXCLUDED.tokens_input, "
                    "  tokens_output = EXCLUDED.tokens_output, "
                    "  cost = EXCLUDED.cost, "
                    "  wall_clock_ms = EXCLUDED.wall_clock_ms, "
                    "  subagents = EXCLUDED.subagents, "
                    "  updated_at = NOW()",
                    (session_id, batch_id,
                     int(usage.get('tokensInput') or 0),
                     int(usage.get('tokensOutput') or 0),
                     usage.get('cost') or 0,
                     int(usage.get('durationMs') or wall_clock_ms or 0),
                     subagents),
                )
            conn.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning('usage accumulate failed sid=%s: %s', session_id, e)


def batch_usage_total(batch_id: str) -> dict:
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(SUM(tokens_input), 0), "
                "COALESCE(SUM(tokens_output), 0), COALESCE(SUM(cost), 0), "
                "COALESCE(SUM(wall_clock_ms), 0) FROM ai_execution_usage "
                "WHERE batch_id = %s", (batch_id,))
            row = cur.fetchone()
            # P1 §9.1 扩展维度（缺口补齐 5）：工具调用 / 子代理计数
            cur.execute(
                "SELECT COUNT(*) FROM agent_tool_calls t WHERE "
                "  t.oc_session_id IN (SELECT opencode_session_id "
                "    FROM ai_chat_sessions WHERE batch_id = %s "
                "    AND opencode_session_id IS NOT NULL) OR "
                "  t.subtask_id IN (SELECT id FROM ai_chat_subtasks WHERE "
                "    root_session_id IN (SELECT id FROM ai_chat_sessions "
                "    WHERE batch_id = %s))",
                (batch_id, batch_id))
            tool_calls = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM ai_chat_subtasks WHERE root_session_id "
                "IN (SELECT id FROM ai_chat_sessions WHERE batch_id = %s)",
                (batch_id,))
            subagents = cur.fetchone()[0]
    return {'tokensInput': int(row[0]), 'tokensOutput': int(row[1]),
            'cost': float(row[2]), 'wallClockMs': int(row[3]),
            'toolCalls': int(tool_calls), 'subagents': int(subagents)}


def evaluate_batch_budget(batch_id: str) -> dict:
    """子任务终态后调用：预算判定。返回
    {'exceeded': bool, 'dimension': str|None, 'action': 'warn'|'drain'|'abort',
     'budget': bool(是否存在)}。超限写 budget.exceeded 事件。"""
    from utils import batch_events
    budget = get_budget('batch', batch_id)
    if not budget:
        return {'exceeded': False, 'dimension': None, 'action': None,
                'budget': False}
    total = batch_usage_total(batch_id)
    for dim, used, limit in (
            ('tokens', total['tokensInput'] + total['tokensOutput'],
             budget['maxTokens']),
            ('cost', total['cost'], budget['maxCost']),
            ('wall_clock_ms', total['wallClockMs'], budget['maxWallClockMs']),
            ('tool_calls', total.get('toolCalls', 0), budget.get('maxToolCalls')),
            ('subagents', total.get('subagents', 0), budget.get('maxSubagents'))):
        if limit is not None and used is not None and used >= float(limit):
            action = budget['onExceed']
            batch_events.append_event(
                batch_id, 'budget.exceeded',
                payload={'dimension': dim, 'used': used, 'limit': float(limit),
                         'action': action})
            return {'exceeded': True, 'dimension': dim, 'action': action,
                    'budget': True}
    return {'exceeded': False, 'dimension': None, 'action': None,
            'budget': True}


def apply_budget_action(batch_id: str, action: str, worker) -> None:
    """超限动作：warn 仅事件；drain 停止 claim 新子任务（批次内不再发新
    pending——运行中的自然收敛）；abort 运行中的置 cancel_requested。"""
    if action == 'abort':
        from db import get_db
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_chat_sessions SET cancel_requested = true "
                    "WHERE batch_id = %s AND status IN ('pending', 'running')",
                    (batch_id,))
            conn.commit()
        if worker is not None:
            worker.notify()
    # warn / drain：仅事件与日志（drain 的"不再 claim"由 evaluate 返回值驱动，
    # dispatcher 在批次超限后跳过该批次的 pending 行）
    logger.warning('batch budget exceeded batch=%s action=%s', batch_id, action)


def batch_budget_blocks_claim(batch_id: str) -> bool:
    """dispatcher 认领前检查：批次预算已超限（drain/abort 语义）则不再认领。"""
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM ai_execution_budgets b "
                "WHERE b.scope_type = 'batch' AND b.scope_id = %s AND b.enabled "
                "  AND ("
                "    (SELECT COALESCE(SUM(tokens_input + tokens_output), 0) "
                "       FROM ai_execution_usage WHERE batch_id = %s) >= b.max_tokens"
                "  OR (SELECT COALESCE(SUM(cost), 0) "
                "       FROM ai_execution_usage WHERE batch_id = %s) >= b.max_cost)",
                (batch_id, batch_id, batch_id),
            )
            return cur.fetchone() is not None


def scope_usage_total(scope_type: str, scope_id: str) -> dict:
    """user / api_key scope 的 usage 汇总（缺口补齐 5：scope 扩展）。"""
    col = {'user': 'user_id', 'api_key': 'api_key_id'}.get(scope_type)
    if not col:
        return {'tokensInput': 0, 'tokensOutput': 0, 'cost': 0.0,
                'wallClockMs': 0}
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(SUM(tokens_input), 0), "
                "COALESCE(SUM(tokens_output), 0), COALESCE(SUM(cost), 0), "
                "COALESCE(SUM(wall_clock_ms), 0) FROM ai_execution_usage "
                "WHERE session_id IN (SELECT id FROM ai_chat_sessions "
                f"WHERE {col} = %s)", (scope_id,))
            row = cur.fetchone()
    return {'tokensInput': int(row[0]), 'tokensOutput': int(row[1]),
            'cost': float(row[2]), 'wallClockMs': int(row[3])}


def evaluate_scope_budget(scope_type: str, scope_id: str) -> dict:
    """user/api_key 维度预算判定（同 batch 口径；超限动作恒为 drain——
    scope 级不 abort 他批正在跑的子任务，只停止接收新认领）。
    缺口补齐 5：scope 扩展。"""
    budget = get_budget(scope_type, scope_id)
    if not budget:
        return {'exceeded': False, 'dimension': None, 'action': None,
                'budget': False}
    total = scope_usage_total(scope_type, scope_id)
    for dim, used, limit in (
            ('tokens', total['tokensInput'] + total['tokensOutput'],
             budget['maxTokens']),
            ('cost', total['cost'], budget['maxCost']),
            ('wall_clock_ms', total['wallClockMs'], budget['maxWallClockMs'])):
        if limit is not None and used is not None and used >= float(limit):
            return {'exceeded': True, 'dimension': dim, 'action': 'drain',
                    'budget': True}
    return {'exceeded': False, 'dimension': None, 'action': None,
            'budget': True}


def concurrency_blocked_batches() -> dict:
    """claim 前的 max_concurrency 检查（缺口补齐 5）：返回已达到
    max_concurrency 上限的 batch_id → running 计数（这些批次的 pending
    子任务本轮不认领）。"""
    from db import get_db
    blocked = {}
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT scope_id, max_concurrency FROM ai_execution_budgets "
                "WHERE scope_type = 'batch' AND enabled "
                "  AND max_concurrency IS NOT NULL")
            for bid, cap in cur.fetchall():
                cur.execute(
                    "SELECT count(*) FROM ai_chat_sessions "
                    "WHERE batch_id = %s AND status = 'running'", (bid,))
                if cur.fetchone()[0] >= int(cap):
                    blocked[bid] = True
    return blocked
