"""单测：utils/field_indexes.py 的资格判定与 field_indexes 表同步逻辑。"""
from unittest.mock import MagicMock

from utils.field_indexes import (
    INDEXABLE_TYPES,
    sql_literal,
    index_name_for,
    indexed_field_names,
    sync_field_indexes,
    mark_all_dropping,
)


class TestSqlLiteral:
    def test_wraps_in_quotes(self):
        assert sql_literal('status') == "'status'"

    def test_escapes_single_quotes(self):
        assert sql_literal("o'brien") == "'o''brien'"


class TestIndexNameFor:
    def test_deterministic(self):
        assert index_name_for('devices', 'status') == index_name_for('devices', 'status')

    def test_differs_by_collection_and_field(self):
        a = index_name_for('devices', 'status')
        b = index_name_for('orders', 'status')
        c = index_name_for('devices', 'priority')
        assert len({a, b, c}) == 3

    def test_is_valid_short_postgres_identifier(self):
        name = index_name_for('一个很长的中文集合名字测试测试测试', '一个很长的中文字段名字测试测试测试')
        assert len(name) < 63
        assert name.isascii()
        assert name.startswith('idx_dyn_fld_')


class TestIndexedFieldNames:
    def test_only_indexed_and_indexable_type_fields(self):
        fields = [
            {'fieldName': 'status', 'controlType': 'select', 'indexed': True},
            {'fieldName': 'note', 'controlType': 'textarea', 'indexed': True},  # 类型不可索引
            {'fieldName': 'priority', 'controlType': 'select', 'indexed': False},  # 没勾选
            {'fieldName': 'ref', 'controlType': 'reference', 'indexed': True},  # 类型不可索引
        ]
        assert indexed_field_names(fields) == {'status'}

    def test_empty_fields(self):
        assert indexed_field_names([]) == set()
        assert indexed_field_names(None) == set()

    def test_all_documented_indexable_types(self):
        assert INDEXABLE_TYPES == {
            'text', 'number', 'select', 'radio', 'date', 'datetime',
            'autoSequence', 'autoTimestamp', 'compositeText', 'statusBadge', 'checkbox',
        }


class TestSyncFieldIndexes:
    def _mock_cursor(self, existing_rows):
        cur = MagicMock()
        cur.fetchall.return_value = existing_rows
        return cur

    def test_inserts_pending_row_for_newly_indexed_field(self):
        cur = self._mock_cursor([])
        fields = [{'fieldName': 'status', 'controlType': 'select', 'indexed': True}]
        sync_field_indexes(cur, 'devices', fields)

        insert_calls = [c for c in cur.execute.call_args_list if 'INSERT INTO field_indexes' in str(c.args[0])]
        assert len(insert_calls) == 1
        args = insert_calls[0].args[1]
        assert args[0] == 'devices'
        assert args[1] == 'status'
        assert args[3] == 'pending'

    def test_does_not_reinsert_already_tracked_field(self):
        cur = self._mock_cursor([('status', 'ready')])
        fields = [{'fieldName': 'status', 'controlType': 'select', 'indexed': True}]
        sync_field_indexes(cur, 'devices', fields)

        insert_calls = [c for c in cur.execute.call_args_list if 'INSERT INTO field_indexes' in str(c.args[0])]
        assert insert_calls == []

    def test_marks_unindexed_field_as_dropping(self):
        cur = self._mock_cursor([('status', 'ready')])
        fields = [{'fieldName': 'status', 'controlType': 'select', 'indexed': False}]
        sync_field_indexes(cur, 'devices', fields)

        update_calls = [c for c in cur.execute.call_args_list if "status = 'dropping'" in str(c.args[0])]
        assert len(update_calls) == 1
        assert update_calls[0].args[1] == ('devices', 'status')

    def test_marks_removed_field_as_dropping(self):
        """字段被整个删除（不在新 fields 列表里）也要清理索引。"""
        cur = self._mock_cursor([('status', 'ready')])
        sync_field_indexes(cur, 'devices', [])

        update_calls = [c for c in cur.execute.call_args_list if "status = 'dropping'" in str(c.args[0])]
        assert len(update_calls) == 1

    def test_does_not_redundantly_mark_already_dropping(self):
        cur = self._mock_cursor([('status', 'dropping')])
        sync_field_indexes(cur, 'devices', [])

        update_calls = [c for c in cur.execute.call_args_list if "status = 'dropping'" in str(c.args[0])]
        assert update_calls == []

    def test_ignores_non_indexable_control_type_even_if_indexed_true(self):
        cur = self._mock_cursor([])
        fields = [{'fieldName': 'attachments', 'controlType': 'file', 'indexed': True}]
        sync_field_indexes(cur, 'devices', fields)

        insert_calls = [c for c in cur.execute.call_args_list if 'INSERT INTO field_indexes' in str(c.args[0])]
        assert insert_calls == []


class TestMarkAllDropping:
    def test_marks_all_non_dropping_rows(self):
        cur = MagicMock()
        mark_all_dropping(cur, 'devices')
        cur.execute.assert_called_once()
        sql, params = cur.execute.call_args.args
        assert "status = 'dropping'" in sql
        assert params == ('devices',)
