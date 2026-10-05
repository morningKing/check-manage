"""AI 压测栈编排（ai-stress spec §2/§5）。

职责单一：建/拆专属压测栈（DB casemanage_stress + 后端:3092 stub 运行时
+ 可选专属 serve:4097），并向套件暴露固定 fixtures。与 dev 零共享——
kill/重启只伤本栈实例。finalizer 保证失败路径也 DROP DB、杀进程树。
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import psycopg2
import psutil
import pytest
import requests

SERVER_DIR = Path(__file__).resolve().parents[2]   # server/tests/stress/ → server/
STRESS_DB = 'casemanage_stress'
STRESS_PORT = 3092
SERVE_PORT = 4097
ADMIN_USER, ADMIN_PASS = 'admin', 'stress-admin-pw'
METRICS_ROOT = Path(__file__).resolve().parents[3] / 'docs' / 'ai-testing' / 'evidence' / 'stress'


def _ensure_port_free(port: int):
    """前置检查：压测栈端口已被占用时，报可读错误而不是 60s 超时后才炸。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        s.connect(('127.0.0.1', port))
    except OSError:
        return                                   # 连不上 = 空闲
    finally:
        try:
            s.close()
        except Exception:
            pass
    raise RuntimeError(
        f'压测栈端口 :{port} 已被占用（压测要求独占该端口）。'
        f'请先释放（netstat -ano | findstr :{port}）后重跑 pytest -m stress。')


def _base_dsn():
    """dev .env 的连接参数，但库名固定 postgres（建/删库用）。"""
    from dotenv import load_dotenv
    load_dotenv(SERVER_DIR / '.env')
    import config
    dsn = dict(config.DB_CONFIG)
    dsn['dbname'] = 'postgres'
    dsn.pop('options', None)
    return dsn


def _db_exists(cur, name):
    cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,))
    return cur.fetchone() is not None


