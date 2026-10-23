"""GateProbe stub LLM —— 脚本化 OpenAI 兼容模型端点（过程检查漏底探针专用）。

作用：把「AI 是否采纳门禁注入的自检提示词」从模型行为方差变成确定性脚本。
真实链路全部为真（批 worker → OC serve → baize-trace 插件 → 后端门禁），
只有模型大脑是脚本——这正是构造「不采纳」场景的关键，生产上无法复现的
「模型无视注入提示词直接重调工具」在这里 100% 确定复现。

脚本选择：按首条 user 消息里的标记分流（[[GATE-PROBE:COMPLY]] /
[[GATE-PROBE:DEFY]]）；同一会话出现第 2 条 user 消息 = 门禁修正轮
（gate_retry continue），切修正脚本（补做步骤后重写）。

COMPLY（采纳）：write(被拦+注入) → read uploads/in.txt（= 采纳注入后补做）
             → write(放行) → 收尾文本
DEFY（违抗）：write(被拦+注入) → write 直接重调（无视注入）→ 收尾文本
修正轮（两者通用，n_user>=2 触发）：read → write → 收尾文本

每个请求全文落 <sandbox>/stub-requests.jsonl（注入文案是否到达模型的证据）。
"""
import hashlib
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get('GATEPROBE_STUB_PORT', '3099'))
# 证据日志默认落脚本旁，探针编排器用 GATEPROBE_LOG_DIR 指到沙箱（仓外）。
LOG_PATH = os.path.join(
    os.environ.get('GATEPROBE_LOG_DIR')
    or os.path.dirname(os.path.abspath(__file__)),
    'stub-requests.jsonl')

CONTENT = '要点一：过程检查探针。\n要点二：注入提示词确定性复现。\n要点三：终态断言兜底。'
WRITE_ARGS = {'filePath': 'outputs/gate-probe-report.md', 'content': CONTENT}
READ_ARGS = {'filePath': 'uploads/in.txt'}

SCRIPTS = {
    'COMPLY': [('tool', 'write'), ('tool', 'read'), ('tool', 'write'),
               ('final', None)],
    'DEFY': [('tool', 'write'), ('tool', 'write'), ('final', None)],
}
CORRECTION = [('tool', 'read'), ('tool', 'write'), ('final', None)]

_lock = threading.Lock()
_conv = {}          # key -> {'scenario','step','correcting'}


def _marker(text):
    m = re.search(r'\[\[GATE-PROBE:([A-Z-]+)\]\]', text or '')
    return m.group(1) if m else None


def _conv_key(messages):
    for msg in messages:
        if msg.get('role') == 'user':
            content = msg.get('content')
            if isinstance(content, list):
                content = ' '.join(
                    p.get('text', '') for p in content
                    if isinstance(p, dict))
            return hashlib.sha256((content or '').encode('utf-8')) \
                .hexdigest()[:16]
    return 'unknown'


def _next_action(key, messages, log):
    with _lock:
        st = _conv.setdefault(key, {'scenario': None, 'step': 0,
                                    'correcting': False})
        if st['scenario'] is None:
            for m in messages:
                if m.get('role') == 'user':
                    st['scenario'] = _marker(
                        m.get('content') if isinstance(m.get('content'), str)
                        else json.dumps(m.get('content'), ensure_ascii=False))
                    break
        if st['scenario'] not in SCRIPTS:
            # 非探针会话（OC 标题生成等旁路请求）：纯文本，绝不返回工具调用
            log['action'] = 'final:passthrough'
            return ('passthrough', None)
        n_user = sum(1 for m in messages if m.get('role') == 'user')
        if n_user >= 2 and not st['correcting']:
            st['correcting'] = True
            st['step'] = 0
            log['correction_round'] = True
        script = CORRECTION if st['correcting'] else SCRIPTS[st['scenario']]
        if st['step'] >= len(script):
            action = ('final', None)            # 脚本耗尽兜底：直接收尾
        else:
            action = script[st['step']]
        st['step'] += 1
        return action


def _tool_call(call_id, name, args):
    return {'id': call_id, 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(args)}}


def _compose(key, messages):
    log = {'key': key, 'n_messages': len(messages)}
    kind, name = _next_action(key, messages, log)
    log['action'] = f'{kind}:{name}'
    call_id = f'call_{key}_{int(time.time() * 1000)}'
    if kind == 'passthrough':
        return {'text': 'DONE', 'tool_calls': [], 'finish': 'stop',
                'log': log}
    if kind == 'final':
        text = ('已写入 outputs/gate-probe-report.md（GATE-PROBE 脚本收尾）。'
                if name is None else 'done')
        return {'text': text, 'tool_calls': [], 'finish': 'stop', 'log': log}
    args = WRITE_ARGS if name == 'write' else READ_ARGS
    return {'text': '', 'tool_calls': [_tool_call(call_id, name, args)],
            'finish': 'tool_calls', 'log': log}


