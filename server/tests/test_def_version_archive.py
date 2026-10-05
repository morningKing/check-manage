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


def test_register_definition_versions_archives_known_and_mismatch(db_conn, tmp_path):
    """spec §4.3：未登记 hash 读文件带正文注册；已知 hash 不重读（返回 0）；
    manifest hash 与重读正文 sha256 不一致 → 只登记 hash 不归档。"""
    from utils.execution_audit import register_definition_versions
    name = f'arch-skill-{uuid.uuid4().hex[:8]}'
    d = tmp_path / name
    d.mkdir()
    md = d / 'SKILL.md'
    md.write_bytes(b'v1')
    h1 = hashlib.sha256(b'v1').hexdigest()
    m1 = [{'kind': 'skill', 'name': name, 'path': str(md), 'content_hash': h1}]
    try:
        assert register_definition_versions(m1) == 1
        with db_conn.cursor() as cur:
            cur.execute("SELECT content, content_captured_at "
                        "FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s AND content_hash=%s",
                        (name, h1))
            row = cur.fetchone()
        assert row and row[0] == 'v1' and row[1] is not None
        assert register_definition_versions(m1) == 0          # check-first 不重复
        md.write_bytes(b'v2')                                  # 新 hash → 新版本行
        h2 = hashlib.sha256(b'v2').hexdigest()
        assert register_definition_versions(
            [{'kind': 'skill', 'name': name, 'path': str(md),
              'content_hash': h2}]) == 1
        bad = 'f' * 64                                         # hash 与正文不一致
        assert register_definition_versions(
            [{'kind': 'skill', 'name': name, 'path': str(md),
              'content_hash': bad}]) == 1
        with db_conn.cursor() as cur:
            cur.execute("SELECT content FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s AND content_hash=%s",
                        (name, bad))
            assert cur.fetchone()[0] is None                   # 只登记不归档
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s", (name,))
        db_conn.commit()


def test_register_definition_versions_non_utf8_degrades_to_hash_only(db_conn, tmp_path):
    """非 UTF-8 定义文件（如 GBK）→ 只登记 hash 不归档，不中止整个注册批；
    同批正常 utf-8 定义照常归档（比照 mismatch 降级语义）。"""
    from utils.execution_audit import register_definition_versions
    gbk_name = f'arch-skill-{uuid.uuid4().hex[:8]}'
    utf8_name = f'arch-skill-{uuid.uuid4().hex[:8]}'
    d = tmp_path / 'nonutf8-batch'
    d.mkdir()
    raw_gbk = '中文正文'.encode('gbk')
    f_gbk = d / f'{gbk_name}.md'
    f_gbk.write_bytes(raw_gbk)
    h_gbk = hashlib.sha256(raw_gbk).hexdigest()
    f_utf8 = d / f'{utf8_name}.md'
    f_utf8.write_bytes(b'utf8-body')
    h_utf8 = hashlib.sha256(b'utf8-body').hexdigest()
    batch = [
        {'kind': 'skill', 'name': gbk_name, 'path': str(f_gbk),
         'content_hash': h_gbk},
        {'kind': 'skill', 'name': utf8_name, 'path': str(f_utf8),
         'content_hash': h_utf8},
    ]
    try:
        assert register_definition_versions(batch) == 2      # 两行都登记
        with db_conn.cursor() as cur:
            cur.execute("SELECT content FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s AND content_hash=%s",
                        (gbk_name, h_gbk))
            assert cur.fetchone()[0] is None                 # GBK 行只登记不归档
            cur.execute("SELECT content FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s AND content_hash=%s",
                        (utf8_name, h_utf8))
            assert cur.fetchone()[0] == 'utf8-body'          # 正常行照常归档
    finally:
        with db_conn.cursor() as cur:
            for n in (gbk_name, utf8_name):
                cur.execute("DELETE FROM ai_skill_def_versions "
                            "WHERE def_kind='skill' AND def_name=%s", (n,))
        db_conn.commit()


def test_collect_and_save_manifests_registers_versions(db_conn, tmp_path, monkeypatch):
    """collect_and_save_workspace_manifests 接线：执行落 manifest 后兜底归档。"""
    import utils.execution_audit as _ea
    name = f'arch-skill-{uuid.uuid4().hex[:8]}'
    ws = tmp_path / 'ws'
    skills = ws / '.opencode' / 'skills' / name
    skills.mkdir(parents=True)
    (skills / 'SKILL.md').write_bytes(b'collect-v1')
    uid, bid, sid, attempt = str(uuid.uuid4()), str(uuid.uuid4()), \
        str(uuid.uuid4()), str(uuid.uuid4())
    try:
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s, %s, 'x', 'FR', 'developer')", (uid, f'fr_{uid[:8]}'))
            cur.execute("INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
                        "VALUES (%s, %s, 'fr', 'p', 1)", (bid, uid))
            cur.execute("INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, "
                        "  batch_seq, workspace_path, session_token) "
                        "VALUES (%s, %s, 'completed', %s, 0, %s, %s)",
                        (sid, uid, bid, str(ws), f'tok-{sid[:12]}'))
            cur.execute("INSERT INTO ai_execution_attempts (id, session_id, source_type, "
                        "  operation, started_at, finished_at) "
                        "VALUES (%s, %s, 'batch', 'send', NOW(), NOW())", (attempt, sid))
        db_conn.commit()
        saved = _ea.collect_and_save_workspace_manifests(attempt, str(ws))
        assert saved >= 1
        h = hashlib.sha256(b'collect-v1').hexdigest()
        with db_conn.cursor() as cur:
            cur.execute("SELECT content FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s AND content_hash=%s",
                        (name, h))
            assert cur.fetchone()[0] == 'collect-v1'
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_execution_manifests WHERE attempt_id=%s", (attempt,))
            cur.execute("DELETE FROM ai_execution_attempts WHERE id=%s", (attempt,))
            cur.execute("DELETE FROM ai_chat_sessions WHERE id=%s", (sid,))
            cur.execute("DELETE FROM ai_chat_batches WHERE id=%s", (bid,))
            cur.execute("DELETE FROM users WHERE id=%s", (uid,))
            cur.execute("DELETE FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s", (name,))
        db_conn.commit()
