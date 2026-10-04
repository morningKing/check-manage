# AI 压力测试方案 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立可重复的 AI 压测体系：StubRuntime 容量阶梯（5→50 并发）、专属实例混沌注入（kill serve/双实例抢租约/outbox 退避/重启守卫）、读路径压载（SSE/分页/洪峰），专属 DB 用完即删零残留。

**Architecture:** conftest 编排专属压测栈（建 `casemanage_stress` 库→init_db→起压测后端:3092 stub 运行时→采样器→finalizer 全拆）；容量/读路径套件跑 stub 层，混沌套件额外拉起专属 serve:4097（真模型，≤3 并发）。pytest marker `stress` 默认排除。

**Tech Stack:** pytest + psycopg2 + requests + psutil + Flask(被测) + opencode serve(被测)。

**Spec:** `docs/superpowers/specs/2026-10-03-ai-stress-testing-design.md`

## Global Constraints

- 压测栈与 dev 零共享：DB `casemanage_stress`（共用 Postgres 实例、独立库）、后端 `FLASK_PORT=3092`、混沌 serve 端口 `4097` + 临时 GLOBAL_DIR。
- 专属 DB 用完即删：finalizer 必须在失败路径也 DROP。
- 压测数据前缀 `STRESS-`（仅辨识用）。
- 混沌层真模型并发 ≤3；容量层全程 stub（零 token）。
- marker `stress` 默认排除：pytest.ini `addopts` 加 `-m "not stress"`，CLI `-m stress` 显式覆盖。
- 不改任何被测产品代码；`utils/runtime/__init__.py` 的 get_runtime 注册分支是唯一例外（stub 是新增运行时实现，属 adapter 机制的预期扩展点）。
- StubClient 消息形状必须与 OpenCode 原始返回逐字段对齐：`[{'info': {...}, 'parts': [...]}]`，assistant 判完成= `info.finish` 非 `{'tool-calls','tool_use'}` 且 `info.time.completed` 存在（worker `_CONTINUATION_FINISH`）。
- 测试命令一律 `cd server && python -m pytest ...`；mcp-server venv 与本计划无关。

---

### Task 1: StubRuntime + get_runtime 注册 + 形状对照单测

**Files:**
- Create: `server/utils/runtime/stub.py`
- Modify: `server/utils/runtime/__init__.py`（get_runtime 的 kind 分支放行 `'stub'`）
- Test: `server/tests/test_runtime_stub.py`

**Interfaces:**
- Consumes: `utils/runtime/base.py` 的 `AgentRuntime`（7 方法 + `kind`）。
- Produces: `StubRuntime(profile: dict | None)`、`StubRuntime.get_client()` 返回 `StubClient`（`create_session(directory=,title=)->str`、`send_prompt_async(sid,content,...)->None`、`get_messages(sid,directory="")->list`、`abort_session(sid,directory="")->None`、`delete_session(sid)->None`）。profile 键：`delay_ms:[min,max]`、`error_rate`、`hang_rate`、`hang_recover_after_ms`。Task 2 的后端 env `AI_AGENT_RUNTIME=stub` + `AI_STUB_PROFILE` 依赖此注册。

- [ ] **Step 1: 写失败测试** `server/tests/test_runtime_stub.py`

```python
"""StubRuntime 单测：消息形状对照 + 行为面（延迟/错误/卡死/取消）。
无 DB、无网络——完成器线程内联驱动（profile delay=0 时 get_messages 即时落完成）。"""
import time
from utils.runtime.stub import StubRuntime, parse_profile

PROFILE_FAST = {'delay_ms': [0, 0], 'error_rate': 0.0, 'hang_rate': 0.0}


def make_rt(**overrides):
    return StubRuntime(parse_profile({**PROFILE_FAST, **overrides}))


def test_create_and_prompt_completes_with_oc_raw_shape():
    rt = make_rt()
    c = rt.get_client()
    sid = c.create_session(directory='D:/ws', title='t')
    assert sid.startswith('stub_')
    c.send_prompt_async(sid, 'hello', directory='D:/ws')
    time.sleep(0.05)                      # 完成器线程落完成
    msgs = c.get_messages(sid)
    assert msgs[0]['info']['role'] == 'user'
    a = [m for m in msgs if m['info']['role'] == 'assistant']
    assert a and a[0]['info']['finish'] == 'stop'          # 终结 reason
    assert a[0]['info']['time']['completed'] > 0            # 完成时间戳
    assert isinstance(a[0]['parts'], list) and a[0]['parts']


def test_prompt_before_completion_has_no_assistant():
    rt = StubRuntime(parse_profile({'delay_ms': [60_000, 60_000]}))
    c = rt.get_client()
    sid = c.create_session(directory='D:/ws')
    c.send_prompt_async(sid, 'hello')
    msgs = c.get_messages(sid)
    assert [m['info']['role'] for m in msgs] == ['user']


def test_error_rate_lands_failed_shape():
    rt = make_rt(error_rate=1.0)
    c = rt.get_client()
    sid = c.create_session(directory='D:/ws')
    c.send_prompt_async(sid, 'x')
    time.sleep(0.05)
    a = [m for m in c.get_messages(sid) if m['info']['role'] == 'assistant']
    assert a[0]['info']['finish'] == 'error'               # worker 判 failed 的形状


def test_abort_lands_terminal_shape():
    rt = StubRuntime(parse_profile({'delay_ms': [60_000, 60_000]}))
    c = rt.get_client()
    sid = c.create_session(directory='D:/ws')
    c.send_prompt_async(sid, 'x')
    c.abort_session(sid)
    a = [m for m in c.get_messages(sid) if m['info']['role'] == 'assistant']
    assert a and a[0]['info']['finish'] not in ('', None, 'tool-calls')


def test_hang_keeps_running_then_recovers():
    rt = StubRuntime(parse_profile({'delay_ms': [50, 50], 'hang_rate': 1.0,
                                    'hang_recover_after_ms': 150}))
    c = rt.get_client()
    sid = c.create_session(directory='D:/ws')
    c.send_prompt_async(sid, 'x')
    time.sleep(0.1)
    assert not [m for m in c.get_messages(sid) if m['info']['role'] == 'assistant']
    time.sleep(0.2)
    assert [m for m in c.get_messages(sid) if m['info']['role'] == 'assistant']


def test_health_and_capabilities():
    rt = make_rt()
    assert rt.health().get('ok') is True
    assert 'stub' in rt.capabilities().get('kind', '')
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && python -m pytest tests/test_runtime_stub.py -q`
Expected: FAIL —— `ModuleNotFoundError: utils.runtime.stub`

