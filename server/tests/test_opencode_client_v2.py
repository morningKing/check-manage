"""opencode_client_v2 单测：mock HTTP 验证请求形状与响应翻译。

契约以 v2.2.6 真机实测为准（{data} 包装、location[directory] query、
prompt body、投影消息形状、interrupt/compact/command 映射）。
"""
import json
from unittest.mock import patch, MagicMock

import pytest

from utils.opencode_client_v2 import OpenCodeClientV2


def _resp(payload, status=200):
    r = MagicMock()
    r.status_code = status
    r.ok = status < 400
    r.json.return_value = payload
    r.raise_for_status = MagicMock()
    return r


def test_create_session_posts_location_and_unwraps_data():
    with patch('utils.opencode_client_v2.requests.post',
               return_value=_resp({'data': {'id': 'ses_42'}})) as post:
        c = OpenCodeClientV2('http://127.0.0.1:4196')
        sid = c.create_session(directory='C:/ws', title='t')
    assert sid == 'ses_42'
    args, kwargs = post.call_args
    assert args[0].endswith('/api/session')
    assert kwargs['params'] == {'location[directory]': 'C:/ws'}
    assert kwargs['json']['location'] == {'directory': 'C:/ws'}
    assert kwargs['json']['title'] == 't'


def test_send_prompt_body_uses_v2_promptinput_fields():
    with patch('utils.opencode_client_v2.requests.post',
               return_value=_resp({'data': {}})) as post:
        OpenCodeClientV2('http://127.0.0.1:4196').send_prompt_async(
            'ses_42', 'hello', model='prov/m1', agent='builder',
            agent_parts=[{'name': 'rev', 'source': {'value': '@rev',
                                                    'start': 0, 'end': 4}}],
            image_parts=[{'name': 'p', 'mime': 'image/png',
                          'url': 'data:image/png;base64,xx'}])
    args, kwargs = post.call_args
    assert args[0].endswith('/api/session/ses_42/prompt')
    body = kwargs['json']
    assert body['text'] == 'hello'
    assert body['model'] == {'providerID': 'prov', 'id': 'm1'}
    assert body['agent'] == 'builder'
    assert body['files'] == [{'uri': 'data:image/png;base64,xx',
                              'name': 'p'}]
    assert body['agents'] == [{'name': 'rev',
                               'mention': {'start': 0, 'end': 4,
                                           'text': '@rev'}}]


def test_abort_maps_to_interrupt():
    with patch('utils.opencode_client_v2.requests.post',
               return_value=_resp({'interrupted': True})) as post:
        OpenCodeClientV2('http://127.0.0.1:4196').abort_session('ses_42')
    args, kwargs = post.call_args
    assert args[0].endswith('/api/session/ses_42/interrupt')
    assert kwargs['json'] == {'resume': False}


def test_get_messages_translates_projection():
    """V2 投影 assistant 消息（真机实测形状）→ V1 {info, parts}。"""
    v2 = {'data': [
        {'type': 'user', 'id': 'msg_u1', 'text': 'q',
         'time': {'created': 1000}},
        {'type': 'assistant', 'id': 'msg_a1', 'agent': 'build',
         'model': {'providerID': 'opencode', 'id': 'm1'},
         'finish': 'stop', 'cost': 0.2,
         'tokens': {'input': 5, 'output': 1}, 'error': None,
         'time': {'created': 2000, 'streamed': 2100, 'completed': 2200},
         'content': [
             {'type': 'text', 'text': 'BRIDGE-OK-42'},
             {'type': 'tool', 'id': 'c1', 'name': 'shell',
              'state': {'status': 'completed',
                        'input': {'command': 'echo hi'},
                        'content': [{'type': 'text', 'text': 'hi\n'}],
                        'metadata': {'sessionID': 'ses_child',
                                     'status': 'completed'}},
              'time': {'created': 2100}},
         ]},
        {'type': 'idle', 'id': 'msg_i1'},   # 真机实测：idle 型消息跳过
    ], 'cursor': {'next': None}}
    with patch('utils.opencode_client_v2.requests.get',
               return_value=_resp(v2)) as get:
        out = OpenCodeClientV2('http://127.0.0.1:4196').get_messages('ses_42')
    args, kwargs = get.call_args
    assert args[0].endswith('/api/session/ses_42/message')
    assert kwargs['params']['order'] == 'asc'
    assert len(out) == 2
    info, parts = out[1]['info'], out[1]['parts']
    assert info['role'] == 'assistant'
    assert info['finish'] == 'stop'
    assert info['time']['completed'] == 2200
    assert info['providerID'] == 'opencode'
    assert info['modelID'] == 'm1'
    text_part, tool_part = parts
    assert text_part['type'] == 'text'
    assert tool_part['type'] == 'tool'
    assert tool_part['tool'] == 'bash'                     # shell→bash
    assert tool_part['state']['status'] == 'completed'
    assert tool_part['state']['output'] == 'hi\n'
    assert tool_part['state']['metadata']['sessionID'] == 'ses_child'
    assert tool_part['state']['input']['subagent_type' if False else
                       'command'] == 'echo hi'


