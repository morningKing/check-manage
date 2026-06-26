import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import utils.batch_engine as eng


def test_content_from_parts_maps_text_and_tool():
    parts = [
        {'type': 'step-start'},
        {'type': 'reasoning', 'text': 'thinking'},
        {'type': 'text', 'text': '总结：项目甲进行中。'},
        {'type': 'tool', 'tool': 'read',
         'state': {'status': 'completed', 'input': {'p': 1}, 'output': 'OUT', 'title': 'T',
                   'time': {'start': 1000, 'end': 1350}}},
        {'type': 'step-finish'},
    ]
    out = eng.BatchWorker._content_from_parts(parts)
    assert {'type': 'text', 'text': '总结：项目甲进行中。'} in out
    tool = [p for p in out if p['type'] == 'tool_use']
    assert len(tool) == 1
    assert tool[0] == {'type': 'tool_use', 'name': 'read', 'title': 'T',
                       'status': 'completed', 'input': {'p': 1}, 'result': 'OUT',
                       'durationMs': 350}
    assert all(p['type'] in ('text', 'tool_use') for p in out)


def test_content_from_parts_drops_empty_text():
    out = eng.BatchWorker._content_from_parts([{'type': 'text', 'text': '   '}])
    assert out == []


# ---------------------------------------------------------------------------
# _persist_conversation – full conversation persistence
# ---------------------------------------------------------------------------

from unittest.mock import patch, MagicMock
from contextlib import contextmanager


def _capture_db():
    inserts = []
    conn = MagicMock(); cur = MagicMock()
    def _exec(sql, params=None):
        if 'INSERT INTO ai_chat_messages' in sql:
            inserts.append(params)
    cur.execute.side_effect = _exec
    conn.cursor.return_value.__enter__ = lambda s: cur
    conn.cursor.return_value.__exit__ = lambda *a: False
    @contextmanager
    def fake_db():
        yield conn
    return fake_db, inserts


def test_persist_conversation_stores_user_and_each_assistant():
    fake_db, inserts = _capture_db()
    raw = [
        {'info': {'role': 'user'}, 'parts': [{'type': 'text', 'text': 'ignored'}]},
        {'info': {'role': 'assistant'},
         'parts': [{'type': 'tool', 'tool': 'read',
                    'state': {'status': 'completed', 'input': {}, 'output': 'O', 'title': ''}}]},
        {'info': {'role': 'assistant'},
         'parts': [{'type': 'text', 'text': '最终答案'}]},
    ]
    oc = MagicMock(); oc.get_messages.return_value = raw
    w = eng.BatchWorker()
    with patch.object(eng, 'opencode_client', oc), patch.object(eng, 'get_db', fake_db):
        w._persist_conversation('sess-1', '我的问题', 'oc-1', {'content': [{'type': 'text', 'text': '最终答案'}]})
    # user row uses a deterministic id ('<sid>:user'); assistants are separate inserts
    user_inserts = [p for p in inserts if str(p[0]).endswith(':user')]
    assistant_inserts = [p for p in inserts if not str(p[0]).endswith(':user')]
    assert len(user_inserts) == 1                # user prompt
    assert len(assistant_inserts) == 2           # 2 assistant messages
    import json
    a1 = json.loads(assistant_inserts[0][2])     # assistant params: (id, sid, content, meta)
    assert a1[0]['type'] == 'tool_use' and a1[0]['name'] == 'read'
