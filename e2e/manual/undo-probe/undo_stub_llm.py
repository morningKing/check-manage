"""UndoProbe stub LLM —— 脚本化 OpenAI 兼容端点（undo-probe E2E 专用，一次性）。

无状态：按「最后一条 user 消息」里的标记 [[UNDO-PROBE:<TAG>]] 选脚本，
按该 user 消息之后的 tool 结果条数决定走到脚本第几步。每个请求全文
（模型视角的完整对话）落 stub-requests.jsonl —— 「撤销后模型还看不看得到
被撤销的对话」在这里取证。

注意：T3 请求的标记提取取 user 文本里的**第一个** UNDO-PROBE 标记；rebuild
场景的历史重放块会把被保留轮次（T1）的标记带进同一条 user 文本，导致该
请求被归类为 T1 —— 编排器（undo_e2e.py）取证时因此不依赖 tag=T3 过滤，
而是按「user 文本同时含 T3 标记与此前对话摘要头」定位重放证据。
"""
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get('UNDOPROBE_STUB_PORT', '3197'))
LOG_PATH = Path(os.environ.get('UNDOPROBE_LOG_DIR') or Path(__file__).parent) \
    / 'stub-requests.jsonl'

W = 'write'
SCRIPTS = {
    # T1：建 a.txt=v1
    'T1': [(W, {'filePath': 'a.txt', 'content': 'v1'})],
    # T2：改 a.txt=v2、新建 b.txt、bash 建 c.txt、写被 .gitignore 忽略的 outputs/o.txt
    'T2': [(W, {'filePath': 'a.txt', 'content': 'v2'}),
           (W, {'filePath': 'b.txt', 'content': 'b-new'}),
           ('bash', {'command': 'echo c1 > c.txt', 'description': 'write c.txt'}),
           (W, {'filePath': 'outputs/o.txt', 'content': 'out-v1'})],
    # T3：纯文本（用来看撤销后的上下文）
    'T3': [],
    # SEED：场景 C 换绑 OC 会话后种的首条 user 消息（只为让 OC 会话非空，
    # 触发「上下文在别的 OC 会话里」的 rebuild 判定）；纯文本不应答工具
    'SEED': [],
    # SLOW：先睡再答（busy 期间调 revert 用）
    'SLOW': [],
}
SLOW_SECONDS = 8
_lock = threading.Lock()


def _text_of(content):
    if isinstance(content, list):
        return ' '.join(p.get('text', '') for p in content if isinstance(p, dict))
    return content or ''


def _plan(body):
    """返回 (tag, step, action)。action: ('tool', name, args) | ('final', text)。"""
    messages = body.get('messages') or []
    last_user = -1
    for i, m in enumerate(messages):
        if m.get('role') == 'user':
            last_user = i
    if last_user < 0 or not body.get('tools'):
        return None, 0, ('final', 'DONE')          # 标题生成等旁路请求
    m = re.search(r'\[\[UNDO-PROBE:([A-Z0-9-]+)\]\]', _text_of(messages[last_user].get('content')))
    tag = m.group(1) if m else None
    if tag not in SCRIPTS:
        return tag, 0, ('final', 'DONE')
    step = sum(1 for x in messages[last_user + 1:] if x.get('role') == 'tool')
    script = SCRIPTS[tag]
    if step < len(script):
        name, args = script[step]
        return tag, step, ('tool', name, args)
    return tag, step, ('final', f'{tag} done')


