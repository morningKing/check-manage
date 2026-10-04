"""StubRuntime 单测：消息形状对照 + 行为面（延迟/错误/卡死/取消）。
无 DB、无网络——完成器线程内联驱动（profile delay=0 时 get_messages 即时落完成）。"""
import time
from utils.runtime.stub import StubRuntime, parse_profile

PROFILE_FAST = {'delay_ms': [0, 0], 'error_rate': 0.0, 'hang_rate': 0.0}


def make_rt(**overrides):
    return StubRuntime(parse_profile({**PROFILE_FAST, **overrides}))


def test_create_and_prompt_completes_with_oc_raw_shape():
    rt = make_rt()
    c = rt.get_client()
    sid = c.create_session(directory='D:/ws', title='t')
    assert sid.startswith('stub_')
    c.send_prompt_async(sid, 'hello', directory='D:/ws')
    time.sleep(0.05)                      # 完成器线程落完成
    msgs = c.get_messages(sid)
    assert msgs[0]['info']['role'] == 'user'
    a = [m for m in msgs if m['info']['role'] == 'assistant']
    assert a and a[0]['info']['finish'] == 'stop'          # 终结 reason
    assert a[0]['info']['time']['completed'] > 0            # 完成时间戳
    assert isinstance(a[0]['parts'], list) and a[0]['parts']


def test_prompt_before_completion_has_no_assistant():
    rt = StubRuntime(parse_profile({'delay_ms': [60_000, 60_000]}))
    c = rt.get_client()
    sid = c.create_session(directory='D:/ws')
    c.send_prompt_async(sid, 'hello')
    msgs = c.get_messages(sid)
    assert [m['info']['role'] for m in msgs] == ['user']


def test_error_rate_lands_failed_shape():
    rt = make_rt(error_rate=1.0)
    c = rt.get_client()
    sid = c.create_session(directory='D:/ws')
    c.send_prompt_async(sid, 'x')
    time.sleep(0.05)
    a = [m for m in c.get_messages(sid) if m['info']['role'] == 'assistant']
    assert a[0]['info']['finish'] == 'error'               # worker 判 failed 的形状


def test_abort_lands_terminal_shape():
    rt = StubRuntime(parse_profile({'delay_ms': [60_000, 60_000]}))
    c = rt.get_client()
    sid = c.create_session(directory='D:/ws')
    c.send_prompt_async(sid, 'x')
    c.abort_session(sid)
    a = [m for m in c.get_messages(sid) if m['info']['role'] == 'assistant']
    assert a and a[0]['info']['finish'] not in ('', None, 'tool-calls')


def test_hang_keeps_running_then_recovers():
    rt = StubRuntime(parse_profile({'delay_ms': [50, 50], 'hang_rate': 1.0,
                                    'hang_recover_after_ms': 150}))
    c = rt.get_client()
    sid = c.create_session(directory='D:/ws')
    c.send_prompt_async(sid, 'x')
    time.sleep(0.1)
    assert not [m for m in c.get_messages(sid) if m['info']['role'] == 'assistant']
    time.sleep(0.2)
    assert [m for m in c.get_messages(sid) if m['info']['role'] == 'assistant']


def test_health_and_capabilities():
    rt = make_rt()
    assert rt.health().get('ok') is True
    assert 'stub' in rt.capabilities().get('kind', '')
