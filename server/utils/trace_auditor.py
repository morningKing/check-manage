"""Deterministic execution auditor (execution-audit Spec §11/§12/§13).

Builds the structured diagnosis for one execution attempt by comparing:
  contract steps (what SHOULD happen, from ai_execution_contracts)
  declared plan  (what the agent SAID it would do, todo snapshots)
  observed evidence (what tools actually ran and returned)

Deterministic rules only — the LLM (trace-analyzer skill) explains the
facts afterwards, it never manufactures them. Every output carries
evidence refs and an evidence level; missing data yields
unknown_due_to_missing_data, not a fabricated verdict.
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_TIMEOUT_MS = 30000

# evidence signal → failure type (checked in order; Spec §12.2)
_FAILURE_SIGNALS = [
    (('context length', 'token limit', 'prompt too long', '上下文超限',
      'maximum context'), 'context_overflow'),
    (('unauthorized', '401', '403', 'permission', 'forbidden', '无权限',
      '权限'), 'permission_denied'),
    (('404', 'not found', 'no such', '不存在', 'not_found'), 'resource_not_found'),
    (('timed out', 'timeout', '超时'), 'timeout'),
    (('connection', 'refused', 'network', 'dns', 'unreachable', 'reset'),
     'network_error'),
    (('provider', 'openai error', 'upstream', 'api error', 'model error'),
     'provider_error'),
    (('invalid', 'validation', 'required', '参数', '校验'), 'invalid_input'),
    (('internal', 'exception', 'traceback', '500'), 'tool_internal_error'),
]


def _norm_input(v) -> str:
    if v is None:
        return ''
    if isinstance(v, str):
        return v
    try:
        return json.dumps(v, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(v)


def tool_calls_from_messages(messages: list[dict]) -> list[dict]:
    """Flatten persisted tool_use parts into an ordered evidence list."""
    calls = []
    for m in messages:
        for idx, part in enumerate(m.get('content') or []):
            if not isinstance(part, dict) or part.get('type') != 'tool_use':
                continue
            calls.append({
                'index': len(calls),
                'name': part.get('name', ''),
                'status': part.get('status', ''),
                'input': part.get('input'),
                'input_text': _norm_input(part.get('input')),
                'result': part.get('result') or part.get('output'),
                'result_text': str(part.get('result') or part.get('output') or '')[:2000],
                'duration_ms': part.get('durationMs') or 0,
                'message_id': m.get('id'),
                'message_seq': m.get('seq'),
                'created_at': m.get('created_at'),
                'part_index': idx,
            })
    return calls


def classify_failure(call: dict) -> dict | None:
    """Failure record for one errored tool call, or None when it succeeded."""
    if call.get('status') != 'error':
        return None
    text = f"{call.get('result_text', '')}\n{call.get('input_text', '')}".lower()
    failure_type = 'unknown'
    for needles, ftype in _FAILURE_SIGNALS:
        if any(n in text for n in needles):
            failure_type = ftype
            break
    duration = call.get('duration_ms') or call.get('durationMs') or 0
    if failure_type in ('unknown', 'tool_internal_error') and duration > _TIMEOUT_MS:
        failure_type = 'timeout'
    ref = (f"message:{call.get('message_id', 'unknown')}"
           f"#tool{call.get('part_index', 0)}")
    return {
        'tool': call.get('tool', call.get('name', '')),
        'status': 'error',
        'failure_type': failure_type,
        'input_preview': call.get('input_text') or _norm_input(call.get('input'))[:300],
        'result_preview': call.get('result_text',
                                   str(call.get('result') or ''))[:500],
        'duration_ms': duration,
        'evidence_refs': [ref],
        'evidence_level': 'confirmed',
    }


def _recovery_for(failure: dict, calls: list[dict], fail_index: int) -> None:
    """Attach what the agent did AFTER the failure (Spec §12.3)."""
    same_input_retry = False
    strategy_changed = False
    recovered = False
    failed_input = failure.get('input_preview', '')
    for nxt in calls[fail_index + 1:]:
        if nxt.get('name') != failure['tool']:
            if nxt.get('status') == 'completed':
                strategy_changed = True
            continue
        nxt_input = nxt.get('input_text') or _norm_input(nxt.get('input'))
        if _norm_input(nxt.get('input')) == failed_input or \
                nxt_input == failed_input:
            same_input_retry = True
            if nxt.get('status') == 'completed':
                recovered = True
        else:
            strategy_changed = True
            if nxt.get('status') == 'completed':
                recovered = True
        break
    failure['recovery'] = {
        'attempted': same_input_retry or strategy_changed,
        'same_input_retry': same_input_retry,
        'strategy_changed': strategy_changed,
        'recovered': recovered,
    }


def _todo_declared(todo_plan: dict) -> dict[str, dict]:
    out = {}
    for s in todo_plan.get('declared_steps', []):
        key = (s.get('content') or '').strip().lower()
        if key:
            out[key] = s
    return out


def _step_evidence(step: dict, calls: list[dict]) -> dict | None:
    """Locate the first tool call that evidences this step. Without
    expected_tools there is no way to tie a generic call to this step —
    evidence stays None (the todo may still claim it)."""
    expected = [t.lower() for t in (step.get('expected_tools') or [])]
    if not expected:
        return None
    for c in calls:
        if c['name'].lower() in expected:
            return c
    return None


def build_step_results(contract: dict | None, todo_plan: dict,
                       calls: list[dict]) -> tuple[list[dict], str]:
    """Returns (step_rows, contract_status). contract None → unknown."""
    if not contract:
        return [], 'no_contract'
    steps = contract.get('steps') or []
    declared_map = _todo_declared(todo_plan)
    # first evidence index per step id (for dependency checks)
    dep_index: dict[str, int] = {}
    rows: list[dict] = []

    # pass 1: evidence per step (regardless of order — out_of_order is judged
    # in pass 2 by comparing the evidence position against its dependencies')
    evidence: dict[str, dict | None] = {}
    for step in steps:
        call = _step_evidence(step, calls)
        evidence[step['id']] = call
        if call is not None:
            dep_index[step['id']] = call['index']

    for step in steps:
        sid = step['id']
        call = evidence.get(sid)
        declared = next((v for k, v in declared_map.items()
                         if k == sid.lower() or sid.lower() in k), None)
        required = bool(step.get('required', True))
        row = {
            'step_id': sid,
            'expected': required,
            'declared_by_agent': declared is not None,
            'observed': call is not None,
            'evidence_refs': [],
            'evidence_level': 'unknown',
            'confidence': None,
            'duration_ms': None,
        }
        if call is not None:
            ref = f"message:{call['message_id']}#tool{call['part_index']}"
            row['evidence_refs'] = [ref]
            row['evidence_level'] = 'confirmed'
            row['duration_ms'] = call.get('duration_ms') or 0
            deps = step.get('depends_on') or []
            missing_dep_index = [dep_index[d] for d in deps
                                 if d in dep_index and dep_index[d] > call['index']]
            if deps and missing_dep_index:
                row['status'] = 'out_of_order'
                row['reason'] = f"前置步骤 {deps} 在本步骤之后才执行"
            elif call['status'] == 'error':
                row['status'] = 'failed'
                row['reason'] = f"步骤工具 {call['name']} 执行失败"
            else:
                row['status'] = 'completed_confirmed'
        else:
            row['evidence_level'] = 'unknown'
            if declared and declared.get('status') == 'completed':
                row['status'] = 'completed_claimed'
                row['reason'] = 'Todo 声明完成，但没有工具证据'
                row['confidence'] = 0.4
            elif declared:
                row['status'] = 'missing'
                row['reason'] = 'Todo 声明未完成，且没有工具证据'
            elif required:
                row['status'] = 'missing'
                row['reason'] = '契约必需步骤，无声明也无工具证据'
            else:
                row['status'] = 'unknown_due_to_missing_data'
                row['reason'] = '可选步骤且无证据'
        rows.append(row)

    if any(r['status'] == 'unknown_due_to_missing_data' for r in rows):
        return rows, 'partial'
    return rows, 'complete'


def _data_completeness(attempt: dict | None, manifests: list[dict],
                       events_count: int, todo_plan: dict,
                       contract: dict | None) -> float:
    checks = [
        bool(attempt),
        bool(manifests),
        events_count > 0,
        bool(attempt and attempt.get('effective_model')),
        bool(attempt and attempt.get('effective_prompt_hash')),
        todo_plan.get('snapshot_count', 0) > 0,
        contract is not None,
    ]
    return round(sum(1 for c in checks if c) / len(checks), 2)


def build_diagnosis_report(attempt: dict, manifests: list[dict],
                           messages: list[dict], events_count: int) -> dict:
    """Assemble the structured report for one attempt (Spec §13)."""
    from utils.execution_contract import contract_for_skill
    from utils.todo_trace import build_declared_plan

    todo_plan = build_declared_plan(messages)
    calls = tool_calls_from_messages(messages)

    # contract: try each skill manifest that has a real file behind it
    contract = None
    contract_skill = None
    for m in manifests:
        if m.get('kind') != 'skill':
            continue
        path = m.get('path')
        c = contract_for_skill(m.get('name', ''), path)
        if c:
            contract = c
            contract_skill = m.get('name')
            break

    step_rows, contract_status = build_step_results(
        contract.get('contract') if contract else None, todo_plan, calls)

    failures = []
    for c in calls:
        f = classify_failure(c)
        if f:
            _recovery_for(f, calls, c['index'])
            failures.append(f)

    if contract is None:
        contract_block = {
            'status': 'unknown_due_to_missing_data',
            'reason': '本次执行的 Skill 未定义结构化执行契约',
            'steps': [],
            'violations': [],
        }
    else:
        violations = [
            {'type': r['status'], 'step_id': r['step_id'],
             'severity': 'high' if r['status'] in ('missing', 'out_of_order', 'failed')
             else 'low',
             'evidence_refs': r['evidence_refs'],
             'reason': r.get('reason')}
            for r in step_rows
            if r['status'] in ('missing', 'out_of_order', 'failed',
                               'completed_claimed')
        ]
        contract_block = {
            'status': contract_status,
            'source': contract.get('source'),
            'skill': contract_skill,
            'confidence': contract.get('confidence'),
            'steps': step_rows,
            'violations': violations,
        }

    required_total = sum(1 for r in step_rows if r['expected'])
    required_done = sum(1 for r in step_rows
                        if r['expected'] and r['status'] == 'completed_confirmed')
    report = {
        'schema_version': 'v1',
        'execution': {
            'session_id': attempt.get('session_id'),
            'attempt_id': attempt.get('id'),
            'attempt_no': attempt.get('attempt_no'),
            'operation': attempt.get('operation'),
            'source_type': attempt.get('source_type'),
            'agent': {
                'requested': attempt.get('requested_agent'),
                'effective': attempt.get('effective_agent'),
                'resolution': attempt.get('agent_resolution'),
            },
            'model': {
                'requested': attempt.get('requested_model'),
                'effective': attempt.get('effective_model'),
                'resolution': attempt.get('model_resolution'),
            },
            'prompt': {
                'raw_hash': attempt.get('raw_prompt_hash'),
                'effective_hash': attempt.get('effective_prompt_hash'),
                'effective_len': attempt.get('effective_prompt_len'),
            },
            'started_at': _iso(attempt.get('started_at')),
            'finished_at': _iso(attempt.get('finished_at')),
            'status': attempt.get('status'),
            'skills': [m for m in manifests if m.get('kind') == 'skill'],
        },
        'contract': contract_block,
        'declared_plan': todo_plan,
        'observed_evidence': {
            'tool_call_count': len(calls),
            'tool_names': [c['name'] for c in calls],
        },
        'tool_failures': failures,
        'step_completion': {
            'required_total': required_total,
            'required_completed_confirmed': required_done,
            'rate': round(required_done / required_total, 2) if required_total else None,
        },
        'data_completeness': {
            'score': _data_completeness(attempt, manifests, events_count,
                                        todo_plan, contract),
            'limitations': [] if contract else ['Skill 未定义执行契约，无法审计步骤遗漏'],
        },
    }
    return report


def _iso(v):
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def ensure_diagnosis_for_session(session_id: str) -> dict:
    """Find the latest terminal attempt of a session and build/refresh its
    diagnosis. Returns the diagnoses DB row as dict, or {'status': ...} when
    there is nothing to audit yet."""
    from db import get_db
    from utils import execution_audit

    attempts = execution_audit.get_attempts(session_id, limit=10)
    terminal = [a for a in attempts
                if a.get('status') in ('completed', 'failed', 'stopped')]
    if not terminal:
        return {'status': 'no_attempt', 'attempts': attempts}

    attempt = terminal[0]
    manifests = execution_audit.get_manifests(attempt['id'])
    messages = _load_messages(session_id)
    events = execution_audit.get_events(attempt['id'], limit=2000)

    report = build_diagnosis_report(attempt, manifests, messages, len(events))
    completeness = report.get('data_completeness', {}).get('score') or 0
    violations = (report.get('contract', {}) or {}).get('violations') or []
    status = 'partial' if (not contract_ready(report)
                           or report['data_completeness']['score'] < 1) else 'completed'

    diag_id = 'diag_' + secrets.token_hex(6)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM ai_execution_diagnoses WHERE attempt_id = %s "
                "ORDER BY created_at DESC LIMIT 1", (attempt['id'],))
            row = cur.fetchone()
            if row:
                diag_id = row[0]
                cur.execute(
                    "UPDATE ai_execution_diagnoses SET status=%s, report=%s, "
                    " data_completeness=%s, completed_at=NOW() WHERE id=%s",
                    (status, json.dumps(report, ensure_ascii=False, default=str),
                     completeness, diag_id))
            else:
                cur.execute(
                    "INSERT INTO ai_execution_diagnoses "
                    "(id, attempt_id, target_session_id, status, report, "
                    " data_completeness, completed_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,NOW())",
                    (diag_id, attempt['id'], session_id, status,
                     json.dumps(report, ensure_ascii=False, default=str),
                     completeness))
            _write_step_rows(cur, attempt['id'],
                             report.get('contract', {}).get('steps') or [],
                             (contract_id_of(report)))
        conn.commit()
    report['diagnosis_id'] = diag_id
    report['status'] = status
    report['events_count'] = len(events)
    return report


def contract_ready(report: dict) -> bool:
    c = report.get('contract', {})
    return c.get('status') not in ('no_contract', 'unknown_due_to_missing_data')


def contract_id_of(report: dict) -> str | None:
    return None  # contract linkage kept via owner; rows store step facts only


def _write_step_rows(cur, attempt_id: str, steps: list[dict], contract_id) -> None:
    cur.execute("DELETE FROM ai_execution_step_results WHERE attempt_id = %s",
                (attempt_id,))
    for r in steps:
        cur.execute(
            "INSERT INTO ai_execution_step_results "
            "(id, attempt_id, contract_id, step_id, expected, declared_by_agent, "
            " observed, status, evidence_level, confidence, duration_ms, "
            " evidence_refs, reason) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (attempt_id, step_id) DO UPDATE SET "
            " status=EXCLUDED.status, evidence_refs=EXCLUDED.evidence_refs, "
            " reason=EXCLUDED.reason",
            ('stpr_' + secrets.token_hex(6), attempt_id, contract_id,
             r.get('step_id'), bool(r.get('expected')),
             bool(r.get('declared_by_agent')), bool(r.get('observed')),
             r.get('status'), r.get('evidence_level'), r.get('confidence'),
             r.get('duration_ms'), json.dumps(r.get('evidence_refs') or [],
                                              ensure_ascii=False),
             r.get('reason')))


def _load_messages(session_id: str) -> list[dict]:
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, role, content, created_at, seq "
                "FROM ai_chat_messages WHERE session_id = %s "
                "ORDER BY COALESCE(seq, 0) ASC, created_at ASC", (session_id,))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        if isinstance(r.get('content'), str):
            try:
                r['content'] = json.loads(r['content'])
            except (ValueError, TypeError):
                r['content'] = []
        r['created_at'] = _iso(r.get('created_at'))
    return rows
