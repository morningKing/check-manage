"""StubRuntime 单测：消息形状对照 + 行为面（延迟/错误/卡死/取消）。
无 DB、无网络——完成器线程内联驱动（profile delay=0 时 get_messages 即时落完成）。"""
import threading
import time

import pytest

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
    # info.id 必须存在且逐轮唯一：worker continue 模式的基线快照
    # （_snapshot_assistant_ids）靠它区分旧回合消息——缺 id 时基线恒空集，
    # 旧终态消息被判为本轮完成，修正轮（gate-retry）0ms 即时假完成
    # （2026-10-05 S3b 能力压测实证：attempt 仅存活 24ms）。
    assert a[0]['info']['id'] and str(a[0]['info']['id']).startswith('msg_')


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
    assert a[0]['info']['time']['completed'] > 0            # finish+completed 须同时成立


def test_second_dispatch_same_session_lands_second_terminal():
    rt = make_rt()
    c = rt.get_client()
    sid = c.create_session(directory='D:/ws')
    c.send_prompt_async(sid, 'round1')
    time.sleep(0.05)
    a1 = [m for m in c.get_messages(sid) if m['info']['role'] == 'assistant']
    assert len(a1) == 1 and a1[0]['info']['finish'] == 'stop'
    c.send_prompt_async(sid, 'round2')                      # 同会话二次派发
    time.sleep(0.05)
    msgs = c.get_messages(sid)
    a = [m for m in msgs if m['info']['role'] == 'assistant']
    assert len(a) == 2                                      # 第二条 assistant 落地
    assert a[1]['info']['finish'] == 'stop'
    assert a[1]['info']['time']['completed'] > a[0]['info']['time']['completed']


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


def test_get_runtime_concurrent_first_call_single_instance(monkeypatch):
    """≥16 线程 barrier 后同时首调 get_runtime()：全部必须拿到同一单例。

    缺陷锁定：懒初始化无锁时，多线程首调竞态会各构造一个 StubRuntime，
    两个独立内存会话字典脑裂——A 实例 create_session、B 实例 get_messages
    抛 KeyError，子任务被误判 failed。StubClient 自身字典操作已在锁内，
    脑裂只可能发生在 get_runtime 单例层。
    """
    import utils.runtime as rtmod
    monkeypatch.setenv('AI_AGENT_RUNTIME', 'stub')
    monkeypatch.setenv('AI_STUB_ALLOW', '1')                # 防呆门显式放行
    monkeypatch.delenv('AI_STUB_PROFILE', raising=False)   # 默认 profile 即可
    monkeypatch.setattr(rtmod, '_default', None)            # 强制走懒初始化竞态窗口
    n = 16
    barrier = threading.Barrier(n)
    results = [None] * n

    def worker(i):
        barrier.wait()
        results[i] = rtmod.get_runtime()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    first = results[0]
    assert isinstance(first, StubRuntime)
    assert all(r is first for r in results)                 # 同一 runtime 单例
    assert all(r.get_client() is first.get_client()
               for r in results)                            # 同一内存会话字典


def test_get_runtime_stub_requires_explicit_allow(monkeypatch):
    """防呆（压测终审 M8）：AI_AGENT_RUNTIME=stub 未带 AI_STUB_ALLOW=1 必须
    拒绝——stub 会假成功所有 agent 任务，误配到生产等于 AI 全体放假。"""
    import utils.runtime as rtmod
    from utils.runtime.base import RuntimeCapabilityError
    monkeypatch.setenv('AI_AGENT_RUNTIME', 'stub')
    monkeypatch.delenv('AI_STUB_ALLOW', raising=False)
    monkeypatch.setattr(rtmod, '_default', None)
    with pytest.raises(RuntimeCapabilityError, match='AI_STUB_ALLOW'):
        rtmod.get_runtime()
    assert rtmod._default is None                           # 拒绝后不留半初始化单例
