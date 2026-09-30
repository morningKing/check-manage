"""单测：init_db.py 的 CONCURRENTLY 索引自愈逻辑（_create_concurrent_index）。

实盘验证过（真实中断一次 CONCURRENTLY 构建，产生真正 invalid 的索引，确认
_create_concurrent_index 能探测并自愈），这里补上可在 CI 里跑的 mock 版回归。
"""
import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import init_db


class TestCreateConcurrentIndex:
    def _mock_connect(self, fetchone_return):
        conn = MagicMock()
        cur = conn.cursor.return_value
        cur.fetchone.return_value = fetchone_return
        return conn, cur

    def test_creates_index_when_absent(self):
        """to_regclass 返回 NULL（索引不存在）：不查 indisvalid 的行，不 DROP，直接 CREATE。"""
        conn, cur = self._mock_connect(None)
        with patch('init_db.psycopg2.connect', return_value=conn):
            init_db._create_concurrent_index(
                'idx_test', 'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_test ON t (c)'
            )
        create_calls = [
            c for c in cur.execute.call_args_list
            if 'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_test ON' in str(c.args[0])
        ]
        assert len(create_calls) == 1
        drop_calls = [c for c in cur.execute.call_args_list if 'DROP INDEX CONCURRENTLY' in str(c.args[0])]
        assert drop_calls == []
        assert conn.autocommit is True

    def test_skips_drop_when_existing_index_is_valid(self):
        conn, cur = self._mock_connect((True,))
        with patch('init_db.psycopg2.connect', return_value=conn):
            init_db._create_concurrent_index(
                'idx_test', 'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_test ON t (c)'
            )
        drop_calls = [c for c in cur.execute.call_args_list if 'DROP INDEX CONCURRENTLY' in str(c.args[0])]
        assert drop_calls == []

    def test_drops_invalid_leftover_before_rebuilding(self):
        """indisvalid=False（上次 CONCURRENTLY 构建失败留下的残留）：先 DROP 再 CREATE。"""
        conn, cur = self._mock_connect((False,))
        with patch('init_db.psycopg2.connect', return_value=conn):
            init_db._create_concurrent_index(
                'idx_test', 'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_test ON t (c)'
            )
        calls = [str(c.args[0]) for c in cur.execute.call_args_list]
        drop_idx = next(i for i, s in enumerate(calls) if 'DROP INDEX CONCURRENTLY' in s)
        create_idx = next(
            i for i, s in enumerate(calls)
            if 'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_test ON' in s
        )
        assert drop_idx < create_idx

    def test_cleans_up_on_create_failure_and_reraises(self):
        conn, cur = self._mock_connect(None)

        def side_effect(sql, *args, **kwargs):
            if str(sql).startswith('CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_test ON'):
                raise Exception('disk full')

        cur.execute.side_effect = side_effect
        with patch('init_db.psycopg2.connect', return_value=conn):
            try:
                init_db._create_concurrent_index(
                    'idx_test', 'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_test ON t (c)'
                )
                assert False, 'should have raised'
            except Exception as e:
                assert 'disk full' in str(e)
        drop_calls = [c for c in cur.execute.call_args_list if 'DROP INDEX CONCURRENTLY' in str(c.args[0])]
        assert len(drop_calls) == 1

    def test_connection_always_closed(self):
        conn, _ = self._mock_connect(None)
        with patch('init_db.psycopg2.connect', return_value=conn):
            init_db._create_concurrent_index(
                'idx_test', 'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_test ON t (c)'
            )
        conn.close.assert_called_once()
