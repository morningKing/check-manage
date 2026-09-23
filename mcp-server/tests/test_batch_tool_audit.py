"""batch_tool_audit 真库测试(共享开发库,用后即清)。

种子:一个批任务 + 两个子会话,持久化含 tool_use parts 的消息
(一条 bash 执行成功并带"已执行"陈述,一条含 error 标记的失败执行)。
"""
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from db import get_db                    # noqa: E402
from tools import batch_tool_audit      # noqa: E402


@pytest.fixture()
def audit_env():
    suffix = uuid.uuid4().hex[:10]
    uid, bid = f'u-audit-{suffix}', f'b-audit-{suffix}'
    s1, s2 = f's-audit-1-{suffix}', f's-audit-2-{suffix}'
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s,%s,'x',%s,'admin')", (uid, 'audit-' + uid, uid))
            cur.execute("INSERT INTO ai_chat_batches (id,user_id,name,prompt,status,total) "
                        "VALUES (%s,%s,'审计测试','p','completed',2)", (bid, uid))
            for seq, sid in enumerate((s1, s2)):
                cur.execute("INSERT INTO ai_chat_sessions "
                            "(id,user_id,status,batch_id,batch_seq,batch_input_file) "
                            "VALUES (%s,%s,'completed',%s,%s,'a.txt')", (sid, uid, bid, seq))
    conn.commit()
    yield {'uid': uid, 'bid': bid, 's1': s1, 's2': s2}
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_sessions WHERE batch_id=%s", (bid,))
            cur.execute("DELETE FROM ai_chat_batches WHERE id=%s", (bid,))
            cur.execute("DELETE FROM users WHERE id=%s", (uid,))
        conn.commit()


def _tool_msg(mid, command, output, status='completed'):
    content = [
        {'type': 'text', 'text': '我来执行脚本'},
        {'type': 'tool_use', 'id': mid, 'name': 'bash',
         'input': {'command': command}, 'status': status,
         'output': output},
        {'type': 'text', 'text': '执行完成'},
    ]
    return json.dumps(content)


def test_audit_finds_executions_and_error_markers(audit_env):
    f = audit_env
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO ai_chat_messages (id, session_id, role, content) "
                        "VALUES (%s,%s,'assistant',%s)",
                        (f'm1', f['s1'],
                         _tool_msg('t1', 'bash reconcile.sh', '对账完成，共核对 12 条')))
            cur.execute("INSERT INTO ai_chat_messages (id, session_id, role, content) "
                        "VALUES (%s,%s,'assistant',%s)",
                        (f'm2', f['s2'],
                         _tool_msg('t2', 'bash deploy.sh',
                                   'Traceback (most recent call last): Permission denied')))
    conn.commit()

    res = batch_tool_audit.audit_batch(f['bid'], tool='bash')
    assert res['summary']['executions'] == 2
    assert res['summary']['sessions'] == 2
    # 两个执行都有错误标记(traceback + permission denied 均被 _ERROR_RE 命中)
    err_cmds = [e for e in
                [x for s in res['sessions'] for x in s['executions']]
                if 'traceback' in ' '.join(e.get('errorMarkers', []))
                or 'permission denied' in ' '.join(e.get('errorMarkers', []))]
    assert len(err_cmds) >= 1
    # 至少一个执行的命令包含 deploy.sh
    assert any('deploy.sh' in e['command'] for e in err_cmds)


def test_audit_pattern_prefilters(audit_env):
    f = audit_env
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO ai_chat_messages (id, session_id, role, content) "
                        "VALUES (%s,%s,'assistant',%s)",
                        (f'm1', f['s1'],
                         _tool_msg('t1', 'bash reconcile.sh', '对账完成')))
            cur.execute("INSERT INTO ai_chat_messages (id, session_id, role, content) "
                        "VALUES (%s,%s,'assistant',%s)",
                        (f'm2', f['s2'], _tool_msg('t2', 'bash deploy.sh', '部署完成')))
    conn.commit()
    res = batch_tool_audit.audit_batch(f['bid'], tool='bash', pattern='reconcile')
    cmds = [e['command'] for sess in res['sessions'] for e in sess['executions']]
    assert len(cmds) == 1 and 'reconcile' in cmds[0]


def test_audit_empty_batch(audit_env):
    res = batch_tool_audit.audit_batch(audit_env['bid'], tool='bash')
    assert res['sessions'] == [] and res['summary']['executions'] == 0
