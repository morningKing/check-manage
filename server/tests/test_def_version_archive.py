# -*- coding: utf-8 -*-
"""定义版本正文归档测试（迁移列 / register_def_version / 扫描兜底）。

db_conn 为 conftest 的真实 dev 库连接；种子行用 uuid 后缀隔离，
finally 清理（仓库种子行清理约定）。
"""
import hashlib
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _load_migration():
    """迁移文件名以数字开头，无法常规 import——按 _run_dated_migrations
    同款 importlib 方式加载。"""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..',
                        'migrations', '2026_10_05_def_version_content_archive.py')
    spec = importlib.util.spec_from_file_location('_mig_def_version_content', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_columns_exist_and_idempotent(db_conn):
    mig = _load_migration()
    mig.run()
    mig.run()  # 幂等：重复执行无异常
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'ai_skill_def_versions' "
            "AND column_name IN ('content', 'content_captured_at')")
        cols = {r[0] for r in cur.fetchall()}
    assert {'content', 'content_captured_at'} <= cols
