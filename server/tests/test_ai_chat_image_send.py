"""Task 9 图片附件 mime 分流 + image_parts 透传（send_message → OpenCode）。

测试位置说明（控制器裁定 2026-10-31 备用路径）：本文件 fixture
复制自 test_routes_ai_chat.py:22（该文件本环境收集期挂起）。
2026-10-31 实测补充：该文件收集现已可通过（PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
--collect-only 0.53s 完成），但**执行**期挂起——stash 验证 clean HEAD 上
既有用例 test_send_message_persists_user_and_calls_opencode 同样挂起。
根因：其 setup fixture 先 patch('db.get_db') 再 `from app import app`，
app 启动迁移 migrations/2026_09_28_message_search_text.py 的
`while True: ... if cur.rowcount == 0: break` 在 MagicMock cursor 上
rowcount 恒真 → 死循环。故按裁定走本备用文件；并在模块顶层（打补丁前）
先 import app 命中 sys.modules 缓存，避免 fixture 内 app 启动迁移踩中同一坑
——test_ai_chat_feedback.py 等未先 patch 即 import app 的文件均正常，即为佐证。
"""
import sys
import os
import pytest
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# 必须先于任何 db.get_db 打补丁完成 app 导入（启动迁移对真测试库跑一次，
# 之后 fixture 内的 from app import app 走模块缓存）——见文件头说明。
from app import app  # noqa: F401,E402

from auth import create_token


def _make_mock_db(mock_conn):
    @contextmanager
    def fake_get_db():
        yield mock_conn
    return fake_get_db


@pytest.fixture
def setup(mock_conn, mock_cursor, tmp_path):
    fake_db = _make_mock_db(mock_conn)
    fake_client = MagicMock()
    fake_client.create_session.return_value = "oc_sess_42"
    fake_client.register_mcp.return_value = None

    class _RuntimeStub:
        """get_runtime() 的测试替身：交互路径（create_session/send_message/
        sse_events）接线 runtime 门面后不再直构 OpenCodeClient，打桩点从
        routes.ai_chat.OpenCodeClient 相应迁到这里——get_client/subscribe_events
        全部委派 fake_client，既有断言（oc.send_prompt_async / oc.subscribe_events）
        保持有效。"""
        def __init__(self, c):
            self._c = c
        def get_client(self):
            return self._c
        def subscribe_events(self, directory='', read_timeout=None):
            return self._c.subscribe_events(directory=directory,
                                            read_timeout=read_timeout)
        def get_messages(self, opencode_session_id, directory=''):
            return self._c.get_messages(opencode_session_id, directory=directory)

    patches = [
        patch('db.get_db', fake_db),
        patch('db.pool', MagicMock()),
        patch('routes.ai_chat.get_db', fake_db),
        patch('utils.session_token.get_db', fake_db),
        patch('utils.chat_persist.get_db', fake_db),
        patch('routes.ai_chat.OpenCodeClient', return_value=fake_client),
        patch('routes.ai_chat.get_runtime',
              lambda: _RuntimeStub(fake_client)),
        patch('config.AI_WORKSPACE_ROOT', str(tmp_path)),
        patch('routes.ai_chat.AI_WORKSPACE_ROOT', str(tmp_path)),
    ]
    for p in patches:
        p.start()

    # Isolate the global persist-listener registry: a prior send test may leave
    # a listener registered for 'sess_x', which would make has_listener() True
    # and skip the SSE proxy's fallback persist under test here.
    from utils import chat_persist
    chat_persist._listeners.clear()

    from app import app
    app.config['TESTING'] = True
    dev = create_token({'id': 'user-1', 'username': 'dev', 'role': 'developer'})
    guest = create_token({'id': 'user-2', 'username': 'g', 'role': 'guest'})

    yield (
        app.test_client(), mock_cursor, fake_client,
        {'Authorization': f'Bearer {dev}'},
        {'Authorization': f'Bearer {guest}'},
        tmp_path,
    )

    for p in patches:
        p.stop()


def test_image_attachment_parts(setup, tmp_path):
    """图片：不内联、进 image_parts（URL 形态按 IMAGE_PART_MODE）；文本照旧内联。"""
    from routes.ai_chat import IMAGE_PART_MODE
    client, cursor, oc, dev_h, _, _ = setup
    ws = tmp_path / 'ws'
    (ws / 'uploads').mkdir(parents=True)
    (ws / 'uploads' / 'x.png').write_bytes(b'\x89PNG\r\n\x1a\n' + b'0' * 32)
    (ws / 'uploads' / 'y.jpeg').write_bytes(b'\xff\xd8\xff\xe0' + b'0' * 32)
    (ws / 'uploads' / 'a.txt').write_text('hello', encoding='utf-8')
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active',
                                    str(ws), None, None)
    resp = client.post(
        '/ai/chat/sessions/sess_x/messages',
        json={'content': '看图', 'attachments': ['uploads/x.png', 'uploads/a.txt',
                                                 'uploads/y.jpeg']},
        headers=dev_h,
    )
    assert resp.status_code == 202
    args, kwargs = oc.send_prompt_async.call_args
    assert 'hello' in args[1]                       # 文本仍内联
    ip = kwargs['image_parts']
    assert ip == [{'name': 'x.png', 'mime': 'image/png', 'url': ip[0]['url']},
                  {'name': 'y.jpeg', 'mime': 'image/jpeg', 'url': ip[1]['url']}]
    if IMAGE_PART_MODE == 'dataurl':
        assert ip[0]['url'].startswith('data:image/png;base64,')
        assert ip[1]['url'].startswith('data:image/jpeg;base64,')
    else:
        assert ip[0]['url'].startswith('file://')
        assert ip[1]['url'].startswith('file://')
    # 非图片二进制走既有 file 分支，不进 image_parts
    resp = client.post(
        '/ai/chat/sessions/sess_x/messages',
        json={'content': '看文件', 'attachments': ['uploads/x.exe']},
        headers=dev_h,
    )
    assert resp.status_code == 202
    _, kwargs2 = oc.send_prompt_async.call_args
    assert kwargs2.get('image_parts') in (None, [])


def test_image_missing_file_no_500(setup, tmp_path):
    """图片文件缺失/不可读：降级 file:// 形态，不 500（dataurl 读失败兜底）。"""
    client, cursor, oc, dev_h, _, _ = setup
    ws = tmp_path / 'ws2'
    (ws / 'uploads').mkdir(parents=True)            # 不造 x.png
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active',
                                    str(ws), None, None)
    resp = client.post(
        '/ai/chat/sessions/sess_x/messages',
        json={'content': '看图', 'attachments': ['uploads/x.png']},
        headers=dev_h,
    )
    assert resp.status_code == 202
    _, kwargs = oc.send_prompt_async.call_args
    assert kwargs['image_parts'][0]['url'].startswith('file://')
