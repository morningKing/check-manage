# -*- coding: utf-8 -*-
"""batch_repo 接管扩展真库测试（spec D3）：repark CAS 收口 +
continue_child 准入 paused（repark 通道）+ resume/transition 恒清
continue_repark 标志。

模式照抄 test_batch_execution_safety_p0.py 的 db_conn/user_id fixture 纪律：
uuid 主键播种 users/batches/sessions，teardown 全量清理（列集合参考
test_takeover_repo.py 的 takeover_ids）。
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils import batch_repo  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def user_id(db_conn):
    uid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, 'x', %s, 'developer')",
            (uid, f'p4_user_{uid[:8]}', f'P4 User {uid[:8]}'),
        )
    db_conn.commit()
    yield uid
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_chat_sessions WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM ai_chat_batches WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    db_conn.commit()


def _seed_child(db_conn, uid, status, *, repark=False, generation=0,
                fencing_token=0, done=0):
    """播种「单子任务批次」，返回 {'uid','bid','sid'}。"""
    bid, sid = str(uuid.uuid4()), str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, status, "
            "  total, done, failed) "
            "VALUES (%s, %s, 'p4-test', 'p', %s, 1, %s, 0)",
            (bid, uid, 'completed' if done else 'running', done))
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, title, status, batch_id, "
            "  batch_seq, batch_input_file, execution_generation, fencing_token, "
            "  continue_repark) "
            "VALUES (%s, %s, '子任务1', %s, %s, 0, 'f0.csv', %s, %s, %s)",
            (sid, uid, status, bid, generation, fencing_token, repark))
    db_conn.commit()
    return {'uid': uid, 'bid': bid, 'sid': sid}


@pytest.fixture
def seeded_paused_child(db_conn, user_id):
    """paused 子任务种子（generation=3 / fencing_token=7 为已知基线）。
    接管轮中间态（running + continue_repark=true，try_repark 的 CAS 前提）
    由 test_transition_child_expect_repark_cas 在其上显式布置——见该用例
    内的「播种」注释；continue_child(repark=True) 的 paused 准入用例则
    直接以 paused 状态调用。"""
    return _seed_child(db_conn, user_id, 'paused', repark=False,
                       generation=3, fencing_token=7)


@pytest.fixture
def seeded_running_child(db_conn, user_id):
    """普通运行中子任务：无接管标志 → repark 必须未命中。"""
    return _seed_child(db_conn, user_id, 'running', repark=False,
                       generation=3, fencing_token=7)


@pytest.fixture
def seeded_completed_child(db_conn, user_id):
    """终态带残留标志：completed + continue_repark=true + done=1，
    验证终态 continue 仍回滚计数且恒清标志。"""
    return _seed_child(db_conn, user_id, 'completed', repark=True,
                       generation=3, fencing_token=7, done=1)


def _fetch_child(db_conn, sid):
    with db_conn.cursor() as cur:
        cur.execute("SELECT status, continue_repark, continue_prompt, pause_requested "
                    "FROM ai_chat_sessions WHERE id=%s", (sid,))
        r = cur.fetchone()
        assert r, 'child row missing'
    return dict(zip(('status', 'continue_repark', 'continue_prompt', 'pause_requested'), r))


def _fetch_status_flag(db_conn, sid):
    r = _fetch_child(db_conn, sid)
    return r['status'], r['continue_repark']


def _fetch_counters(db_conn, bid):
    with db_conn.cursor() as cur:
        cur.execute("SELECT done, failed FROM ai_chat_batches WHERE id=%s", (bid,))
        return cur.fetchone()


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------

def test_transition_child_expect_repark_cas(db_conn, seeded_paused_child):
    """repark CAS：continue_repark=true + running + generation 命中才落到 paused；标志被清。"""
    # 播种 running + continue_repark=true + fencing_token=7, generation=3
    # （= continue(repark) 后被 worker 认领的接管轮中间态；种子是 paused，
    #   这里显式布置 try_repark CAS 要求的 running+标志+已知 gen/token）
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET status='running', continue_repark=true, "
                    "execution_generation=3, fencing_token=7 WHERE id=%s",
                    (seeded_paused_child['sid'],))
    db_conn.commit()
    res = batch_repo.try_repark_transition(seeded_paused_child['sid'],
                                           generation=3, fencing_token=7)
    assert res is not None
    status, repark = _fetch_status_flag(db_conn, seeded_paused_child['sid'])
    assert (status, repark) == ('paused', False)
    # 批计数不动（done/failed 不变）
    assert _fetch_counters(db_conn, seeded_paused_child['bid']) == (0, 0)


def test_try_repark_miss_when_flag_absent(db_conn, seeded_running_child):
    res = batch_repo.try_repark_transition(seeded_running_child['sid'],
                                           generation=3, fencing_token=7)
    assert res is None  # 非接管轮 → 调用方回落常规终态路径
    assert _fetch_status_flag(db_conn, seeded_running_child['sid'])[0] == 'running'


def test_continue_child_paused_repark_sets_flag_and_keeps_counters(db_conn, seeded_paused_child):
    batch_repo.continue_child(seeded_paused_child['uid'], seeded_paused_child['bid'],
                              seeded_paused_child['sid'], '人工纠正指令', repark=True)
    row = _fetch_child(db_conn, seeded_paused_child['sid'])
    assert row['status'] == 'pending' and row['continue_repark'] is True
    assert row['continue_prompt'] == '人工纠正指令'
    assert row['pause_requested'] is False
    assert _fetch_counters(db_conn, seeded_paused_child['bid']) == (0, 0)  # paused 不占计数


def test_continue_child_paused_without_repark_rejected(db_conn, seeded_paused_child):
    with pytest.raises(ValueError):
        batch_repo.continue_child(seeded_paused_child['uid'], seeded_paused_child['bid'],
                                  seeded_paused_child['sid'], 'x', repark=False)


def test_continue_child_terminal_rolls_back_counter_and_clears_flag(db_conn, seeded_completed_child):
    # 播种 completed 子任务（done=1）且带残留 continue_repark=true
    batch_repo.continue_child(seeded_completed_child['uid'], seeded_completed_child['bid'],
                              seeded_completed_child['sid'], '追问')
    row = _fetch_child(db_conn, seeded_completed_child['sid'])
    assert row['status'] == 'pending' and row['continue_repark'] is False
    assert _fetch_counters(db_conn, seeded_completed_child['bid'])[0] == 0  # done 回滚


def test_resume_child_clears_repark_flag(db_conn, seeded_paused_child):
    batch_repo.continue_child(seeded_paused_child['uid'], seeded_paused_child['bid'],
                              seeded_paused_child['sid'], 'x', repark=True)
    # 模拟接管轮被协作暂停回 paused（带残留标志），再 resume
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET status='paused', continue_repark=true "
                    "WHERE id=%s", (seeded_paused_child['sid'],))
    db_conn.commit()
    batch_repo.resume_child(seeded_paused_child['uid'], seeded_paused_child['bid'],
                            seeded_paused_child['sid'])
    assert _fetch_status_flag(db_conn, seeded_paused_child['sid']) == ('pending', False)


def test_mark_cancelled_wins_over_repark_flag(db_conn, seeded_paused_child, monkeypatch):
    """取消优先于接管标志（spec §4.2 精神）：running + continue_repark=true 的
    人工接管轮运行期间，worker 取消收口（_mark_cancelled）必须立即生效——
    expect_repark 不得挡取消（否则 CAS 0 行 → miss 被丢弃 → 任务继续跑到
    repark，取消静默失效）。命中后标志随 SET 恒清，cancelled 记 failed 聚合。"""
    from utils.batch_engine import BatchWorker
    # 播种接管轮中间态：running + 标志 + cancel_requested + 已知 gen/token
    with db_conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET status='running', continue_repark=true, "
                    "cancel_requested=true, execution_generation=3, fencing_token=7 "
                    "WHERE id=%s", (seeded_paused_child['sid'],))
    db_conn.commit()
    # 轻量实例（绕过 __init__ 的线程/租约）+ generation 直通 + 收口旁路桩
    w = BatchWorker.__new__(BatchWorker)
    monkeypatch.setattr(BatchWorker, '_resolve_generation',
                        lambda self, sid, gen: gen)
    import utils.execution_audit as _audit
    import utils.skill_fit as _sfit
    monkeypatch.setattr(_audit, 'finish_latest_running', lambda *a, **k: None)
    monkeypatch.setattr(_sfit, 'compute_for_session', lambda *a, **k: None)
    w._mark_cancelled(seeded_paused_child['sid'], seeded_paused_child['bid'],
                      generation=3, fencing_token=7)
    status, repark = _fetch_status_flag(db_conn, seeded_paused_child['sid'])
    assert (status, repark) == ('cancelled', False)
    # cancelled 记入 failed 聚合（既有语义），done 不动
    assert _fetch_counters(db_conn, seeded_paused_child['bid']) == (0, 1)
