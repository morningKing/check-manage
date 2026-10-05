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
