"""Tests for proxy.py startup diagnostics.

Regression guard for "proxy.py can't bring up the backend with no error": the
managed subprocesses now log to a file (not /dev/null) and a child that dies on
startup is reported loudly instead of swallowed.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import proxy


def test_read_tail_returns_last_n_lines(tmp_path):
    p = tmp_path / 'x.log'
    p.write_text('\n'.join(f'line{i}' for i in range(100)), encoding='utf-8')
    tail = proxy._read_tail(str(p), n=5)
    assert tail.strip().splitlines() == ['line95', 'line96', 'line97', 'line98', 'line99']


def test_read_tail_missing_file_is_empty():
    assert proxy._read_tail(os.path.join(os.path.dirname(__file__), 'no_such.log')) == ''


class _FakeProc:
    def __init__(self, rc):
        self._rc = rc
        self.returncode = rc

    def poll(self):
        return self._rc


def test_report_dead_subprocess_running_is_silent(capsys):
    # poll() is None => still running => no diagnostic, returns False
    assert proxy._report_dead_subprocess('Backend', _FakeProc(None), 'whatever.log') is False
    assert capsys.readouterr().out == ''


def test_report_dead_subprocess_exited_prints_diagnostic(tmp_path, capsys):
    log = tmp_path / 'b.log'
    log.write_text("Traceback ...\nModuleNotFoundError: No module named 'waitress'\n",
                   encoding='utf-8')
    dead = proxy._report_dead_subprocess('Backend', _FakeProc(1), str(log))
    out = capsys.readouterr().out
    assert dead is True
    assert 'exit code 1' in out
    assert 'waitress' in out                      # the real cause from the log tail
    assert 'pip install -r requirements.txt' in out  # the actionable fix
    assert str(log) in out                        # points at the full log


def test_report_dead_subprocess_exited_without_log(tmp_path, capsys):
    # Missing log file must not crash; still reports exit code + hint.
    dead = proxy._report_dead_subprocess('MCP server', _FakeProc(2), str(tmp_path / 'absent.log'))
    out = capsys.readouterr().out
    assert dead is True
    assert 'exit code 2' in out
    assert 'pip install -r requirements.txt' in out
