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


def _def_versions(cur, kind, name):
    cur.execute(
        "SELECT content_hash, content, content_captured_at "
        "FROM ai_skill_def_versions WHERE def_kind=%s AND def_name=%s "
        "ORDER BY first_seen_at", (kind, name))
    return cur.fetchall()


def test_register_def_version_content_backfill_and_no_overwrite(db_conn):
    """新 hash 带正文注册；无正文行被后到注册补齐；已有正文永不覆盖。"""
    from utils.skill_fit import register_def_version
    name = f'arch-skill-{uuid.uuid4().hex[:8]}'
    h = hashlib.sha256(b'v1').hexdigest()
    try:
        with db_conn.cursor() as cur:
            register_def_version(cur, 'skill', name, h)  # 拟合路径：无正文
            db_conn.commit()
            rows = _def_versions(cur, 'skill', name)
            assert len(rows) == 1 and rows[0][1] is None and rows[0][2] is None
            register_def_version(cur, 'skill', name, h, content='正文 v1')
            db_conn.commit()
            rows = _def_versions(cur, 'skill', name)
            assert len(rows) == 1                        # 不翻倍
            assert rows[0][1] == '正文 v1' and rows[0][2] is not None
            register_def_version(cur, 'skill', name, h, content='偷换正文')
            db_conn.commit()
            rows = _def_versions(cur, 'skill', name)
            assert len(rows) == 1 and rows[0][1] == '正文 v1'  # 已有正文不覆盖
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s", (name,))
        db_conn.commit()


def test_register_def_version_empty_hash_allowed(db_conn):
    """manifest 无 hash 按空串注册（content_hash NOT NULL 兜底，现状行为保持）。"""
    from utils.skill_fit import register_def_version
    name = f'arch-skill-{uuid.uuid4().hex[:8]}'
    try:
        with db_conn.cursor() as cur:
            register_def_version(cur, 'agent', name, '')
            db_conn.commit()
            rows = _def_versions(cur, 'agent', name)
            assert len(rows) == 1 and rows[0][0] == ''
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_skill_def_versions "
                        "WHERE def_kind='agent' AND def_name=%s", (name,))
        db_conn.commit()
