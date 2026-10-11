"""门禁策略库（2026-10-11 spec §8）：命名的规则集，批引用即生效。

策略只是模板——生效快照是登记时的 action_expectations 行，策略后改不重写
已登记任务；DELETE 只防护非终态引用（批自身 pending/running/paused，或其
下仍有非终态子会话）。checks 全量过 validate_checks（deny/post/checkpoint
混装），不带病入库。
"""
import json
import logging
import secrets

from db import get_db as _default

log = logging.getLogger(__name__)


class PolicyInUseError(RuntimeError):
    """策略仍被非终态批引用，删除被拒绝。"""


def create_policy(user_id: str, name: str, description, checks,
                  enabled: bool = True, get_db=None) -> dict:
    from utils.agent_ledger import validate_checks
    normalized = validate_checks(checks or [])
    name = (name or '').strip()
    if not name or len(name) > 100:
        raise ValueError('策略名必填且不超过 100 字')
    pid = 'gpol_' + secrets.token_hex(6)
    db_ctx = get_db or _default
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO gate_policies (id, name, description, checks, "
                " enabled, created_by) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
                (pid, name, description,
                 json.dumps(normalized, ensure_ascii=False)
                 if normalized else '[]',
                 bool(enabled), user_id))
            row = cur.fetchone()
            cols = [d[0] for d in cur.description]
        conn.commit()
    return dict(zip(cols, row))


def list_policies(enabled=None, get_db=None) -> list[dict]:
    db_ctx = get_db or _default
    sql = "SELECT * FROM gate_policies"
    params: list = []
    if enabled is not None:
        sql += " WHERE enabled = %s"
        params.append(bool(enabled))
    sql += " ORDER BY updated_at DESC"
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


def get_policy(pid: str, get_db=None) -> dict | None:
    db_ctx = get_db or _default
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM gate_policies WHERE id = %s", (pid,))
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))


def update_policy(pid: str, *, name=None, description=None, checks=None,
                  enabled=None, get_db=None) -> dict | None:
    """哨兵语义：None=不更新该字段（checks/name 用 None 判定；enabled 用
    is None 判定——False 是合法值）。checks 显式传入才过校验。"""
    from utils.agent_ledger import validate_checks
    sets, params = [], []
    if name is not None:
        name = (name or '').strip()
        if not name or len(name) > 100:
            raise ValueError('策略名必填且不超过 100 字')
        sets.append('name=%s'); params.append(name)
    if description is not None:
        sets.append('description=%s'); params.append(description)
    if checks is not None:
        normalized = validate_checks(checks)
        sets.append('checks=%s')
        params.append(json.dumps(normalized, ensure_ascii=False))
    if enabled is not None:
        sets.append('enabled=%s'); params.append(bool(enabled))
    if not sets:
        return get_policy(pid, get_db=get_db)
    sets.append('updated_at=NOW()')
    params.append(pid)
    db_ctx = get_db or _default
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE gate_policies SET {', '.join(sets)} "
                        "WHERE id=%s RETURNING *", tuple(params))
            row = cur.fetchone()
            cols = [d[0] for d in cur.description] if row else []
        conn.commit()
    return dict(zip(cols, row)) if row else None


def count_active_refs(pid: str, get_db=None) -> int:
    """引用该策略且仍存活的批数（DELETE 防护依据）。

    「存活」= 批自身处于非终态（pending/running/paused），或其下仍有
    非终态子会话。只看子会话不够：刚创建尚未派发出会话的 running/pending
    批同样必须挡住删除；终态批的历史引用随策略删除放行（期望行快照已
    生效，不回写）。
    """
    db_ctx = get_db or _default
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM ai_chat_batches b WHERE "
                " b.gate_policy_id = %s AND ("
                "   b.status IN ('pending','running','paused')"
                "   OR EXISTS ("
                "     SELECT 1 FROM ai_chat_sessions s WHERE s.batch_id = b.id "
                "     AND s.status IN ('pending','running','paused')))",
                (pid,))
            return cur.fetchone()[0]


def delete_policy(pid: str, get_db=None) -> bool:
    if count_active_refs(pid, get_db=get_db) > 0:
        raise PolicyInUseError('策略仍被运行中/待运行的批任务引用，'
                               '请先解绑或等待其终态')
    db_ctx = get_db or _default
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM gate_policies WHERE id=%s", (pid,))
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted
