"""执行器租约（P0 单实例安全，P1 接管语义的前置）。

ai_batch_worker_leases 表上的原子抢占/续租/释放原语。P0 语义（最小可用）：

- acquire：`INSERT ... ON CONFLICT DO UPDATE ... WHERE lease_until < NOW()
  OR owner_id = 自己` 原子抢占 —— 抢不到说明另一个活实例持有租约；
- heartbeat：条件 UPDATE 续租，owner 不匹配即失败（执行权已丢失）；
- release：条件 DELETE。

P0 不做跨实例接管（不因 lease 过期抢占别人，只解决同一套部署里多个进程
各自跑 worker）；fencing_token 预留单调递增语义，P1 接管时 +1 并用于写回
校验。函数在表缺失/连接失败时返回失败态并记日志 —— 租约绝不反过来打断
调用方（最坏情况退化为 P0 之前的多进程行为，而不是服务起不来）。
"""
import logging
import os
import socket
import uuid

logger = logging.getLogger(__name__)

DEFAULT_LEASE_TTL_SEC = 90
DEFAULT_HEARTBEAT_SEC = 20


def owner_id() -> str:
    """本进程实例标识：host:pid:random。同机多进程靠 pid 区分，跨机靠 host。"""
    return f'{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}'


def acquire(lease_key: str, owner: str, *,
            ttl_sec: int = DEFAULT_LEASE_TTL_SEC,
            lease_kind: str | None = None) -> tuple[bool, int]:
    """原子抢占租约。返回 (ok, fencing_token)。

    冲突规则：仅当现有租约已过期（lease_until < NOW()）或本就属于自己时
    允许抢占/续占；持有者的租约未过期时返回 (False, 0)。fencing_token 每次
    acquire 都 +1（含自己续占），保证旧执行体的 token 一定小于新执行体。
    """
    try:
        from db import get_db
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ai_batch_worker_leases
                        (lease_key, lease_kind, owner_id, fencing_token,
                         acquired_at, heartbeat_at, lease_until)
                    VALUES (%s, %s, %s, 1, NOW(), NOW(),
                            NOW() + (%s || ' seconds')::interval)
                    ON CONFLICT (lease_key) DO UPDATE SET
                        owner_id = EXCLUDED.owner_id,
                        lease_kind = EXCLUDED.lease_kind,
                        fencing_token = ai_batch_worker_leases.fencing_token + 1,
                        acquired_at = NOW(),
                        heartbeat_at = NOW(),
                        lease_until = EXCLUDED.lease_until
                      WHERE ai_batch_worker_leases.lease_until < NOW()
                         OR ai_batch_worker_leases.owner_id = EXCLUDED.owner_id
                    RETURNING fencing_token
                    """,
                    (lease_key, lease_kind or lease_key, owner, ttl_sec),
                )
                row = cur.fetchone()
        if row:
            return True, int(row[0])
        return False, 0
    except Exception as e:  # noqa: BLE001 —— 表缺失/库抖动不阻断启动
        logger.warning('lease acquire failed key=%s: %s', lease_key, e)
        return False, 0


def heartbeat(lease_key: str, owner: str, *,
              ttl_sec: int = DEFAULT_LEASE_TTL_SEC) -> bool:
    """续租。owner 不匹配（执行权已被接管/释放）返回 False。"""
    try:
        from db import get_db
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE ai_batch_worker_leases
                       SET heartbeat_at = NOW(),
                           lease_until = NOW() + (%s || ' seconds')::interval
                     WHERE lease_key = %s AND owner_id = %s
                    """,
                    (ttl_sec, lease_key, owner),
                )
                ok = cur.rowcount > 0
        return ok
    except Exception as e:  # noqa: BLE001
        logger.warning('lease heartbeat failed key=%s: %s', lease_key, e)
        return False


def release(lease_key: str, owner: str) -> bool:
    try:
        from db import get_db
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM ai_batch_worker_leases "
                    "WHERE lease_key = %s AND owner_id = %s",
                    (lease_key, owner),
                )
                ok = cur.rowcount > 0
        return ok
    except Exception as e:  # noqa: BLE001
        logger.warning('lease release failed key=%s: %s', lease_key, e)
        return False
