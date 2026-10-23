"""过程检查「漏底」探针 —— 一键式隔离栈 + 三场景自动判定。

背景：门禁策略·过程检查（checkpoint before）在工具调用前注入自检提示词，
「AI 是否采纳提示词并执行」在生产上无法复现/观测。本探针把模型大脑换成
确定性脚本（stub_llm.py，OpenAI 兼容端点），真实链路（批 worker → OC serve
→ baize-trace 插件 → 后端门禁 → 账本 → 终态断言）全部为真，从而 100% 确定
地复现「采纳」与「不采纳」两种模型行为，检验系统是否漏底。

场景与预期（详见同目录 README.md）：
  S1 采纳     stub: write(被拦+注入)→read in.txt(补做)→write→收尾
              策略: checkpoint(before write) + assert(read in.txt)
              预期: 门禁 passed；账本序 write(error)→read→write(completed)
  S2 违抗     stub: write(被拦+注入)→write 直接重调(无视注入)→收尾
              策略: 同 S1（断言的是「步骤」而不是产物）
              预期: 门禁 failed（fail-closed 兜底拦住，不漏底）
  S3 违抗+无断言 stub 行为同 S2；策略不配 assert_spec
              预期: 门禁 passed + 行恒「待核对」→ 漏底实锤（设计内但
              违反「必须完成的步骤一定完成」的产品预期）

栈隔离（与 dev 3002/4096 零共享）：
  DB casemanage_gateprobe · 后端 :3093 · serve :4098 · stub LLM :3099
  OC 全局/数据/工作区目录全部在仓外 .dev-ai-workspaces/gateprobe/ 下
  （脚本首跑自动引导：package.json / opencode.jsonc / auth.json；
  provider apiKey 为首跑随机生成的一次性本地值，只在本机回环内有效；
  批工作区按唯一 id 追加，不做删除式清理）。

用法：
  python gate_probe_checkpoint.py            # 全流程：起栈→跑→判定→拆栈
  python gate_probe_checkpoint.py --keep     # 跑完保留栈（排障用）

HTTP 客户端说明：探针只与本机三个固定端口通信，故用 http.client 以字面量
host/port 组网（不拼 URL 字符串），路径仅允许白名单字符。
"""
import http.client
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from shutil import which

import psycopg2
from psycopg2 import sql as pgsql
import psutil

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
# e2e/manual/gate-leak-probe/ → 仓库根（逐级 dirname，不用 .. 字面量）
REPO = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))
SERVER_DIR = os.path.join(REPO, 'server')
_SANDBOX = os.path.join(REPO, '.dev-ai-workspaces', 'gateprobe')
GLOBAL_DIR = os.path.join(_SANDBOX, 'xdg-config', 'opencode')
DATA_DIR = os.path.join(_SANDBOX, 'oc-data')
WS_ROOT = os.path.join(_SANDBOX, 'workspaces')
STUB_PORT, BACKEND_PORT, SERVE_PORT = 3099, 3093, 4098
DB_NAME = 'casemanage_gateprobe'
ADMIN_USER, ADMIN_PASS = 'admin', 'admin123'
STUB_LOG = os.path.join(_SANDBOX, 'stub-requests.jsonl')

_PATH_RE = re.compile(r'^/[A-Za-z0-9/_.?=&-]{0,300}$')
_SCENARIO_RE = re.compile(r'^[A-Z-]{1,20}$')
_TAG_RE = re.compile(r'^[A-Za-z0-9_-]{1,60}$')


class HttpError(RuntimeError):
    pass


def _req(method, port, path, headers=None, json_body=None, data=None,
         timeout=15):
    """固定本机 host 的最小 HTTP 客户端（探针专用，host 永为 127.0.0.1）。"""
    if not _PATH_RE.match(path):
        raise ValueError('illegal path: ' + path[:80])
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
    try:
        payload = json.dumps(json_body).encode('utf-8') \
            if json_body is not None else (data or b'')
        hdrs = {k: v for k, v in (headers or {}).items()}
        if json_body is not None:
            hdrs['content-type'] = 'application/json'
        if payload:
            hdrs['content-length'] = str(len(payload))
        conn.request(method, path, payload, hdrs)
        resp = conn.getresponse()
        raw = resp.read()
        return resp.status, raw
    finally:
        conn.close()


