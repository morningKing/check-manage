"""server/utils/opencode_parts.py 的测试。

map_part 是从 chat_persist.py（apply_event 的 text/tool 分支）与
batch_engine.py（_content_from_parts）里抽出来的共享映射——这两处原来的实现
逻辑分别独立测试过，这里额外补一份直接测 map_part 本身，并且要能证明：把两处
调用点接到它之后，两边的既有测试不用改一行断言就能继续通过（那两个文件各自
的测试文件里不新增关于这个抽取的用例，靠它们原样通过来当证据）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.opencode_parts import map_part, format_opencode_error  # noqa: E402


def test_map_part_text_unconditional_no_empty_filter():
    """text 分支不过滤空文本——过滤留给调用方（见既有两处实现的分工）。"""
    assert map_part({'type': 'text', 'text': 'hi'}) == {'type': 'text', 'text': 'hi'}
    assert map_part({'type': 'text', 'text': ''}) == {'type': 'text', 'text': ''}
    assert map_part({'type': 'text'}) == {'type': 'text', 'text': ''}


def test_map_part_tool_maps_all_fields():
    part = {'type': 'tool', 'tool': 'bash',
            'state': {'status': 'completed', 'title': 't', 'input': {'cmd': 'ls'},
                      'output': 'ok', 'time': {'start': 1000, 'end': 1123}}}
    assert map_part(part) == {
        'type': 'tool_use', 'name': 'bash', 'title': 't', 'status': 'completed',
        'input': {'cmd': 'ls'}, 'result': 'ok', 'durationMs': 123,
    }


def test_map_part_tool_falls_back_to_state_result_when_output_absent():
    part = {'type': 'tool', 'tool': 'x', 'state': {'status': 'error', 'result': 'boom'}}
    mapped = map_part(part)
    assert mapped['result'] == 'boom'


def test_map_part_subtask_maps_agent_and_description():
    part = {'type': 'subtask', 'sessionID': 'ses_child1', 'agent': 'build',
            'description': '重构模块 X'}
    assert map_part(part) == {
        'type': 'subtask_use', 'subtaskId': 'ses_child1',
        'agent': 'build', 'description': '重构模块 X', 'status': 'running',
    }


def test_map_part_subtask_uses_status_lookup_when_provided():
    part = {'type': 'subtask', 'sessionID': 'ses_child1', 'agent': 'build',
            'description': 'x'}
    mapped = map_part(part, subtask_status={'ses_child1': 'failed'})
    assert mapped['status'] == 'failed'


def test_map_part_subtask_without_session_id_is_dropped():
    """畸形数据（缺 sessionID 的 subtask part）不应该产出一个没法拉取的占位。"""
    assert map_part({'type': 'subtask', 'agent': 'build'}) is None


def test_map_part_unknown_type_is_dropped():
    assert map_part({'type': 'reasoning', 'text': 'thinking...'}) is None


def test_format_opencode_error_with_provider_and_message():
    err = {'name': 'ProviderAuthError', 'data': {'providerID': 'anthropic',
                                                  'message': 'API key not found'}}
    msg = format_opencode_error(err)
    assert 'ProviderAuthError' in msg
    assert 'anthropic' in msg
    assert 'API key not found' in msg


def test_format_opencode_error_without_detail():
    msg = format_opencode_error({'name': 'UnknownError', 'data': {}})
    assert 'UnknownError' in msg


def test_format_opencode_error_handles_none():
    msg = format_opencode_error(None)
    assert 'UnknownError' in msg
