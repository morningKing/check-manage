"""V2→V1 事件桥（OC2 适配阶段一）。

把 OpenCode 2.x /api/event 的细粒度事件流翻译成 V1 形态帧
``{'event': <type>, 'data': {'type': <type>, 'properties': {...}}}``——
chat_persist.apply_event / ai_chat SSE / 前端 tool-renderers 全部按 V1 契约
消费，桥让它们对 V2 会话零改动工作。

V2 契约出处（dev@ed0171f，二进制实测前的源码级基线，TEST-ME 注释标注
需 v2 二进制实测复核的点）：
- SSE data-only 帧 ``data: {json}\\n\\n``，首帧 server.connected，15s
  heartbeat 注释帧，慢消费者溢出断流（volatile）——server/event-feed.ts:29
- 事件信封 {id, created(ms), type, location?:{directory}, data}——
  schema/event.ts:67-78
- 事件载荷字段——schema/session-event.ts（text.delta 的增量字段叫 ``delta``，
  tool 调用 id 字段叫 ``id``，无 callID/tool 名字段）

设计约束：
- 有损翻译显式登记（BRIDGE_LOSSY），缺的行为宁可不下发也不伪造；
- assistant 消息元信息（tokens/cost/finish/time.completed）由
  session.step.ended 合成 message.updated 下发（meta_from_info 的输入）；
- 断线对账：V2 事件流 volatile、断线必丢——权威态是 REST 投影消息
  （client_v2.get_messages 已翻译成 V1 形状），桥不缓存历史。
"""
import json
import logging
import time

logger = logging.getLogger(__name__)

# V2 工具名 → V1 工具名（实测 v2.2.6：bash 工具叫 shell、task 工具叫
# subagent）。消费方（前端 tool-renderers / chat_persist 子代理发现）按 V1
# 名分发——桥归一化回 V1，上层零改动。
_V2_TOOL_TO_V1 = {'subagent': 'task', 'shell': 'bash'}


def normalize_v2_tool(part: dict) -> dict:
    """把 V2 tool part 归一化成 V1 形状（事件桥与 REST 投影翻译共用）：
    - name 映射回 V1（subagent→task、shell→bash）；
    - task 工具 input.agent → input.subagent_type（V1 发现逻辑读该字段）。"""
    if part.get('type') != 'tool':
        return part
    v2_name = part.get('tool') or ''
    part['tool'] = _V2_TOOL_TO_V1.get(v2_name, v2_name)
    st = part.get('state') or {}
    inp = st.get('input')
    if isinstance(inp, dict) and part['tool'] == 'task' \
            and 'agent' in inp and 'subagent_type' not in inp:
        inp['subagent_type'] = inp['agent']
    return part