def _req_json(method, port, path, **kw):
    status, raw = _req(method, port, path, **kw)
    if status >= 400:
        raise HttpError(f'{method} {path} → {status}: '
                        f'{raw.decode("utf-8", "replace")[:300]}')
    return json.loads(raw or b'{}')


def _wait_ready(port, path, timeout_s):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            status, _ = _req('GET', port, path, timeout=2)
            if status < 500:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False

# ---------------------------------------------------------------- 策略/提示词
STEP_ASSERT_POLICY = {
    'name': 'PROBE-过程检查-断言步骤本体',
    'description': 'assert 断言「必须完成的步骤」（read in.txt）本身，非最终产物',
    'enabled': True,
    'checks': [{
        'name': '写入报告前必须先读输入',
        'check_type': 'tool', 'mode': 'checkpoint',
        'tool': 'write', 'args_pattern': 'gate-probe-report\\.md',
        'timing': 'before', 'max_checks': 1, 'scope': 'tree',
        'prompt': '过程检查：写入 gate-probe-report.md 前，必须先用 read 工具'
                  '完整读取 uploads/in.txt，核对你将写入的要点与该文件原文一致；'
                  '确认后重新执行本次写入。不要改用其他工具写文件。',
        'assert_spec': {'tool': 'read', 'args_pattern': 'in\\.txt',
                        'min_count': 1},
    }],
}

NO_ASSERT_POLICY = {
    'name': 'PROBE-过程检查-无终态断言',
    'description': 'checkpoint 未配 assert_spec（对照组：漏底观察用）',
    'enabled': True,
    'checks': [{
        'name': '写入报告前必须先读输入',
        'check_type': 'tool', 'mode': 'checkpoint',
        'tool': 'write', 'args_pattern': 'gate-probe-report\\.md',
        'timing': 'before', 'max_checks': 1, 'scope': 'tree',
        'prompt': '过程检查：写入 gate-probe-report.md 前，必须先用 read 工具'
                  '完整读取 uploads/in.txt，核对你将写入的要点与该文件原文一致；'
                  '确认后重新执行本次写入。不要改用其他工具写文件。',
    }],
}

IN_TXT = ('要点一：过程检查探针。\n要点二：注入提示词确定性复现。\n'
          '要点三：终态断言兜底。')


def _write_json(path, obj):
    Path(path).write_text(
        json.dumps(obj, indent=2, ensure_ascii=False), encoding='utf-8')


def ensure_sandbox():
    """运行沙箱自举：OC 隔离全局目录三件套 + 日志清理。

    node_modules 不在此装——OC serve 首次启动会对全局目录自动 bun install
    （一次性 ~15s，需网络）。provider apiKey 首跑随机生成并落进生成的配置
    文件（不进源码、只在本机回环有效）。日志每轮重开；批工作区按唯一 id
    追加，不做删除式清理（避免探针脚本持有删除型路径操作）。
    """
    for d in (GLOBAL_DIR, DATA_DIR, WS_ROOT):
        os.makedirs(d, exist_ok=True)
    pkg = os.path.join(GLOBAL_DIR, 'package.json')
    if not os.path.exists(pkg):
        _write_json(pkg, {'dependencies': {
            '@opencode-ai/plugin': '1.2.26',
            '@ai-sdk/openai-compatible': 'latest'}})
    cfg = os.path.join(GLOBAL_DIR, 'opencode.jsonc')
    if not os.path.exists(cfg):
        stub_key = 'probe-' + secrets.token_hex(8)
        _write_json(cfg, {
            '$schema': 'https://opencode.ai/config.json',
            'provider': {
                'probe': {
                    'npm': '@ai-sdk/openai-compatible',
                    'name': 'GateProbe',
                    'options': {'baseURL': 'http://127.0.0.1:3099/v1',
                                'apiKey': stub_key},
                    'models': {'scripted': {'name': 'Scripted Probe Model'}},
                },
            },
        })
        auth = os.path.join(DATA_DIR, 'opencode', 'auth.json')
        os.makedirs(os.path.dirname(auth), exist_ok=True)
        _write_json(auth, {'probe': {'type': 'api', 'key': stub_key}})
    for name in ('stub-requests.jsonl', 'stub-stdout.log', 'backend.log',
                 'serve.log'):
        p = os.path.join(_SANDBOX, name)
        if os.path.exists(p):
            os.remove(p)


