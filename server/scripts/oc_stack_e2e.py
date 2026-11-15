# -*- coding: utf-8 -*-
"""双栈真机 E2E：V1(:4096) 与 V2(:4196) 同题对跑（LongCat 2.5 Free）。

每栈流程：create_session → send_prompt_async(LongCat) → SSE 帧收集线程
+ REST 轮询收口（finish/time.completed，两栈经适配层同形）→ 断言助手
回复非空并记录实服模型 → delete_session 清理。

前置：两栈 serve 已起（V1 dev 栈 / V2 经 ai.opencode.opencode_launch 拉起）；
本脚本不属于 pytest 套件（需真实 LLM 渠道），运维/验收手动跑：
    python scripts/oc_stack_e2e.py            # 两栈都跑
    python scripts/oc_stack_e2e.py --stack v2 # 单栈
"""
import argparse
import sys
import threading
import time

sys.path.insert(0, '.')  # server/ cwd

from config import OPENCODE_BASE_URL  # noqa: E402
from ai.opencode.opencode_client import OpenCodeClient  # noqa: E402
from ai.opencode.opencode_launch import oc2_base_url, oc2_known_password  # noqa: E402
from ai.opencode.opencode_client_v2 import OpenCodeClientV2  # noqa: E402

MODEL = 'opencode/longcat-2.5-free'
PROMPT = 'Reply with exactly: STACK-E2E-OK'
TURN_TIMEOUT = 120.0
WORK = 'C:/Users/admin/tmp/oc2-probe/work'


def _finished(msgs):
    """两栈同形的完成判定（与 batch facade 同源逻辑）：末条 assistant 有
    终态 finish + time.completed。"""
    for m in reversed(msgs):
        info = m.get('info') or {}
        if info.get('role') == 'assistant':
            finish = info.get('finish')
            done = (info.get('time') or {}).get('completed')
            return bool(done) and finish not in (None, '',
                                                 'tool-calls', 'tool_use')
    return False


def _collect_sse(client, directory, frames, stop_evt):
    try:
        for f in client.subscribe_events(directory=directory,
                                         read_timeout=TURN_TIMEOUT):
            if stop_evt.is_set():
                return
            frames.append(f)
            if f['event'] in ('session.idle', 'session.error'):
                return
    except Exception as e:  # noqa: BLE001
        frames.append({'event': '_stream_end',
                       'data': {'error': '%s: %s' % (type(e).__name__, e)}})


def run_stack(name, client):
    print('==== %s (%s) ====' % (name, client.base_url))
    t0 = time.monotonic()
    sid = client.create_session(directory=WORK,
                                title='stack-e2e-%s' % name)
    print('  session:', sid)
    frames = []
    stop_evt = threading.Event()
    listener = threading.Thread(
        target=_collect_sse, args=(client, WORK, frames, stop_evt), daemon=True)
    listener.start()
    time.sleep(1.0)
    client.send_prompt_async(sid, PROMPT, model=MODEL, directory=WORK)
    print('  prompt sent (model=%s)' % MODEL)
    msgs = []
    deadline = time.monotonic() + TURN_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(2)
        msgs = client.get_messages(sid, directory=WORK) or []
        if _finished(msgs):
            break
    stop_evt.set()
    elapsed = time.monotonic() - t0
    if not _finished(msgs):
        print('  RESULT: TIMEOUT %.0fs — frames=%d msgs=%d'
              % (elapsed, len(frames), len(msgs)))
        client.delete_session(sid)
        return False
    assistant = [m for m in msgs
                 if (m.get('info') or {}).get('role') == 'assistant']
    last = assistant[-1] if assistant else {}
    info = last.get('info') or {}
    texts = ''.join(p.get('text') or '' for p in (last.get('parts') or [])
                    if p.get('type') == 'text')
    served = '%s/%s' % (info.get('providerID') or '?',
                        info.get('modelID') or '?')
    idle = [f for f in frames if f['event'] == 'session.idle']
    err = [f for f in frames if f['event'] == 'session.error']
    part_frames = [f for f in frames if f['event'] == 'message.part.updated']
    print('  served model:', served, '| finish:', info.get('finish'),
          '| cost:', info.get('cost'))
    print('  reply: %r' % texts[:120])
    print('  sse frames: %d (part=%d idle=%d error=%d)'
          % (len(frames), len(part_frames), len(idle), len(err)))
    ok = bool(texts.strip()) and bool(idle) and not err
    print('  RESULT: %s (%.1fs)' % ('PASS' if ok else 'FAIL', elapsed))
    client.delete_session(sid)
    print('  cleanup: session deleted')
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stack', choices=['v1', 'v2', 'both'], default='both')
    args = ap.parse_args()
    results = {}
    if args.stack in ('v2', 'both'):
        results['v2'] = run_stack(
            'V2/OpenCode-2.0',
            OpenCodeClientV2(oc2_base_url(), password=oc2_known_password()))
    if args.stack in ('v1', 'both'):
        results['v1'] = run_stack(
            'V1/OpenCode-1.x', OpenCodeClient(OPENCODE_BASE_URL))
    print('==== SUMMARY ====')
    for k, v in results.items():
        print('  %s: %s' % (k, 'PASS' if v else 'FAIL'))
    sys.exit(0 if all(results.values()) else 1)


if __name__ == '__main__':
    main()
