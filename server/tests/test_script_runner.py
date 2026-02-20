"""
脚本沙箱执行器单元测试

测试导出脚本、ETL 脚本、校验脚本的沙箱安全和执行逻辑。
"""

import sys
import os
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.script_runner import (
    _validate_script,
    run_export_script,
    run_etl_script,
    run_validation_script,
    FORBIDDEN_NAMES,
)


# ==================== _validate_script ====================

class TestValidateScript:
    def test_import_forbidden(self):
        with pytest.raises(ValueError, match='import'):
            _validate_script('import os')

    def test_from_import_forbidden(self):
        with pytest.raises(ValueError, match='import'):
            _validate_script('from os import path')

    def test_open_forbidden(self):
        with pytest.raises(ValueError, match='open'):
            _validate_script('f = open("test.txt")')

    def test_exec_forbidden(self):
        with pytest.raises(ValueError, match='exec'):
            _validate_script('x = 1\nexec("code")')

    def test_eval_forbidden(self):
        with pytest.raises(ValueError, match='eval'):
            _validate_script('x = eval("1+1")')

    def test_dunder_forbidden(self):
        with pytest.raises(ValueError, match='双下划线'):
            _validate_script('x = obj.__class__')

    def test_safe_script_passes(self):
        # 不应抛异常
        _validate_script('result = [x for x in data]')

    def test_inline_import_not_caught(self):
        # import 在字符串中间不是语句，不应被拦截
        _validate_script('msg = "please reimport data"')


# ==================== run_export_script ====================

class TestRunExportScript:
    def test_basic_json_export(self):
        script = 'result = json.dumps(data)'
        data = [{'name': 'test'}]
        fields = [{'fieldName': 'name', 'label': '名称'}]

        result_bytes, filename, content_type = run_export_script(
            script, data, fields, '测试页面', 'json'
        )
        assert b'test' in result_bytes
        assert filename == '测试页面.json'
        assert content_type == 'application/json'

    def test_custom_filename(self):
        script = 'result = "hello"\nfilename = "custom.txt"'
        result_bytes, filename, _ = run_export_script(
            script, [], [], '页面', 'txt'
        )
        assert filename == 'custom.txt'

    def test_custom_content_type(self):
        script = 'result = "data"\ncontent_type = "text/xml"'
        _, _, content_type = run_export_script(
            script, [], [], '页面', 'txt'
        )
        assert content_type == 'text/xml'

    def test_result_required(self):
        with pytest.raises(ValueError, match='result'):
            run_export_script('x = 1', [], [], '页面', 'json')

    def test_result_must_be_str_or_bytes(self):
        with pytest.raises(ValueError, match='str 或 bytes'):
            run_export_script('result = 123', [], [], '页面', 'json')

    def test_bytes_result(self):
        script = 'result = b"binary data"'
        result_bytes, _, _ = run_export_script(script, [], [], '页面', 'json')
        assert result_bytes == b'binary data'

    def test_injected_modules(self):
        """验证预注入模块可用"""
        script = '''
import_data = json.dumps(data)
csv_writer = csv.writer(io.StringIO())
val = math.ceil(1.5)
result = str(val)
'''
        result_bytes, _, _ = run_export_script(script, [], [], '页面', 'txt')
        assert result_bytes == b'2'


# ==================== run_etl_script ====================

class TestRunEtlScript:
    def test_basic_transform(self):
        script = 'result = [{"name": r["name"].upper()} for r in records]'
        records = [{'name': 'hello'}, {'name': 'world'}]
        out = run_etl_script(script, records)
        assert out == [{'name': 'HELLO'}, {'name': 'WORLD'}]

    def test_result_required(self):
        with pytest.raises(ValueError, match='result'):
            run_etl_script('x = 1', [])

    def test_filter_records(self):
        script = 'result = [r for r in records if r.get("score", 0) > 50]'
        records = [{'score': 80}, {'score': 30}, {'score': 90}]
        out = run_etl_script(script, records)
        assert len(out) == 2

    def test_import_blocked(self):
        with pytest.raises(ValueError, match='import'):
            run_etl_script('import os\nresult = []', [])


# ==================== run_validation_script ====================

class TestRunValidationScript:
    def _make_mock_conn(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []
        mock_cur.fetchone.return_value = None
        mock_conn.cursor.return_value = mock_cur
        return mock_conn

    def test_add_error(self):
        script = 'add_error("名称不能为空")'
        errors, warnings, _ = run_validation_script(
            script, {'name': ''}, 'create', None,
            [{'fieldName': 'name'}], 'test', self._make_mock_conn()
        )
        assert len(errors) == 1
        assert '名称' in errors[0]

    def test_add_warning(self):
        script = 'add_warning("建议填写描述")'
        errors, warnings, _ = run_validation_script(
            script, {}, 'create', None, [], 'test', self._make_mock_conn()
        )
        assert len(errors) == 0
        assert len(warnings) == 1

    def test_no_errors(self):
        script = 'x = 1'  # 什么都不做
        errors, warnings, _ = run_validation_script(
            script, {}, 'create', None, [], 'test', self._make_mock_conn()
        )
        assert len(errors) == 0
        assert len(warnings) == 0

    def test_action_variable(self):
        script = '''
if action == "create":
    add_error("不允许创建")
'''
        errors, _, _ = run_validation_script(
            script, {}, 'create', None, [], 'test', self._make_mock_conn()
        )
        assert len(errors) == 1

    def test_record_variable(self):
        script = '''
if not record.get("name"):
    add_error("名称不能为空")
'''
        errors, _, _ = run_validation_script(
            script, {'name': ''}, 'create', None, [], 'test', self._make_mock_conn()
        )
        assert len(errors) == 1

    def test_set_relations(self):
        script = 'set_relations("tags", "tag-collection", "items", ["id1", "id2"])'
        _, _, pending = run_validation_script(
            script, {}, 'create', None, [], 'test', self._make_mock_conn()
        )
        assert len(pending) == 1
        assert pending[0]['fieldName'] == 'tags'
        assert pending[0]['ids'] == ['id1', 'id2']

    def test_query_function(self):
        """验证 query() 函数能被调用"""
        mock_conn = self._make_mock_conn()
        mock_conn.cursor().fetchall.return_value = [
            ('rec-1', {'name': 'existing'}),
        ]
        script = '''
existing = query("test")
if len(existing) > 0:
    add_warning("已有数据")
'''
        _, warnings, _ = run_validation_script(
            script, {}, 'create', None, [], 'test', mock_conn
        )
        assert len(warnings) == 1