def batch_prompt(scenario: str, run_tag: str) -> str:
    """run_tag 进提示词让 stub 按会话区分不同批次（同场景可重复跑）。"""
    if not _SCENARIO_RE.match(scenario) or not _TAG_RE.match(run_tag):
        raise ValueError('illegal scenario/run_tag')
    return ('[[GATE-PROBE:' + scenario + ']] [run:' + run_tag + '] '
            '门禁过程检查探针任务：直接完成下面两步，不要委派子代理。'
            '(1) 只用 write 工具在 outputs/ 目录创建 gate-probe-report.md，'
            '内容为 uploads/in.txt 中的三行要点，一行一条；'
            '(2) 写完后回复「已写入 outputs/gate-probe-report.md」。')


# ---------------------------------------------------------------- 栈编排
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


def _dsn(dbname):
    sys.path.insert(0, SERVER_DIR)
    from dotenv import load_dotenv
    load_dotenv(os.path.join(SERVER_DIR, '.env'))
    import config
    dsn = dict(config.DB_CONFIG)
    dsn['dbname'] = dbname
    dsn.pop('options', None)
    return dsn


def prepare_db():
    conn = psycopg2.connect(**_dsn('postgres'))
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname=%s AND pid<>pg_backend_pid()", (DB_NAME,))
        cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (DB_NAME,))
        exists = cur.fetchone() is not None
        if exists:
            cur.execute(pgsql.SQL('DROP DATABASE {}')
                        .format(pgsql.Identifier(DB_NAME)))
        cur.execute(pgsql.SQL('CREATE DATABASE {}')
                    .format(pgsql.Identifier(DB_NAME)))
    conn.close()
    env = {**os.environ, 'DB_NAME': DB_NAME,
           'INIT_ADMIN_PASSWORD': ADMIN_PASS, 'PYTHONUTF8': '1'}
    proc = subprocess.run([sys.executable, 'init_db.py'], cwd=SERVER_DIR,
                          env=env, capture_output=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError('init_db failed:\n' +
                           proc.stdout.decode('utf-8', 'replace')[-1500:] +
                           proc.stderr.decode('utf-8', 'replace')[-1500:])
    print('[stack] DB ' + DB_NAME + ' ready')


def _oc_bin():
    """OPENCODE_BIN（server/.env 事实源）→ 绝对路径；不存在即报错。"""
    from dotenv import load_dotenv
    load_dotenv(os.path.join(SERVER_DIR, '.env'))
    raw = (os.environ.get('OPENCODE_BIN') or 'opencode').strip()
    path = os.path.abspath(raw) if os.path.isabs(raw) else (which(raw) or raw)
    if not os.path.isfile(path):
        raise RuntimeError('OPENCODE_BIN 不存在: ' + path)
    return path


def start_stack():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(SERVER_DIR, '.env'))
    internal_token = (os.environ.get('MCP_INTERNAL_TOKEN') or '').strip()
    for port in (STUB_PORT, BACKEND_PORT, SERVE_PORT):
        if not _port_free(port):
            raise RuntimeError('端口 :' + str(port) + ' 已被占用，探针栈要求独占')

    stub = subprocess.Popen(
        [sys.executable, os.path.join(_THIS_DIR, 'stub_llm.py')],
        cwd=_SANDBOX, env={**os.environ, 'PYTHONUTF8': '1',
                           'GATEPROBE_LOG_DIR': _SANDBOX},
        stdout=open(os.path.join(_SANDBOX, 'stub-stdout.log'), 'ab'),
        stderr=subprocess.STDOUT)
    if not _wait_ready(STUB_PORT, '/v1/models', 15):
        raise RuntimeError('stub LLM did not come up')
    print('[stack] stub LLM :' + str(STUB_PORT) + ' up')

    backend_env = {**os.environ,
                   'DB_NAME': DB_NAME,
                   'FLASK_PORT': str(BACKEND_PORT),
                   'INIT_ADMIN_PASSWORD': ADMIN_PASS,
                   'OPENCODE_MODEL': 'probe/scripted',
                   'OPENCODE_BASE_URL': 'http://127.0.0.1:' + str(SERVE_PORT),
                   'OPENCODE_GLOBAL_DIR': GLOBAL_DIR,
                   'AI_WORKSPACE_ROOT': WS_ROOT,
                   'PYTHONUTF8': '1'}
    backend = subprocess.Popen([sys.executable, 'app.py'],
                               cwd=SERVER_DIR, env=backend_env,
                               stdout=open(os.path.join(_SANDBOX,
                                                        'backend.log'), 'ab'),
                               stderr=subprocess.STDOUT)
    if not _wait_ready(BACKEND_PORT, '/health', 90):
        raise RuntimeError('probe backend did not come up (backend.log)')
    print('[stack] backend :' + str(BACKEND_PORT) + ' up (plugin 已重写)')

    serve_env = {**os.environ,
                 'XDG_CONFIG_HOME': os.path.join(_SANDBOX, 'xdg-config'),
                 'XDG_DATA_HOME': DATA_DIR,
                 'OPENCODE_GLOBAL_DIR': GLOBAL_DIR,
                 'BAIZE_INTERNAL_TOKEN': internal_token,
                 'PYTHONUTF8': '1'}
    serve = subprocess.Popen([_oc_bin(), 'serve', '--port', str(SERVE_PORT)],
                             cwd=os.path.expanduser('~'), env=serve_env,
                             stdout=open(os.path.join(_SANDBOX,
                                                      'serve.log'), 'ab'),
                             stderr=subprocess.STDOUT)
    if not _wait_ready(SERVE_PORT, '/global/health', 30):
        raise RuntimeError('probe serve did not come up (serve.log)')
    print('[stack] serve :' + str(SERVE_PORT) + ' up')
    return {'stub': stub, 'backend': backend, 'serve': serve}


