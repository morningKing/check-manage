"""ai_chat_messages.seq 的备份→还原真实 Postgres 集成测试（不 mock）。

覆盖代码审查发现的两条 Important：

1. `BACKUP_TABLES` 里 `ai_chat_messages` 的显式列清单本来不含 `seq`（也不含
   既有就缺的 `meta`），备份/还原会静默把它们丢掉；还原时 `seq` 缺失，走
   `BIGSERIAL` 默认值按 `_export_table` 的（无 `ORDER BY` 、不保证顺序的）
   导出顺序重新生成，等于把上一轮刚修好的"插入即定序"不变量清零重来。
2. `restore_backup` 把每行原始 `seq` 值原样写回之后，`seq` 列背后那个真实
   Postgres 序列对象的 `nextval()` 计数器不会跟着自动前移——不重播种的话，
   还原后第一条新插入的消息会拿到 <= 已还原数据最大 seq 的值，与还原数据
   重新打平。

走真实的 `create_backup()` → 手动删除还原范围内的行 → `restore_backup()` 全
流程；`tables=['ai_chat_messages']` + 默认 `mode='upsert'` 把影响面限定在
这几行新插入的记录上，不会波及库里其他数据——同一套路见
`test_api_key_batch_fk.py` / `test_batch_engine.py` 的 `db_conn` 真实 DB 集
成测试。之所以选真实往返而不是纯 mock：这条 bug 的本质就是"这两个函数之间
的隐藏契约"（导出列清单 × 还原写回 × 序列重播种三者要对齐），mock 掉任何一
环都验证不到真实的端到端行为。
"""
import os
import sys
import json
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.backup import create_backup, restore_backup, BACKUP_TABLE_MAP


# ---------------------------------------------------------------------------
# 列清单本身的核查（轻量，不碰 DB）
# ---------------------------------------------------------------------------

def test_ai_chat_messages_backup_columns_include_seq_and_meta():
    """列清单必须包含 seq/meta，否则备份/还原会静默跳过它们（本函数锁住这个
    不能再退化的契约）。"""
    _, columns, jsonb_indices, _ = BACKUP_TABLE_MAP['ai_chat_messages']
    clean = [c.strip('"') for c in columns]
    assert 'seq' in clean
    assert 'meta' in clean
    # content/meta 是 jsonb，还原阶段要走 psycopg2.extras.Json 包装；
    # seq 是 bigint，不能被误当成 jsonb 包装（会导致 INSERT 类型错误）。
    jsonb_names = {clean[i] for i in jsonb_indices}
    assert jsonb_names == {'content', 'meta'}
    assert 'seq' not in jsonb_names


# ---------------------------------------------------------------------------
# 端到端往返（真实 DB）
# ---------------------------------------------------------------------------

@pytest.fixture
def user_id(db_conn):
    """Insert a throwaway user, yield its id, clean up after."""
    uid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, %s, %s, 'developer')",
            (uid, f'bkp_user_{uid[:8]}', 'x', f'BKP User {uid[:8]}'),
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


