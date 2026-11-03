"""完成保证测试：消灭「OC 会话还在跑但子任务已 completed」的分叉。

背景（2026-11-03 生产复现）：_await_finished 只看最后一条 assistant 消息的
单点 finished 判定即返回，且正常完成路径不 abort OC——三个过早窗口：
  W1 tool-calls 裁决竞态（工具 part 已 completed、下一条消息还没出现的瞬间
     与「回合真结束」REST 视图完全相同）；
  W2 auto-compaction autocontinue（finish='stop' 后 OC 自动续跑）；
  W3 完成不 abort 无兜底（判定错了永久分叉，无机制发现）。

修复语义（见 _await_finished / _finish_with_guarantee）：
  1. 稳定窗：finished 判定须连续 FINISH_CONFIRM_POLLS 个轮询成立且
     assistant 消息 id 集合不变；
  2. 止血 abort：完成返回前 best-effort abort 主会话与在跑的 task 子代理
     （租约失守时不得 abort——接管者可能复用会话）；
  3. 终拉对账：abort 后再拉一次，id 集合变多 = 判定过早实锤，记
     child.premature_finish 事件（best-effort）。

红绿纪律：T1-T5/T7 在修复前 commit 上必须红（旧代码单点判定/无 abort），
T6 是守护新边缘的 green 守卫。桩模式照抄 test_batch_engine.py 的 facade 层
side_effect 序列（消息形状 = _summarize 的映射后形态）。
"""
import threading

import pytest


def _msg(mid, *, finished, finish='stop', running_tool=False, content=None,
         error=None):
    return {'role': 'assistant', 'id': mid, 'finished': finished,
            'finish': finish, 'running_tool': running_tool,
            'content': content or [], 'error': error}


def _text(t):
    return [{'type': 'text', 'text': t}]


def _mk_worker(monkeypatch, seq, aborted, poll_sec=0.02, confirm=None):
    """facade 层假 OC：list_messages 按 seq 逐次弹（耗尽后重复末元素），
    abort_session 记录目标会话 id。"""
    from unittest.mock import MagicMock
    import utils.batch_engine as eng
    w = eng.BatchWorker()
    w.POLL_INTERVAL_SEC = poll_sec
    w.PROGRESS_PERSIST_SEC = 999
    w.QUESTION_CHECK_INTERVAL = 999
    if confirm is not None:
        w.FINISH_CONFIRM_POLLS = confirm
    calls = {'i': 0}
    fake = MagicMock()

    def lm(oc, directory=''):
        i = min(calls['i'], len(seq) - 1)
        calls['i'] += 1
        return list(seq[i])
    fake.list_messages.side_effect = lm
    fake.abort_session.side_effect = lambda oc, directory='': aborted.append(oc)
    monkeypatch.setattr(eng, 'opencode_client', fake)
    return w, calls


def test_t1_tool_calls_gap_race_waits_for_real_finish(monkeypatch):
    """W1 竞态：工具结果落地、下一条消息尚未出现的瞬间，旧代码把 tool-calls
    消息判成回合结束立刻返回——新语义必须等到真正的终态消息，且返回前
    abort 已发出。"""
    m1 = _msg('m1', finished=True, finish='tool-calls',
              content=[{'type': 'tool_use', 'name': 'bash', 'status': 'completed',
                        'output_len': 5, 'child_sid': None}])
    m2_run = _msg('m2', finished=False, finish=None, content=_text('partial'))
    m2_done = _msg('m2', finished=True, finish='stop', content=_text('final'))
    aborted = []
    w, calls = _mk_worker(monkeypatch, [[m1], [m1, m2_run], [m1, m2_done]],
                          aborted)
    preview, msg = w._await_finished('oc', 'sid-test')
    assert msg['id'] == 'm2'          # 旧代码此处返回 m1 → 红
    assert aborted == ['oc']          # 旧代码不 abort → 红


def test_t2_stable_finish_aborts_before_return(monkeypatch):
    """W3 止血：即使稳定窗判定成立（回合真结束了），返回前也必须 best-effort
    abort——这是不变式（completed ⟹ OC 回合已终结）的硬保证，幂等 no-op。"""
    done = _msg('m1', finished=True, content=_text('ok'))
    aborted = []
    w, _ = _mk_worker(monkeypatch, [[done]], aborted)
    w._await_finished('oc', 'sid-test')
    assert aborted == ['oc']          # 旧代码无 abort → 红