- [ ] **Step 3: 实现** `server/utils/runtime/stub.py`

```python
"""StubRuntime —— 压测容量层专用运行时（ai-stress spec §3）。

内存态模拟 agent 生命周期：send_prompt_async 记录派发，完成器线程按 profile
延迟落 assistant 终态消息。消息形状与 opencode_client.get_messages 原始返回
逐字段对齐（{'info','parts'}；完成判据 = info.finish 非 tool-calls 且
info.time.completed 存在——worker list_messages 门控的字段，形状漂移即
worker 永远判未完成，test_runtime_stub 的对照测试锁住）。
"""
import random
import threading
import time
import uuid

from utils.runtime.base import AgentRuntime

DEFAULT_PROFILE = {'delay_ms': [2000, 8000], 'error_rate': 0.0,
                   'hang_rate': 0.0, 'hang_recover_after_ms': 0}


def parse_profile(raw: dict | None) -> dict:
    p = {**DEFAULT_PROFILE, **(raw or {})}
    p['delay_ms'] = [int(p['delay_ms'][0]), int(p['delay_ms'][1])]
    for k in ('error_rate', 'hang_rate'):
        p[k] = float(p[k])
    p['hang_recover_after_ms'] = int(p['hang_recover_after_ms'])
    return p


class _Session:
    __slots__ = ('messages', 'pending_until', 'outcome', 'done',
                 'hang_until', 'directory')
    def __init__(self):
        self.messages = []          # raw OpenCode 形状
        self.pending_until = None
        self.outcome = 'stop'       # 'stop' | 'error'
        self.done = False
        self.hang_until = 0.0
        self.directory = ''


class StubClient:
    """batch_engine._OpenCodeFacade 经 get_runtime().get_client() 消费的面。"""

    def __init__(self, profile: dict):
        self.profile = profile
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()
        self._completer = threading.Thread(target=self._complete_loop,
                                           daemon=True)
        self._completer.start()

    def create_session(self, *, directory: str, title: str = '') -> str:
        sid = f'stub_{uuid.uuid4().hex}'
        s = _Session()
        s.directory = directory
        with self._lock:
            self._sessions[sid] = s
        return sid

    def send_prompt_async(self, sid: str, content: str, model: str = '',
                          directory: str = '', agent: str = '',
                          agent_parts=None) -> None:
        with self._lock:
            s = self._sessions[sid]
            s.messages.append({'info': {'role': 'user', 'time': {}},
                               'parts': [{'type': 'text', 'text': content}]})
            lo, hi = self.profile['delay_ms']
            s.pending_until = time.time() + random.uniform(lo, hi) / 1000
            s.outcome = 'error' if random.random() < self.profile['error_rate'] \
                else 'stop'
            if random.random() < self.profile['hang_rate']:
                s.hang_until = time.time() + \
                    self.profile['hang_recover_after_ms'] / 1000

    def get_messages(self, sid: str, directory: str = '') -> list:
        with self._lock:
            return [dict(m) for m in self._sessions[sid].messages]

    def abort_session(self, sid: str, directory: str = '') -> None:
        with self._lock:
            s = self._sessions[sid]
            s.pending_until = None
            s.hang_until = 0.0
            self._append_terminal(s, 'aborted')

    def delete_session(self, sid: str) -> None:
        with self._lock:
            self._sessions.pop(sid, None)

    # ---- 内部 ----
    def _append_terminal(self, s: _Session, finish: str) -> None:
        now_ms = int(time.time() * 1000)
        s.messages.append({
            'info': {'role': 'assistant', 'finish': finish,
                     'time': {'completed': now_ms}},
            'parts': [{'type': 'text', 'text': f'STUB-DONE {finish}'}],
        })

    def _complete_loop(self) -> None:
        while True:
            time.sleep(0.02)
            with self._lock:
                for s in self._sessions.values():
                    if s.done or s.pending_until is None:
                        continue
                    now = time.time()
                    if now < s.hang_until:
                        continue
                    if now >= s.pending_until:
                        s.pending_until = None
                        s.done = True
                        self._append_terminal(s, s.outcome)


class StubRuntime(AgentRuntime):
    kind = 'stub'

    def __init__(self, profile: dict | None = None):
        self.profile = parse_profile(profile)
        self._client = StubClient(self.profile)

    def capabilities(self) -> dict:
        return {'kind': 'stub', 'checkpoint': False, 'pause': False,
                'network_isolation': True}

    def create_session(self, directory: str, title: str = '') -> str:
        return self._client.create_session(directory=directory, title=title)

    def dispatch(self, oc_session_id: str, prompt: str, *, directory: str = '',
                 agent: str = '', model: str = '') -> None:
        self._client.send_prompt_async(oc_session_id, prompt,
                                       directory=directory, agent=agent,
                                       model=model)

    def list_messages(self, oc_session_id: str, directory: str = '') -> list:
        return self._client.get_messages(oc_session_id, directory=directory)

    def get_messages(self, oc_session_id: str, directory: str = '') -> list:
        return self._client.get_messages(oc_session_id, directory=directory)

    def abort(self, oc_session_id: str, directory: str = '') -> None:
        self._client.abort_session(oc_session_id, directory=directory)

    def health(self) -> dict:
        return {'ok': True, 'kind': 'stub'}

    def get_client(self) -> StubClient:
        return self._client
```

