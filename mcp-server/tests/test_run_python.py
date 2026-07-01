"""Tests for tools.run_python."""

import os
from unittest.mock import patch, MagicMock
from contextlib import contextmanager
import pytest


def _ctx(role="developer"):
    from context import ToolContext
    return ToolContext(session_id="s1", user_id="u1", role=role)


def _fake_db(workspace):
    cur = MagicMock()
    cur.fetchone.return_value = (workspace,)
    conn = MagicMock(); conn.cursor.return_value = cur
    @contextmanager
    def _get():
        yield conn
    return _get


def test_runs_code_and_captures_stdout(tmp_path):
    with patch('tools.run_python.get_db', _fake_db(str(tmp_path))):
        from tools.run_python import handle
        res = handle({'code': 'print("hello from script")'}, _ctx())
    assert res['exit_code'] == 0
    assert 'hello from script' in res['stdout']
    assert res['output_files'] == []


def test_reports_files_written_to_outputs(tmp_path):
    code = (
        "import os\n"
        "os.makedirs('outputs', exist_ok=True)\n"
        "open('outputs/result.txt','w').write('done')\n"
        "print('wrote file')\n"
    )
    with patch('tools.run_python.get_db', _fake_db(str(tmp_path))):
        from tools.run_python import handle
        res = handle({'code': code}, _ctx())
    assert res['exit_code'] == 0
    assert 'outputs/result.txt' in res['output_files']
    assert (tmp_path / 'outputs' / 'result.txt').read_text() == 'done'


def test_captures_stderr_on_error(tmp_path):
    with patch('tools.run_python.get_db', _fake_db(str(tmp_path))):
        from tools.run_python import handle
        res = handle({'code': 'raise ValueError("boom")'}, _ctx())
    assert res['exit_code'] != 0
    assert 'boom' in res['stderr']


def test_guest_blocked(tmp_path):
    from tools.run_python import handle, RunPythonError
    with pytest.raises(RunPythonError):
        handle({'code': 'print(1)'}, _ctx('guest'))


def test_kefu_guest_blocked(tmp_path):
    from tools.run_python import handle, RunPythonError
    with pytest.raises(RunPythonError):
        handle({'code': 'print(1)'}, _ctx('kefu-guest'))


def test_requires_code(tmp_path):
    with patch('tools.run_python.get_db', _fake_db(str(tmp_path))):
        from tools.run_python import handle, RunPythonError
        with pytest.raises(RunPythonError):
            handle({}, _ctx())


def test_interpreter_has_pandas_and_openpyxl(tmp_path):
    with patch('tools.run_python.get_db', _fake_db(str(tmp_path))):
        from tools.run_python import handle
        res = handle({'code': 'import pandas, openpyxl; print("ok", pandas.__version__)'}, _ctx())
    assert res['exit_code'] == 0, res['stderr']
    assert 'ok' in res['stdout']


def test_produces_xlsx_via_pandas(tmp_path):
    code = (
        "import pandas as pd, os\n"
        "os.makedirs('outputs', exist_ok=True)\n"
        "pd.DataFrame({'a':[1,2],'b':[3,4]}).to_excel('outputs/data.xlsx', index=False)\n"
        "print('done')\n"
    )
    with patch('tools.run_python.get_db', _fake_db(str(tmp_path))):
        from tools.run_python import handle
        res = handle({'code': code}, _ctx())
    assert res['exit_code'] == 0, res['stderr']
    assert 'outputs/data.xlsx' in res['output_files']
    assert (tmp_path / 'outputs' / 'data.xlsx').exists()


def test_interpreter_override_env(tmp_path, monkeypatch):
    from tools import run_python
    monkeypatch.setenv('RUN_PYTHON_EXECUTABLE', '/custom/python')
    assert run_python._interpreter() == '/custom/python'
    monkeypatch.delenv('RUN_PYTHON_EXECUTABLE')
    import sys as _sys
    assert run_python._interpreter() == _sys.executable
