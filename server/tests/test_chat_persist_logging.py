"""The persistence listener used to swallow crashes (except: pass), which is why
a stuck session left no trace. These guard that failures now get logged."""

import logging
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import utils.chat_persist as cp


def test_listener_thread_logs_crash_and_cleans_registry(caplog):
    # Task 3 接线：_listener_thread 经 get_runtime().subscribe_events 取事件源，
    # 打桩点从模块级 OpenCodeClient 迁到 cp.get_runtime。
    fake_runtime = MagicMock()
    fake_runtime.subscribe_events.side_effect = RuntimeError('boom')
    with patch.object(cp, 'get_runtime', return_value=fake_runtime):
        with caplog.at_level(logging.ERROR, logger='utils.chat_persist'):
            cp._listener_thread('sess_crash', 'oc1', '/ws')
    assert any('persist listener crashed' in r.getMessage()
               and 'sess_crash' in r.getMessage() for r in caplog.records)
    # the thread removes itself from the registry on exit
    assert 'sess_crash' not in cp._listeners


def test_persist_turn_logs_db_error(caplog):
    state = cp.new_state()
    state['part_order'] = ['p1']
    state['parts_by_id'] = {'p1': {'type': 'text', 'text': 'hi'}}
    state['turn_msg_id'] = 'm1'

    def boom(*a, **k):
        raise RuntimeError('db down')

    with patch.object(cp, 'get_db', boom):
        with caplog.at_level(logging.WARNING, logger='utils.chat_persist'):
            cp.persist_turn('sess_db', state)  # must not raise
    assert any('persist_turn DB error' in r.getMessage()
               and 'sess_db' in r.getMessage() for r in caplog.records)
