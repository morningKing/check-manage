"""容量阶梯：并发 5→10→20→35→50，每级 100 批 × 5 children = 500 children
（全阶梯累计 500 批，对齐 spec「批次累计约 500」量级）。附 1 个空壳批
（开放 API files:[] 创建→append 填充）覆盖 0 文件路径。
升/停规则（spec §4.1）：成功率≥99% 且不变量全绿→升级；首次失败即停，
容量结论=最后全绿级。全程 stub（零 token）。"""
import time

import pytest
import requests

from tests.stress.conftest import (METRICS_ROOT, create_batch, append_files,
                                   wait_terminal)

pytestmark = pytest.mark.stress

LADDER = [5, 10, 20, 35, 50]
BATCHES_PER_LEVEL = 100
CHILDREN_PER_BATCH = 5
SUCCESS_FLOOR = 0.99


def _make_api_key(stress_stack) -> str:
    r = requests.post(f'{stress_stack.base}/apiKeys',
                      headers=stress_stack.auth_header,
                      json={'name': 'STRESS-capacity'}, timeout=10)
    r.raise_for_status()
    return r.json()['key']                       # routes/api_keys.py:71


def _empty_shell_scenario(stress_stack):
    """空壳批：开放 API 0 文件创建 → append 2 文件 → 照常收敛。"""
    key = _make_api_key(stress_stack)
    api = {'base': stress_stack.base,
           'headers': {'X-API-Key': key, 'Authorization': stress_stack.auth_header['Authorization']}}
    bid = create_batch(stress_stack, 0, api=api)         # 0 文件空壳（bf68849）
    append_files(stress_stack, bid, 2, api=api)
    d = wait_terminal(stress_stack, bid, timeout_s=300)
    assert len(d.get('sessions', [])) == 2, d.get('sessions')
    assert d['batch']['status'] == 'completed'


def _run_level(stress_stack, sampler, level: int) -> dict:
    stress_stack.restart_backend(
        concurrency=level,
        profile={'delay_ms': [2000, 8000], 'error_rate': 0.0,
                 'hang_rate': 0.0})
    sampler.record(f'capacity-L{level}')
    batch_ids = [create_batch(stress_stack, CHILDREN_PER_BATCH)
                 for _ in range(BATCHES_PER_LEVEL)]
    t0 = time.time()
    details = [wait_terminal(stress_stack, b, timeout_s=1200) for b in batch_ids]
    wall = time.time() - t0
    children = [c for d in details for c in d.get('sessions', [])]
    done = sum(1 for c in children if c['status'] == 'completed')
    failed = sum(1 for c in children if c['status'] == 'failed')
    cancelled = sum(1 for c in children if c['status'] == 'cancelled')
    return {'level': level, 'batches': len(batch_ids),
            'children': len(children), 'done': done,
            'failed': failed, 'cancelled': cancelled,
            'failed_errors': [c.get('error_message') for c in children
                              if c['status'] == 'failed'],
            'success_rate': done / max(1, len(children)),
            'throughput_cpm': len(children) / wall * 60,
            'invariants': stress_stack.invariants(), 'wall_s': wall}


def test_capacity_ladder(stress_stack, sampler):
    levels = []
    capacity = None
    for level in LADDER:
        r = _run_level(stress_stack, sampler, level)
        levels.append(r)
        inv = r['invariants']
        green = (r['success_rate'] >= SUCCESS_FLOOR
                 and inv['orphans'] == 0 and inv['zombie_running'] == 0
                 and inv['counter_violations'] == 0)
        if green:
            capacity = level
        else:
            break                    # 首个失败级：记录失败形态后停
    assert capacity is not None, f'连 L{LADDER[0]} 都未通过: {levels}'
    _empty_shell_scenario(stress_stack)
    # 结论落盘（Task 6 渲染器复用同一 JSON）。用 conftest 的 METRICS_ROOT
    # （仓库根 docs/ai-testing/evidence/stress）而非相对路径：pytest 从 server/
    # 运行时相对路径会把结论 JSON 与采样产物劈成两棵树。
    import json
    METRICS_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    (METRICS_ROOT / f"{stamp}-capacity.json").write_text(
        json.dumps({'capacity': capacity, 'levels': levels},
                   ensure_ascii=False, indent=1), encoding='utf-8')
    # spec §5：跑完自动汇总 md（渲染器见 tests/stress/report.py）
    from tests.stress.report import render_capacity_report
    (METRICS_ROOT / f"{stamp}-capacity.md").write_text(
        render_capacity_report({'capacity': capacity, 'levels': levels}),
        encoding='utf-8')
