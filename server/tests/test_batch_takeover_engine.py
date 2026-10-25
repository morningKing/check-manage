# -*- coding: utf-8 -*-
"""batch_engine 完成路径的前置 repark 尝试（spec D3，任务 5）：

_mark_done / _mark_failed 开头先试 batch_repo.try_repark_transition——
接管轮（continue_repark=true）命中时收口回 paused：审计按 'stopped'
（TAKEOVER_REPARK）记、不落 completed/failed 终态、不计数；
未命中（非接管轮/已过期）原样回落常规路径，行为逐字节不变。
_mark_failed 的 repark 判定必须先于 failed 审计，接管轮绝不记 'failed'。

纯 mock-seam 测试：BatchWorker.__new__ 跳过 __init__，_resolve_generation
打桩直传，utils.batch_repo / utils.execution_audit / utils.skill_fit
全部 monkeypatch，零 DB。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.batch_engine import BatchWorker  # noqa: E402


def _worker():
    w = BatchWorker.__new__(BatchWorker)  # 不跑 __init__，仅测方法
    w._resolve_generation = lambda sid, gen: gen  # 桩：直接采用传入 generation
    return w


def test_mark_done_reparks_takeover_turn(monkeypatch):
    hit = {'called': 0}

    def fake_repark(sid, *, generation, fencing_token):
        hit['called'] += 1
        assert (generation, fencing_token) == (3, 9)
        return {'id': sid}

    monkeypatch.setattr('utils.batch_repo.try_repark_transition', fake_repark)
    audit = []
    monkeypatch.setattr('utils.execution_audit.finish_latest_running',
                        lambda sid, status, **kw: audit.append(status))
    monkeypatch.setattr('utils.skill_fit.compute_for_session', lambda sid: None)
    transition_calls = []

    def fake_transition(sid, status, **kw):
        transition_calls.append(status)
        raise AssertionError('repark 命中后不得再走常规 transition_child')

    monkeypatch.setattr('utils.batch_repo.transition_child', fake_transition)
    worker = _worker()
    worker._mark_done('sess-1', 'b-1', 'preview', generation=3, fencing_token=9)
    assert audit == ['stopped']            # repark 命中：审计按 stopped 收口
    assert hit['called'] == 1
    assert transition_calls == []          # 不落 completed 终态、不计数


def test_mark_done_falls_through_when_not_takeover(monkeypatch):
    monkeypatch.setattr('utils.batch_repo.try_repark_transition',
                        lambda sid, *, generation, fencing_token: None)
    done = {}

    def fake_transition(sid, status, **kw):
        done['status'] = status
        return {'batch_status': None}      # 批未整体收口：不触发 callback

    monkeypatch.setattr('utils.batch_repo.transition_child', fake_transition)
    monkeypatch.setattr('utils.execution_audit.finish_latest_running',
                        lambda *a, **kw: None)
    monkeypatch.setattr('utils.skill_fit.compute_for_session', lambda sid: None)
    worker = _worker()
    worker._mark_done('sess-1', 'b-1', 'preview', generation=3, fencing_token=9)
    assert done['status'] == 'completed'   # 未命中 → 常规 completed 路径


def test_mark_failed_reparks_takeover_turn(monkeypatch):
    hit = {'called': 0}

    def fake_repark(sid, *, generation, fencing_token):
        hit['called'] += 1
        assert (generation, fencing_token) == (3, 9)
        return {'id': sid}

    monkeypatch.setattr('utils.batch_repo.try_repark_transition', fake_repark)
    audit = []
    monkeypatch.setattr('utils.execution_audit.finish_latest_running',
                        lambda sid, status, **kw: audit.append(status))
    monkeypatch.setattr('utils.skill_fit.compute_for_session', lambda sid: None)
    transition_calls = []

    def fake_transition(sid, status, **kw):
        transition_calls.append(status)
        raise AssertionError('repark 命中后不得再走常规 transition_child')

    monkeypatch.setattr('utils.batch_repo.transition_child', fake_transition)
    worker = _worker()
    worker._mark_failed('sess-1', 'b-1', 'boom', generation=3, fencing_token=9)
    assert audit == ['stopped']            # 接管轮按 stopped 收口，绝不记 failed
    assert 'failed' not in audit
    assert hit['called'] == 1
    assert transition_calls == []          # 不落 failed 终态、不计数


def test_mark_failed_falls_through_when_not_takeover(monkeypatch):
    events = []

    def fake_repark(sid, *, generation, fencing_token):
        events.append('repark')
        return None

    monkeypatch.setattr('utils.batch_repo.try_repark_transition', fake_repark)
    monkeypatch.setattr('utils.skill_fit.compute_for_session', lambda sid: None)

    def fake_audit(sid, status, **kw):
        events.append(f'audit:{status}')

    monkeypatch.setattr('utils.execution_audit.finish_latest_running', fake_audit)

    def fake_transition(sid, status, **kw):
        events.append(f'transition:{status}')
        return {'batch_status': None}

    monkeypatch.setattr('utils.batch_repo.transition_child', fake_transition)
    worker = _worker()
    worker._mark_failed('sess-1', 'b-1', 'boom', generation=3, fencing_token=9)
    assert 'audit:failed' in events        # 未命中 → 常规 failed 审计收口
    assert 'transition:failed' in events   # 未命中 → 常规 failed 终态落库
    if 'repark' in events:  # 实现落地后恒真：repark 尝试先于 failed 审计
        assert events.index('repark') < events.index('audit:failed')
