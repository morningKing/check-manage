"""验证 script_runner 常驻 worker 生命周期。

必须以真实文件 + __main__ 守卫运行（Windows multiprocessing spawn 的要求；
Flask/pytest 真实调用链不受影响——脚本在运行期才被调用，不在 import 期）。
"""
import sys
import time


def main():
    sys.path.insert(0, '.')
    from utils.script_runner import _process_exec, _worker_state  # noqa: E402

    payload = _process_exec('validation', 'result = 1 + 1',
                            {'data': [], 'fields': []}, 10, 'timeout')
    assert payload == {'ok': True}, payload
    proc = _worker_state['proc']
    assert proc is not None and proc.is_alive(), 'worker 应存活'
    print('worker pid:', proc.pid)

    _process_exec('validation', 'result = 2 + 2',
                  {'data': [], 'fields': []}, 10, 'timeout')
    assert _worker_state['proc'].pid == proc.pid, 'worker 应复用'
    print('worker reused ok')

    t0 = time.time()
    try:
        _process_exec('validation', 'while True: pass', {}, 1, '超时')
        raise SystemExit('ERROR: no timeout raised')
    except TimeoutError:
        print('timeout raised in %.1fs' % (time.time() - t0))
    assert _worker_state['proc'] is None, '超时后 worker 应被移除'

    _process_exec('validation', 'result = 3', {'data': [], 'fields': []}, 10, 'timeout')
    assert _worker_state['proc'].pid != proc.pid, '新 worker 应是全新进程'
    print('fresh worker after timeout ok')
    print('ALL CHECKS PASSED')


if __name__ == '__main__':
    main()
