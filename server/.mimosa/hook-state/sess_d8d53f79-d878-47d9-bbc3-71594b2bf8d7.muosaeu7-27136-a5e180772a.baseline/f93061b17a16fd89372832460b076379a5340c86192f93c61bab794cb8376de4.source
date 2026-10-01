"""oc_watchdog（OpenCode serve 崩溃看门狗）单元测试。

覆盖：健康重置、连续失败阈值触发重启、重启成功/失败落状态、
外部进程标记后不再尝试、手动重启进行中跳过、重启锁互斥。
不依赖真实 OC 进程（打桩 utils.opencode_global 的健康与重启）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from utils import oc_watchdog
from utils import opencode_global as ocg


@pytest.fixture(autouse=True)
def reset_state():
    with oc_watchdog._STATE_LOCK:
        oc_watchdog._STATE.update(
            enabled=True, intervalSec=30, threshold=2, lastCheckAt=None,
            consecutiveFailures=0, lastHealthyAt=None, lastRestartAt=None,
            lastRestartResult=None, lastError=None, externalProcess=False,
            leaseHeld=False)
    yield


def _fail(n_times=1):
    for _ in range(n_times):
        oc_watchdog.tick()


def test_healthy_resets_failures(monkeypatch):
    monkeypatch.setattr(ocg, 'serve_health', lambda: {'healthy': True})
    oc_watchdog._update(consecutiveFailures=1)
    r = oc_watchdog.tick()
    assert r == {'action': 'none', 'healthy': True}
    assert oc_watchdog.snapshot()['consecutiveFailures'] == 0
    assert ocg.restart_busy() is False  # 健康路径不碰重启锁


def test_threshold_triggers_restart(monkeypatch):
    monkeypatch.setattr(ocg, 'serve_health', lambda: {'healthy': False})
    calls = []
    monkeypatch.setattr(ocg, 'restart_serve',
                        lambda: calls.append(1) or {'pid': 42, 'version': 'x'})
    # 第 1 次失败：等待（阈值 2）
    r = oc_watchdog.tick()
    assert r['action'] == 'wait' and not calls
    # 第 2 次失败：触发重启
    r = oc_watchdog.tick()
    assert r == {'action': 'restarted', 'ok': True}
    assert len(calls) == 1
    snap = oc_watchdog.snapshot()
    assert snap['lastRestartResult']['ok'] is True and 'pid=42' in snap['lastRestartResult']['detail']


def test_restart_failure_recorded(monkeypatch):
    monkeypatch.setattr(ocg, 'serve_health', lambda: {'healthy': False})
    monkeypatch.setattr(ocg, 'restart_serve',
                        lambda: (_ for _ in ()).throw(
                            ocg.OpenCodeGlobalError('RESTART_FAILED', 'boom', 502)))
    oc_watchdog.tick()
    r = oc_watchdog.tick()
    assert r['action'] == 'restart-failed'
    assert oc_watchdog.snapshot()['lastRestartResult']['ok'] is False


def test_external_process_marks_and_stops(monkeypatch):
    monkeypatch.setattr(ocg, 'serve_health', lambda: {'healthy': False})
    calls = []

    def _raise():
        calls.append(1)
        raise ocg.OpenCodeGlobalError('EXTERNAL_PROCESS', '外部实例', 409)
    monkeypatch.setattr(ocg, 'restart_serve', _raise)
    oc_watchdog.tick()                      # 失败 1 次 → wait
    oc_watchdog.tick()                      # 失败 2 次 → 首次尝试重启 → 标记外部
    assert oc_watchdog.snapshot()['externalProcess'] is True
    assert len(calls) == 1
    # 第 3 次起不再尝试重启（外部进程不代杀）
    r = oc_watchdog.tick()
    assert r['action'] == 'skip-external'
    assert len(calls) == 1
    # 健康恢复后标记复位
    monkeypatch.setattr(ocg, 'serve_health', lambda: {'healthy': True})
    oc_watchdog.tick()
    assert oc_watchdog.snapshot()['externalProcess'] is False


def test_skip_while_manual_restart_in_progress(monkeypatch):
    monkeypatch.setattr(ocg, 'serve_health', lambda: {'healthy': False})
    monkeypatch.setattr(ocg, 'restart_busy', lambda: True)
    calls = []
    monkeypatch.setattr(ocg, 'restart_serve', lambda: calls.append(1) or {})
    oc_watchdog.tick()
    r = oc_watchdog.tick()
    assert r['action'] == 'skip-busy'
    assert not calls


def test_restart_lock_and_busy(monkeypatch):
    """RESTART_LOCK 被《管理页重启》持有时 restart_busy 为真；释放后为假。"""
    assert ocg.restart_busy() is False
    with ocg.RESTART_LOCK:
        assert ocg.restart_busy() is True
    assert ocg.restart_busy() is False


def test_snapshot_shape():
    snap = oc_watchdog.snapshot()
    for key in ('enabled', 'intervalSec', 'threshold', 'consecutiveFailures',
                'lastRestartResult', 'externalProcess', 'leaseHeld'):
        assert key in snap
