"""ScopeProbe stub LLM —— 脚本化 OpenAI 兼容模型端点（插件作用域探针专用）。

与 gate-leak-probe/stub_llm.py 同款形态（脚本化模型端点，把模型行为方差变
确定性），脚本简化为：任何会话的第一步 bash `echo scope-probe`，然后收尾
done。真实链路全部为真（OC serve → 三个平台插件 → sink），只有模型大脑是
脚本。每个请求全文落 <sandbox>/stub-requests.jsonl。
"""
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get('GATEPROBE_STUB_PORT', '4199'))
LOG_PATH = os.path.join(
    os.environ.get('GATEPROBE_LOG_DIR')
    or os.path.dirname(os.path.abspath(__file__)),
    'stub-requests.jsonl')

TOOL_CALL = {'id': 'call_scope_probe', 'type': 'function',
             'function': {'name': 'bash',
                          'arguments': '{"command":"echo scope-probe"}'}}


def _log_request(body):
    try:
        n = len(body.get('messages') or [])
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(json.dumps({'ts': time.strftime('%H:%M:%S'),
                                'n_messages': n}, ensure_ascii=False) + '\n')
    except Exception:
        pass


def _sse_chunks(resp_id, model):
    base = {'id': resp_id, 'object': 'chat.completion.chunk',
            'created': int(time.time()), 'model': model}
    chunks = [
        {**base, 'choices': [{'index': 0,
                              'delta': {'role': 'assistant', 'content': None},
                              'finish_reason': None}]},
        {**base, 'choices': [{'index': 0, 'delta': {'tool_calls': [
            {'index': 0, 'id': TOOL_CALL['id'], 'type': 'function',
             'function': {'name': 'bash', 'arguments': ''}}]}}]},
        {**base, 'choices': [{'index': 0, 'delta': {'tool_calls': [
            {'index': 0, 'function': {'arguments': TOOL_CALL['function']['arguments']}}]}}]},
        {**base, 'choices': [{'index': 0, 'delta': {},
                              'finish_reason': 'tool_calls'}]},
    ]
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
                {'id': 'scripted', 'object': 'model', 'owned_by': 'scopeprobe'}]})
            return
        self._respond_json({'error': 'not found'}, 404)

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = json.loads(self.rfile.read(length) or b'{}')
        if not self.path.rstrip('/').endswith('/chat/completions'):
            self._respond_json({'error': 'not found'}, 404)
            return
        _log_request(body)
        print(f'[scopeprobe-stub] POST messages={len(body.get("messages") or [])}',
              flush=True)
        # 终态：本轮对话里已出现过 tool 结果（bash 跑完）→ 收尾 done，
        # 否则会无限循环（stub 每次都回工具调用，OC 会一直再问模型）。
        finished = any((m.get('role') == 'tool') for m in body.get('messages') or [])
        resp_id = f'chatcmpl-{int(time.time() * 1000)}'
        model = body.get('model') or 'scripted'
        if finished:
            self.send_response(200)
            self.send_header('content-type', 'application/json')
            msg = {'role': 'assistant', 'content': 'done'}
            data = json.dumps({
                'id': resp_id, 'object': 'chat.completion',
                'created': int(time.time()), 'model': model,
                'choices': [{'index': 0, 'message': msg,
                             'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 1, 'completion_tokens': 1,
                          'total_tokens': 2},
            }).encode('utf-8')
            self.send_header('content-length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(200)
        self.send_header('content-type', 'text/event-stream')
        self.send_header('cache-control', 'no-cache')
        # HTTP/1.1 无 Content-Length 的 body 以连接关闭为界（SSE read 超时根因）
        self.send_header('connection', 'close')
        self.end_headers()
        for chunk in _sse_chunks(resp_id, model):
            self.wfile.write(b'data: ' + json.dumps(chunk).encode('utf-8') + b'\n\n')
            self.wfile.flush()
        self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()
        self.close_connection = True

    def log_message(self, *a):
        pass


def main():
    srv = ThreadingHTTPServer(('127.0.0.1', PORT), H)
    print(f'[scopeprobe-stub] listening :{PORT}, log → {LOG_PATH}', flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