def _drop_db(name):
    dsn = _base_dsn()
    conn = psycopg2.connect(**dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname=%s AND pid<>pg_backend_pid()", (name,))
        if _db_exists(cur, name):
            cur.execute(f'DROP DATABASE "{name}"')
    conn.close()


class Sampler:
    """2s 周期抓 /metrics + 后端进程 RSS；record() 切换标签并落 JSON。"""

    def __init__(self, stack):
        self.stack = stack
        self.samples = []
        self.label = 'idle'
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def record(self, label: str):
        self._flush()
        self.label = label

    def _loop(self):
        while not self._stop.is_set():
            self._snap()
            time.sleep(2)

    def _snap(self):
        row = {'t': time.time(), 'label': self.label}
        try:
            r = requests.get(f'{self.stack.base}/metrics', timeout=3)
            for line in r.text.splitlines():
                if line and not line.startswith('#'):
                    k, _, v = line.partition(' ')
                    row[k] = float(v)
        except Exception:
            row['metrics_error'] = True
        try:
            p = psutil.Process(self.stack.backend_pid)
            row['backend_rss_mb'] = p.memory_info().rss / 1e6
            row['backend_cpu'] = p.cpu_percent(interval=None)
        except Exception:
            pass
        self.samples.append(row)

    def _flush(self):
        if not self.samples:
            return
        METRICS_ROOT.mkdir(parents=True, exist_ok=True)
        out = METRICS_ROOT / f"{time.strftime('%Y%m%d-%H%M%S')}-{self.label}.json"
        out.write_text(json.dumps(self.samples, ensure_ascii=False, indent=1),
                       encoding='utf-8')
        self.samples = []


class Stack:
    """压测栈句柄：后端进程/登录态/DB 查询/重启/专属 serve。"""

    def __init__(self):
        self.base = f'http://127.0.0.1:{STRESS_PORT}'
        self.backend_pid = None
        self._proc = None
        self._backend_log = None
        self._serve_procs = []
        self.token = ''
        self.tmp_dirs = []
        self.db_dsn = {**_base_dsn(), 'dbname': STRESS_DB}

    # ---- 生命周期 ----
    def start_backend(self, concurrency: int, profile: dict | None = None,
                      env_extra: dict | None = None):
        """env_extra：混沌层切换真实运行时用（AI_AGENT_RUNTIME/OPENCODE_BASE_URL）。"""
        _ensure_port_free(STRESS_PORT)
        env = {**os.environ,
               'FLASK_PORT': str(STRESS_PORT),
               'DB_NAME': STRESS_DB,
               'AI_AGENT_RUNTIME': 'stub',
               'AI_STUB_ALLOW': '1',
               'AI_BATCH_CONCURRENCY': str(concurrency),
               'INIT_ADMIN_PASSWORD': ADMIN_PASS,
               'PYTHONUTF8': '1'}
        if profile:
            env['AI_STUB_PROFILE'] = json.dumps(profile)
        env.update(env_extra or {})
        # 后端日志落盘（metrics 目录/backend.log，追加）：werkzeug 每请求一条
        # access log，stdout=PIPE 无人消费会在 30-60 分钟压测下写满管道缓冲 →
        # 日志写阻塞请求线程 → 后端静默停摆。落盘零阻塞且留排障证据。
        METRICS_ROOT.mkdir(parents=True, exist_ok=True)
        self._close_backend_log()
        self._backend_log = open(METRICS_ROOT / 'backend.log', 'ab')
        self._proc = subprocess.Popen(
            [sys.executable, 'app.py'], cwd=str(SERVER_DIR), env=env,
            stdout=self._backend_log, stderr=subprocess.STDOUT)
        self.backend_pid = self._proc.pid
        self._wait_health()

    def restart_backend(self, concurrency: int, profile: dict | None = None,
                        env_extra: dict | None = None):
        self.stop_backend()
        self.start_backend(concurrency, profile, env_extra)

    def _close_backend_log(self):
        if getattr(self, '_backend_log', None):
            try:
                self._backend_log.close()
            except Exception:
                pass
            self._backend_log = None

    def stop_backend(self):
        if self._proc and self._proc.poll() is None:
            for child in psutil.Process(self._proc.pid).children(recursive=True):
                child.kill()
            self._proc.kill()
            self._proc.wait(timeout=10)
        self._proc = None
        self._close_backend_log()

    def _wait_health(self, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if requests.get(f'{self.base}/health', timeout=2).status_code < 500:
                    self._login()
                    return
            except Exception:
                time.sleep(1)
        raise RuntimeError('stress backend did not come up in 60s')

    def _login(self):
        r = requests.post(f'{self.base}/auth/login', timeout=5, json={
            'username': ADMIN_USER, 'password': ADMIN_PASS})
        r.raise_for_status()
        self.token = r.json()['token']

    @property
    def auth_header(self):
        return {'Authorization': f'Bearer {self.token}'}

    # ---- 专属 serve（混沌层）----
    def spawn_serve(self, port: int = SERVE_PORT):
        for p in self._serve_procs:              # 幂等：已有存活实例直接复用
            if p.poll() is None:
                return p
        from dotenv import load_dotenv
        load_dotenv(SERVER_DIR / '.env')     # opencode_bin() 读 os.environ——先让 .env 的 OPENCODE_BIN 入环境
        from utils.opencode_launch import opencode_bin, serve_env
        gd = tempfile.mkdtemp(prefix='stress-oc-global-')
        self.tmp_dirs.append(gd)
        env = serve_env({**os.environ, 'OPENCODE_GLOBAL_DIR': gd})
        proc = subprocess.Popen(
            [opencode_bin(), 'serve', '--port', str(port)], cwd=str(Path.home()),
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._serve_procs.append(proc)
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                if requests.get(f'http://127.0.0.1:{port}/global/health',
                                timeout=2).ok:
                    return proc
            except Exception:
                time.sleep(1)
        raise RuntimeError(f'dedicated serve :{port} did not come up')

    def kill_serve(self, proc):
        for child in psutil.Process(proc.pid).children(recursive=True):
            child.kill()
        proc.kill()
        proc.wait(timeout=10)
        if proc in self._serve_procs:
            self._serve_procs.remove(proc)

    # ---- DB ----
    def db_query(self, sql, params=()):
        conn = psycopg2.connect(**self.db_dsn)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()
        finally:
            conn.close()

    def invariants(self) -> dict:
        # 计数守恒：ai_chat_batches 没有 cancelled 列（cancelled 子任务计入
        # failed 聚合，见 utils/batch_repo.py 与 batch_engine._recompute_batch_status
        # —— 终态时 terminal=done+failed 必须 == total），故守恒式为 total=done+failed。
        bad = self.db_query(
            "SELECT count(*) FROM ai_chat_batches "
            "WHERE total <> done+failed "
            "AND status IN ('completed','failed','partial')")[0][0]
        orphans = self.db_query(
            "SELECT count(*) FROM ai_chat_sessions s WHERE s.batch_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM ai_chat_batches b WHERE b.id=s.batch_id)")[0][0]
        zombie = self.db_query(
            "SELECT count(*) FROM ai_chat_sessions WHERE status='running' "
            "AND last_active_at < NOW() - interval '10 minutes'")[0][0]
        return {'orphans': orphans, 'zombie_running': zombie,
                'counter_violations': bad}

    # ---- 拆栈 ----
    def teardown(self):
        # 每步独立 try：任何一步失败都不阻断后续步骤——DROP DB 必须被尝试
        # （finalizer 失败路径也要拆干净）。
        errs = []
        try:
            self.stop_backend()
        except Exception as e:
            errs.append(f'stop_backend: {e}')
        for p in list(self._serve_procs):
            try:
                self.kill_serve(p)
            except Exception as e:
                errs.append(f'kill_serve: {e}')
        for d in self.tmp_dirs:
            shutil.rmtree(d, ignore_errors=True)
        try:
            _drop_db(STRESS_DB)
        except Exception as e:
            errs.append(f'drop_db: {e}')
        if errs:
            print(f'[stress-teardown] 拆栈部分失败（继续完成剩余步骤）: {"; ".join(errs)}')


def _create_db():
    _drop_db(STRESS_DB)                       # 幂等：先清残留
    dsn = _base_dsn()
    conn = psycopg2.connect(**dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{STRESS_DB}"')
    conn.close()
    env = {**os.environ, 'DB_NAME': STRESS_DB,
           'INIT_ADMIN_PASSWORD': ADMIN_PASS, 'PYTHONUTF8': '1'}
    proc = subprocess.run([sys.executable, 'init_db.py'], cwd=str(SERVER_DIR),
                          env=env, capture_output=True, timeout=300)
    if proc.returncode != 0:
        # 失败时把捕获的输出翻出来，否则 CalledProcessError 里看不到死因。
        raise RuntimeError(
            f'init_db.py failed (exit {proc.returncode})\n'
            f'--- stdout tail ---\n{proc.stdout.decode("utf-8", "replace")[-2000:]}\n'
            f'--- stderr ---\n{proc.stderr.decode("utf-8", "replace")[-2000:]}')


@pytest.fixture(scope='session')
def stress_stack():
    stack = Stack()
    sampler = None
    try:
        _create_db()
        stack.start_backend(concurrency=3)
        sampler = Sampler(stack)
        yield stack
    finally:
        # 建栈中途失败（init_db/启动/登录）也要拆——失败路径同样 DROP DB、杀进程。
        if sampler is not None:
            sampler._stop.set()
            sampler._flush()
        stack.teardown()


@pytest.fixture(scope='session')
def sampler(stress_stack):
    s = Sampler(stress_stack)
    yield s
    s._stop.set()
    s._flush()


# ---- 共享助手（套件 import）----
def create_batch(stack, n_children: int, *, callback_url=None, api=None):
    """建批。n_children = 子任务数（内部 API 每个文件一个子任务，上限 50/批，
    故 >50 时复用同一 staged 文件路径凑不满——套件用 ≤50）。api=可选
    dict(base, headers) 走开放 API（空壳批/回调场景）；默认管理 API。

    已核实形状（routes/ai_chat_batches.py:64-95/139-141, utils/batch_repo.py:313,
    routes/open_api_batches.py:147-194/413-414/637-668）：
    - 内部 staging：POST form {file(单数), upload_session_id} → 201 扁平 {name, path}
    - 内部建批：files=[{name, path}], ... → 201 {'batch': {...}, 'sessions': [...]}
      —— 批 id 取 r.json()['batch']['id']
    - 开放上传：POST /v1/ai-batches/uploads（multipart 字段名 files 复数，
      request.files.getlist('files')）→ 201 {'files': [{name, path}, …]}
    - 开放建批：POST /v1/ai-batches {name, prompt, files, callbackUrl}
      （files 可空=空壳）→ 201 {'batchId', 'status', 'total'}，批 id 取 ['batchId']
    - 开放 append：POST /v1/ai-batches/<id>/append {files:[...]} → 200
      {'batchId', 'status', 'total', 'appended'}
    """
    s = requests.Session()
    prompt = '直接回复:STRESS-OK。不要读取文件,不要执行命令。'
    name = f'STRESS-{time.time_ns()}'
    if api:
        base, hdr = api['base'], api['headers']
        up = s.post(f'{base}/v1/ai-batches/uploads', headers=hdr,
                    files={'files': ('stress-in.txt', b'STRESS-INPUT', 'text/plain')},
                    data={'upload_session_id': name}, timeout=30)
        up.raise_for_status()
        staged = up.json()['files']              # [{'name','path'},…]（非扁平）
        body = {'name': name, 'prompt': prompt,
                'files': staged * n_children}
        if callback_url:
            body['callbackUrl'] = callback_url
        r = s.post(f'{base}/v1/ai-batches', headers=hdr, json=body, timeout=30)
        r.raise_for_status()
        return r.json()['batchId']
    up = s.post(f'{stack.base}/ai/chat/batches/staging/upload',
                headers=stack.auth_header,
                files={'file': ('stress-in.txt', b'STRESS-INPUT', 'text/plain')},
                data={'upload_session_id': name}, timeout=30)
    up.raise_for_status()
    staged = up.json()                           # 扁平 {name, path}（内部 staging 特有）
    body = {'name': name, 'prompt': prompt,
            'files': [staged] * min(n_children, 50)}
    r = s.post(f'{stack.base}/ai/chat/batches', headers=stack.auth_header,
               json=body, timeout=30)
    r.raise_for_status()
    return r.json()['batch']['id']               # 201 {'batch':…, 'sessions':…}


def append_files(stack, batch_id: str, n: int, *, api: dict):
    """空壳批 append 填充（开放 API POST /v1/ai-batches/<id>/append）。"""
    s = requests.Session()
    up = s.post(f'{api["base"]}/v1/ai-batches/uploads', headers=api['headers'],
                files={'files': ('stress-append.txt', b'STRESS-APPEND', 'text/plain')},
                data={'upload_session_id': f'append-{time.time_ns()}'}, timeout=30)
    up.raise_for_status()
    r = s.post(f'{api["base"]}/v1/ai-batches/{batch_id}/append',
               headers=api['headers'],
               json={'files': up.json()['files'] * n}, timeout=30)
    r.raise_for_status()
    return r.json()


def wait_terminal(stack, batch_id: str, timeout_s: float):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        d = requests.get(f'{stack.base}/ai/chat/batches/{batch_id}',
                         headers=stack.auth_header, timeout=10).json()
        if d.get('batch', {}).get('status') in ('completed', 'failed', 'partial'):
            return d
        time.sleep(3)
    raise TimeoutError(f'batch {batch_id} not terminal in {timeout_s}s')


def admin_login(base: str) -> str:
    return requests.post(f'{base}/auth/login', timeout=5, json={
        'username': ADMIN_USER, 'password': ADMIN_PASS}).json()['token']
