"""Langfuse 集成测试：贯通「持久化路径 → 映射 → 导出」的真实代码路径。

按计划（docs/superpowers/plans/2026-09-12-langfuse-agent-observability.md
Task 7）的全局约束运行：Langfuse 默认关闭、无网络依赖——导出器用注入的
fake（记录提交内容），OpenCode 侧用 fake 客户端；真实 Langfuse 自托管栈的
冒烟（e2e/langfuse-trace-link.spec.ts + 真栈断言）依赖 Docker，在本环境
不可用时按计划记录为环境相关跳过。

覆盖：
  - 交互回合：事件源 → 持久化（先于导出）→ 映射出 root generation +
    execute_tool，trace_id 确定性、session 关联正确；
  - 批任务子会话：REST 快照 → root/tool/嵌套子代理树，batch_id/task_id
    元数据、终态状态；
  - 失败隔离：导出器抛异常 / Langfuse 停用 → 持久化照常完成、无异常外泄。
"""
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _ev(etype, props):
    return {'event': etype, 'data': {'properties': props}}


class RecordingExporter:
    """submit() 同步记录提交内容 —— 让集成测试确定性断言管线产物。"""

    def __init__(self, result=None, error=None):
        self.submissions: list[list] = []
        self.result = result
        self.error = error

    def submit(self, observations):
        if self.error is not None:
            raise self.error
        self.submissions.append(list(observations))
        return self.result if self.result is not None else 'accepted'


@pytest.fixture
def user_id(db_conn):
    uid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, %s, %s, 'developer')",
            (uid, f'lf_user_{uid[:8]}', 'x', f'LF User {uid[:8]}'),
        )
    db_conn.commit()
    yield uid
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_chat_sessions WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM ai_chat_batches WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    db_conn.commit()


def _seed_interactive_session(db_conn, user_id, oc='oc-it'):
    sid = 'sess_' + uuid.uuid4().hex[:12]
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, title, status, "
            "  opencode_session_id, workspace_path) "
            "VALUES (%s, %s, 'lf-it', 'active', %s, '')",
            (sid, user_id, oc),
        )
    db_conn.commit()
    return sid


# ---------------------------------------------------------------------------
# 交互回合：事件源 → 持久化 → 导出
# ---------------------------------------------------------------------------

