"""OC 插件作用域探针 —— 平台插件不得影响非平台会话（真链路版）。

验证 utils/oc_plugin_scope.py 的作用域修复在**真实 OpenCode serve**里成立：
三个平台插件（baize-trace / baize-tool-timeout / baize-subagent-reuse）随全局
目录加载，但只对平台工作区根之下的目录实例生效。

场景（隔离栈，0 LLM——模型大脑是脚本化 stub，其余链路全真）：
  A. 平台目录会话（probe-workspaces/user-admin/s1 之下）跑一次工具调用
     → sink 必须收到该会话的 pre-check / post-check / runtime-events 请求
  B. 非平台目录会话（probe-other/project 之下）跑同样的工具调用
     → sink 对该会话的请求增量必须为 0（插件完全惰性）

栈（全部隔离，与 dev :3002/:4096 零共享）：DB 不需要（不走批 worker，直接
驱动 OC serve 的 message API）；stub LLM :4199；OC serve :4198；sink 随机
端口。OC 全局目录/数据目录全在仓外 .dev-ai-workspaces/pluginscope/ 下。

运行：python plugin_scope_probe.py [--keep]
前置：server/.env 可用（OPENCODE_BIN）；首次跑 OC serve 会对隔离全局目录
自动装依赖（bun install ~15s，需网络）。
"""
import http.client
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from shutil import which

import psutil

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))
SERVER_DIR = os.path.join(REPO, 'server')
_SANDBOX = os.path.join(REPO, '.dev-ai-workspaces', 'pluginscope')
XDG_CONFIG = os.path.join(_SANDBOX, 'xdg-config')
OC_GLOBAL = os.path.join(XDG_CONFIG, 'opencode')
DATA_DIR = os.path.join(_SANDBOX, 'oc-data')
WS_ROOT = os.path.join(_SANDBOX, 'probe-workspaces')
OTHER_ROOT = os.path.join(_SANDBOX, 'probe-other')
STUB_PORT, SERVE_PORT = 4199, 4198
STUB_KEY = 'probe-' + secrets.token_hex(8)

sys.path.insert(0, SERVER_DIR)


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Path(path).write_text(
        json.dumps(obj, indent=2, ensure_ascii=False), encoding='utf-8')


def _oc_bin():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(SERVER_DIR, '.env'))
    raw = (os.environ.get('OPENCODE_BIN') or 'opencode').strip()
    path = os.path.abspath(raw) if os.path.isabs(raw) else (which(raw) or raw)
    if not os.path.isfile(path):
        raise RuntimeError('OPENCODE_BIN 不存在: ' + path)
    return path


