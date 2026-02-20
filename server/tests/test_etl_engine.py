"""
ETL 引擎单元测试

测试各步骤类型的纯逻辑行为，不依赖实际数据库。
"""

import sys
import os
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.etl_engine import (
    _resolve_path,
    _step_json_input,
    _step_field_mapping,
    _step_filter,
    _step_script,
    _step_save_to_collection,
    _execute_step,
    execute_task,
)


# ==================== _resolve_path ====================

class TestResolvePath:
    def test_nested_path(self):
        data = {'data': {'items': [1, 2, 3]}}
        assert _resolve_path(data, 'data.items') == [1, 2, 3]

    def test_empty_path(self):
        data = {'key': 'value'}
        assert _resolve_path(data, '') == data

    def test_none_path(self):
        data = {'key': 'value'}
        assert _resolve_path(data, None) == data

    def test_missing_key(self):
        data = {'a': {'b': 1}}
        assert _resolve_path(data, 'a.c') is None

    def test_non_dict_intermediate(self):
        data = {'a': 'string'}
        assert _resolve_path(data, 'a.b') is None

    def test_single_key(self):
        data = {'items': [1, 2]}
        assert _resolve_path(data, 'items') == [1, 2]


# ==================== json_input ====================

class TestJsonInput:
    def test_array_input(self):
        ctx = {'records': []}
        _step_json_input({'data': '[{"a": 1}, {"a": 2}]'}, ctx)
        assert len(ctx['records']) == 2
        assert ctx['records'][0]['a'] == 1

    def test_object_wraps_to_array(self):
        ctx = {'records': []}
        _step_json_input({'data': '{"name": "test"}'}, ctx)
        assert len(ctx['records']) == 1
        assert ctx['records'][0]['name'] == 'test'

    def test_invalid_json(self):
        ctx = {'records': []}
        with pytest.raises(ValueError, match='JSON'):
            _step_json_input({'data': '{invalid'}, ctx)

    def test_non_object_non_array(self):
        ctx = {'records': []}
        with pytest.raises(ValueError, match='数组或对象'):
            _step_json_input({'data': '"just a string"'}, ctx)

    def test_empty_default(self):
        ctx = {'records': []}
        _step_json_input({}, ctx)
        assert ctx['records'] == []


# ==================== field_mapping ====================

class TestFieldMapping:
    def test_basic_mapping(self):
        ctx = {'records': [{'name': 'a', 'type': 'b'}]}
        config = {
            'mappings': [
                {'source': 'name', 'target': 'caseName'},
                {'source': 'type', 'target': 'caseType'},
            ],
            'keepUnmapped': False,
        }
        _step_field_mapping(config, ctx)
        assert ctx['records'] == [{'caseName': 'a', 'caseType': 'b'}]

    def test_keep_unmapped(self):
        ctx = {'records': [{'name': 'a', 'extra': 'x'}]}
        config = {
            'mappings': [{'source': 'name', 'target': 'caseName'}],
            'keepUnmapped': True,
        }
        _step_field_mapping(config, ctx)
        rec = ctx['records'][0]
        assert rec['caseName'] == 'a'
        assert rec['extra'] == 'x'
        assert 'name' not in rec

    def test_discard_unmapped(self):
        ctx = {'records': [{'name': 'a', 'extra': 'x'}]}
        config = {
            'mappings': [{'source': 'name', 'target': 'caseName'}],
            'keepUnmapped': False,
        }
        _step_field_mapping(config, ctx)
        assert ctx['records'] == [{'caseName': 'a'}]

    def test_empty_mappings(self):
        original = [{'name': 'a'}]
        ctx = {'records': original}
        _step_field_mapping({'mappings': []}, ctx)
        assert ctx['records'] == original

    def test_source_not_in_record(self):
        ctx = {'records': [{'name': 'a'}]}
        config = {
            'mappings': [{'source': 'missing', 'target': 'out'}],
            'keepUnmapped': False,
        }
        _step_field_mapping(config, ctx)
        assert ctx['records'] == [{}]


# ==================== filter ====================

class TestFilter:
    def test_basic_filter(self):
        ctx = {'records': [
            {'name': 'a', 'score': 80},
            {'name': 'b', 'score': 40},
            {'name': 'c', 'score': 90},
        ]}
        _step_filter({'expression': 'record.get("score", 0) > 50'}, ctx)
        assert len(ctx['records']) == 2
        assert ctx['records'][0]['name'] == 'a'
        assert ctx['records'][1]['name'] == 'c'

    def test_empty_expression(self):
        original = [{'x': 1}]
        ctx = {'records': original}
        _step_filter({'expression': ''}, ctx)
        assert ctx['records'] == original

    def test_expression_error_skips_record(self):
        ctx = {'records': [{'name': 'a'}, {'name': 'b'}]}
        # 引用不存在的变量，表达式出错，记录会被跳过
        _step_filter({'expression': 'nonexistent_var > 0'}, ctx)
        assert ctx['records'] == []


# ==================== script ====================

class TestScript:
    def test_basic_transform(self):
        ctx = {'records': [{'name': 'a'}, {'name': 'b'}]}
        config = {'script': 'result = [{"name": r["name"].upper()} for r in records]'}
        _step_script(config, ctx)
        assert ctx['records'] == [{'name': 'A'}, {'name': 'B'}]

    def test_empty_script(self):
        original = [{'x': 1}]
        ctx = {'records': original}
        _step_script({'script': ''}, ctx)
        assert ctx['records'] == original

    def test_script_result_not_list(self):
        ctx = {'records': []}
        config = {'script': 'result = "not a list"'}
        with pytest.raises(ValueError, match='列表'):
            _step_script(config, ctx)


# ==================== save_to_collection ====================

