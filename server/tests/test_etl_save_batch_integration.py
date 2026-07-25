"""集成测试：ETL save_to_collection 分批写库 —— 走真实 DB，不 mock 游标。

背景：insert 走 execute_values（内部要用 cur.mogrify，纯 MagicMock 配不出正确行为）；
upsert/update 的批量匹配查询、批次失败 rollback、取消后已写批次保留，这几个行为
都是真实 SQL 事务语义，mock 出来的置信度低于直接连测试库验证。

同 test_open_api_batch_integration.py / test_dynamic_keyword_search.py 模式：
测试数据用 `_t_` 前缀自建自清理，走 get_db()（不 mock）。
"""
import os
import sys
from datetime import datetime, timezone

import psycopg2.extras
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from db import get_db  # noqa: E402
from utils.etl_engine import _step_save_to_collection  # noqa: E402

COLLECTION = '_t_etl_batch_save'


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM dynamic_data WHERE collection = %s', (COLLECTION,))


def _fetch_all():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT id, data FROM dynamic_data WHERE collection = %s ORDER BY id', (COLLECTION,))
        return cur.fetchall()


class TestInsertBatch:
    def test_batch_insert_writes_all_records(self):
        with get_db() as conn:
            ctx = {
                'records': [{'name': f'r{i}'} for i in range(5)],
                'total': 0, 'success': 0, 'error': 0, 'errors': [], 'cancelled': False,
            }
            _step_save_to_collection(
                {'collection': COLLECTION, 'mode': 'insert'}, ctx, conn, dry_run=False,
            )
            assert ctx['success'] == 5
            assert ctx['error'] == 0
        rows = _fetch_all()
        assert len(rows) == 5
        assert {r[1]['name'] for r in rows} == {f'r{i}' for i in range(5)}


class TestUpsertBatch:
    def test_upsert_mixes_existing_update_and_new_insert_in_one_batch(self):
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'INSERT INTO dynamic_data (id, collection, data, created_at) VALUES (%s, %s, %s, %s)',
                ('_t_existing_1', COLLECTION, psycopg2.extras.Json({'code': 'A', 'name': '旧名字'}),
                 datetime.now(timezone.utc)),
            )
            conn.commit()

            ctx = {
                'records': [
                    {'code': 'A', 'name': '新名字'},   # 命中已存在记录，走 UPDATE
                    {'code': 'B', 'name': '全新记录'},  # 不存在，走 INSERT
                ],
                'total': 0, 'success': 0, 'error': 0, 'errors': [], 'cancelled': False,
            }
            _step_save_to_collection(
                {'collection': COLLECTION, 'mode': 'upsert', 'matchField': 'code'},
                ctx, conn, dry_run=False,
            )
            assert ctx['success'] == 2
            assert ctx['error'] == 0
        rows = {r[0]: r[1] for r in _fetch_all()}
        assert rows['_t_existing_1']['name'] == '新名字'
        assert any(d['code'] == 'B' and d['name'] == '全新记录' for d in rows.values())


class TestUpdateBatch:
    def test_update_mode_skips_unmatched_records(self):
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'INSERT INTO dynamic_data (id, collection, data, created_at) VALUES (%s, %s, %s, %s)',
                ('_t_existing_2', COLLECTION, psycopg2.extras.Json({'code': 'X', 'name': '旧'}),
                 datetime.now(timezone.utc)),
            )
            conn.commit()

            ctx = {
                'records': [
                    {'code': 'X', 'name': '新'},       # 命中，更新
                    {'code': 'NOPE', 'name': '不存在'},  # 不命中，计为失败
                ],
                'total': 0, 'success': 0, 'error': 0, 'errors': [], 'cancelled': False,
            }
            _step_save_to_collection(
                {'collection': COLLECTION, 'mode': 'update', 'matchField': 'code'},
                ctx, conn, dry_run=False,
            )
            assert ctx['success'] == 1
            assert ctx['error'] == 1
        rows = _fetch_all()
        assert len(rows) == 1
        assert rows[0][1]['name'] == '新'


