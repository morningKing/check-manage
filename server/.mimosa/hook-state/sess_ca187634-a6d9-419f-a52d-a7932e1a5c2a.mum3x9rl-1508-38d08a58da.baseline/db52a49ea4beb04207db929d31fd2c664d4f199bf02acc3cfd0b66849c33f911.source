"""sync_platform_skills_to_oc_global 的单元测试。

覆盖：启用技能建链接（无符号链接权限环境为带标记副本）、禁用技能清残留
（不动用户自装目录）、幂等。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from utils.global_skills import (PLATFORM_SYNC_MARKER,
                                 sync_platform_skills_to_oc_global)


@pytest.fixture
def setup(tmp_path):
    ws_root = tmp_path / 'ai-workspaces'
    gs = ws_root / 'global-skills'
    oc = tmp_path / 'oc-global'
    for name in ('data-ops', 'trace-analyzer'):
        (gs / name).mkdir(parents=True)
        (gs / name / 'SKILL.md').write_text('---\nname: x\n---\n', encoding='utf-8')
    (oc / 'skills' / 'user-own').mkdir(parents=True)  # 用户自装技能
    return str(ws_root), str(oc)


def _installed(path):
    """同步后的形态：符号链接 或 带平台标记的副本目录。"""
    return os.path.islink(path) or (
        os.path.isdir(path)
        and os.path.exists(os.path.join(path, PLATFORM_SYNC_MARKER)))


def test_sync_creates_entries_for_enabled(setup):
    ws, oc = setup
    synced = sync_platform_skills_to_oc_global(oc, ws, skills=[
        {'name': 'data-ops', 'enabled': True},
        {'name': 'trace-analyzer', 'enabled': True},
    ])
    assert sorted(synced) == ['data-ops', 'trace-analyzer']
    for name in ('data-ops', 'trace-analyzer'):
        p = os.path.join(oc, 'skills', name)
        assert _installed(p), f'{p} 既非链接也非带标记副本'
    assert os.path.exists(os.path.join(oc, 'skills', 'data-ops', 'SKILL.md'))


def test_sync_removes_stale_entries_for_disabled(setup):
    ws, oc = setup
    sync_platform_skills_to_oc_global(oc, ws, skills=[
        {'name': 'data-ops', 'enabled': True}])
    # 之后禁用：平台同步残留（链接或带标记副本）应被清除
    synced = sync_platform_skills_to_oc_global(oc, ws, skills=[
        {'name': 'data-ops', 'enabled': False}])
    assert synced == []
    assert not _installed(os.path.join(oc, 'skills', 'data-ops'))


def test_sync_never_touches_user_owned_dirs(setup):
    ws, oc = setup
    # 用户自装同名实体目录（无平台标记）→ 平台让位，不覆盖不删除
    own = os.path.join(oc, 'skills', 'data-ops')
    os.makedirs(own)
    synced = sync_platform_skills_to_oc_global(oc, ws, skills=[
        {'name': 'data-ops', 'enabled': True}])
    assert 'data-ops' in synced          # 视为可用
    assert os.path.isdir(own) and not _installed(own)  # 内容未被平台接管


def test_sync_idempotent(setup):
    ws, oc = setup
    skills = [{'name': 'data-ops', 'enabled': True}]
    sync_platform_skills_to_oc_global(oc, ws, skills=skills)
    p = os.path.join(oc, 'skills', 'data-ops')
    before = os.lstat(p).st_mtime_ns
    synced = sync_platform_skills_to_oc_global(oc, ws, skills=skills)
    assert synced == ['data-ops']
    assert os.lstat(p).st_mtime_ns == before  # 未重写