class V2EventBridge:
    """有状态聚合器：一条 V2 事件 → 零或多条 V1 形态帧。

    每个事件订阅方（subscribe_events 调用）持有独立实例——跨订阅无共享
    状态，断线重连即全新视图（对账靠 REST，不靠桥）。

    ``permission_id_resolver``：V2 permission.asked 事件 data 里没有请求 id
    （per_ id 只存在于 GET /api/permission/request）——传入可调用对象时桥
    反查一次列表补齐 id，bypass 自动放行链路才能工作；不传则下发无 id
    事件，消费方按无 id 降级（fail-open 回手动权限卡）。
    """

    def __init__(self, permission_id_resolver=None):
        self._seen_msgs = {}       # assistantMessageID -> info 增量（model 等）
        self._text_acc = {}        # (mid, ordinal) -> accumulated text
        self._tool_acc = {}        # (mid, call_id) -> {'input_raw': str, ...}
        self._permission_id_resolver = permission_id_resolver

    # ---- part id 生成（跨事件稳定——消费方按 pid 幂等覆盖）----
    @staticmethod
    def _text_part_id(mid, ordinal):
        return f"{mid}#t{ordinal}"

    @staticmethod
    def _tool_part_id(mid, call_id):
        return f"{mid}#c{call_id}"

    def _frame(self, etype, properties):
        return {'event': etype, 'data': {'type': etype,
                                         'properties': properties}}

    def _msg_frame(self, mid, sid, extra_info=None, ms_created=None):
        """合成 V1 message.updated 帧（assistant info）。首见消息时下发，
        令 chat_persist 把 mid 记进 assistant_msg_ids。"""
        info = {'id': mid, 'role': 'assistant', 'sessionID': sid,
                'time': {'created': ms_created or int(time.time() * 1000)}}
        info.update(self._seen_msgs.get(mid) or {})
        info.update(extra_info or {})
        return self._frame('message.updated',
                           {'info': info, 'sessionID': sid})

    def _ensure_msg(self, mid, sid, ms_created=None):
        """mid 首见时发一条 message.updated，后续不再重发（除非 step.ended
        等显式补 meta）。返回帧或 None。"""
        if mid in self._seen_msgs:
            return None
        self._seen_msgs[mid] = {}
        return self._msg_frame(mid, sid, ms_created=ms_created)

    # ---- 主入口 ----
    def translate(self, evt) -> list:
        """一条 V2 信封事件 {type, data, ...} → V1 形态帧列表。"""
        etype = (evt or {}).get('type') or ''
        data = (evt or {}).get('data') or {}
        if etype == 'server.connected':
            return []

        # --- 会话执行生命周期（V1 idle/error 的判活依据）---
        if etype == 'session.execution.succeeded':
            sid = data.get('sessionID') or ''
            return [self._frame('session.idle', {'sessionID': sid})]
        if etype == 'session.execution.failed':
            sid = data.get('sessionID') or ''
            return [self._frame('session.error',
                                {'sessionID': sid,
                                 'error': data.get('error')})]
        if etype == 'session.execution.interrupted':
            # V1 abort 后 OpenCode 同样发 session.idle（abort_session
            # docstring 实证）——interrupted→idle 是语义正确映射。
            sid = data.get('sessionID') or ''
            return [self._frame('session.idle', {'sessionID': sid})]

        # --- assistant 消息流（text/reasoning/tool 聚合成 part 帧）---
        if etype.startswith('session.step.'):
            return self._translate_step(etype, data)
        if etype.startswith('session.text.'):
            return self._translate_stream(etype, data, 'text')
        if etype.startswith('session.reasoning.'):
            return self._translate_stream(etype, data, 'reasoning')
        if etype.startswith('session.tool.'):
            return self._translate_tool(etype, data)

        # --- 权限（bypass 自动放行的输入；事件 data 无 id，需 resolver）---
        if etype == 'permission.asked':
            return self._translate_permission(data)

        # --- BRIDGE_LOSSY：V2 独有/暂不下发的事件 ---
        # session.created/renamed/updated、session.inbox.*、session.compaction.*、
        # session.usage.updated、session.skill.activated、form.*、
        # session.revert.*、filesystem.changed、agent/model/provider.updated、
        # deprecated session.idle/session.status（execution.* 已覆盖判活）。
        return []

    # ---- step 帧：消息元信息（model/finish/tokens/cost）----
    def _translate_step(self, etype, data):
        sid = data.get('sessionID') or ''
        mid = data.get('assistantMessageID') or ''
        if not mid:
            return []
        frames = []
        if etype == 'session.step.started':
            if mid not in self._seen_msgs:
                self._seen_msgs[mid] = {}
            model = data.get('model') or {}
            self._seen_msgs[mid].update({
                'providerID': model.get('providerID') or '',
                'modelID': model.get('id') or '',
                'agent': data.get('agent') or '',
            })
            f = self._msg_frame(mid, sid)
            if f:
                frames.append(f)
        elif etype == 'session.step.ended':
            # finish/tokens/cost/time.completed —— meta_from_info 的输入
            extra = {'finish': data.get('finish'),
                     'cost': data.get('cost'),
                     'tokens': data.get('tokens'),
                     'time': {'created': int(time.time() * 1000),
                              'completed': int(time.time() * 1000)}}
            frames.append(self._msg_frame(mid, sid, extra_info=extra))
        return frames

    # ---- text/reasoning 流 ----
    def _translate_stream(self, etype, data, ptype):
        sid = data.get('sessionID') or ''
        mid = data.get('assistantMessageID') or ''
        ordinal = data.get('ordinal', 0)
        if not mid:
            return []
        pid = self._text_part_id(mid, ordinal)
        key = (mid, ordinal)
        frames = []
        ensure = self._ensure_msg(mid, sid)
        if ensure:
            frames.append(ensure)
        if etype.endswith('.delta'):
            self._text_acc[key] = (self._text_acc.get(key) or '') + \
                (data.get('delta') or '')
        elif etype.endswith('.ended'):
            self._text_acc[key] = data.get('text') or ''
        part = {'id': pid, 'messageID': mid, 'sessionID': sid,
                'type': ptype, 'text': self._text_acc.get(key) or ''}
        frames.append(self._frame('message.part.updated',
                                  {'part': part, 'sessionID': sid}))
        return frames

    # ---- tool 调用流 ----
    def _translate_tool(self, etype, data):
        sid = data.get('sessionID') or ''
        mid = data.get('assistantMessageID') or ''
        call_id = data.get('id')
        if not mid or call_id is None:
            return []
        pid = self._tool_part_id(mid, call_id)
        key = (mid, call_id)
        acc = self._tool_acc.setdefault(key, {'input_raw': '',
                                              'metadata': {},
                                              'name': data.get('name')})
        frames = []
        ensure = self._ensure_msg(mid, sid)
        if ensure:
            frames.append(ensure)

        part = self._tool_part(key, pid, mid, sid)
        if etype == 'session.tool.input.started':
            acc['name'] = data.get('name') or acc.get('name') or ''
            part['tool'] = acc['name']
            part['state']['status'] = 'pending'
        elif etype == 'session.tool.input.delta':
            acc['input_raw'] += data.get('delta') or ''
            part['state']['status'] = 'pending'
        elif etype == 'session.tool.input.ended':
            acc['input_raw'] = data.get('text') or acc['input_raw']
            try:
                part['state']['input'] = json.loads(acc['input_raw']) \
                    if acc['input_raw'] else {}
            except (ValueError, TypeError):
                part['state']['input'] = {'_raw': acc['input_raw']}
            part['state']['status'] = 'pending'
        elif etype == 'session.tool.called':
            if data.get('input'):
                part['state']['input'] = data.get('input')
            part['state']['status'] = 'running' if data.get('executed') \
                else 'pending'
        elif etype == 'session.tool.progress':
            meta = data.get('metadata') or {}
            acc['metadata'].update(meta)
            # 子代理发现的关键透传点：V1 task 工具的子会话 id 在
            # state.metadata.sessionId——V2 对应字段 TEST-ME（二进制实测）。
            part['state']['metadata'] = dict(acc['metadata'])
            part['state']['status'] = 'running'
        elif etype == 'session.tool.success':
            content = data.get('content') or []
            out = ''.join(c.get('text') or '' for c in content
                          if isinstance(c, dict) and c.get('type') == 'text')
            part['state']['status'] = 'completed'
            part['state']['output'] = out
            if data.get('metadata'):
                acc['metadata'].update(data['metadata'])
                part['state']['metadata'] = dict(acc['metadata'])
        elif etype == 'session.tool.failed':
            err = data.get('error') or {}
            part['state']['status'] = 'error'
            part['state']['error'] = err if isinstance(err, dict) \
                else {'message': str(err)}
            part['state']['output'] = ''
        frames.append(self._frame('message.part.updated',
                                  {'part': normalize_v2_tool(part),
                                   'sessionID': sid}))
        return frames

    def _tool_part(self, key, pid, mid, sid):
        acc = self._tool_acc[key]
        raw = acc.get('input_raw') or ''
        try:
            inp = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            inp = {'_raw': raw}
        return {'id': pid, 'messageID': mid, 'sessionID': sid,
                'type': 'tool', 'tool': acc.get('name') or '',
                'callID': key[1],
                'state': {'status': 'pending', 'input': inp,
                          'output': '', 'metadata': dict(acc['metadata'])}}

    # ---- 权限 ----
    def _translate_permission(self, data):
        sid = data.get('sessionID') or ''
        props = {'sessionID': sid,
                 'permission': data.get('action') or '',
                 'patterns': data.get('resources') or [],
                 'metadata': data.get('metadata') or {},
                 'message': data.get('message')}
        req_id = ''
        if self._permission_id_resolver:
            try:
                req_id = self._permission_id_resolver(sid,
                                                      data.get('action')) or ''
            except Exception as e:  # noqa: BLE001
                logger.warning('permission id resolve failed sid=%s: %s',
                               sid, e)
        if not req_id:
            # BRIDGE_LOSSY：拿不到 per_ id——下发无 id 事件，bypass 放行
            # 优雅降级（消费方按无 id 跳过），不伪造。
            logger.info('permission.asked without request id sid=%s', sid)
        else:
            props['id'] = req_id
        return [self._frame('permission.asked', props)]
