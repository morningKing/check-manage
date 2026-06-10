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
    validate_export_script_scope,
    run_export_script,
    run_etl_script,
    run_validation_script,
    FORBIDDEN_NAMES,
)


# ==================== validate_export_script_scope ====================

# 菜单级脚本的执行上下文只注入 menu_data/menu_name/total_records，
# 不注入 data/fields/page_name。把页面级代码（用 fields）存成 scope=menu
# 运行时会抛 NameError: name 'fields' is not defined。保存时即拦截。
PAGE_CSV = (
    "output = io.StringIO()\n"
    "writer = csv.writer(output)\n"
    "headers = [f['label'] for f in fields]\n"
    "writer.writerow(headers)\n"
    "result = output.getvalue()\n"
)
MENU_CSV = (
    "result = []\n"
    "for table in menu_data:\n"
    "    result.append({'filename': table['pageName'] + '.csv', 'content': ''})\n"
)


class TestValidateExportScriptScope:
    def test_menu_scope_without_menu_data_rejected(self):
        with pytest.raises(ValueError, match='menu_data'):
            validate_export_script_scope('menu', PAGE_CSV)

    def test_menu_scope_with_menu_data_ok(self):
        validate_export_script_scope('menu', MENU_CSV)  # should not raise

    def test_page_scope_with_page_code_ok(self):
        validate_export_script_scope('page', PAGE_CSV)  # should not raise

    def test_row_scope_with_page_code_ok(self):
        validate_export_script_scope('row', PAGE_CSV)  # row uses page-style vars too

    def test_none_scope_defaults_to_page_ok(self):
        validate_export_script_scope(None, PAGE_CSV)  # treated as page, no menu_data needed


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

    def test_pandas_available(self):
        """验证 pandas 可用（如果已安装）"""
        script = '''
if pd is None:
    result = "pandas not installed"
else:
    df = pd.DataFrame(data)
    result = str(len(df))
'''
        result_bytes, _, _ = run_export_script(script, [], [], '测试', 'txt')
        # 如果 pandas 安装了，返回 '0'，否则返回 'pandas not installed'
        assert result_bytes in [b'0', b'pandas not installed']

    def test_numpy_available(self):
        """验证 numpy 可用（如果已安装）"""
        script = '''
if np is None:
    result = "numpy not installed"
else:
    arr = np.array([1, 2, 3])
    result = str(arr.sum())
'''
        result_bytes, _, _ = run_export_script(script, [], [], '测试', 'txt')
        # 如果 numpy 安装了，返回 '6'，否则返回 'numpy not installed'
        assert result_bytes in [b'6', b'numpy not installed']


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


# ==================== run_menu_export_script ====================