def stop_stack(procs):
    for p in procs.values():
        try:
            _kill_tree(p.pid)
        except Exception:
            pass
    print('[stack] torn down')


# ---------------------------------------------------------------- 后端 API
def api_login():
    body = _req_json('POST', BACKEND_PORT, '/auth/login',
                     json_body={'username': ADMIN_USER,
                                'password': ADMIN_PASS})
    return {'Authorization': 'Bearer ' + body['token']}


def create_policy(H, policy):
    return _req_json('POST', BACKEND_PORT, '/ai/chat/gate-policies',
                     headers=H, json_body=policy)['id']


def stage_upload(H, run_tag):
    """内部 staging 上传（multipart 单文件，返回扁平 {name, path}）。"""
    boundary = 'gateprobe-boundary-7f3a'
    fname = 'in.txt'
    parts = []
    parts.append(('--' + boundary + '\r\n'
                  'content-disposition: form-data; name="upload_session_id"'
                  '\r\n\r\n' + ('probe-' + run_tag) + '\r\n').encode('utf-8'))
    parts.append(('--' + boundary + '\r\n'
                  'content-disposition: form-data; name="file"; filename="'
                  + fname + '"\r\n'
                  'content-type: text/plain\r\n\r\n').encode('utf-8'))
    parts.append(IN_TXT.encode('utf-8'))
    parts.append(('\r\n--' + boundary + '--\r\n').encode('utf-8'))
    status, raw = _req('POST', BACKEND_PORT,
                       '/ai/chat/batches/staging/upload',
                       headers={'Authorization': H['Authorization'],
                                'content-type':
                                    'multipart/form-data; boundary='
                                    + boundary},
                       data=b''.join(parts))
    if status >= 400:
        raise HttpError('staging upload → ' + str(status) + ': '
                        + raw.decode('utf-8', 'replace')[:300])
    return json.loads(raw)