def test_get_messages_pagination_cursor_only():
    """V2 游标内嵌排序状态（真机实测）：带 cursor 再传 limit/order 直接
    400——翻页请求只带 cursor，空页即停。"""
    page1 = {'data': [
        {'type': 'user', 'id': 'msg_u1', 'text': 'q',
         'time': {'created': 1000}},
    ], 'cursor': {'next': 'eyJpZCI6Im1zZyJ9'}}
    page2 = {'data': [
        {'type': 'assistant', 'id': 'msg_a1', 'agent': 'build',
         'model': {'providerID': 'opencode', 'id': 'm1'},
         'finish': 'stop', 'cost': 0, 'tokens': {}, 'error': None,
         'time': {'created': 2000, 'completed': 2100}, 'content': []},
    ], 'cursor': {'next': 'eyJpZCI6Im1zZyJ9'}}  # 服务端总回 cursor（空页）
    empty = {'data': [], 'cursor': {'next': None}}
    with patch('utils.opencode_client_v2.requests.get',
               side_effect=[_resp(page1), _resp(page2), _resp(empty)]) as get:
        out = OpenCodeClientV2('http://127.0.0.1:4196').get_messages('ses_42')
    assert len(out) == 2
    calls = get.call_args_list
    assert calls[0].kwargs['params'] == {'limit': '200', 'order': 'asc'}
    assert calls[1].kwargs['params'] == {'cursor': 'eyJpZCI6Im1zZyJ9'}
    assert calls[1].args[0].endswith('/message')


def test_revert_stage_wraps_v2_response_into_v1_shape():
    """V2 stage 真机实测响应 {messageID, files}——无 V1 'revert' 键。
    适配成 {'revert': {...}}：undo 编排器 REVERT_MISMATCH 校验读
    ['revert']['messageID']，零改动工作。"""
    with patch('utils.opencode_client_v2.requests.post',
               return_value=_resp({'data': {'messageID': 'msg_u1',
                                            'files': ['a.txt']}})) as post:
        out = OpenCodeClientV2('http://127.0.0.1:4196').revert_stage(
            'ses_42', 'msg_u1')
    args, _ = post.call_args
    assert args[0].endswith('/api/session/ses_42/revert/stage')
    assert out == {'revert': {'messageID': 'msg_u1', 'files': ['a.txt']}}


def test_list_agents_maps_id_to_name():
    v2 = {'location': {}, 'data': [
        {'id': 'build', 'mode': 'primary', 'hidden': False},
        {'id': 'compaction', 'mode': 'primary', 'hidden': True},
        {'id': 'general', 'mode': 'subagent', 'hidden': False},
    ]}
    with patch('utils.opencode_client_v2.requests.get',
               return_value=_resp(v2)):
        out = OpenCodeClientV2('http://127.0.0.1:4196').list_agents()
    names = {a['name']: a for a in out}
    assert names['build']['mode'] == 'primary'
    assert names['compaction']['hidden'] is True
    assert names['general']['mode'] == 'subagent'


def test_list_providers_synth_v1_shape():
    prov = {'location': {}, 'data': [
        {'id': 'p1', 'name': 'P1', 'activation': 'enabled'},
        {'id': 'p2', 'name': 'P2', 'activation': 'disabled'},
    ]}
    models = {'location': {}, 'data': [
        {'providerID': 'p1', 'id': 'm1', 'name': 'M1'},
    ]}
    dflt = {'data': {'providerID': 'p1', 'id': 'm1'}}
    with patch('utils.opencode_client_v2.requests.get',
               side_effect=[_resp(prov), _resp(models), _resp(dflt)]):
        out = OpenCodeClientV2('http://127.0.0.1:4196').list_providers()
    assert [p['id'] for p in out['all']] == ['p1', 'p2']
    assert out['all'][0]['models']['m1']['name'] == 'M1'
    assert out['connected'] == {'p1': True, 'p2': False}
    assert out['default'] == {'p1': 'm1'}


def test_list_mcp_maps_status():
    v2 = {'location': {}, 'data': [
        {'name': 'check-manage', 'status': {'status': 'connected'}},
    ]}
    with patch('utils.opencode_client_v2.requests.get',
               return_value=_resp(v2)):
        out = OpenCodeClientV2('http://127.0.0.1:4196').list_mcp('C:/ws')
    assert out == {'check-manage': {'status': 'connected'}}


