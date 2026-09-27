"""管理员软删除批子任务的测试(真实 DB,与 test_ai_batch_admin_repo 同约定)。

软删除 = 置 deleted_at,前台批次详情不再显示,数据保留;
计数(total/done/failed)按未删除子任务重算;仅终态可删。
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from db import get_db                    # noqa: E402
from utils import batch_repo             # noqa: E402


@pytest.fixture
def soft_delete_batch():
    """一个批任务 + 三个子任务:completed / failed / running。"""
    suffix = uuid.uuid4().hex[:8]
    uid, bid = f'u-sd-{suffix}', f'b-sd-{suffix}'
    kids = {'done': f'sd-done-{suffix}', 'fail': f'sd-fail-{suffix}',
            'run': f'sd-run-{suffix}'}
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s,%s,'x',%s,'developer')", (uid, 'sd-' + uid, uid))
            cur.execute("INSERT INTO ai_chat_batches (id,user_id,name,prompt,status,total) "
                        "VALUES (%s,%s,'软删除测试','p','running',3)", (bid, uid))
            for seq, (key, sid) in enumerate(kids.items()):
                status = {'done': 'completed', 'fail': 'failed', 'run': 'running'}[key]
                cur.execute("INSERT INTO ai_chat_sessions "
                            "(id,user_id,status,batch_id,batch_seq,batch_input_file) "
                            "VALUES (%s,%s,%s,%s,%s,%s)",
                            (sid, uid, status, bid, seq, f'{key}.txt'))
    conn.commit()
    yield {'uid': uid, 'bid': bid, 'kids': kids}
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_batches WHERE id=%s", (bid,))
            cur.execute("DELETE FROM users WHERE id=%s", (uid,))
        conn.commit()


def test_soft_delete_hides_from_detail_and_recounts(soft_delete_batch):
    f = soft_delete_batch
    r = batch_repo.admin_soft_delete_child(f['bid'], f['kids']['fail'])
    assert r is not None and r['status'] == 'failed'
    # 详情不再显示被删行,其余两行可见
    d = batch_repo.get_batch_detail(f['uid'], f['bid'])
    assert [s['id'] for s in d['sessions']] == [f['kids']['done'], f['kids']['run']]
    # 计数按未删除子任务重算
    assert d['batch']['total'] == 2
    assert d['batch']['done'] == 1
    assert d['batch']['failed'] == 0
    # 管理员详情同样不可见
    da = batch_repo.admin_get_batch_detail(f['bid'])
    assert [s['id'] for s in da['sessions']] == [f['kids']['done'], f['kids']['run']]


def test_soft_delete_only_terminal(soft_delete_batch):
    f = soft_delete_batch
    with pytest.raises(ValueError, match='先取消'):
        batch_repo.admin_soft_delete_child(f['bid'], f['kids']['run'])


def test_soft_delete_twice_raises(soft_delete_batch):
    f = soft_delete_batch
    assert batch_repo.admin_soft_delete_child(f['bid'], f['kids']['done']) is not None
    with pytest.raises(ValueError, match='已删除'):
        batch_repo.admin_soft_delete_child(f['bid'], f['kids']['done'])


def test_soft_delete_unknown_child_returns_none(soft_delete_batch):
    f = soft_delete_batch
    assert batch_repo.admin_soft_delete_child(f['bid'], 'no-such-sid') is None


def test_recompute_marks_batch_completed_after_deleting_non_completed(soft_delete_batch):
    """软删除 non-completed 子任务后,剩余全部 completed → 批次状态重算为
    completed(cancelled 按既有聚合约定计入 failed 侧)。"""
    f = soft_delete_batch
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE ai_chat_sessions SET status='cancelled' WHERE id=%s",
                        (f['kids']['run'],))
    conn.commit()
    # cancelled 也是终态,可软删除;删除 failed 与 cancelled 两个子任务
    batch_repo.admin_soft_delete_child(f['bid'], f['kids']['fail'])
    batch_repo.admin_soft_delete_child(f['bid'], f['kids']['run'])
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM ai_chat_batches WHERE id=%s", (f['bid'],))
            assert cur.fetchone()[0] == 'completed'
            cur.execute("SELECT total, done, failed FROM ai_chat_batches WHERE id=%s",
                        (f['bid'],))
            assert cur.fetchone() == (1, 1, 0)
