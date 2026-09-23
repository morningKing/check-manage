"""执行前审批（ai-harness-p2 spec §7）。

与 P0 的动作门禁（事后核对）互补，不互相替代。决策写 decision_hash
（可审计不可篡改）；approve/reject 幂等（uniq pending per step + 状态 CAS）。
"""
import hashlib
import json
import secrets

from db import get_db


def create_approval(**kwargs) -> str:
    """（引擎内部入口；测试可直接造）"""
    aid = 'apr_' + secrets.token_hex(6)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ai_approval_requests "
                "  (id, run_id, step_id, risk_level, effect_summary, "
                "   requested_roles, requested_users) "
                "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)",
                (aid, kwargs['run_id'], kwargs.get('step_id'),
                 kwargs.get('risk_level') or 'medium',
                 kwargs.get('effect_summary'),
                 json.dumps(kwargs.get('requested_roles') or []),
                 json.dumps(kwargs.get('requested_users') or [])))
        conn.commit()
    return aid


def can_decide(appr: dict, user: dict) -> bool:
    """审批权限：admin.ai_approval 能力键（调用方已鉴权）或被点名的用户/角色。"""
    if user and (user.get('username') in (appr.get('requested_users') or [])):
        return True
    roles = appr.get('requested_roles') or []
    if user and user.get('role') in roles:
        return True
    return False  # 能力键在路由层由 @require_permission 把关


def decide(approval_id: str, decision: str, decided_by: str, *,
           comment: str | None = None) -> dict | None:
    """approve / reject（幂等：非 pending 返回既有状态，不重复决策）。"""
    if decision not in ('approved', 'rejected'):
        raise ValueError('decision 只支持 approved/rejected')
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, run_id, step_id, requested_at "
                        "FROM ai_approval_requests WHERE id = %s",
                        (approval_id,))
            row = cur.fetchone()
            if not row:
                return None
            if row[0] != 'pending':
                return {'id': approval_id, 'status': row[0],
                        'duplicate': True}
            decision_hash = hashlib.sha256(
                json.dumps({'id': approval_id, 'decision': decision,
                            'by': decided_by,
                            'comment': comment or ''},
                           ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
            cur.execute(
                "UPDATE ai_approval_requests SET status=%s, decided_by=%s, "
                "  decision_comment=%s, decision_hash=%s, resolved_at=NOW() "
                "WHERE id=%s AND status='pending' RETURNING run_id, step_id",
                (decision, decided_by, comment, decision_hash, approval_id))
            landed = cur.fetchone()
        conn.commit()
    if not landed:
        return {'id': approval_id, 'status': 'pending', 'duplicate': True}
    run_id, step_id = landed
    # 推进 step / run（approval 是结构性节点：approve → succeeded；reject → failed）
    from utils import orchestration_engine
    new_step_status = 'succeeded' if decision == 'approved' else 'failed'
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ai_orchestration_steps SET status=%s, "
                "  error_message=%s, finished_at=NOW(), updated_at=NOW() "
                "WHERE id=%s",
                (new_step_status,
                 None if decision == 'approved' else '审批被拒绝',
                 step_id))
        conn.commit()
    from utils import batch_events
    batch_events.append_event(run_id, 'command.applied',
                              aggregate_type='step', aggregate_id=step_id,
                              payload={'approval': decision,
                                       'by': decided_by,
                                       'decisionHash': decision_hash})
    orchestration_engine._advance_run(run_id)
    return {'id': approval_id, 'status': decision, 'duplicate': False,
            'runId': run_id, 'stepId': step_id}


def expire_overdue() -> int:
    """审批超时 → expired（按策略等同 reject，spec §7.2 升级语义后续扩展）。"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ai_approval_requests SET status='expired', "
                "  resolved_at=NOW() "
                "WHERE status='pending' AND expires_at IS NOT NULL "
                "  AND expires_at < NOW() RETURNING id, run_id, step_id")
            rows = cur.fetchall()
        conn.commit()
    for _aid, run_id, step_id in rows:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_orchestration_steps SET status='failed', "
                    "  error_message='审批超时', finished_at=NOW(), "
                    "  updated_at=NOW() WHERE id=%s", (step_id,))
            conn.commit()
        from utils import orchestration_engine
        orchestration_engine._advance_run(run_id)
    return len(rows)


def get_pending_for_inbox(user: dict, limit: int = 50) -> list[dict]:
    """inbox 投影（Phase B）：pending 审批 → /workflow/inbox 的 kind='ai_approval'
    项。角色/用户定向过滤；admin.ai_approval 权限由路由层把关后可见全部。"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, run_id, step_id, risk_level, effect_summary, "
                "requested_roles, requested_users, requested_at "
                "FROM ai_approval_requests WHERE status='pending' "
                "ORDER BY requested_at DESC LIMIT %s", (limit,))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    out = []
    for r in rows:
        if not can_decide(r, user):
            # 非定向对象不可见（admin 能力键的路由调用方另行放开）
            continue
        out.append({
            'kind': 'ai_approval',
            'approvalId': r['id'],
            'instanceId': r['run_id'],
            'workflowName': 'AI 编排审批',
            'stageName': r.get('effect_summary') or r['step_id'],
            'collection': 'ai_orchestration_runs',
            'recordId': r['run_id'],
            'riskLevel': r.get('risk_level'),
            'enteredAt': r['requested_at'].isoformat() if r.get('requested_at') else None,
        })
    return out


def list_all(status: str | None = None, limit: int = 100) -> list[dict]:
    with get_db() as conn:
        with conn.cursor() as cur:
            if status:
                cur.execute(
                    "SELECT id, run_id, step_id, status, risk_level, "
                    "effect_summary, decided_by, decision_comment, "
                    "requested_at, resolved_at FROM ai_approval_requests "
                    "WHERE status=%s ORDER BY requested_at DESC LIMIT %s",
                    (status, limit))
            else:
                cur.execute(
                    "SELECT id, run_id, step_id, status, risk_level, "
                    "effect_summary, decided_by, decision_comment, "
                    "requested_at, resolved_at FROM ai_approval_requests "
                    "ORDER BY requested_at DESC LIMIT %s", (limit,))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        for k in ('requested_at', 'resolved_at'):
            if r.get(k) is not None:
                r[k] = r[k].isoformat()
    return rows
