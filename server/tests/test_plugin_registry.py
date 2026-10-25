# -*- coding: utf-8 -*-
"""plugin_registry 真库测试：TTL 缓存/失效回落/种子不覆盖/门校验/菜单过滤。"""
import os, sys, uuid
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils import plugin_registry as pr  # noqa: E402


@pytest.fixture
def db_rows(db_conn):
    ids = [f'pp_{uuid.uuid4().hex[:10]}' for _ in range(2)]
    # plugin_enabled 对未注册 pid 直接 False（不查库），自造 pid 必须临时
    # 挂进注册表才能走到 DB 读取路径；default_enabled 一律 False，让 DB 值
    # （第一行 true / 第二行 false）成为被证明对象。teardown 摘除还原。
    for i in (0, 1):
        pr.PLUGINS[ids[i]] = pr.PluginManifest(
            id=ids[i], name=f'pp-test-{i}', description='test-only',
            version='0', default_enabled=False)
    with db_conn.cursor() as cur:
        for i in (0, 1):
            cur.execute("INSERT INTO platform_plugins (plugin_id, enabled) VALUES (%s, %s)",
                        (ids[i], i == 0))   # 第一行 enabled=true，第二行 false
    db_conn.commit()
    yield ids
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM platform_plugins WHERE plugin_id = ANY(%s)", (ids,))
        # 任何用例把 smart-writing 行翻成 FALSE 都要还原：删行回落
        # manifest default_enabled=True，下次启动/迁移种子重建行。
        cur.execute("DELETE FROM platform_plugins WHERE plugin_id = 'smart-writing'")
    db_conn.commit()
    for pid in ids:
        pr.PLUGINS.pop(pid, None)
    pr.invalidate()


def test_manifest_singleton_shape():
    m = pr.PLUGINS['smart-writing']
    assert m.process == 'se-node'
    assert m.menu_prefixes == ('/suggest-editor',)
    assert m.default_enabled is True


def test_enabled_reads_db_and_falls_back(db_rows):
    pr.invalidate()
    assert pr.plugin_enabled(db_rows[0]) is True
    assert pr.plugin_enabled(db_rows[1]) is False
    # 迁移种子已跑：smart-writing 行在且 enabled=TRUE
    assert pr.plugin_enabled('smart-writing') is True
    # 未知 pid → False
    assert pr.plugin_enabled('no-such') is False


def test_unknown_plugin_defaults_false_without_row():
    pr.invalidate()
    assert pr.plugin_enabled('no-such') is False


def test_invalidate_rereads(db_conn, db_rows):
    pr.invalidate()
    assert pr.plugin_enabled(db_rows[1]) is False
    with db_conn.cursor() as cur:
        cur.execute("UPDATE platform_plugins SET enabled = TRUE WHERE plugin_id = %s", (db_rows[1],))
    db_conn.commit()
    assert pr.plugin_enabled(db_rows[1]) is False        # 缓存仍命中
    pr.invalidate(db_rows[1])
    assert pr.plugin_enabled(db_rows[1]) is True         # 失效后重读


def test_gate_validates_url_prefix():
    from flask import Blueprint
    bp = Blueprint('x', __name__, url_prefix='/wrong')
    with pytest.raises(RuntimeError, match='api_prefixes'):
        pr.gate_plugin(bp, 'smart-writing')


def test_filter_menu_items_by_disabled_prefix(db_conn, monkeypatch):
    monkeypatch.setattr(pr, 'plugin_enabled',
                        lambda pid: {'smart-writing': False}.get(pid, True))
    items = [{'path': '/suggest-editor'}, {'path': '/suggest-editor/x'},
             {'path': '/home'}, {'path': None}]
    out = pr.filter_menu_items(items)
    assert [i['path'] for i in out] == ['/home', None]


def test_seed_never_resets_admin_choice(db_conn, db_rows):
    """种子幂等：先禁用再重跑迁移 run()，选择保留（spec §4 硬约束）。"""
    import importlib.util as ilu
    mp = os.path.join(os.path.dirname(__file__), '..', 'migrations',
                      '2026_10_25b_platform_plugins.py')
    spec = ilu.spec_from_file_location('_pp_mig', mp)
    mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with db_conn.cursor() as cur:  # smart-writing 行强制禁用（模拟管理员选择）
        cur.execute("INSERT INTO platform_plugins (plugin_id, enabled) VALUES ('smart-writing', FALSE)"
                    " ON CONFLICT (plugin_id) DO UPDATE SET enabled = FALSE")
    db_conn.commit()
    mod.run()
    mod.run()  # 跑两遍证幂等
    with db_conn.cursor() as cur:
        cur.execute("SELECT enabled FROM platform_plugins WHERE plugin_id = 'smart-writing'")
        assert cur.fetchone()[0] is False
    # 尾部还原（污染纪律）：删行回落 manifest 默认，下次种子重建
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM platform_plugins WHERE plugin_id = 'smart-writing'")
    db_conn.commit()
    pr.invalidate()
