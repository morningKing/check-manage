# -*- coding: utf-8 -*-
"""治理两表迁移幂等测试：run() 两遍后 information_schema 可见两表。"""
import importlib.util
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

_MIG = os.path.join(os.path.dirname(__file__), '..', 'migrations',
                    '2026_10_25c_mcp_tool_governance.py')


def _run_migration():
    spec = importlib.util.spec_from_file_location('_mtg_mig_test', _MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.run()


def test_migration_idempotent_creates_both_tables(db_conn):
    _run_migration()
    _run_migration()  # 第二遍不抛 = 幂等
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name IN ('ai_mcp_tools', 'ai_mcp_tool_rules')")
        names = {r[0] for r in cur.fetchall()}
    assert names == {'ai_mcp_tools', 'ai_mcp_tool_rules'}
