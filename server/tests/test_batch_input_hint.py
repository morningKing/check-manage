"""Regular batch children must be told where their uploaded input file is, so the
agent reads it instead of asking for a path. Scan children already carry their own
context preamble (ai_scan_engine.assemble_prompt) and must be left untouched."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import utils.batch_engine as eng


def test_input_hint_added_for_regular_child():
    sr = {'scan_task_id': None, 'batch_input_file': 'batch-staging/u/x/report.txt'}
    out = eng.BatchWorker._with_input_hint('总结这个文件', sr)
    assert 'uploads/report.txt' in out          # points the agent at the file
    assert out.rstrip().endswith('总结这个文件')   # original prompt preserved at the end


def test_input_hint_skipped_for_scan_child():
    sr = {'scan_task_id': 'scan-1', 'batch_input_file': 'scan-staging/t/r/st-1'}
    assert eng.BatchWorker._with_input_hint('p', sr) == 'p'


def test_input_hint_skipped_when_no_file():
    sr = {'scan_task_id': None, 'batch_input_file': None}
    assert eng.BatchWorker._with_input_hint('p', sr) == 'p'


def test_input_hint_handles_windows_path_and_unicode():
    sr = {'scan_task_id': None, 'batch_input_file': 'batch-staging\\u\\x\\日报.txt'}
    out = eng.BatchWorker._with_input_hint('p', sr)
    assert 'uploads/日报.txt' in out


# ── continue 轮附件提示（_with_continue_files_hint，2026-11-20）────────

def test_continue_files_hint_single_file_names_it():
    files = [{'name': '增量.csv', 'path': 'batch-staging/u/x/增量.csv'}]
    out = eng.BatchWorker._with_continue_files_hint('再深入分析', files)
    assert 'uploads/增量.csv' in out
    assert '本轮' in out                 # 措辞区分于首轮输入文件提示
    assert out.rstrip().endswith('再深入分析')


def test_continue_files_hint_multi_file_does_not_name_any_single_file():
    files = [
        {'name': 'a.csv', 'path': 'batch-staging/u/x/a.csv'},
        {'name': 'b.csv', 'path': 'batch-staging/u/y/b.csv'},
    ]
    out = eng.BatchWorker._with_continue_files_hint('p', files)
    assert 'uploads/ 目录下（共 2 个）' in out
    assert 'a.csv' not in out
    assert 'b.csv' not in out


def test_continue_files_hint_empty_files_returns_prompt_unchanged():
    assert eng.BatchWorker._with_continue_files_hint('p', []) == 'p'
    assert eng.BatchWorker._with_continue_files_hint('p', None) == 'p'
