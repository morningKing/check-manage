"""能力压测套件共享助手（2026-10-05 capability-stress spec §5）。
全部打压测栈 :3092；DB 操作一律走 stack.db_dsn（casemanage_stress），
绝不触碰 dev 库。"""
import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass

import psycopg2
import requests


@dataclass
class Frame:
    event: str
    data: dict
    eid: str | None


def create_interactive_session(stack) -> str:
    r = requests.post(f'{stack.base}/ai/chat/sessions',
                      headers=stack.auth_header, json={}, timeout=15)
    r.raise_for_status()
    return r.json()['id']


def send_message(stack, sid, content) -> None:
    r = requests.post(f'{stack.base}/ai/chat/sessions/{sid}/messages',
                      headers=stack.auth_header,
                      json={'content': content}, timeout=15)
    r.raise_for_status()


def open_sse(stack, path, params=None):
    """SSE 长连接 contextmanager。用法：
    with open_sse(stack, '/ai/chat/batches/events', {'ids': bid}) as frames:
        for f in frames: ...   # 生成器内部 while True，用方负责 break
    """
    @contextmanager
    def _ctx():
        r = requests.get(f'{stack.base}{path}', params=params or {},
                         headers=stack.auth_header, stream=True,
                         timeout=(5, 600))
        try:
            assert r.status_code == 200, f'SSE status {r.status_code}'

            def _iter():
                ev, eid, data_buf = None, None, []
                for raw in r.iter_lines(chunk_size=1):
                    line = raw.decode('utf-8') if isinstance(raw, bytes) else raw
                    if line == '':
                        if data_buf:
                            try:
                                payload = json.loads(''.join(data_buf))
                            except json.JSONDecodeError:
                                payload = {'_raw': ''.join(data_buf)}
                            yield Frame(ev or '', payload, eid)
                        ev, eid, data_buf = None, None, []
                    elif line.startswith('event:'):
                        ev = line[len('event:'):].strip()
                    elif line.startswith('id:'):
                        eid = line[len('id:'):].strip()
                    elif line.startswith('data:'):
                        data_buf.append(line[len('data:'):].strip())
            yield _iter()
        finally:
            r.close()
    return _ctx()


def stress_db(stack):
    @contextmanager
    def _ctx():
        conn = psycopg2.connect(**stack.db_dsn)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return _ctx


def register_expectations(stack, sid, checks) -> int:
    """生产登记函数 + 压测库连接：与路由/引擎同一条登记代码路径。"""
    from utils import agent_ledger
    getdb = stress_db(stack)
    return agent_ledger.register_session_expectations(
        sid, checks, source='stress', get_db=getdb)


def collect_sse(stack, path, params, stop: threading.Event,
                sink: list, on_predicate=None):
    """线程 target：持续收帧进 sink，on_predicate(Frame) 为真或 stop 置位即回。"""
    try:
        with open_sse(stack, path, params) as frames:
            for f in frames:
                sink.append(f)
                if on_predicate and on_predicate(f):
                    return
                if stop.is_set():
                    return
    except Exception as e:                       # 线程内不许 assert
        sink.append(e)
