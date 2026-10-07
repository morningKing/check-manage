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


def test_sync_refreshes_stale_platform_copy(setup):
    """删旧传新（同名更新）只换中央存储；带标记的复制兜底副本若不校验
    内容，会把旧版本一直喂给 OC（2026-10-07 实测缺口）。同步必须识别
    陈旧副本并刷新；刷新后仍带标记。"""
    ws, oc = setup
    skills = [{'name': 'data-ops', 'enabled': True}]
    sync_platform_skills_to_oc_global(oc, ws, skills=skills)
    copy = os.path.join(oc, 'skills', 'data-ops')
    if not os.path.isdir(copy) or os.path.islink(copy):
        pytest.skip('本机有符号链接权限，复制兜底路径不适用')
    # 模拟"删旧传新"：中央存储内容更新（副本仍是旧内容）
    src_skill = os.path.join(ws, 'global-skills', 'data-ops', 'SKILL.md')
    with open(src_skill, 'w', encoding='utf-8') as f:
        f.write('---\nname: data-ops\nversion: v2\n---\nnew body\n')
    synced = sync_platform_skills_to_oc_global(oc, ws, skills=skills)
    assert synced == ['data-ops']
    with open(os.path.join(copy, 'SKILL.md'), encoding='utf-8') as f:
        assert 'version: v2' in f.read()          # 副本已刷新
    assert os.path.exists(os.path.join(copy, PLATFORM_SYNC_MARKER))
    # 刷新后幂等：内容一致不再重写（标记文件 mtime 不变）
    marker_mtime = os.lstat(os.path.join(copy, PLATFORM_SYNC_MARKER)).st_mtime_ns
    synced = sync_platform_skills_to_oc_global(oc, ws, skills=skills)
    assert synced == ['data-ops']
    assert os.lstat(
        os.path.join(copy, PLATFORM_SYNC_MARKER)).st_mtime_ns == marker_mtime


def test_sync_never_touches_user_owned_dir_with_different_content(setup):
    """用户自装同名目录（无标记）即便内容与平台不同也不被接管/刷新。"""
    ws, oc = setup
    own = os.path.join(oc, 'skills', 'data-ops')
    os.makedirs(own)
    with open(os.path.join(own, 'SKILL.md'), 'w', encoding='utf-8') as f:
        f.write('---\nname: my-own-data-ops\n---\nuser content')
    synced = sync_platform_skills_to_oc_global(oc, ws, skills=[
        {'name': 'data-ops', 'enabled': True}])
    assert 'data-ops' in synced
    with open(os.path.join(own, 'SKILL.md'), encoding='utf-8') as f:
        assert 'user content' in f.read()          # 未被平台内容覆盖
    assert not os.path.exists(os.path.join(own, PLATFORM_SYNC_MARKER))
