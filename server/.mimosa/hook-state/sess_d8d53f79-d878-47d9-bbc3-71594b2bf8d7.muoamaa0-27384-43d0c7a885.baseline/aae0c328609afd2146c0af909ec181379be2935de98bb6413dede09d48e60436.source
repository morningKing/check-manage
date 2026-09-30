"""回归测试：get_batch_results() 的 `output` 必须按子任务 status 设门。

为什么会有半截文本躺在库里：`batch_engine._run_one` 在会话**运行期间**以
PROGRESS_PERSIST_SEC 为周期反复调 `_persist_conversation`（`on_progress=
_persist_progress`），好让界面能实时看到对话。于是：

* `running` 的子任务，`ai_chat_messages` 里已经有 assistant 消息；
* 跑到一半超时 / 报错被标 `failed` 的子任务，那些**半截**消息也留在库里。

对外契约（docs/user-guide/integration/ai-batch-api.md 4.5 / 9.2）承诺
`status != 'completed'` 时 `output` 恒为 null。不设门的话，集成方按文档推荐的
「进入终态再取结果」流程就会把被截断的 AI 输出当成最终结果写进下游系统
（`partial` 也是终态）。

真 Postgres 集成测试（不 mock），与 test_batch_results_ordering.py 同一模式。
"""
import os
import sys
import json
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import utils.batch_repo as repo


@pytest.fixture
def user_id(db_conn):
    """Insert a throwaway user, yield its id, clean up after."""
    uid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, %s, %s, 'developer')",
            (uid, f'gate_user_{uid[:8]}', 'x', f'Gate User {uid[:8]}'),
        )
    db_conn.commit()
    yield uid
    with db_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM ai_chat_messages WHERE session_id IN "
            "  (SELECT id FROM ai_chat_sessions WHERE user_id = %s)", (uid,))
        cur.execute("DELETE FROM ai_chat_sessions WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM ai_chat_batches WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    db_conn.commit()


@pytest.fixture
def batch_with_children(db_conn, user_id):
    """一个批任务 + 三个子任务（running / failed / completed），
    三个都已经有 assistant 消息落库（模拟 _persist_progress 的中途落库）。"""
    bid = str(uuid.uuid4())
    sids = {}
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
            "VALUES (%s, %s, 'output-gate-test', 'p', 3)",
            (bid, user_id),
        )
        for seq, (status, text, err) in enumerate([
            ('running', '这是跑到一半的半截文本', None),
            ('failed', '这是超时前产出的半截文本', 'timeout after 480s'),
            ('completed', '这是真正完成的完整答案', None),
        ]):
            sid = str(uuid.uuid4())
            sids[status] = sid
            cur.execute(
                "INSERT INTO ai_chat_sessions "
                "  (id, user_id, status, batch_id, batch_seq, batch_input_file, "
                "   error_message) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (sid, user_id, status, bid, seq,
                 f'batch-staging/u/s/{status}.pdf', err),
            )
            cur.execute(
                "INSERT INTO ai_chat_messages (id, session_id, role, content) "
                "VALUES (%s, %s, 'assistant', %s::jsonb)",
                (f'{sid}:a:0', sid, json.dumps([{'type': 'text', 'text': text}])),
            )
    db_conn.commit()
    yield bid, sids
    with db_conn.cursor() as cur:
        for sid in sids.values():
            cur.execute("DELETE FROM ai_chat_messages WHERE session_id = %s", (sid,))
        cur.execute("DELETE FROM ai_chat_sessions WHERE batch_id = %s", (bid,))
        cur.execute("DELETE FROM ai_chat_batches WHERE id = %s", (bid,))
    db_conn.commit()


def _by_name(results):
    return {r['name']: r for r in results}


def test_running_child_with_persisted_message_returns_null_output(batch_with_children):
    """执行中的子任务已经有 assistant 消息，也不能把半截文本吐出去。"""
    bid, _ = batch_with_children
    r = _by_name(repo.get_batch_results(bid))['running.pdf']
    assert r['status'] == 'running'
    assert r['output'] is None, f"running 子任务泄漏了中途文本: {r['output']!r}"


def test_failed_child_with_persisted_message_returns_null_output(batch_with_children):
    """超时被标 failed 的子任务，库里留着的半截文本同样不能吐出去。

    这是最危险的一条：批任务此时是终态（partial），集成方正好会在这一刻取结果。
    """
    bid, _ = batch_with_children
    r = _by_name(repo.get_batch_results(bid))['failed.pdf']
    assert r['status'] == 'failed'
    assert r['output'] is None, f"failed 子任务泄漏了半截文本: {r['output']!r}"
    assert r['error'] == 'timeout after 480s'   # 失败原因照常返回


def test_completed_child_still_returns_full_text(batch_with_children):
    """设门不能误伤正常路径：completed 仍返回完整文本。"""
    bid, _ = batch_with_children
    r = _by_name(repo.get_batch_results(bid))['completed.pdf']
    assert r['status'] == 'completed'
    assert r['output'] == '这是真正完成的完整答案'
