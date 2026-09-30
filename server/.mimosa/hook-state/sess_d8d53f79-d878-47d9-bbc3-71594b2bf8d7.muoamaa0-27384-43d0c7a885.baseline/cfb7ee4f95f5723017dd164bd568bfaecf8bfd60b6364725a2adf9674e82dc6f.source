"""
statusBadge 超时兜底定时任务测试
"""
import sys
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _make_mock_db(mock_conn):
    @contextmanager
    def fake_get_db():
        yield mock_conn
    return fake_get_db


class TestStatusBadgeTimeoutTick:
    def _find_sql_calls(self, mock_cursor, needle):
        return [
            c for c in mock_cursor.execute.call_args_list
            if c.args and needle in str(c.args[0])
        ]

    def test_timed_out_record_gets_written_to_timeout_value(self, mock_conn, mock_cursor):
        """非终态、超过 timeoutSec 未变化的记录应被写成 timeoutValue"""
        from utils import status_badge_timeout_scheduler as mod
        with patch('utils.status_badge_timeout_scheduler.get_db', _make_mock_db(mock_conn)):
            old_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            mock_cursor.fetchall.side_effect = [
                [{
                    'id': 'page-demo',
                    'fields': [{
                        'fieldName': 'status',
                        'controlType': 'statusBadge',
                        'statusBadgeConfig': {
                            'timeoutSec': 60,
                            'timeoutValue': 'failed',
                            'options': [
                                {'value': 'processing', 'label': '处理中'},
                                {'value': 'failed', 'label': '失败', 'terminal': True},
                            ],
                        },
                    }],
                }],
                [{'id': 'r1', 'branch_id': 'main', 'data': {
                    'status': 'processing',
                    '_statusBadge_status_changedAt': old_ts,
                }}],
            ]
            mod._tick()

            update_calls = self._find_sql_calls(mock_cursor, 'UPDATE dynamic_data')
            assert len(update_calls) == 1
            params = update_calls[0].args[1]  # (data, collection, id, branch_id)
            written_data = params[0].adapted
            assert written_data['status'] == 'failed'
            assert '_statusBadge_status_changedAt' in written_data
            # UPDATE targets the same record/branch the SELECT found it in
            assert params[1:] == ('demo', 'r1', 'main')

    def test_terminal_record_is_skipped(self, mock_conn, mock_cursor):
        """已经是终态的记录不应被扫描到（SQL 里 NOT (... = ANY(terminal_values)) 条件）"""
        from utils import status_badge_timeout_scheduler as mod
        with patch('utils.status_badge_timeout_scheduler.get_db', _make_mock_db(mock_conn)):
            mock_cursor.fetchall.side_effect = [
                [{
                    'id': 'page-demo',
                    'fields': [{
                        'fieldName': 'status',
                        'controlType': 'statusBadge',
                        'statusBadgeConfig': {
                            'timeoutSec': 60,
                            'timeoutValue': 'failed',
                            'options': [
                                {'value': 'processing', 'label': '处理中'},
                                {'value': 'failed', 'label': '失败', 'terminal': True},
                            ],
                        },
                    }],
                }],
                [],  # 第二条 SELECT（超时记录扫描）返回空——模拟 SQL 条件已经排除了终态记录
            ]
            mod._tick()
            update_calls = self._find_sql_calls(mock_cursor, 'UPDATE dynamic_data')
            assert len(update_calls) == 0

    def test_field_without_timeout_sec_is_skipped(self, mock_conn, mock_cursor):
        """没配置 timeoutSec 的 statusBadge 字段完全不参与扫描（不产生任何扫描 SELECT/UPDATE）"""
        from utils import status_badge_timeout_scheduler as mod
        with patch('utils.status_badge_timeout_scheduler.get_db', _make_mock_db(mock_conn)):
            mock_cursor.fetchall.return_value = [{
                'id': 'page-demo',
                'fields': [{
                    'fieldName': 'status',
                    'controlType': 'statusBadge',
                    'statusBadgeConfig': {'options': []},  # 没有 timeoutSec/timeoutValue
                }],
            }]
            mod._tick()
            select_scan_calls = [
                c for c in mock_cursor.execute.call_args_list
                if 'FROM dynamic_data' in str(c.args[0])
            ]
            update_calls = self._find_sql_calls(mock_cursor, 'UPDATE dynamic_data')
            assert len(select_scan_calls) == 0
            assert len(update_calls) == 0
