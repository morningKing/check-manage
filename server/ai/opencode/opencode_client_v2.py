"""OpenCode 2.x HTTP 客户端（契约基线：v2.2.6 真机实测 + dev@ed0171f
源码级提取；TEST-ME 注释 = 仍待复核的点）。

与 ai.opencode.opencode_client.OpenCodeClient 同签名同返回形状——上层消费方
（batch facade / routes / runtime / kefu / verifier / suggest_editor）零改动。
V2 差异全部在内部消化：

- 路由面 /api/*；location-scoped 端点经 ``?location[directory]=``
  （deepObject）传递目录；session 级端点由 sessionID 反查 location；
- 响应统一 ``{data}``（location-scoped ``{location, data}``）包装 → 解包；
- V2 投影消息 → V1 ``{info, parts}`` 形状（_translate_messages），工具名
  归一化回 V1（shell→bash、subagent→task）经 events_bridge_v2.normalize_v2_tool；
- question→form、permission reply body 字段 ``decision``、abort→interrupt；
- /api/event 全局 SSE 流 → events_bridge_v2 翻译成 V1 帧序列。

与 V1 签名的两处显式差异（V2 API 结构所迫，见各方法 docstring）：
- reply/reject question、reply permission 需要调用方传 ``oc_session_id``
  （V2 这些端点是会话作用域；V1 是全局 id 寻址）。

认证（真机实测）：``opencode serve`` 即使未设 OPENCODE_PASSWORD 也会生成
随机密码并打印到 stdout（"server password <token>" 行）——启动链路必须
从 serve 日志刮取并传入构造器；未传密码时所有请求 401。

SSRF 防护：所有含动态段的 URL 在发送前做协议/主机/端口边界断言
（部署契约 = OPENCODE2_BASE_URL 默认 'http://127.0.0.1:4196'，字面量
校验；python -O 下断言被剥离，主防线是 _ID_RE 白名单 + percent-encode）。
"""
import json
import logging
import os as _os
import re
import time
from typing import Iterator
from urllib.parse import quote, urlsplit

import requests

from ai.opencode.opencode_client import OpenCodeError  # 上层 except 兼容同一异常

logger = logging.getLogger(__name__)

# V2 消息列表分页上限（session.message.list limit 1-200）——get_messages
# 循环 cursor.next 拉全量，V1 语义是无上限全量列表。
_PAGE_LIMIT = 200

# OC2 会话/请求 id 白名单（ses_*/msg_*/frm_*/per_* 同族：字母数字+_-）。
_ID_RE = re.compile(r'[A-Za-z0-9_-]+')

# 动态段拼进 URL 路径的请求：部署契约字面量（与 OPENCODE2_BASE_URL 默认值
# 一致）。改部署端口/主机时同步更新。
_OC2_URL_SCHEME = 'http'
_OC2_URL_HOST = '127.0.0.1'
_OC2_URL_PORT = 4196


def _oc2_request(method: str, url: str, **kwargs):
    """双 id 路径请求的统一入口：requests 官方通用 API（requests.post 等
    动词的底层实现），发送前做协议/主机/端口边界断言。

    动态路径段已在调用方完成 _ID_RE 白名单 + percent-encode；此处断言是
    纵深防御（python -O 下剥离——主防线是白名单）。"""
    _u = urlsplit(url)
    assert _u.scheme == _OC2_URL_SCHEME, 'oc2 URL scheme boundary'
    assert _u.hostname == _OC2_URL_HOST and _u.port == _OC2_URL_PORT, \
        'oc2 URL host boundary violated'
    return requests.request(method, url, **kwargs)


