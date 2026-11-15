"""events_bridge_v2 单测：V2 事件 → V1 形态帧的翻译契约。

载荷字段以 v2.2.6 真机实测为准（sse-capture / sse-tool-capture /
sse-sub-capture 三份实拍流）。
"""
from ai.sessions.events_bridge_v2 import V2EventBridge, normalize_v2_tool


def _evt(etype, data):
    return {'type': etype, 'data': data,
            'location': {'directory': 'C:\\ws'}}


def test_execution_succeeded_maps_to_idle():
    b = V2EventBridge()
    frames = b.translate(_evt('session.execution.succeeded',
                              {'sessionID': 'ses_a'}))
    assert frames == [{'event': 'session.idle',
                       'data': {'type': 'session.idle',
                                'properties': {'sessionID': 'ses_a'}}}]


def test_execution_failed_maps_to_error():
    b = V2EventBridge()
    frames = b.translate(_evt('session.execution.failed',
                              {'sessionID': 'ses_a',
                               'error': {'type': 'x', 'message': 'boom'}}))
    assert frames[0]['event'] == 'session.error'
    props = frames[0]['data']['properties']
    assert props['sessionID'] == 'ses_a'
    assert props['error'] == {'type': 'x', 'message': 'boom'}


def test_execution_interrupted_maps_to_idle():
    """V1 abort 后 OC 同样发 session.idle——interrupted→idle 语义闭环。"""
    b = V2EventBridge()
    frames = b.translate(_evt('session.execution.interrupted',
                              {'sessionID': 'ses_a',
                               'reason': 'user'}))
    assert frames[0]['event'] == 'session.idle'


def test_text_stream_aggregates_to_part_frames():
    """text.started/delta×2/ended → message.updated(首次) + part.updated
    （text 累计值，part id 稳定）。"""
    b = V2EventBridge()
    mid = 'msg_1'
    seq = [
        _evt('session.text.started',
             {'sessionID': 'ses_a', 'assistantMessageID': mid, 'ordinal': 0}),
        _evt('session.text.delta',
             {'sessionID': 'ses_a', 'assistantMessageID': mid,
              'ordinal': 0, 'delta': 'BRIDGE'}),
        _evt('session.text.delta',
             {'sessionID': 'ses_a', 'assistantMessageID': mid,
              'ordinal': 0, 'delta': '-OK'}),
        _evt('session.text.ended',
             {'sessionID': 'ses_a', 'assistantMessageID': mid,
              'ordinal': 0, 'text': 'BRIDGE-OK'}),
    ]
    all_frames = []
    for e in seq:
        all_frames.extend(b.translate(e))
    # 首帧必须是 message.updated（令消费方把 mid 记进 assistant_msg_ids）
    assert all_frames[0]['event'] == 'message.updated'
    info = all_frames[0]['data']['properties']['info']
    assert info == {'id': mid, 'role': 'assistant', 'sessionID': 'ses_a',
                    'time': info['time']}
    parts = [f for f in all_frames if f['event'] == 'message.part.updated']
    assert len(parts) == 4  # started(空文本) + delta×2 + ended
    pids = {f['data']['properties']['part']['id'] for f in parts}
    assert pids == {mid + '#t0'}
    texts = [f['data']['properties']['part']['text'] for f in parts]
    assert texts == ['', 'BRIDGE', 'BRIDGE-OK', 'BRIDGE-OK']


