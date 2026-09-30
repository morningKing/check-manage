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
