"""门禁探针手动执行脚本（配套 gate-task-subagent-probe.md）。

在本机/生产后端上用 API 完整跑一遍「task 委派子代理 write 被 checkpoint(before)
命中」场景：建策略 → 上传 in.txt → 建批(引用策略) → 等终态 → 打印子任务门禁。
只依赖 requests；结论核对仍按套件文档 §3 的判定表。

用法：python gate_probe_run.py [BASE]   # BASE 默认 http://127.0.0.1:3002
"""
import json
import sys
import time

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:3002'

CHECKS = [{
    'name': '报告写入前确认', 'check_type': 'tool', 'mode': 'checkpoint',
    'tool': 'write', 'args_pattern': 'gate-probe-report\\.md',
    'timing': 'before', 'max_checks': 1, 'scope': 'tree',
    'prompt': '过程检查：写入 gate-probe-report.md 前，必须先用 read 工具完整读取 '
              'uploads/in.txt，逐行核对你将写入的三行要点与该文件原文一致；确认无误后'
              '重新执行本次写入。不要改用 bash/echo/edit 等其他工具，也不要更改目标文件名。',
    'assert_spec': {'tool': 'write', 'args_pattern': 'gate-probe-report\\.md',
                    'min_count': 1},
}]

IN_TXT = '\n'.join([
    '验证要点一：task 工具委派的子代理内，write 工具调用会触发门禁过程检查。',
    '验证要点二：checkpoint(before) 命中后注入提示词并消耗预算。',
    '验证要点三：终态断言以服务端动作账本为唯一证据来源。',
])

PROMPT = (
    '用 task 工具委派一个子代理完成下面的任务，不要在根会话里自己写文件。'
    '委派提示词必须原样传达这三条要求：\n'
    '(1) 先用 read 工具读取 uploads/in.txt，取出其中三行要点；\n'
    '(2) 只用 write 工具（禁止用 bash/echo/edit/patch 写文件）在 outputs/ 目录下创建 '
    'gate-probe-report.md，内容为这三行要点，一行一条；\n'
    '(3) 写完后回复「已写入 outputs/gate-probe-report.md」。'
)


def main():
    r = requests.post(f'{BASE}/auth/login', timeout=10,
                      json={'username': 'admin', 'password': 'admin123'})
    r.raise_for_status()
    H = {'Authorization': f"Bearer {r.json()['token']}"}

    r = requests.post(f'{BASE}/ai/chat/gate-policies', headers=H, timeout=10,
                      json={'name': f'PROBE-task子代理write前置检查-{int(time.time())}',
                            'description': 'task 委派子代理 write 命中验证（探针）',
                            'enabled': True, 'checks': CHECKS})
    print('create policy:', r.status_code)
    r.raise_for_status()
    pid = r.json()['id']
    print('policy:', pid)

    r = requests.post(f'{BASE}/ai/chat/batches/staging/upload', headers=H,
                      timeout=10,
                      files={'file': ('in.txt', IN_TXT.encode('utf-8'),
                                      'text/plain')},
                      data={'upload_session_id': f'probe-{int(time.time())}'})
    r.raise_for_status()
    staged = r.json()
    print('staged:', json.dumps(staged, ensure_ascii=False)[:200])

    r = requests.post(f'{BASE}/ai/chat/batches', headers=H, timeout=10,
                      json={'name': f'PROBE-MANUAL-{int(time.time())}',
                            'prompt': PROMPT, 'files': [staged],
                            'gatePolicyId': pid})
    print('create batch:', r.status_code)
    r.raise_for_status()
    body = r.json()
    bid = body.get('batchId') or (body.get('batch') or {}).get('id')
    print('batch:', bid)

    deadline, status = time.time() + 600, None
    while time.time() < deadline:
        d = requests.get(f'{BASE}/ai/chat/batches/{bid}', headers=H,
                         timeout=10).json()
        status = (d.get('batch') or {}).get('status')
        if status in ('completed', 'failed', 'stopped', 'cancelled'):
            break
        time.sleep(10)
    print('terminal:', status)
    for s in (d.get('sessions') or []):
        print('session:', json.dumps(
            {k: s.get(k) for k in ('sessionId', 'status', 'gateStatus',
                                   'gateError', 'error', 'preview')},
            ensure_ascii=False)[:400])
    print('batchId:', bid)


if __name__ == '__main__':
    main()
