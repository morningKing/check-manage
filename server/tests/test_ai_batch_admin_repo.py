"""管理员作用域读函数的测试（真实 DB）。

这些函数与既有的 list_batches / get_batch_detail 的语义**相反**：它们刻意不按
归属用户过滤。因此测试的重点不是"能查到"，而是"跨用户能查到"与"筛选真的收窄了"。
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from db import get_db                    # noqa: E402
from utils import batch_repo             # noqa: E402


@pytest.fixture
def two_users_two_batches():
    """两个用户各一个批任务：一个来自界面（api_key_id 为 NULL），一个来自 API。

    收尾把造的数据全删掉 —— 这些测试跑在共享开发库上，留下的行会污染后续断言。
    """
    uid_a = 'u-adm-' + uuid.uuid4().hex[:8]
    uid_b = 'u-adm-' + uuid.uuid4().hex[:8]
    key_id = 'ak-adm-' + uuid.uuid4().hex[:8]
    bid_ui = 'b-adm-' + uuid.uuid4().hex[:8]
    bid_api = 'b-adm-' + uuid.uuid4().hex[:8]
    sid = 's-adm-' + uuid.uuid4().hex[:8]
    with get_db() as conn:
        with conn.cursor() as cur:
            for uid, uname in ((uid_a, 'alice-' + uid_a), (uid_b, 'bob-' + uid_b)):
                cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                            "VALUES (%s,%s,'x',%s,'developer')", (uid, uname, uname))
            cur.execute("INSERT INTO api_keys (id, name, key_hash, owner_user_id) "
                        "VALUES (%s,'adm-test',%s,%s)", (key_id, 'h-' + key_id, uid_b))
            cur.execute("INSERT INTO ai_chat_batches (id,user_id,name,prompt,status,total) "
                        "VALUES (%s,%s,'界面批任务','p','completed',1)", (bid_ui, uid_a))
            cur.execute("INSERT INTO ai_chat_batches (id,user_id,name,prompt,status,total,api_key_id) "
                        "VALUES (%s,%s,'接口批任务','p','failed',1,%s)", (bid_api, uid_b, key_id))
            cur.execute("INSERT INTO ai_chat_sessions (id,user_id,status,batch_id,batch_seq,batch_input_file) "
                        "VALUES (%s,%s,'failed',%s,0,'uploads/a.txt')", (sid, uid_b, bid_api))
        conn.commit()
    yield {'uid_a': uid_a, 'uid_b': uid_b, 'bid_ui': bid_ui,
           'bid_api': bid_api, 'sid': sid,
           'uname_a': 'alice-' + uid_a}
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_batches WHERE id IN (%s,%s)", (bid_ui, bid_api))
            cur.execute("DELETE FROM api_keys WHERE id=%s", (key_id,))
            cur.execute("DELETE FROM users WHERE id IN (%s,%s)", (uid_a, uid_b))
        conn.commit()


def _ids(res):
    return {b['id'] for b in res['items']}


def test_list_spans_all_users(two_users_two_batches):
    """核心断言：两个不同用户的批任务同时出现在结果里。"""
    f = two_users_two_batches
    res = batch_repo.admin_list_batches(page=1, page_size=100)
    got = _ids(res)
    assert f['bid_ui'] in got and f['bid_api'] in got


def test_list_carries_owner_username_and_source(two_users_two_batches):
    """跨用户列表没有归属列无法使用；来源列直接对应"界面触发还是 API 触发"。"""
    f = two_users_two_batches
    res = batch_repo.admin_list_batches(page=1, page_size=100)
    by_id = {b['id']: b for b in res['items']}
    assert by_id[f['bid_ui']]['owner_username'] == f['uname_a']
    assert by_id[f['bid_ui']]['source'] == 'ui'
    assert by_id[f['bid_api']]['source'] == 'api'


def test_filter_by_source_excludes_the_other(two_users_two_batches):
    """筛选必须真的收窄 —— 只断言"包含"会让一个永远返回全集的实现也通过。"""
    f = two_users_two_batches
    only_api = _ids(batch_repo.admin_list_batches(page=1, page_size=100, source='api'))
    assert f['bid_api'] in only_api
    assert f['bid_ui'] not in only_api


def test_filter_by_status_excludes_the_other(two_users_two_batches):
    f = two_users_two_batches
    only_failed = _ids(batch_repo.admin_list_batches(page=1, page_size=100, status='failed'))
    assert f['bid_api'] in only_failed
    assert f['bid_ui'] not in only_failed


def test_filter_by_owner_keyword_excludes_the_other(two_users_two_batches):
    f = two_users_two_batches
    only_alice = _ids(batch_repo.admin_list_batches(page=1, page_size=100,
                                                    owner_keyword=f['uname_a']))
    assert f['bid_ui'] in only_alice
    assert f['bid_api'] not in only_alice


def test_filter_by_name_keyword_excludes_the_other(two_users_two_batches):
    f = two_users_two_batches
    only_api = _ids(batch_repo.admin_list_batches(page=1, page_size=100,
                                                  name_keyword='接口'))
    assert f['bid_api'] in only_api
    assert f['bid_ui'] not in only_api


def test_detail_spans_users_and_lists_children(two_users_two_batches):
    f = two_users_two_batches
    d = batch_repo.admin_get_batch_detail(f['bid_api'])
    assert d is not None
    assert d['batch']['owner_username'].startswith('bob-')
    assert d['batch']['source'] == 'api'
    assert [s['id'] for s in d['sessions']] == [f['sid']]


def test_detail_missing_returns_none():
    assert batch_repo.admin_get_batch_detail('b-does-not-exist') is None


def test_get_owner_returns_the_real_owner(two_users_two_batches):
    """写路径靠它拿归属用户；返回错的用户会让重试静默失效。"""
    f = two_users_two_batches
    assert batch_repo.admin_get_batch_owner(f['bid_api']) == f['uid_b']
    assert batch_repo.admin_get_batch_owner('b-nope') is None


@pytest.fixture
def batch_with_many_messages(two_users_two_batches):
    """给子任务塞 3 条消息，且**故意让 created_at 完全相同、seq 乱序**。

    这是本文件最关键的构造：批任务子会话的消息由 worker 批量持久化，同事务内
    now() 是常量，created_at 相同，按它排序取不出确定结果。若实现改回
    ORDER BY created_at，本用例会红。
    """
    f = two_users_two_batches
    sid = f['sid']
    import json
    with get_db() as conn:
        with conn.cursor() as cur:
            for mid, role, text in (('m-c', 'assistant', '第三条'),
                                    ('m-a', 'user', '第一条'),
                                    ('m-b', 'assistant', '第二条')):
                cur.execute(
                    "INSERT INTO ai_chat_messages (id, session_id, role, content, created_at) "
                    "VALUES (%s,%s,%s,%s::jsonb, TIMESTAMPTZ '2026-01-01 00:00:00+08')",
                    (mid + '-' + sid, sid, role,
                     json.dumps([{'type': 'text', 'text': text}])))
        conn.commit()
    # seq 由 BIGSERIAL 按插入顺序生成：m-c < m-a < m-b
    yield {**f, 'expected_order': ['第三条', '第一条', '第二条']}


def _texts(res):
    out = []
    for m in res['messages']:
        for p in (m['content'] or []):
            if p.get('type') == 'text':
                out.append(p['text'])
    return out


def test_messages_ordered_by_seq_not_created_at(batch_with_many_messages):
    f = batch_with_many_messages
    res = batch_repo.admin_get_child_messages(f['bid_api'], f['sid'])
    assert _texts(res) == f['expected_order']
    assert res['total'] == 3
    assert res['truncated'] is False


def test_messages_truncate_to_the_most_recent(batch_with_many_messages):
    """截断必须取**最近**的若干条，并仍以 seq 升序返回（自然阅读顺序）。"""
    f = batch_with_many_messages
    res = batch_repo.admin_get_child_messages(f['bid_api'], f['sid'], limit=2)
    assert res['truncated'] is True
    assert res['total'] == 3
    assert _texts(res) == f['expected_order'][-2:]     # 最近 2 条，升序


def test_messages_reject_session_from_another_batch(batch_with_many_messages):
    """sessionId 必须属于该 batchId，否则这个路径就成了"用任意 batchId 读任意会话"。"""
    f = batch_with_many_messages
    assert batch_repo.admin_get_child_messages(f['bid_ui'], f['sid']) is None
