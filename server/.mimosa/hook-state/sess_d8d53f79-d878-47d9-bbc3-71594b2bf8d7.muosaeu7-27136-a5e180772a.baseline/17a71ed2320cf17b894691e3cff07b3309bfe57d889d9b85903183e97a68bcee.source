"""Tests for utils.py_runner (user-triggered script execution)."""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.py_runner import run_python_in_workspace, _interpreter


def test_runs_and_captures_stdout(tmp_path):
    res = run_python_in_workspace('print("hi runner")', str(tmp_path))
    assert res['exitCode'] == 0
    assert 'hi runner' in res['stdout']
    assert res['outputFiles'] == []


def test_reports_new_output_files(tmp_path):
    code = (
        "import os\n"
        "os.makedirs('outputs', exist_ok=True)\n"
        "open('outputs/r.txt','w').write('ok')\n"
    )
    res = run_python_in_workspace(code, str(tmp_path))
    assert res['exitCode'] == 0
    assert 'outputs/r.txt' in res['outputFiles']
    assert (tmp_path / 'outputs' / 'r.txt').read_text() == 'ok'


def test_produces_xlsx_via_pandas(tmp_path):
    code = (
        "import pandas as pd, os\n"
        "os.makedirs('outputs', exist_ok=True)\n"
        "pd.DataFrame({'a':[1,2]}).to_excel('outputs/d.xlsx', index=False)\n"
    )
    res = run_python_in_workspace(code, str(tmp_path))
    assert res['exitCode'] == 0, res['stderr']
    assert 'outputs/d.xlsx' in res['outputFiles']


def test_captures_error(tmp_path):
    res = run_python_in_workspace('raise ValueError("boom")', str(tmp_path))
    assert res['exitCode'] != 0
    assert 'boom' in res['stderr']


def test_strips_leading_bare_filename_line(tmp_path):
    # Some-model quirk: first code line is just the filename → would NameError verbatim
    code = "squares.py\nprint('after filename line')\n"
    res = run_python_in_workspace(code, str(tmp_path))
    assert res['exitCode'] == 0, res['stderr']
    assert 'after filename line' in res['stdout']


def test_interpreter_env_override(monkeypatch):
    monkeypatch.setenv('RUN_PYTHON_EXECUTABLE', '/x/py')
    assert _interpreter() == '/x/py'
    monkeypatch.delenv('RUN_PYTHON_EXECUTABLE')
    assert _interpreter() == sys.executable
