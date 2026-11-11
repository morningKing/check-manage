"""AI 会话撤销/重做 0-LLM 真实链路 E2E（undo-redo spec §3 断言三件套）。

隔离栈（与 dev 栈 3002/4096 零共享）：
  stub LLM :3197 + oc serve :4198 + 平台后端 :3094 + DB casemanage_undoprobe
  OC 全局/数据/工作区目录全部在仓内 .dev-ai-workspaces/undo-e2e/（被根
  .gitignore 覆盖）下，每轮独立时间戳子目录，不做删除式清理。

四场景：
  A 原生撤销：preview → undo → 文件物理还原 → 双 SSE 连接收 session.revert
    → T3 请求上下文真回退（含 T1、不含 T2）→ 发送提交后 revert 清空
    → 提交后 redo 409 NOTHING_TO_REDO
  B 重做全恢复：undo → redo → 文件/消息窗口恢复、revert 清空
  C 锚失效 → rebuild 降级 + 历史重放（直接改库模拟「会话恢复换绑过 OC」，
    探针特权，生产路径无此操作）
  D 批子会话 409（执行所有权门禁：非终态批子会话不开放手工撤销）

运行：python e2e/manual/undo-probe/undo_e2e.py [--keep]
排障：失败时看 SB/backend.log / serve.log / stub.log，详见同目录 README.md。
"""
import http.client
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

REPO = Path(__file__).resolve().parents[3]
SERVER_DIR = REPO / 'server'
SB = REPO / '.dev-ai-workspaces' / 'undo-e2e' / (
    time.strftime('%H%M%S') + '-' + secrets.token_hex(3))
GP_OC = REPO / '.dev-ai-workspaces' / 'gateprobe' / 'xdg-config' / 'opencode'
STUB_PORT, SERVE_PORT, BACKEND_PORT = 3197, 4198, 3094
DB_NAME = 'casemanage_undoprobe'
ADMIN_USER, ADMIN_PASS = 'admin', 'admin123'
OC_EXE_FALLBACK = (r'C:\Users\admin\AppData\Roaming\npm\node_modules\opencode-ai'
                   r'\bin\opencode.exe')
PROBE_TOKEN = os.environ.get('UNDOPROBE_TOKEN') or secrets.token_hex(12)
PROCS = []
_ev_lock = threading.Lock()
_results = []

TOKEN = ''


# ---------------------------------------------------------------- HTTP 基元
def _req(port, method, path, body=None, headers=None, timeout=40):
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
    try:
        payload = json.dumps(body).encode() if body is not None else None
        h = {k: v for k, v in (headers or {}).items()}
        if payload:
            h['content-type'] = 'application/json'
            h['content-length'] = str(len(payload))
        conn.request(method, path, payload, h)
        r = conn.getresponse()
        raw = r.read()
        try:
            return r.status, json.loads(raw or b'null')
        except json.JSONDecodeError:
            return r.status, raw
    finally:
        conn.close()


def check(label, cond, detail=''):
    with _ev_lock:
        _results.append(bool(cond))
    print(f'  {"✓" if cond else "✗ FAIL"} {label}' + (f'  ({detail})' if detail else ''))


def _wait(port, path, timeout_s):
    dl = time.time() + timeout_s
    while time.time() < dl:
        try:
            if _req(port, 'GET', path, timeout=2)[0] < 500:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def _api(method, path, body=None, timeout=60):
    """平台后端 API。蓝图无 /api 前缀（gate-leak-probe 同款直连方式）。"""
    return _req(BACKEND_PORT, method, path, body,
                headers={'Authorization': f'Bearer {TOKEN}'}, timeout=timeout)


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


# ---------------------------------------------------------------- DB（探针特权）
def _dsn(dbname):
    """server/.env + server/config.py 的 DB_CONFIG，dbname 按需覆盖。"""
    sys.path.insert(0, str(SERVER_DIR))
    from dotenv import load_dotenv
    load_dotenv(SERVER_DIR / '.env')
    import config
    dsn = dict(config.DB_CONFIG)
    dsn['dbname'] = dbname
    return dsn


