import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from utils.ai_scan_engine import extract_json


def test_extract_fenced_json():
    text = "分析如下\n```json\n{\"结论\": \"通过\", \"意见\": \"ok\"}\n```\n谢谢"
    assert extract_json(text) == {'结论': '通过', '意见': 'ok'}


def test_extract_last_balanced_object():
    text = '随便 {\"a\":1} 中间 {\"结论\": \"驳回\"}'
    assert extract_json(text) == {'结论': '驳回'}


def test_extract_none_returns_none():
    assert extract_json('没有 JSON 的纯文本') is None


from utils.ai_scan_engine import message_text


def test_message_text_joins_parts():
    msg = {'role': 'assistant', 'content': [
        {'type': 'text', 'text': 'a'}, {'type': 'text', 'text': 'b'}]}
    assert message_text(msg) == 'a\nb'


def test_message_text_none():
    assert message_text(None) == ''


from unittest.mock import MagicMock, patch
from contextlib import contextmanager
import utils.ai_scan_engine as se


def _patch_db():
    cur = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = lambda s: cur
    conn.cursor.return_value.__exit__ = lambda s, *a: None
    conn.__enter__ = lambda s: conn
    conn.__exit__ = lambda s, *a: None
    @contextmanager
    def fake():
        yield conn
    return fake, cur


TASK = {'id': 't1', 'collection': 'orders', 'branch_id': 'main',
        'status_field': '审核状态', 'done_value': '已审核', 'failed_value': '审核失败',
        'field_mapping': [{'jsonKey': '结论', 'column': '审核结论', 'required': True}]}


def test_writeback_success_sets_mapped_columns_and_done():
    fake, cur = _patch_db()
    row = {'scan_task_id': 't1', 'source_record_id': 'rec-1'}
    msg = {'content': [{'type': 'text', 'text': '```json\n{"结论":"通过"}\n```'}]}
    with patch('utils.ai_scan_engine.get_db', fake), \
         patch('utils.ai_scan_engine._load_task', lambda tid: TASK):
        se.on_child_finished(row, msg, ok=True)
    upd = [c for c in cur.execute.call_args_list if 'UPDATE dynamic_data' in str(c.args[0])]
    assert upd, 'expected a dynamic_data UPDATE'
    flat = [v for c in upd for v in (c.args[1] if len(c.args) > 1 else ())]
    assert '通过' in flat and '已审核' in flat and 'rec-1' in flat


def test_writeback_missing_required_marks_failed():
    fake, cur = _patch_db()
    row = {'scan_task_id': 't1', 'source_record_id': 'rec-1'}
    msg = {'content': [{'type': 'text', 'text': '没有 JSON'}]}
    with patch('utils.ai_scan_engine.get_db', fake), \
         patch('utils.ai_scan_engine._load_task', lambda tid: TASK):
        se.on_child_finished(row, msg, ok=True)
    flat = [v for c in cur.execute.call_args_list for v in (c.args[1] if len(c.args) > 1 else ())]
    assert '审核失败' in flat


def test_writeback_child_failed_marks_failed():
    fake, cur = _patch_db()
    row = {'scan_task_id': 't1', 'source_record_id': 'rec-1'}
    with patch('utils.ai_scan_engine.get_db', fake), \
         patch('utils.ai_scan_engine._load_task', lambda tid: TASK):
        se.on_child_finished(row, None, ok=False)
    flat = [v for c in cur.execute.call_args_list for v in (c.args[1] if len(c.args) > 1 else ())]
    assert '审核失败' in flat
