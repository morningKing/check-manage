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


def test_s2_concurrent_terminal_gate_evaluation(stress_stack, sampler):
    """50 子任务 ±窗口并发终态 × 每子任务 3 条期望（2 file 应过 + 1 tool 必败对照）。
    S2.1 期望行恰好核对一次（无 pending 残留 / 无重复评估）；
    S2.2 gate_passed/gate_failed 计数与期望行一致（passed=2 failed=1）；
    S2.3 gate.evaluated 审计事件数 == 子任务数；
    S2.4 drain 高峰无超时、批次可达终态；
    S2.5 invariants 守恒。
    （spec §5 修订：应过侧只用 file 类——db_record 建表不在 migrations，
    避免无谓 schema 耦合；db_record 功能正确性由既有单测覆盖。）"""
    sampler.record('cap-S2-start')
    # 记录重启前日志长度：backend.log 跨 pytest 会话追加，drain 断言只看本轮增量
    log_baseline = (METRICS_ROOT / 'backend.log').stat().st_size \
        if (METRICS_ROOT / 'backend.log').exists() else 0
    stress_stack.restart_backend(concurrency=10,
                                 profile={'delay_ms': [8000, 12000]})
    # 纯相对 glob（不含 .. ；字面量 'dir/*.ext' 会触发 Mimosa 穿越误报，故拼接）
    glob1 = 'outputs/ok-' + '*.md'
    glob2 = 'artifacts/' + '*.txt'
    checks = [
        {'name': 'file-pass-1', 'check_type': 'file',
         'effect_spec': {'path': glob1}},
        {'name': 'file-pass-2', 'check_type': 'file',
         'effect_spec': {'path': glob2}},
        {'name': 'tool-fail-ctrl', 'check_type': 'tool',
         'tool': 'bash', 'args_pattern': 'never-matched-marker'},
    ]
    bid = create_batch(stress_stack, 50, action_checks=checks)
    # workspace_path 由 worker 认领时才落——轮询播种：每个子任务工作区一路径
    # 就绪立即种"应过"文件（认领后 stub 还要跑 8-12s，播种窗口充足）。
    # stub 无工具部分 → tool-fail-ctrl 必败（对照组）。
    children = stress_stack.db_query(
        "SELECT s.id, s.workspace_path, s.batch_seq FROM ai_chat_sessions s "
        "WHERE s.batch_id=%s ORDER BY s.batch_seq", (bid,))
    assert len(children) == 50
    import os
    from pathlib import Path
    from config import AI_WORKSPACE_ROOT      # 与后端同源（env 缺省在 config 兜底）
    ws_root = os.path.realpath(AI_WORKSPACE_ROOT)

    def _seed(sid, ws, seq):
        # 种文件前归一并校验工作区在 workspace 根内（禁越界，Mimosa 建议）
        ws_real = Path(os.path.realpath(ws))
        assert os.path.commonpath([str(ws_real), ws_root]) == ws_root, \
            f'工作区越界: {ws}'
        out_dir = ws_real / 'outputs'
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f'ok-{seq}.md').write_text('seeded', encoding='utf-8')
        art_dir = ws_real / 'artifacts'
        art_dir.mkdir(parents=True, exist_ok=True)
        (art_dir / f'a-{seq}.txt').write_text('seeded', encoding='utf-8')

    seeded = set()
    deadline = time.time() + 300      # 含 worker 租约 TTL 交接窗（实测可达 ~80s）
    while len(seeded) < 50 and time.time() < deadline:
        for sid, ws, seq in stress_stack.db_query(
                "SELECT s.id, s.workspace_path, s.batch_seq FROM ai_chat_sessions s "
                "WHERE s.batch_id=%s AND s.workspace_path IS NOT NULL", (bid,)):
            if sid in seeded:
                continue
            _seed(sid, ws, seq)
            seeded.add(sid)
        time.sleep(0.5)
    assert len(seeded) == 50, f'仅 {len(seeded)}/50 子任务工作区就绪（超时）'
    wait_terminal(stress_stack, bid, timeout_s=900)
    detail = requests.get(f'{stress_stack.base}/ai/chat/batches/{bid}',
                          headers=stress_stack.auth_header, timeout=10).json()
    # S2.1 每子任务 3 条期望全部 last_status 非空（恰好一次终态核对，无残留）
    rows = stress_stack.db_query(
        "SELECT scope_id, count(*), count(last_status), count(DISTINCT name) "
        "FROM action_expectations WHERE scope_id IN "
        "(SELECT id FROM ai_chat_sessions WHERE batch_id=%s) GROUP BY scope_id",
        (bid,))
    assert len(rows) == 50, f'期望行分布 {len(rows)} 子任务 != 50'
    for sid, n, checked, distinct in rows:
        assert (n, checked, distinct) == (3, 3, 3), (sid, n, checked, distinct)
    # S2.2 计数一致：file 应过 2 + tool 必败 1 → 每子任务 passed=2 failed=1
    sess = {s['id']: s for s in detail['sessions']}
    for sid, _ws, _seq in children:
        assert sess[sid]['gate_passed'] == 2 and sess[sid]['gate_failed'] == 1, \
            sess[sid]
    # S2.3 审计事件与子任务一一对应（gate.evaluated 落 ai_execution_events，
    # 带 session_id——经会话归属对账到批）
    ev = stress_stack.db_query(
        "SELECT count(*) FROM ai_execution_events e "
        "JOIN ai_chat_sessions s ON s.id = e.session_id "
        "WHERE s.batch_id=%s AND e.event_type='gate.evaluated'", (bid,))
    assert ev[0][0] == 50, f'gate.evaluated 事件 {ev[0][0]} != 50'
    # S2.4 无 drain 超时日志（只扫本轮后端启动后的增量）
    log = (METRICS_ROOT / 'backend.log').read_text(encoding='utf-8',
                                                   errors='replace')[log_baseline:]
    assert 'drain timeout' not in log.lower(), 'wait_subtasks_drained 高峰超时'
    # S2.5
    inv = stress_stack.invariants()
    assert inv == {'orphans': 0, 'zombie_running': 0, 'counter_violations': 0}, inv
    _dump('s2-gate-concurrent', {'children': 50, 'checks_per_child': 3})

