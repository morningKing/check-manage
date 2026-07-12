"""单测：utils/search_text.py 的 compute_search_text 拼接逻辑。"""
from utils.search_text import compute_search_text, DIRECT_SEARCHABLE_TYPES


class TestComputeSearchText:
    def test_concatenates_direct_searchable_field_values(self):
        fields = [
            {'fieldName': 'name', 'controlType': 'text'},
            {'fieldName': 'note', 'controlType': 'textarea'},
        ]
        data = {'name': '张三', 'note': '备注内容'}
        assert compute_search_text(data, fields) == '张三 备注内容'

    def test_skips_non_direct_searchable_types(self):
        fields = [
            {'fieldName': 'name', 'controlType': 'text'},
            {'fieldName': 'ref', 'controlType': 'reference'},
            {'fieldName': 'files', 'controlType': 'file'},
            {'fieldName': 'items', 'controlType': 'quoteSelect'},
        ]
        data = {'name': '张三', 'ref': 'rec-1', 'files': [{'uid': 'x'}], 'items': ['a', 'b']}
        assert compute_search_text(data, fields) == '张三'

    def test_skips_none_and_empty_string_values(self):
        fields = [
            {'fieldName': 'a', 'controlType': 'text'},
            {'fieldName': 'b', 'controlType': 'text'},
            {'fieldName': 'c', 'controlType': 'text'},
        ]
        data = {'a': None, 'b': '', 'c': 'kept'}
        assert compute_search_text(data, fields) == 'kept'

    def test_number_and_date_fields_are_stringified(self):
        fields = [
            {'fieldName': 'price', 'controlType': 'number'},
            {'fieldName': 'due', 'controlType': 'date'},
        ]
        data = {'price': 999, 'due': '2026-01-01'}
        assert compute_search_text(data, fields) == '999 2026-01-01'

    def test_empty_fields_returns_empty_string(self):
        assert compute_search_text({'x': 'y'}, []) == ''

    def test_missing_data_and_fields_do_not_raise(self):
        assert compute_search_text(None, None) == ''
        assert compute_search_text({}, [{'fieldName': 'x', 'controlType': 'text'}]) == ''

    def test_all_documented_direct_searchable_types_included(self):
        # 防止有人悄悄改了类型集合却没意识到会影响搜索覆盖面
        assert DIRECT_SEARCHABLE_TYPES == {
            'text', 'textarea', 'markdown', 'number', 'autoSequence',
            'select', 'radio', 'date', 'datetime', 'autoTimestamp', 'compositeText',
        }
