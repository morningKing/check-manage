"""Batch completion detection (regression for the idle-race bug).

The worker used to detect a finished turn by catching OpenCode's one-shot
`session.idle` event inside short, intermittent subscription windows; if idle
fired in the gap between windows (common under concurrency) it was missed and
the child hung until the 30-min timeout. The fix derives completion from the
REST message state instead: a turn is finished when the latest assistant
message has a terminal `finish` reason (anything other than 'tool-calls').
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import utils.batch_engine as eng


class _FakeClient:
    def __init__(self, msgs):
        self._msgs = msgs

    def get_messages(self, oc_session_id, directory=''):
        return self._msgs


def _facade(msgs, monkeypatch):
    f = eng._OpenCodeFacade()
    monkeypatch.setattr(f, '_client', lambda: _FakeClient(msgs))
    return f


def _amsg(finish, completed, text):
    """An OpenCode assistant message in REST shape."""
    return {
        'info': {'role': 'assistant', 'finish': finish,
                 'time': {'created': 1, 'completed': completed}},
        'parts': [
            {'type': 'step-start'},
            {'type': 'reasoning', 'text': 'thinking...'},
            {'type': 'text', 'text': text},
            {'type': 'step-finish'},
        ],
    }


def test_tool_calls_step_is_not_finished(monkeypatch):
    # Intermediate step (agent will run a tool and continue) -> NOT finished.
    f = _facade([_amsg('tool-calls', 123, '')], monkeypatch)
    out = f.list_messages('s')
    assert out[-1]['finished'] is False


def test_stop_step_is_finished_with_text(monkeypatch):
    # Multi-step turn: tool-calls step then final 'stop' step -> finished, with text.
    f = _facade([_amsg('tool-calls', 1, ''), _amsg('stop', 2, '最终答案')], monkeypatch)
    out = f.list_messages('s')
    last = [m for m in out if m['role'] == 'assistant'][-1]
    assert last['finished'] is True
    assert any(p.get('type') == 'text' and p.get('text') == '最终答案'
               for p in last['content'])


def test_streaming_message_not_finished(monkeypatch):
    # Still generating: no completion timestamp, no finish reason -> NOT finished.
    f = _facade([_amsg(None, None, '')], monkeypatch)
    out = f.list_messages('s')
    assert out[-1]['finished'] is False


def test_request_error_returns_not_finished(monkeypatch):
    # A transient REST blip must not fail the child; report "not finished" so the
    # poll loop retries (persistent failure still hits the timeout -> failed).
    import requests

    class _BoomClient:
        def get_messages(self, oc_session_id, directory=''):
            raise requests.RequestException('boom')

    f = eng._OpenCodeFacade()
    monkeypatch.setattr(f, '_client', lambda: _BoomClient())
    out = f.list_messages('s')
    assert out == [{'role': 'assistant', 'finished': False, 'content': []}]


def test_progress_signature_changes_on_text_growth():
    sig = eng.BatchWorker._progress_signature
    a = [{'role': 'assistant', 'finished': False,
          'content': [{'type': 'text', 'text': 'hi'}]}]
    b = [{'role': 'assistant', 'finished': False,
          'content': [{'type': 'text', 'text': 'hi there'}]}]
    assert sig(a) != sig(b)          # text grew -> progress
    assert sig(a) == sig(a)          # frozen -> no progress


def test_await_finished_stalls_fast_not_session_timeout(monkeypatch):
    """A frozen, never-finishing turn must fail via the STALL watchdog quickly,
    not hang for the full SESSION_TIMEOUT_SEC."""
    from unittest.mock import MagicMock
    import time

    fake = MagicMock()
    fake.list_messages.return_value = [
        {'role': 'assistant', 'finished': False,
         'content': [{'type': 'text', 'text': 'half-written'}]}
    ]
    monkeypatch.setattr(eng, 'opencode_client', fake)

    w = eng.BatchWorker()
    w.SESSION_TIMEOUT_SEC = 30     # must NOT be what fires
    w.STALL_TIMEOUT_SEC = 0.5      # stall trips fast
    w.POLL_INTERVAL_SEC = 0.1

    t0 = time.time()
    raised = None
    try:
        w._await_finished('oc-x')
    except eng._SessionTimeout as e:
        raised = e
    elapsed = time.time() - t0

    assert raised is not None
    assert 'stall' in str(raised).lower()
    assert elapsed < 10            # failed via stall, not the 30s session timeout
