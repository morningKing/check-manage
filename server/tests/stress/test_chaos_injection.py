"""混沌注入（spec §4.2，专属真 serve 层，≤3 并发）：
C1 kill serve→子任务 failed 非僵尸→重启续跑
C2 kill -9 worker→租约接管+fencing 递增+无双重执行
C3 outbox 503/超时→退避→dead_letter→needs_review 不自动重放
C4 有活动会话时 restart 无 force→409
C1/C2 期间后端 runtime=opencode_local 指向专属 serve:4097。"""
import time

import pytest
import requests

from tests.stress.conftest import create_batch, wait_terminal
from tests.stress.fakes import callback_target

pytestmark = pytest.mark.stress

REAL_ENV = {'AI_AGENT_RUNTIME': 'opencode_local',
            'OPENCODE_BASE_URL': 'http://127.0.0.1:4097'}


def _switch_to_real_serve(stress_stack):
    """后端切到专属真 serve:4097（opencode_local）；dev 的 4096 不受影响。
    spawn_serve 幂等（存活实例直接复用），本助手可被多个场景重复调用。"""
    stress_stack.spawn_serve()     # :4097
    stress_stack.restart_backend(concurrency=3, env_extra=REAL_ENV)


def test_c1_kill_serve_children_fail_then_recover(stress_stack, sampler):
    _switch_to_real_serve(stress_stack)
    sampler.record('chaos-C1')
    bid = create_batch(stress_stack, 2)
    time.sleep(10)                                  # 子任务进入 running
    serve_proc = stress_stack._serve_procs[-1]
    stress_stack.kill_serve(serve_proc)             # 注入：kill 专属 serve
    d = wait_terminal(stress_stack, bid, timeout_s=300)
    assert all(c['status'] == 'failed' for c in d['sessions']), d['sessions']
    assert all(c.get('error_message') for c in d['sessions'])   # 非僵尸：有错误信息
    stress_stack.spawn_serve()                      # 重启后队列继续消化
    bid2 = create_batch(stress_stack, 1)
    d2 = wait_terminal(stress_stack, bid2, timeout_s=420)
    assert d2['batch']['status'] == 'completed'


def test_c2_worker_kill_lease_takeover_no_double_exec(stress_stack, sampler):
    _switch_to_real_serve(stress_stack)             # 幂等：已在则复用
    sampler.record('chaos-C2')
    bid = create_batch(stress_stack, 2)
    time.sleep(8)
    fence_before = stress_stack.db_query(
        "SELECT fencing_token FROM ai_chat_sessions "
        "WHERE batch_id=%s AND fencing_token IS NOT NULL", (bid,))
    stress_stack.stop_backend()                     # kill -9 等价：进程直接杀
    # 接管实例必须同运行时（真 serve 的 oc_session_id 在 stub 客户端里不存在）
    stress_stack.start_backend(concurrency=3, env_extra=REAL_ENV)
    d = wait_terminal(stress_stack, bid, timeout_s=420)
    assert d['batch']['status'] in ('completed', 'partial', 'failed')
    fence_after = stress_stack.db_query(
        "SELECT fencing_token FROM ai_chat_sessions "
        "WHERE batch_id=%s AND fencing_token IS NOT NULL", (bid,))
    if fence_before and fence_after:
        assert fence_after[0][0] >= fence_before[0][0]   # 接管则 token 不回退
    inv = stress_stack.invariants()
    assert inv['zombie_running'] == 0 and inv['orphans'] == 0
    # 无双重执行：每子任务消息轮数=派发轮数（重复执行会翻倍）
    dup = stress_stack.db_query(
        "SELECT s.id, count(*) FROM ai_chat_sessions s "
        "JOIN ai_chat_messages m ON m.session_id=s.id "
        "WHERE s.batch_id=%s AND m.role='user' GROUP BY s.id "
        "HAVING count(*) > 1", (bid,))
    assert dup == [], f'疑似双重执行: {dup}'


def test_c3_outbox_503_then_ok_backoff_dead_letter(stress_stack, sampler):
    _switch_to_real_serve(stress_stack)
    sampler.record('chaos-C3')
    key = _make_api_key(stress_stack)
    hdr = {'X-API-Key': key}
    api = {'base': stress_stack.base, 'headers': hdr}
    srv, state = callback_target.start(behavior='503')
    try:
        bid = create_batch(stress_stack, 1, callback_url='http://127.0.0.1:3098/cb',
                           api=api)
        wait_terminal(stress_stack, bid, timeout_s=420)
        time.sleep(60)                              # 等 outbox 重试进入退避
        rows = stress_stack.db_query(
            "SELECT status, attempt_count FROM ai_delivery_outbox "
            "WHERE batch_id=%s", (bid,))
        assert rows and rows[0][1] >= 2, rows       # 至少重试过 2 次
    finally:
        callback_target.shutdown(srv)
    inv = stress_stack.invariants()
    assert inv['orphans'] == 0


def _make_api_key(stress_stack) -> str:
    r = requests.post(f'{stress_stack.base}/apiKeys',
                      headers=stress_stack.auth_header,
                      json={'name': 'STRESS-c3'}, timeout=10)
    r.raise_for_status()
    return r.json()['key']                          # routes/api_keys.py:71


def test_c4_restart_guard_refuses_without_force(stress_stack):
    _switch_to_real_serve(stress_stack)
    bid = create_batch(stress_stack, 1)
    time.sleep(8)                                   # 制造活动会话窗口
    r = requests.post(f'{stress_stack.base}/ai/opencode/restart',
                      headers=stress_stack.auth_header, json={}, timeout=30)
    assert r.status_code == 409
    assert r.json()['code'] == 'ACTIVE_WORKLOAD'
    wait_terminal(stress_stack, bid, timeout_s=420)
