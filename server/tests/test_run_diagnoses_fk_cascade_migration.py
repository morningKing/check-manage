# -*- coding: utf-8 -*-
"""ai_run_diagnoses.attempt_id 外键补 ON DELETE CASCADE 迁移测试。

契约：run() 幂等（两遍不抛）；跑完后从 attempts 删行级联删诊断行——
删批任务链路（sessions → attempts → diagnoses）不再被诊断行挡住。
"""
import importlib.util
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

_MIG = os.path.join(os.path.dirname(__file__), '..', 'migrations',
                    '2026_11_28_run_diagnoses_fk_cascade.py')


def _run_migration():
    spec = importlib.util.spec_from_file_location('_run_diag_fk_test', _MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.run()


def _cascade_type(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("""
            SELECT confdeltype::text FROM pg_constraint
            WHERE conrelid = 'ai_run_diagnoses'::regclass
              AND confrelid = 'ai_execution_attempts'::regclass
              AND contype = 'f'""")
        return {r[0] for r in cur.fetchall()}


def test_migration_makes_fk_cascade_and_is_idempotent(db_conn):
    _run_migration()
    _run_migration()  # 第二遍不抛 = 幂等
    assert _cascade_type(db_conn) == {'c'}


def test_deleting_attempt_cascades_diagnosis(db_conn):
    """行为契约：删 attempt 行 → 诊断行随级联消失（修复前此处报 FK violation）。"""
    _run_migration()
    sid = 'AITEST-DIAGFK-' + uuid.uuid4().hex[:8]
    aid = 'AITEST-DIAGFK-attempt-' + uuid.uuid4().hex[:8]
    with db_conn.cursor() as cur:
        # 自造父行（AITEST 前缀）满足 attempts → sessions 的既有 FK
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, title, status) "
            "VALUES (%s, 'user-admin', 'AITEST-DIAGFK session', 'completed')",
            (sid,))
        cur.execute(
            "INSERT INTO ai_execution_attempts (id, session_id, source_type) "
            "VALUES (%s, %s, 'batch')",
            (aid, sid))
        cur.execute(
            "INSERT INTO ai_run_diagnoses (id, attempt_id, overall, dimensions, "
            "generator_version) VALUES (%s, %s, 'success', '{}', 'v1')",
            ('AITEST-DIAGFK-diag-' + uuid.uuid4().hex[:8], aid))
        db_conn.commit()

        cur.execute("DELETE FROM ai_execution_attempts WHERE id = %s", (aid,))
        cur.execute(
            "SELECT count(*) FROM ai_run_diagnoses WHERE attempt_id = %s", (aid,))
        remaining = cur.fetchone()[0]
        cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
        db_conn.commit()
    assert remaining == 0
