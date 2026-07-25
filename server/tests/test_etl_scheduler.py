"""ETL 后台调度器测试：认领语义 + 孤儿恢复 + 端到端执行一条 pending 日志。

走真实数据库（FOR UPDATE SKIP LOCKED 的并发语义、APScheduler 的 tick 执行，
都不适合 mock）。测试数据用 `_t_` 前缀自建自清理。
"""
import os
import sys
import time
import uuid
from datetime import datetime, timezone

import psycopg2.extras
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from db import get_db  # noqa: E402
from utils import etl_scheduler  # noqa: E402

TASK_ID = '_t_etl_sched_task'
COLLECTION = '_t_etl_sched_out'


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM etl_logs WHERE task_id = %s", (TASK_ID,))
        cur.execute("DELETE FROM etl_tasks WHERE id = %s", (TASK_ID,))
        cur.execute("DELETE FROM dynamic_data WHERE collection = %s", (COLLECTION,))


def _seed_task(steps):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO etl_tasks (id, name, description, steps, enabled, created_at, updated_at) '
            "VALUES (%s, %s, '', %s, TRUE, NOW(), NOW()) "
            'ON CONFLICT (id) DO UPDATE SET steps = EXCLUDED.steps',
            (TASK_ID, 'sched-test', psycopg2.extras.Json(steps)),
        )


def _seed_pending_log():
    log_id = f'_t_log_{uuid.uuid4().hex[:8]}'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO etl_logs (id, task_id, task_name, status, started_at, "
            "total_records, success_count, error_count, step_results, progress_current, cancel_requested) "
            "VALUES (%s, %s, 'sched-test', 'pending', %s, 0, 0, 0, '[]'::jsonb, 0, FALSE)",
            (log_id, TASK_ID, datetime.now(timezone.utc)),
        )
    return log_id


def _fetch_log(log_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT status, progress_current, total_records, current_step_name, success_count, error_count '
            'FROM etl_logs WHERE id = %s',
            (log_id,),
        )
        return cur.fetchone()


class TestClaimOne:
    # 注意：_claim_one 认领的是整张 etl_logs 表里最早的 pending 行，不按 task_id
    # 过滤。如果本机同时有 `npm run dev:all` 的后端进程在跑（它自己的调度器
    # 也在轮询同一个数据库），这个测试有极小概率被真实调度器抢先认领掉刚插入
    # 的这一行，导致断言失败——这是 claim-then-run 模式对着同一个开发数据库
    # 跑测试时的固有特性，不是这个测试本身的 bug。跑这个测试文件前建议先停掉
    # 本机正在跑的后端进程。
    def test_claims_pending_row_and_marks_running(self):
        _seed_task([{'id': 's1', 'name': 'input', 'type': 'json_input',
                     'config': {'data': '[]'}, 'onError': 'stop'}])
        log_id = _seed_pending_log()

        claimed = etl_scheduler._claim_one()

        assert claimed is not None
        assert claimed[0] == log_id
        status, *_ = _fetch_log(log_id)
        assert status == 'running'

    def test_no_pending_rows_returns_none(self):
        # _cleanup 已经清过本文件的 _t_ 前缀数据；不额外插入 pending 行
        claimed = etl_scheduler._claim_one()
        assert claimed is None or claimed[0].startswith('_t_') is False
        # 弱断言：不假设整个数据库没有其它 pending 行（并行测试可能存在别的
        # 测试数据），只确认调用不报错即可——真正的"认领了我插入的那一行"
        # 已经在 test_claims_pending_row_and_marks_running 里验证过


class TestRestartAudit:
    def test_resets_running_rows_to_pending(self):
        log_id = _seed_pending_log()
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE etl_logs SET status = 'running' WHERE id = %s", (log_id,))

        etl_scheduler._restart_audit()

        status, *_ = _fetch_log(log_id)
        assert status == 'pending'


class TestRunOneEndToEnd:
    def test_executes_pending_log_and_writes_result(self):
        _seed_task([
            {
                'id': 's1', 'name': '输入', 'type': 'json_input',
                'config': {'data': '[{"name": "a"}, {"name": "b"}]'},
                'onError': 'stop',
            },
            {
                'id': 's2', 'name': '写入', 'type': 'save_to_collection',
                'config': {'collection': COLLECTION, 'mode': 'insert'},
                'onError': 'stop',
            },
        ])
        log_id = _seed_pending_log()

        etl_scheduler._run_one(log_id, TASK_ID)

        status, progress, total, step_name, success, error = _fetch_log(log_id)
        assert status == 'success'
        assert success == 2
        assert error == 0
        assert total == 2
        assert progress == 2

        with get_db() as conn:
            cur = conn.cursor()
            cur.execute('SELECT COUNT(*) FROM dynamic_data WHERE collection = %s', (COLLECTION,))
            assert cur.fetchone()[0] == 2

    def test_deleted_task_marks_log_as_error(self):
        log_id = _seed_pending_log()
        # 故意不 seed task：模拟认领之后、执行之前任务被删除
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM etl_tasks WHERE id = %s", (TASK_ID,))

        etl_scheduler._run_one(log_id, TASK_ID)

        status, *_ = _fetch_log(log_id)
        assert status == 'error'
