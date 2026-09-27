import queue
from utils import kefu_event_bus as bus


def test_publish_delivers_to_subscribers_of_that_sid():
    q1 = bus.subscribe('s1')
    q2 = bus.subscribe('s1')
    qx = bus.subscribe('other')
    bus.publish('s1', {'type': 'takeover'})
    assert q1.get_nowait() == {'type': 'takeover'}
    assert q2.get_nowait() == {'type': 'takeover'}
    assert qx.empty()
    bus.unsubscribe('s1', q1); bus.unsubscribe('s1', q2); bus.unsubscribe('other', qx)


def test_publish_no_subscribers_is_noop():
    bus.publish('nobody', {'type': 'release'})   # must not raise


def test_full_queue_drops_without_raising():
    q = bus.subscribe('sfull')
    for _ in range(bus._MAX_Q + 5):
        bus.publish('sfull', {'type': 'human_message'})   # must not raise
    assert q.qsize() == bus._MAX_Q
    bus.unsubscribe('sfull', q)


def test_unsubscribe_removes_and_cleans_empty_sid():
    q = bus.subscribe('sclean')
    assert 'sclean' in bus._subscribers
    bus.unsubscribe('sclean', q)
    assert 'sclean' not in bus._subscribers