def test_backup_restore_round_trip_preserves_seq_order_and_reseeds_sequence(db_conn, user_id):
    """写 3 条 assistant 消息 → 备份 → 删除 → 还原 → 断言：
    (a) 还原后 3 条消息的 seq 相对顺序（乃至绝对值）原样保留；
    (b) 还原后新插入一条消息的 seq 大于所有还原数据的 seq（证明 setval 生效，
        序列计数器被顶到了还原数据的最大值之上，不会与还原数据重新打平）。
    """
    bid = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
            "VALUES (%s, %s, 'seq-backup-test', 'p', 1)",
            (bid, user_id),
        )
        cur.execute(
            "INSERT INTO ai_chat_sessions "
            "  (id, user_id, status, batch_id, batch_seq, batch_input_file) "
            "VALUES (%s, %s, 'completed', %s, 0, 'batch-staging/u/s/a.pdf')",
            (sid, user_id, bid),
        )
        msg_ids = [f'{sid}:a:{i}' for i in range(3)]
        for i, mid in enumerate(msg_ids):
            cur.execute(
                "INSERT INTO ai_chat_messages (id, session_id, role, content) "
                "VALUES (%s, %s, 'assistant', %s::jsonb)",
                (mid, sid, json.dumps([{'type': 'text', 'text': f'msg-{i}'}])),
            )
    db_conn.commit()

    # 记下备份前的真实 seq（自然递增，BIGSERIAL 默认赋值）
    with db_conn.cursor() as cur:
        cur.execute("SELECT id, seq FROM ai_chat_messages WHERE session_id = %s ORDER BY seq", (sid,))
        original = cur.fetchall()
    assert [r[0] for r in original] == msg_ids
    original_seqs = [r[1] for r in original]
    assert original_seqs == sorted(original_seqs)  # 严格递增，互不相同

    backup_id = None
    zip_path = None
    try:
        # 备份（真实 DB，走 utils.backup.get_db，未 mock）
        result = create_backup(backup_type='manual', created_by='test',
                               tables=['ai_chat_messages'])
        backup_id = result['id']
        zip_path = result['filePath']
        assert os.path.isfile(zip_path)

        # 从 DB 里删掉这 3 条消息，模拟"还原目标里这些消息已经不在了"
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_messages WHERE session_id = %s", (sid,))
        db_conn.commit()

        # 故意把序列计数器往回打到 1——模拟"灾难恢复/全新库"场景：新库的
        # ai_chat_messages_seq_seq 序列对象是全新的（从 1 开始），但要还原
        # 进去的历史数据 seq 值远大于 1。如果不这么做，这个测试在共享的开发
        # 库里会因为"测试套件里其他用例早就通过自然 INSERT 把序列顶到了远超
        # original_seqs 的位置"而在没有 setval 修复的情况下也碰巧通过——那就
        # 抓不住 bug 了（已实测验证：先跑过一次没有这步、也没有 setval 修复
        # 的版本，2 passed，说明测试没抓住问题；加上这步之后才稳定复现）。
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT setval(pg_get_serial_sequence('ai_chat_messages', 'seq'), 1, false)"
            )
        db_conn.commit()

        # 还原（真实 DB，走 db.pool，未 mock）。mode 用默认 upsert：不 DELETE，
        # 只对这 3 个 id 做 INSERT（此时它们已被上面删掉，等价于纯 INSERT），
        # 不会波及数据库里其他任何行。如果 restore_backup 里的 setval 修复生效，
        # 这一步会把序列重新顶到 max(还原数据的 seq) 之上；如果没生效，序列
        # 会停留在上面人为调低的 1，下一条自然插入的消息会拿到一个很小的 seq。
        restore_backup(zip_path, tables=['ai_chat_messages'], mode='upsert',
                       restore_vector_store=False, restore_data_files=False)

        # (a) 相对顺序 + 绝对值都应该原样恢复——证明 seq 真的被导出/写回了，
        #     而不是被 present 过滤掉、退回 BIGSERIAL 默认值重新生成。
        with db_conn.cursor() as cur:
            cur.execute("SELECT id, seq FROM ai_chat_messages WHERE session_id = %s ORDER BY seq", (sid,))
            restored = cur.fetchall()
        assert [r[0] for r in restored] == msg_ids
        assert [r[1] for r in restored] == original_seqs

        # (b) setval 生效：还原后新插入一条消息，seq 必须大于所有还原数据的
        #     seq，不能落回旧计数、与还原数据重新打平。
        new_id = f'{sid}:a:new'
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ai_chat_messages (id, session_id, role, content) "
                "VALUES (%s, %s, 'assistant', %s::jsonb) RETURNING seq",
                (new_id, sid, json.dumps([{'type': 'text', 'text': 'post-restore'}])),
            )
            new_seq = cur.fetchone()[0]
        db_conn.commit()
        assert new_seq > max(original_seqs)
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_messages WHERE session_id = %s", (sid,))
            cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (sid,))
            cur.execute("DELETE FROM ai_chat_batches WHERE id = %s", (bid,))
        db_conn.commit()
        if backup_id:
            with db_conn.cursor() as cur:
                cur.execute("DELETE FROM backups WHERE id = %s", (backup_id,))
            db_conn.commit()
        if zip_path and os.path.isfile(zip_path):
            os.remove(zip_path)