class OpenCodeClientV2:
    def __init__(self, base_url: str, timeout: int = 30, password: str = ''):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._password = (password or '').strip()
        if self._password:
            import base64
            token = base64.b64encode(
                f'opencode:{self._password}'.encode()).decode()
            self._auth_headers = {'Authorization': f'Basic {token}'}
        else:
            self._auth_headers = {}

    # ---- 基础 ----
    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _loc_params(self, directory: str) -> dict:
        """V2 location 传递：deepObject query（真机实测与
        server/location.ts:57-63 一致）。缺省目录 = server 启动 cwd——
        平台侧总是显式传。"""
        return {'location[directory]': directory} if directory else {}

    def _headers(self) -> dict:
        return dict(self._auth_headers)

    @staticmethod
    def _unwrap_data(resp_json):
        """V2 响应统一 {data} 包装（location-scoped 再包一层 {location}，
        真机实测一致）。"""
        if isinstance(resp_json, dict) and 'data' in resp_json:
            return resp_json['data']
        return resp_json

    # ---- 会话生命周期 ----
    def create_session(self, *, directory: str, title: str = "") -> str:
        """POST /api/session（真机实测：响应 {data:{id: ses_*, ...}}）。"""
        t0 = time.monotonic()
        try:
            resp = requests.post(
                self._url("/api/session"),
                params=self._loc_params(directory),
                json={'title': title, 'location': {'directory': directory}},
                # 与 V1 同理：冷目录 bootstrap 可达 60-90s+（V2 location
                # 按需加载）——创建放宽到 120s。
                timeout=max(self.timeout, 120),
                headers=self._headers(),
            )
            resp.raise_for_status()
        except Exception as e:
            logger.warning('oc2 create_session failed dir=%s: %s', directory, e)
            raise
        data = self._unwrap_data(resp.json()) or {}
        oc_id = data.get('id') or ''
        if not oc_id:
            raise OpenCodeError('create_session: response missing id')
        logger.info('oc2 create_session ok id=%s dir=%s %.0fms',
                    oc_id, directory, (time.monotonic() - t0) * 1000)
        return oc_id

    def delete_session(self, opencode_session_id: str) -> None:
        """V2 DELETE /api/session/:id 递归删子会话（session.remove）。
        204/404 容忍，与 V1 相同。"""
        try:
            resp = requests.delete(
                self._url(f"/api/session/{opencode_session_id}"),
                timeout=self.timeout, headers=self._headers())
            if resp.status_code not in (200, 204, 404):
                resp.raise_for_status()
        except requests.RequestException as e:
            raise OpenCodeError(str(e))

    def get_messages(self, opencode_session_id: str, directory: str = "") -> list:
        """GET /api/session/:id/message（真机实测：{data, cursor:{next}}，
        分页拉全量）→ V1 ``{info, parts}`` 形状列表。

        真机实测 assistant 投影：{id, agent, model:{providerID,id},
        finish, cost, tokens, error, time:{created,streamed,completed},
        content:[...]} —— finish/tokens/cost/error/time.completed 与 V1
        info 同名，facade 完成判定与 meta_from_info 直接兼容。"""
        out = []
        cursor = ''
        for _page in range(50):  # 200×50=1 万条防御上限
            if cursor:
                # V2 游标内嵌排序状态（真机实测）：带 cursor 再传 limit/order
                # 直接 400——翻页请求只带 cursor。
                params = {'cursor': cursor}
            else:
                params = {'limit': '200', 'order': 'asc'}
            resp = requests.get(
                self._url(f"/api/session/{opencode_session_id}/message"),
                params=params, timeout=self.timeout, headers=self._headers())
            resp.raise_for_status()
            body = resp.json() or {}
            data = body.get('data') or []
            out.extend(self._translate_messages(data))
            cursor = ((body.get('cursor') or {}).get('next') or '')
            if not cursor or not data:
                break
        return out

    @staticmethod
    def _translate_messages(v2_messages: list) -> list:
        """V2 SessionMessage → V1 {info, parts}。翻 user/assistant 两类；
        其余类型（compaction/shell/subtask-switched/idle——真机实测列表里有
        idle 型消息）跳过——BRIDGE_LOSSY 登记。"""
        from ai.sessions.events_bridge_v2 import normalize_v2_tool
        out = []
        for m in v2_messages or []:
            mtype = m.get('type')
            mid = m.get('id') or ''
            ms = (m.get('time') or {}).get('created')
            if mtype == 'assistant':
                info = {'id': mid, 'role': 'assistant',
                        'agent': m.get('agent') or '',
                        'finish': m.get('finish'),
                        'error': m.get('error'),
                        'cost': m.get('cost'),
                        'tokens': m.get('tokens'),
                        'time': dict(m.get('time') or {})}
                model = m.get('model') or {}
                if model.get('providerID'):
                    info['providerID'] = model['providerID']
                    info['modelID'] = model.get('id') or ''
                parts = []
                for c in m.get('content') or []:
                    ctype = c.get('type')
                    if ctype == 'text':
                        parts.append({'type': 'text',
                                      'text': c.get('text') or ''})
                    elif ctype == 'reasoning':
                        parts.append({'type': 'reasoning',
                                      'text': c.get('text') or ''})
                    elif ctype == 'tool':
                        st = c.get('state') or {}
                        status = {'streaming': 'pending', 'running': 'running',
                                  'completed': 'completed',
                                  'error': 'error'}.get(st.get('status'),
                                                        'pending')
                        content = st.get('content') or []
                        out_text = ''.join(
                            x.get('text') or '' for x in content
                            if isinstance(x, dict) and x.get('type') == 'text')
                        state = {'status': status,
                                 'input': st.get('input') or {},
                                 'output': out_text,
                                 'metadata': st.get('metadata') or {}}
                        if st.get('status') == 'error' and st.get('error'):
                            state['error'] = st['error']
                        parts.append(normalize_v2_tool({
                            'type': 'tool', 'tool': c.get('name') or '',
                            'callID': c.get('id') or '',
                            'id': c.get('id') or '',
                            'state': state,
                            'time': dict(c.get('time') or {})}))
                out.append({'info': info, 'parts': parts})
            elif mtype == 'user':
                out.append({'info': {'id': mid, 'role': 'user',
                                     'time': {'created': ms}},
                            'parts': [{'type': 'text',
                                       'text': m.get('text') or ''}]})
        return out

    def send_prompt_async(self, opencode_session_id: str, content: str,
                          model: str = "", directory: str = "",
                          agent: str = "", agent_parts=None,
                          image_parts=None) -> None:
        """POST /api/session/:id/prompt（真机实测：响应 {data: inbox user}）。

        V1 parts 数组 → V2 PromptInput 字段：
        - 文本 → ``text``；
        - image_parts {name,mime,url} → ``files:[{uri, name}]``（TEST-ME：
          data: URI 是否被 V2 接受）；
        - agent_parts {name, source{value,start,end}} → ``agents:[{name,
          mention:{start,end,text}}]``。
        model "<p>/<m>" → {providerID, id}（真机实测接受）。"""
        body = {'text': content}
        files = []
        for img in (image_parts or []):
            uri = img.get('url')
            if uri:
                files.append({'uri': uri, 'name': img.get('name') or None})
        agents = []
        for m in (agent_parts or []):
            name = (m.get('name') or '').strip()
            if not name:
                continue
            src = m.get('source') if isinstance(m.get('source'), dict) else m
            entry = {'name': name}
            if all(k in src for k in ("value", "start", "end")):
                entry['mention'] = {'start': src['start'], 'end': src['end'],
                                    'text': src['value']}
            agents.append(entry)
        if files:
            body['files'] = files
        if agents:
            body['agents'] = agents
        if model and "/" in model:
            provider_id, model_id = model.split("/", 1)
            body['model'] = {'providerID': provider_id, 'id': model_id}
        if agent:
            body['agent'] = agent
        t0 = time.monotonic()
        logger.info('oc2 send_prompt -> session=%s model=%s agent=%s '
                    'files=%d agents=%d len=%d',
                    opencode_session_id, model or 'default',
                    agent or 'default', len(files), len(agents), len(content))
        try:
            resp = requests.post(
                self._url(f"/api/session/{opencode_session_id}/prompt"),
                json=body, timeout=self.timeout, headers=self._headers())
            resp.raise_for_status()
        except Exception as e:
            logger.warning('oc2 send_prompt FAILED session=%s %.0fms: %s',
                           opencode_session_id,
                           (time.monotonic() - t0) * 1000, e)
            raise

    def abort_session(self, opencode_session_id: str, directory: str = "") -> None:
        """POST /api/session/:id/interrupt（resume=False = V1 abort 语义）。
        V1 abort 后 OC 发 session.idle——V2 由桥把 execution.interrupted
        翻成 session.idle，语义闭环。"""
        try:
            requests.post(
                self._url(f"/api/session/{opencode_session_id}/interrupt"),
                json={'resume': False}, timeout=self.timeout,
                headers=self._headers())
        except requests.RequestException as e:
            raise OpenCodeError(str(e))

    def summarize_session(self, opencode_session_id: str, provider_id: str,
                          model_id: str, directory: str = "") -> None:
        """POST /api/session/:id/compact（steer 默认：下一步边界执行而非
        等待完成——与 V1 summarize 阻塞语义有差异，登记；TEST-ME：
        compact payload 是否收 model）。"""
        body = {}
        if provider_id and model_id:
            body['model'] = {'providerID': provider_id, 'id': model_id}
        resp = requests.post(
            self._url(f"/api/session/{opencode_session_id}/compact"),
            json=body, timeout=(10, None), headers=self._headers())
        resp.raise_for_status()

    # ---- 目录列表端点 ----
    def list_providers(self) -> dict:
        """GET /api/provider + /api/model 合成 V1 ``{all, default,
        connected}`` 形状（前端模型选择器）。models map 值为 V2 Model.Info
        透传——前端消费字段 TEST-ME（Phase 3 对齐）。"""
        resp = requests.get(self._url("/api/provider"),
                            params=self._loc_params(''),
                            timeout=self.timeout, headers=self._headers())
        resp.raise_for_status()
        providers = self._unwrap_data(resp.json()) or []
        resp2 = requests.get(self._url("/api/model"),
                             params=self._loc_params(''),
                             timeout=self.timeout, headers=self._headers())
        resp2.raise_for_status()
        models = self._unwrap_data(resp2.json()) or []
        by_provider = {}
        for m in models:
            pid = m.get('providerID') or ''
            by_provider.setdefault(pid, {})[m.get('id') or ''] = m
        all_list = []
        connected = {}
        for p in providers:
            pid = p.get('id') or ''
            all_list.append({'id': pid, 'name': p.get('name') or pid,
                             'models': by_provider.get(pid, {})})
            connected[pid] = (p.get('activation') or '') in ('enabled', 'auto')
        default = {}
        try:
            resp3 = requests.get(self._url("/api/model/default"),
                                 params=self._loc_params(''),
                                 timeout=self.timeout,
                                 headers=self._headers())
            if resp3.ok:
                dm = self._unwrap_data(resp3.json()) or {}
                if dm.get('providerID') and dm.get('id'):
                    default = {dm['providerID']: dm['id']}
        except requests.RequestException:
            pass
        return {'all': all_list, 'default': default, 'connected': connected}

    def _list_scoped(self, path: str, directory: str):
        """location-scoped 列表端点通用取数（{location,data} 解包）。path
        只接受类内常量端点路径。"""
        resp = requests.get(self._url(path),
                            params=self._loc_params(directory),
                            timeout=self.timeout, headers=self._headers())
        resp.raise_for_status()
        return self._unwrap_data(resp.json()) or []

    def list_commands(self, directory: str = "") -> list:
        return self._list_scoped("/api/command", directory)

    def list_skills(self, directory: str = "") -> list:
        return self._list_scoped("/api/skill", directory)

    def list_agents(self, directory: str = "") -> list:
        """GET /api/agent → V1 形状（name=id 保标识语义；批预检
        _check_agent 按 name 匹配）。hidden 透传。真机实测 V2 内置 agent
        的 hidden 集合仍是 compaction/title/summary——INTERNAL_AGENTS
        名单兼容。"""
        agents = self._list_scoped("/api/agent", directory)
        out = []
        for a in agents or []:
            out.append({'name': a.get('id') or '',
                        'mode': a.get('mode') or 'subagent',
                        'description': a.get('description') or '',
                        'model': (a.get('model') or {}).get('id') or '',
                        'hidden': bool(a.get('hidden')),
                        '_v2': a})
        return out

    def run_command(self, opencode_session_id: str, command: str,
                    arguments: str = "", model: str = "",
                    directory: str = "") -> None:
        """POST /api/session/:id/command body {name, text}。V1 的 model
        字符串参数 V2 命令端点不收（会话模型走会话级切换端点）——显式
        忽略并落 info（命令面板场景，批任务不经过）。"""
        if model:
            logger.info('oc2 run_command ignores model=%s', model)
        body = {'name': command, 'text': arguments or ''}
        resp = requests.post(
            self._url(f"/api/session/{opencode_session_id}/command"),
            json=body, timeout=(10, None), headers=self._headers())
        resp.raise_for_status()

    # ---- question → form 桥 ----
    def list_questions(self, directory: str = "") -> list:
        """GET /api/form → 合成 V1 QuestionRequest 近似形状（每项含
        sessionID——V2 reply/cancel 端点是会话作用域，调用方从中取
        sessionID 传回 reply_question/reject_question）。"""
        forms = self._list_scoped("/api/form", directory)
        out = []
        for f in forms or []:
            questions = []
            for fld in (f.get('fields') or []):
                opts = [{'label': (o.get('value') if o.get('value') is not None
                                   else o.get('label') or ''),
                         'description': o.get('description') or ''}
                        for o in (fld.get('options') or [])
                        if isinstance(o, dict)]
                questions.append({
                    'question': fld.get('title') or fld.get('key') or '',
                    'header': fld.get('key') or '',
                    'options': opts,
                    'multiple': fld.get('type') == 'multiselect',
                    'custom': fld.get('type') == 'string'
                              and bool(fld.get('custom')),
                })
            out.append({'id': f.get('id') or '',
                        'sessionID': f.get('sessionID') or '',
                        'tool': 'form',
                        'questions': questions,
                        '_v2_form': f})
        return out

    def reply_question(self, request_id: str, answers: list,
                       directory: str = "",
                       oc_session_id: str = "") -> bool:
        """V1 answers=[[label], ...] → V2 {answer: {key: value}}。

        ``oc_session_id`` 必填（缺省抛错）：V2 form reply 端点是会话作用域
        （POST /api/session/:sid/form/:formID/reply），调用方从
        list_questions() 的返回项里取 sessionID 传入。answers 按字段顺序
        把选中 label 映射回 options.value（尽力而为桥接，复杂表单 Phase 3
        原生适配）。"""
        if not oc_session_id or not _ID_RE.fullmatch(oc_session_id):
            raise ValueError('oc_session_id is required by the V2 form API '
                             'and must match [A-Za-z0-9_-]+')
        if not request_id or not _ID_RE.fullmatch(request_id):
            raise ValueError(f'invalid form id: {request_id!r}')
        sid = quote(oc_session_id, safe='')
        rid = quote(request_id, safe='')
        answer = {}
        try:
            # 字段映射需要 form 主体（fields/options）；拿不到时退化为
            # 纯文本应答（answer 兜底 key 'answer'）。
            form = {}
            for f in self._list_scoped("/api/form", directory) or []:
                if f.get('id') == request_id:
                    form = f
                    break
        except requests.RequestException:
            form = {}
        fields = (form or {}).get('fields') or []
        if fields:
            for i, fld in enumerate(fields):
                pick = None
                if i < len(answers) and answers[i]:
                    pick = answers[i][0] if isinstance(answers[i], list) \
                        else answers[i]
                if pick is None:
                    continue
                value = pick
                for o in (fld.get('options') or []):
                    if not isinstance(o, dict):
                        continue
                    if pick in (o.get('label'), o.get('value')):
                        value = o.get('value') if o.get('value') is not None \
                            else pick
                        break
                answer[fld.get('key') or f'field{i}'] = value
        else:
            flat = [a[0] if isinstance(a, list) else a
                    for a in (answers or []) if a]
            answer = {'answer': flat[0] if flat else ''}
        resp = _oc2_request('POST',
                            self._url("/api/session/%s/form/%s/reply"
                                      % (sid, rid)),
                            json={'answer': answer},
                            timeout=self.timeout,
                            headers=self._headers())
        resp.raise_for_status()
        return True

    def reject_question(self, request_id: str, directory: str = "",
                        oc_session_id: str = "") -> bool:
        """批任务自动拒问主路径：DELETE /api/session/:sid/form/:formID
        （cancel，带 reason）。

        ``oc_session_id`` 必填（缺省抛错）——调用方从 list_questions()
        返回项里取 sessionID 传入。"""
        if not oc_session_id or not _ID_RE.fullmatch(oc_session_id):
            raise ValueError('oc_session_id is required by the V2 form API '
                             'and must match [A-Za-z0-9_-]+')
        if not request_id or not _ID_RE.fullmatch(request_id):
            raise ValueError(f'invalid form id: {request_id!r}')
        sid = quote(oc_session_id, safe='')
        rid = quote(request_id, safe='')
        resp = _oc2_request('DELETE',
                            self._url("/api/session/%s/form/%s"
                                      % (sid, rid)),
                            params={'message': 'rejected by platform'},
                            timeout=self.timeout,
                            headers=self._headers())
        resp.raise_for_status()
        return True

    # ---- permission ----
    def list_permissions(self, directory: str = "") -> list:
        """GET /api/permission/request → V1 形状：{id, sessionID,
        permission, patterns, metadata, tool}（V1 patterns ≙ V2 resources，
        真机实测字段名一致；V1 的 always 字段 V2 无对应——省略）。"""
        reqs = self._list_scoped("/api/permission/request", directory)
        out = []
        for r in reqs or []:
            src = r.get('source') or {}
            out.append({'id': r.get('id') or '',
                        'sessionID': r.get('sessionID') or '',
                        'permission': r.get('action') or '',
                        'patterns': r.get('resources') or [],
                        'metadata': r.get('metadata') or {},
                        'message': r.get('message'),
                        'tool': src.get('type') or '',
                        '_v2': r})
        return out

    def reply_permission(self, request_id: str, reply: str,
                         directory: str = "",
                         oc_session_id: str = "") -> None:
        """POST /api/session/:sid/permission/:requestID/reply body
        {decision}（V1 body 字段是 {reply}——V2 改名 decision，取值相同）。

        ``oc_session_id`` 必填（缺省抛错）——调用方从 list_permissions()
        返回项或 permission.asked 事件的 sessionID 传入。"""
        if reply not in ('once', 'always', 'reject'):
            raise ValueError(f'invalid permission reply: {reply!r}')
        if not oc_session_id or not _ID_RE.fullmatch(oc_session_id):
            raise ValueError('oc_session_id is required by the V2 permission '
                             'API and must match [A-Za-z0-9_-]+')
        if not request_id or not _ID_RE.fullmatch(request_id):
            raise ValueError(f'invalid permission request id: {request_id!r}')
        sid = quote(oc_session_id, safe='')
        rid = quote(request_id, safe='')
        resp = _oc2_request('POST',
                            self._url("/api/session/%s/permission/%s/reply"
                                      % (sid, rid)),
                            json={'decision': reply},
                            timeout=self.timeout,
                            headers=self._headers())
        resp.raise_for_status()

    # ---- undo/redo（V1 revert 三元组的 V2 对应）----
    def revert_stage(self, opencode_session_id: str, oc_message_id: str,
                     directory: str = "") -> dict:
        """POST /api/session/:id/revert/stage body {messageID}。

        V2 响应（真机实测）：{messageID, files}——无 V1 的 'revert' 键
        （snapshot/diff 概念走 V2 的 /diff 端点）。适配成 V1 形状
        {'revert': {messageID, files}}：undo 编排器的 REVERT_MISMATCH
        校验读 ['revert']['messageID']，零改动工作。"""
        resp = requests.post(
            self._url(f"/api/session/{opencode_session_id}/revert/stage"),
            json={'messageID': oc_message_id},
            timeout=max(self.timeout, 20), headers=self._headers())
        resp.raise_for_status()
        data = self._unwrap_data(resp.json()) or {}
        if 'revert' in data:   # 防御：V2 若未来直接回 revert 态则透传
            return data
        return {'revert': {'messageID': data.get('messageID') or '',
                           'files': data.get('files') or []}}

    def revert_session(self, opencode_session_id: str, message_id: str,
                       directory: str = "") -> dict:
        return self.revert_stage(opencode_session_id, message_id,
                                 directory=directory)

    def revert_clear(self, opencode_session_id: str,
                     directory: str = "") -> None:
        """DELETE /api/session/:id/revert（撤销重做；无撤销态 no-op）。"""
        resp = requests.delete(
            self._url(f"/api/session/{opencode_session_id}/revert"),
            timeout=max(self.timeout, 20), headers=self._headers())
        if resp.status_code not in (200, 204, 404, 409):
            resp.raise_for_status()

    def unrevert_session(self, opencode_session_id: str,
                         directory: str = "") -> None:
        self.revert_clear(opencode_session_id, directory=directory)

    def get_project(self, directory: str = "") -> dict:
        """undo 文件能力分级输入（vcs/worktree 判定）。V2 合成：location
        信息 + /api/vcs。形状近似 {id, vcs, worktree}——TEST-ME。"""
        out = {}
        try:
            resp = requests.get(self._url("/api/location"),
                                params=self._loc_params(directory),
                                timeout=self.timeout, headers=self._headers())
            if resp.ok:
                data = self._unwrap_data(resp.json()) or {}
                proj = data.get('project') or {}
                out['id'] = proj.get('id') or ''
                out['worktree'] = data.get('directory') or directory
        except requests.RequestException:
            pass
        try:
            resp = requests.get(self._url("/api/vcs"),
                                params=self._loc_params(directory),
                                timeout=self.timeout, headers=self._headers())
            if resp.ok:
                vcs = self._unwrap_data(resp.json()) or {}
                out['vcs'] = 'git' if (vcs.get('current')
                                       or vcs.get('default')) else ''
        except requests.RequestException:
            out.setdefault('vcs', '')
        return out

    def list_mcp(self, directory: str = "") -> dict:
        """GET /api/mcp → V1 形状 {name: {status}}。"""
        servers = self._list_scoped("/api/mcp", directory)
        out = {}
        for s in servers or []:
            name = s.get('name') or ''
            st = (s.get('status') or {}).get('status') or ''
            out[name] = {'status': st}
        return out

    def list_lsp(self, directory: str = "") -> list:
        """V2 无 LSP（官方移除）——恒空列表，调用方按空降级。"""
        return []

    def list_formatters(self, directory: str = "") -> list:
        """V2 formatter 状态端点未在契约提取清单内——恒空降级（TEST-ME）。"""
        return []

    # ---- 事件流 ----
    def subscribe_events(self, directory: str = "",
                         read_timeout=None) -> Iterator[dict]:
        """GET /api/event 全局流（V2 无 per-directory 订阅；Basic auth 同样
        要求，真机实测）→ 按 location.directory 过滤 → 事件桥翻译成 V1
        帧序列。

        帧：data-only（真机实测一致），15s heartbeat 注释帧（': heartbeat'
        行不以 data: 开头，解析器自然忽略）。首帧 server.connected 跳过。
        permission id resolver：桥收到 permission.asked 时反查一次列表补
        per_ id（bypass 放行链路；V2 permission.asked 事件 data 无 id）。"""
        from ai.sessions.events_bridge_v2 import V2EventBridge

        def _resolve_permission_id(sid, action):
            for r in self._list_scoped("/api/permission/request", directory):
                if r.get('sessionID') == sid and (
                        not action or r.get('action') == action):
                    return r.get('id') or ''
            return ''

        bridge = V2EventBridge(permission_id_resolver=_resolve_permission_id)

        params = {}
        if directory:
            params['location[directory]'] = directory
        with requests.get(
            self._url("/api/event"),
            params=params,
            stream=True,
            timeout=read_timeout,
            headers={'Accept': 'text/event-stream', **self._headers()},
        ) as resp:
            resp.raise_for_status()
            data_buf = []
            for raw in resp.iter_lines():
                if raw is None:
                    continue
                line = raw.decode('utf-8') if isinstance(raw, bytes) else raw
                line = line.rstrip('\r\n')
                if line == '':
                    if data_buf:
                        joined = ''.join(data_buf)
                        try:
                            obj = json.loads(joined)
                        except json.JSONDecodeError:
                            obj = {'_raw': joined}
                        data_buf = []
                        if not isinstance(obj, dict):
                            continue
                        if obj.get('type') == 'server.connected':
                            continue
                        # location 过滤：V2 信封里的目录是 OC 规范化形态
                        # （Windows 反斜杠），调用方可能传正斜杠——两侧
                        # normcase+normpath 归一后再比（否则 text/tool 事件
                        # 被整批误杀，只剩无 location 信封的 execution.*）。
                        if directory:
                            _loc = ((obj.get('location') or {})
                                    .get('directory') or '')
                            if _loc and _os.path.normcase(
                                    _os.path.normpath(_loc)) != _os.path \
                                    .normcase(_os.path.normpath(directory)):
                                continue
                        for frame in bridge.translate(obj):
                            yield frame
                    data_buf = []
                elif line.startswith('data:'):
                    data_buf.append(line[len('data:'):].strip())
                # ': heartbeat' 注释帧与 event:/id:/retry: 行——V2 只发
                # data-only + 注释帧，其余忽略