def test_interactive_turn_exports_generation_and_tool_tree(db_conn, user_id,
                                                            monkeypatch):
    import utils.chat_persist as chat_persist
    from utils.langfuse_config import trace_id_for

    sid = _seed_interactive_session(db_conn, user_id)
    exporter = RecordingExporter()
    monkeypatch.setattr(chat_persist, 'get_langfuse_exporter',
                        lambda: exporter)

    events = [
        _ev('message.updated',
            {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc-it'}}),
        _ev('message.part.updated',
            {'part': {'id': 'p1', 'messageID': 'm1', 'type': 'text',
                      'text': '你好，答案是 42', 'sessionID': 'oc-it'}}),
        _ev('message.part.updated',
            {'part': {'id': 'p2', 'messageID': 'm1', 'type': 'tool',
                      'tool': 'bash',
                      'state': {'status': 'completed', 'output': 'ok',
                                'time': {'start': 1000, 'end': 1123}},
                      'sessionID': 'oc-it'}}),
        _ev('session.idle', {'sessionID': 'oc-it'}),
    ]
    chat_persist._run_listener(sid, 'oc-it', iter(events), directory='')

    # 持久化先于导出：消息已落库，且导出确实发生
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ai_chat_messages WHERE session_id=%s", (sid,))
        assert cur.fetchone()[0] >= 1
    assert exporter.submissions, 'interactive turn must submit observations'

    obs = [o for batch in exporter.submissions for o in batch]
    names = {o.name for o in obs}
    assert 'generation' in names
    assert 'execute_tool' in names
    # trace/session 关联：确定性 trace id + 应用会话 id
    expected_trace = trace_id_for(sid, None, 'm1')
    assert all(o.trace_id == expected_trace for o in obs)
    assert all(o.session_id == sid for o in obs)
    # 工具观测带成功状态与耗时映射
    tool = [o for o in obs if o.name == 'execute_tool'][0]
    assert tool.status in ('success', 'completed')
    # 确定性：同一回合重复导出产生同一 trace id
    assert trace_id_for(sid, None, 'm1') == expected_trace


def test_interactive_export_failure_does_not_break_persistence(db_conn,
                                                                 user_id,
                                                                 monkeypatch):
    """导出器抛异常（Langfuse 不可达的代理形态）→ 持久化照常、无异常外泄。"""
    import utils.chat_persist as chat_persist

    sid = _seed_interactive_session(db_conn, user_id)

    class _Boom:
        def submit(self, observations):
            raise RuntimeError('langfuse down')

    monkeypatch.setattr(chat_persist, 'get_langfuse_exporter', lambda: _Boom())

    events = [
        _ev('message.updated',
            {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc-it'}}),
        _ev('message.part.updated',
            {'part': {'id': 'p1', 'messageID': 'm1', 'type': 'text',
                      'text': '正常回复', 'sessionID': 'oc-it'}}),
        _ev('session.idle', {'sessionID': 'oc-it'}),
    ]
    # 不抛异常即为通过：导出失败绝不打断监听/持久化
    chat_persist._run_listener(sid, 'oc-it', iter(events), directory='')

    with db_conn.cursor() as cur:
        cur.execute("SELECT content FROM ai_chat_messages WHERE session_id=%s", (sid,))
        rows = cur.fetchall()
    assert rows, 'persistence must survive exporter failure'


# ---------------------------------------------------------------------------
# 批任务子会话：REST 快照 → root/tool/子代理树
# ---------------------------------------------------------------------------

def _seed_batch(db_conn, user_id, n=1):
    bid = str(uuid.uuid4())
    sids = []
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
            "VALUES (%s, %s, 'lf-batch', 'p', %s)", (bid, user_id, n))
        for i in range(n):
            sid = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO ai_chat_sessions "
                "  (id, user_id, status, batch_id, batch_seq, batch_input_file) "
                "VALUES (%s, %s, 'pending', %s, %s, %s)",
                (sid, user_id, bid, i, f'f{i}.csv'))
            sids.append(sid)
    db_conn.commit()
    return bid, sids


def _fake_oc_with_subagent(root_text='root answer', child_text='child answer'):
    """root 会话：text + tool:'task'（真实子会话 id=child-1，error 终态模拟
    已完成的委托）；child-1 会话：自己的 assistant 消息。"""
    class _Fake:
        def create_session(self, directory='', title=''):
            return 'oc-batch'

        def send_message(self, oc, prompt, directory='', agent='', model=''):
            return None

        def list_agents(self, directory=''):
            return []

        def get_messages(self, oc, directory=''):
            if oc == 'child-1':
                return [{
                    'info': {'role': 'assistant', 'id': 'c-a1', 'sessionID': 'child-1',
                             'finish': 'stop', 'time': {'created': 5, 'completed': 6}},
                    'parts': [{'id': 'cp1', 'type': 'text', 'text': child_text}],
                }]
            return [{
                'info': {'role': 'user', 'id': 'u1', 'sessionID': 'oc-batch',
                         'time': {'created': 1}},
                'parts': [{'id': 'up1', 'type': 'text', 'text': 'do it'}],
            }, {
                'info': {'role': 'assistant', 'id': 'a1', 'sessionID': 'oc-batch',
                         'finish': 'stop', 'time': {'created': 2, 'completed': 9}},
                'parts': [
                    {'id': 'ap1', 'type': 'text', 'text': root_text},
                    {'id': 'ap2', 'type': 'tool', 'tool': 'bash',
                     'state': {'status': 'completed', 'output': 'ok',
                               'time': {'start': 10, 'end': 20}}},
                    {'id': 'ap3', 'type': 'tool', 'tool': 'task',
                     'state': {'status': 'completed', 'output': child_text,
                               'metadata': {'sessionId': 'child-1'},
                               'input': {'subagent_type': 'researcher',
                                         'description': 'dig deeper'}}},
                ],
            }]

        def list_messages(self, oc, directory=''):
            raw = self.get_messages(oc, directory=directory)
            out = []
            for m in raw:
                info = m['info']
                content = []
                running = False
                for p in m['parts']:
                    t = p.get('type')
                    if t == 'text':
                        content.append({'type': 'text', 'text': p['text']})
                    elif t == 'tool':
                        st = p.get('state') or {}
                        out_len = len(st.get('output') or '')
                        content.append({'type': 'tool_use', 'name': p.get('tool'),
                                        'status': st.get('status'),
                                        'output_len': out_len,
                                        'child_sid': (st.get('metadata') or {}).get('sessionId')})
                        if st.get('status') in (None, '', 'pending', 'running'):
                            running = True
                finished = bool((info.get('time') or {}).get('completed')) \
                    and info.get('finish') not in (None, '', 'tool-calls', 'tool_use')
                out.append({'role': 'assistant', 'finished': finished,
                            'content': content, 'finish': info.get('finish'),
                            'running_tool': running, 'error': info.get('error'),
                            'id': info.get('id')})
            return out

    return _Fake()


def test_batch_child_exports_root_tool_and_subagent_tree(db_conn, user_id,
                                                          monkeypatch, tmp_path):
    import utils.batch_engine as eng
    from utils.langfuse_config import trace_id_for

    bid, sids = _seed_batch(db_conn, user_id)
    sid = sids[0]
    exporter = RecordingExporter()
    monkeypatch.setattr(eng, 'get_langfuse_exporter', lambda: exporter)
    monkeypatch.setattr(eng, '_prepare_workspace',
                        lambda *a, **kw: str(tmp_path))
    monkeypatch.setattr(eng, 'opencode_client', _fake_oc_with_subagent())

    worker = eng.BatchWorker()
    worker.POLL_INTERVAL_SEC = 0
    worker._run_one({'id': sid, 'user_id': user_id, 'batch_id': bid,
                     'batch_input_file': 'x.csv', 'input_files': None,
                     'scan_task_id': None, 'opencode_session_id': None,
                     'workspace_path': str(tmp_path), 'continue_prompt': None,
                     'agent': '', 'model': ''})

    with db_conn.cursor() as cur:
        cur.execute("SELECT status FROM ai_chat_sessions WHERE id=%s", (sid,))
        assert cur.fetchone()[0] == 'completed'

    assert exporter.submissions, 'batch run must submit observations'
    obs = [o for batch in exporter.submissions for o in batch]
    names = {o.name for o in obs}
    # root generation、工具调用、委托的子代理三种类都在
    assert 'generation' in names
    assert 'execute_tool' in names
    assert 'invoke_agent' in names

    expected_trace = trace_id_for(sid, bid, f'{sid}:user')
    assert all(o.trace_id == expected_trace for o in obs)
    assert all(o.session_id == sid for o in obs)

    # invoke_agent 规范化：真实子会话 id 作为 task_id（不是父会话的
    # SubtaskPart.sessionID），batch_id 进 metadata
    agent = [o for o in obs if o.name == 'invoke_agent'][0]
    assert agent.metadata.get('task_id') == 'child-1'
    assert agent.metadata.get('batch_id') == bid
    tool_names = {o.name for o in obs if o.name == 'execute_tool'}
    assert 'execute_tool' in tool_names  # 普通工具也在场

    # 终态状态映射：回合成功 → 非 error 状态
    root = [o for o in obs if o.name == 'generation'][0]
    assert root.status != 'error'


# ---------------------------------------------------------------------------
# Langfuse 停用：零导出
# ---------------------------------------------------------------------------

def test_disabled_langfuse_exports_nothing(db_conn, user_id, monkeypatch):
    """LANGFUSE_ENABLED=false（默认）：submit 返回 DISABLED、不产生任何出站
    请求，交互链路照常完成。"""
    import utils.chat_persist as chat_persist
    from utils.langfuse_exporter import LangfuseExporter
    from utils.langfuse_config import LangfuseSettings

    sid = _seed_interactive_session(db_conn, user_id)
    settings = LangfuseSettings(enabled=False, host='', public_key='',
                                secret_key='', environment='test',
                                capture_content=False, sample_rate=1.0,
                                queue_size=16, flush_interval_seconds=0.1)
    calls = []
    exporter = LangfuseExporter(settings, client_factory=lambda: (_ for _ in ()).throw(
        AssertionError('disabled exporter must not construct a client')))
    # 包一层记录：submit 的返回值应为 DISABLED
    original_submit = exporter.submit

    def _spy(observations):
        result = original_submit(observations)
        calls.append(result)
        return result

    monkeypatch.setattr(exporter, 'submit', _spy)
    monkeypatch.setattr(chat_persist, 'get_langfuse_exporter', lambda: exporter)

    events = [
        _ev('message.updated',
            {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc-it'}}),
        _ev('message.part.updated',
            {'part': {'id': 'p1', 'messageID': 'm1', 'type': 'text',
                      'text': 'disabled path', 'sessionID': 'oc-it'}}),
        _ev('session.idle', {'sessionID': 'oc-it'}),
    ]
    chat_persist._run_listener(sid, 'oc-it', iter(events), directory='')

    from utils.langfuse_exporter import ExportResult
    assert calls and all(r == ExportResult.DISABLED for r in calls)
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ai_chat_messages WHERE session_id=%s", (sid,))
        assert cur.fetchone()[0] >= 1
