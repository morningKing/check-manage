"""The persistence listener used to swallow crashes (except: pass), which is why
a stuck session left no trace. These guard that failures now get logged."""

import logging
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import utils.chat_persist as cp


def test_listener_thread_logs_crash_and_cleans_registry(caplog):
    fake_client = MagicMock()
    fake_client.subscribe_events.side_effect = RuntimeError('boom')
    with patch.object(cp, 'OpenCodeClient', return_value=fake_client):
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