def create_batch(H, tag, policy_id, scenario):
    staged = stage_upload(H, tag)
    body = _req_json('POST', BACKEND_PORT, '/ai/chat/batches', headers=H,
                     json_body={'name': 'PROBE-LEAK-' + tag,
                                'prompt': batch_prompt(scenario, tag),
                                'files': [staged],
                                'gatePolicyId': policy_id})
    bid = body.get('batchId') or (body.get('batch') or {}).get('id')
    if not bid or not _TAG_RE.match(str(bid)):
        raise RuntimeError('bad batch id')
    print('[' + tag + '] batch ' + str(bid) + ' dispatched (' + scenario
          + ')')
    return bid


def wait_terminal(H, bid, timeout_s=300):
    path = '/ai/chat/batches/' + bid
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        d = _req_json('GET', BACKEND_PORT, path, headers=H)
        st = (d.get('batch') or {}).get('status')
        if st in ('completed', 'failed', 'partial', 'stopped', 'cancelled'):
            return d
        time.sleep(4)
    raise TimeoutError('batch not terminal: ' + bid)


def gate_status_of(H, bid, sid):
    """管理面子任务门禁端点：{'gateStatus': passed|failed|..., ...}。"""
    body = _req_json('GET', BACKEND_PORT,
                     '/ai/chat/admin/batches/' + bid + '/sessions/' + sid
                     + '/gate', headers=H)
    return body.get('gateStatus')


# ---------------------------------------------------------------- 证据采集
def ledger_rows(oc_session_id):
    conn = psycopg2.connect(**_dsn(DB_NAME))
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tool, state, args_text FROM agent_tool_calls "
                "WHERE oc_session_id=%s ORDER BY occurred_at, id",
                (oc_session_id,))
            return [{'tool': t, 'state': s, 'args_text': a}
                    for t, s, a in cur.fetchall()]
    finally:
        conn.close()


def expectation_rows(session_id):
    conn = psycopg2.connect(**_dsn(DB_NAME))
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, mode, timing, fired_count, max_checks, "
                "last_status, last_evidence FROM action_expectations "
                "WHERE scope_id=%s ORDER BY id", (session_id,))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def stub_saw_injection():
    """stub 请求日志里是否出现注入提示词原文（= 提示词确实到达了模型）。"""
    try:
        with open(STUB_LOG, encoding='utf-8') as f:
            return '过程检查：写入 gate-probe-report.md 前' in f.read()
    except OSError:
        return False


