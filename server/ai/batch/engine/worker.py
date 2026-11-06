import logging
import os
import select
import threading
import time
import traceback
import uuid

from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import requests
from psycopg2.extras import RealDictCursor

from db import get_db

from .exceptions import _SessionTimeout, _TurnFailed
from .lifecycle import _recompute_batch_status
from .facade import opencode_client, execution_lease_ttl
from .runner import WorkerRunnerMixin
from .gates import WorkerGatesMixin
from .subtasks import WorkerSubtasksMixin
from .transitions import WorkerTransitionsMixin

logger = logging.getLogger(__name__)

class WorkerLifecycleMixin:
    # 租约 key 可注入（测试隔离/多部署共存）；生产默认 'batch'
    LEASE_KEY = os.getenv('AI_BATCH_LEASE_KEY', 'batch')
    POLL_INTERVAL_SEC = 2
    # P3-C1（PG LISTEN/NOTIFY 替代纯轮询）：子会话入队（status→pending）时
    # 迁移 2026_09_26_batch_notify_trigger.py 的触发器 pg_notify 本频道，
    # dispatcher 的专用 LISTEN 连接 select 到可读即唤醒 claim——入队到
    # claim 从「等下一轮 2s poll」变成事件驱动。断线自动重连
    # （LISTEN_RECONNECT_SEC 退避），重连窗口内仍走 _wake.wait 轮询兜底。
    LISTEN_CHANNEL = 'batch_claim_ready'
    LISTEN_RECONNECT_SEC = 5.0
    # LISTEN 健在时 select 的等待切片上限：通知即时唤醒；切片上限同时保证
    # 外部 notify() 置位的 _wake 最多一个切片内被看到（不差于原 2s poll）。
    LISTEN_SLICE_SEC = 2.0
    # Hard per-child cap. Default 0 = NO cap: a batch child runs as long as it
    # keeps making progress (legit long tasks / subagent delegations shouldn't be
    # killed by an arbitrary clock). Override with AI_BATCH_SESSION_TIMEOUT_SEC.
    SESSION_TIMEOUT_SEC = int(os.getenv('AI_BATCH_SESSION_TIMEOUT_SEC', '0'))
    # Persist the conversation at most this often off the REST poll, so a running
    # batch child can be viewed live without flooding the DB.
    PROGRESS_PERSIST_SEC = 2.5
    # The real safety net (kept even with no hard cap): if the turn produces NO
    # new output AND nothing is in flight for this long, it's a half-open/frozen
    # turn — fail it. Tool/subagent activity counts as progress (see
    # _progress_signature / the active_tool reset in _await_finished), so a
    # working turn never trips this; only a genuinely dead one does.
    STALL_TIMEOUT_SEC = 180
    # 思考盲区阈值（2026-11-04 生产误报）：OpenCode 对 reasoning 不做流式
    # 落盘——实测 11429 字符的思考 part 存活 67.9s 期间 REST 全接口零增量
    # （part/message/session 的 updated 全部冻结在思考结束时刻）。单次思考
    # 超过 STALL_TIMEOUT_SEC 的活回合会被误判 stalled 并 abort。判定为
    # 「思考盲区」（step 进行中，或最后一条消息无任何实质 part 且未终结，
    # 见 _thinking_blindspot）的冻结改用本阈值——盲区与 provider 真死在
    # 轮询视角不可区分，只能拉长不能免除。0 = 禁用分层（一律 STALL 阈值）。
    THINKING_STALL_TIMEOUT_SEC = int(os.getenv('AI_BATCH_THINKING_STALL_SEC', '600'))
    # Tool-stuck watchdog: a tool call whose (name, status, output) signature
    # stays frozen this long is hung — question 挂起、MCP 服务无响应、bash 死循环
    # 都会被它接住（running tool 会让上面的 STALL 永远不触发，没有这道闸一个
    # 卡死的工具调用就能把子任务挂到天荒地老）。子代理委托（task 工具）不受
    # 此限：只要子代理自己的消息还在推进，看门狗就一直顺延 —— 见
    # _subagent_progressing。0 = 关闭（不建议）。
    TOOL_STALL_TIMEOUT_SEC = int(os.getenv('AI_BATCH_TOOL_STALL_SEC', '900'))
    # 子代理作保探针（_children_progressing）的连续失败预算：读子代理消息
    # 连续失败超过这个次数就不再作保（顺延会退化成永活，看门狗失效）；
    # 瞬时抖动（≤ 此值）仍按「查不到 ≠ 死了」豁免。
    CHILD_PROBE_ERROR_BUDGET = 3
    # 自动重试预算（批任务无人值守的"失败不终局"）：可重试类失败（停滞/
    # 工具卡死/网络异常）把子任务重新排队，预算用尽才落 failed。0 = 关闭。
    MAX_AUTO_RETRY = int(os.getenv('AI_BATCH_MAX_AUTO_RETRY', '2'))
    # 完成保证（2026-11-03 生产分叉修复：子会话还在跑、状态已 completed）：
    # finished 判定须连续 N 个轮询成立且 assistant 消息 id 集合不变才放行
    # （tool-calls 裁决间隙 / autocontinue 与「回合真结束」的单点快照不可
    # 区分）。1 = 回退旧的单点判定语义。
    FINISH_CONFIRM_POLLS = max(1, int(os.getenv('AI_BATCH_FINISH_CONFIRM_POLLS', '2') or 2))
    # 放行后 best-effort abort 主会话与在跑的 task 子代理再返回——回合真
    # 结束时为幂等 no-op，判定过早时杀死 zombie。不变式「completed ⟹ OC
    # 回合已终结」由它硬保证。0 = 回退旧行为（不 abort）。
    ABORT_ON_FINISH = os.getenv('AI_BATCH_ABORT_ON_FINISH', '1') != '0'
    # _TurnFailed.name 白名单：只有这些 provider 错误值得重试。
    # ProviderAuthError（密钥错，重试必然再炸）、MessageAbortedError（用户
    # 主动中断）、ContextOverflowError（上下文超限，续跑只会更大）不在列。
    RETRYABLE_TURN_FAILED = {'APIError', 'UnknownError',
                             'MessageOutputLengthError', 'StructuredOutputError'}
    # 自动续跑时注入的提示词：与 resume 同语义——从原会话上下文继续。
    # 带原因版由 _auto_retry_prompt 组合（对齐 _maybe_gate_retry 的定向修复
    # 范式）——原因不随重试丢失是 2026-11-03 取证缺口修复的三渠道之一。
    AUTO_RETRY_PROMPT_TAIL = (
        '请先查看工作区现状与已有对话上下文，从中断处继续完成原任务；'
        '已完成的部分不要重做，最终给出完整结果。')
    AUTO_RETRY_CONTINUE_PROMPT = (
        '上一轮执行因异常中断（系统已自动重试）。' + AUTO_RETRY_PROMPT_TAIL)
    # How often (seconds) the poll loop checks for & auto-rejects pending
    # question-tool requests. OpenCode parks the turn on an unanswered question
    # indefinitely; the reject resolves the tool call and the model continues.
    QUESTION_CHECK_INTERVAL = 10

    def __init__(self):
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._executor = ThreadPoolExecutor(
            max_workers=self._resolve_max_concurrent())
        self._running_session_ids: set = set()
        # 动作账本健康标记(设计 §5.1):_persist_conversation 每次落账后更新,
        # 门禁核对据此区分 failed 与 inconclusive;核对后清除。
        self._ledger_health: dict = {}
        # 委派级门禁（2026-09-30）：定向 verifier 组在名单内子代理全部终态时
        # 提前判定——failed 且批未开 gate_retry → 置 cancel_requested 立即停止；
        # 开启 → 暂存 verdict 交终态合并（gate_retry 定向修复仍生效）。
        # _deleg_gate_done: (session_id, subtask_id) -> True（已核对，防重复）；
        # _deleg_gate_results: session_id -> [verdict rows]；_gate_checking:
        # 判官同步核对期间豁免 tool-stuck 看门狗（核对会暂停轮询线程）。
        # 快照短路（批满负载优化）：session_id -> 最近一次持久化的消息签名
        self._persist_sig: dict = {}
        # 委派豁免（2026-10-12）：session_id -> 上次快照是否含未终态 task
        # part（=委派执行中）。委派期根静止、sig 恒等，但子代理树在持续
        # 产出——此标记为真时不短路（否则子代理消息直到委派完成才一次性
        # 落库，气泡无实时内容，生产实测）。
        self._persist_open_delegation: dict = {}
        self._deleg_gate_done: dict = {}
        self._deleg_gate_results: dict = {}
        self._gate_checking: set = set()
        self._lock = threading.Lock()
        self._dispatcher: threading.Thread | None = None
        # F9：重排前的旧 attempt id（session_id -> attempt_id），下一次
        # create_attempt 以 parent_attempt_id 串链。跨重启由
        # _consume_parent_attempt 查库兜底（最新 recovering + 白名单）。
        self._parent_attempt: dict = {}
        # P0 单实例租约（spec §5.3）：持lease才跑 dispatcher 与 _restart_audit。
        self._lease_owner: str | None = None
        self._holds_lease = False
        # F3（pending-flip 修复）：每次执行的子会话租约心跳句柄
        # （session_id -> stop Event）。心跳独立线程续租，覆盖 dispatch 前的
        # 冷目录 create_session、子代理收敛等待、门禁核对等非轮询阶段；
        # _run_one 的 finally 统一停止（含测试直呼 _run_one 的路径）。
        self._heartbeats: dict = {}
        # F5：缓重排计时器（session_id -> 首次判飞行中的时刻）。对账器每轮
        # 末清掉已离开 stale 集的键，防无界增长。
        self._recover_defer_since: dict = {}
        # F5 二次确认（2026-11-03）：步骤边界/空快照的「模糊态」跟踪
        # （session_id -> {'sig', 'since'}）。同一签名稳定超过
        # RECOVER_AMBIGUOUS_STABLE_SEC 才判空闲，避免单次采样把活回合重排。
        self._recover_ambiguous: dict = {}
        self._lease_thread: threading.Thread | None = None
        # 租约被占时的后台重试（快速重启的 TTL 窗口内不永久放弃 dispatcher）
        self._acquire_retry_thread: threading.Thread | None = None
        # 租约 key 可注入（测试隔离/多部署共存）；生产默认 'batch'
        self._lease_key = self.LEASE_KEY
        # P3-C1：专用 LISTEN 连接（独立直连，不占 ThreadedConnectionPool
        # 名额）。None = 未连接/不可用（轮询兜底接管）；_listen_next_try 是
        # 断线重连的退避计时器（monotonic 秒）。
        self._listen_conn = None
        self._listen_next_try = 0.0
        self._listen_lock = threading.Lock()

    @staticmethod
    def _resolve_max_concurrent() -> int:
        """执行并行度：跟随 AI_BATCH_CONCURRENCY，与 _effective_concurrency
        的认领上限同源。2026-10-05 S2 能力压测发现：原硬编码类属性 3 使 env
        只放大认领数、执行并行度恒 3——「并发终态窗」物理不成立，容量阶梯
        各级"无拐点"亦由此而来。env 缺省仍为 3（未配置环境行为不变）。"""
        try:
            return max(1, int(os.getenv('AI_BATCH_CONCURRENCY', '3')))
        except ValueError:
            return 3

    # --- lifecycle ---

    # 抢占失败后的重试间隔（旧实例 lease 过期即自动接管）
    ACQUIRE_RETRY_SEC = float(os.getenv('AI_BATCH_LEASE_RETRY_SEC', '5'))
    LEASE_KEY = os.getenv('AI_BATCH_LEASE_KEY', 'batch')

    def start(self):
        if self._dispatcher and self._dispatcher.is_alive():
            return
        # P0 单实例保护（spec §5.3）：抢占不到租约就不启动 dispatcher——
        # 多进程部署（gunicorn 多 worker 等）只有一个实例真正执行批任务。
        from ai.batch import execution_lease as execution_lease
        self._lease_owner = execution_lease.owner_id()
        acquired, _token = execution_lease.acquire(self._lease_key, self._lease_owner,
                                                   lease_kind='dispatcher')
        if not acquired:
            # 运维关键：快速重启（上一个实例的 lease 尚在 TTL 内）不能永久
            # 放弃 dispatcher——进入后台重试，旧租约一过期即自动接管。
            logger.warning(
                'batch worker lease NOT acquired; retrying every %ss until '
                'the lease becomes available', self.ACQUIRE_RETRY_SEC)
            self._holds_lease = False
            self._acquire_retry_thread = threading.Thread(
                target=self._acquire_retry_loop, daemon=True,
                name='batch-worker-lease-retry')
            self._acquire_retry_thread.start()
            return
        self._start_with_lease()

    def _acquire_retry_loop(self):
        from ai.batch import execution_lease as execution_lease
        while not self._stop.is_set():
            if self._stop.wait(self.ACQUIRE_RETRY_SEC):
                return
            ok, _token = execution_lease.acquire(self._lease_key, self._lease_owner,
                                                     lease_kind='dispatcher')
            if not ok:
                continue
            logger.info('batch worker lease acquired after retry; '
                        'starting dispatcher')
            self._holds_lease = True
            # H8：不再无条件重放（旧实现 running→pending + 重发原始 prompt，
            # 重做已产生的外部副作用）。遗留 running 行由恢复决策表接管：
            # _reconcile_stale_running 按 lease/checkpoint/oc 会话状态分流。
            self._dispatcher = threading.Thread(
                target=self._dispatcher_loop, daemon=True, name='batch-worker')
            self._dispatcher.start()
            self._lease_thread = threading.Thread(
                target=self._lease_loop, daemon=True, name='batch-worker-lease')
            self._lease_thread.start()
            return

    def _start_with_lease(self):
        self._holds_lease = True
        # H8：同上，重启后遗留 running 行交给 _reconcile_stale_running 决策
        # （lease 未过期等待 / 404 失败 / unknown effect → needs_review /
        # 会话活着 → 原地续跑），不做无条件重放。
        self._dispatcher = threading.Thread(
            target=self._dispatcher_loop, daemon=True, name='batch-worker')
        self._dispatcher.start()
        self._lease_thread = threading.Thread(
            target=self._lease_loop, daemon=True, name='batch-worker-lease')
        self._lease_thread.start()

    def _lease_loop(self):
        """租约心跳：续租失败连续超过 3 次（租约丢失或 DB 持续不可用）时
        停止本实例的 dispatcher——宁可整实例让位，不可与接管者双跑。"""
        from ai.batch import execution_lease as execution_lease
        misses = 0
        while not self._stop.is_set():
            if self._stop.wait(execution_lease.DEFAULT_HEARTBEAT_SEC):
                break
            if execution_lease.heartbeat(self._lease_key, self._lease_owner):
                misses = 0
            else:
                misses += 1
                logger.warning('batch worker lease heartbeat missed (%d/3)',
                               misses)
                if misses >= 3:
                    logger.error('batch worker lease LOST; stopping dispatcher')
                    self._stop.set()
                    self._wake.set()
                    break

    def stop(self, *, wait: bool = True, timeout: float = 5.0):
        """Stop the dispatcher and (optionally) wait for it + the executor.

        Without `wait`, the dispatcher receives the stop signal but its daemon
        thread may still be inside a DB poll cycle when this returns — that
        leaks into subsequent tests under pytest because the next claim picks
        up rows the next test just seeded. Default to waiting so tests behave.
        """
        self._stop.set()
        self._wake.set()
        self._listen_close()  # P3-C1：先关 LISTEN 连接再 join dispatcher
        if not wait:
            return
        if self._dispatcher and self._dispatcher.is_alive():
            self._dispatcher.join(timeout=timeout)
        # cancel_futures so pending submissions don't keep the threadpool alive
        self._executor.shutdown(wait=True, cancel_futures=True)
        # Allow a follow-on start() to spin up a fresh executor.
        self._executor = ThreadPoolExecutor(
            max_workers=self._resolve_max_concurrent())
        if self._acquire_retry_thread and self._acquire_retry_thread.is_alive():
            self._acquire_retry_thread.join(timeout=2)
        if self._holds_lease:
            from ai.batch import execution_lease as execution_lease
            execution_lease.release(self._lease_key, self._lease_owner)
            self._holds_lease = False

    def notify(self):
        self._wake.set()

    # --- dispatcher ---

    # 运行中对账器节流：每分钟最多跑一轮（与 dispatcher 同线程，无竞态）。
    RECONCILE_INTERVAL_SEC = float(os.getenv('AI_BATCH_RECONCILE_SEC', '60'))
    # F5：对账器缓重排兜底上限（秒）。worker 死后没有 STALL 看门狗盯着
    # OC 回合，「看似活着」的永久悬挂由这个上限顶替——超时后照常重排。
    RECOVER_DEFER_MAX_SEC = float(
        os.getenv('AI_BATCH_RECOVER_DEFER_MAX_SEC', '1800'))
    # F5 二次确认（2026-11-03）：最新 assistant 消息以 continuation finish
    # （tool-calls/tool_use）收尾、且工具已落地时，单次采样分不清「步骤间隙
    # （下一步马上来）」和「回合真的结束」（question 被自动拒绝时 OpenCode
    # 直接把消息完结、finish 却留在 tool-calls）。这类模糊态（含「REST 返回
    # 空快照但库里已有消息」）要求签名在窗口内纹丝不动才判空闲；否则缓重排。
    # 0/负数 = 关闭二次确认（退化为旧的单次采样语义）。
    RECOVER_AMBIGUOUS_STABLE_SEC = float(
        os.getenv('AI_BATCH_RECOVER_AMBIGUOUS_SEC', '60'))

    # --- PG LISTEN/NOTIFY（P3-C1）---

    def _listen_connect(self):
        """建立（或重连）专用 LISTEN 连接。

        独立 psycopg2 直连——不经过 db.get_db 的 ThreadedConnectionPool，
        不占池名额（连接池连接被归还后不能保证留在本线程，NOTIFY 需要一条
        专属于 listener 的长连接）。autocommit=True 让 LISTEN 立即生效。
        失败只记录并按退避计时器顺延重试，绝不抛出——dispatcher 始终有
        POLL_INTERVAL/_wake.wait 轮询兜底。"""
        with self._listen_lock:
            if self._stop.is_set():
                return
            if self._listen_conn is not None and not self._listen_conn.closed:
                return
            if time.monotonic() < self._listen_next_try:
                return  # 断线退避窗口内：不再尝试，兜底轮询接管
            try:
                import psycopg2
                from config import DB_CONFIG
                conn = psycopg2.connect(connect_timeout=5, **DB_CONFIG)
                conn.autocommit = True
                with conn.cursor() as cur:
                    cur.execute(f'LISTEN {self.LISTEN_CHANNEL}')
                self._listen_conn = conn
                logger.info('batch dispatcher LISTEN %s ready',
                            self.LISTEN_CHANNEL)
            except Exception:
                logger.warning('batch dispatcher LISTEN connect failed; '
                               'poll fallback stays active', exc_info=True)
                self._listen_conn = None
                self._listen_next_try = (time.monotonic()
                                         + self.LISTEN_RECONNECT_SEC)

    def _listen_close(self):
        """关闭 LISTEN 连接（stop() 跨线程调用安全，best-effort）。"""
        with self._listen_lock:
            conn, self._listen_conn = self._listen_conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def _listen_mark_down(self):
        """LISTEN 连接异常后统一收口：关闭连接 + 启动重连退避计时器。"""
        self._listen_close()
        with self._listen_lock:
            self._listen_next_try = (time.monotonic()
                                     + self.LISTEN_RECONNECT_SEC)

    def _wait_for_wake(self, timeout: float) -> None:
        """dispatcher 的等待原语（替代裸 self._wake.wait(timeout)）。

        LISTEN 健在：select 在通知 socket 上等——pg_notify 即时唤醒
        （收到 → conn.poll() 消费 + _wake.set() 立即返回）。select 按
        LISTEN_SLICE_SEC 切片，保证外部 notify() 置位的 _wake 最多一个
        切片内被看到（不差于原 2s poll 的响应口径）。

        LISTEN 不可用（未连上/断线退避窗口内）：退回既有
        self._wake.wait(timeout) 轮询兜底——LISTEN 失败时行为与改造前
        完全一致，不会因 NOTIFY 通道问题影响派发。"""
        deadline = time.monotonic() + timeout
        while not self._stop.is_set():
            if self._wake.is_set():
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            conn = self._listen_conn
            if conn is None or conn.closed:
                self._listen_connect()
                conn = self._listen_conn
            slice_sec = min(remaining, self.LISTEN_SLICE_SEC)
            if conn is None or conn.closed:
                # 兜底：无可用 LISTEN 连接（连接中失败/退避窗口内）
                self._wake.wait(slice_sec)
                continue
            try:
                r, _w, _x = select.select([conn.fileno()], [], [], slice_sec)
            except Exception:
                # fd 已被 stop() 关闭 / 连接被内核判死：转入重连退避，
                # 本切片按 Event 兜底等待（与改造前等价）
                logger.warning('batch LISTEN connection lost; '
                               'reconnecting with backoff', exc_info=True)
                self._listen_mark_down()
                self._wake.wait(slice_sec)
                continue
            if r:
                try:
                    conn.poll()  # 消费通知，连接回到可 select 状态
                    self._wake.set()
                    return
                except Exception:
                    logger.warning('batch LISTEN poll failed; '
                                   'reconnecting with backoff', exc_info=True)
                    self._listen_mark_down()
                    continue

    def _dispatcher_loop(self):
        logger.info('batch dispatcher started')
        last_reconcile = 0.0
        try:
            # P3-C1：dispatcher 启动即尝试建立 LISTEN 连接（失败不阻断，
            # _wait_for_wake 内按退避计时器自动重试 + 轮询兜底）。
            self._listen_connect()
            while not self._stop.is_set():
                self._wait_for_wake(10)
                self._wake.clear()
                if self._stop.is_set():
                    break
                now = time.time()
                if now - last_reconcile >= self.RECONCILE_INTERVAL_SEC:
                    last_reconcile = now
                    # 对账器在 claim 之前跑（同线程），避开 claim→入账窗口
                    try:
                        self._reconcile_stale_running()
                    except Exception:
                        logger.exception('batch reconcile failed; will retry')
                if not self._dispatch_tick():
                    # The tick hit an error (DB hiccup, pool exhaustion). Back off
                    # briefly to avoid hot-looping, but KEEP the loop alive.
                    self._stop.wait(self.POLL_INTERVAL_SEC)
        finally:
            # Normally only reached via stop(). If it's ever reached otherwise,
            # this log turns a silent dead worker — the cause of "批任务一直待运行"
            # — into something diagnosable.
            logger.info('batch dispatcher exited (stop=%s)', self._stop.is_set())
            self._listen_close()  # P3-C1：dispatcher 退出即释放 LISTEN 连接
            if self._holds_lease:
                from ai.batch import execution_lease as execution_lease
                execution_lease.release(self._lease_key, self._lease_owner)
                self._holds_lease = False

    def _effective_concurrency(self) -> int:
        """P2-B1：并发度优先级 ai_settings.batch_max_concurrent > env AI_BATCH_CONCURRENCY > 3。"""
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT batch_max_concurrent FROM ai_settings LIMIT 1")
                    row = cur.fetchone()
                    if row and row[0]:
                        return max(1, int(row[0]))
        except Exception:
            pass
        return max(1, int(os.getenv('AI_BATCH_CONCURRENCY', '3')))

    def _dispatch_tick(self) -> bool:
        """Run one claim+submit cycle. Returns True normally, False if an
        exception was caught.

        NEVER raises: a transient failure here (a DB hiccup, connection-pool
        exhaustion) must not propagate out of _dispatcher_loop and kill the
        worker thread — that would leave every future batch hanging in 'pending'
        forever with no error and no recovery until Flask restarts."""
        try:
            self._cancel_pending_requests()
            with self._lock:
                # P2-B1：并发度可配——ai_settings.batch_max_concurrent >
                # env AI_BATCH_CONCURRENCY > 类属性默认 3。
                free = self._effective_concurrency() - len(self._running_session_ids)
            if free <= 0:
                return True
            pending = self._claim_pending_sessions(limit=free)
            for s in pending:
                with self._lock:
                    self._running_session_ids.add(s['id'])
                self._executor.submit(self._safe_run_one, s)
            return True
        except Exception:
            logger.exception('batch dispatcher tick failed; will retry')
            return False

    def _safe_run_one(self, session_row):
        try:
            self._run_one(session_row)
        except Exception:
            traceback.print_exc()
        finally:
            # F3 兜底：_run_one 大 try 之前（工作区准备/会话创建段）抛异常时
            # 不经过其 finally——心跳线程必须在此再停一次（pop 幂等）。
            _hb_stop = self._heartbeats.pop(session_row['id'], None)
            if _hb_stop is not None:
                _hb_stop.set()
            with self._lock:
                self._running_session_ids.discard(session_row['id'])
            self.notify()  # let the dispatcher start the next queued one

    # --- DB primitives ---

    def _claim_pending_sessions(self, limit: int) -> list:
        if limit <= 0:
            return []
        # 缺口补齐 5（P1 §9.2）：batch 级 max_concurrency——达到上限的批次
        # 本轮不认领（留 pending）
        try:
            from ai.batch.execution_budget import concurrency_blocked_batches
            self._blocked_batches = concurrency_blocked_batches()
        except Exception:
            self._blocked_batches = set()
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "WITH picked AS ( "
                    "  SELECT id FROM ai_chat_sessions "
                    "   WHERE status = 'pending' "
                    "     AND deleted_at IS NULL "
                    "     AND (batch_id IS NOT NULL OR api_key_id IS NOT NULL "
                    "          OR orchestration_run_id IS NOT NULL) "
                    "     AND (batch_id IS NULL OR batch_id::text NOT IN "
                    "          %(blocked)s) "
                    "     AND NOT EXISTS ( "
                    "       SELECT 1 FROM ai_execution_budgets b "
                    "        WHERE b.scope_type = 'batch' AND b.scope_id = ai_chat_sessions.batch_id "
                    "          AND b.enabled AND b.on_exceed IN ('drain','abort') "
                    "          AND ( "
                    "            (SELECT COALESCE(SUM(tokens_input + tokens_output), 0) "
                    "               FROM ai_execution_usage WHERE batch_id = b.scope_id) >= b.max_tokens "
                    "          OR (SELECT COALESCE(SUM(cost), 0) "
                    "               FROM ai_execution_usage WHERE batch_id = b.scope_id) >= b.max_cost "
                    "          )) "
                    "   ORDER BY created_at, batch_seq "
                    "   FOR UPDATE SKIP LOCKED LIMIT %(limit)s "
                    ") "
                    "UPDATE ai_chat_sessions s "
                    "   SET status = 'running', "
                    "       lease_owner = %(owner)s, "
                    "       lease_until = NOW() + (%(ttl)s || ' seconds')::interval, "
                    "       heartbeat_at = NOW(), "
                    "       fencing_token = s.fencing_token + 1 "
                    "FROM picked WHERE s.id = picked.id "
                    "RETURNING s.*",
                    {'limit': limit, 'owner': self._lease_owner or 'inline',
                     'blocked': tuple(self._blocked_batches or ('__none__',)),
                     'ttl': str(execution_lease_ttl())},
                )
                rows = [dict(r) for r in cur.fetchall()]
                # 启动耗时观测（batch boot 系列）：claim 时刻 - 会话创建时刻
                # = 排队时长（并发槽满/预算封锁时子会话静默 pending，这是
                # 「批创建后页面长时间无动静」的第一嫌疑，先把它显形）。
                for r in rows:
                    _created = r.get('created_at')
                    if _created is not None:
                        try:
                            from datetime import datetime, timezone
                            _waited = (datetime.now(timezone.utc)
                                       - _created).total_seconds()
                            logger.info(
                                'batch boot sid=%s step=queue waited=%ds '
                                'seq=%s batch=%s', r['id'], int(_waited),
                                r.get('batch_seq'), r.get('batch_id'))
                        except Exception:
                            pass
                # P0 ownership（spec §4.1/§5.1）：claim 即开 turn——先关掉该
                # 会话残留的 active turn（崩溃遗留），再插入本轮 turn 并绑到
                # active_turn_id。部分唯一索引 uniq_ai_chat_turns_active 保证
                # 任一子会话同时最多一个 active turn（数据库级）。
                for r in rows:
                    cur.execute(
                        "UPDATE ai_chat_turns SET status = 'cancelled', "
                        "  error_code = 'SUPERSEDED', finished_at = NOW() "
                        "WHERE session_id = %s "
                        "  AND status IN ('accepted','running','recovering')",
                        (r['id'],),
                    )
                    turn_id = 'turn_' + uuid.uuid4().hex
                    cur.execute(
                        "INSERT INTO ai_chat_turns "
                        "  (id, session_id, batch_id, user_id, client_request_id, "
                        "   operation, status, expected_generation, started_at) "
                        "VALUES (%s, %s, %s, %s, %s, 'send', 'running', %s, NOW())",
                        (turn_id, r['id'], r.get('batch_id'), r['user_id'],
                         f"claim-{turn_id}", r.get('execution_generation') or 0),
                    )
                    cur.execute(
                        "UPDATE ai_chat_sessions SET active_turn_id = %s "
                        "WHERE id = %s",
                        (turn_id, r['id']),
                    )
                    r['turn_id'] = turn_id
                # Reflect "in progress" in the batch the moment a child starts —
                # otherwise the batch stays 'pending' (sidebar shows 待运行) until
                # the FIRST child reaches a terminal state, even while children run.
                batch_ids = list({r['batch_id'] for r in rows if r.get('batch_id')})
                if batch_ids:
                    cur.execute(
                        "UPDATE ai_chat_batches SET status = 'running' "
                        "WHERE id = ANY(%s) AND status = 'pending'",
                        (batch_ids,),
                    )
            conn.commit()
        return rows

    def _cancel_pending_requests(self):
        """Convert still-pending children with cancel_requested / pause_requested
        to their terminal (cancelled) or rest-state (paused) before the claim CTE
        below ever sees them — so a stopped-while-queued child never occupies a
        worker slot, and a paused one stays parked until resumed. Mirrors
        etl_scheduler.py's cancel_requested pattern, but checked pre-claim
        instead of mid-run (the mid-run check lives in _await_finished).

        Cancelled children count toward the batch's `failed` counter (same
        aggregate bucket _mark_failed uses); paused children change no counter
        (they're not terminal — resume puts them back to pending). The
        distinguishing 'cancelled' vs 'paused' literal lives on the child row
        itself for callers who want to tell the two apart.
        """
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_chat_sessions SET status = 'cancelled', "
                    "  error_message = '已被调用方取消' "
                    "WHERE status = 'pending' AND cancel_requested "
                    "RETURNING id, batch_id"
                )
                rows = cur.fetchall()
                counts = Counter(r[1] for r in rows if r[1])
                # H7④（12 号 §4.1）：同批多行取消时锚定本批最后一个
                # 刚转移的子会话（effect 落账不用 tiebreaker 兜底）
                anchor_by_batch = {}
                for rid_, bid_ in rows:
                    if bid_:
                        anchor_by_batch[bid_] = rid_
                for bid, n in counts.items():
                    cur.execute(
                        "UPDATE ai_chat_batches SET failed = failed + %s WHERE id = %s",
                        (n, bid),
                    )
                # NOT cancel_requested：同一名义请求不会两个标志并存（pause 与
                # cancel 是互斥的用户动作），但万一同时置位，取消优先于暂停。
                cur.execute(
                    "UPDATE ai_chat_sessions SET status = 'paused' "
                    "WHERE status = 'pending' AND pause_requested "
                    "  AND NOT cancel_requested "
                    "RETURNING id, batch_id"
                )
                paused_rows = cur.fetchall()
                paused_batch_ids = {r[1] for r in paused_rows if r[1]}
            conn.commit()
        for bid in counts:
            _recompute_batch_status(bid, anchor_sid=anchor_by_batch.get(bid))
        for bid in paused_batch_ids:
            _recompute_batch_status(bid)

    def _restart_audit(self):
        """H8 修复后不再使用：无条件 running→pending 重放与 P1 恢复决策表
        冲突（重启即重做副作用）。遗留行为由 _reconcile_stale_running 的
        恢复决策接管。方法保留为空操作以兼容潜在外部引用。"""
        return None

    @staticmethod
    def _is_retryable(exc: BaseException) -> bool:
        """失败分类：是否值得自动重试。

        可重试：停滞/工具卡死（_SessionTimeout 的两个 watchdog 原因）、
        provider 瞬时错误（RETRYABLE_TURN_FAILED 白名单）、网络异常。
        不可重试：硬超时上限（长任务被时钟杀掉，续跑只会更大）、
        ProviderAuthError（密钥错，重试必然再炸）、用户中断、上下文超限，
        以及一切白名单之外的未知失败（保守不重试）。
        """
        if isinstance(exc, _SessionTimeout):
            return exc.reason in ('stalled (no progress)', 'tool stuck',
                                  'poll unreachable')
        if isinstance(exc, _TurnFailed):
            return exc.name in BatchWorker.RETRYABLE_TURN_FAILED
        if isinstance(exc, requests.exceptions.RequestException):
            return True
        return False

    @classmethod
    def _auto_retry_prompt(cls, reason: str | None) -> str:
        """自动续跑提示词（带原因版）。无原因回落静态文案（兼容既有调用）；
        有原因时拼进括号——这是重试原因在对话内可见的唯一渠道（对齐
        _maybe_gate_retry 把失败明细拼进定向修复提示词的范式）。"""
        if not reason:
            return cls.AUTO_RETRY_CONTINUE_PROMPT
        return ('上一轮执行因异常中断（' + reason[:300].rstrip('。')
                + '，系统已自动重试）。' + cls.AUTO_RETRY_PROMPT_TAIL)

    def _maybe_auto_retry(self, session_id: str,
                          generation: int | None = None,
                          reason: str | None = None,
                          batch_id: str | None = None) -> bool:
        """可重试失败时把子任务重新排队（状态回 pending，不占批次计数）。

        已开跑过的（有 OpenCode 会话）置 continue_prompt 走 continue 模式，
        在原会话/原工作区上续跑；从未开跑的原样重排。预算（MAX_AUTO_RETRY）
        从 DB 现读——claim 行里的快照在本轮已过期。预算用尽返回 False，由
        调用方走既有 failed 路径。

        reason 三渠道留痕（2026-11-03，此前重排即丢失、事后无法取证）：
        续跑提示词（对话内可见）+ attempt error_message + child.retry 批事件；
        日志行同步带原因。error_message 在重排时仍清空——pending 不是终局，
        原因去事件/attempt 里查。"""
        if self.MAX_AUTO_RETRY <= 0:
            return False
        prompt = self._auto_retry_prompt(reason)
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT retry_count, opencode_session_id FROM ai_chat_sessions "
                    "WHERE id = %s", (session_id,))
                row = cur.fetchone()
                if row is None:
                    return False
                retry_count, oc = row
                if retry_count >= self.MAX_AUTO_RETRY:
                    return False
                cur.execute(
                    "UPDATE ai_chat_sessions "
                    "SET status='pending', retry_count = retry_count + 1, "
                    "    error_message=NULL, cancel_requested=false, pause_requested=false, "
                    "    execution_generation = execution_generation + 1, "
                    "    active_turn_id = NULL, "
                    "    continue_prompt = CASE WHEN %s IS NOT NULL THEN %s "
                    "                      ELSE continue_prompt END "
                    "WHERE id = %s AND status = 'running' "
                    "  AND NOT cancel_requested AND NOT pause_requested",
                    (oc, prompt, session_id))
                requeued = cur.rowcount > 0
        if not requeued:
            # F1（pending-flip 修复）：行已被并发收口（终态）或用户已置
            # cancel/pause——不重排、不清标志。调用方走 _mark_failed → CAS
            # 未命中 → _on_transition_miss 按控制标志重定向到 cancelled/
            # paused，终态行不得复活。
            logger.info('batch auto-retry skipped sid=%s '
                        '(row no longer requeueable)', session_id)
            return False
        self._close_attempt_for_requeue(session_id, 'AUTO_RETRY',
                                        error_message=reason)
        # 每次重试一条 child.retry（与 child.stall 的判定现场互补：那是
        # 触发瞬间快照，这是重试动作记录）——error_message 只剩最后一次，
        # 事件才是逐次重试原因的持久证据。append_event 自身 best-effort。
        if batch_id:
            from ai.batch import batch_events as _batch_events
            _batch_events.append_event(
                batch_id, 'child.retry', aggregate_type='session',
                aggregate_id=session_id, execution_generation=generation,
                payload={'session_id': session_id, 'reason': reason,
                         'retry': retry_count + 1,
                         'max': self.MAX_AUTO_RETRY})
        logger.warning('batch auto-retry sid=%s attempt=%d/%d reason=%s '
                       '(re-queued as pending%s)',
                       session_id, retry_count + 1, self.MAX_AUTO_RETRY,
                       reason or 'unknown',
                       ', continue on same opencode session' if oc else '')
        self.notify()  # 立刻唤醒调度器接续
        return True

    def _start_lease_heartbeat(self, session_id: str,
                               fencing_token) -> 'threading.Event | None':
        """F3：为本轮执行启动子会话租约心跳线程，返回 lease_lost 事件。

        旧实现把续租内联在 _await_finished 的轮询循环里——claim→send 之间
        （冷目录 create_session 可达 120s，超过租约 TTL 90s）、子代理收敛
        等待、门禁核对等阶段租约裸奔过期，另一进程的对账器会把仍在工作的
        会话重排回 pending（生产「运行中→待运行但会话还在干」成因）。

        心跳节奏 min(20, TTL/3)（TTL 收紧的测试配置自动等比加快）；续租
        失败（owner/fencing 不匹配 = 执行权易主）只置位 lease_lost，由执行
        线程在轮询/检查点自行放弃——不 abort、不写终态（F4，与 _LeaseLost
        处理器语义一致：接管者可能正在复用该 OpenCode 会话）。

        无租约身份（未 start 的 worker / 直呼测试）返回 None，行为同旧。"""
        owner = self._lease_owner
        if not owner or fencing_token is None:
            return None
        lease_lost = threading.Event()
        stop = threading.Event()
        self._heartbeats[session_id] = stop
        threading.Thread(
            target=self._lease_heartbeat_loop,
            args=(session_id, owner, fencing_token, lease_lost, stop),
            daemon=True, name=f'lease-hb-{str(session_id)[:8]}').start()
        return lease_lost

    def _lease_heartbeat_loop(self, session_id: str, lease_owner: str,
                              fencing_token: int, lease_lost: threading.Event,
                              stop: threading.Event) -> None:
        interval = max(1.0, min(20.0, execution_lease_ttl() / 3.0))
        while not stop.wait(interval):
            try:
                if not self._renew_child_lease(session_id, lease_owner,
                                               fencing_token):
                    lease_lost.set()
                    logger.warning('child lease lost sid=%s (heartbeat); '
                                   'executor will drop', session_id)
                    return
            except Exception:
                # DB 瞬断：下一拍再试；期间租约若真过期被接管，下一拍续租
                # 必然 rowcount=0 → lease_lost 置位，执行线程放手。
                logger.exception('lease heartbeat failed sid=%s (will retry)',
                                 session_id)

    @staticmethod
    def _renew_child_lease(session_id: str, lease_owner: str,
                           fencing_token: int) -> bool:
        """子会话租约续租（spec §6.2）。owner/fencing 不匹配 → False。"""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_chat_sessions "
                    "   SET heartbeat_at = NOW(), "
                    "       lease_until = NOW() + (%s || ' seconds')::interval "
                    " WHERE id = %s AND lease_owner = %s "
                    "   AND fencing_token = %s",
                    (str(execution_lease_ttl()), session_id, lease_owner,
                     fencing_token),
                )
                ok = cur.rowcount > 0
            conn.commit()
        return ok

    @staticmethod
    def _abort_oc_quietly(oc_session_id: str | None, directory: str = '',
                          *, why: str = '') -> None:
        """在「判定失败/重排」之前尽力 abort 对应的 OpenCode 回合。

        OpenCode 是独立进程：平台 worker 一旦放弃（停滞/对账重排），回合
        不会自己停——任务已 failed/pending、会话却继续出字，自动重试的
        continue 提示词还会排在这个活回合后面。取消/暂停/工具卡死三条路径
        本来就会 abort，这里把停滞与对账恢复也补齐（2026-11-03）。

        纯清场：空会话号跳过；任何异常只记日志，绝不反向影响失败/重排本身
        （abort 失败最多留下一个仍在跑的回合，不能把收口流程炸掉）。"""
        if not oc_session_id:
            return
        try:
            opencode_client.abort_session(oc_session_id, directory=directory)
            logger.info('batch abort issued oc=%s (%s)', oc_session_id,
                        why or 'turn no longer owned')
        except Exception:
            logger.warning('batch abort failed oc=%s (%s); turn may keep '
                           'running', oc_session_id, why or '', exc_info=True)

    def _reconcile_stale_running(self) -> None:
        """运行中对账器（P1 恢复决策，spec §4.2/§6.3）。

        与 P0 的差异：孤儿判定不再只看本进程线程表——先看子会话租约：
          - lease_until > NOW() → 有人在工作（可能是另一实例），不重排不误杀；
          - lease 过期 / NULL（legacy 行）→ 按恢复决策表分流：
              * OpenCode 会话 404 → 带准确原因失败（沿用 P0 语义）；
              * OpenCode 不可达 → 跳过本轮；
              * 存在 unknown 副作用 → needs_review（禁止自动重放）；
              * OC 回合仍在飞行中（F5，最新 assistant 消息未终态/带未完成
                工具）→ 跳过本轮（行保持 running，等回合空闲）；缓重排有
                兜底上限 RECOVER_DEFER_MAX_SEC，防「看似活着」的永久悬挂
                挂死恢复；
              * 回合处于模糊态（continuation finish + 工具已落地 = 步骤
                边界；或 REST 空快照但库里有消息）→ 要求签名在
                RECOVER_AMBIGUOUS_STABLE_SEC 内不变才判空闲，避免把步骤
                间隙误判为「线程丢失」（2026-11-03 生产误重排窗口）；
              * 重排/失败前 abort 该 OC 回合——对账器的「丢失」只说明平台
                侧没人续租，OpenCode 是独立进程，可能还在正常生成；
              * OpenCode 会话存在 + 有 checkpoint → continue（原会话续跑，
                注入续跑提示词，generation 不变）；
              * OpenCode 会话存在 + 无 checkpoint → 预算内原地续跑（沿用
                旧行为；无进度证据的重放边界由容灾文档 §8.1 声明）；
              * 无 oc 会话 → 原样重排（尚未开跑）。

        每次接管写 ai_batch_events('child.recovered')。与 dispatcher 同线程，
        天然避开 claim→入账窗口的竞态；任何异常只记日志。"""
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, batch_id, opencode_session_id, workspace_path, "
                        "lease_until, fencing_token FROM ai_chat_sessions "
                        "WHERE status = 'running' "
                        "  AND (batch_id IS NOT NULL OR api_key_id IS NOT NULL)")
                    rows = cur.fetchall()
        except Exception:
            logger.exception('reconcile query failed')
            return
        with self._lock:
            tracked = set(self._running_session_ids)
        stale_seen = {r[0] for r in rows}
        for sid, batch_id, oc, ws, lease_until, fencing_token in rows:
            if sid in tracked:
                continue  # 本进程正在跑，轮询循环自己负责
            if lease_until is not None:
                from datetime import datetime, timezone
                try:
                    remaining = lease_until - datetime.now(timezone.utc)
                    if lease_until.tzinfo is None:
                        remaining = lease_until - datetime.now()
                    if remaining.total_seconds() > 0:
                        continue  # 租约未过期：原 owner 仍可能在工作
                except TypeError:
                    pass
            try:
                from ai.batch import execution_effect as execution_effect
                if execution_effect.has_unknown_effects(sid):
                    self._mark_needs_review(
                        sid, batch_id,
                        '存在结局未知的副作用（unknown effect），'
                        '已停止自动执行，请人工复核后重试或重执行',
                        fencing_token=fencing_token)
                    self._emit_recovered(batch_id, sid, 'needs_review')
                    continue
                if not oc:
                    # 丢失发生在 create_session 之前：原样重排（全新执行）
                    self._requeue_lost(sid, continue_on_same=False,
                                       batch_id=batch_id)
                    self._emit_recovered(batch_id, sid, 'requeue_fresh')
                    continue
                try:
                    raw_msgs = opencode_client.get_messages(oc, directory=ws or '')
                except requests.exceptions.HTTPError as e:
                    status = e.response.status_code if e.response is not None else None
                    if status == 404:
                        self._mark_failed(
                            sid, batch_id,
                            error='对账器发现 OpenCode 会话已失效'
                                  '（服务端可能重启或清理过该会话），请重试或继续执行',
                            fencing_token=fencing_token)
                        self._emit_recovered(batch_id, sid, 'opencode_404')
                    continue  # 其余 HTTP 状态：本轮跳过
                except requests.exceptions.RequestException:
                    continue  # OpenCode 整体不可达：不批量误杀
                # P1 §4.2 恢复决策消费 checkpoint：oc 存活但「无 checkpoint
                # 且无副作用且无消息」→ 无进度证据，failed(retryable) 优于
                # 静默重放（spec 末行）；其余维持原地续跑（docstring 取舍）
                try:
                    from ai.batch import execution_checkpoint as _ckpt
                    _has_ckpt = _ckpt.latest_checkpoint(sid) is not None
                except Exception:
                    _has_ckpt = True  # 判定基建异常 → 保守续跑
                # 消息计数无条件读：F5 用它区分「REST 空快照」与「从未开跑」
                # （空列表 + 库里已有消息 = 轮询瞬时失明，不能当空闲）。
                with get_db() as _c2:
                    with _c2.cursor() as _cu:
                        _cu.execute(
                            "SELECT count(*) FROM ai_chat_messages "
                            "WHERE session_id = %s", (sid,))
                        _n_msgs = _cu.fetchone()[0]
                if not _has_ckpt:
                    with get_db() as _c2:
                        with _c2.cursor() as _cu:
                            _cu.execute(
                                "SELECT 1 FROM ai_execution_effects "
                                "WHERE session_id = %s LIMIT 1", (sid,))
                            _n_effects = 0 if _cu.fetchone() is None else 1
                    if _n_msgs == 0 and _n_effects == 0:
                        self._mark_failed(
                            sid, batch_id,
                            error='对账器发现执行线程丢失，且无任何进度证据'
                                  '（checkpoint/消息/副作用均为空），已按可重试失败收口',
                            fencing_token=fencing_token)
                        self._emit_recovered(batch_id, sid, 'failed_no_progress')
                        continue
                # F5（pending-flip 修复）：OC 回合仍在飞行中 → 本轮跳过。
                # 重启/失联窗口里 OpenCode 是独立进程、回合还在正常生成——
                # 立即重排会让行翻 pending（状态说谎）且新 claim 向同一会话
                # 补发 continue 提示词（同回合双发）。行保持 running，等回合
                # 空闲（下轮对账）再重排续跑；兜底上限防「看似活着」的永久
                # 悬挂挂死恢复（worker 死后没有 STALL 看门狗，上限顶替它）。
                decision = self._recover_turn_state(
                    sid, raw_msgs, has_persisted_history=_n_msgs > 0)
                if decision:
                    _since = self._recover_defer_since.get(sid)
                    if _since is None:
                        _since = self._recover_defer_since[sid] = time.time()
                    if time.time() - _since <= self.RECOVER_DEFER_MAX_SEC:
                        self._emit_recovered(batch_id, sid, decision)
                        continue
                    logger.warning('recover defer cap exceeded sid=%s '
                                   '(turn looks alive for %.0fs); requeueing',
                                   sid, time.time() - _since)
                self._recover_defer_since.pop(sid, None)
                self._recover_ambiguous.pop(sid, None)
                self._requeue_lost(sid, continue_on_same=True,
                                   batch_id=batch_id,
                                   oc_session_id=oc, directory=ws or '')
                self._emit_recovered(batch_id, sid, 'requeue_continue')
            except Exception:
                logger.exception('reconcile row failed sid=%s', sid)
        # 缓重排计时器只对「仍在 stale 集里的飞行中会话」有意义；行已离开
        # （重排/终态/本进程接管）就清，防 dict 无界增长。
        self._recover_defer_since = {
            k: v for k, v in self._recover_defer_since.items()
            if k in stale_seen}
        self._recover_ambiguous = {
            k: v for k, v in self._recover_ambiguous.items()
            if k in stale_seen}

    def _emit_recovered(self, batch_id: str | None, sid: str, decision: str):
        if batch_id:
            from ai.batch import batch_events as batch_events
            batch_events.append_event(
                batch_id, 'child.recovered', aggregate_type='child',
                aggregate_id=sid, payload={'decision': decision})

    def _recover_turn_state(self, sid: str, raw_msgs: list,
                            has_persisted_history: bool = False) -> str | None:
        """F5 二次确认：返回本轮 child.recovered 决策名，None = 可以重排。

        `flying`（未终态/工具在跑）直接缓重排；`ambiguous`（continuation
        finish + 工具已落地 = 步骤边界，或 REST 空快照但库里有历史）单次
        采样分不清「下一步马上来」与「回合真的结束」，要求签名在
        RECOVER_AMBIGUOUS_STABLE_SEC 内纹丝不动才放开重排——这正是
        「对账器说工作线程丢了、会话却还在正常执行」的误判窗口
        （2026-11-03：重启后对账恰好落在步骤间隙/空快照上）。"""
        state = self._turn_flight_state(
            raw_msgs, has_persisted_history=has_persisted_history)
        if state == 'idle':
            self._recover_ambiguous.pop(sid, None)
            return None
        if state == 'flying':
            self._recover_ambiguous.pop(sid, None)
            return 'deferred_in_flight'
        sig = self._raw_progress_signature(raw_msgs)
        prev = self._recover_ambiguous.get(sid)
        if prev is None or prev.get('sig') != sig:
            self._recover_ambiguous[sid] = {'sig': sig, 'since': time.time()}
            return 'deferred_ambiguous'
        if time.time() - prev['since'] < self.RECOVER_AMBIGUOUS_STABLE_SEC:
            return 'deferred_ambiguous'
        logger.info('recover ambiguity stable for %.0fs sid=%s; treat as idle '
                    '(sig unchanged, no step started)',
                    time.time() - prev['since'], sid)
        self._recover_ambiguous.pop(sid, None)
        return None

    @staticmethod
    def _turn_flight_state(raw_msgs: list, *,
                           has_persisted_history: bool = False) -> str:
        """OC 会话最新 assistant 消息的存活三态：flying / ambiguous / idle。

        镜像 list_messages 的终态判定（finish + time.completed + running
        tool），作用在 get_messages 的原始 {'info','parts'} 形状上。与
        list_messages 的 `resolved_tool_seen` 刻意不同：那边把「continuation
        finish + 工具已落地」当终态（question 被自动拒绝后 OpenCode 直接完结
        消息，finish 却留在 tool-calls）；对账器若照抄，就会把每个步骤间隙
        也当终态而误重排在飞的回合。这里把它标成 ambiguous，交给调用方做
        签名稳定性二次确认。无 assistant 消息时：库里有历史（REST 空快照）
        → ambiguous，从未开跑 → idle。"""
        msgs = raw_msgs or []
        if not msgs:
            return 'ambiguous' if has_persisted_history else 'idle'
        for m in reversed(msgs):
            info = m.get('info') or {}
            if info.get('role') != 'assistant':
                continue
            finish = info.get('finish')
            completed = (info.get('time') or {}).get('completed')
            running_tool = False
            for p in (m.get('parts') or m.get('content') or []):
                if p.get('type') == 'tool' and \
                        (p.get('state') or {}).get('status') in (
                            None, '', 'pending', 'running'):
                    running_tool = True
                    break
            finished = bool(completed) and finish not in (None, '')
            if running_tool or not finished:
                return 'flying'
            if finish in ('tool-calls', 'tool_use'):
                return 'ambiguous'
            return 'idle'
        return 'idle'

    def _requeue_lost(self, session_id: str, *, continue_on_same: bool,
                      batch_id: str | None,
                      oc_session_id: str | None = None, directory: str = ''):
        """对账器把丢失的 running 行重新排队（retry_count 预算内，超限则失败）。

        F1（pending-flip 修复）：不清 cancel_requested/pause_requested——
        worker 失联期间用户按下的中断/暂停必须保留，重排回 pending 后由
        _cancel_pending_requests 正常消费（cancelled / paused），而不是
        抹掉标志让任务原样重跑。

        重排/失败前先 abort OpenCode 回合（2026-11-03）：对账器判「工作线程
        丢失」依据的是租约，OpenCode 回合是独立进程、可能还在正常生成。
        不 abort 会同时留下两个问题——行已 pending/failed 但会话继续输出，
        以及重排后的 continue 提示词排进这个活回合（同回合双发）。"""
        self._abort_oc_quietly(oc_session_id, directory=directory,
                               why='reconcile requeue/fail')
        lost_reason = ('执行工作进程与该子任务失去联系（可能因服务重启或崩溃），'
                       '对账器已自动重排续跑')
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT retry_count, fencing_token "
                            "FROM ai_chat_sessions WHERE id=%s",
                            (session_id,))
                row = cur.fetchone()
                if row is None:
                    return
                if row[0] >= self.MAX_AUTO_RETRY:
                    self._mark_failed(
                        session_id, batch_id,
                        error='对账器发现子任务的工作线程丢失，且自动重试预算已用尽',
                        fencing_token=row[1])
                    return
                cur.execute(
                    "UPDATE ai_chat_sessions "
                    "SET status='pending', retry_count = retry_count + 1, "
                    "    error_message=NULL, "
                    "    execution_generation = execution_generation + 1, "
                    "    active_turn_id = NULL, "
                    "    continue_prompt = CASE WHEN %s THEN %s ELSE continue_prompt END "
                    "WHERE id = %s AND status = 'running'",
                    (continue_on_same, self._auto_retry_prompt(lost_reason),
                     session_id))
                requeued = cur.rowcount > 0
                # P1 §5.3 checkpoint 写入时机：recovery（重排换代前的最后快照）
                if requeued:
                    try:
                        from ai.batch import execution_checkpoint as execution_checkpoint
                        execution_checkpoint.write_checkpoint(
                            session_id, checkpoint_type='recovery',
                            execution_generation=None,
                            context_snapshot={
                                'continueOnSame': continue_on_same,
                                'retryCount': row[0] + 1})
                    except Exception:
                        pass
            conn.commit()
        if requeued:
            self._close_attempt_for_requeue(session_id, 'RECONCILE_REQUEUE',
                                            error_message=lost_reason)
            logger.warning('reconcile re-queued lost running session sid=%s '
                           '(continue_on_same=%s)', session_id, continue_on_same)
            if batch_id is not None:
                _recompute_batch_status(batch_id, anchor_sid=session_id)
            self.notify()


# ---------------------------------------------------------------------------
# BatchWorker
# ---------------------------------------------------------------------------

class BatchWorker(WorkerLifecycleMixin, WorkerRunnerMixin, WorkerGatesMixin,
                  WorkerSubtasksMixin, WorkerTransitionsMixin):
    """组合类（预检裁定）：WorkerLifecycleMixin 为本体（__init__/start/stop/
    租约监管/监听/派发），单会话执行与完成保证、动作门禁、子任务持久化、
    终态转移由其余四个 mixin 组成——方法集与拆分前 BatchWorker 一致。"""


# 包内交叉引用（_thinking_blindspot 调用期解析 BatchWorker）：runner.py 由本
# 模块顶部导入（WorkerRunnerMixin），其导入期不能反向导入本模块（会撞半
# 初始化）；调用期本模块必已加载完成，故这里把组合类回填进 runner 命名
# 空间——镜像 facade.py 尾部导入的既有形态。
from . import runner as _runner_mod  # noqa: E402
_runner_mod.BatchWorker = BatchWorker


__all__ = [
    'BatchWorker',
    'logger',
    'Counter',
    'ThreadPoolExecutor',
    'RealDictCursor',
    'get_db',
]
