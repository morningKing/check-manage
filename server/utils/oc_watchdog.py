"""OpenCode serve 崩溃看门狗——运行中自动拉起。

背景（2026-09-28）：自动拉起只有 proxy 启动时的一次（OPENCODE_AUTOSTART），
serve 运行中挂掉后无人管——批任务烧完重试预算落 failed、交互会话报错，
所有 AI 能力静默不可用直到人工发现。本模块补上运行中守护：

每 interval 秒探测一次 `opencode_global.serve_health()`，连续 threshold 次
不可达即调 `restart_serve()` 自动拉起（读 server/.env 的 OPENCODE_BIN /
OPENCODE_SERVE_CMD，与 proxy/管理页重启同一套代码路径与所有权保护）。

安全设计：
- 租约 `lease_kind='oc-watchdog'`（execution_lease 原子抢占）：多进程部署
  只有一个看门狗真正工作，其余进程的 tick 直接跳过；
- 与管理页手动重启共用 opencode_global.RESTART_LOCK：手动重启进行中时
  看门狗跳过本轮（不会双重拉起）；看门狗重启进行中时管理页重启等待锁；
- 端口被**非平台所有**的进程占着时 restart_serve 抛 EXTERNAL_PROCESS——
  看门狗绝不误杀外部实例，标记 external 后停止尝试直到健康恢复；
- 恢复动作只做"拉起进程"，不自动重放失败的批任务子任务（重放与否属
  恢复决策表/人工职责，避免盲目重试有副作用的执行）。

配置（server/.env，默认即开启——这是保护性兜底而非限制项）：
  AI_OC_WATCHDOG_INTERVAL_SEC   探测间隔，默认 30；0=关闭看门狗
  AI_OC_WATCHDOG_THRESHOLD      连续失败多少次才重启，默认 2
状态在管理页 /ai/opencode/overview 的 watchdog 字段可见。
"""
import logging
import os
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

LEASE_KEY = 'oc-watchdog'

_interval_sec = max(0, int(os.getenv('AI_OC_WATCHDOG_INTERVAL_SEC', '30') or 30))
_threshold = max(1, int(os.getenv('AI_OC_WATCHDOG_THRESHOLD', '2') or 2))

_STATE = {
    'enabled': _interval_sec > 0,
    'intervalSec': _interval_sec,
    'threshold': _threshold,
    'lastCheckAt': None,
    'consecutiveFailures': 0,
    'lastHealthyAt': None,
    'lastRestartAt': None,
    'lastRestartResult': None,   # {'ok': bool, 'detail': str}
    'lastError': None,           # 非 External 的最近一次异常摘要
    'externalProcess': False,    # 端口被外部进程占用（不自动处理）
    'leaseHeld': False,
}
_STATE_LOCK = threading.Lock()


def snapshot() -> dict:
    """管理页可见的看门狗状态。"""
    with _STATE_LOCK:
        return dict(_STATE)


def _update(**kv):
    with _STATE_LOCK:
        _STATE.update(kv)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def tick() -> dict:
    """单次探测（线程调度调用；测试直接调用）。返回本轮动作摘要。

    所有可能失败的依赖（健康检查/租约/重启）都吞异常记状态——看门狗自身
    故障绝不能反过来打断 Flask。
    """
    from utils import opencode_global as ocg

    _update(lastCheckAt=_now_iso())
    try:
        health = ocg.serve_health()
        healthy = bool(health.get('healthy'))
    except Exception as e:  # noqa: BLE001
        logger.warning('watchdog health probe failed: %s', e)
        healthy = False

    with _STATE_LOCK:
        if healthy:
            _STATE['consecutiveFailures'] = 0
            _STATE['lastHealthyAt'] = _now_iso()
            _STATE['externalProcess'] = False
            _STATE['lastError'] = None
        else:
            _STATE['consecutiveFailures'] = int(_STATE['consecutiveFailures']) + 1
        failures = int(_STATE['consecutiveFailures'])
        external = bool(_STATE['externalProcess'])
    if healthy:
        return {'action': 'none', 'healthy': True}

    if failures < _threshold:
        return {'action': 'wait', 'healthy': False, 'failures': failures}

    if external:
        # 外部进程占着端口：永不代杀，等它恢复或人工处理
        return {'action': 'skip-external', 'healthy': False}

    if ocg.restart_busy():
        # 管理页手动重启进行中：跳过，避免双重拉起
        return {'action': 'skip-busy', 'healthy': False}

    _update(lastRestartAt=_now_iso())
    try:
        result = ocg.restart_serve()
        _update(lastRestartResult={'ok': True,
                                   'detail': f"pid={result.get('pid')} "
                                             f"version={result.get('version')}"})
        logger.warning('[oc-watchdog] serve 连续 %s 次不可达，已自动重启 '
                       '(pid=%s)', failures, result.get('pid'))
        return {'action': 'restarted', 'ok': True}
    except ocg.OpenCodeGlobalError as e:
        if e.code == 'EXTERNAL_PROCESS':
            _update(externalProcess=True,
                    lastRestartResult={'ok': False, 'detail': e.message})
            logger.warning('[oc-watchdog] serve 不可达但端口被外部进程占用，'
                           '不自动处理：%s', e.message)
            return {'action': 'skip-external', 'ok': False}
        _update(lastRestartResult={'ok': False, 'detail': e.message},
                lastError=e.message)
        logger.error('[oc-watchdog] 自动重启失败：%s', e.message)
        return {'action': 'restart-failed', 'ok': False}
    except Exception as e:  # noqa: BLE001
        _update(lastRestartResult={'ok': False, 'detail': str(e)},
                lastError=str(e))
        logger.error('[oc-watchdog] 自动重启异常：%s', e)
        return {'action': 'restart-failed', 'ok': False}


def start(app=None):
    """注册 APScheduler 任务（多进程安全由租约保证）。interval=0 时不启动。"""
    if not _STATE['enabled']:
        logger.info('oc-watchdog disabled (AI_OC_WATCHDOG_INTERVAL_SEC=0)')
        return None
    from apscheduler.schedulers.background import BackgroundScheduler
    from utils import execution_lease

    owner = execution_lease.owner_id()

    def _job():
        if not _STATE.get('leaseHeld'):
            ok, _ = execution_lease.acquire(LEASE_KEY, owner, lease_kind=LEASE_KEY)
            _update(leaseHeld=bool(ok))
            if not ok:
                return  # 另一实例的看门狗在值班
        if not execution_lease.heartbeat(LEASE_KEY, owner):
            _update(leaseHeld=False)
            return
        try:
            tick()
        except Exception as e:  # noqa: BLE001 —— tick 已尽量自吞，这里兜底
            logger.error('oc-watchdog tick crashed: %s', e)

    sched = BackgroundScheduler(timezone='Asia/Shanghai')
    sched.add_job(_job, 'interval', seconds=_interval_sec, id=LEASE_KEY,
                  max_instances=1, coalesce=True)
    sched.start()
    logger.info('oc-watchdog started (interval=%ss threshold=%s)',
                _interval_sec, _threshold)
    return sched