class TestProgressAndCancel:
    def test_progress_cb_reports_running_totals_across_batches(self):
        """SAVE_BATCH_SIZE=1000，构造 3 条即可验证单批场景的回调；多批场景在
        test_etl_engine.py 的纯逻辑测试里已经用 dry_run 验证过回调调用形状，
        这里重点验证真实写库路径下 progress_cb 同样被正确调用。"""
        with get_db() as conn:
            ctx = {
                'records': [{'name': f'r{i}'} for i in range(3)],
                'total': 0, 'success': 0, 'error': 0, 'errors': [], 'cancelled': False,
            }
            calls = []
            _step_save_to_collection(
                {'collection': COLLECTION, 'mode': 'insert'}, ctx, conn, dry_run=False,
                progress_cb=lambda cur, tot: calls.append((cur, tot)),
            )
            assert calls == [(3, 3)]

    def test_cancel_after_first_batch_keeps_already_written_data(self, monkeypatch):
        """把 SAVE_BATCH_SIZE 临时改小，制造"多批"场景：第一批写完后取消，
        断言第一批已经落库、第二批没有被处理。"""
        import utils.etl_engine as etl_engine
        monkeypatch.setattr(etl_engine, 'SAVE_BATCH_SIZE', 2)

        with get_db() as conn:
            ctx = {
                'records': [{'name': f'r{i}'} for i in range(4)],  # 2 批，每批 2 条
                'total': 0, 'success': 0, 'error': 0, 'errors': [], 'cancelled': False,
            }
            call_count = {'n': 0}

            def _cancel_after_first_batch():
                call_count['n'] += 1
                return call_count['n'] >= 1  # 第一批写完、检查取消时就返回 True

            _step_save_to_collection(
                {'collection': COLLECTION, 'mode': 'insert'}, ctx, conn, dry_run=False,
                cancel_check=_cancel_after_first_batch,
            )
            assert ctx['cancelled'] is True
            assert ctx['success'] == 2  # 只有第一批的 2 条

        rows = _fetch_all()
        assert len(rows) == 2  # 第一批已提交，数据库里能查到；第二批从未执行


class TestBatchFailureRollback:
    def test_batch_insert_failure_rolls_back_only_that_batch(self, monkeypatch):
        """人为让 execute_values 在处理某一批时抛错，断言：(a) 这一批的记录
        没有落库；(b) 连接在异常后仍然可用（能继续下一批，或至少调用方能正常
        commit/rollback，不会因为"事务已中止"而级联失败）；(c) errors 里记录了
        失败原因。"""
        import utils.etl_engine as etl_engine

        original_write_batch = etl_engine._write_batch
        call_count = {'n': 0}

        def _flaky_write_batch(cur, collection, mode, match_field, batch, now):
            call_count['n'] += 1
            if call_count['n'] == 1:
                raise Exception('模拟批量写入失败')
            return original_write_batch(cur, collection, mode, match_field, batch, now)

        monkeypatch.setattr(etl_engine, '_write_batch', _flaky_write_batch)
        monkeypatch.setattr(etl_engine, 'SAVE_BATCH_SIZE', 2)

        with get_db() as conn:
            ctx = {
                'records': [{'name': f'r{i}'} for i in range(4)],  # 2 批
                'total': 0, 'success': 0, 'error': 0, 'errors': [], 'cancelled': False,
            }
            _step_save_to_collection(
                {'collection': COLLECTION, 'mode': 'insert'}, ctx, conn, dry_run=False,
            )
            # 第一批失败（计入 error），第二批正常（计入 success）
            assert ctx['success'] == 2
            assert ctx['error'] == 2
            assert any('模拟批量写入失败' in e for e in ctx['errors'])

        rows = _fetch_all()
        # 只有第二批（未失败的那批）的 2 条记录落库
        assert len(rows) == 2
