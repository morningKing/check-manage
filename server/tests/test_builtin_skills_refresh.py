"""内置技能内容刷新(ensure_builtin_skills / _refresh_builtin)测试。

仓库 skills/ 是内置技能唯一事实来源:已安装副本在未被管理员改动时会随仓库
内容刷新;被改过(哈希对不上 marker)则保护不动。全部走临时目录+真库行,收尾清理。
"""
import os
import shutil
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.global_skills import (BUILTIN_HASH_MARKER, _dir_hash,
                                 global_skills_root)


@pytest.fixture
def skill_env(tmp_path):
    """临时 workspace + 一个已安装的内置技能目录 + DB 行 + 临时"仓库"技能。"""
    from db import get_db
    ws = str(tmp_path / 'ws')
    root = global_skills_root(ws)
    name = f'probe-skill-{uuid.uuid4().hex[:6]}'
    dest = os.path.join(root, name)
    os.makedirs(dest)
    desc = f'内置探针 {name}'
    with open(os.path.join(dest, 'SKILL.md'), 'w', encoding='utf-8') as f:
        f.write(f'---\nname: {name}\ndescription: {desc}\n---\n\nv1 内容\n')

    repo_dir = os.path.join(str(tmp_path), 'repo-skills', name)
    os.makedirs(repo_dir)
    with open(os.path.join(repo_dir, 'SKILL.md'), 'w', encoding='utf-8') as f:
        f.write(f"---\nname: {name}\ndescription: {desc}\n---\n\nv1 内容\n")

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO global_skills (id, name, description, enabled, file_size, "
                "  created_at, updated_at) VALUES (%s,%s,%s,TRUE,0,now(),now())",
                (f'gs-{name}', name, desc))
        conn.commit()
    yield {'name': name, 'dest': dest, 'desc': desc, 'repo_dir': repo_dir}
    shutil.rmtree(os.path.join(str(tmp_path), 'ws'), ignore_errors=True)
    from db import get_db as g
    with g() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM global_skills WHERE name=%s", (name,))
        conn.commit()


def test_refresh_updates_unmodified_install(skill_env):
    """无 marker 的存量安装:采纳仓库内容刷新并写入 marker、更新 DB 描述。"""
    f = skill_env
    with open(os.path.join(f['repo_dir'], 'SKILL.md'), 'w', encoding='utf-8') as fh:
        fh.write(f"---\nname: {f['name']}\ndescription: {f['desc']} v2\n---\n\nv2 新配方\n")

    from utils.global_skills import _refresh_builtin
    assert _refresh_builtin(f['dest'], f['desc'] + ' v2',
                            repo_skills_dir=f['repo_dir']) is True

    with open(os.path.join(f['dest'], 'SKILL.md'), encoding='utf-8') as fh:
        assert 'v2 新配方' in fh.read()
    assert os.path.isfile(os.path.join(f['dest'], BUILTIN_HASH_MARKER))
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT description FROM global_skills WHERE name=%s",
                        (f['name'],))
            assert 'v2' in cur.fetchone()[0]


def test_refresh_protects_admin_modified(skill_env):
    """marker 存在且安装副本哈希对不上 → 管理员改过,拒绝刷新。"""
    f = skill_env
    with open(os.path.join(f['dest'], BUILTIN_HASH_MARKER), 'w', encoding='utf-8') as fh:
        fh.write(_dir_hash(f['dest']))
    with open(os.path.join(f['dest'], 'SKILL.md'), 'a', encoding='utf-8') as fh:
        fh.write('\n管理员补充的定制说明\n')
    with open(os.path.join(f['repo_dir'], 'SKILL.md'), 'w', encoding='utf-8') as fh:
        fh.write(f"---\nname: {f['name']}\ndescription: {f['desc']} v2\n---\n\nv2\n")

    from utils.global_skills import _refresh_builtin
    assert _refresh_builtin(f['dest'], f['desc'],
                            repo_skills_dir=f['repo_dir']) is False
    with open(os.path.join(f['dest'], 'SKILL.md'), encoding='utf-8') as fh:
        assert '管理员补充的定制说明' in fh.read()


def test_refresh_idempotent_when_repo_unchanged(skill_env):
    """安装副本与 marker 一致、仓库内容也一致 → 幂等跳过(返回 False,内容不动)。"""
    f = skill_env
    with open(os.path.join(f['dest'], BUILTIN_HASH_MARKER), 'w', encoding='utf-8') as fh:
        fh.write(_dir_hash(f['dest']))

    from utils.global_skills import _refresh_builtin
    assert _refresh_builtin(f['dest'], f['desc'],
                            repo_skills_dir=f['repo_dir']) is False
    with open(os.path.join(f['dest'], 'SKILL.md'), encoding='utf-8') as fh:
        assert 'v1 内容' in fh.read()
    # 二次校验:marker 记录的哈希与当前内容一致(哈希不含 marker 自身)
    with open(os.path.join(f['dest'], BUILTIN_HASH_MARKER), encoding='utf-8') as fh:
        assert fh.read().strip() == _dir_hash(f['dest'])


def test_fresh_install_marker_stable_across_boots(skill_env):
    """新装路径写 marker 后,下一次启动(_refresh_builtin)必须是 no-op——
    回归:marker 曾被计入目录哈希,导致每次启动都误判'被管理员改动'。"""
    f = skill_env
    # 模拟 ensure 的 fresh-install 落盘
    shutil.rmtree(f['dest'])
    shutil.copytree(f['repo_dir'], f['dest'])
    with open(os.path.join(f['dest'], BUILTIN_HASH_MARKER), 'w',
              encoding='utf-8') as fh:
        fh.write(_dir_hash(f['dest']))

    from utils.global_skills import _refresh_builtin
    assert _refresh_builtin(f['dest'], f['desc'],
                            repo_skills_dir=f['repo_dir']) is False