`server/utils/runtime/__init__.py` 的 `get_runtime()` 分支改为：

```python
        kind = os.getenv('AI_AGENT_RUNTIME', 'opencode_local')
        if kind == 'stub':
            from utils.runtime.stub import StubRuntime
            _default = StubRuntime()          # profile 由 AI_STUB_PROFILE 注入
        elif kind != 'opencode_local':
            raise RuntimeCapabilityError(
                f'runtime {kind} 尚未在此环境启用（Phase E 按环境开启）')
        else:
            _default = OpenCodeLocalRuntime()
```

StubRuntime 构造里补 profile 读取（构造参数为 None 时）：

```python
    def __init__(self, profile: dict | None = None):
        if profile is None:
            import json as _json, os as _os
            raw = _os.getenv('AI_STUB_PROFILE', '')
            profile = _json.loads(raw) if raw.strip() else None
        self.profile = parse_profile(profile)
        self._client = StubClient(self.profile)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && python -m pytest tests/test_runtime_stub.py -q`
Expected: PASS（7 例）

- [ ] **Step 5: 回归 get_runtime 既有行为**

Run: `cd server && python -m pytest tests/ -q -k "runtime and not stress" 2>/dev/null | tail -3`
Expected: 既有 runtime 相关测试全绿（`AI_AGENT_RUNTIME` 未设时默认 opencode_local 路径不变）。

- [ ] **Step 6: Commit**

```bash
git add server/utils/runtime/stub.py server/utils/runtime/__init__.py server/tests/test_runtime_stub.py
git commit -m "feat(stress): StubRuntime——行为面可编程的压测运行时+消息形状对照单测"
```

---

### Task 2: 压测栈编排 conftest + marker 注册 + 栈冒烟

**Files:**
- Create: `server/tests/stress/conftest.py`、`server/tests/stress/__init__.py`、`server/tests/stress/fakes/__init__.py`
- Modify: `server/pytest.ini`
- Test: `server/tests/stress/test_stack_smoke.py`

**Interfaces:**
- Consumes: Task 1 StubRuntime 注册；`init_db.init_db()`（`INIT_ADMIN_PASSWORD` env 种管理员）；`config.DB_CONFIG`（env `DB_NAME` 覆盖库名）；`FLASK_PORT` env。
- Produces（后续三套件全部消费的 fixtures/助手，签名固定）：
  - `stress_stack`（session fixture）→ `Stack` 对象：`.base='http://127.0.0.1:3092'`、`.token`(str)、`.auth_header`、`.db_dsn(dict)`、`.metrics_dir(Path)`、`.stop()`、`.restart_backend(concurrency:int, profile:dict|None=None)`、`.spawn_serve(port:int)->Popen`、`.kill_serve(proc)`；
  - `create_batch(stack, n_children, *, callback_url=None, api=None) -> batch_id`（staging 上传 1 小文件 + POST /ai/chat/batches）；
  - `wait_terminal(stack, batch_id, timeout_s) -> detail dict`；
  - `db_query(stack, sql, params) -> list[tuple]`（直连 casemanage_stress）；
  - `invariants(stack) -> dict`（计数守恒/孤儿/僵尸三查）；
  - `sampler`（session fixture）→ `Sampler.record(label)` 落 JSON；
  - `admin_login(base) -> token`。

- [ ] **Step 1: pytest.ini 注册 marker 并默认排除**

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_functions = test_*
addopts = -v --tb=short -m "not stress"
markers =
    stress: AI 压力测试（专属压测栈，pytest -m stress 显式触发）
```

- [ ] **Step 2: 写栈冒烟测试** `server/tests/stress/test_stack_smoke.py`

```python
"""压测栈冒烟：建库→init_db→起后端→登录→查询不变量→拆栈。唯一目的 =
证明 conftest 的编排器自身可用；运行约 30-60s。"""
import pytest

pytestmark = pytest.mark.stress


def test_stack_boots_and_login_works(stress_stack):
    import requests
    r = requests.get(f'{stress_stack.base}/health', timeout=5)
    assert r.status_code in (200, 401)          # 活着即可（health 可能带鉴权）
    assert stress_stack.token                    # admin 登录成功
    rows = stress_stack.db_query(
        "SELECT count(*) FROM users WHERE username='admin'")
    assert rows[0][0] == 1


def test_invariants_clean_on_fresh_stack(stress_stack):
    inv = stress_stack.invariants()
    assert inv['orphans'] == 0 and inv['zombie_running'] == 0
```

- [ ] **Step 3: 跑确认失败**

Run: `cd server && python -m pytest tests/stress/test_stack_smoke.py -m stress -q`
Expected: FAIL —— fixture `stress_stack` not found

- [ ] **Step 4: 实现 conftest** `server/tests/stress/conftest.py`

```python
"""AI 压测栈编排（ai-stress spec §2/§5）。

职责单一：建/拆专属压测栈（DB casemanage_stress + 后端:3092 stub 运行时
+ 可选专属 serve:4097），并向套件暴露固定 fixtures。与 dev 零共享——
kill/重启只伤本栈实例。finalizer 保证失败路径也 DROP DB、杀进程树。
"""
import json
import os
import shutil
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