def _log(tag, step, action, body):
    entry = {'ts': time.strftime('%H:%M:%S'), 'tag': tag, 'step': step,
             'action': action[0] + ':' + str(action[1])[:40], 'messages': []}
    for m in body.get('messages') or []:
        role = m.get('role')
        item = {'role': role}
        if role == 'tool':
            item['tool_result'] = _text_of(m.get('content'))[:160]
        elif role == 'assistant':
            tcs = m.get('tool_calls') or []
            if tcs:
                item['tool_calls'] = [
                    t.get('function', {}).get('name', '') + ' '
                    + t.get('function', {}).get('arguments', '')[:80] for t in tcs]
            if m.get('content'):
                item['text'] = _text_of(m.get('content'))[:120]
        elif role == 'user':
            item['text'] = _text_of(m.get('content'))[:2000]
        else:
            item['len'] = len(_text_of(m.get('content')))
        entry['messages'].append(item)
    with _lock:
        with LOG_PATH.open('a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def _chunks(resp_id, model, action):
    base = {'id': resp_id, 'object': 'chat.completion.chunk',
            'created': int(time.time()), 'model': model}
    deltas, finish = [], 'stop'
    if action[0] == 'tool':
        _, name, args = action
        call_id = 'call_' + str(int(time.time() * 1000))
        deltas.append({'role': 'assistant', 'content': None, 'tool_calls': [
            {'index': 0, 'id': call_id, 'type': 'function',
             'function': {'name': name, 'arguments': ''}}]})
        deltas.append({'tool_calls': [
            {'index': 0, 'function': {'arguments': json.dumps(args)}}]})
        finish = 'tool_calls'
    else:
        deltas.append({'role': 'assistant', 'content': action[1]})
    out = [{**base, 'choices': [{'index': 0, 'delta': d, 'finish_reason': None}]}
           for d in deltas]
    out.append({**base, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}})
    return out


class H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def _json(self, obj, status=200):
        data = json.dumps(obj).encode('utf-8')
        self.send_response(status)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip('/').endswith('/models'):
            self._json({'object': 'list', 'data': [
                {'id': 'scripted', 'object': 'model', 'owned_by': 'undoprobe'}]})
            return
        self._json({'error': 'not found'}, 404)

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = json.loads(self.rfile.read(length) or b'{}')
        if not self.path.rstrip('/').endswith('/chat/completions'):
            self._json({'error': 'not found'}, 404)
            return
        tag, step, action = _plan(body)
        _log(tag, step, action, body)
        print(f'[undo-stub] tag={tag} step={step} action={action[0]} '
              f'messages={len(body.get("messages") or [])}', flush=True)
        if tag == 'SLOW':
            time.sleep(SLOW_SECONDS)
        model = body.get('model') or 'scripted'
        resp_id = 'chatcmpl-' + str(int(time.time() * 1000))
        if body.get('stream'):
            self.send_response(200)
            self.send_header('content-type', 'text/event-stream')
            self.send_header('cache-control', 'no-cache')
            self.send_header('connection', 'close')   # 无 Content-Length：以断连为界
            self.end_headers()
            for chunk in _chunks(resp_id, model, action):
                self.wfile.write(b'data: ' + json.dumps(chunk).encode('utf-8') + b'\n\n')
                self.wfile.flush()
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
            self.close_connection = True
            return
        msg = {'role': 'assistant', 'content': None}
        finish = 'stop'
        if action[0] == 'tool':
            msg['tool_calls'] = [{'id': 'call_' + str(int(time.time() * 1000)),
                                  'type': 'function',
                                  'function': {'name': action[1],
                                               'arguments': json.dumps(action[2])}}]
            finish = 'tool_calls'
        else:
            msg['content'] = action[1]
        self._json({'id': resp_id, 'object': 'chat.completion',
                    'created': int(time.time()), 'model': model,
                    'choices': [{'index': 0, 'message': msg, 'finish_reason': finish}],
                    'usage': {'prompt_tokens': 10, 'completion_tokens': 5,
                              'total_tokens': 15}})

    def log_message(self, *a):
        pass


if __name__ == '__main__':
    srv = ThreadingHTTPServer(('127.0.0.1', PORT), H)
    print(f'[undo-stub] listening :{PORT}, log -> {LOG_PATH}', flush=True)
    srv.serve_forever()
