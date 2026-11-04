import json
import logging
import secrets

logger = logging.getLogger(__name__)


def _abort_timeout_child(session_id: str | None) -> None:
    """遗留项 3：step 超时兜底的收尾——abort 子会话的 OpenCode 回合。

    超时 CAS 只把 step 标 failed；子会话若已有在跑的 OpenCode 回合，不
    abort 会滞留 running 直到对账器接管。这里按 step.session_id 反查
    ai_chat_sessions.opencode_session_id 并 best-effort abort：进程已死/
    网络异常只记日志不抛——step 的 failed 转移已落地，对账器兜底语义
    不变（batch_engine 取消路径同模式）。"""
    if not session_id:
        return
    try:
        from db import get_db
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT opencode_session_id, workspace_path "
                    "FROM ai_chat_sessions WHERE id = %s", (session_id,))
                row = cur.fetchone()
        oc_sid, ws = row if row else (None, None)
        if not oc_sid:
            return
        from ai.opencode.opencode_client import OpenCodeClient
        from config import OPENCODE_BASE_URL
        OpenCodeClient(OPENCODE_BASE_URL).abort_session(oc_sid,
                                                        directory=ws or '')
    except Exception:
        logger.warning('step timeout abort child session failed sid=%s',
                       session_id, exc_info=True)



def _open_policy_approval(run: dict, step: dict, policy: dict):
    """策略拦截（P2 §7.1 策略拦截最小集）：节点声明 approval_policy 的
    agent step 在派发前进入 waiting_approval；approve 后正常派发
    （decide 落 step succeeded 的结构性语义不适用——这里 step 回 blocked，
    由下轮 advance 重新派发）。幂等：uniq pending per step 天然防重。"""
    import uuid as _uuid
    from db import get_db
    aid = 'apr_' + secrets.token_hex(6)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO ai_approval_requests "
                    "  (id, run_id, step_id, risk_level, effect_summary, "
                    "   requested_roles, requested_users, expires_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, NULL)",
                    (aid, run['id'], step['id'],
                     policy.get('risk_level') or 'medium',
                     f"策略拦截: {step.get('name') or step['node_id']}",
                     json.dumps(policy.get('requested_roles') or []),
                     json.dumps(policy.get('requested_users') or [])))
                cur.execute(
                    "UPDATE ai_orchestration_steps SET status='waiting_approval', "
                    "  updated_at=NOW() WHERE id=%s", (step['id'],))
            conn.commit()
        from ai.batch import batch_events as batch_events
        batch_events.append_event(run['id'], 'approval.requested',
                                  aggregate_type='step',
                                  aggregate_id=step['id'],
                                  payload={'policy': True})
    except Exception:
        logger.exception('policy approval open failed step=%s', step['id'])


def _open_approval(run: dict, step: dict):
    """approval step → 创建审批请求（幂等：uniq pending per step）。"""
    import uuid as _uuid
    from db import get_db
    node = step.get('node_def') or {}
    appr = node.get('approval') or {}
    aid = 'apr_' + secrets.token_hex(6)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO ai_approval_requests "
                    "  (id, run_id, step_id, risk_level, effect_summary, "
                    "   requested_roles, requested_users, expires_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, "
                    "  CASE WHEN %s IS NULL THEN NULL "
                    "       ELSE NOW() + (%s || ' seconds')::interval END)",
                    (aid, run['id'], step['id'],
                     appr.get('risk_level') or 'medium',
                     node.get('name') or step['node_id'],
                     json.dumps(appr.get('requested_roles') or []),
                     json.dumps(appr.get('requested_users') or []),
                     appr.get('timeoutSeconds'),
                     str(int(appr.get('timeoutSeconds') or 0) or 0)))
                cur.execute(
                    "UPDATE ai_orchestration_steps SET status='waiting_approval', "
                    "  updated_at=NOW() WHERE id=%s", (step['id'],))
            conn.commit()
        from ai.batch import batch_events as batch_events
        batch_events.append_event(run['id'], 'approval.requested',
                                  aggregate_type='step',
                                  aggregate_id=step['id'],
                                  payload={'approvalId': aid})
    except Exception:
        # 并发下第二个 pending 插入被唯一索引挡住 → 已有请求在 pending，跳过
        logger.debug('approval already pending step=%s', step['id'], exc_info=True)



__all__ = [
    'logger',
    '_open_approval',
    '_open_policy_approval',
    '_abort_timeout_child',
]
