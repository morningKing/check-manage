"""性能快改批 ①：_persist_conversation 同内容重复持久化不得产生新行版本。

流式期每 2.5s（PROGRESS_PERSIST_SEC）对全部 assistant 行做无条件
ON CONFLICT DO UPDATE——内容未变的行也写新版本，触发
ai_chat_messages_search_text_fn 重算（jsonb_array_elements + string_agg）
与 GIN trgm 索引维护，制造 WAL/dead tuple 写放大（批子会话越多越重）。

判据（真实库链路，仅 mock OC REST，get_db 走真库）：
- 同内容重复持久化后行版本 xmin 不变（修复前必然变化）；
- 内容真正变化时仍必须更新（防 WHERE 条件误伤正常写入）。
"""
import os
import sys
import uuid
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import utils.batch_engine as eng  # noqa: E402


@pytest.fixture
def child_session():
    """真库播种一个 running 子会话（不挂批次；persist 不校验批归属）。"""
    import psycopg2
    from config import DB_CONFIG
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    sid = f'sess_wa_{uuid.uuid4().hex[:10]}'
    cur.execute(
        "INSERT INTO users (id, username, password_hash, display_name, role) "
        "VALUES ('user-wa', 'write_amp', 'x', 'Write Amp', 'developer') "
        "ON CONFLICT (id) DO NOTHING")
    cur.execute(
        "INSERT INTO ai_chat_sessions (id, user_id, title, status) "
        "VALUES (%s, 'user-wa', 'write-amp-test', 'running')", (sid,))
    conn.commit()
    yield sid
    cur.execute("DELETE FROM ai_chat_messages WHERE session_id = %s", (sid,))
    cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
    conn.commit()
    conn.close()


def _raw(text, mid='msg_wa_fixed_1'):
    """一条已完结的 assistant 消息（info 固定 → meta 确定，两次调用全等）。"""
    return [{'info': {'role': 'assistant', 'id': mid, 'finish': 'stop',
                      'time': {'created': 1000, 'completed': 1350},
                      'tokens': {'input': 10, 'output': 5}, 'cost': 0.01},
             'parts': [{'type': 'text', 'text': text}]}]


def _persist(sid, raw):
    oc = MagicMock()
    oc.get_messages.return_value = raw
    w = eng.BatchWorker()
    with patch.object(eng, 'opencode_client', oc):
        w._persist_conversation(sid, '问题', 'oc-wa', {'content': []})


def _row_versions(sid):
    """[(id, xmin, content->>0 text)] —— 按落库顺序返回 assistant 行。"""
    import psycopg2
    from config import DB_CONFIG
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, xmin::text, content FROM ai_chat_messages "
            "WHERE session_id = %s AND role = 'assistant' ORDER BY seq", (sid,))
        return cur.fetchall()
    finally:
        conn.close()


def test_identical_repersist_writes_no_new_row_version(child_session):
    _persist(child_session, _raw('答案 A'))
    before = _row_versions(child_session)
    assert before, '首次持久化应落 assistant 行'

    # 流式期每 2.5s 的真实形态：同一会话、同一消息、内容未变、重复持久化
    _persist(child_session, _raw('答案 A'))
    after = _row_versions(child_session)

    assert [r[0] for r in after] == [r[0] for r in before], '消息不应增减'
    assert [r[1] for r in after] == [r[1] for r in before], (
        '同内容重复 upsert 产生了新行版本（xmin 变化）——search_text 触发器'
        '与 GIN trgm 随之全量重算，批任务流式期的写放大根因')


def test_changed_content_still_updates(child_session):
    _persist(child_session, _raw('答案 A'))
    before = _row_versions(child_session)
    assert before, '首次持久化应落 assistant 行'

    _persist(child_session, _raw('答案 A（修订）'))
    after = _row_versions(child_session)

    assert [r[1] for r in after] != [r[1] for r in before], (
        '内容真正变化时必须更新——跳过条件不得误伤正常写入')
    assert '修订' in after[0][2][0]['text']
