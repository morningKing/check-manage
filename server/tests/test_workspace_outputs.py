"""list_session_files util 行为契约测试 + 与 routes/ai_chat.py::list_files
端点的字段集等价锁。"""
import os
import tempfile
from unittest.mock import patch
from utils.workspace_outputs import (
    list_session_files, LISTFILES_MAX_FILES, LISTFILES_MAX_DEPTH, LISTFILES_SKIP_DIRS,
    augment_with_data_file_id,
)


def _make_workspace(tmpdir):
    os.makedirs(os.path.join(tmpdir, 'uploads'), exist_ok=True)
    os.makedirs(os.path.join(tmpdir, 'outputs'), exist_ok=True)
    os.makedirs(os.path.join(tmpdir, 'outputs', 'sub'), exist_ok=True)
    with open(os.path.join(tmpdir, 'uploads', 'in.txt'), 'w') as f:
        f.write('input')
    with open(os.path.join(tmpdir, 'outputs', 'report.md'), 'w') as f:
        f.write('# Report')
    with open(os.path.join(tmpdir, 'outputs', 'sub', 'a.txt'), 'w') as f:
        f.write('a')
    with open(os.path.join(tmpdir, 'root.txt'), 'w') as f:
        f.write('root')
    # noise: 必须跳过
    os.makedirs(os.path.join(tmpdir, 'node_modules'), exist_ok=True)
    with open(os.path.join(tmpdir, 'node_modules', 'x.js'), 'w') as f:
        f.write('noise')
    # dotfile: 必须跳过
    with open(os.path.join(tmpdir, '.secret'), 'w') as f:
        f.write('hidden')
    # opencode.json: 必须跳过
    with open(os.path.join(tmpdir, 'opencode.json'), 'w') as f:
        f.write('{}')


def test_list_session_files_categorizes_dirs():
    with tempfile.TemporaryDirectory() as td:
        _make_workspace(td)
        files, truncated = list_session_files(td)
        by_path = {f['path']: f for f in files}
        # uploads/ 直接子：dir='uploads'
        assert by_path['uploads/in.txt']['dir'] == 'uploads'
        # outputs/ 递归：dir='outputs'
        assert by_path['outputs/report.md']['dir'] == 'outputs'
        assert by_path['outputs/sub/a.txt']['dir'] == 'outputs'
        # 根目录：dir='workspace'
        assert by_path['root.txt']['dir'] == 'workspace'
        # 字段齐全
        for f in files:
            assert set(f.keys()) == {'name', 'path', 'dir', 'size'}
            assert isinstance(f['size'], int)


def test_list_session_files_excludes_noise_and_dotfiles():
    with tempfile.TemporaryDirectory() as td:
        _make_workspace(td)
        files, _ = list_session_files(td)
        paths = {f['path'] for f in files}
        assert 'node_modules/x.js' not in paths
        assert '.secret' not in paths
        assert 'opencode.json' not in paths
        assert 'uploads/in.txt' in paths  # 但 uploads/ 目录本身被遍历


def test_listfiles_constants_match_old_definitions():
    """常量值与 routes/ai_chat.py 里抽走前一致（防误改）。"""
    assert LISTFILES_MAX_FILES == 1000
    assert LISTFILES_MAX_DEPTH == 8
    # outputs/ 故意不在跳过集（它要被列）；uploads/ 不在跳过集（被列但分类为 uploads）
    assert LISTFILES_SKIP_DIRS == {'uploads', '.git', 'node_modules', '.venv', '__pycache__', '.opencode'}


def test_augment_with_data_file_id_in_place():
    """单 SELECT 不 N+1，命中记录加 dataFileId，未命中 None。

    implementation 用 `from db import get_db` 懒导入（与 workspace_changes
    里的 record_session_files 同模式），所以 patch 的源是 `db.get_db`，
    而不是 `utils.workspace_outputs.get_db`——后者只是模块属性影子，懒导入
    每次仍从 `db` 模块原样取，patch 无效。
    """
    files = [
        {'name': 'a.txt', 'path': 'outputs/a.txt', 'dir': 'outputs', 'size': 10},
        {'name': 'b.txt', 'path': 'outputs/b.txt', 'dir': 'outputs', 'size': 20},
    ]
    rows = [('outputs/a.txt', 'df-1')]  # 只有 a 有记录

    class FakeCur:
        def execute(self, sql, params=None):
            FakeCur._exec_count += 1
        def fetchall(self):
            return rows
    FakeCur._exec_count = 0

    class FakeConn:
        def cursor(self): return FakeCur()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    with patch('db.get_db', new=lambda: FakeConn()):
        augment_with_data_file_id('sess-1', files)

    assert 'dataFileId' in files[0]
    assert files[0]['dataFileId'] == 'df-1'
    assert files[1]['dataFileId'] is None
    assert FakeCur._exec_count == 1   # 只一条 SELECT，不 N+1