def prepare_db():
    """每轮全新隔离库：drop + create + init_db（含 undo-redo 三张新表迁移）。"""
    import psycopg2
    from psycopg2 import sql as pgsql
    conn = psycopg2.connect(**_dsn('postgres'))
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname=%s AND pid<>pg_backend_pid()", (DB_NAME,))
        cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (DB_NAME,))
        if cur.fetchone() is not None:
            cur.execute(pgsql.SQL('DROP DATABASE {}').format(pgsql.Identifier(DB_NAME)))
        cur.execute(pgsql.SQL('CREATE DATABASE {}').format(pgsql.Identifier(DB_NAME)))
    conn.close()
    env = {**os.environ, 'DB_NAME': DB_NAME,
           'INIT_ADMIN_PASSWORD': ADMIN_PASS, 'PYTHONUTF8': '1'}
    proc = subprocess.run([sys.executable, 'init_db.py'], cwd=str(SERVER_DIR),
                          env=env, capture_output=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError('init_db failed:\n' +
                           proc.stdout.decode('utf-8', 'replace')[-1500:] +
                           proc.stderr.decode('utf-8', 'replace')[-1500:])
    print('[stack] DB ' + DB_NAME + ' ready')


def _oc_bin():
    """OPENCODE_BIN（server/.env 事实源）→ 绝对路径；缺失回退 brief npm 路径。"""
    from dotenv import load_dotenv
    load_dotenv(SERVER_DIR / '.env')
    raw = (os.environ.get('OPENCODE_BIN') or OC_EXE_FALLBACK).strip()
    path = os.path.abspath(raw) if os.path.isabs(raw) else raw
    if not os.path.isfile(path):
        print(f'[stack] 警告：OPENCODE_BIN 不存在 {path}，回退 {OC_EXE_FALLBACK}')
        path = OC_EXE_FALLBACK
    return path


# ---------------------------------------------------------------- 栈编排
def start_stack():
    global TOKEN
    for port in (STUB_PORT, SERVE_PORT, BACKEND_PORT):
        if not _port_free(port):
            raise RuntimeError(f'端口 :{port} 已被占用，探针栈要求独占'
                               '（严禁复用 dev 栈 3002/4096 的任何端口）')
    SB.mkdir(parents=True, exist_ok=True)
    gdir = SB / 'xdg-config' / 'opencode'
    gdir.mkdir(parents=True)
    for n in ('package.json', 'package-lock.json', 'bun.lock', 'node_modules'):
        src, dst = GP_OC / n, gdir / n
        (shutil.copytree if src.is_dir() else shutil.copy2)(src, dst)
    (gdir / 'opencode.jsonc').write_text(json.dumps({
        '$schema': 'https://opencode.ai/config.json',
        'provider': {'probe': {
            'npm': '@ai-sdk/openai-compatible', 'name': 'UndoE2E',
            'options': {'baseURL': f'http://127.0.0.1:{STUB_PORT}/v1',
                        'apiKey': PROBE_TOKEN},
            'models': {'scripted': {'name': 'Scripted'}}}}}), encoding='utf-8')
    auth = SB / 'oc-data' / 'opencode' / 'auth.json'
    auth.parent.mkdir(parents=True, exist_ok=True)
    auth.write_text(json.dumps({'probe': {'type': 'api', 'key': PROBE_TOKEN}}),
                    encoding='utf-8')

    stub = subprocess.Popen([sys.executable, str(Path(__file__).parent / 'undo_stub_llm.py')],
                            cwd=str(SB), env={**os.environ, 'UNDOPROBE_STUB_PORT': str(STUB_PORT),
                                              'UNDOPROBE_LOG_DIR': str(SB), 'PYTHONUTF8': '1'},
                            stdout=open(SB / 'stub.log', 'ab'), stderr=subprocess.STDOUT)
    PROCS.append(stub)
    assert _wait(STUB_PORT, '/v1/models', 15), 'stub not up'
    print(f'[stack] stub LLM :{STUB_PORT} up')

    from dotenv import load_dotenv
    load_dotenv(SERVER_DIR / '.env')
    internal_token = (os.environ.get('MCP_INTERNAL_TOKEN') or '').strip()
    env = {**os.environ, 'DB_NAME': DB_NAME, 'FLASK_PORT': str(BACKEND_PORT),
           'INIT_ADMIN_PASSWORD': ADMIN_PASS,
           'OPENCODE_MODEL': 'probe/scripted',
           'OPENCODE_BASE_URL': f'http://127.0.0.1:{SERVE_PORT}',
           'OPENCODE_GLOBAL_DIR': str(gdir), 'AI_WORKSPACE_ROOT': str(SB / 'ws'),
           'PYTHONUTF8': '1'}
    be = subprocess.Popen([sys.executable, 'app.py'], cwd=str(SERVER_DIR), env=env,
                          stdout=open(SB / 'backend.log', 'ab'), stderr=subprocess.STDOUT)
    PROCS.append(be)
    assert _wait(BACKEND_PORT, '/health', 120), 'backend not up'
    print(f'[stack] backend :{BACKEND_PORT} up')

    serve = subprocess.Popen([_oc_bin(), 'serve', '--port', str(SERVE_PORT)],
                             cwd=os.path.expanduser('~'),
                             env={**os.environ, 'XDG_CONFIG_HOME': str(SB / 'xdg-config'),
                                  'XDG_DATA_HOME': str(SB / 'oc-data'),
                                  'OPENCODE_GLOBAL_DIR': str(gdir),
                                  'BAIZE_INTERNAL_TOKEN': internal_token,
                                  'PYTHONUTF8': '1'},
                             stdout=open(SB / 'serve.log', 'ab'), stderr=subprocess.STDOUT)
    PROCS.append(serve)
    assert _wait(SERVE_PORT, '/global/health', 90), 'serve not up'
    print(f'[stack] oc serve :{SERVE_PORT} up')

    st, body = _req(BACKEND_PORT, 'POST', '/auth/login',
                    {'username': ADMIN_USER, 'password': ADMIN_PASS})
    assert st == 200, f'login failed: {st}（新库未播种 admin？查 backend.log）'
    TOKEN = body['token']


def stop_stack():
    for p in reversed(PROCS):
        try:
            subprocess.run(['taskkill', '/PID', str(p.pid), '/T', '/F'],
                           capture_output=True, timeout=20)
        except Exception:
            pass
    PROCS.clear()
    # 端口释放确认：三个端口必须回到空闲（拆栈验收口径）
    released = True
    for _ in range(15):
        released = all(_port_free(p) for p in (STUB_PORT, SERVE_PORT, BACKEND_PORT))
        if released:
            break
        time.sleep(1)
    if released:
        print('[stack] torn down，端口 3197/4198/3094 已全部释放')
    else:
        busy = [str(p) for p in (STUB_PORT, SERVE_PORT, BACKEND_PORT) if not _port_free(p)]
        print(f'[stack] 警告：端口 {",".join(busy)} 仍未释放（taskkill 可能未清干净，'
              '用 netstat -ano | findstr 查 PID 手工处置）')


# ---------------------------------------------------------------- 会话操作
def new_session():
    st, body = _api('POST', '/ai/chat/sessions', {})
    assert st == 201, (st, body)
    return body['id'], body['workspacePath']


def send(sid, tag):
    st, body = _api('POST', f'/ai/chat/sessions/{sid}/messages',
                    {'content': f'[[UNDO-PROBE:{tag}]] 请按标记执行。'}, timeout=90)
    assert st == 202, (st, body)
    return body['messageId']


def _oc_sid_of(sid):
    """探针特权：直接读库取当前绑定的 OC 会话 id（rebuild 换绑后跟随新值）。"""
    import psycopg2
    with psycopg2.connect(**_dsn(DB_NAME)) as conn, conn.cursor() as cur:
        cur.execute("SELECT opencode_session_id FROM ai_chat_sessions WHERE id=%s",
                    (sid,))
        row = cur.fetchone()
        return row[0] if row else None


def wait_oc_idle(oc_sid, ws, timeout_s=120):
    """OC REST 权威完成信号（get_messages 契约）：最后一条 assistant 消息
    time.completed 已落 + finish 不在续跑态（None/unknown/tool-calls）。

    平台行在回合流式期间就增量落库（time-debounced），只看「assistant 行 +
    meta.durationMs」会在工具调用间隙提前返回——此时 undo 会撞上
    SESSION_BUSY（首轮实测 409）。必须等 OC 真正 idle。"""
    dl = time.time() + timeout_s
    while time.time() < dl:
        st, om = _req(SERVE_PORT, 'GET',
                      f'/session/{oc_sid}/message?directory={quote(ws, safe="")}',
                      timeout=15)
        if st == 200 and om:
            info = (om[-1] or {}).get('info') or {}
            t = info.get('time') or {}
            if info.get('role') == 'assistant' and t.get('completed') \
                    and info.get('finish') not in (None, 'unknown', 'tool-calls'):
                return om
        time.sleep(1)
    raise RuntimeError('oc turn idle timeout')


def wait_turn(sid, ws, n_before, timeout_s=120):
    """等当前回合真正完成（OC idle → 平台行落库 → 监听线程退出）。

    n_before 是「全会话累计」口径——撤销搬走 stash 后计数下降，
    撤销后发送的新回合按剩余行数+1 传参（场景 A/C 撤销 u2 后为 2）。"""
    oc = _oc_sid_of(sid)
    assert oc, 'session 无 opencode_session_id'
    wait_oc_idle(oc, ws, timeout_s)
    dl = time.time() + 30
    while time.time() < dl:
        st, body = _api('GET', f'/ai/chat/sessions/{sid}/messages')
        assert st == 200, (st, body)
        done = [m for m in body['messages'] if m['role'] == 'assistant'
                and (m.get('meta') or {}).get('durationMs')]
        if len(done) >= n_before:
            time.sleep(1.5)   # 让回合监听线程走完 idle→退出（undo 前 has_listener 必须为 False）
            return body['messages'], body.get('revert')
        time.sleep(1)
    raise RuntimeError(f'platform persist timeout（等待 {n_before} 个完成回合）')


def files_state(ws):
    out = {}
    for rel in ('a.txt', 'b.txt', 'c.txt'):
        p = Path(ws) / rel
        out[rel] = p.read_text(encoding='utf-8', errors='replace').strip() if p.exists() else '<缺失>'
    return out


def stub_rows():
    return [json.loads(l) for l in
            (SB / 'stub-requests.jsonl').read_text(encoding='utf-8').splitlines()
            if l.strip()]


def stub_users_for(tag):
    """stub 收到的、该 tag 标记的**最后一个**请求里模型看到的 user 文本序列。"""
    rows = [r for r in stub_rows() if r.get('tag') == tag]
    assert rows, f'stub 没收到 tag={tag} 的请求'
    return [m.get('text', '') for m in rows[-1]['messages'] if m['role'] == 'user']


def make_sse_listener(key, sid, frames):
    """订阅平台 SSE（/ai/chat/sessions/<sid>/events），记录 (event 名, data) 对。

    平台帧形为 `event: <type>\\ndata: <json>`（_format_sse）——事件名在
    event: 行上，data: 行不含类型名，所以必须解析两行（骨架按 data 行
    contains 'session.revert' 匹配永远不中）。收到 session.revert 即返回。"""
    def run():
        try:
            conn = http.client.HTTPConnection('127.0.0.1', BACKEND_PORT, timeout=None)
            conn.request('GET', f'/ai/chat/sessions/{sid}/events',
                         headers={'Authorization': f'Bearer {TOKEN}',
                                  'Accept': 'text/event-stream'})
            resp = conn.getresponse()
            if resp.status != 200:
                frames[key].append(('__http__', str(resp.status)))
                return
            cur_event = None
            while True:
                line = resp.fp.readline()
                if not line:
                    frames[key].append(('__eof__', ''))
                    return
                s = line.decode('utf-8', 'replace').strip()
                if s.startswith('event:'):
                    cur_event = s[len('event:'):].strip()
                elif s.startswith('data:'):
                    frames[key].append((cur_event or '?', s[len('data:'):].strip()[:160]))
                    if cur_event == 'session.revert':
                        return
        except Exception as e:
            frames[key].append(('__error__', str(e)[:120]))
    th = threading.Thread(target=run, daemon=True, name=f'sse-{key}')
    th.start()
    return th


def got_revert(frames, key):
    return any(ev == 'session.revert' for ev, _ in frames[key])


# ---------------------------------------------------------------- 场景
def scenario_native():
    print('\n===== A：原生撤销（文件/上下文/提交/跨标签页） =====')
    sid, ws = new_session()
    send(sid, 'T1'); wait_turn(sid, ws, 1)
    u2 = send(sid, 'T2'); wait_turn(sid, ws, 2)
    fs = files_state(ws)
    check('T2 后 a.txt=v2', fs['a.txt'] == 'v2', str(fs))

    st, pv = _api('GET', f'/ai/chat/sessions/{sid}/undo-preview?messageId={u2}')
    check('preview: messageCount>=2 / capability=full',
          st == 200 and pv.get('messageCount', 0) >= 2
          and pv.get('fileCapability') == 'full', str(pv))

    # 跨标签页：两条 SSE 连接（平台帧 event: 行携带类型名）
    frames = {'a': [], 'b': []}
    th = [make_sse_listener(k, sid, frames) for k in ('a', 'b')]
    time.sleep(1.5)

    st, body = _api('DELETE', f'/ai/chat/sessions/{sid}/messages/{u2}', timeout=90)
    check('undo 200 且 revert.mode=native',
          st == 200 and (body.get('revert') or {}).get('mode') == 'native',
          f'st={st} body={json.dumps(body, ensure_ascii=False)[:220]}')
    msgs = body.get('messages') or []
    check('平台窗口已裁掉 u2 及之后', bool(msgs) and all(m['id'] != u2 for m in msgs),
          f'n={len(msgs)}')
    fs = files_state(ws)
    check('文件还原: a.txt=v1 / b.txt 缺失 / c.txt 缺失',
          fs == {'a.txt': 'v1', 'b.txt': '<缺失>', 'c.txt': '<缺失>'}, str(fs))

    [t.join(timeout=15) for t in th]
    check('两个 SSE 连接都收到 session.revert',
          got_revert(frames, 'a') and got_revert(frames, 'b'),
          str({k: [ev for ev, _ in v][-3:] for k, v in frames.items()}))

    st, body = _api('GET', f'/ai/chat/sessions/{sid}/messages')
    check('GET messages 带 revert 态（任意标签页刷新即收敛）',
          st == 200 and (body.get('revert') or {}).get('mode') == 'native',
          json.dumps(body.get('revert'), ensure_ascii=False)[:120])

    send(sid, 'T3'); wait_turn(sid, ws, 2)      # 撤销后剩 1 个完成回合 + 本回合
    users = stub_users_for('T3')
    check('T3 请求含 T1、不含 T2（上下文真回退，E7）',
          any('UNDO-PROBE:T1' in u for u in users)
          and not any('UNDO-PROBE:T2' in u for u in users),
          json.dumps(users, ensure_ascii=False)[:200])

    st, body = _api('GET', f'/ai/chat/sessions/{sid}/messages')
    check('发送后提交：revert 清空', st == 200 and body.get('revert') is None,
          json.dumps(body.get('revert'), ensure_ascii=False)[:120])
    st, body = _api('POST', f'/ai/chat/sessions/{sid}/redo', timeout=30)
    err = (body.get('error') or {}) if isinstance(body, dict) else {}
    check('提交后 redo → NOTHING_TO_REDO 409',
          st == 409 and err.get('code') == 'NOTHING_TO_REDO',
          f'st={st} body={json.dumps(body, ensure_ascii=False)[:160]}')


def scenario_redo():
    print('\n===== B：重做全恢复 =====')
    sid, ws = new_session()
    send(sid, 'T1'); wait_turn(sid, ws, 1)
    u2 = send(sid, 'T2'); wait_turn(sid, ws, 2)
    st, body = _api('DELETE', f'/ai/chat/sessions/{sid}/messages/{u2}', timeout=90)
    check('undo 200', st == 200,
          f'st={st} body={json.dumps(body, ensure_ascii=False)[:220]}')
    check('undo 后 b.txt 缺失 / a.txt 回 v1',
          files_state(ws)['b.txt'] == '<缺失>' and files_state(ws)['a.txt'] == 'v1',
          str(files_state(ws)))
    st, body = _api('POST', f'/ai/chat/sessions/{sid}/redo', timeout=90)
    check('redo 200', st == 200, f'st={st} {json.dumps(body, ensure_ascii=False)[:160]}')
    fs = files_state(ws)
    check('文件恢复 v2/b-new（重做还原撤销的全部文件改动）',
          fs['a.txt'] == 'v2' and fs['b.txt'] == 'b-new', str(fs))
    st, body = _api('GET', f'/ai/chat/sessions/{sid}/messages')
    ids = [m['id'] for m in body['messages']]
    check('消息窗口恢复含 u2', u2 in ids, str(ids))
    check('redo 后 revert 清空', body.get('revert') is None,
          json.dumps(body.get('revert'), ensure_ascii=False)[:120])


def scenario_rebuild():
    print('\n===== C：锚失效 → rebuild + 历史重放 =====')
    sid, ws = new_session()
    send(sid, 'T1'); wait_turn(sid, ws, 1)
    u2 = send(sid, 'T2'); wait_turn(sid, ws, 2)
    # 模拟「会话恢复过」：把平台 oc 指针换到一个有内容的新 OC 会话。
    # 新会话必须非空（种一条 user 消息），否则 resolve_undo_target 在
    # oc_messages 为空时走 native no-op 而不是 rebuild。
    st, oc_new = _req(SERVE_PORT, 'POST',
                      f'/session?directory={quote(ws, safe="")}', {'title': 'replaced'})
    assert st == 200 and oc_new.get('id'), (st, oc_new)
    st, _r = _req(SERVE_PORT, 'POST',
                  f"/session/{oc_new['id']}/prompt_async?directory={quote(ws, safe='')}",
                  {'parts': [{'type': 'text', 'text': '[[UNDO-PROBE:SEED]] 请按标记执行。'}],
                   'model': {'providerID': 'probe', 'modelID': 'scripted'}})
    assert st in (200, 202, 204), (st, _r)
    wait_oc_idle(oc_new['id'], ws, timeout_s=60)   # seed 回合完成（OC 不 busy）

    import psycopg2
    conn = psycopg2.connect(**_dsn(DB_NAME))
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE ai_chat_sessions SET opencode_session_id=%s WHERE id=%s",
                    (oc_new['id'], sid))
    conn.close()

    st, body = _api('DELETE', f'/ai/chat/sessions/{sid}/messages/{u2}', timeout=90)
    check('undo → rebuild 模式（锚不在当前 OC 会话 → 重建式降级）',
          st == 200 and (body.get('revert') or {}).get('mode') == 'rebuild',
          f'st={st} revert={json.dumps(body.get("revert"), ensure_ascii=False)[:200]}')

    send(sid, 'T3'); wait_turn(sid, ws, 2)
    # 重放块把被保留轮次的 T1 标记带进了同一条 user 文本 → stub 的首标记
    # 归类为 T1；取证不按 tag=T3 过滤，按「T3 标记 + 摘要头」双条件定位。
    hit = None
    for r in stub_rows():
        users = [m.get('text', '') for m in r['messages'] if m['role'] == 'user']
        if any('UNDO-PROBE:T3' in u and '此前对话摘要' in u for u in users):
            hit = r
    check('T3 请求含「此前对话摘要」（历史重放到达模型）', hit is not None,
          '' if hit is None else 'stub 请求 ts=' + hit.get('ts', ''))


