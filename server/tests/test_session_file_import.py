"""utils/session_file_import.py 的端到端测试（真实 git + 真实 DB）。"""
import os
import subprocess
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from db import get_db  # noqa: E402


def _git(cwd, *args):
    subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True)


def _mk_session():
    uid = 'u-import-' + uuid.uuid4().hex[:8]
    sid = 'sess_import_' + uuid.uuid4().hex[:8]
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


@pytest.fixture
def store_root(tmp_path, monkeypatch):
    """把 data_files 的存储根指到临时目录，避免污染真实存储。"""
    import routes.data_files as df
    store = tmp_path / 'data-files-store'
    monkeypatch.setattr(df, 'DATA_FILES_ROOT', str(store))
    return store


def _cleanup(uid, file_ids):
    with get_db() as conn:
        with conn.cursor() as cur:
            for fid in file_ids:
                cur.execute("DELETE FROM data_files WHERE id = %s", (fid,))
            cur.execute("DELETE FROM users WHERE id = %s", (uid,))
        conn.commit()


def test_import_end_to_end(tmp_path, store_root):
    """记录 -> 导入 -> data_files 落盘落库 -> 幂等 -> 白名单/缺失分支。"""
    from utils.workspace_changes import git_changes, record_session_files
    from utils.session_file_import import import_recorded_files
    uid, sid = _mk_session()
    created_ids = []
    try:
        ws = str(tmp_path / 'ws')
        os.makedirs(ws)
        _git(ws, 'init', '-q')
        for name, content in (('report.md', '# 报告\n'), ('out/data.csv', 'a,b\n1,2\n')):
            p = os.path.join(ws, name)
            os.makedirs(os.path.dirname(p), exist_ok=True) if '/' in name else None
            with open(p, 'w', encoding='utf-8') as f:
                f.write(content)
        changes, _, ok = git_changes(ws)
        assert ok
        record_session_files(sid, changes)

        results = import_recorded_files(sid, ws,
                                        ['report.md', 'out/data.csv', 'ghost.py'])
        by = {r['path']: r for r in results}
        # 白名单外的路径拒绝
        assert by['ghost.py']['code'] == 'NOT_RECORDED'
        # 记录过的两个文件导入成功，拿到 data_files id
        for name in ('report.md', 'out/data.csv'):
            r = by[name]
            assert r['status'] == 'imported', r
            fid = r['file']['id']
            created_ids.append(fid)
            assert r['file']['name'] == name.rsplit('/', 1)[-1]
            assert r['file']['size'] > 0
            # 磁盘上真的落了
            assert os.path.isfile(os.path.join(str(store_root), fid[:2], fid,
                                               name.rsplit('/', 1)[-1]))
        # data_file_id 已回填
        from utils.workspace_changes import get_recorded_path
        assert get_recorded_path(sid, 'report.md')['dataFileId'] == created_ids[0]

        # 幂等：再次导入返回原 id，不新增行
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM data_files")
                n_before = cur.fetchone()[0]
        again = import_recorded_files(sid, ws, ['report.md'])
        assert again[0]['status'] == 'existing'
        assert again[0]['file']['id'] == created_ids[0]
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM data_files")
                assert cur.fetchone()[0] == n_before

        # 记录过但工作区里已删掉的文件 → FILE_MISSING（还没导入过的才走到这）
        with open(os.path.join(ws, 'gone.py'), 'w') as f:
            f.write('x')
        record_session_files(sid, [{'path': 'gone.py', 'status': 'added'}])
        os.remove(os.path.join(ws, 'gone.py'))
        missing = import_recorded_files(sid, ws, ['gone.py'])
        assert missing[0]['code'] == 'FILE_MISSING'
    finally:
        _cleanup(uid, created_ids)


def test_import_rejects_path_traversal(tmp_path, store_root):
    """白名单按记录里的相对路径匹配，`../` 之类逃不出去。"""
    from utils.workspace_changes import record_session_files
    from utils.session_file_import import import_recorded_files
    uid, sid = _mk_session()
    try:
        record_session_files(sid, [{'path': 'a.py', 'status': 'added'}])
        results = import_recorded_files(sid, str(tmp_path), ['../secret.py'])
        assert results[0]['code'] == 'NOT_RECORDED'
    finally:
        _cleanup(uid, [])
