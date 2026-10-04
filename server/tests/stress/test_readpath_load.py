"""读路径压载（spec §4.3）：SSE 并发连接 / 事件分页 / 大列表 / outbox 洪峰。
全部 stub 层（回 stub 后端即可），不依赖真模型。

与 brief 的差异（写前对照实际路由核实，见 task-5-report.md）：
- R1 打真 SSE 路由 GET /ai/chat/batches/events?ids=<bid>
  （routes/ai_chat_batches.py:806 batch_events_sse）——brief 的
  /ai/chat/batches/<id>/events 是 JSON 分页端点（:747），stream 上去
  数秒即读完，压不到长连接；
- R2 分页参数名用 afterSeq（:756），brief 的 after_seq 会被服务端静默忽略。
"""
import threading
import time

import psycopg2
import psycopg2.extras
import pytest
import requests

from tests.stress.conftest import create_batch, wait_terminal
from tests.stress.fakes import callback_target

pytestmark = pytest.mark.stress


def test_r1_sse_50_connections_5min(stress_stack, sampler):
    sampler.record('readpath-R1-start')
    bid = create_batch(stress_stack, 50)            # 50 children → 事件流有料
    errors = []
    stop = threading.Event()

    def watch():
        try:
            with requests.get(
                    f'{stress_stack.base}/ai/chat/batches/events',
                    headers=stress_stack.auth_header, stream=True,
                    params={'ids': bid},
                    timeout=(5, 300)) as r:
                if r.status_code != 200:            # 线程内不许 assert（不冒泡）
                    errors.append(f'SSE status {r.status_code}')
                    return
                for _ in r.iter_lines(chunk_size=1):
                    if stop.is_set():
                        return
        except Exception as e:                      # 断流/超时都算失败
            errors.append(e)

    threads = [threading.Thread(target=watch, daemon=True) for _ in range(50)]
    for t in threads:
        t.start()
    time.sleep(300)                                 # 持续 5 分钟
    stop.set()
    for t in threads:
        t.join(timeout=10)
    wait_terminal(stress_stack, bid, timeout_s=600)
    sampler.record('readpath-R1-end')
    assert not errors, f'SSE 异常: {errors[:3]}'
    inv = stress_stack.invariants()
    assert inv['zombie_running'] == 0


def test_r2_events_pagination_p95(stress_stack, sampler):
    sampler.record('readpath-R2')
    bid = create_batch(stress_stack, 20)
    wait_terminal(stress_stack, bid, timeout_s=600)
    lat = []
    for after in range(0, 200, 20):
        t0 = time.time()
        r = requests.get(
            f'{stress_stack.base}/ai/chat/batches/{bid}/events',
            headers=stress_stack.auth_header,
            params={'afterSeq': after, 'limit': 20}, timeout=10)
        lat.append(time.time() - t0)
        assert r.status_code == 200
    lat.sort()
    p95 = lat[int(len(lat) * 0.95) - 1]
    _dump('r2-events-p95', {'p95_s': p95, 'samples': lat})
    assert p95 < 2.0, f'events 分页 P95 {p95:.2f}s 超 2s'


def test_r3_admin_list_500_batches(stress_stack, sampler):
    sampler.record('readpath-R3')
    lat = []
    for page in range(5):
        t0 = time.time()
        r = requests.get(f'{stress_stack.base}/ai/chat/admin/batches',
                         headers=stress_stack.auth_header,
                         params={'page': page + 1, 'pageSize': 100}, timeout=15)
        lat.append(time.time() - t0)
        assert r.status_code == 200
    lat.sort()
    _dump('r3-admin-list', {'samples': lat})
    assert lat[-1] < 5.0, f'管理列表最大延迟 {lat[-1]:.2f}s 超 5s'


def test_r4_outbox_flood_3k(stress_stack, sampler):
    sampler.record('readpath-R4')
    srv, state = callback_target.start(behavior='ok')
    try:
        t0 = time.time()
        now = time.time_ns()
        # 洪峰 3000 而非 1 万：投递器单线程 LIMIT 10/5s tick ≈120 行/分钟，
        # 1 万行要 ~83 分钟远超 1800s 死线；3000 行 ~25 分钟，死线内可达。
        flood = [(f'obx-stress-{now}-{i}', f'evt-{now}-{i}')
                 for i in range(3000)]
        conn = psycopg2.connect(**stress_stack.db_dsn)
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(
                cur,
                "INSERT INTO ai_delivery_outbox (id, event_id, batch_id, "
                "event_type, target_url, payload, signature, idempotency_key, "
                "status, next_retry_at, attempt_count) VALUES (%s,%s,%s,'t',"
                "'http://127.0.0.1:3098/cb','{}','',%s,'pending',NOW(),0)",
                [(o, e, f'stress-flood-{now % 100000}', f'k-{e}')
                 for o, e in flood])
        conn.commit()
        conn.close()
        deadline = time.time() + 1800               # 3000 行给 30 分钟（约 25 分钟可达）
        while time.time() < deadline:
            if state['deliveries'] >= 3000:
                break
            time.sleep(10)
        wall = time.time() - t0
        _dump('r4-outbox-flood', {'delivered': state['deliveries'],
                                  'wall_s': wall,
                                  'throughput_per_min':
                                      state['deliveries'] / max(1, wall) * 60})
        assert state['deliveries'] >= 3000
        # 真实去重信号（替代恒真的 idem_keys 断言）：投递链路
        # webhook_engine._fire_single_webhook 根本不发 Idempotency-Key 头，
        # key 集合必空、len==len(set) 恒真。改为 DB 终态精确校验——
        # 重试残留会使 outbox 非 delivered 计数 >0，重复投递会使 deliveries 超额。
        conn = psycopg2.connect(**stress_stack.db_dsn)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM ai_delivery_outbox "
                            "WHERE batch_id LIKE 'stress-flood-%' "
                            "AND status <> 'delivered'")
                leftover = cur.fetchone()[0]
        finally:
            conn.close()
        assert leftover == 0, \
            f'outbox 残留 {leftover} 行未 delivered（重试未收口）'
        assert state['deliveries'] == 3000, \
            f"投递次数 {state['deliveries']} != 3000（重复投递/计数漂移）"
    finally:
        callback_target.shutdown(srv)


def _dump(name, payload):
    import json
    from pathlib import Path
    out = Path('docs/ai-testing/evidence/stress')
    out.mkdir(parents=True, exist_ok=True)
    (out / f'{time.strftime("%Y%m%d-%H%M%S")}-{name}.json').write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
