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
import glob as _glob
import json
import logging
import os
import re
import time

import psycopg2.extras

from db import get_db as _default_get_db

MAX_ARGS_LEN = 8192
MAX_FILE_EVIDENCE = 1000
# 终态门禁前置的子代理收敛等待(生产观察:模型可能先于子代理结束回合,
# 子代理脱离父回合继续执行——此刻核对会得到"中途快照"的通过/失败)。
SUBTASK_DRAIN_TIMEOUT_SEC = 120
SUBTASK_DRAIN_POLL_SEC = 2.0
INTERACTIVE_SUBTASK_DRAIN_TIMEOUT_SEC = 30

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


def extract_from_parts(parts) -> list:
    """从 part 字典集合抽取 tool part 记录(REST 消息 parts 与交互态
    parts_by_id 的值同构)。返回 [(part_id, tool, args_text, state)]。"""
    out: dict = {}
    for p in parts or []:
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


def extract_tool_parts(messages) -> list:
    """从一批 OpenCode 消息抽取 tool part 记录,按 part_id 去重(同 part 取
    最后一次出现的状态——轮询/快照重复投递时,后到的状态更新)。"""
    parts = []
    for m in messages or []:
        parts.extend(m.get('parts') or [])
    return extract_from_parts(parts)


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


def record_state(session_id: str, oc_session_id: str, state, *,
                 get_db=None) -> bool:
    """交互侧(M2):把 chat_persist 累积态(root 的 parts_by_id + state['subtasks']
    里每个子代理自己的累积态)落账。幂等与批任务路径共用同一张账本。"""
    db_ctx = get_db or _default_get_db
    ok = True
    all_ok = True
    scopes = [(oc_session_id, None)]
    for child_sid, child_scope in (state.get('subtasks') or {}).items():
        scopes.append((child_sid, child_scope))
    for child_sid, scope in scopes:
        if scope is None:
            part_map = state.get('parts_by_id')
        else:
            part_map = scope.get('parts_by_id')
        part_rows = extract_from_parts((part_map or {}).values())
        if not part_rows:
            continue
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
                        [(child_sid, session_id,
                          None if scope is None else child_sid,
                          pid, tool, args, st)
                         for (pid, tool, args, st) in part_rows],
                    )
        except Exception as e:  # noqa: BLE001
            log.warning('agent ledger record_state failed oc=%s: %s', child_sid, e)
            ok = False
            all_ok = False
    return ok and all_ok


def finalize_interactive_turn(session_id: str, oc_session_id: str, state,
                              get_db=None) -> dict:
    """交互回合收敛(idle/error)时的统一收口(设计 M2):先落账,等子代理收敛,
    再核对该会话的期望。不阻断回合——结果只写期望行与日志,由调用方告警/展示。"""
    record_ok = record_state(session_id, oc_session_id, state, get_db=get_db)
    drained = wait_subtasks_drained(
        session_id,
        timeout_sec=INTERACTIVE_SUBTASK_DRAIN_TIMEOUT_SEC,
        poll_sec=SUBTASK_DRAIN_POLL_SEC, get_db=get_db)
    gate = check_session_gate(session_id, ledger_healthy=record_ok and drained,
                              get_db=get_db)
    if gate['status'] == 'failed':
        log.warning('interactive action gate failed session=%s: %s',
                    session_id, gate_failure_message(gate))
    elif gate['status'] == 'inconclusive':
        log.warning('interactive action gate inconclusive session=%s: %s',
                    session_id, gate.get('error'))
    return gate


# ---------------------------------------------------------------------------
# 2. 期望登记
# ---------------------------------------------------------------------------

VALID_SCOPES = ('session', 'tree')
VALID_CHECK_TYPES = ('tool', 'file', 'db_record')


