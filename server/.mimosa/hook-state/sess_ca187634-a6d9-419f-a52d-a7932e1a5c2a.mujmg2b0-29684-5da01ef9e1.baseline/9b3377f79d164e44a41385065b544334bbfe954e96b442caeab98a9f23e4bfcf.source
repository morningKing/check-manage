"""字段索引后台任务：轮询 field_indexes 表，异步 CREATE/DROP INDEX CONCURRENTLY。

见 utils/field_indexes.py（资格判定 + 与 page_configs.fields 的同步）。
CONCURRENTLY 不能在事务块里跑，每次建/删都用独立的 autocommit 连接。
"""
import traceback
import psycopg2
from apscheduler.schedulers.background import BackgroundScheduler
from config import DB_CONFIG
from db import get_db
from utils.field_indexes import sql_literal

_scheduler = None
TICK_INTERVAL_SEC = 30
BATCH_SIZE = 5


def _build_index(collection, field_name, index_name):
    """建一个 (data->>'field') WHERE collection='x' 的部分表达式索引。

    返回 None 表示成功，否则返回错误信息字符串。
    """
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute(
            f'CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name} '
            f'ON dynamic_data ((data->>{sql_literal(field_name)})) '
            f'WHERE collection = {sql_literal(collection)}'
        )
        return None
    except Exception as e:
        # CONCURRENTLY 建失败会在 pg_index 里留一个 invalid 索引占位，
        # 不清掉的话同名索引永远建不起来（下次重试会一直卡在这里）。
        try:
            cur.execute(f'DROP INDEX CONCURRENTLY IF EXISTS {index_name}')
        except Exception:
            pass
        return str(e)
    finally:
        conn.close()


def _drop_index(index_name):
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute(f'DROP INDEX CONCURRENTLY IF EXISTS {index_name}')
        return None
    except Exception as e:
        return str(e)
    finally:
        conn.close()


def _tick():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT collection, field_name, index_name FROM field_indexes "
            "WHERE status = 'pending' ORDER BY requested_at LIMIT %s",
            (BATCH_SIZE,),
        )
        pending = cur.fetchall()
        for collection, field_name, _ in pending:
            cur.execute(
                "UPDATE field_indexes SET status = 'building' "
                'WHERE collection = %s AND field_name = %s',
                (collection, field_name),
            )
        conn.commit()

        cur.execute(
            "SELECT collection, field_name, index_name FROM field_indexes "
            "WHERE status = 'dropping' ORDER BY requested_at LIMIT %s",
            (BATCH_SIZE,),
        )
        dropping = cur.fetchall()

    for collection, field_name, index_name in pending:
        error = _build_index(collection, field_name, index_name)
        with get_db() as conn:
            cur = conn.cursor()
            if error:
                cur.execute(
                    "UPDATE field_indexes SET status = 'failed', error = %s "
                    'WHERE collection = %s AND field_name = %s',
                    (error[:2000], collection, field_name),
                )
            else:
                cur.execute(
                    "UPDATE field_indexes SET status = 'ready', ready_at = NOW(), error = NULL "
                    'WHERE collection = %s AND field_name = %s',
                    (collection, field_name),
                )
            conn.commit()

    for collection, field_name, index_name in dropping:
        error = _drop_index(index_name)
        with get_db() as conn:
            cur = conn.cursor()
            if error:
                cur.execute(
                    'UPDATE field_indexes SET error = %s '
                    'WHERE collection = %s AND field_name = %s',
                    (error[:2000], collection, field_name),
                )
            else:
                cur.execute(
                    'DELETE FROM field_indexes WHERE collection = %s AND field_name = %s',
                    (collection, field_name),
                )
            conn.commit()


def _safe_tick():
    try:
        _tick()
    except Exception:
        traceback.print_exc()


def start_field_index_scheduler(app):
    global _scheduler
    if _scheduler is not None:
        return
    # 启动时把上次进程异常退出、卡在 building 状态的行退回 pending 重试
    # （不会重复建：CREATE INDEX CONCURRENTLY IF NOT EXISTS 本身幂等）。
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE field_indexes SET status = 'pending' WHERE status = 'building'")
        conn.commit()

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(_safe_tick, 'interval', seconds=TICK_INTERVAL_SEC,
                       id='field_index_tick', max_instances=1, coalesce=True)
    _scheduler.start()
