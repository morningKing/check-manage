# -*- coding: utf-8 -*-
"""sync_metadata：快照 upsert 参数/消失行清理/矩阵播种默认值（fake_db 录制）。"""
from unittest.mock import MagicMock
from contextlib import contextmanager


def _make_fake_db():
    cur = MagicMock()
    cur.__enter__.return_value = cur      # with conn.cursor() as cur → 同一个 mock
    cur.__exit__.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cur

    @contextmanager
    def _fake():
        yield conn
    return _fake, cur


def test_sync_upserts_snapshot_and_seeds_defaults(monkeypatch):
    import sync_metadata
    fake_db, cur = _make_fake_db()
    monkeypatch.setattr(sync_metadata, 'get_db', fake_db)
    out = sync_metadata.sync_metadata_to_db()
    assert out['tools'] == 27
    assert out['rules_seeded'] == 27 * 4

    # 第一次 executemany = 快照 upsert（27 行，含分组元数据）
    snap = cur.executemany.call_args_list[0]
    rows = snap.args[1]
    by_name = {r[0]: r for r in rows}
    assert set(by_name) == {r[0] for r in rows}
    assert len(rows) == 27
    q = by_name['query_collection']
    assert q[1] == 'data-query' and q[2] == '数据查询' and q[5] == 'read'

    # DELETE 消失行：ANY(当前名单)
    del_sql = cur.execute.call_args_list[0].args[0]
    assert 'DELETE FROM ai_mcp_tools' in del_sql and 'ANY' in del_sql

    # 第二次 executemany = 矩阵播种（ON CONFLICT DO NOTHING）
    seed = cur.executemany.call_args_list[1]
    seed_sql = seed.args[0]
    assert 'ON CONFLICT (role, tool_name) DO NOTHING' in seed_sql
    seed_rows = {(r[0], r[1]): r[2] for r in seed.args[1]}
    assert seed_rows[('admin', 'run_python')] is True
    assert seed_rows[('developer', 'run_python')] is True
    assert seed_rows[('guest', 'run_python')] is False          # write 拒绝
    assert seed_rows[('guest', 'query_collection')] is True     # read 放行
    assert seed_rows[('kefu-guest', 'read_upload')] is True     # 白名单平移
    assert seed_rows[('kefu-guest', 'read_data_file')] is False
