"""能力稳定性压测（spec docs/superpowers/specs/2026-10-05-batch-capability-stress-design.md）。
S0 交互冒烟 / S1 SSE 帧对账 / S2 并发终态×门禁 / S3 修正-重试-取消竞态 /
S4 交互收口竞争 / S5 树作用域聚合。全程 stub，0 token。"""
import threading
import time

import pytest
import requests

from tests.stress.caphelpers import (collect_sse, create_interactive_session,
                                     open_sse, register_expectations,
                                     send_message, stress_db)
from tests.stress.conftest import METRICS_ROOT, create_batch, wait_terminal

pytestmark = pytest.mark.stress


@pytest.fixture(scope='module', autouse=True)
def _reset_stub_runtime(stress_stack):
    stress_stack.restart_backend(concurrency=3)


def _dump(name, payload):
    import json
    METRICS_ROOT.mkdir(parents=True, exist_ok=True)
    (METRICS_ROOT / f'{time.strftime("%Y%m%d-%H%M%S")}-{name}.json').write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')


def test_s0_interactive_roundtrip_smoke(stress_stack, sampler):
    """接线判别力锚：base（硬编码 OpenCodeClient）上建会话即 ConnectionError。
    全链路：建会话 → 先挂 SSE → 发送 → 帧序正确 → 消息落库 → 账本恰 2 行 bash。"""
    sampler.record('cap-S0-start')
    stress_stack.restart_backend(
        concurrency=3, profile={'delay_ms': [300, 800], 'tool_parts': 2})
    sid = create_interactive_session(stress_stack)
    sink = []
    stop = threading.Event()
    t = threading.Thread(target=collect_sse, daemon=True, args=(
        stress_stack, f'/ai/chat/sessions/{sid}/events', None, stop, sink,
        lambda f: f.event in ('session.idle', 'session.error')))
    t.start()
    time.sleep(0.5)                     # 先挂流再发（事件不可重放）
    send_message(stress_stack, sid,
                 '直接回复:STRESS-OK。不要读取文件,不要执行命令。')
    t.join(timeout=30)
    stop.set()
    assert not any(isinstance(f, Exception) for f in sink), sink[:3]
    types = [f.event for f in sink]
    assert types and types[0] == 'message.updated', types
    assert types[-1] in ('session.idle', 'session.error'), types
    assert types.count('message.part.updated') == 3, types   # 2 tool + 1 text
    # 会话级 SSE 帧 data 就是 props 本身（sse_events 直接 yield props，
    # 无批次端点的 {'type','properties'} 包装）——sessionID 是 apply_event 路由键
    assert all(f.data.get('sessionID') for f in sink), \
        'sessionID 路由键缺失'
    # 落库：assistant 消息已持久化（监听器写或 SSE 兜底写，二选一但必有一）
    rows = stress_stack.db_query(
        "SELECT count(*) FROM ai_chat_messages "
        "WHERE session_id=%s AND role='assistant'", (sid,))
    assert rows[0][0] >= 1, 'SSE idle 后消息未落库'
    # 账本：tool_parts=2 → bash 恰 2 行（幂等键 (oc_session_id, part_id)）
    rows = stress_stack.db_query(
        "SELECT count(*) FROM agent_tool_calls t JOIN ai_chat_sessions s "
        "ON s.id=%s AND t.oc_session_id=s.opencode_session_id "
        "AND t.tool='bash'", (sid,))
    assert rows[0][0] == 2, f'账本 bash 行数 {rows[0][0]} != 2'
    inv = stress_stack.invariants()
    assert inv['zombie_running'] == 0


