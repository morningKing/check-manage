"""运营日志表保留任务（大数据量优化 §2.2/2.3/2.4，2026-09-28）。

operation_logs / agent_tool_calls / webhook_logs 无保留期无限增长——行数
本身拖慢写入与索引，agent_tool_calls 还放大动作门禁核对的全量扫描成本。
挂进既有每日 retention 作业（app.py _audit_retention_daily），按天阈值
分批 DELETE（ctid 批量，避免大表长事务锁）。

配置（server/.env，天数；0=该表禁用清理）：
  OPERATION_LOG_RETENTION_DAYS    默认 180
  AGENT_TOOL_CALL_RETENTION_DAYS  默认 180（与执行事件 180 天口径一致）
  WEBHOOK_LOG_RETENTION_DAYS      默认 90
注意：这是删除型任务——沿用执行事件「30/180 天双层保留」已确立的默认
开启语义；如需永久留存某表，把对应天数设为 0。
"""
import logging
import os

logger = logging.getLogger(__name__)

_BATCH = 10000
_MAX_BATCHES = 500  # 单日单表清理上限 500 万行，防首次回填清理拖死作业


def _retention_days(env_key: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(env_key, str(default)) or default))
    except ValueError:
        return default


def _purge(cur, table: str, ts_col: str, days: int) -> int:
    """按时间阈值分批删除，返回删除总行数。days<=0 时不清理。"""
    if days <= 0:
        return 0
    total = 0
    for _ in range(_MAX_BATCHES):
        cur.execute(
            f"DELETE FROM {table} WHERE ctid IN ("
            f"  SELECT ctid FROM {table} "
            f"  WHERE {ts_col} < NOW() - (%s || ' days')::interval "
            f"  LIMIT %s)", (str(days), _BATCH))
        removed = cur.rowcount
        total += removed
        if removed < _BATCH:
            break
    return total


def apply_log_retention() -> dict:
    """清理三张无保留期日志表的过期行。单表失败不影响其余表。"""
    result = {}
    specs = [
        ('operation_logs', 'created_at',
         _retention_days('OPERATION_LOG_RETENTION_DAYS', 180)),
        ('agent_tool_calls', 'occurred_at',
         _retention_days('AGENT_TOOL_CALL_RETENTION_DAYS', 180)),
        ('webhook_logs', 'created_at',
         _retention_days('WEBHOOK_LOG_RETENTION_DAYS', 90)),
    ]
    from db import get_db
    with get_db() as conn:
        for table, ts_col, days in specs:
            try:
                with conn.cursor() as cur:
                    removed = _purge(cur, table, ts_col, days)
                conn.commit()
                result[table] = {'retentionDays': days, 'deleted': removed}
                if removed:
                    logger.info('log retention %s: deleted %s rows (> %sd)',
                                table, removed, days)
            except Exception as e:  # noqa: BLE001 —— 单表失败不阻断其余表
                conn.rollback()
                result[table] = {'retentionDays': days, 'error': str(e)}
                logger.warning('log retention %s failed: %s', table, e)
    return result
