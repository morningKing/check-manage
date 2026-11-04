import logging
import traceback
import uuid

from db import get_db
from utils import agent_ledger
from ai.sessions.ai_message_meta import meta_from_info, public_meta

from .discovery import (discover_subtasks, subtask_status_from_messages,
                        _raw_has_open_delegation)
from .facade import opencode_client

logger = logging.getLogger(__name__)

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class WorkerSubtasksMixin:
    @staticmethod
    def _content_from_parts(parts, subtask_status: dict | None = None,
                            subtask_id_map: dict | None = None,
                            subtask_segments: dict | None = None) -> list:
        """Map one OpenCode message's parts to persisted typed content: text +
        tool_use + subtask_use (matches interactive build_content + the
        AiContentPart schema). Drops reasoning/step markers. 委托给
        utils.opencode_parts.map_part（chat_persist.py 共用同一份映射）；空文本
        的过滤保留在这里——这是原有实现的分工，map_part 本身不过滤。`subtask`
        part 自 Task 5 起放行给 map_part：批任务路径每次都重新拉取完整消息列表
        （REST 快照式，见 `_persist_conversation`/`_collect_subtasks`），调用方
        总能传入当前整棵子代理树算好的 `subtask_status`，所以占位气泡不会像
        Task 4/5 接入前那样卡死在默认的 'running'。`subtask_id_map` 把 subtask
        part 的 id 映射到正确的子会话 sessionID（修复 SubtaskPart.sessionID
        实际是父会话的 bug）。"""
        from ai.opencode.opencode_parts import map_part
        # /command 路径同一消息里 subtask part 与 tool:'task' part 并存且指向
        # 同一子会话：subtask part 的占位元数据（agent/description）更准确，
        # 先收集，tool part 派生的占位去重时让位。
        subtask_by_child = {}
        for p in (parts or []):
            if p.get('type') == 'subtask':
                mapped = map_part(p, subtask_status=subtask_status,
                                  subtask_id_map=subtask_id_map,
                                  subtask_segments=subtask_segments)
                if mapped:
                    subtask_by_child[mapped['subtaskId']] = mapped
        out = []
        seen_subtask_ids = set()
        for p in (parts or []):
            if p.get('type') not in ('text', 'tool', 'subtask', 'reasoning'):
                continue
            mapped = map_part(p, subtask_status=subtask_status,
                              subtask_id_map=subtask_id_map,
                              subtask_segments=subtask_segments)
            if mapped is None:
                continue
            if mapped['type'] in ('text', 'reasoning') and not mapped['text'].strip():
                continue
            if mapped['type'] == 'subtask_use':
                mapped = subtask_by_child.get(mapped['subtaskId'], mapped)
                if mapped['subtaskId'] in seen_subtask_ids:
                    continue
                seen_subtask_ids.add(mapped['subtaskId'])
            # OpenCode 有时把一段连续推理拆成很多个短小的 reasoning part
            # （各自独立 id，每个只有一两个 token），不是复用同一个 id 增量
            # 续写——原样一个 part 一个 content 条目会渲成一长串「思考完成」
            # 气泡（同一处理见 chat_persist.py::_flatten_scope）。相邻的直接
            # 拼进上一条，不留分隔符；中间隔了别的 part 类型才算另一段。
            if mapped['type'] == 'reasoning' and out and out[-1]['type'] == 'reasoning':
                out[-1]['text'] += mapped['text']
                continue
            out.append(mapped)
        return out

    @staticmethod
    def _preview_from(message: dict) -> str | None:
        for part in (message.get('content') or []):
            if part.get('type') == 'text' and part.get('text'):
                t = part['text'].strip().splitlines()
                return (t[0] if t else '')[:200]
        return None

    def _collect_subtasks(self, messages: list, known: dict, child_messages: dict,
                          parent_depth: int, parent_sid: str | None, directory: str):
        """阶段一（只发现、不持久化）：递归扫描 messages 里的子代理，拉取
        每一个的自己的消息、算出它自己的状态，全部记进 known/child_messages。
        `known` 在递归调用之间原地累积（同一个字典对象一路传下去），既防止
        重复处理同一个子代理，也是深度判断的依据。持久化留到阶段二统一做——
        必须先拿到整棵树的完整状态快照，各层的占位气泡才能在同一次持久化里
        全部用上最新状态，不会出现"父级先写了、子代理的状态后来才算出来"
        这种顺序问题。"""
        newly = discover_subtasks(messages, known, parent_depth, parent_sid)
        for sid, info in newly.items():
            known[sid] = info
            try:
                msgs = opencode_client.get_messages(sid, directory=directory) or []
            except Exception:
                msgs = []
            status, error = subtask_status_from_messages(msgs)
            info['status'] = status
            info['error'] = error
            child_messages[sid] = msgs
            self._collect_subtasks(msgs, known, child_messages, info['depth'], sid, directory)

    def _write_subtask(self, root_session_id: str, subtask_id: str, info: dict,
                       subtask_status: dict, child_messages: list,
                       subtask_id_map: dict | None = None,
                       turn_no: int = 0, turn_label: str = '',
                       reuse_agents: list | None = None):
        """阶段二：把一个子代理的摘要行 + 它当前拉到的全部消息 upsert 进库。
        `subtask_status` 是整棵树的完整状态快照（阶段一算好的），传给
        `_content_from_parts` 让这个子代理自己内容里（如果有）更深一层的
        占位气泡也能用上最新状态——跟顶层的处理方式统一。`subtask_id_map` 把
        subtask part 的 id 映射到正确的子会话 sessionID。

        会话复用（subagent_reuse）相关：
        - turn_segments：子会话里每条 user 消息 = 一次委派任务（边界）。按
          user 消息序号（跨拉取稳定）合并任务段 {ord, turn, label, firstMsgId}；
        - ai_subagent_pins：复用名单内的 agent，把最新子会话 id 登记为锚点
          （与服务端持久化双保险；OC 插件 after 回调是主登记路径）。"""
        import json as _json
        with get_db() as conn:
            cur = conn.cursor()
            # 既有任务段提前读出：assistant 消息里的嵌套 subtask_use 需要
            # segmentCount（气泡头徽标），段合并也复用这一份
            cur.execute("SELECT turn_segments FROM ai_chat_subtasks WHERE id = %s",
                        (subtask_id,))
            row = cur.fetchone()
            segments = (row and row[0]) or []
            subtask_segments = {subtask_id: segments}
            cur.execute(
                "INSERT INTO ai_chat_subtasks "
                "  (id, root_session_id, parent_subtask_id, agent, description, status, "
                "   error_message, completed_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, "
                "        CASE WHEN %s IN ('completed','failed') THEN now() ELSE NULL END) "
                "ON CONFLICT (id) DO UPDATE SET "
                "  status = EXCLUDED.status, error_message = EXCLUDED.error_message, "
                "  completed_at = COALESCE(ai_chat_subtasks.completed_at, EXCLUDED.completed_at)",
                (subtask_id, root_session_id, info.get('parent_id'), info.get('agent'),
                 info.get('description'), info['status'], info.get('error'), info['status']),
            )
            user_ords = []   # (ordinal, mid, label) —— 任务段边界
            ordinal = 0
            for m in child_messages:
                minfo = m.get('info') or {}
                role = minfo.get('role')
                if role == 'user':
                    texts = [p.get('text', '') for p in (m.get('parts') or [])
                             if p.get('type') == 'text' and (p.get('text') or '').strip()]
                    if not texts:
                        continue
                    content = [{'type': 'text', 'text': t} for t in texts]
                    mid = minfo.get('id') or f'{subtask_id}:u:{ordinal}'
                    cur.execute(
                        "INSERT INTO ai_chat_subtask_messages (id, subtask_id, role, content) "
                        "VALUES (%s, %s, 'user', %s) "
                        "ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content",
                        (mid, subtask_id, _json.dumps(content)),
                    )
                    user_ords.append((ordinal, mid, (texts[0] or '')[:80]))
                    ordinal += 1
                    continue
                if role != 'assistant':
                    continue
                content = self._content_from_parts(m.get('parts'), subtask_status,
                                                   subtask_id_map, subtask_segments)
                if not content:
                    continue
                mid = minfo.get('id') or f'{subtask_id}:a:{id(m)}'
                # 轮次 meta 与根会话同源（2026-10-12）：子代理推理耗时是性能
                # 分析「子代理内部推理段」的数据前提——此前子代理消息从不写
                # meta，推理段永远只能靠工具间隙推导。with_anchor：批路径的
                # 行是回合结束后才写的，startedAt（消息自身起始）才是嵌套
                # 窗口的合法起点。
                meta = public_meta(meta_from_info(minfo), with_anchor=True)
                cur.execute(
                    "INSERT INTO ai_chat_subtask_messages (id, subtask_id, role, content, meta) "
                    "VALUES (%s, %s, 'assistant', %s, %s) "
                    "ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content, "
                    "  meta = COALESCE(EXCLUDED.meta, ai_chat_subtask_messages.meta)",
                    (mid, subtask_id, _json.dumps(content),
                     _json.dumps(meta) if meta else None),
                )
            # 任务段合并：子会话内新增的 user 消息（按序号判新）→ 追加任务段
            if user_ords:
                covered = {seg.get('ord') for seg in segments}
                for ord_i, mid, label in user_ords:
                    if ord_i in covered:
                        continue
                    segments.append({
                        'ord': ord_i,
                        # turn = 该子代理会话的第几轮委派（段数序号）。不用
                        # execution_generation——它跨重试/重新执行单调累加，
                        # 重新执行清行后首段会被记成"第 N 轮派发"（生产实测
                        # 第 7 轮），与"重新执行=全新一轮从第 1 轮计"相悖。
                        'turn': len(segments),
                        'label': label or (turn_label or '')[:80],
                        'firstMsgId': mid,
                        'startedAt': _now_iso(),
                    })
                cur.execute("UPDATE ai_chat_subtasks SET turn_segments = %s::jsonb "
                            "WHERE id = %s",
                            (_json.dumps(segments, ensure_ascii=False), subtask_id))
            # 复用锚点登记（双保险；OC 插件 after 回调是主路径）
            agent_name = info.get('agent')
            if agent_name and reuse_agents and agent_name in reuse_agents:
                cur.execute(
                    "INSERT INTO ai_subagent_pins "
                    "  (id, root_session_id, batch_id, agent, task_id) "
                    "VALUES (%s, %s, (SELECT batch_id FROM ai_chat_sessions "
                    "          WHERE id = %s), %s, %s) "
                    "ON CONFLICT (root_session_id, agent) DO UPDATE SET "
                    "  task_id = EXCLUDED.task_id, updated_at = NOW()",
                    # 主键用随机值：真正的幂等键是 (root_session_id, agent)
                    # 唯一索引——OC 子会话 id 前 8 字符跨批次常相同，不能入主键
                    ('spin_' + uuid.uuid4().hex,
                     root_session_id, root_session_id, agent_name, subtask_id),
                )
            conn.commit()

    def _persist_conversation(self, session_id: str, prompt: str,
                              oc_session_id: str, assistant_msg: dict | None,
                              directory: str = '',
                              generation: int | None = None,
                              force: bool = False,
                              turn_label: str = '',
                              reuse_agents: list | None = None):
        """Persist the FULL conversation: the user prompt + every assistant
        message (mapped to text + tool_use parts) read from OpenCode's REST
        message list, so the batch child's thread shows tool bubbles like an
        interactive session. Falls back to `assistant_msg` if REST yields none.

        Idempotent — the user row uses a deterministic id and each assistant row
        is keyed on its OpenCode message id (ON CONFLICT DO UPDATE), so calling
        this repeatedly while a turn runs upserts the growing conversation
        instead of duplicating it (that's how the live view is driven).
        Best-effort; never raises.

        P0 stale-write 防线（spec §6.3）：传了 generation 时校验子会话仍在
        running 且代数未变——retry/reexecute 换代后，旧 turn 的迟到进度快照
        不再写入，避免旧回合消息污染新回合的对话历史。"""
        if generation is not None:
            from utils import batch_repo
            st = batch_repo.read_child_control_state(session_id)
            if st and (st['status'] != 'running'
                       or st['execution_generation'] != generation):
                logger.info('stale persist skipped sid=%s gen=%s current=%s/%s',
                            session_id, generation, st['status'],
                            st['execution_generation'])
                return
        try:
            import json as _json
            raw = []
            try:
                raw = opencode_client.get_messages(oc_session_id, directory=directory) or []
            except Exception:
                raw = []

            # 快照短路（2026-09-30 批满负载优化）：消息列表与上次持久化完全
            # 一致时跳过整棵子代理树的 REST 重拉与全量 upsert——稳定期（长
            # 工具执行）的周期持久化从每次全量工作退化为一次哈希比较。收尾
            # 持久化传 force=True 不短路。
            # 委派豁免（2026-10-12）：sig 只哈希根会话消息，而委派执行期根
            # 静止（task part 停在 running）——按 sig 短路会把子代理树的
            # 重拉一并跳过，子代理消息直到委派完成才一次性落库（子代理气泡
            # 无实时内容，生产报告 + 真批任务实测）。上次快照含未终态 task
            # part 时照常全量拉取；发现/内容延迟随之收敛到 ≤1 个进度周期。
            sig = _json.dumps(raw, ensure_ascii=False, default=str)
            if not force and self._persist_sig.get(session_id) == sig \
                    and not self._persist_open_delegation.get(session_id):
                return
            self._persist_sig[session_id] = sig
            self._persist_open_delegation[session_id] = _raw_has_open_delegation(raw)

            # 阶段一：递归发现整棵子代理树 + 各自的当前状态。
            known: dict = {}
            child_messages: dict = {}
            self._collect_subtasks(raw, known, child_messages, parent_depth=0,
                                   parent_sid=None, directory=directory)
            subtask_status = {sid: info['status'] for sid, info in known.items()}
            # subtask part 的 id -> 正确的子会话 sessionID（修复 SubtaskPart.sessionID
            # 实际是父会话的 bug）：发现阶段已经把 _part_id 记进了 info。
            subtask_id_map = {info['_part_id']: sid for sid, info in known.items()
                              if info.get('_part_id')}

            # F1（P0 spec §8.2）：先 upsert 每个子代理自己的 ai_chat_subtasks
            # 行，再写子代理的工具账本——agent_tool_calls.subtask_id 外键指向
            # ai_chat_subtasks(id)，旧顺序（先账本后建行）让首次出现的子代理
            # INSERT 违反 FK、账本缺口 → 门禁被误判 inconclusive。先落行/
            # 消息再落顶层，占位气泡不会有哪一层状态落后。
            for sid, info in known.items():
                self._write_subtask(session_id, sid, info, subtask_status,
                                    child_messages.get(sid, []), subtask_id_map,
                                    turn_no=generation or 0,
                                    turn_label=turn_label,
                                    reuse_agents=reuse_agents)
            # 委派级门禁（2026-09-30）：定向 verifier 组在名单内子代理全部
            # 终态时提前判定；未开 gate_retry 且不过 → 置 cancel_requested
            # 立即停止（用户语义：校验不过别让它继续跑完整轮）。
            if reuse_agents or True:   # 定向组不依赖 subagent_reuse 名单
                self._maybe_check_delegation_gate(session_id, directory, known,
                                                  raw=raw)
            # 顶层内容里的嵌套 subtask_use 带 segmentCount（气泡徽标）——
            # 必须在 _write_subtask 合并任务段之后读，才是本次持久化的最新值
            subtask_segments: dict = {}
            if known:
                try:
                    with get_db() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                "SELECT id, turn_segments FROM ai_chat_subtasks "
                                "WHERE id = ANY(%s)", (list(known.keys()),))
                            for rid, segs in cur.fetchall():
                                if segs:
                                    subtask_segments[rid] = segs
                except Exception:  # noqa: BLE001
                    pass

            # 动作账本（设计 §5.1）：根会话与每个子代理的 tool part 落账。
            # best-effort：失败置健康标记，终态门禁按 inconclusive 处理，
            # 不算未到位也不静默放行。
            ledger_ok = agent_ledger.record_messages(
                oc_session_id, raw, root_session_id=session_id,
                get_db=get_db)
            for child_sid, child_msgs in child_messages.items():
                child_agent = (known.get(child_sid) or {}).get('agent')
                if not agent_ledger.record_messages(
                        child_sid, child_msgs, root_session_id=session_id,
                        subtask_id=child_sid, agent_name=child_agent,
                        get_db=get_db):
                    ledger_ok = False
            self._ledger_health[session_id] = ledger_ok

            assistant_rows = []   # (message_id, content, meta)
            for m in raw:
                info = m.get('info') or {}
                if info.get('role') != 'assistant':
                    continue
                content = self._content_from_parts(m.get('parts'), subtask_status,
                                                   subtask_id_map, subtask_segments)
                if content:
                    # with_anchor：批路径的行是回合结束后才写的——轮次窗口
                    # 的起点必须是消息自身起始（perf 优先读 startedAt）。
                    meta = public_meta(meta_from_info(info), with_anchor=True)
                    assistant_rows.append((info.get('id') or f'{session_id}:a:{len(assistant_rows)}',
                                           content, meta))
            if not assistant_rows:   # REST gave nothing usable — fall back to final msg
                parts = (assistant_msg or {}).get('content') or []
                assistant_rows.append((f'{session_id}:a:final',
                                       parts if parts else [{'type': 'text', 'text': ''}], None))
            with get_db() as conn:
                with conn.cursor() as cur:
                    # M2 回修：与 _persist_user_prompt 同一 id（g 后缀）——
                    # 固定 id 会与 g0 行内容重复，首轮出现两条相同 user 气泡
                    suffix = f':g{generation}' if generation is not None else ''
                    cur.execute(
                        "INSERT INTO ai_chat_messages (id, session_id, role, content) "
                        "VALUES (%s, %s, 'user', %s::jsonb) ON CONFLICT (id) DO NOTHING",
                        (f'{session_id}:user{suffix}', session_id,
                         _json.dumps([{'type': 'text', 'text': prompt}])),
                    )
                    for mid, content, meta in assistant_rows:
                        # 写放大防线（性能快改批 ①）：进度持久化每 2.5s 全量
                        # upsert 全部 assistant 行，内容未变的行不得写新版本
                        # ——否则 search_text 触发器与 GIN trgm 每轮全量重算，
                        # WAL/dead tuple 随批子会话数线性放大。WHERE 跳过即无
                        # 行版本（xmin 不变），真变化仍照常更新。
                        cur.execute(
                            "INSERT INTO ai_chat_messages (id, session_id, role, content, meta) "
                            "VALUES (%s, %s, 'assistant', %s::jsonb, %s::jsonb) "
                            "ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content, "
                            "  meta = COALESCE(EXCLUDED.meta, ai_chat_messages.meta) "
                            "WHERE ai_chat_messages.content IS DISTINCT FROM EXCLUDED.content "
                            "   OR (EXCLUDED.meta IS NOT NULL "
                            "       AND ai_chat_messages.meta IS DISTINCT FROM EXCLUDED.meta)",
                            (mid, session_id, _json.dumps(content),
                             _json.dumps(meta) if meta else None),
                        )
                    # P2-C5：实时 usage 累计（不等回合收敛）。刻意走累加而非
                    # get_session_usage 的 max 口径——进度持久化在回合内多次
                    # 执行、每条 assistant 消息的 tokensInput 又含重发的上下文，
                    # 运行中数值只求"实时可见"偏大无妨；子任务终态 finally 的
                    # accumulate_usage 以精确口径整行覆盖（REPLACE 语义），
                    # 收敛后数值自校正。
                    for mid, content, meta in assistant_rows:
                        if meta and isinstance(meta, dict) and (
                                meta.get('tokensInput') or meta.get('tokensOutput')
                                or meta.get('cost')):
                            cur.execute("""
                                INSERT INTO ai_execution_usage
                                    (session_id, tokens_input, tokens_output, cost, updated_at)
                                VALUES (%s, %s, %s, %s, NOW())
                                ON CONFLICT (session_id) DO UPDATE SET
                                    tokens_input = ai_execution_usage.tokens_input + EXCLUDED.tokens_input,
                                    tokens_output = ai_execution_usage.tokens_output + EXCLUDED.tokens_output,
                                    cost = ai_execution_usage.cost + EXCLUDED.cost,
                                    updated_at = NOW()
                            """, (session_id,
                                  int(meta.get('tokensInput') or 0),
                                  int(meta.get('tokensOutput') or 0),
                                  float(meta.get('cost') or 0)))
                conn.commit()
        except Exception:
            traceback.print_exc()


__all__ = [
    '_now_iso',
    'logger',
    'get_db',
    'meta_from_info',
    'public_meta',
]