class TestRunMenuExportScript:
    """测试菜单级导出脚本执行"""

    def test_single_file_output(self):
        """单文件输出"""
        from utils.script_runner import run_menu_export_script

        script = 'result = json.dumps(menu_data, ensure_ascii=False)'
        menu_data = [
            {'collection': 'table1', 'pageName': '表1', 'records': [{'id': 1}], 'fields': [], 'recordCount': 1}
        ]

        files = run_menu_export_script(script, menu_data, '测试菜单', 'json')

        assert len(files) == 1
        result_bytes, filename, content_type = files[0]
        assert b'table1' in result_bytes
        assert filename == '测试菜单.json'
        assert content_type == 'application/json'

    def test_multi_file_output(self):
        """多文件输出"""
        from utils.script_runner import run_menu_export_script

        script = '''
result = [
    {'filename': 'file1.json', 'content': '{"a": 1}'},
    {'filename': 'file2.csv', 'content': 'name,value\\ntest,1'},
]
'''
        menu_data = [
            {'collection': 'table1', 'pageName': '表1', 'records': [], 'fields': [], 'recordCount': 0}
        ]

        files = run_menu_export_script(script, menu_data, '测试菜单', 'json')

        assert len(files) == 2
        assert files[0][1] == 'file1.json'
        assert files[1][1] == 'file2.csv'

    def test_custom_filename(self):
        """自定义文件名"""
        from utils.script_runner import run_menu_export_script

        script = '''
result = "data"
filename = "custom_export.json"
'''
        files = run_menu_export_script(script, [], '菜单', 'json')

        assert files[0][1] == 'custom_export.json'

    def test_injected_variables(self):
        """验证注入变量可用"""
        from utils.script_runner import run_menu_export_script

        script = '''
output = {
    'menuName': menu_name,
    'totalRecords': total_records,
    'tableCount': len(menu_data)
}
result = json.dumps(output)
'''
        menu_data = [
            {'collection': 't1', 'pageName': '表1', 'records': [{'id': 1}], 'fields': [], 'recordCount': 1},
            {'collection': 't2', 'pageName': '表2', 'records': [{'id': 2}, {'id': 3}], 'fields': [], 'recordCount': 2},
        ]

        files = run_menu_export_script(script, menu_data, '测试菜单', 'json')

        import json
        result = json.loads(files[0][0])
        assert result['menuName'] == '测试菜单'
        assert result['totalRecords'] == 3
        assert result['tableCount'] == 2

    def test_result_required(self):
        """必须设置 result"""
        from utils.script_runner import run_menu_export_script

        with pytest.raises(ValueError, match='result'):
            run_menu_export_script('x = 1', [], '菜单', 'json')

    def test_result_must_be_valid_type(self):
        """result 必须是有效类型"""
        from utils.script_runner import run_menu_export_script

        with pytest.raises(ValueError, match='str、bytes 或 list'):
            run_menu_export_script('result = 123', [], '菜单', 'json')

    def test_multi_file_item_must_have_filename_and_content(self):
        """多文件输出时每个元素必须有 filename 和 content"""
        from utils.script_runner import run_menu_export_script

        with pytest.raises(ValueError, match='filename 和 content'):
            run_menu_export_script('result = [{"filename": "test.json"}]', [], '菜单', 'json')

    def test_multi_file_item_must_be_dict(self):
        """多文件输出时元素必须是 dict"""
        from utils.script_runner import run_menu_export_script

        with pytest.raises(ValueError, match='必须是 dict'):
            run_menu_export_script('result = ["string"]', [], '菜单', 'json')

    def test_bytes_result(self):
        """bytes 类型结果"""
        from utils.script_runner import run_menu_export_script

        script = 'result = b"binary data"'
        files = run_menu_export_script(script, [], '菜单', 'json')

        assert files[0][0] == b'binary data'

    def test_content_type_inference(self):
        """content_type 自动推断"""
        from utils.script_runner import run_menu_export_script

        script = 'result = [{"filename": "data.csv", "content": "a,b\\n1,2"}]'
        files = run_menu_export_script(script, [], '菜单', 'json')

        assert files[0][2] == 'text/csv'

    def test_import_blocked(self):
        """禁止 import"""
        from utils.script_runner import run_menu_export_script

        with pytest.raises(ValueError, match='import'):
            run_menu_export_script('import os\nresult = "x"', [], '菜单', 'json')

    def test_injected_modules_available(self):
        """预注入模块可用"""
        from utils.script_runner import run_menu_export_script

        script = '''
# 使用预注入的模块
output = io.StringIO()
writer = csv.writer(output)
writer.writerow(['name', 'value'])
result = output.getvalue()
'''
        files = run_menu_export_script(script, [], '菜单', 'csv')

        assert b'name,value' in files[0][0]

    def test_can_access_all_table_data(self):
        """可以访问所有数据表的数据"""
        from utils.script_runner import run_menu_export_script

        script = '''
# 遍历所有表，提取数据
all_records = []
for table in menu_data:
    for record in table['records']:
        record['_tableName'] = table['pageName']
        all_records.append(record)
result = json.dumps(all_records, ensure_ascii=False)
'''
        menu_data = [
            {
                'collection': 'cases',
                'pageName': '用例表',
                'records': [{'id': 'c1', 'name': '用例1'}, {'id': 'c2', 'name': '用例2'}],
                'fields': [],
                'recordCount': 2
            },
            {
                'collection': 'plans',
                'pageName': '计划表',
                'records': [{'id': 'p1', 'name': '计划1'}],
                'fields': [],
                'recordCount': 1
            },
        ]

        files = run_menu_export_script(script, menu_data, '巡检管理', 'json')

        import json
        result = json.loads(files[0][0])
        assert len(result) == 3
        assert result[0]['_tableName'] == '用例表'
        assert result[2]['_tableName'] == '计划表'