def test_s1_batch_sse_frame_reconciliation_and_resume(stress_stack, sampler):
    """50 观察者并发持流约 5min + 帧级正确性：
    S1.1 帧 seq 对 ai_batch_events 表对账（watcher 内严格递增无重复，
         全体并集 == 表全集，无缺口）；
    S1.2 1/3 观察者早期断开、带 Last-Event-ID 重连，补发恰好衔接游标
         （跨重连仍严格递增，且终态前收到 batch_done）；
    S1.3 终态 batch_done 后服务端关流（ChunkedEncodingError 按终态分类）。
    delay 18-22s × 50 子任务 ÷ 并发3 ≈ 5.5min——持流是真 5 分钟量级。"""
    sampler.record('cap-S1-start')
    stress_stack.restart_backend(concurrency=3,
                                 profile={'delay_ms': [18000, 22000]})
    bid = create_batch(stress_stack, 50)
    errors = []
    resume_proofs = []
    stop = threading.Event()
    watcher_lists = []                       # 每个 watcher 的 seen_seqs 列表
    lists_lock = threading.Lock()

    def watch(i: int):
        seen_seqs = []
        last_eid = None
        try:
            for attempt in range(2):        # attempt0 正常；attempt1 仅断线者重连
                headers = dict(stress_stack.auth_header)
                if last_eid is not None:
                    headers['Last-Event-ID'] = last_eid
                with requests.get(
                        f'{stress_stack.base}/ai/chat/batches/events',
                        headers=headers, stream=True,
                        params={'ids': bid},
                        timeout=(5, 600)) as r:
                    if r.status_code != 200:
                        errors.append(f'w{i} status {r.status_code}')
                        return
                    ev, eid = None, None
                    # 断线观察者：流内到达时限主动断开（iter_lines 只在服务端
                    # 关流时才返回，被动等断线永远等不到）
                    disconnect_after = (2 + (i % 5)) \
                        if (attempt == 0 and i % 3 == 0) else None
                    t_stream = time.time()
                    for raw in r.iter_lines(chunk_size=1):
                        line = raw.decode('utf-8') if isinstance(raw, bytes) else raw
                        if line == '':
                            if ev == 'batch_event' and eid:
                                seq = int(eid.rpartition(':')[2])
                                seen_seqs.append(seq)
                                last_eid = eid
                            elif ev == 'batch_done':
                                if attempt == 1 and i % 3 == 0:
                                    resume_proofs.append((i, len(seen_seqs)))
                                return
                            ev, eid = None, None
                        elif line.startswith('event:'):
                            ev = line[7:].strip()
                        elif line.startswith('id:'):
                            eid = line[3:].strip()
                        elif line.startswith('data:'):
                            pass
                        if disconnect_after is not None \
                                and time.time() - t_stream >= disconnect_after:
                            break           # 主动断开 → 走 Last-Event-ID 重连
                if attempt == 0 and i % 3 == 0 and not stop.is_set():
                    time.sleep(2 + (i % 5))      # 早期随机断开
                    continue                     # 带 Last-Event-ID 重连
                return
        except requests.exceptions.ChunkedEncodingError:
            # 服务端 batch_done 关流；urllib3 报 premature end——按批次终态分类
            try:
                d = requests.get(f'{stress_stack.base}/ai/chat/batches/{bid}',
                                 headers=stress_stack.auth_header,
                                 timeout=10).json()
                if d.get('batch', {}).get('status') not in (
                        'completed', 'failed', 'partial'):
                    errors.append(f'w{i} 断流且未终态')
            except Exception:
                errors.append(f'w{i} 断流且状态探针失败')
        except Exception as e:
            errors.append(f'w{i}: {e!r}')
        finally:
            with lists_lock:
                watcher_lists.append(seen_seqs)

    # 1s/连接爬坡建连（R1 教训：瞬时借满 DB 池 → PoolError → 500）
    threads = []
    for i in range(50):
        t = threading.Thread(target=watch, args=(i,), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(1.0)
    for t in threads:
        t.join(timeout=600)
    stop.set()
    wait_terminal(stress_stack, bid, timeout_s=600)
    assert not errors, f'SSE 异常: {errors[:3]}'

    # S1.1 对账：每个 watcher 内严格递增（游标单调、无重复）；全体并集 == 表全集
    for w in watcher_lists:
        assert all(b > a for a, b in zip(w, w[1:])), \
            f'watcher 内 seq 非严格递增: {w[:10]}...'
    table_seqs = [r[0] for r in stress_stack.db_query(
        'SELECT event_seq FROM ai_batch_events WHERE batch_id=%s '
        'ORDER BY event_seq', (bid,))]
    union = sorted({s for w in watcher_lists for s in w})
    assert union == table_seqs, \
        f'流/表不对账: 流缺 {sorted(set(table_seqs) - set(union))[:5]} ' \
        f'流多 {sorted(set(union) - set(table_seqs))[:5]}'
    # S1.2 重连补发：断线者经 Last-Event-ID 重连后撑到 batch_done
    assert resume_proofs, f'无重连补发实证: {resume_proofs}'
    for i, n in resume_proofs:
        assert n > 0
    _dump('s1-sse-reconcile', {'table_events': len(table_seqs),
                               'watchers': len(watcher_lists),
                               'resume_proofs': resume_proofs})
    inv = stress_stack.invariants()
    assert inv['zombie_running'] == 0