def test_list_questions_synths_v1_shape_with_session_id():
    v2 = {'location': {}, 'data': [
        {'id': 'frm_1', 'sessionID': 'ses_9',
         'fields': [{'key': 'mode', 'title': 'Pick', 'type': 'string',
                     'options': [{'value': 'fast', 'label': 'Fast'},
                                 {'value': 'slow', 'label': 'Slow'}]}]},
    ]}
    with patch('utils.opencode_client_v2.requests.get',
               return_value=_resp(v2)):
        out = OpenCodeClientV2('http://127.0.0.1:4196').list_questions()
    q = out[0]
    assert q['id'] == 'frm_1'
    assert q['sessionID'] == 'ses_9'
    assert q['questions'][0]['options'][0] == {'label': 'fast',
                                               'description': ''}


def test_reply_question_requires_session_id():
    c = OpenCodeClientV2('http://127.0.0.1:4196')
    with pytest.raises(ValueError):
        c.reply_question('frm_1', [['fast']])


def test_reply_question_maps_labels_to_option_values():
    forms = {'location': {}, 'data': [
        {'id': 'frm_1', 'sessionID': 'ses_9',
         'fields': [{'key': 'mode', 'title': 'Pick', 'type': 'string',
                     'options': [{'value': 'fast', 'label': 'Fast'}]}]},
    ]}
    with patch('utils.opencode_client_v2.requests.get',
               return_value=_resp(forms)) as get, \
            patch('utils.opencode_client_v2.requests.request',
                  return_value=_resp({'ok': True})) as req:
        OpenCodeClientV2('http://127.0.0.1:4196').reply_question(
            'frm_1', [['Fast']], oc_session_id='ses_9')
    args, kwargs = req.call_args
    assert args[0] == 'POST'
    assert args[1].endswith('/api/session/ses_9/form/frm_1/reply')
    assert kwargs['json'] == {'answer': {'mode': 'fast'}}


def test_reject_question_uses_session_scoped_delete():
    with patch('utils.opencode_client_v2.requests.request',
               return_value=_resp({'ok': True})) as req:
        OpenCodeClientV2('http://127.0.0.1:4196').reject_question(
            'frm_1', oc_session_id='ses_9')
    args, kwargs = req.call_args
    assert args[0] == 'DELETE'
    assert args[1].endswith('/api/session/ses_9/form/frm_1')
    assert kwargs['params'] == {'message': 'rejected by platform'}


def test_reply_permission_decision_body():
    with patch('utils.opencode_client_v2.requests.request',
               return_value=_resp({'ok': True})) as req:
        OpenCodeClientV2('http://127.0.0.1:4196').reply_permission(
            'per_1', 'once', oc_session_id='ses_9')
    args, kwargs = req.call_args
    assert args[0] == 'POST'
    assert args[1].endswith('/api/session/ses_9/permission/per_1/reply')
    assert kwargs['json'] == {'decision': 'once'}


def test_reply_permission_rejects_bad_ids():
    c = OpenCodeClientV2('http://127.0.0.1:4196')
    with pytest.raises(ValueError):
        c.reply_permission('../etc', 'once', oc_session_id='ses_9')
    with pytest.raises(ValueError):
        c.reply_permission('per_1', 'once', oc_session_id='')


def test_list_permissions_maps_resources_to_patterns():
    v2 = {'location': {}, 'data': [
        {'id': 'per_1', 'sessionID': 'ses_9', 'action': 'external_directory',
         'resources': ['C:/x/**'], 'metadata': {'filepath': 'C:/x/f'},
         'source': {'type': 'tool', 'messageID': 'm', 'id': 'c'}},
    ]}
    with patch('utils.opencode_client_v2.requests.get',
               return_value=_resp(v2)):
        out = OpenCodeClientV2('http://127.0.0.1:4196').list_permissions()
    p = out[0]
    assert p['patterns'] == ['C:/x/**']
    assert p['permission'] == 'external_directory'
    assert p['tool'] == 'tool'


def test_lsp_and_formatters_degrade_empty():
    c = OpenCodeClientV2('http://127.0.0.1:4196')
    assert c.list_lsp() == []
    assert c.list_formatters() == []


def test_auth_header_when_password():
    c = OpenCodeClientV2('http://127.0.0.1:4196', password='pw')
    assert c._headers()['Authorization'].startswith('Basic ')
    bare = OpenCodeClientV2('http://127.0.0.1:4196')
    assert bare._headers() == {}