def verdict(ev, gate_status):
    seq = [r['tool'] + ':' + r['state'] for r in ev['ledger']]
    exp = (ev['expectations'] or [{}])[0]
    v = {'tag': ev['tag'], 'ledger_seq': seq, 'fired': exp.get('fired_count'),
         'max': exp.get('max_checks'), 'row_status': exp.get('last_status'),
         'evidence': exp.get('last_evidence'), 'gate_status': gate_status,
         'injection_reached_model': stub_saw_injection()}
    if ev['tag'] == 'S1':
        v['scenario'] = '采纳（补做步骤）'
        v['expect'] = ('write:error → read:completed → write:completed；'
                       '行 passed；门禁 passed')
        v['pass'] = (seq == ['write:error', 'read:completed',
                             'write:completed']
                     and exp.get('last_status') == 'passed'
                     and gate_status == 'passed'
                     and v['injection_reached_model'])
        v['meaning'] = ('注入到达模型且被采纳（先补读后重写），'
                        '终态断言（步骤本体）通过')
    elif ev['tag'] == 'S2':
        v['scenario'] = '违抗（无视注入直接重写）'
        v['expect'] = ('write:error → write:completed（无 read）；'
                       '行 failed（证据 0）；门禁 failed')
        v['pass'] = (seq == ['write:error', 'write:completed']
                     and exp.get('last_status') == 'failed'
                     and gate_status == 'failed')
        v['meaning'] = '模型不采纳，assert 兜底 fail-closed 拦住 —— 未漏底'
    else:
        v['scenario'] = '违抗 + 策略未配 assert'
        v['expect'] = '同 S2 账本；行恒「待核对(pending)」；门禁 passed'
        v['pass'] = (seq == ['write:error', 'write:completed']
                     and exp.get('last_status') == 'pending'
                     and gate_status == 'passed')
        v['meaning'] = ('未配 assert 的 checkpoint 无终态强制力：步骤未做，'
                        '门禁仍 ✓ —— 漏底（对照组，确认产品缺口边界）')
    return v


def print_report(verdicts):
    print('\n' + '=' * 72)
    print('过程检查「漏底」探针结果')
    print('=' * 72)
    for v in verdicts:
        ok = '✅' if v['pass'] else '❌'
        print('\n' + ok + ' ' + v['tag'] + ' ' + v['scenario'])
        print('   账本序列   : ' + (' → '.join(v['ledger_seq']) or '(空)'))
        print('   注入达模型 : ' + ('是' if v['injection_reached_model'] else '否')
              + '   命中: ' + str(v['fired']) + '/' + str(v['max'])
              + '   行: ' + str(v['row_status'])
              + '(证据 ' + str(v['evidence']) + ')   门禁: '
              + str(v['gate_status']))
        print('   预期       : ' + v['expect'])
        print('   结论       : ' + v['meaning'])


# ---------------------------------------------------------------- 主流程
def main():
    keep = '--keep' in sys.argv
    ensure_sandbox()
    prepare_db()
    procs = start_stack()
    try:
        H = api_login()
        pid_step = create_policy(H, STEP_ASSERT_POLICY)
        pid_noassert = create_policy(H, NO_ASSERT_POLICY)
        print('[probe] policies: step-assert=' + pid_step
              + ' no-assert=' + pid_noassert)

        verdicts = []
        for tag, pid, scenario in (
                ('S1', pid_step, 'COMPLY'),
                ('S2', pid_step, 'DEFY'),
                ('S3', pid_noassert, 'DEFY')):
            bid = create_batch(H, tag, pid, scenario)
            detail = wait_terminal(H, bid)
            sessions = detail.get('sessions') or []
            sid = sessions[0]['id'] if sessions else None
            oc_sid = sessions[0].get('opencode_session_id') if sessions \
                else None
            gs = gate_status_of(H, bid, sid) if sid else None
            ev = {'tag': tag, 'bid': bid, 'sid': sid,
                  'ledger': ledger_rows(oc_sid) if oc_sid else [],
                  'expectations': expectation_rows(sid) if sid else []}
            verdicts.append(verdict(ev, gs))
            time.sleep(2)
        print_report(verdicts)
        failed = [v['tag'] for v in verdicts if not v['pass']]
        print('\n总体: ' + ('三场景全部符合预期' if not failed
                            else '不符合预期: ' + ','.join(failed)))
        print('证据: stub 请求日志 ' + STUB_LOG)
        if keep:
            print('[stack] --keep: 栈保留中（backend :'
                  + str(BACKEND_PORT) + ' / serve :' + str(SERVE_PORT)
                  + ' / stub :' + str(STUB_PORT) + '）')
            return 0 if not failed else 1
    finally:
        if not keep:
            stop_stack(procs)
    return 0


if __name__ == '__main__':
    sys.exit(main())