def test_tool_stream_full_lifecycle():
    """input.started→input.ended(JSON)→called→progress(metadata.sessionId
    透传)→success(content 合成 output)。"""
    b = V2EventBridge()
    mid = 'msg_2'
    cid = '2795f9d6-d46d-44da-9490-e1713f506ea4'
    seq = [
        _evt('session.tool.input.started',
             {'sessionID': 'ses_a', 'assistantMessageID': mid, 'id': cid,
              'name': 'shell'}),
        _evt('session.tool.input.ended',
             {'sessionID': 'ses_a', 'assistantMessageID': mid, 'id': cid,
              'text': '{"command": "echo hi"}'}),
        _evt('session.tool.called',
             {'sessionID': 'ses_a', 'assistantMessageID': mid, 'id': cid,
              'input': {'command': 'echo hi'}, 'executed': False}),
        _evt('session.tool.progress',
             {'sessionID': 'ses_a', 'assistantMessageID': mid, 'id': cid,
              'metadata': {'sessionID': 'ses_child', 'status': 'running'}}),
        _evt('session.tool.success',
             {'sessionID': 'ses_a', 'assistantMessageID': mid, 'id': cid,
              'content': [{'type': 'text', 'text': 'hi\n'}],
              'metadata': {'status': 'completed', 'exit': 0},
              'executed': False}),
    ]
    all_frames = []
    for e in seq:
        all_frames.extend(b.translate(e))
    parts = [f['data']['properties']['part']
             for f in all_frames if f['event'] == 'message.part.updated']
    assert parts[0]['tool'] == 'bash'          # shell→bash 归一化
    assert parts[0]['state']['status'] == 'pending'
    assert parts[-1]['state']['status'] == 'completed'
    assert parts[-1]['state']['output'] == 'hi\n'
    # 子代理发现链路的关键透传点（真机实测：progress metadata.sessionID）
    assert parts[-1]['state']['metadata'].get('sessionID') == 'ses_child'


def test_tool_subagent_input_alias():
    """subagent→task 归一化 + input.agent→input.subagent_type 别名。
    （真实序列：input.started 先行携带 name——called 事件本身无 name。）"""
    b = V2EventBridge()
    seq = [
        _evt('session.tool.input.started',
             {'sessionID': 'ses_a', 'assistantMessageID': 'msg_3',
              'id': 'c1', 'name': 'subagent'}),
        _evt('session.tool.called',
             {'sessionID': 'ses_a', 'assistantMessageID': 'msg_3',
              'id': 'c1',
              'input': {'agent': 'general', 'description': 'd',
                        'prompt': 'p'},
              'executed': True}),
    ]
    all_frames = []
    for e in seq:
        all_frames.extend(b.translate(e))
    part = all_frames[-1]['data']['properties']['part']
    assert part['tool'] == 'task'
    assert part['state']['input']['subagent_type'] == 'general'
    assert part['state']['input']['agent'] == 'general'  # 原字段保留
    assert part['state']['status'] == 'running'


def test_step_ended_emits_meta_message_updated():
    """step.ended → message.updated 携带 finish/tokens/cost/time.completed
    （meta_from_info 的输入）。"""
    b = V2EventBridge()
    frames = b.translate(_evt('session.step.ended',
                              {'sessionID': 'ses_a',
                               'assistantMessageID': 'msg_4',
                               'finish': 'stop',
                               'cost': 0.1,
                               'tokens': {'input': 10, 'output': 5}}))
    assert len(frames) == 1
    info = frames[0]['data']['properties']['info']
    assert info['finish'] == 'stop'
    assert info['cost'] == 0.1
    assert info['tokens']['output'] == 5
    assert info['time']['completed']


def test_permission_asked_without_id_degrades():
    """无 resolver 时下发无 id 事件（bypass 优雅降级，不伪造）。"""
    b = V2EventBridge()
    frames = b.translate(_evt('permission.asked',
                              {'sessionID': 'ses_a', 'action': 'shell',
                               'resources': ['rm -rf /']}))
    props = frames[0]['data']['properties']
    assert 'id' not in props
    assert props['permission'] == 'shell'
    assert props['patterns'] == ['rm -rf /']


def test_permission_asked_with_resolver():
    b = V2EventBridge(permission_id_resolver=lambda s, a: 'per_123')
    frames = b.translate(_evt('permission.asked',
                              {'sessionID': 'ses_a', 'action': 'shell',
                               'resources': []}))
    assert frames[0]['data']['properties']['id'] == 'per_123'


def test_unknown_events_pass_through_silently():
    b = V2EventBridge()
    assert b.translate(_evt('session.created', {'sessionID': 'ses_a'})) == []
    assert b.translate(_evt('session.inbox.delivered',
                            {'sessionID': 'ses_a', 'inboxID': 'x'})) == []
    assert b.translate(_evt('server.connected', {})) == []


def test_normalize_v2_tool_direct():
    p = normalize_v2_tool({'type': 'tool', 'tool': 'subagent',
                           'state': {'input': {'agent': 'explore'}}})
    assert p['tool'] == 'task'
    assert p['state']['input']['subagent_type'] == 'explore'
    # 非 tool part 原样返回
    assert normalize_v2_tool({'type': 'text', 'text': 'x'}) == \
        {'type': 'text', 'text': 'x'}
