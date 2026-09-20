"""Agent 动作账本与到位门禁。

设计:docs/design/AI子任务动作账本与到位门禁设计.md

三个职责:
1. extract_tool_parts / record_messages —— OpenCode 消息里的 tool part 落账到
   agent_tool_calls。幂等键 (oc_session_id, part_id);upsert 带条件更新防写放大
   (part 状态未变不产生行写入)。落账 best-effort:失败返回 False 不抛,
   调用方据此把门禁判为 inconclusive,不算未到位也不静默放行。
2. validate_checks / register_session_expectations —— 把批定义/模板的
   action_checks 登记为期望行(派发前调用,先于执行不存在漏登窗口);
   正则可编译性在此校验,登记后终态核对前执行侧无增删路径。
3. check_session_gate —— 终态核对:对某子任务会话的全部期望逐一在账本计数,
   tree 作用域覆盖根会话 + 该根下全部子代理(skill 步骤常由子代理执行)。
"""
import json
import logging
import re

import psycopg2.extras

from db import get_db as _default_get_db

MAX_ARGS_LEN = 8192

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. 落账
# ---------------------------------------------------------------------------

def args_to_text(inp) -> str:
    """把工具入参压成可检索文本:字符串直接用;对象按 key=value 拼接;
    其余类型 JSON 序列化。只服务于正则匹配,不追求还原原始结构。"""
    if inp is None:
        return ''
    if isinstance(inp, str):
        return inp
    if isinstance(inp, dict):
        parts = []
        for k, v in inp.items():
            if isinstance(v, str):
                parts.append(f'{k}={v}')
            else:
                try:
                    parts.append(f'{k}={json.dumps(v, ensure_ascii=False)}')
                except (TypeError, ValueError):
                    parts.append(f'{k}={v}')
        return '\n'.join(parts)
    try:
        return json.dumps(inp, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(inp)


def extract_tool_parts(messages) -> list:
    """从一批 OpenCode 消息抽出 tool part 记录,按 part_id 去重(同 part 取
    最后一次出现的状态——轮询/快照重复投递时,后到的状态更新)。

    返回 [(part_id, tool, args_text, state)]。"""
    out: dict = {}
    for m in messages or []:
        for p in (m.get('parts') or []):
            if not isinstance(p, dict) or p.get('type') != 'tool':
                continue
            pid = p.get('id')
            tool = p.get('tool')
            if not pid or not tool:
                continue
            state_obj = p.get('state') or {}
            state = (state_obj.get('status') or 'pending')
            args_text = args_to_text(state_obj.get('input'))[:MAX_ARGS_LEN]
            out[pid] = (str(pid), str(tool), args_text, str(state)[:20])
    return list(out.values())


def record_messages(oc_session_id: str, messages, *,
                    root_session_id=None, subtask_id=None,
                    get_db=None) -> bool:
    """把一批消息里的 tool part 落账。best-effort:失败记日志返回 False。

    `get_db` 参数:调用方(batch_engine)传入自己的模块级 get_db,使其在单测里
    跟随调用方被打桩,保持用例密闭;缺省用本模块绑定的真实连接。"""
    db_ctx = get_db or _default_get_db
    part_rows = extract_tool_parts(messages)
    if not part_rows:
        return True
    try:
        with db_ctx() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO agent_tool_calls
                        (oc_session_id, root_session_id, subtask_id,
                         part_id, tool, args_text, state)
                    VALUES %s
                    ON CONFLICT (oc_session_id, part_id) DO UPDATE
                    SET state = EXCLUDED.state,
                        args_text = EXCLUDED.args_text
                    WHERE agent_tool_calls.state IS DISTINCT FROM EXCLUDED.state
                       OR agent_tool_calls.args_text IS DISTINCT FROM EXCLUDED.args_text
                    """,
                    [(oc_session_id, root_session_id, subtask_id,
                      pid, tool, args, state)
                     for (pid, tool, args, state) in part_rows],
                )
        return True
    except Exception as e:  # noqa: BLE001 —— 账本绝不打断任务流程
        log.warning('agent ledger record failed oc=%s: %s', oc_session_id, e)
        return False


# ---------------------------------------------------------------------------
# 2. 期望登记
# ---------------------------------------------------------------------------

VALID_SCOPES = ('session', 'tree')


def validate_checks(checks) -> list:
    """规范化并校验 action_checks 数组;非法抛 ValueError(创建接口回 400)。"""
    if checks in (None, []):
        return []
    if not isinstance(checks, list):
        raise ValueError('action_checks 必须是数组')
    normalized = []
    for i, c in enumerate(checks):
        if not isinstance(c, dict):
            raise ValueError(f'action_checks[{i}] 必须是对象')
        name = (c.get('name') or '').strip()
        tool = (c.get('tool') or '').strip()
        pattern = (c.get('args_pattern') or '').strip()
        if not name or len(name) > 100:
            raise ValueError(f'action_checks[{i}].name 必填且不超过 100 字')
        if not tool or len(tool) > 50:
            raise ValueError(f'action_checks[{i}].tool 必填且不超过 50 字')
        if not pattern:
            raise ValueError(f'action_checks[{i}].args_pattern 必填')
        try:
            re.compile(pattern)
        except re.error as e:
            raise ValueError(f'action_checks[{i}].args_pattern 不是合法正则: {e}')
        scope = (c.get('scope') or 'tree').strip()
        if scope not in VALID_SCOPES:
            raise ValueError(f'action_checks[{i}].scope 只支持 {VALID_SCOPES}')
        try:
            min_count = int(c.get('min_count', 1) or 1)
        except (TypeError, ValueError):
            raise ValueError(f'action_checks[{i}].min_count 必须是整数')
        if min_count < 1:
            raise ValueError(f'action_checks[{i}].min_count 至少为 1')
        require_state = (c.get('require_state') or 'completed').strip()
        normalized.append({
            'name': name, 'tool': tool, 'args_pattern': pattern,
            'require_state': require_state, 'min_count': min_count,
            'scope': scope,
        })
    names = [c['name'] for c in normalized]
    if len(names) != len(set(names)):
        raise ValueError('action_checks 内 name 重复')
    return normalized


def register_session_expectations(session_id: str, checks, source: str = 'batch',
                                  get_db=None) -> int:
    """把规范化后的 checks 登记为某子任务会话的期望行(先于派发调用)。
    重复登记(重试/续跑)按 (scope_type, scope_id, name) 幂等覆盖。
    `get_db` 语义同 record_messages——跟随调用方打桩。"""
    db_ctx = get_db or _default_get_db
    normalized = validate_checks(checks)
    if not normalized:
        return 0
    with db_ctx() as conn:
        with conn.cursor() as cur:
            for c in normalized:
                cur.execute(
                    """
                    INSERT INTO action_expectations
                        (scope_type, scope_id, name, tool, args_pattern,
                         require_state, min_count, source, last_status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                    ON CONFLICT (scope_type, scope_id, name) DO UPDATE SET
                        tool = EXCLUDED.tool,
                        args_pattern = EXCLUDED.args_pattern,
                        require_state = EXCLUDED.require_state,
                        min_count = EXCLUDED.min_count,
                        source = EXCLUDED.source,
                        last_status = 'pending',
                        last_checked_at = NULL,
                        last_evidence = NULL
                    """,
                    (c['scope'], session_id, c['name'], c['tool'],
                     c['args_pattern'], c['require_state'], c['min_count'],
                     source),
                )
    return len(normalized)


# ---------------------------------------------------------------------------
# 3. 终态核对
# ---------------------------------------------------------------------------

def _subtree_oc_ids(cur, session_id: str, root_oc_id) -> list:
    """tree 作用域的账本会话集合:根会话 oc id + 该根下全部子代理会话 id
    (ai_chat_subtasks.id 即子代理 oc 会话 id)。"""
    cur.execute(
        "SELECT id FROM ai_chat_subtasks WHERE root_session_id = %s",
        (session_id,),
    )
    ids = [r[0] for r in cur.fetchall()]
    if root_oc_id:
        ids.insert(0, root_oc_id)
    # 去重保序
    seen, out = set(), []
    for i in ids:
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def check_session_gate(session_id: str, ledger_healthy: bool = True,
                       get_db=None) -> dict:
    """终态核对某会话的全部期望。

    返回 {'status': 'passed'|'failed'|'inconclusive',
          'results': [{name, tool, args_pattern, require_state, min_count,
                       scope, evidence, status}], 'error'?: str}

    - 无期望 → passed(门禁只约束登记过的事项)
    - 会话映射不到 OpenCode 会话 / 账本不健康 / 查询异常 → inconclusive
    `get_db` 语义同 record_messages——跟随调用方打桩。
    """
    db_ctx = get_db or _default_get_db
    try:
        with db_ctx() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT opencode_session_id FROM ai_chat_sessions "
                    "WHERE id = %s",
                    (session_id,),
                )
                row = cur.fetchone()
                oc_sid = row[0] if row else None
                if not ledger_healthy or not oc_sid:
                    return {'status': 'inconclusive', 'results': [],
                            'error': None if ledger_healthy else 'ledger unhealthy'}
                cur.execute(
                    """
                    SELECT id, name, tool, args_pattern, require_state,
                           min_count, scope_type
                    FROM action_expectations
                    WHERE scope_id = %s AND scope_type IN ('session','tree')
                    ORDER BY id
                    """,
                    (session_id,),
                )
                exps = cur.fetchall()
                if not exps:
                    return {'status': 'passed', 'results': []}
                tree_ids = None
                results = []
                for (eid, name, tool, pattern, req_state, min_count,
                     scope) in exps:
                    if scope == 'tree':
                        if tree_ids is None:
                            tree_ids = _subtree_oc_ids(cur, session_id, oc_sid)
                        ids = tree_ids
                    else:
                        ids = [oc_sid]
                    evidence = 0
                    if ids:
                        cur.execute(
                            """
                            SELECT COUNT(*) FROM agent_tool_calls
                            WHERE oc_session_id = ANY(%s)
                              AND tool = %s AND state = %s
                              AND args_text ~ %s
                            """,
                            (ids, tool, req_state, pattern),
                        )
                        evidence = cur.fetchone()[0]
                    status = 'passed' if evidence >= min_count else 'failed'
                    cur.execute(
                        """
                        UPDATE action_expectations
                        SET last_status = %s, last_checked_at = now(),
                            last_evidence = %s
                        WHERE id = %s
                        """,
                        (status, evidence, eid),
                    )
                    results.append({
                        'name': name, 'tool': tool, 'args_pattern': pattern,
                        'require_state': req_state, 'min_count': min_count,
                        'scope': scope, 'evidence': evidence, 'status': status,
                    })
        overall = 'failed' if any(r['status'] == 'failed' for r in results) \
            else 'passed'
        return {'status': overall, 'results': results}
    except Exception as e:  # noqa: BLE001 —— 核对自身异常按 inconclusive 处理
        log.warning('action gate check failed sid=%s: %s', session_id, e)
        return {'status': 'inconclusive', 'results': [], 'error': str(e)[:300]}


def gate_failure_message(gate_result: dict) -> str:
    """把 failed 的核对结果压成子任务 error_message(action_gate: 前缀 +
    逐条缺失项),沿用对账器"带准确原因失败"的风格。"""
    missed = [r for r in gate_result.get('results', [])
              if r.get('status') == 'failed']
    parts = [f"{r['name']}(期望 {r['tool']} ~ {r['args_pattern']},"
             f"账本命中 {r['evidence']}/{r['min_count']})"
             for r in missed]
    return 'action_gate: ' + '; '.join(parts)[:400]


def count_tree_tool_calls(session_id: str, tool: str, args_pattern: str,
                          require_state: str = 'completed',
                          get_db=None) -> dict:
    """试跑核对(编写辅助):按 tree 作用域跑一次匹配,返回命中数与样例,
    供创建对话框保存前验证正则。会话映射不到 OpenCode 时 evidence=0。"""
    db_ctx = get_db or _default_get_db
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT opencode_session_id FROM ai_chat_sessions "
                "WHERE id = %s",
                (session_id,),
            )
            row = cur.fetchone()
            oc_sid = row[0] if row else None
            ids = _subtree_oc_ids(cur, session_id, oc_sid) if oc_sid else []
            if not ids:
                return {'evidence': 0, 'samples': []}
            cur.execute(
                """
                SELECT COUNT(*) FROM agent_tool_calls
                WHERE oc_session_id = ANY(%s) AND tool = %s
                  AND state = %s AND args_text ~ %s
                """,
                (ids, tool, require_state, args_pattern),
            )
            evidence = cur.fetchone()[0]
            cur.execute(
                """
                SELECT args_text, state FROM agent_tool_calls
                WHERE oc_session_id = ANY(%s) AND tool = %s
                  AND state = %s AND args_text ~ %s
                LIMIT 5
                """,
                (ids, tool, require_state, args_pattern),
            )
            samples = [{'args': (r[0] or '')[:300], 'state': r[1]}
                       for r in cur.fetchall()]
            return {'evidence': evidence, 'samples': samples}
