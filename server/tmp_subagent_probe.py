# -*- coding: utf-8 -*-
"""真机探针：API 全链路跑一轮带子代理委派的 AI 会话，然后检查
subtask 气泡数据源的 parts 形状（reasoning 是否落库、是否被并进 text）。

用法：python tmp_subagent_probe.py
"""
import json
import sys
import time

import requests
import psycopg2

BASE = 'http://127.0.0.1:3002'

PROMPT = (
    '请用 task 工具委派一个 general 子代理去做这件事：让子代理用 read 工具读取'
    '工作区里的 AGENTS.md 文件的前 5 行并原样返回。你拿到子代理结果后，'
    '把结果总结成一句话告诉我。必须真的委派子代理，不要自己直接读文件。'
)


def main():
    s = requests.Session()
    r = s.post(f'{BASE}/auth/login',
               json={'username': 'admin', 'password': 'admin123'}, timeout=15)
    r.raise_for_status()
    token = r.json().get('token') or r.json().get('accessToken')
    print('login ok, token len =', len(token or ''))
    s.headers['Authorization'] = f'Bearer {token}'

    r = s.post(f'{BASE}/ai/chat/sessions', json={}, timeout=60)
    r.raise_for_status()
    sid = r.json()['id']
    print('session created:', sid)

    t0 = time.time()
    r = s.post(f'{BASE}/ai/chat/sessions/{sid}/messages',
               json={'content': PROMPT}, timeout=30)
    print('send status:', r.status_code, str(r.json())[:200] if r.status_code != 200 else 'ok')

    # 轮询子任务直到终态（最多 5 分钟）
    subtasks = []
    deadline = time.time() + 300
    while time.time() < deadline:
        try:
            r = s.get(f'{BASE}/ai/chat/sessions/{sid}/subtasks', timeout=15)
            subtasks = r.json() if r.status_code == 200 else []
        except Exception as e:
            print('subtasks poll err:', e)
        if subtasks and all(st.get('status') in ('completed', 'failed') for st in subtasks):
            break
        time.sleep(5)
    print(f'elapsed {time.time()-t0:.0f}s; subtasks={json.dumps(subtasks, ensure_ascii=False)[:400]}')

    if not subtasks:
        print('!! 没有发现子任务——委派未发生或发现链断了')
        dump_parent(s, sid)
        return

    for st in subtasks:
        cid = st['id']
        r = s.get(f'{BASE}/ai/chat/sessions/{sid}/subtasks/{cid}/messages', timeout=15)
        data = r.json()
        print(f"\n=== subtask {cid} status={data['subtask']['status']} ===")
        for m in data['messages']:
            parts = m.get('content') or []
            types = [p.get('type') for p in parts]
            print(f"  [{m['role']}] {len(parts)} parts: {types}")
            for p in parts[:6]:
                txt = (p.get('text') or '')[:120].replace('\n', ' ')
                print(f"    - {p.get('type')}: {txt}")
    dump_parent(s, sid)


def dump_parent(s, sid):
    r = s.get(f'{BASE}/ai/chat/sessions/{sid}/messages', timeout=15)
    msgs = r.json() if r.status_code == 200 else []
    if isinstance(msgs, dict):
        msgs = msgs.get('messages') or []
    print(f"\n=== parent messages ({len(msgs)}) ===")
    for m in msgs[-3:]:
        parts = m.get('content') or []
        types = [p.get('type') for p in parts]
        print(f"  [{m.get('role')}] {len(parts)} parts: {types}")
        for p in parts[:8]:
            txt = (p.get('text') or '')[:100].replace('\n', ' ')
            print(f"    - {p.get('type')}: {txt}")


if __name__ == '__main__':
    main()
