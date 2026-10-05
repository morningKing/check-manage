"""BatchWorker 执行并行度回归（2026-10-05 S2 能力压测发现）。

AI_BATCH_CONCURRENCY 此前只放大认领数（_effective_concurrency 读 env），
执行线程池 MAX_CONCURRENT 却是硬编码类属性 3——env 调大后实际并行度不变，
「并发终态窗」物理不成立（也解释了容量阶梯各级"无拐点"：实际并行恒 3）。
修复后执行器线程数必须跟随同一 env。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_batch_worker_executor_honors_concurrency_env(monkeypatch):
    from utils.batch_engine import BatchWorker
    monkeypatch.setenv('AI_BATCH_CONCURRENCY', '7')
    w = BatchWorker()
    try:
        assert w._executor._max_workers == 7, \
            (f'执行器线程数 {w._executor._max_workers} 未跟随 '
             'AI_BATCH_CONCURRENCY=7（认领数与执行并行度脱节）')
    finally:
        w._executor.shutdown(wait=False)


def test_batch_worker_executor_default_three(monkeypatch):
    """env 缺省保持原默认 3（不改变未配置环境的行为）。"""
    from utils.batch_engine import BatchWorker
    monkeypatch.delenv('AI_BATCH_CONCURRENCY', raising=False)
    w = BatchWorker()
    try:
        assert w._executor._max_workers == 3
    finally:
        w._executor.shutdown(wait=False)
