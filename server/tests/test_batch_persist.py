import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import utils.batch_engine as eng


def test_content_from_parts_maps_text_and_tool():
    parts = [
        {'type': 'step-start'},
        {'type': 'reasoning', 'text': 'thinking'},
        {'type': 'text', 'text': '总结：项目甲进行中。'},
        {'type': 'tool', 'tool': 'read',
         'state': {'status': 'completed', 'input': {'p': 1}, 'output': 'OUT', 'title': 'T'}},
        {'type': 'step-finish'},
    ]
    out = eng.BatchWorker._content_from_parts(parts)
    assert {'type': 'text', 'text': '总结：项目甲进行中。'} in out
    tool = [p for p in out if p['type'] == 'tool_use']
    assert len(tool) == 1
    assert tool[0] == {'type': 'tool_use', 'name': 'read', 'title': 'T',
                       'status': 'completed', 'input': {'p': 1}, 'result': 'OUT'}
    assert all(p['type'] in ('text', 'tool_use') for p in out)


def test_content_from_parts_drops_empty_text():
    out = eng.BatchWorker._content_from_parts([{'type': 'text', 'text': '   '}])
    assert out == []
