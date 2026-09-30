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


# ---------------------------------------------------------------------------
# Critical regression: set_data_file_id UPSERT (spec §2.5 idempotency contract)
#
# Bug surface: outputs/ files are .gitignored → git_changes never sees them →
# record_session_files never INSERTs a row → admin import via extra_whitelist
# succeeds (save_workspace_file writes a data_files row), then set_data_file_id
# fires UPDATE-only on a non-existent row → silent no-op → no linkage. Next
# admin import of the same path repeats the whole thing → a SECOND data_files
# row appears (duplication breaks the §2.5 「下次 DB 白名单也命中该路径」contract).
#
# Two complementary tests below:
#   (a) real-impl: drives the REAL set_data_file_id (no mock) against a fake
#       DB that simulates ai_chat_session_files' UNIQUE(session_id, path) +
#       CHECK(status IN ('added','modified')) semantics. With UPDATE-only this
#       RED-fails (row never INSERTed); with UPSERT GREEN (exactly one row).
#   (b) mock-flow : full import_recorded_files flow's idempotency sentinel —
#       a mocked set_data_file_id that records into recorded_rows proves the
#       upper flow re-uses the existing data_file_id on 2nd import instead of
#       calling save_workspace_file again. (Locks the integration contract; on
#       its own it can't distinguish UPDATE from UPSERT, hence (a).)
# ---------------------------------------------------------------------------

def test_set_data_file_id_upserts_when_no_pre_row(monkeypatch):
    """Real-impl RED/GREEN: set_data_file_id must UPSERT so the row exists
    after an admin import of an outputs/ file that never had a prior
    ai_chat_session_files row. Drives the REAL implementation through a fake
    connection that interprets only the SQL shapes set_data_file_id issues and
    honors UNIQUE (session_id, path) + CHECK(status IN ('added','modified'))."""
    # Fake ai_chat_session_files table keyed by (session_id, path).
    rows = {}

    class FakeCursor:
        def execute(self, sql, params=None):
            sql = sql.strip()
            self.rowcount = 0
            if sql.startswith('UPDATE ai_chat_session_files SET data_file_id'):
                # Old UPDATE-only path.
                df_id, sid, p = params
                key = (sid, p)
                if key in rows:
                    rows[key]['data_file_id'] = df_id
                    self.rowcount = 1
            elif sql.startswith('INSERT INTO ai_chat_session_files'):
                # UPSERT path.
                sid, p, df_id = params
                key = (sid, p)
                self.rowcount = 1
                if key in rows:
                    # ON CONFLICT (session_id, path) DO UPDATE — only
                    # data_file_id + last_seen_at move; status is preserved.
                    rows[key]['data_file_id'] = df_id
                else:
                    rows[key] = {'status': 'added', 'data_file_id': df_id}
            # any other SQL the function might issue — silently no-op.

        def close(self):
            pass

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def commit(self):
            pass

        def close(self):
            pass

    from contextlib import contextmanager

    @contextmanager
    def _fake_get_db():
        yield FakeConn()

    monkeypatch.setattr('db.get_db', _fake_get_db)

    from utils.workspace_changes import set_data_file_id

    # Precondition: outputs/ file has no recorded row (git_changes never saw it
    # because .gitignore shields outputs/).
    assert ('s', 'outputs/report.md') not in rows

    # The admin import of this path. UPSERT's INSERT branch must create the row.
    # With old UPDATE-only implementation: rowcount=0, rows stays empty.
    set_data_file_id('s', 'outputs/report.md', 'df-1')

    rec = rows.get(('s', 'outputs/report.md'))
    assert rec is not None, (
        'set_data_file_id must UPSERT — INSERT the missing row for an outputs/ '
        'file; old UPDATE-only found zero rows and silently no-op\'d, breaking '
        'spec §2.5 idempotency (next admin import would duplicate the data_files row)'
    )
    assert rec['data_file_id'] == 'df-1'
    assert rec['status'] == 'added'  # satisfies CHECK (status IN ('added','modified'))

    # Conflict branch: re-linking must keep exactly one row (no duplicates),
    # and only data_file_id should advance — status stays 'added'.
    set_data_file_id('s', 'outputs/report.md', 'df-2')
    assert len(rows) == 1
    assert rows[('s', 'outputs/report.md')]['data_file_id'] == 'df-2'
    assert rows[('s', 'outputs/report.md')]['status'] == 'added'


def test_import_idempotent_when_no_pre_row(monkeypatch, tmp_path):
    """Mock-flow sentinel for the same regression as above. Walks the full
    import_recorded_files flow twice with a mocked set_data_file_id that
    records rows (mimicking the FIXED UPSERT). Proves the upper layer reaches
    the idempotent 'existing' branch on the second call — save_workspace_file
    called exactly once across both calls."""
    ws = str(tmp_path)
    os.makedirs(os.path.join(ws, 'outputs'), exist_ok=True)
    with open(os.path.join(ws, 'outputs', 'report.md'), 'w') as f:
        f.write('report content')

    # Mocked DB state: path -> row dict, None when no row yet.
    recorded_rows = {}

    def fake_get_recorded_path(sid, p):
        return recorded_rows.get(p)

    def fake_set_data_file_id(sid, p, fid):
        # Mirrors the UPSERT behavior: always records the row with dataFileId.
        recorded_rows[p] = {'path': p, 'status': 'added', 'dataFileId': fid}

    # save_workspace_file returns a fresh meta each call (mimics DB INSERT into
    # data_files — the bug surfaces as a SECOND call to this).
    meta_seq = [{'id': f'df-{i}', 'name': 'report.md', 'size': 14}
                for i in range(1, 5)]
    save_idx = [0]

    def fake_save_workspace_file(abs_path, rel_path, uploaded_by=None):
        meta = meta_seq[save_idx[0]]
        save_idx[0] += 1
        return meta, None

    def fake_data_file_meta(fid):
        # Idempotent reuse: the 'existing' branch looks up by id.
        for v in recorded_rows.values():
            if v['dataFileId'] == fid:
                return {'id': fid, 'name': 'report.md', 'size': 14}
        return None

    monkeypatch.setattr('utils.session_file_import.get_recorded_path',
                       fake_get_recorded_path)
    monkeypatch.setattr('utils.session_file_import.set_data_file_id',
                       fake_set_data_file_id)
    monkeypatch.setattr('routes.data_files.save_workspace_file',
                       fake_save_workspace_file)
    monkeypatch.setattr('routes.data_files.data_file_meta', fake_data_file_meta)

    # First import: row absent → extra_whitelist covers → save fires once →
    # set_data_file_id mock records the row.
    r1 = import_recorded_files('s', ws, ['outputs/report.md'],
                                uploaded_by='o1',
                                extra_whitelist={'outputs/report.md'})
    assert r1[0]['status'] == 'imported'
    assert r1[0]['file']['id'] == 'df-1'
    assert save_idx[0] == 1  # save called exactly once

    # Second import: get_recorded_path now returns the row → take 'existing'
    # branch → save_workspace_file MUST NOT be called a second time.
    r2 = import_recorded_files('s', ws, ['outputs/report.md'],
                                uploaded_by='o1',
                                extra_whitelist={'outputs/report.md'})
    assert r2[0]['status'] == 'existing'
    assert r2[0]['file']['id'] == 'df-1'  # same id, no duplicate row
    assert save_idx[0] == 1  # save_workspace_file NOT called again

    # Linkage recorded: row carries the data_file_id of the first import.
    assert recorded_rows['outputs/report.md']['dataFileId'] == 'df-1'