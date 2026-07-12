"""字段索引后台任务测试（utils/field_index_scheduler.py）。"""
import sys
import os
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _make_mock_db(mock_conn):
    @contextmanager
    def fake_get_db():
        yield mock_conn
    return fake_get_db


class TestFieldIndexTick:
    def _find_sql_calls(self, mock_cursor, needle):
        return [
            c for c in mock_cursor.execute.call_args_list
            if c.args and needle in str(c.args[0])
        ]

    def test_pending_row_gets_built_and_marked_ready(self, mock_conn, mock_cursor):
        from utils import field_index_scheduler as mod
        mock_cursor.fetchall.side_effect = [
            [('devices', 'status', 'idx_dyn_fld_abc123')],  # pending batch
            [],  # dropping batch
        ]
        build_conn = MagicMock()
        with patch('utils.field_index_scheduler.get_db', _make_mock_db(mock_conn)), \
             patch('utils.field_index_scheduler.psycopg2.connect', return_value=build_conn):
            mod._tick()

        # bookkeeping: pending -> building
        building_calls = self._find_sql_calls(mock_cursor, "status = 'building'")
        assert len(building_calls) == 1
        assert building_calls[0].args[1] == ('devices', 'status')

        # the CONCURRENTLY build ran on the separate autocommit connection
        build_cur = build_conn.cursor.return_value
        create_calls = [c for c in build_cur.execute.call_args_list if 'CREATE INDEX CONCURRENTLY' in str(c.args[0])]
        assert len(create_calls) == 1
        assert 'idx_dyn_fld_abc123' in create_calls[0].args[0]
        assert "data->>'status'" in create_calls[0].args[0]
        assert "collection = 'devices'" in create_calls[0].args[0]
        assert build_conn.autocommit is True

        # bookkeeping: building -> ready
        ready_calls = self._find_sql_calls(mock_cursor, "status = 'ready'")
        assert len(ready_calls) == 1
        assert ready_calls[0].args[1] == ('devices', 'status')

    def test_build_failure_marks_row_failed_with_error_and_cleans_up_invalid_index(self, mock_conn, mock_cursor):
        from utils import field_index_scheduler as mod
        mock_cursor.fetchall.side_effect = [
            [('devices', 'status', 'idx_dyn_fld_abc123')],
            [],
        ]
        build_conn = MagicMock()
        build_cur = build_conn.cursor.return_value
        build_cur.execute.side_effect = [Exception('disk full'), None]  # CREATE fails, cleanup DROP succeeds
        with patch('utils.field_index_scheduler.get_db', _make_mock_db(mock_conn)), \
             patch('utils.field_index_scheduler.psycopg2.connect', return_value=build_conn):
            mod._tick()

        failed_calls = self._find_sql_calls(mock_cursor, "status = 'failed'")
        assert len(failed_calls) == 1
        assert failed_calls[0].args[1][0] == 'disk full'

        drop_calls = [c for c in build_cur.execute.call_args_list if 'DROP INDEX CONCURRENTLY' in str(c.args[0])]
        assert len(drop_calls) == 1

    def test_dropping_row_gets_dropped_and_row_deleted(self, mock_conn, mock_cursor):
        from utils import field_index_scheduler as mod
        mock_cursor.fetchall.side_effect = [
            [],  # pending batch
            [('devices', 'status', 'idx_dyn_fld_abc123')],  # dropping batch
        ]
        build_conn = MagicMock()
        with patch('utils.field_index_scheduler.get_db', _make_mock_db(mock_conn)), \
             patch('utils.field_index_scheduler.psycopg2.connect', return_value=build_conn):
            mod._tick()

        build_cur = build_conn.cursor.return_value
        drop_calls = [c for c in build_cur.execute.call_args_list if 'DROP INDEX CONCURRENTLY' in str(c.args[0])]
        assert len(drop_calls) == 1
        assert 'idx_dyn_fld_abc123' in drop_calls[0].args[0]

        delete_calls = self._find_sql_calls(mock_cursor, 'DELETE FROM field_indexes')
        assert len(delete_calls) == 1
        assert delete_calls[0].args[1] == ('devices', 'status')

    def test_no_pending_or_dropping_rows_is_a_noop(self, mock_conn, mock_cursor):
        from utils import field_index_scheduler as mod
        mock_cursor.fetchall.side_effect = [[], []]
        with patch('utils.field_index_scheduler.get_db', _make_mock_db(mock_conn)), \
             patch('utils.field_index_scheduler.psycopg2.connect') as mock_connect:
            mod._tick()
        mock_connect.assert_not_called()


class TestSafeTick:
    def test_exceptions_are_swallowed(self):
        from utils import field_index_scheduler as mod
        with patch('utils.field_index_scheduler._tick', side_effect=RuntimeError('boom')):
            mod._safe_tick()  # 不应抛出