SERVER_DIR = Path(__file__).resolve().parents[1]
STRESS_DB = 'casemanage_stress'
STRESS_PORT = 3092
SERVE_PORT = 4097
ADMIN_USER, ADMIN_PASS = 'admin', 'stress-admin-pw'
METRICS_ROOT = Path(__file__).resolve().parents[2] / 'docs' / 'ai-testing' / 'evidence' / 'stress'


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
        self._serve_procs = []
        self.token = ''
        self.tmp_dirs = []
        self.db_dsn = {**_base_dsn(), 'dbname': STRESS_DB}

    # ---- 生命周期 ----
    def start_backend(self, concurrency: int, profile: dict | None = None,
                      env_extra: dict | None = None):
        """env_extra：混沌层切换真实运行时用（AI_AGENT_RUNTIME/OPENCODE_BASE_URL）。"""
        env = {**os.environ,
               'FLASK_PORT': str(STRESS_PORT),
               'DB_NAME': STRESS_DB,
               'AI_AGENT_RUNTIME': 'stub',
               'AI_BATCH_CONCURRENCY': str(concurrency),
               'INIT_ADMIN_PASSWORD': ADMIN_PASS,
               'PYTHONUTF8': '1'}
        if profile:
            env['AI_STUB_PROFILE'] = json.dumps(profile)
        env.update(env_extra or {})
        self._proc = subprocess.Popen(
            [sys.executable, 'app.py'], cwd=str(SERVER_DIR), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.backend_pid = self._proc.pid
        self._wait_health()

    def restart_backend(self, concurrency: int, profile: dict | None = None,
                        env_extra: dict | None = None):
        self.stop_backend()
        self.start_backend(concurrency, profile, env_extra)

    def stop_backend(self):
        if self._proc and self._proc.poll() is None:
            for child in psutil.Process(self._proc.pid).children(recursive=True):
                child.kill()
            self._proc.kill()
            self._proc.wait(timeout=10)
        self._proc = None

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
        from utils.opencode_launch import serve_env
        gd = tempfile.mkdtemp(prefix='stress-oc-global-')
        self.tmp_dirs.append(gd)
        env = serve_env({**os.environ, 'OPENCODE_GLOBAL_DIR': gd})
        proc = subprocess.Popen(
            ['opencode', 'serve', '--port', str(port)], cwd=str(Path.home()),
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
        orphans = self.db_query(
            "SELECT count(*) FROM ai_chat_sessions s WHERE s.batch_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM ai_chat_batches b WHERE b.id=s.batch_id)")[0][0]
        zombie = self.db_query(
            "SELECT count(*) FROM ai_chat_sessions WHERE status='running' "
            "AND last_active_at < NOW() - interval '10 minutes'")[0][0]
        bad = self.db_query(
            "SELECT count(*) FROM ai_chat_batches WHERE total <> done+failed+cancelled "
            "AND status IN ('completed','failed','partial')")[0][0]
        return {'orphans': orphans, 'zombie_running': zombie,
                'counter_violations': bad}

    # ---- 拆栈 ----
    def teardown(self):
        self.stop_backend()
        for p in list(self._serve_procs):
            self.kill_serve(p)
        for d in self.tmp_dirs:
            shutil.rmtree(d, ignore_errors=True)
        _drop_db(STRESS_DB)


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
    subprocess.run([sys.executable, 'init_db.py'], cwd=str(SERVER_DIR),
                   env=env, check=True, capture_output=True, timeout=300)


@pytest.fixture(scope='session')
def stress_stack():
    stack = Stack()
    _create_db()
    stack.start_backend(concurrency=3)
    sampler = Sampler(stack)
    yield stack
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

    已核实形状（routes/ai_chat_batches.py:66-143, routes/open_api_batches.py:147/358）：
    - 内部 staging：POST form {file, upload_session_id} → 201 {name, path}
    - 内部建批：files=[{name, path}], ... → 201（children=len(files)）
    - 开放上传：POST /v1/ai-batches/uploads（同 form）→ 201 同形状
    - 开放建批：POST /v1/ai-batches {name, prompt, files, callbackUrl}（files 可空=空壳）
    """
    s = requests.Session()
    prompt = '直接回复:STRESS-OK。不要读取文件,不要执行命令。'
    name = f'STRESS-{time.time_ns()}'
    if api:
        base, hdr = api['base'], api['headers']
        up = s.post(f'{base}/v1/ai-batches/uploads', headers=hdr,
                    files={'file': ('stress-in.txt', b'STRESS-INPUT', 'text/plain')},
                    data={'upload_session_id': name}, timeout=30)
        up.raise_for_status()
        staged = up.json()                       # {name, path}
        body = {'name': name, 'prompt': prompt,
                'files': [staged] * n_children}
        if callback_url:
            body['callbackUrl'] = callback_url
        r = s.post(f'{base}/v1/ai-batches', headers=hdr, json=body, timeout=30)
        r.raise_for_status()
        return r.json()['id']
    up = s.post(f'{stack.base}/ai/chat/batches/staging/upload',
                headers=stack.auth_header,
                files={'file': ('stress-in.txt', b'STRESS-INPUT', 'text/plain')},
                data={'upload_session_id': name}, timeout=30)
    up.raise_for_status()
    staged = up.json()                           # {name, path}
    body = {'name': name, 'prompt': prompt,
            'files': [staged] * min(n_children, 50)}
    r = s.post(f'{stack.base}/ai/chat/batches', headers=stack.auth_header,
               json=body, timeout=30)
    r.raise_for_status()
    return r.json()['id']


def append_files(stack, batch_id: str, n: int, *, api: dict):
    """空壳批 append 填充（开放 API POST /v1/ai-batches/<id>/append）。"""
    s = requests.Session()
    up = s.post(f'{api["base"]}/v1/ai-batches/uploads', headers=api['headers'],
                files={'file': ('stress-append.txt', b'STRESS-APPEND', 'text/plain')},
                data={'upload_session_id': f'append-{time.time_ns()}'}, timeout=30)
    up.raise_for_status()
    r = s.post(f'{api["base"]}/v1/ai-batches/{batch_id}/append',
               headers=api['headers'],
               json={'files': [up.json()] * n}, timeout=30)
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
```

`server/tests/stress/__init__.py`、`server/tests/stress/fakes/__init__.py` 均为空文件。

- [ ] **Step 5: 跑冒烟确认通过**

Run: `cd server && python -m pytest tests/stress/test_stack_smoke.py -m stress -q`
Expected: PASS（2 例，约 60s——含建库/init_db/后端启动）
注意：若 :3092 或端口占用/`opencode` 不在 PATH 等环境问题，先修环境再绿；conftest 顶部可加「:3092 已被占用即报可读错误」的前置检查。

- [ ] **Step 6: 确认默认跑不触发压测**

Run: `cd server && python -m pytest tests/test_runtime_stub.py -q 2>&1 | tail -2`
Expected: 正常执行（marker 排除不影响非 stress 用例）。

- [ ] **Step 7: Commit**

```bash
git add server/tests/stress/ server/pytest.ini
git commit -m "feat(stress): 压测栈编排 conftest——专属DB/后端/采样器/finalizer 拆栈+marker 默认排除"
```

---

### Task 3: 容量阶梯套件

**Files:**
- Create: `server/tests/stress/test_capacity_ladder.py`

**Interfaces:**
- Consumes: Task 2 全部 fixtures/助手；Task 1 profile。
- Produces: `docs/ai-testing/evidence/stress/<ts>-capacity-L<并发>.json`（每级采样）+ 终局 md 结论（§Task 6 渲染器）。

- [ ] **Step 1: 写套件**（marker stress；阶梯常量与升/停规则照 spec §4.1）

```python
"""容量阶梯：并发 5→10→20→35→50，每级 100 批 × 5 children = 500 children
（全阶梯累计 500 批，对齐 spec「批次累计约 500」量级）。附 1 个空壳批
（开放 API files:[] 创建→append 填充）覆盖 0 文件路径。
升/停规则（spec §4.1）：成功率≥99% 且不变量全绿→升级；首次失败即停，
容量结论=最后全绿级。全程 stub（零 token）。"""
import time

import pytest
import requests

from tests.stress.conftest import create_batch, append_files, wait_terminal

pytestmark = pytest.mark.stress

LADDER = [5, 10, 20, 35, 50]
BATCHES_PER_LEVEL = 100
CHILDREN_PER_BATCH = 5
SUCCESS_FLOOR = 0.99


def _make_api_key(stress_stack) -> str:
    r = requests.post(f'{stress_stack.base}/apiKeys',
                      headers=stress_stack.auth_header,
                      json={'name': 'STRESS-capacity'}, timeout=10)
    r.raise_for_status()
    return r.json()['key']                       # routes/api_keys.py:71


def _empty_shell_scenario(stress_stack):
    """空壳批：开放 API 0 文件创建 → append 2 文件 → 照常收敛。"""
    key = _make_api_key(stress_stack)
    api = {'base': stress_stack.base,
           'headers': {'X-API-Key': key, 'Authorization': stress_stack.auth_header['Authorization']}}
    bid = create_batch(stress_stack, 0, api=api)         # 0 文件空壳（bf68849）
    append_files(stress_stack, bid, 2, api=api)
    d = wait_terminal(stress_stack, bid, timeout_s=300)
    assert len(d.get('sessions', [])) == 2, d.get('sessions')
    assert d['batch']['status'] == 'completed'


def _run_level(stress_stack, sampler, level: int) -> dict:
    stress_stack.restart_backend(
        concurrency=level,
        profile={'delay_ms': [2000, 8000], 'error_rate': 0.0,
                 'hang_rate': 0.0})
    sampler.record(f'capacity-L{level}')
    batch_ids = [create_batch(stress_stack, CHILDREN_PER_BATCH)
                 for _ in range(BATCHES_PER_LEVEL)]
    t0 = time.time()
    details = [wait_terminal(stress_stack, b, timeout_s=1200) for b in batch_ids]
    wall = time.time() - t0
    children = [c for d in details for c in d.get('sessions', [])]
    done = sum(1 for c in children if c['status'] == 'completed')
    failed = sum(1 for c in children if c['status'] == 'failed')
    cancelled = sum(1 for c in children if c['status'] == 'cancelled')
    return {'level': level, 'batches': len(batch_ids),
            'children': len(children), 'done': done,
            'failed': failed, 'cancelled': cancelled,
            'success_rate': done / max(1, len(children)),
            'throughput_cpm': len(children) / wall * 60,
            'invariants': stress_stack.invariants(), 'wall_s': wall}


def test_capacity_ladder(stress_stack, sampler):
    levels = []
    capacity = None
    for level in LADDER:
        r = _run_level(stress_stack, sampler, level)
        levels.append(r)
        inv = r['invariants']
        green = (r['success_rate'] >= SUCCESS_FLOOR
                 and inv['orphans'] == 0 and inv['zombie_running'] == 0
                 and inv['counter_violations'] == 0)
        if green:
            capacity = level
        else:
            break                    # 首个失败级：记录失败形态后停
    assert capacity is not None, f'连 L{LADDER[0]} 都未通过: {levels}'
    _empty_shell_scenario(stress_stack)
    # 结论落盘（Task 6 渲染器复用同一 JSON）
    import json
    from pathlib import Path
    out = Path('docs/ai-testing/evidence/stress')
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{time.strftime('%Y%m%d-%H%M%S')}-capacity.json").write_text(
        json.dumps({'capacity': capacity, 'levels': levels},
                   ensure_ascii=False, indent=1), encoding='utf-8')
```

注：开放 API 请求头只需 `X-API-Key`（uploads/create/append 都走 key 鉴权）。

- [ ] **Step 2: 跑套件（全阶梯约 30-60 分钟）**

Run: `cd server && python -m pytest tests/stress/test_capacity_ladder.py -m stress -q`
Expected: PASS（或明确断言失败——若 L5 即失败属产品缺陷，停下报告，不属测试代码问题）

- [ ] **Step 3: 检查产物**

Run: `ls docs/ai-testing/evidence/stress/ | tail -5`
Expected: capacity.json + 各级采样 JSON 存在且 label 正确。

- [ ] **Step 4: Commit**

```bash
git add server/tests/stress/test_capacity_ladder.py
git commit -m "feat(stress): 容量阶梯套件——5→50 并发升停规则+计数守恒断言"
```

---

### Task 4: 混沌注入套件（专属真 serve 层）

**Files:**
- Create: `server/tests/stress/test_chaos_injection.py`
- Create: `server/tests/stress/fakes/callback_target.py`

**Interfaces:**
- Consumes: Task 2 `stress_stack.spawn_serve/kill_serve`、`restart_backend`；开放 API 建 key+批（复用 `server/tests/test_open_api_batches_crud.py` 的建 key/HDR 模式——那是开放 API 鉴权的既定测试模式）。
- Produces: `fakes/callback_target.py` 提供 `start(port=3098, behavior='ok') -> (thread, state)`，`behavior ∈ {'ok','503','timeout'}`，`state['deliveries']` 收到请求计数、`state['bodies']` 原始体列表；C1–C4 场景判定。

- [ ] **Step 1: 实现假回调目标** `server/tests/stress/fakes/callback_target.py`

```python
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
```

- [ ] **Step 2: 写混沌套件** `server/tests/stress/test_chaos_injection.py`

```python
"""混沌注入（spec §4.2，专属真 serve 层，≤3 并发）：
C1 kill serve→子任务 failed 非僵尸→重启续跑
C2 kill -9 worker→租约接管+fencing 递增+无双重执行
C3 outbox 503/超时→退避→dead_letter→needs_review 不自动重放
C4 有活动会话时 restart 无 force→409
C1/C2 期间后端 runtime=opencode_local 指向专属 serve:4097。"""
import time

import pytest
import requests

from tests.stress.conftest import create_batch, wait_terminal
from tests.stress.fakes import callback_target

pytestmark = pytest.mark.stress

REAL_ENV = {'AI_AGENT_RUNTIME': 'opencode_local',
            'OPENCODE_BASE_URL': 'http://127.0.0.1:4097'}


def _switch_to_real_serve(stress_stack):
    """后端切到专属真 serve:4097（opencode_local）；dev 的 4096 不受影响。
    spawn_serve 幂等（存活实例直接复用），本助手可被多个场景重复调用。"""
    stress_stack.spawn_serve()     # :4097
    stress_stack.restart_backend(concurrency=3, env_extra=REAL_ENV)


def test_c1_kill_serve_children_fail_then_recover(stress_stack, sampler):
    _switch_to_real_serve(stress_stack)
    sampler.record('chaos-C1')
    bid = create_batch(stress_stack, 2)
    time.sleep(10)                                  # 子任务进入 running
    serve_proc = stress_stack._serve_procs[-1]
    stress_stack.kill_serve(serve_proc)             # 注入：kill 专属 serve
    d = wait_terminal(stress_stack, bid, timeout_s=300)
    assert all(c['status'] == 'failed' for c in d['sessions']), d['sessions']
    assert all(c.get('error_message') for c in d['sessions'])   # 非僵尸：有错误信息
    stress_stack.spawn_serve()                      # 重启后队列继续消化
    bid2 = create_batch(stress_stack, 1)
    d2 = wait_terminal(stress_stack, bid2, timeout_s=420)
    assert d2['batch']['status'] == 'completed'


def test_c2_worker_kill_lease_takeover_no_double_exec(stress_stack, sampler):
    _switch_to_real_serve(stress_stack)             # 幂等：已在则复用
    sampler.record('chaos-C2')
    bid = create_batch(stress_stack, 2)
    time.sleep(8)
    fence_before = stress_stack.db_query(
        "SELECT fencing_token FROM ai_chat_sessions "
        "WHERE batch_id=%s AND fencing_token IS NOT NULL", (bid,))
    stress_stack.stop_backend()                     # kill -9 等价：进程直接杀
    # 接管实例必须同运行时（真 serve 的 oc_session_id 在 stub 客户端里不存在）
    stress_stack.start_backend(concurrency=3, env_extra=REAL_ENV)
    d = wait_terminal(stress_stack, bid, timeout_s=420)
    assert d['batch']['status'] in ('completed', 'partial', 'failed')
    fence_after = stress_stack.db_query(
        "SELECT fencing_token FROM ai_chat_sessions "
        "WHERE batch_id=%s AND fencing_token IS NOT NULL", (bid,))
    if fence_before and fence_after:
        assert fence_after[0][0] >= fence_before[0][0]   # 接管则 token 不回退
    inv = stress_stack.invariants()
    assert inv['zombie_running'] == 0 and inv['orphans'] == 0
    # 无双重执行：每子任务消息轮数=派发轮数（重复执行会翻倍）
    dup = stress_stack.db_query(
        "SELECT s.id, count(*) FROM ai_chat_sessions s "
        "JOIN ai_chat_messages m ON m.session_id=s.id "
        "WHERE s.batch_id=%s AND m.role='user' GROUP BY s.id "
        "HAVING count(*) > 1", (bid,))
    assert dup == [], f'疑似双重执行: {dup}'


def test_c3_outbox_503_then_ok_backoff_dead_letter(stress_stack, sampler):
    _switch_to_real_serve(stress_stack)
    sampler.record('chaos-C3')
    key = _make_api_key(stress_stack)
    hdr = {'X-API-Key': key}
    api = {'base': stress_stack.base, 'headers': hdr}
    srv, state = callback_target.start(behavior='503')
    try:
        bid = create_batch(stress_stack, 1, callback_url='http://127.0.0.1:3098/cb',
                           api=api)
        wait_terminal(stress_stack, bid, timeout_s=420)
        time.sleep(60)                              # 等 outbox 重试进入退避
        rows = stress_stack.db_query(
            "SELECT status, attempt_count FROM ai_delivery_outbox "
            "WHERE batch_id=%s", (bid,))
        assert rows and rows[0][1] >= 2, rows       # 至少重试过 2 次
    finally:
        callback_target.shutdown(srv)
    inv = stress_stack.invariants()
    assert inv['orphans'] == 0


def _make_api_key(stress_stack) -> str:
    r = requests.post(f'{stress_stack.base}/apiKeys',
                      headers=stress_stack.auth_header,
                      json={'name': 'STRESS-c3'}, timeout=10)
    r.raise_for_status()
    return r.json()['key']                          # routes/api_keys.py:71


def test_c4_restart_guard_refuses_without_force(stress_stack):
    _switch_to_real_serve(stress_stack)
    bid = create_batch(stress_stack, 1)
    time.sleep(8)                                   # 制造活动会话窗口
    r = requests.post(f'{stress_stack.base}/ai/opencode/restart',
                      headers=stress_stack.auth_header, json={}, timeout=30)
    assert r.status_code == 409
    assert r.json()['code'] == 'ACTIVE_WORKLOAD'
    wait_terminal(stress_stack, bid, timeout_s=420)
```

- [ ] **Step 3: 跑套件（真模型，约 20-40 分钟）**

Run: `cd server && python -m pytest tests/stress/test_chaos_injection.py -m stress -q`
Expected: PASS；若 C1 断言失败（子任务僵尸 running），属产品缺陷线索，报告并停。

- [ ] **Step 4: Commit**

```bash
git add server/tests/stress/test_chaos_injection.py server/tests/stress/fakes/
git commit -m "feat(stress): 混沌注入套件——kill serve/租约接管/outbox 退避/重启守卫"
```

---

### Task 5: 读路径压载套件

**Files:**
- Create: `server/tests/stress/test_readpath_load.py`

**Interfaces:**
- Consumes: Task 2 fixtures/助手、Task 4 `callback_target`（R4 复用）、Task 3 遗留的容量数据（R3 大列表）。
- Produces: R1–R4 延迟/吞吐 JSON。

- [ ] **Step 1: 写套件**

```python
"""读路径压载（spec §4.3）：SSE 并发连接 / 事件分页 / 大列表 / outbox 洪峰。
全部 stub 层（回 stub 后端即可），不依赖真模型。"""
import threading
import time

import psycopg2
import psycopg2.extras
import pytest
import requests

from tests.stress.conftest import create_batch, wait_terminal
from tests.stress.fakes import callback_target

pytestmark = pytest.mark.stress


def test_r1_sse_50_connections_5min(stress_stack, sampler):
    sampler.record('readpath-R1-start')
    bid = create_batch(stress_stack, 50)            # 50 children → 事件流有料
    errors = []
    stop = threading.Event()

    def watch():
        try:
            with requests.get(
                    f'{stress_stack.base}/ai/chat/batches/{bid}/events',
                    headers=stress_stack.auth_header, stream=True,
                    timeout=(5, 300)) as r:
                if r.status_code != 200:            # 线程内不许 assert（不冒泡）
                    errors.append(f'SSE status {r.status_code}')
                    return
                for _ in r.iter_lines(chunk_size=1):
                    if stop.is_set():
                        return
        except Exception as e:                      # 断流/超时都算失败
            errors.append(e)

    threads = [threading.Thread(target=watch, daemon=True) for _ in range(50)]
    for t in threads:
        t.start()
    time.sleep(300)                                 # 持续 5 分钟
    stop.set()
    for t in threads:
        t.join(timeout=10)
    wait_terminal(stress_stack, bid, timeout_s=600)
    sampler.record('readpath-R1-end')
    assert not errors, f'SSE 异常: {errors[:3]}'
    inv = stress_stack.invariants()
    assert inv['zombie_running'] == 0


def test_r2_events_pagination_p95(stress_stack, sampler):
    sampler.record('readpath-R2')
    bid = create_batch(stress_stack, 20)
    wait_terminal(stress_stack, bid, timeout_s=600)
    lat = []
    for after in range(0, 200, 20):
        t0 = time.time()
        r = requests.get(
            f'{stress_stack.base}/ai/chat/batches/{bid}/events',
            headers=stress_stack.auth_header,
            params={'after_seq': after, 'limit': 20}, timeout=10)
        lat.append(time.time() - t0)
        assert r.status_code == 200
    lat.sort()
    p95 = lat[int(len(lat) * 0.95) - 1]
    _dump('r2-events-p95', {'p95_s': p95, 'samples': lat})
    assert p95 < 2.0, f'events 分页 P95 {p95:.2f}s 超 2s'


def test_r3_admin_list_500_batches(stress_stack, sampler):
    sampler.record('readpath-R3')
    lat = []
    for page in range(5):
        t0 = time.time()
        r = requests.get(f'{stress_stack.base}/ai/chat/admin/batches',
                         headers=stress_stack.auth_header,
                         params={'page': page + 1, 'pageSize': 100}, timeout=15)
        lat.append(time.time() - t0)
        assert r.status_code == 200
    lat.sort()
    _dump('r3-admin-list', {'samples': lat})
    assert lat[-1] < 5.0, f'管理列表最大延迟 {lat[-1]:.2f}s 超 5s'


def test_r4_outbox_flood_10k(stress_stack, sampler):
    sampler.record('readpath-R4')
    srv, state = callback_target.start(behavior='ok')
    try:
        t0 = time.time()
        now = time.time_ns()
        flood = [(f'obx-stress-{now}-{i}', f'evt-{now}-{i}')
                 for i in range(10000)]
        conn = psycopg2.connect(**stress_stack.db_dsn)
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(
                cur,
                "INSERT INTO ai_delivery_outbox (id, event_id, batch_id, "
                "event_type, target_url, payload, signature, idempotency_key, "
                "status, next_retry_at, attempt_count) VALUES (%s,%s,%s,'t',"
                "'http://127.0.0.1:3098/cb','{}','',%s,'pending',NOW(),0)",
                [(o, e, f'stress-flood-{now % 100000}', f'k-{e}')
                 for o, e in flood])
        conn.commit()
        conn.close()
        deadline = time.time() + 1800               # 1 万行排空给 30 分钟
        while time.time() < deadline:
            if state['deliveries'] >= 10000:
                break
            time.sleep(10)
        wall = time.time() - t0
        _dump('r4-outbox-flood', {'delivered': state['deliveries'],
                                  'wall_s': wall,
                                  'throughput_per_min':
                                      state['deliveries'] / max(1, wall) * 60})
        assert state['deliveries'] >= 10000
        # 幂等：带 Idempotency 头的投递无重复 key（重放会撞 key）
        keys = [k for k in state['idem_keys'] if k]
        assert len(keys) == len(set(keys)), '检测到重复投递（幂等键重复）'
    finally:
        callback_target.shutdown(srv)


def _dump(name, payload):
    import json
    from pathlib import Path
    out = Path('docs/ai-testing/evidence/stress')
    out.mkdir(parents=True, exist_ok=True)
    (out / f'{time.strftime("%Y%m%d-%H%M%S")}-{name}.json').write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
```

- [ ] **Step 2: 跑套件（约 40 分钟，R1 占 5 分钟 + R4 排空）**

Run: `cd server && python -m pytest tests/stress/test_readpath_load.py -m stress -q`
Expected: PASS；R4 排空吞吐写入 JSON。R3 依赖 Task 3 遗留的批次量（先跑容量或临时建 100+ 批次均可——`create_batch` 循环 100 次即可）。

- [ ] **Step 3: Commit**

```bash
git add server/tests/stress/test_readpath_load.py
git commit -m "feat(stress): 读路径压载套件——SSE 50 连接/事件分页/大列表/outbox 万行洪峰"
```

---

### Task 6: 报告渲染 + 使用说明 + 收口验证

**Files:**
- Create: `server/tests/stress/report.py`、`server/tests/stress/README.md`
- Test: `server/tests/test_stress_report.py`

**Interfaces:**
- Consumes: Task 3 capacity.json 结构（`{capacity, levels:[{level,done,failed,cancelled,success_rate,throughput_cpm,invariants}]}`）。
- Produces: `render_capacity_report(data: dict) -> str`（md 表格+结论行）；`README.md` 记录运行方式与产物位置。

- [ ] **Step 1: 写失败测试** `server/tests/test_stress_report.py`

```python
from tests.stress.report import render_capacity_report


def test_renders_table_and_conclusion():
    data = {'capacity': 20, 'levels': [
        {'level': 5, 'done': 100, 'failed': 0, 'cancelled': 0,
         'success_rate': 1.0, 'throughput_cpm': 30.5,
         'invariants': {'orphans': 0, 'zombie_running': 0,
                        'counter_violations': 0}},
        {'level': 10, 'done': 99, 'failed': 1, 'cancelled': 0,
         'success_rate': 0.99, 'throughput_cpm': 41.2,
         'invariants': {'orphans': 0, 'zombie_running': 0,
                        'counter_violations': 0}},
        {'level': 20, 'done': 90, 'failed': 10, 'cancelled': 0,
         'success_rate': 0.9, 'throughput_cpm': 38.0,
         'invariants': {'orphans': 2, 'zombie_running': 1,
                        'counter_violations': 0}},
    ]}
    md = render_capacity_report(data)
    assert '容量结论: 20 并发' in md
    assert '| 5 |' in md and '| 20 |' in md
    assert 'L20 未达标' in md
```

- [ ] **Step 2: 跑确认失败** —— `cd server && python -m pytest tests/test_stress_report.py -q`，FAIL 模块不存在。

- [ ] **Step 3: 实现** `server/tests/stress/report.py`

```python
"""压测结果 md 渲染（spec §5）。输入 = capacity 套件落盘的 JSON。"""


def render_capacity_report(data: dict) -> str:
    lines = ['# AI 容量阶梯报告', '',
             f"**容量结论: {data['capacity']} 并发**（最后全绿级）", '',
             '| 级 | done | failed | cancelled | 成功率 | 吞吐(cpm) | 不变量 |',
             '|----|------|--------|-----------|--------|-----------|--------|']
    for lv in data['levels']:
        inv = lv['invariants']
        inv_txt = '绿' if (inv['orphans'] == 0 and inv['zombie_running'] == 0
                           and inv['counter_violations'] == 0) else \
            f"异常(孤儿{inv['orphans']}/僵尸{inv['zombie_running']}/计数{inv['counter_violations']})"
        mark = ' ✅' if lv['success_rate'] >= 0.99 else ' ❌ L%d 未达标' % lv['level']
        lines.append(
            f"| {lv['level']}{mark} | {lv['done']} | {lv['failed']} | "
            f"{lv['cancelled']} | {lv['success_rate']:.1%} | "
            f"{lv['throughput_cpm']:.1f} | {inv_txt} |")
    return '\n'.join(lines)
```

- [ ] **Step 4: 跑确认通过 + 全量非 stress 回归**

Run: `cd server && python -m pytest tests/test_stress_report.py tests/test_runtime_stub.py -q`
Expected: PASS。
Run: `cd server && python -m pytest tests/ -q 2>&1 | tail -2`（默认排除 stress，历史基线不回归）
Expected: 与基线一致全绿。

- [ ] **Step 5: 使用说明** `server/tests/stress/README.md`

````markdown
# AI 压力测试（ai-stress spec 落地）

运行（默认 pytest 已排除本目录，需显式 `-m stress`）：

```bash
cd server
python -m pytest tests/stress/test_stack_smoke.py -m stress -q   # 栈冒烟 ~1min
python -m pytest tests/stress/test_capacity_ladder.py -m stress -q   # 容量阶梯 ~30-60min
python -m pytest tests/stress/test_chaos_injection.py -m stress -q   # 混沌（真模型）~20-40min
python -m pytest tests/stress/test_readpath_load.py -m stress -q     # 读路径 ~40min
```

- 专属栈：DB `casemanage_stress`（用完 DROP）、后端 :3092（stub 运行时）、
  混沌 serve :4097（临时 GLOBAL_DIR）。与 dev（3002/4096/3003/5173）零共享。
- 产物：`docs/ai-testing/evidence/stress/*.json`（采样/结论）；
  容量结论用 `python -c "from tests.stress.report import render_capacity_report; ..."` 渲染。
- 前置：Postgres 可建库；`opencode` 在 PATH（混沌层）；:3092/:3098/:4097 空闲。
````

- [ ] **Step 6: Commit**

```bash
git add server/tests/stress/report.py server/tests/stress/README.md server/tests/test_stress_report.py
git commit -m "feat(stress): 容量报告渲染+压测使用说明+默认排除收口验证"
```
