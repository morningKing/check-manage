"""_import_child_outputs_to_record 的确定性集成测试（execution-audit Spec
「AI 产出文件 → 数据行呈现」）：outputs/ 产物导入 data_files 并追加到记录
的 file 字段（{uid,name} 结构，与手工上传同构）。"""

import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.ai_scan_engine import _import_child_outputs_to_record


@pytest.fixture
def scan_env(db_conn, tmp_path):
    """建集合 + 记录 + 模拟子会话工作区（outputs/result.txt）。

    Yields dict: {db_conn(用不到，清理用), session_row, rid, coll, uid, task}。
    """
    uid = str(uuid.uuid4())
    coll = f'scanout_{uuid.uuid4().hex[:8]}'
    sid = f'sess_{uuid.uuid4().hex[:12]}'
    rid = f'rec_{uuid.uuid4().hex[:10]}'
    ws = tmp_path / 'ws'
    (ws / 'outputs').mkdir(parents=True)
    (ws / 'outputs' / 'result.txt').write_text('BAIZE-E2E-OK', encoding='utf-8')
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, 'x', 'u', 'developer')", (uid, f'so_{uid[:8]}'))
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, title, status, workspace_path) "
            "VALUES (%s, %s, 't', 'completed', %s)", (sid, uid, str(ws)))
        cur.execute(
            "INSERT INTO dynamic_data (id, collection, data, branch_id) "
            "VALUES (%s, %s, %s, 'main')",
            (rid, coll, json.dumps({'title': '记录', 'scan_status': '待处理'})))
    db_conn.commit()
    env = {
        'db_conn': db_conn,
        'uid': uid,
        'sid': sid,
        'rid': rid,
        'coll': coll,
        'task': {'collection': coll, 'branchId': 'main'},
        'session_row': {'workspace_path': str(ws), 'user_id': uid},
        'ws': str(ws),
    }
    yield env
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM dynamic_data WHERE id = %s", (rid,))
        cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    db_conn.commit()


def test_import_outputs_into_record(scan_env, db_conn):
    env = scan_env
    n = _import_child_outputs_to_record(
        task=env['task'], record_id=env['rid'], session_row=env['session_row'],
        file_field='result_doc')
    assert n == 1

    with db_conn.cursor() as cur:
        cur.execute("SELECT data FROM dynamic_data WHERE id = %s", (env['rid'],))
        data = cur.fetchone()[0]
    # 记录文件字段出现 {uid,name} 结构（与手工上传同构，数据行可直接呈现）
    doc = data.get('result_doc')
    assert isinstance(doc, list) and len(doc) == 1
    assert doc[0]['name'] == 'result.txt'
    assert doc[0]['uid']

    # data_files 表里确实有这条产物，文件真实存在
    with db_conn.cursor() as cur:
        cur.execute("SELECT original_name, storage_path FROM data_files "
                    "WHERE uploaded_by = %s "
                    "ORDER BY storage_path DESC LIMIT 1", (env['uid'],))
        name, path = cur.fetchone()
        assert name == 'result.txt'
        assert os.path.exists(path)


def test_import_outputs_missing_workspace(scan_env, db_conn, tmp_path):
    scan_env['session_row']['workspace_path'] = str(tmp_path / 'nope')
    n = _import_child_outputs_to_record(
        task=scan_env['task'], record_id=scan_env['rid'],
        session_row=scan_env['session_row'], file_field='result_doc')
    assert n == 0