def _log_request(log, body):
    """全量请求落 JSONL：工具结果里是否出现注入文案，在这里取证。"""
    entry = {'ts': time.strftime('%Y-%m-%d %H:%M:%S'), **log,
             'messages': []}
    for m in body.get('messages') or []:
        role = m.get('role')
        item = {'role': role}
        if role == 'tool':
            c = m.get('content')
            if isinstance(c, list):
                c = ' '.join(p.get('text', '') for p in c
                             if isinstance(p, dict))
            item['tool_result'] = (c or '')[:4000]
        elif role == 'user':
            c = m.get('content')
            if isinstance(c, list):
                c = ' '.join(p.get('text', '') for p in c
                             if isinstance(p, dict))
            item['text'] = (c or '')[:400]
        elif role == 'assistant':
            tcs = m.get('tool_calls') or []
            if tcs:
                item['tool_calls'] = [
                    t.get('function', {}).get('name') for t in tcs]
            c = m.get('content')
            if c:
                item['text'] = str(c)[:200]
        entry['messages'].append(item)
    with _lock:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def _sse_chunks(resp_id, model, composed):
    base = {'id': resp_id, 'object': 'chat.completion.chunk',
            'created': int(time.time()), 'model': model}
    deltas = []
    if composed['text']:
        deltas.append({'role': 'assistant', 'content': composed['text']})
    for i, tc in enumerate(composed['tool_calls']):
        deltas.append({'role': 'assistant', 'content': None,
                       'tool_calls': [{'index': i, 'id': tc['id'],
                                       'type': 'function',
                                       'function': {'name': tc['function']['name'],
                                                    'arguments': ''}}]})
        deltas.append({'tool_calls': [{'index': i,
                                       'function': {'arguments': tc['function']['arguments']}}]})
    chunks = []
    for d in deltas:
        chunks.append({**base, 'choices': [{'index': 0, 'delta': d,
                                            'finish_reason': None}]})
    chunks.append({**base, 'choices': [{'index': 0, 'delta': {},
                                        'finish_reason': composed['finish']}]})
    return chunks


class H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def _respond_json(self, obj, status=200):
        data = json.dumps(obj).encode('utf-8')
        self.send_response(status)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip('/').endswith('/models'):
            self._respond_json({'object': 'list', 'data': [
                {'id': 'scripted', 'object': 'model', 'owned_by': 'gateprobe'}]})
            return
        self._respond_json({'error': 'not found'}, 404)

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = json.loads(self.rfile.read(length) or b'{}')
        if not self.path.rstrip('/').endswith('/chat/completions'):
            self._respond_json({'error': 'not found'}, 404)
            return
        messages = body.get('messages') or []
        print(f'[gateprobe-stub] POST {self.path} messages={len(messages)} '
              f'stream={bool(body.get("stream"))}', flush=True)
        key = _conv_key(messages)
        composed = _compose(key, messages)
        _log_request(composed['log'], body)
        resp_id = f'chatcmpl-{key}-{composed["log"]["action"]}'
        model = body.get('model') or 'scripted'
        if body.get('stream'):
            self.send_response(200)
            self.send_header('content-type', 'text/event-stream')
            self.send_header('cache-control', 'no-cache')
            # HTTP/1.1 无 Content-Length 的 body 以连接关闭为界——不关连接
            # 客户端会一直等到超时（首次冒烟 "SSE read timed out" 的根因）。
            self.send_header('connection', 'close')
            self.end_headers()
            for chunk in _sse_chunks(resp_id, model, composed):
                self.wfile.write(
                    b'data: ' + json.dumps(chunk).encode('utf-8') + b'\n\n')
                self.wfile.flush()
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
            self.close_connection = True
        else:
            msg = {'role': 'assistant', 'content': composed['text'] or None}
            if composed['tool_calls']:
                msg['tool_calls'] = composed['tool_calls']
            self._respond_json({
                'id': resp_id, 'object': 'chat.completion',
                'created': int(time.time()), 'model': model,
                'choices': [{'index': 0, 'message': msg,
                             'finish_reason': composed['finish']}],
                'usage': {'prompt_tokens': 1, 'completion_tokens': 1,
                          'total_tokens': 2},
            })

    def log_message(self, *a):
        pass


def main():
    srv = ThreadingHTTPServer(('127.0.0.1', PORT), H)
    print(f'[gateprobe-stub] listening :{PORT}, log → {LOG_PATH}', flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
