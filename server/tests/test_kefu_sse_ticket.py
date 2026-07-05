import time
from utils import kefu_sse_ticket as tk


def test_issue_then_consume_returns_user():
    t = tk.issue('user-admin')
    assert isinstance(t, str) and t
    assert tk.consume(t) == 'user-admin'


def test_consume_is_one_time():
    t = tk.issue('u1')
    assert tk.consume(t) == 'u1'
    assert tk.consume(t) is None


def test_unknown_ticket_returns_none():
    assert tk.consume('nope') is None


def test_expired_ticket_returns_none(monkeypatch):
    t = tk.issue('u2')
    # Capture the future timestamp before patching: tk.time is the same
    # singleton module object as this file's `import time`, so patching
    # tk.time.time also replaces this file's time.time — calling
    # time.time() *inside* the lambda body would recurse into itself.
    future = time.time() + tk._TTL + 1
    monkeypatch.setattr(tk.time, 'time', lambda: future)
    assert tk.consume(t) is None
