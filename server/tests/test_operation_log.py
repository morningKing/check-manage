"""
操作日志工具函数单元测试

测试 log_operation、pick_display_name、get_field_label_map。
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class TestPickDisplayName:
    def test_with_fields_priority(self):
        from utils.operation_log import pick_display_name

        fields = [
            {'fieldName': 'desc', 'controlType': 'textarea', 'order': 2},
            {'fieldName': 'title', 'controlType': 'text', 'order': 1},
        ]
        data = {'title': '标题', 'desc': '描述'}
        assert pick_display_name(data, fields) == '标题'

    def test_with_fields_skip_non_text(self):
        from utils.operation_log import pick_display_name

        fields = [
            {'fieldName': 'count', 'controlType': 'number', 'order': 1},
            {'fieldName': 'note', 'controlType': 'textarea', 'order': 2},
        ]
        data = {'count': 42, 'note': '备注'}
        assert pick_display_name(data, fields) == '备注'

    def test_fallback_to_name_key(self):
        from utils.operation_log import pick_display_name

        data = {'name': '测试名称', 'other': 'value'}
        assert pick_display_name(data) == '测试名称'

    def test_fallback_to_caseName(self):
        from utils.operation_log import pick_display_name

        data = {'caseName': '用例名称'}
        assert pick_display_name(data) == '用例名称'

    def test_fallback_to_planName(self):
        from utils.operation_log import pick_display_name

        data = {'planName': '计划名称'}
        assert pick_display_name(data) == '计划名称'

    def test_fallback_to_specialName(self):
        from utils.operation_log import pick_display_name

        data = {'specialName': '专项名称'}
        assert pick_display_name(data) == '专项名称'

    def test_returns_none_when_no_match(self):
        from utils.operation_log import pick_display_name

        data = {'id': '123', 'count': 42}
        assert pick_display_name(data) is None

    def test_empty_data(self):
        from utils.operation_log import pick_display_name

        assert pick_display_name({}) is None

    def test_non_string_values_skipped(self):
        from utils.operation_log import pick_display_name

        data = {'name': 123}
        assert pick_display_name(data) is None

    def test_with_autoSequence_field(self):
        from utils.operation_log import pick_display_name

        fields = [
            {'fieldName': 'seqNo', 'controlType': 'autoSequence', 'order': 1},
            {'fieldName': 'desc', 'controlType': 'textarea', 'order': 2},
        ]
        data = {'seqNo': 'IC-001', 'desc': '描述'}
        assert pick_display_name(data, fields) == 'IC-001'

    def test_autoSequence_has_priority_by_order(self):
        from utils.operation_log import pick_display_name

        fields = [
            {'fieldName': 'name', 'controlType': 'text', 'order': 2},
            {'fieldName': 'seqNo', 'controlType': 'autoSequence', 'order': 1},
        ]
        data = {'seqNo': 'IC-002', 'name': '记录名'}
        # order=1 的 autoSequence 排在前面
        assert pick_display_name(data, fields) == 'IC-002'

    def test_autoSequence_skipped_when_empty(self):
        from utils.operation_log import pick_display_name

        fields = [
            {'fieldName': 'seqNo', 'controlType': 'autoSequence', 'order': 1},
            {'fieldName': 'name', 'controlType': 'text', 'order': 2},
        ]
        data = {'seqNo': '', 'name': '有名称'}
        # autoSequence 值为空，应该跳过取 text 字段
        assert pick_display_name(data, fields) == '有名称'


class TestGetFieldLabelMap:
    def test_builds_mapping(self):
        from utils.operation_log import get_field_label_map

        fields = [
            {'fieldName': 'name', 'label': '名称'},
            {'fieldName': 'count', 'label': '数量'},
        ]
        result = get_field_label_map(fields)
        assert result == {'name': '名称', 'count': '数量'}

    def test_missing_label_falls_back(self):
        from utils.operation_log import get_field_label_map

        fields = [
            {'fieldName': 'name'},
        ]
        result = get_field_label_map(fields)
        assert result == {'name': 'name'}

    def test_empty_fields(self):
        from utils.operation_log import get_field_label_map

        assert get_field_label_map([]) == {}


class TestGetPageInfo:
    def test_returns_page_info(self):
        from utils.operation_log import get_page_info

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ('测试页', [{'fieldName': 'name'}])

        name, fields = get_page_info(mock_cursor, 'testCol')
        assert name == '测试页'
        assert len(fields) == 1

    def test_not_found_returns_defaults(self):
        from utils.operation_log import get_page_info

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None

        name, fields = get_page_info(mock_cursor, 'unknownCol')
        assert name == 'unknownCol'
        assert fields == []


class TestLogOperation:
    def test_writes_log_entry(self):
        from utils.operation_log import log_operation

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        @contextmanager
        def fake_get_db():
            yield mock_conn

        from flask import Flask
        app = Flask(__name__)

        with app.test_request_context():
            from flask import g
            g.current_user = {'userId': 'u1', 'username': 'admin', 'role': 'admin'}

            with patch('utils.operation_log.get_db', fake_get_db):
                log_operation('create', 'menu', 'menu-1', '首页', '新增菜单「首页」')

            assert mock_cursor.execute.called

    def test_no_user_skips(self):
        from utils.operation_log import log_operation

        mock_conn = MagicMock()

        @contextmanager
        def fake_get_db():
            yield mock_conn

        from flask import Flask
        app = Flask(__name__)

        with app.test_request_context():
            with patch('utils.operation_log.get_db', fake_get_db):
                log_operation('create', 'menu', 'menu-1', '首页', '新增菜单')

            # cursor.execute should not have been called
            assert not mock_conn.cursor.return_value.execute.called

    def test_exception_does_not_propagate(self):
        from utils.operation_log import log_operation

        from flask import Flask
        app = Flask(__name__)

        with app.test_request_context():
            from flask import g
            g.current_user = {'userId': 'u1', 'username': 'admin', 'role': 'admin'}

            with patch('utils.operation_log.get_db', side_effect=Exception('db error')):
                # Should not raise
                log_operation('create', 'menu', 'menu-1', '首页', '测试')
