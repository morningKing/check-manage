"""list_session_files util 行为契约测试 + 与 routes/ai_chat.py::list_files
端点的字段集等价锁。"""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch
from utils.workspace_outputs import (
    list_session_files, LISTFILES_MAX_FILES, LISTFILES_MAX_DEPTH, LISTFILES_SKIP_DIRS,
    augment_with_data_file_id, search_session_files,
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
    assert LISTFILES_MAX_DEPTH == 32
    # outputs/ 故意不在跳过集（它要被列）；uploads/ 不在跳过集（被列但分类为 uploads）
    assert LISTFILES_SKIP_DIRS == {'uploads', '.git', 'node_modules', '.venv', '__pycache__', '.opencode'}


def test_list_session_files_finds_deeply_nested_files():
    """回归测试：曾经 LISTFILES_MAX_DEPTH=8 会静默丢弃更深层的新增/修改文件
    （用户报告"新增或修改的文件目录过深，在会话管理里显示不出来"）。"""
    with tempfile.TemporaryDirectory() as td:
        deep_dir = os.path.join(td, 'outputs')
        for i in range(12):
            deep_dir = os.path.join(deep_dir, f'level{i}')
        os.makedirs(deep_dir, exist_ok=True)
        with open(os.path.join(deep_dir, 'deep.txt'), 'w') as f:
            f.write('deep')
        files, truncated = list_session_files(td)
        paths = {f['path'] for f in files}
        expected = 'outputs/' + '/'.join(f'level{i}' for i in range(12)) + '/deep.txt'
        assert expected in paths
        assert truncated is False


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


# ---- search_session_files：@ 联想服务端搜索（含克隆仓库内容） ----


def _add_cloned_repo(td):
    """造一个克隆进工作区的代码仓库（含自己的 .git）——AGENTS.md 引导的标准
    工作区形态，list_session_files 整棵跳过它，@ 联想搜索必须能触及。"""
    root = Path(td)
    (root / 'cloned' / '.git').mkdir(parents=True, exist_ok=True)
    (root / 'cloned' / 'src').mkdir(parents=True, exist_ok=True)
    (root / 'cloned' / 'code.py').write_text('x=1', encoding='utf-8')
    (root / 'cloned' / 'src' / 'helper.ts').write_text('export {}', encoding='utf-8')
    (root / 'cloned' / '.git' / 'config').write_text('[core]', encoding='utf-8')


def test_search_finds_nested_repo_files():
    """克隆仓库（自带 .git）里的文件必须可被搜到；.git 内部永不出现。"""
    with tempfile.TemporaryDirectory() as td:
        _make_workspace(td)
        _add_cloned_repo(td)
        files, truncated = search_session_files(td, 'helper')
        paths = [f['path'] for f in files]
        assert 'cloned/src/helper.ts' in paths
        assert all('.git/' not in p for p in paths)


def test_search_scoring_basename_prefix_beats_substring():
    """评分与前端 filterFileItems 镜像：basename 前缀 > path 前缀 >
    basename 子串 > path 子串。"""
    with tempfile.TemporaryDirectory() as td:
        _make_workspace(td)
        root = Path(td)
        # basename 前缀命中（score 1）
        (root / 'repo').mkdir(exist_ok=True)
        (root / 'repo' / 'alpha.py').write_text('1', encoding='utf-8')
        # basename 子串命中（score 3）：'al' 在 'marshal' 中部
        (root / 'repo' / 'marshal.py').write_text('2', encoding='utf-8')
        # path 子串命中（score 4）：basename 不含 'al'
        (root / 'galaxy').mkdir(exist_ok=True)
        (root / 'galaxy' / 'beta.py').write_text('3', encoding='utf-8')
        files, _ = search_session_files(td, 'al')
        assert [f['path'] for f in files] == ['repo/alpha.py', 'repo/marshal.py', 'galaxy/beta.py']


def test_search_empty_query_returns_scan_order():
    """空 q：全部文件按扫描序返回（uploads 优先，随后 walk 序），用于 @ 裸唤起。"""
    with tempfile.TemporaryDirectory() as td:
        _make_workspace(td)
        files, truncated = search_session_files(td, '', limit=50)
        paths = [f['path'] for f in files]
        assert truncated is False
        assert paths[0] == 'uploads/in.txt'
        assert 'outputs/report.md' in paths
        assert 'root.txt' in paths


def test_search_limit_truncates_with_flag():
    with tempfile.TemporaryDirectory() as td:
        _make_workspace(td)
        root = Path(td)
        (root / 'many').mkdir(exist_ok=True)
        for i in range(8):
            (root / 'many' / f'item{i}.py').write_text(str(i), encoding='utf-8')
        files, truncated = search_session_files(td, 'item', limit=3)
        assert len(files) == 3
        assert truncated is True


def test_search_respects_skip_dirs_and_dotfiles():
    with tempfile.TemporaryDirectory() as td:
        _make_workspace(td)
        _add_cloned_repo(td)
        files, _ = search_session_files(td, '', limit=50)
        paths = {f['path'] for f in files}
        # 既有排除规则不变
        assert 'node_modules/x.js' not in paths
        assert '.secret' not in paths
        assert 'opencode.json' not in paths
        assert 'cloned/.git/config' not in paths


def test_search_no_match_returns_empty():
    with tempfile.TemporaryDirectory() as td:
        _make_workspace(td)
        files, truncated = search_session_files(td, 'zzz-does-not-exist')
        assert files == []
        assert truncated is False


def test_search_null_workspace_returns_empty():
    assert search_session_files('', 'x') == ([], False)


def test_search_limit_clamped():
    with tempfile.TemporaryDirectory() as td:
        _make_workspace(td)
        files, _ = search_session_files(td, '', limit=0)
        assert len(files) == 1   # 钳到 1
        files, _ = search_session_files(td, '', limit=999)
        assert len(files) <= 50   # 钳到 50