def test_t3_finish_aborts_running_task_children(monkeypatch):
    """子代理止血：完成时刻仍在跑的 task 子会话一并 abort，否则父级 completed
    而子代理烧 token 的分叉仍在。"""
    done = _msg('m1', finished=True,
                content=[{'type': 'tool_use', 'name': 'task', 'status': 'running',
                          'output_len': 0, 'child_sid': 'oc-child'}])
    aborted = []
    w, _ = _mk_worker(monkeypatch, [[done]], aborted)
    w._await_finished('oc', 'sid-test')
    assert 'oc' in aborted and 'oc-child' in aborted   # 旧代码 → 红


def test_t4_premature_finish_leaves_evidence(monkeypatch):
    """W2 取证：稳定窗通过、abort 已发，但终拉发现判定时刻之后又冒了新消息
    （autocontinue/竞态实锤）——必须留 child.premature_finish 事件，且返回
    终拉里更完整的最后一条消息。"""
    m1 = _msg('m1', finished=True, content=_text('early'))
    m2 = _msg('m2', finished=True, content=_text('late continuation'))
    aborted = []
    w, _ = _mk_worker(monkeypatch, [[m1], [m1], [m1, m2]], aborted)
    events = []
    import utils.batch_events as be
    monkeypatch.setattr(
        be, 'append_event',
        lambda *a, **k: events.append((a, k)) or 'eid')
    preview, msg = w._await_finished('oc', 'sid-test', batch_id='b1')
    assert msg['id'] == 'm2'
    fired = [e for e in events if e[0][1] == 'child.premature_finish']
    assert fired, 'must record premature_finish evidence'   # 旧代码 → 红
    assert fired[0][0][0] == 'b1'


def test_t5_candidate_idset_change_resets_window(monkeypatch):
    """稳定窗计数按 id 集合：候选期间冒新消息（集合变化）计数重置，不能拿
    上一候选的计数直接续判——否则 W1 竞态里第二条消息刚 finished 一次就放行。"""
    m1 = _msg('m1', finished=True, content=_text('one'))
    m2 = _msg('m2', finished=True, content=_text('two'))
    aborted = []
    # confirm=3：poll1 候选 {m1}=1；poll2 集合变 {m1,m2} 重置为 1；
    # poll3=2；poll4=3 才确认。旧代码 poll1 即返回 m1 → 红。
    w, calls = _mk_worker(monkeypatch,
                          [[m1], [m1, m2], [m1, m2], [m1, m2]],
                          aborted, confirm=3)
    preview, msg = w._await_finished('oc', 'sid-test')
    assert msg['id'] == 'm2'
    assert calls['i'] >= 5   # 4 次判定轮询 + 终拉，证明没有提前放行


def test_t6_deadline_exit_with_candidate_completes_not_timeout(monkeypatch):
    """守护新边缘：SESSION_TIMEOUT 到点退出时若稳定窗已有 ≥1 次确认（回合
    实际已结束、只差确认轮询），走完成路径而非超时失败。"""
    m1 = _msg('m1', finished=True, content=_text('done'))
    aborted = []
    # POLL=0.05s、deadline=0.08s、confirm=4：第 2 次轮询后（cand=2）到点退出
    # ——须按完成收口。margin 2×，防慢机 flake。
    w, _ = _mk_worker(monkeypatch, [[m1]], aborted, poll_sec=0.05, confirm=4)
    w.SESSION_TIMEOUT_SEC = 0.08
    preview, msg = w._await_finished('oc', 'sid-test')
    assert msg['id'] == 'm1'
    assert aborted == ['oc']


def test_t7_lease_lost_never_aborts(monkeypatch):
    """既有语义守护（test_lease_lost_drops_without_abort 同款）：租约失守时
    不得 abort——接管者可能正在复用该 OpenCode 会话。稳定窗通过但 lease_lost
    置位 → _LeaseLost 且零 abort。"""
    from utils.batch_engine import _LeaseLost
    m1 = _msg('m1', finished=True, content=_text('ok'))
    aborted = []
    w, _ = _mk_worker(monkeypatch, [[m1], [m1]], aborted)
    lost = threading.Event()

    # 第 2 次轮询返回数据前置位：循环顶检查已过，逼出「确认后、abort 前」
    # 的租约检查路径。
    import utils.batch_engine as eng
    real_lm = eng.opencode_client.list_messages.side_effect
    state = {'n': 0}

    def lm(oc, directory=''):
        state['n'] += 1
        if state['n'] == 2:
            lost.set()
        return real_lm(oc, directory)
    eng.opencode_client.list_messages.side_effect = lm

    with pytest.raises(_LeaseLost):
        w._await_finished('oc', 'sid-test', lease_lost=lost)
    assert aborted == []             # 旧代码此处正常返回不 abort → 红
