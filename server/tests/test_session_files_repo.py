"""ai_chat_session_files 独立记录的测试（真实 DB + 真实 git）。"""
import os
import subprocess
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from db import get_db  # noqa: E402


def _git(cwd, *args):
    subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True)


def _mk_session():
    """插一对临时 user/session 行，返回 (uid, sid)；调用方负责删 user 收尾。"""
    uid = 'u-sessfiles-' + uuid.uuid4().hex[:8]
    sid = 'sess_files_' + uuid.uuid4().hex[:8]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s,%s,'x',%s,'developer')", (uid, uid, uid))
            cur.execute("INSERT INTO ai_chat_sessions (id, user_id, workspace_path, "
                        "session_token, token_expires_at) VALUES "
                        "(%s,%s,'/tmp/x',%s, now() + interval '1 day')",
                        (sid, uid, 'tok-' + uuid.uuid4().hex))
        conn.commit()
    return uid, sid


def test_tables_exist_with_expected_columns():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'ai_chat_session_files' ORDER BY column_name
            """)
            cols = {r[0] for r in cur.fetchall()}
    assert cols == {'id', 'session_id', 'path', 'status', 'data_file_id',
                    'first_seen_at', 'last_seen_at'}


def test_record_and_get_session_files(tmp_path):
    """git 扫描 -> record_session_files -> get_session_files 端到端。"""
    from utils.workspace_changes import git_changes, record_session_files, get_session_files
    uid, sid = _mk_session()
    try:
        ws = str(tmp_path)
        _git(ws, 'init', '-q')
        with open(os.path.join(ws, 'tracked.txt'), 'w') as f:
            f.write('base\n')
        _git(ws, 'add', '.')
        _git(ws, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'base')
        with open(os.path.join(ws, 'new.py'), 'w') as f:
            f.write('print(1)\n')                       # added
        with open(os.path.join(ws, 'tracked.txt'), 'w') as f:
            f.write('edited\n')                         # modified

        changes, _, ok = git_changes(ws)
        assert ok
        record_session_files(sid, changes)

        files = get_session_files(sid)
        by = {f['path']: f for f in files}
        assert by['new.py']['status'] == 'added'
        assert by['tracked.txt']['status'] == 'modified'
        assert by['new.py']['firstSeenAt'] and by['new.py']['lastSeenAt']
        # 按路径排序返回
        assert [f['path'] for f in files] == sorted(f['path'] for f in files)

        # 幂等：重复记录不产生重复行
        record_session_files(sid, changes)
        assert len(get_session_files(sid)) == 2
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE id = %s", (uid,))
            conn.commit()


def test_status_updates_but_first_seen_preserved():
    """同一文件先 added 后被跟踪并修改：status 更新为 modified，
    first_seen_at 保留第一次出现的时间。"""
    from utils.workspace_changes import record_session_files, get_session_files
    uid, sid = _mk_session()
    try:
        record_session_files(sid, [{'path': 'a.py', 'status': 'added'}])
        first = get_session_files(sid)[0]
        record_session_files(sid, [{'path': 'a.py', 'status': 'modified'}])
        after = get_session_files(sid)[0]
        assert after['status'] == 'modified'
        assert after['firstSeenAt'] == first['firstSeenAt']
        assert len(get_session_files(sid)) == 1
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE id = %s", (uid,))
            conn.commit()


def test_collapsed_dir_entries_not_recorded():
    """折叠的 `dir/` 条目与 deleted 状态不落库——记录的是具体文件。"""
    from utils.workspace_changes import record_session_files, get_session_files
    uid, sid = _mk_session()
    try:
        record_session_files(sid, [
            {'path': 'pulled/', 'status': 'added', 'kind': 'dir', 'count': 99},
            {'path': 'gone.txt', 'status': 'deleted'},
            {'path': 'real.py', 'status': 'added'},
        ])
        assert [f['path'] for f in get_session_files(sid)] == ['real.py']
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE id = %s", (uid,))
            conn.commit()


def test_deleting_session_cascades_files():
    from utils.workspace_changes import record_session_files, get_session_files
    uid, sid = _mk_session()
    record_session_files(sid, [{'path': 'a.py', 'status': 'added'}])
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
        conn.commit()
    assert get_session_files(sid) == []
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (uid,))
        conn.commit()


def test_session_files_table_in_backup_map():
    """新表必须出现在备份清单与还原顺序里（历史上忘加过三次）。"""
    from utils.backup import BACKUP_TABLE_MAP, RESTORE_ORDER
    assert 'ai_chat_session_files' in BACKUP_TABLE_MAP
    assert 'ai_chat_session_files' in RESTORE_ORDER
    # 外键依赖：必须排在 ai_chat_sessions 之后还原
    assert RESTORE_ORDER.index('ai_chat_session_files') > RESTORE_ORDER.index('ai_chat_sessions')


def test_recorded_path_whitelist_and_data_file_mapping():
    """导入端点依赖的三件事：白名单查询、单条查询、data_file_id 回填。"""
    from utils.workspace_changes import (record_session_files, get_recorded_paths,
                                         get_recorded_path, set_data_file_id,
                                         get_session_files)
    uid, sid = _mk_session()
    try:
        record_session_files(sid, [{'path': 'out/report.md', 'status': 'added'}])
        assert get_recorded_paths(sid) == {'out/report.md'}
        assert get_recorded_path(sid, 'nope.txt') is None
        rec = get_recorded_path(sid, 'out/report.md')
        assert rec['status'] == 'added' and rec['dataFileId'] is None

        set_data_file_id(sid, 'out/report.md', 'df-123')
        assert get_recorded_path(sid, 'out/report.md')['dataFileId'] == 'df-123'
        assert get_session_files(sid)[0]['dataFileId'] == 'df-123'
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE id = %s", (uid,))
            conn.commit()