def _port_free(port):
    s = socket.socket()
    s.settimeout(1)
    try:
        s.connect(('127.0.0.1', port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def _kill_tree(pid):
    try:
        for c in psutil.Process(pid).children(recursive=True):
            c.kill()
        psutil.Process(pid).kill()
    except psutil.NoSuchProcess:
        pass
    except Exception:
        pass


# ------------------------------------------------------------------ sink
class Sink:
    """记录收到的每个请求路径的本地 HTTP 服务（顶替平台后端内部端点）。"""

    def __init__(self):
        self.hits: list[str] = []
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def do_POST(self):
                length = int(self.headers.get('Content-Length') or 0)
                self.rfile.read(length)
                outer.hits.append(self.path)
                body = b'{"rules":0,"allow":true,"inject":false,"enabled":false}'
                self.send_response(200)
                self.send_header('content-type', 'application/json')
                self.send_header('content-length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.port = self.srv.server_address[1]
        self.srv_thread = None

    def start(self):
        import threading
        self.srv_thread = threading.Thread(target=self.srv.serve_forever,
                                           daemon=True)
        self.srv_thread.start()

    def stop(self):
        self.srv.shutdown()

    def hits_for(self, session_path_fragment: str) -> int:
        return sum(1 for h in self.hits if session_path_fragment in h)


# ------------------------------------------------------- OC 驱动（V1 API）
def _oc_req(method, path, json_body=None, timeout=30):
    conn = http.client.HTTPConnection('127.0.0.1', SERVE_PORT, timeout=timeout)
    try:
        payload = json.dumps(json_body).encode('utf-8') \
            if json_body is not None else None
        hdrs = {'content-type': 'application/json'}
        if payload:
            hdrs['content-length'] = str(len(payload))
        conn.request(method, path, payload, hdrs)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def run_turn(directory: str, prompt: str, timeout_s: int = 120):
    fwd = directory.replace(os.sep, '/')
    # 新目录的首次实例创建会触发插件依赖在线安装 + models.dev 拉取，可能远超
    # 常规耗时——重试直到成功（探针超时 90s/次，最多 3 次）。
    sid = None
    for attempt in range(3):
        try:
            status, raw = _oc_req('POST', f'/session?directory={fwd}', {},
                                  timeout=90)
            if status < 400:
                sid = json.loads(raw)['id']
                break
            raise RuntimeError(f'{status} {raw[:200]}')
        except (TimeoutError, RuntimeError) as e:
            print(f'[probe] create session attempt {attempt + 1} failed: '
                  f'{str(e)[:120]}')
            time.sleep(3)
    if not sid:
        raise RuntimeError('create session kept timing out')
    # V1 派发端点是 /prompt_async（平台 send_prompt_async 同款）：body 带
    # model:{providerID,modelID}，directory 走 query 参数（工具 cwd=工作区）。
    fwd = directory.replace(os.sep, '/')
    _oc_req('POST', f'/session/{sid}/prompt_async?directory={fwd}', {
        'parts': [{'type': 'text', 'text': prompt}],
        'model': {'providerID': 'probe', 'modelID': 'scripted'},
    }, timeout=timeout_s)
    # stub 会在工具结果后收尾（role=tool 出现 → finish stop）——给足时间
    time.sleep(timeout_s // 4)
    time.sleep(3)          # 插件的 after 钩子/fetch 有毫秒级尾巴
    return sid


# ------------------------------------------------------------------ 主流程
def main():
    keep = '--keep' in sys.argv
    from dotenv import load_dotenv
    load_dotenv(os.path.join(SERVER_DIR, '.env'))
    internal_token = (os.environ.get('MCP_INTERNAL_TOKEN')
                      or 'probe-' + secrets.token_hex(8))
    for port in (STUB_PORT, SERVE_PORT):
        if not _port_free(port):
            raise RuntimeError(f'端口 :{port} 已被占用，探针要求独占')

    # 1) 渲染三个新插件进隔离全局目录（作用域根 = probe-workspaces）
    os.makedirs(OC_GLOBAL, exist_ok=True)
    _write_json(os.path.join(OC_GLOBAL, 'package.json'), {'dependencies': {
        '@opencode-ai/plugin': '1.2.26',
        '@ai-sdk/openai-compatible': 'latest'}})
    _write_json(os.path.join(OC_GLOBAL, 'opencode.jsonc'), {
        '$schema': 'https://opencode.ai/config.json',
        'provider': {
            'probe': {
                'npm': '@ai-sdk/openai-compatible',
                'name': 'ScopeProbe',
                'options': {'baseURL': f'http://127.0.0.1:{STUB_PORT}/v1',
                            'apiKey': STUB_KEY},
                'models': {'scripted': {'name': 'Scripted Probe Model'}},
            },
        },
    })
    _write_json(os.path.join(DATA_DIR, 'opencode', 'auth.json'),
                {'probe': {'type': 'api', 'key': STUB_KEY}})

    from utils.oc_plugin_scope import platform_workspace_roots
    sys.path.insert(0, os.path.join(REPO, 'server'))
    roots = [WS_ROOT]          # 只把探针工作区根当平台根——隔离断言更锐利
    from utils.tool_timeout_plugin import ensure_tool_timeout_plugin
    from utils.subagent_reuse_plugin import ensure_subagent_reuse_plugin
    from utils.skillopt import ensure_runtime_plugin
    sink_port_holder = {'port': 0}

    sink = Sink()

    def _deploy():
        ep = f'http://127.0.0.1:{sink_port_holder["port"]}/ai/memory/internal/runtime-events'
        ensure_tool_timeout_plugin(OC_GLOBAL, workspace_roots=roots)
        ensure_subagent_reuse_plugin(
            OC_GLOBAL, f'http://127.0.0.1:{sink_port_holder["port"]}/ai/subagent-internal',
            internal_token, workspace_roots=roots)
        ensure_runtime_plugin(
            OC_GLOBAL, ep, internal_token,
            gate_endpoint=f'http://127.0.0.1:{sink_port_holder["port"]}/ai/gate/internal/pre-check',
            gate_post_endpoint=f'http://127.0.0.1:{sink_port_holder["port"]}/ai/gate/internal/post-check',
            workspace_roots=roots)

    # 2) stub LLM（复用 gate-leak-probe 的脚本化端点：bash echo × 1 → 收尾）
    stub = subprocess.Popen(
        [sys.executable, os.path.join(_THIS_DIR, 'stub_llm.py')],
        cwd=_SANDBOX,
        env={**os.environ, 'PYTHONUTF8': '1',
             'GATEPROBE_STUB_PORT': str(STUB_PORT),
             'GATEPROBE_LOG_DIR': _SANDBOX},
        stdout=open(os.path.join(_SANDBOX, 'stub-stdout.log'), 'ab'),
        stderr=subprocess.STDOUT)
    deadline = time.time() + 15
    up = False
    while time.time() < deadline:
        try:
            conn = http.client.HTTPConnection('127.0.0.1', STUB_PORT, timeout=2)
            conn.request('GET', '/v1/models')
            up = conn.getresponse().status < 500
            conn.close()
        except Exception:
            pass
        if up:
            break
        time.sleep(1)
    if not up:
        stub.kill()
        raise RuntimeError('stub LLM did not come up')
    print('[stack] stub LLM :' + str(STUB_PORT) + ' up')

    # 3) sink 起来后部署插件（端点指向 sink），再拉 OC serve
    sink.start()
    sink_port_holder['port'] = sink.port
    _deploy()

    for d in (os.path.join(WS_ROOT, 'user-admin', 's1'), OTHER_ROOT):
        os.makedirs(d, exist_ok=True)

    serve = subprocess.Popen(
        [_oc_bin(), 'serve', '--port', str(SERVE_PORT)],
        cwd=os.path.expanduser('~'),
        env={**os.environ,
             'XDG_CONFIG_HOME': XDG_CONFIG,
             'XDG_DATA_HOME': DATA_DIR,
             'BAIZE_INTERNAL_TOKEN': internal_token,
             'PYTHONUTF8': '1'},
        stdout=open(os.path.join(_SANDBOX, 'serve.log'), 'ab'),
        stderr=subprocess.STDOUT)

    verdict = {}
    try:
        deadline = time.time() + 60
        up = False
        while time.time() < deadline:
            try:
                conn = http.client.HTTPConnection('127.0.0.1', SERVE_PORT, timeout=2)
                conn.request('GET', '/global/health')
                up = conn.getresponse().status < 500
                conn.close()
            except Exception:
                pass
            if up:
                break
            time.sleep(1)
        if not up:
            raise RuntimeError('probe serve did not come up (serve.log)')
        print('[stack] serve :' + str(SERVE_PORT) + ' up')
        time.sleep(2)

        prompt = ('[[SCOPE-PROBE]] 探针任务：用 bash 工具执行 `echo scope-probe`，'
                  '然后直接回复 done。不要委派子代理。')
        before = len(sink.hits)
        platform_sid = run_turn(os.path.join(WS_ROOT, 'user-admin', 's1'), prompt)
        platform_hits = sink.hits[before:]
        verdict['A 平台目录：工具回合打到后端（pre/post/事件 >0）'] = len(platform_hits) > 0
        verdict[f'A 平台目录明细（{len(platform_hits)} 个请求）'] = ' | '.join(platform_hits[:6])

        before = len(sink.hits)
        other_sid = run_turn(OTHER_ROOT, prompt)
        other_hits = sink.hits[before:]
        verdict['B 非平台目录：整个回合 0 个插件请求'] = len(other_hits) == 0
        if other_hits:
            verdict['B 违例请求明细'] = ' | '.join(other_hits[:6])

        ok = verdict['A 平台目录：工具回合打到后端（pre/post/事件 >0）'] \
            and verdict['B 非平台目录：整个回合 0 个插件请求']
        print('\n===== 判定 =====')
        for k, v in verdict.items():
            mark = '✅' if v else '❌'
            print(f'{mark} {k}: {v}')
        print('平台会话 id:', platform_sid, '| 非平台会话 id:', other_sid)
        print('结论：', 'PASS' if ok else 'FAIL')
        return 0 if ok else 1
    finally:
        if not keep:
            _kill_tree(serve.pid)
            sink.stop()
            stub.kill()
            print('[stack] torn down')
        else:
            print('[stack] kept alive（serve :' + str(SERVE_PORT) + ' / stub :'
                  + str(STUB_PORT) + '）——排查后手工清理')


if __name__ == '__main__':
    sys.exit(main())