def _validate_effect_spec(check_type, spec, idx):
    """效果断言的参数校验(M3):file=工作区相对 glob;db_record=集合+Mongo 过滤。"""
    if not isinstance(spec, dict):
        raise ValueError(f'action_checks[{idx}].effect_spec 必须是对象')
    if check_type == 'file':
        path = (spec.get('path') or '').strip()
        if not path:
            raise ValueError(f'action_checks[{idx}].effect_spec.path 必填')
        if os.path.isabs(path) or '..' in path.replace('\\', '/').split('/'):
            raise ValueError(f'action_checks[{idx}].effect_spec.path '
                             '必须是会话工作区内的相对路径')
        return {'path': path}
    if check_type == 'db_record':
        collection = (spec.get('collection') or '').strip()
        filt = spec.get('filter')
        if not collection:
            raise ValueError(f'action_checks[{idx}].effect_spec.collection 必填')
        from utils.mongo_query import translate as mongo_translate, MongoQueryError
        try:
            mongo_translate(filt or {})
        except MongoQueryError as e:
            raise ValueError(f'action_checks[{idx}].effect_spec.filter 非法: {e}')
        return {'collection': collection, 'filter': filt or {}}
    raise ValueError(f'action_checks[{idx}].check_type 不支持: {check_type}')


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
        if not name or len(name) > 100:
            raise ValueError(f'action_checks[{i}].name 必填且不超过 100 字')
        check_type = (c.get('check_type') or 'tool').strip()
        if check_type not in VALID_CHECK_TYPES:
            raise ValueError(f'action_checks[{i}].check_type 只支持 '
                             f'{VALID_CHECK_TYPES}')
        tool = (c.get('tool') or '').strip()
        pattern = (c.get('args_pattern') or '').strip()
        if check_type == 'tool':
            if not tool or len(tool) > 50:
                raise ValueError(f'action_checks[{i}].tool 必填且不超过 50 字')
            if not pattern:
                raise ValueError(f'action_checks[{i}].args_pattern 必填')
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f'action_checks[{i}].args_pattern 不是合法正则: {e}')
        else:
            tool = tool or check_type
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
        effect_spec = None
        if check_type in ('file', 'db_record'):
            effect_spec = _validate_effect_spec(check_type, c.get('effect_spec'), i)
        normalized.append({
            'name': name, 'tool': tool, 'args_pattern': pattern,
            'require_state': require_state, 'min_count': min_count,
            'scope': scope, 'check_type': check_type,
            'effect_spec': effect_spec,
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
                         require_state, min_count, source, last_status,
                         check_type, effect_spec)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s)
                    ON CONFLICT (scope_type, scope_id, name) DO UPDATE SET
                        tool = EXCLUDED.tool,
                        args_pattern = EXCLUDED.args_pattern,
                        require_state = EXCLUDED.require_state,
                        min_count = EXCLUDED.min_count,
                        source = EXCLUDED.source,
                        check_type = EXCLUDED.check_type,
                        effect_spec = EXCLUDED.effect_spec,
                        last_status = 'pending',
                        last_checked_at = NULL,
                        last_evidence = NULL
                    """,
                    (c['scope'], session_id, c['name'], c['tool'],
                     c['args_pattern'], c['require_state'], c['min_count'],
                     source, c['check_type'],
                     psycopg2.extras.Json(c['effect_spec'])
                     if c['effect_spec'] else None),
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


def _count_file_evidence(cur, session_id: str, spec) -> int:
    """file 原语:会话工作区内相对 glob 的命中文件数(封顶防误配大目录)。"""
    cur.execute("SELECT workspace_path FROM ai_chat_sessions WHERE id = %s",
                (session_id,))
    row = cur.fetchone()
    ws = row[0] if row else None
    if not ws or not os.path.isdir(ws):
        return 0
    pattern = os.path.join(ws, (spec or {}).get('path') or '')
    hits = _glob.glob(pattern, recursive=True) if pattern else []
    return min(len(hits), MAX_FILE_EVIDENCE)


def _count_db_record_evidence(cur, spec) -> int:
    """db_record 原语:dynamic_data 中按集合 + Mongo 过滤的命中记录数。"""
    from utils.mongo_query import translate as mongo_translate
    collection = (spec or {}).get('collection') or ''
    filt = (spec or {}).get('filter') or {}
    where, params = mongo_translate(filt)
    cur.execute(
        'SELECT COUNT(*) FROM dynamic_data WHERE collection = %s AND (' + where + ')',
        [collection] + params,
    )
    return cur.fetchone()[0]


def wait_subtasks_drained(session_id: str, timeout_sec: int = SUBTASK_DRAIN_TIMEOUT_SEC,
                          poll_sec: float = SUBTASK_DRAIN_POLL_SEC,
                          get_db=None) -> bool:
    """终态门禁前置:等待该会话的全部子代理收敛。

    模型可能先于子代理结束自己的回合,子代理脱离父回合继续执行——此刻核对
    得到的是"中途快照"。轮询 ai_chat_subtasks 直到无 running 子代理或超时。
    返回 True=已收敛/本就无子代理;False=超时仍有 running(照常核对,但结果
    可能偏乐观,调用方日志留痕)。"""
    db_ctx = get_db or _default_get_db
    deadline = time.time() + timeout_sec
    while True:
        try:
            with db_ctx() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) FROM ai_chat_subtasks "
                        "WHERE root_session_id = %s AND status = 'running'",
                        (session_id,),
                    )
                    running = cur.fetchone()[0]
        except Exception as e:  # noqa: BLE001 —— 探测失败不阻塞核对
            log.warning('subtask drain probe failed sid=%s: %s', session_id, e)
            return True
        if not isinstance(running, int) or running == 0:
            # 非整数计数只在打桩/异常环境出现——视为已收敛,绝不空转真实 sleep
            return True
        if time.time() >= deadline:
            log.warning('subtask drain timeout sid=%s: %d still running',
                        session_id, running)
            return False
        time.sleep(poll_sec)


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
                           min_count, scope_type, check_type, effect_spec
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
                     scope, check_type, effect_spec) in exps:
                    if check_type == 'file':
                        evidence = _count_file_evidence(cur, session_id,
                                                        effect_spec)
                    elif check_type == 'db_record':
                        evidence = _count_db_record_evidence(cur, effect_spec)
                    else:
                        if scope == 'tree':
                            if tree_ids is None:
                                tree_ids = _subtree_oc_ids(cur, session_id,
                                                           oc_sid)
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
                        'check_type': check_type or 'tool',
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
