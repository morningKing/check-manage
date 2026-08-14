"""import_recorded_files 的 extra_whitelist 扩展语义。
- 默认（不传）：DB 命中放行，DB 无 → NOT_RECORDED（与旧行为等价）
- 传额外白名单：DB 无但 path 在 extra 中 → 放行，导入成功后回填 set_data_file_id
- 都不命中：NOT_RECORDED
- extra 与 DB 命中重叠：走 DB 快路径（已有的 data_file_id 直接 reuse）

Ruling C：save_workspace_file / data_file_meta 在 import_recorded_files
内部以 `from routes.data_files import ...` 形式懒导入，故 monkeypatch 必须落在
源模块 routes.data_files 上，patch 全名 `utils.session_file_import.save_workspace_file`
会 AttributeError。get_recorded_path / set_data_file_id 是 utils.session_file_import
顶层 `from utils.workspace_changes import ...` 进来的，照常 patch 本模块名即可。
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.session_file_import import import_recorded_files


@pytest.fixture
def workspace_with_files():
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, 'outputs'), exist_ok=True)
        with open(os.path.join(td, 'outputs', 'report.md'), 'w') as f:
            f.write('# r')
        yield td


def test_import_DB_hit_uses_existing_data_file(workspace_with_files, monkeypatch):
    """DB 有记录且有 data_file_id → status=existing，不重新写。"""
    def fake_get_recorded_path(sid, p):
        return {'path': p, 'status': 'added', 'dataFileId': 'df-1'}

    def fake_data_file_meta(fid):
        return {'id': 'df-1', 'name': 'report.md', 'size': 3}

    monkeypatch.setattr('utils.session_file_import.get_recorded_path', fake_get_recorded_path)
    # Ruling C: data_file_meta 懒导入，patch 源模块
    monkeypatch.setattr('routes.data_files.data_file_meta', fake_data_file_meta)
    results = import_recorded_files('s', workspace_with_files, ['outputs/report.md'])
    assert results[0]['status'] == 'existing'
    assert results[0]['file']['id'] == 'df-1'


def test_import_DB_miss_without_extra_returns_NOT_RECORDED(workspace_with_files, monkeypatch):
    monkeypatch.setattr('utils.session_file_import.get_recorded_path', lambda sid, p: None)
    results = import_recorded_files('s', workspace_with_files, ['outputs/report.md'])
    assert results[0].get('code') == 'NOT_RECORDED'


def test_import_DB_miss_with_extra_whitelist_passes(workspace_with_files, monkeypatch):
    """admin 端的核心扩展：DB 无但 live 在白名单 → 放行 + 真导入 + 回填。"""
    monkeypatch.setattr('utils.session_file_import.get_recorded_path', lambda sid, p: None)
    saved = {'meta': {'id': 'df-new', 'name': 'report.md', 'size': 3}, 'err': None}

    def fake_save_workspace_file(abs_path, rel_path, uploaded_by=None):
        return saved['meta'], saved['err']

    set_calls = []
    # Ruling C: save_workspace_file 懒导入，patch 源模块
    monkeypatch.setattr('routes.data_files.save_workspace_file', fake_save_workspace_file)
    monkeypatch.setattr('utils.session_file_import.set_data_file_id',
                        lambda sid, p, fid: set_calls.append((sid, p, fid)))
    results = import_recorded_files(
        'sess-X', workspace_with_files, ['outputs/report.md'],
        uploaded_by='owner-1',
        extra_whitelist={'outputs/report.md'},
    )
    assert results[0]['status'] == 'imported'
    assert results[0]['file']['id'] == 'df-new'
    assert set_calls == [('sess-X', 'outputs/report.md', 'df-new')]  # 回填到 DB


def test_import_DB_miss_not_in_extra_returns_NOT_RECORDED(workspace_with_files, monkeypatch):
    monkeypatch.setattr('utils.session_file_import.get_recorded_path', lambda sid, p: None)
    results = import_recorded_files(
        's', workspace_with_files, ['outputs/other.md'],
        extra_whitelist={'outputs/report.md'},  # 不含 other
    )
    assert results[0].get('code') == 'NOT_RECORDED'


def test_import_default_no_change_to_owner_semantics(workspace_with_files, monkeypatch):
    """owner 端默认不传 extra：行为与旧实现一致。回归锚。"""
    monkeypatch.setattr('utils.session_file_import.get_recorded_path', lambda sid, p: None)
    results = import_recorded_files(
        's', workspace_with_files, ['outputs/report.md'],
        # 不传 extra_whitelist
    )
    assert results[0].get('code') == 'NOT_RECORDED'