class TestSaveToCollection:
    def test_dry_run_counts_only(self):
        mock_cur = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        ctx = {
            'records': [{'name': 'a'}, {'name': 'b'}],
            'total': 0, 'success': 0, 'error': 0, 'errors': [],
        }
        config = {'collection': 'test', 'mode': 'insert'}
        _step_save_to_collection(config, ctx, mock_conn, dry_run=True)
        assert ctx['success'] == 2
        assert ctx['total'] == 2
        # dry_run 不应执行 INSERT/UPDATE
        mock_cur.execute.assert_not_called()

    def test_insert_mode(self):
        mock_cur = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        ctx = {
            'records': [{'name': 'a'}],
            'total': 0, 'success': 0, 'error': 0, 'errors': [],
        }
        config = {'collection': 'test', 'mode': 'insert'}
        _step_save_to_collection(config, ctx, mock_conn, dry_run=False)
        assert ctx['success'] == 1
        assert mock_cur.execute.call_count == 1
        sql = mock_cur.execute.call_args[0][0]
        assert 'INSERT INTO dynamic_data' in sql

    def test_upsert_existing(self):
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = ('existing-id',)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        ctx = {
            'records': [{'name': 'a'}],
            'total': 0, 'success': 0, 'error': 0, 'errors': [],
        }
        config = {'collection': 'test', 'mode': 'upsert', 'matchField': 'name'}
        _step_save_to_collection(config, ctx, mock_conn, dry_run=False)
        assert ctx['success'] == 1
        # Should have SELECT then UPDATE
        calls = mock_cur.execute.call_args_list
        assert any('SELECT' in str(c) for c in calls)
        assert any('UPDATE' in str(c) for c in calls)

    def test_upsert_new(self):
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        ctx = {
            'records': [{'name': 'a'}],
            'total': 0, 'success': 0, 'error': 0, 'errors': [],
        }
        config = {'collection': 'test', 'mode': 'upsert', 'matchField': 'name'}
        _step_save_to_collection(config, ctx, mock_conn, dry_run=False)
        assert ctx['success'] == 1
        calls = mock_cur.execute.call_args_list
        assert any('INSERT' in str(c) for c in calls)

    def test_empty_collection_error(self):
        ctx = {
            'records': [{'name': 'a'}],
            'total': 0, 'success': 0, 'error': 0, 'errors': [],
        }
        with pytest.raises(ValueError, match='目标集合'):
            _step_save_to_collection({'collection': '', 'mode': 'insert'}, ctx, MagicMock(), False)

    def test_empty_records_noop(self):
        mock_conn = MagicMock()
        ctx = {
            'records': [],
            'total': 0, 'success': 0, 'error': 0, 'errors': [],
        }
        _step_save_to_collection({'collection': 'test', 'mode': 'insert'}, ctx, mock_conn, False)
        mock_conn.cursor.assert_not_called()


# ==================== _execute_step ====================

class TestExecuteStep:
    def test_unknown_type(self):
        ctx = {'records': []}
        with pytest.raises(ValueError, match='未知的步骤类型'):
            _execute_step('unknown_type', {}, ctx, None, False)


# ==================== execute_task ====================

class TestExecuteTask:
    def test_empty_steps(self):
        ctx = execute_task({'steps': []}, None)
        assert ctx['records'] == []
        assert ctx['step_results'] == []

    def test_no_steps_key(self):
        ctx = execute_task({}, None)
        assert ctx['records'] == []

    def test_on_error_stop(self):
        """错误步骤 onError=stop 时应阻止后续步骤"""
        task = {
            'steps': [
                {'id': 's1', 'name': 'bad', 'type': 'unknown_bad', 'config': {}, 'onError': 'stop'},
                {'id': 's2', 'name': 'good', 'type': 'json_input', 'config': {'data': '[{"a":1}]'}, 'onError': 'stop'},
            ]
        }
        ctx = execute_task(task, None)
        assert len(ctx['step_results']) == 1
        assert ctx['step_results'][0]['status'] == 'error'

    def test_on_error_skip(self):
        """错误步骤 onError=skip 时应继续执行后续步骤"""
        task = {
            'steps': [
                {'id': 's1', 'name': 'bad', 'type': 'unknown_bad', 'config': {}, 'onError': 'skip'},
                {'id': 's2', 'name': 'good', 'type': 'json_input', 'config': {'data': '[{"a":1}]'}, 'onError': 'stop'},
            ]
        }
        ctx = execute_task(task, None)
        assert len(ctx['step_results']) == 2
        assert ctx['step_results'][0]['status'] == 'error'
        assert ctx['step_results'][1]['status'] == 'success'
        assert len(ctx['records']) == 1

    def test_pipeline_flow(self):
        """完整管道流转: json_input → field_mapping → filter"""
        task = {
            'steps': [
                {
                    'id': 's1', 'name': 'input', 'type': 'json_input',
                    'config': {'data': '[{"name":"a","score":80},{"name":"b","score":30}]'},
                    'onError': 'stop',
                },
                {
                    'id': 's2', 'name': 'map', 'type': 'field_mapping',
                    'config': {
                        'mappings': [{'source': 'name', 'target': 'caseName'}],
                        'keepUnmapped': True,
                    },
                    'onError': 'stop',
                },
                {
                    'id': 's3', 'name': 'filter', 'type': 'filter',
                    'config': {'expression': 'record.get("score", 0) > 50'},
                    'onError': 'stop',
                },
            ]
        }
        ctx = execute_task(task, None)
        assert len(ctx['step_results']) == 3
        assert all(r['status'] == 'success' for r in ctx['step_results'])
        assert len(ctx['records']) == 1
        assert ctx['records'][0]['caseName'] == 'a'
        assert ctx['records'][0]['score'] == 80
