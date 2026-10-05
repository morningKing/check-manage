"""outbox 投递目标的假实现：可控行为（ok/503/超时挂起），记录收到的投递。
HTTPServer 单线程足够（outbox 并发投递来自后端线程，收包即 204）。"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def start(port: int = 3098, behavior: str = 'ok'):
    state = {'deliveries': 0, 'bodies': [], 'idem_keys': [], 'behavior': behavior}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get('Content-Length') or 0)
            body = self.rfile.read(length)
            state['deliveries'] += 1
            state['bodies'].append(body.decode('utf-8', 'replace'))
            state['idem_keys'].append(
                self.headers.get('X-Idempotency-Key')
                or self.headers.get('Idempotency-Key') or '')
            if state['behavior'] == '503':
                self.send_response(503); self.end_headers()
            elif state['behavior'] == 'timeout':
                threading.Event().wait(65)   # 超过 outbox 客户端超时
                try:
                    self.send_response(204); self.end_headers()
                except Exception:
                    pass
            else:
                self.send_response(204); self.end_headers()

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(('127.0.0.1', port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, state


def shutdown(srv):
    srv.shutdown()
