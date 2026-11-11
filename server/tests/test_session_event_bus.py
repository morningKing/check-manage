"""跨连接平台事件总线（undo-redo spec §2.6）。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.session_event_bus import register, unregister, broadcast


def test_broadcast_fans_out_only_to_session_subscribers():
    q1, q2 = register('s1'), register('s1')
    q3 = register('s2')
    try:
        assert broadcast('s1', 'session.revert', {'revert': None}) == 2
        f1 = q1.get_nowait()
        assert f1['event'] == 'session.revert'
        assert f1['data']['properties'] == {'revert': None}
        assert q2.get_nowait()['data']['type'] == 'session.revert'
        assert q3.empty()                      # 不串会话
    finally:
        unregister('s1', q1); unregister('s1', q2); unregister('s2', q3)


def test_unregister_stops_delivery_and_broadcast_ok_when_no_subscriber():
    q = register('s9')
    unregister('s9', q)
    assert broadcast('s9', 'session.revert', {}) == 0   # 无订阅者不报错
    assert broadcast('s404', 'session.revert', {}) == 0