def scenario_batch_child():
    print('\n===== D：批子会话 409（执行所有权门禁，不开放手工撤销） =====')
    # 种一个 running 批子会话行（ai_chat_batches 需 prompt NOT NULL；会话行
    # 无 kind 列）。undo 应 409 BATCH_SESSION_CONTROLLED。
    import psycopg2
    from psycopg2.extras import Json
    sid = 'sess_bchild_' + secrets.token_hex(4)
    bid = 'batch_' + sid
    conn = psycopg2.connect(**_dsn(DB_NAME))
    with conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE username='admin' LIMIT 1")
        uid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, status, total) "
            "VALUES (%s, %s, 'undo-e2e-batch', 'probe batch prompt', 'running', 1) "
            "ON CONFLICT (id) DO NOTHING", (bid, uid))
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, workspace_path, session_token, "
            "  token_expires_at, status, batch_id, batch_seq) "
            "VALUES (%s, %s, %s, %s, NOW() + interval '1 day', 'running', %s, 1)",
            (sid, uid, str(SB / 'bchild-ws'), 'tok-undoprobe-' + sid, bid))
        cur.execute(
            "INSERT INTO ai_chat_messages (id, session_id, role, content) "
            "VALUES (%s, %s, 'user', %s)",
            (sid + ':u', sid, Json([{'type': 'text', 'text': 'x'}])))
    conn.close()
    st, body = _api('DELETE', f'/ai/chat/sessions/{sid}/messages/{sid}:u', timeout=30)
    err = (body.get('error') or {}) if isinstance(body, dict) else {}
    check('批子会话 undo → 409 BATCH_SESSION_CONTROLLED',
          st == 409 and err.get('code') == 'BATCH_SESSION_CONTROLLED',
          f'st={st} body={json.dumps(body, ensure_ascii=False)[:160]}')
    with psycopg2.connect(**_dsn(DB_NAME)) as c2, c2.cursor() as cu:
        cu.execute("DELETE FROM ai_chat_messages WHERE session_id=%s", (sid,))
        cu.execute("DELETE FROM ai_chat_sessions WHERE id=%s", (sid,))
        cu.execute("DELETE FROM ai_chat_batches WHERE id=%s", (bid,))


# ---------------------------------------------------------------- 主流程
def run_scenario(label, fn):
    before = len(_results)
    try:
        fn()
    except Exception as e:
        with _ev_lock:
            _results.append(False)
        print(f'  ✗ SCENARIO ERROR [{label}]: {type(e).__name__}: {str(e)[:400]}')


def main():
    keep = '--keep' in sys.argv
    prepare_db()
    start_stack()
    try:
        run_scenario('A', scenario_native)
        run_scenario('B', scenario_redo)
        run_scenario('C', scenario_rebuild)
        run_scenario('D', scenario_batch_child)
    finally:
        if not keep:
            stop_stack()
        print('\n===== 结果 =====')
        ok = all(_results) and bool(_results)
        print('ALL GREEN' if ok else 'HAS FAILURES', f'(sandbox={SB})')
        if keep:
            print(f'[stack] --keep: 栈保留中（stub :{STUB_PORT} / serve :{SERVE_PORT}'
                  f' / backend :{BACKEND_PORT}），手工排查后 taskkill 树清理')
        sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
